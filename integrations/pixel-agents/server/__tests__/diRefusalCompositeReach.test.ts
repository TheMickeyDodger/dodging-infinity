/**
 * Can each of the four "Nothing was read." refusals reach the screen as a FILE refusal BESIDE an already-drawn overview? (per-body reachability, the sibling increment)
 *
 * The shape: a successful overview is read and drawn, the reader selects a file, and THAT request is refused. The webview's reducer attaches any refusal to the settled overview with no filter on the code, so
 * the question for each body is only whether the office can produce, or relay, its code for a file request after an overview. This file DEMONSTRATES it for each, through the real `DiOffice.readCodeReview`
 * (the one handler for every review message), with the exported member-bound review fixture (hash-pinned; FIXTURE evidence only, never live):
 *
 *  - backend_discovery_unavailable: the office reads the discovery file on EVERY review read, so it can be gone for the second request (office-origin, decided before the backend is asked);
 *  - member_listed_more_than_once and mission_listed_more_than_once: the office admits EVERY request against the CURRENT listing, so a listing that changed after the overview lists the member (or the Mission)
 *    twice for the second request (office-origin, decided before the backend is asked);
 *  - no_fresh_catalog_answer and mission_listed_more_than_once (the backend's own): the backend refuses a file request at its own admission (demonstrated in tests/test_refusal_file_view_reach.py); the office
 *    relays that refusal as the backend's, for the FILE view.
 *
 * It asserts nothing about wording: that is the webview's (diRefusalBodiesComposite.test.ts).
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentStateStore } from '../src/agentStateStore.js';
import type { BackendResult } from '../src/di/backendClient.js';
import type { DiscoveryResult } from '../src/di/backendDiscovery.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import type { CodeReviewRef } from '../src/di/codeReview.js';
import { reviewOfferFor } from '../src/di/codeReview.js';
import { DiOffice } from '../src/di/diOffice.js';
import type { MemberPresentation, MissionPresentation, OfficeView } from '../src/di/officeView.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const FIXTURE = path.join(__dirname, 'fixtures/di-backend/v2/code-review-v1.FIXTURE.json');
const FIXTURE_SHA256 = 'ad29c7dcf1b8b3d7bc764db9ac9f0717efacbed2c98fb98e2786170b8325ab25';
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const ENDPOINT: DiscoveryResult = { ok: true, endpoint: { port: 1, token: 'secret-not-logged' } };

function exported(): Raw {
  const bytes = fs.readFileSync(FIXTURE);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== FIXTURE_SHA256) throw new Error(`FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}
const catalogAnswer = (raw: Raw): Raw => raw.catalog_answer as Raw;
const generatedAt = (answer: Raw): number => Number((answer.response as Raw).generated_at);
const missionRaw = (answer: Raw): Raw => ((answer.response as Raw).missions as Raw[])[0];

function viewOf(answer: Raw): OfficeView {
  const parsed = parseEnvelope(answer);
  if (!parsed.ok) throw new Error(`envelope refused: ${JSON.stringify(parsed.verdict)}`);
  return buildOfficeView(parsed.envelope, FRESH, generatedAt(answer), 2);
}
function theMission(view: OfficeView): MissionPresentation {
  if (view.missions.state !== 'observed') throw new Error(`missions ${view.missions.state}`);
  return view.missions.value[0];
}
function theMember(view: OfficeView): MemberPresentation {
  const mission = theMission(view);
  if (mission.members.state !== 'observed') throw new Error('members not observed');
  return mission.members.value[0];
}
/** The overview request for the fixture's one agent, exactly as the client would send it, and the FILE request for one of the files its overview lists. */
function requests(raw: Raw): { overview: CodeReviewRef; file: CodeReviewRef; filePath: string } {
  const view = viewOf(catalogAnswer(raw));
  const offer = reviewOfferFor(theMission(view), theMember(view));
  if (offer.state !== 'offered') throw new Error(`not offered: ${offer.reason}`);
  const overview: CodeReviewRef = { missionRef: theMission(view).missionRef, memberRef: theMember(view).memberRef, taskId: offer.taskId, listing: offer.listing, requestId: 'w.1' };
  const filePath = Object.keys(raw.files as Raw)[0]!;
  const answer = (raw.files as Raw)[filePath] as Raw;
  return { overview, file: { ...overview, snapshot: String(answer.snapshot), fileId: String(answer.file_id), requestId: 'w.2' }, filePath };
}
const messageOf = (ref: CodeReviewRef): Raw => ({ type: 'diCodeReviewRead', ...ref });
const http200 = (body: unknown): BackendResult => ({ kind: 'http', status: 200, headers: {}, body, bodyProblem: null });

