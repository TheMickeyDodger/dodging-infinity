/**
 * FIXTURE SOURCE — an in-process stand-in for the DI backend, for VISUAL TESTING ONLY.
 *
 *   node dist/di-cli.js --state-dir <abs dir> --fixture-source <abs fixture script>
 *
 * A fixture script is a labelled, hash-traceable JSON file of catalog "frames". It is read
 * ONCE at launch (size-checked before parsing) and then served from memory: each poll of the
 * office's ordinary consumer loop (diOffice.ts) receives the next frame. It REPLACES the two
 * backend seams — the discovery-file read and the HTTP request — so a fixture office never
 * reads a backend discovery file, never opens a backend connection, and never learns a backend
 * token. That is the configuration-level guarantee: canonical discovery is disabled because the
 * office is composed without it, not because a file happens to be missing.
 *
 * What is still exercised for real: envelope parsing, the serve-time verdict, the office view,
 * the local expiry re-evaluation and character projection. Timestamps are moved to "now" (or
 * deliberately into the past for a stale frame) when a frame is served, so the projection's
 * freshness rules decide liveness exactly as they would for a real answer.
 */
import * as fs from 'fs';

import {
  DI_FIXTURE_LABEL_PREFIX,
  DI_FIXTURE_MAX_FRAME_REPEAT,
  DI_FIXTURE_MAX_FRAMES,
  DI_FIXTURE_SOURCE_MAX_BYTES,
} from '../constants.js';
import type { BackendResult, BackendTransportProblem } from './backendClient.js';
import type { BackendEndpoint, DiscoveryResult } from './backendDiscovery.js';
import { FRESHNESS_HEALTH_HEADER, FRESHNESS_PROBLEM_HEADER } from './serveTimeVerdict.js';

export class FixtureSourceError extends Error {}

type Obj = Record<string, unknown>;

const TRANSPORT_PROBLEMS: readonly BackendTransportProblem[] = [
  'backend_unreachable',
  'backend_timeout',
  'backend_response_too_large',
  'backend_network_error',
  'backend_request_invalid',
];

export type FixtureFrame =
  | {
      kind: 'catalog';
      envelope: Obj;
      /** fresh: timestamps moved to now. stale: valid_until lies `staleSeconds` in the past. */
      timing: 'fresh' | 'stale';
      staleSeconds: number;
      health: string | null;
      problem: string | null;
      repeat: number;
    }
  | { kind: 'body_not_json'; repeat: number }
  | { kind: 'json_body'; body: unknown; repeat: number }
  | { kind: 'http_error'; status: number; repeat: number }
  | { kind: 'transport_error'; problem: BackendTransportProblem; repeat: number };

export interface FixtureScript {
  fixtureId: string;
  label: string;
  description: string;
  afterLast: 'hold' | 'cycle';
  frames: FixtureFrame[];
}

/** The two backend seams a DiOffice consumes, served from a fixture script. */
export interface FixtureSourceDeps {
  readDiscovery: (filePath: string) => DiscoveryResult;
  request: (endpoint: BackendEndpoint) => Promise<BackendResult>;
}

export interface FixtureSource {
  script: FixtureScript;
  deps: FixtureSourceDeps;
  /** Frames served so far (diagnostics and tests). */
  served(): number;
}

function isObj(value: unknown): value is Obj {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function intIn(value: unknown, min: number, max: number, what: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < min || value > max)
    throw new FixtureSourceError(`${what} must be an integer in ${min}..${max}`);
  return value;
}

function optionalString(value: unknown, what: string): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string' || value.length === 0 || value.length > 256)
    throw new FixtureSourceError(`${what} must be a non-empty string of at most 256 characters`);
  return value;
}

function parseFrame(raw: unknown, index: number): FixtureFrame {
  const where = `frames[${index}]`;
  if (!isObj(raw)) throw new FixtureSourceError(`${where} must be an object`);
  const repeat =
    raw.repeat === undefined
      ? 1
      : intIn(raw.repeat, 1, DI_FIXTURE_MAX_FRAME_REPEAT, `${where}.repeat`);
  switch (raw.kind) {
    case 'catalog': {
      if (!isObj(raw.envelope) || !isObj(raw.envelope.response))
        throw new FixtureSourceError(`${where}.envelope must be a catalog envelope object`);
      const timing = raw.timing ?? 'fresh';
      if (timing !== 'fresh' && timing !== 'stale')
        throw new FixtureSourceError(`${where}.timing must be "fresh" or "stale"`);
      return {
        kind: 'catalog',
        envelope: raw.envelope,
        timing,
        staleSeconds:
          timing === 'stale' ? intIn(raw.stale_seconds, 1, 86_400, `${where}.stale_seconds`) : 0,
        health: optionalString(raw.health, `${where}.health`),
        problem: optionalString(raw.problem, `${where}.problem`),
        repeat,
      };
    }
    case 'body_not_json':
      return { kind: 'body_not_json', repeat };
    case 'json_body':
      if (!('body' in raw)) throw new FixtureSourceError(`${where}.body is required`);
      return { kind: 'json_body', body: raw.body, repeat };
    case 'http_error':
      return { kind: 'http_error', status: intIn(raw.status, 400, 599, `${where}.status`), repeat };
    case 'transport_error': {
      const problem = raw.problem;
      if (!TRANSPORT_PROBLEMS.includes(problem as BackendTransportProblem))
        throw new FixtureSourceError(`${where}.problem must be a backend transport problem code`);
      return { kind: 'transport_error', problem: problem as BackendTransportProblem, repeat };
    }
    default:
      throw new FixtureSourceError(`${where}.kind is not a known fixture frame kind`);
  }
}

/** Validate a parsed fixture script. Throws FixtureSourceError with the first problem. */
export function parseFixtureScript(value: unknown): FixtureScript {
  if (!isObj(value)) throw new FixtureSourceError('a fixture script must be a JSON object');
  if (value.fixture !== true)
    throw new FixtureSourceError('not a fixture script: "fixture": true is required');
  const fixtureId = value.fixture_id;
  if (typeof fixtureId !== 'string' || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(fixtureId))
    throw new FixtureSourceError('fixture_id must be a short lowercase id');
  const label = value.label;
  if (
    typeof label !== 'string' ||
    !label.startsWith(DI_FIXTURE_LABEL_PREFIX) ||
    label.length > 200
  )
    throw new FixtureSourceError(`label must start with "${DI_FIXTURE_LABEL_PREFIX}"`);
  const description = typeof value.description === 'string' ? value.description : '';
  const afterLast = value.after_last ?? 'hold';
  if (afterLast !== 'hold' && afterLast !== 'cycle')
    throw new FixtureSourceError('after_last must be "hold" or "cycle"');
  if (!Array.isArray(value.frames) || value.frames.length === 0)
    throw new FixtureSourceError('frames must be a non-empty array');
  if (value.frames.length > DI_FIXTURE_MAX_FRAMES)
    throw new FixtureSourceError(`at most ${DI_FIXTURE_MAX_FRAMES} frames`);
  return {
    fixtureId,
    label,
    description,
    afterLast,
    frames: value.frames.map((f, i) => parseFrame(f, i)),
  };
}