describe('a FILE request after an observed overview: the four bodies\' codes are each reachable', () => {
  let clockMs = 0;
  const feed: { answer: Raw } = { answer: {} };
  const discoveryNow: { value: DiscoveryResult } = { value: ENDPOINT };
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  async function office(requestReview: (endpoint: unknown, q: unknown) => Promise<BackendResult>) {
    const answer = clone(catalogAnswer(exported()));
    clockMs = generatedAt(answer) * 1000;
    feed.answer = answer;
    discoveryNow.value = ENDPOINT;
    const o = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => discoveryNow.value,
        request: async () => ({ kind: 'http', status: 200, headers: { 'x-di-freshness-health': 'fresh' }, body: clone(feed.answer), bodyProblem: null }),
        nowMs: () => clockMs,
        log: () => undefined,
        requestReview: vi.fn(requestReview),
      },
    });
    await o.poll();
    return o;
  }

  /** The backend: the exported overview for the overview request, the exported file answer for the file request; `onFile` may replace the file answer. */
  function backend(raw: Raw, onFile?: (fileBody: Raw) => Raw) {
    return vi.fn(async (_endpoint: unknown, query: unknown) => {
      const q = query as Raw;
      if (q.fileId === undefined) return http200(clone(raw.overview as Raw));
      const body = clone(((raw.files as Raw)[Object.keys(raw.files as Raw)[0]!]) as Raw);
      return http200(onFile ? onFile(body) : body);
    });
  }

  /** Overview first (observed and relayed), then the world moves by `move`, then the FILE request. Returns what the second request was answered with and how often the backend was asked. */
  async function overviewThenFile(raw: Raw, backendFn: ReturnType<typeof backend>, move: (o: DiOffice) => Promise<void> | void) {
    const o = await office(backendFn);
    const r = requests(raw);
    const sent: Raw[] = [];
    await o.readCodeReview(messageOf(r.overview), (m) => sent.push(m));
    expect(sent[0], 'the overview is observed first: this is the composite shape').toMatchObject({ state: 'observed', view: 'overview' });
    clockMs += 10_000; // past the bounded read rate
    await move(o);
    await o.readCodeReview(messageOf(r.file), (m) => sent.push(m));
    o.stop();
    return { second: sent[1]!, sentCount: sent.length, backendCalls: backendFn.mock.calls.length };
  }

  it('backend_discovery_unavailable: the discovery file is read on EVERY review read, so it can be gone for the file request; the backend is not asked again', async () => {
    const raw = exported();
    const b = backend(raw);
    const { second, sentCount, backendCalls } = await overviewThenFile(raw, b, () => {
      discoveryNow.value = { ok: false, problem: 'discovery_absent' };
    });
    expect(sentCount).toBe(2);
    expect(second).toMatchObject({ state: 'refused', decidedBy: 'office', problem: 'backend_discovery_unavailable', view: 'file' });
    expect(backendCalls, 'only the overview asked the backend').toBe(1);
  });

  it('member_listed_more_than_once: the office admits EVERY request against the CURRENT listing, so a member listed twice since the overview refuses the file request; the backend is not asked again', async () => {
    const raw = exported();
    const b = backend(raw);
    const { second, backendCalls } = await overviewThenFile(raw, b, async (o) => {
      const twice = clone(catalogAnswer(raw));
      const roster = missionRaw(twice).members as Raw[];
      roster.push(clone(roster[0]!));
      feed.answer = twice;
      await o.poll();
    });
    expect(second).toMatchObject({ state: 'refused', decidedBy: 'office', problem: 'member_listed_more_than_once', view: 'file' });
    expect(backendCalls).toBe(1);
  });

  it('mission_listed_more_than_once (office-origin): a Mission listed twice since the overview refuses the file request; the backend is not asked again', async () => {
    const raw = exported();
    const b = backend(raw);
    const { second, backendCalls } = await overviewThenFile(raw, b, async (o) => {
      const twice = clone(catalogAnswer(raw));
      const missions = (twice.response as Raw).missions as Raw[];
      missions.push(clone(missions[0]!));
      feed.answer = twice;
      await o.poll();
    });
    expect(second).toMatchObject({ state: 'refused', decidedBy: 'office', problem: 'mission_listed_more_than_once', view: 'file' });
    expect(backendCalls).toBe(1);
  });

  for (const code of ['no_fresh_catalog_answer', 'mission_listed_more_than_once']) {
    it(`${code} (backend-origin): the office relays the backend's refusal of the FILE request as the backend's own, beside the already-observed overview`, async () => {
      const raw = exported();
      const b = backend(raw, (file) => ({ ...file, state: 'refused', problem: code, detail: 'because', target: null, comparison: null, overview: null, file: null }));
      const { second, backendCalls } = await overviewThenFile(raw, b, () => undefined);
      expect(second).toMatchObject({ state: 'refused', decidedBy: 'backend', problem: code, view: 'file' });
      expect(backendCalls, 'the backend was asked for the overview and for the file').toBe(2);
    });
  }

  it('the control: with nothing moved, the file request is NOT refused with any of these codes', async () => {
    const raw = exported();
    const { second } = await overviewThenFile(raw, backend(raw), () => undefined);
    expect(second).toMatchObject({ view: 'file' });
    expect(['backend_discovery_unavailable', 'member_listed_more_than_once', 'mission_listed_more_than_once', 'no_fresh_catalog_answer']).not.toContain(second.problem);
  });
});