/** Read a fixture script from an absolute path: size-checked BEFORE parsing. */
export function readFixtureScript(filePath: string): FixtureScript {
  let stat: fs.Stats;
  try {
    stat = fs.statSync(filePath);
  } catch {
    throw new FixtureSourceError('the fixture script is not readable');
  }
  if (!stat.isFile()) throw new FixtureSourceError('the fixture script is not a regular file');
  if (stat.size > DI_FIXTURE_SOURCE_MAX_BYTES)
    throw new FixtureSourceError(`the fixture script exceeds ${DI_FIXTURE_SOURCE_MAX_BYTES} bytes`);
  let parsed: unknown;
  try {
    parsed = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch {
    throw new FixtureSourceError('the fixture script is not JSON');
  }
  return parseFixtureScript(parsed);
}

/**
 * The envelope with every timestamp re-based so that `generated_at` is `base` (and
 * `valid_until` is `base + max_age_seconds`). Pure: the script's own object is never mutated,
 * so every serve starts from the same recorded frame.
 */
export function rebaseEnvelope(envelope: Obj, base: number): Obj {
  const out = JSON.parse(JSON.stringify(envelope)) as Obj;
  out.requested_at = base;
  const response = out.response as Obj;
  const maxAge = typeof response.max_age_seconds === 'number' ? response.max_age_seconds : 60;
  response.generated_at = base;
  response.valid_until = base + maxAge;
  if (isObj(response.runtime_snapshot)) response.runtime_snapshot.observed_at = base;
  if (isObj(out.runtime_capture)) out.runtime_capture.observed_at = base;
  if (Array.isArray(response.sources))
    for (const s of response.sources) if (isObj(s)) s.observed_at = base;
  if (Array.isArray(response.missions))
    for (const m of response.missions)
      if (isObj(m) && Array.isArray(m.members))
        for (const member of m.members) if (isObj(member)) member.observed_at = base;
  return out;
}

function frameResult(frame: FixtureFrame, nowSeconds: number): BackendResult {
  switch (frame.kind) {
    case 'catalog': {
      const maxAge =
        typeof frame.envelope.response === 'object' &&
        typeof (frame.envelope.response as Obj).max_age_seconds === 'number'
          ? ((frame.envelope.response as Obj).max_age_seconds as number)
          : 60;
      const base = frame.timing === 'fresh' ? nowSeconds : nowSeconds - maxAge - frame.staleSeconds;
      const headers: Record<string, string> = { 'content-type': 'application/json; charset=utf-8' };
      const health = frame.health ?? (frame.timing === 'fresh' ? 'fresh' : 'stale');
      headers[FRESHNESS_HEALTH_HEADER] = health;
      if (frame.problem) headers[FRESHNESS_PROBLEM_HEADER] = frame.problem;
      return {
        kind: 'http',
        status: 200,
        headers,
        body: rebaseEnvelope(frame.envelope, base),
        bodyProblem: null,
      };
    }
    case 'body_not_json':
      return { kind: 'http', status: 200, headers: {}, body: undefined, bodyProblem: 'body_not_json' };
    case 'json_body':
      return {
        kind: 'http',
        status: 200,
        headers: { [FRESHNESS_HEALTH_HEADER]: 'fresh' },
        body: JSON.parse(JSON.stringify(frame.body)) as unknown,
        bodyProblem: null,
      };
    case 'http_error':
      return { kind: 'http', status: frame.status, headers: {}, body: undefined, bodyProblem: null };
    case 'transport_error':
      return { kind: 'transport_error', problem: frame.problem };
  }
}

/**
 * Serve a validated script: poll N receives the frame whose cumulative `repeat` range holds N;
 * after the last frame it holds the last one, or starts over (`after_last: "cycle"`).
 */
export function createFixtureSource(
  script: FixtureScript,
  nowMs: () => number = Date.now,
): FixtureSource {
  const sequence: FixtureFrame[] = [];
  for (const frame of script.frames) for (let i = 0; i < frame.repeat; i++) sequence.push(frame);
  let served = 0;
  // The endpoint is a placeholder that never leaves this module: request() ignores it.
  const endpoint: BackendEndpoint = { port: 0, token: '' };
  return {
    script,
    served: () => served,
    deps: {
      readDiscovery: () => ({ ok: true, endpoint }),
      request: () => {
        const index =
          script.afterLast === 'cycle'
            ? served % sequence.length
            : Math.min(served, sequence.length - 1);
        served++;
        return Promise.resolve(frameResult(sequence[index], Math.floor(nowMs() / 1000)));
      },
    },
  };
}
