/**
 * THE HERDR CONTROL'S SERVER SIDE: what the office's Herdr buttons reach.
 *
 * A client sends `diHerdrRequest`: EXACTLY `type`, `seq`, `op` and `missionRef`, `op` one of two closed operations. This module answers the ASKING connection with exactly one `diHerdrReply`, correlated by
 * seq and op, for every message whose type, seq and op are valid (a refusal and an office without Herdr targeting included). A message outside that domain gets no reply and nothing is invented for it.
 *
 *   - `status`: is a launch available for this Mission RIGHT NOW? It runs the SAME admission the launch runs (herdrLaunch.ts `check()`): the target, the terminal, the Mission's binding read afresh, its identity
 *     verdict, Herdr's live agent list. It writes, focuses and opens NOTHING. The office's Herdr controls are enabled by this answer and by nothing else.
 *   - `launch`: the route (herdrLaunch.ts `launch()`): admission again, then the launcher file, the workspace focus with its read-back, and the terminal. Its real outcome, refusal words included, is the reply.
 *
 * What the client names is a Mission reference. It is matched against the office's OWN latest catalog view and is never a path, a pane, a workspace or an argument. The Herdr server, the herdr executable and
 * the HOME are not the client's to say: they are the three flags the office was started with, or the route is "not configured" and says which flags it is waiting for.
 *
 * ONE request at a time, and a launch is not repeated within MIN_LAUNCH_INTERVAL_MS: a double click is one window, not two. Everything that can throw is caught and answered as a refusal.
 *
 * THIS MODULE SENDS NO INPUT. It starts no process itself (herdrProcess.ts, injected, is the entry's only process module), and it offers no op that types, prompts or sends keys. Its log lines carry
 * problem codes only: never a path, a session, a pane or a workspace id.
 */
import type { DiHerdrAnswer, DiHerdrOp, DiHerdrReply } from '../../../core/src/messages.js';
import type {
  HerdrAttachTarget,
  HerdrCheckResult,
  HerdrLaunchEffects,
  HerdrLaunchResult,
  NodeLaunchApis,
} from './herdrLaunch.js';
import { createHerdrLauncher, nodeHerdrLaunchEffects } from './herdrLaunch.js';
import type { HerdrConfig, LauncherDirFs, StateContainment } from './herdrTarget.js';
import { HERDR_FLAGS, herdrBindingFromView, isMissionRef, launcherDirProblem } from './herdrTarget.js';
import type { OfficeView } from './officeView.js';

const MAX_SEQ = 2147483647;
/** One terminal window per click: a second launch inside this window is refused, not queued. */
export const MIN_LAUNCH_INTERVAL_MS = 3000;
/** The reply's `reason` is bounded by the contract (core/asyncapi.yaml, DiHerdrReply). */
const MAX_REASON_CHARS = 2000;
const OPERATIONS: readonly DiHerdrOp[] = ['status', 'launch'];
const REQUEST_KEYS = ['type', 'seq', 'op', 'missionRef'] as const;
/** The problem codes THIS module produces beside the route's own refusals: the only ones its log lines carry. */
const OWN_CODES: ReadonlySet<string> = new Set([
  'request_invalid',
  'herdr_not_configured',
  'office_busy',
  'launch_rate_limited',
  'launcher_directory_unusable',
  'herdr_route_failed',
]);

export type ParsedHerdrRequest =
  | { ok: true; seq: number; op: DiHerdrOp; missionRef: string }
  | { ok: false; seq: number | null; op: DiHerdrOp | null; reason: string };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * A `diHerdrRequest`, EXACTLY: the four keys, each in its closed grammar. Anything else is refused, never repaired. A refusal carries this message's seq and op only when its type, seq and op are ALL
 * valid (the correlation domain); outside it neither is invented.
 */
export function parseHerdrRequest(msg: Record<string, unknown>): ParsedHerdrRequest {
  const seq =
    typeof msg.seq === 'number' && Number.isInteger(msg.seq) && msg.seq >= 0 && msg.seq <= MAX_SEQ ? msg.seq : null;
  const op = typeof msg.op === 'string' && (OPERATIONS as readonly string[]).includes(msg.op) ? (msg.op as DiHerdrOp) : null;
  const correlated = msg.type === 'diHerdrRequest' && seq !== null && op !== null;
  const refuse = (reason: string): ParsedHerdrRequest => ({
    ok: false,
    seq: correlated ? seq : null,
    op: correlated ? op : null,
    reason,
  });
  if (msg.type !== 'diHerdrRequest') return refuse('not a diHerdrRequest');
  if (seq === null) return refuse('seq must be a non-negative integer');
  if (op === null) return refuse('op is not one of the two operations');
  if (!isObject(msg) || Object.keys(msg).length !== REQUEST_KEYS.length || !REQUEST_KEYS.every((key) => Object.hasOwn(msg, key))) {
    return refuse('a Herdr request takes exactly type, seq, op and missionRef');
  }
  if (!isMissionRef(msg.missionRef)) return refuse('missionRef is not a Mission reference');
  return { ok: true, seq, op, missionRef: msg.missionRef };
}

function reply(
  seq: number,
  op: DiHerdrOp,
  state: DiHerdrReply['state'],
  problem: string | null,
  reason: string | null,
  answer: DiHerdrAnswer | null,
): DiHerdrReply {
  return { type: 'diHerdrReply', seq, op, state, problem, reason: reason === null ? null : reason.slice(0, MAX_REASON_CHARS), answer };
}

/** The reply to a message this office will not act on: correlated by ITS OWN seq and op inside the correlation domain; null, NO reply, outside it. */
export function invalidRequestReply(parsed: Extract<ParsedHerdrRequest, { ok: false }>): DiHerdrReply | null {
  if (parsed.seq === null || parsed.op === null) return null;
  return reply(parsed.seq, parsed.op, 'refused', 'request_invalid', parsed.reason, null);
}

/** The reply when this office was started without Herdr targeting: it names exactly what it is waiting for. Nothing was attempted. */
export function notConfiguredReply(parsed: Extract<ParsedHerdrRequest, { ok: true }>): DiHerdrReply {
  return reply(
    parsed.seq,
    parsed.op,
    'unavailable',
    'herdr_not_configured',
    `Herdr is not set up in this office: it was started without ${HERDR_FLAGS.join(', ')}. Start it with all three (absolute paths: the herdr executable, the HOME Herdr runs under, and Herdr's API socket inside that HOME) to enable the Herdr terminal. Nothing was attempted.`,
    null,
  );
}

/** `status`: the same admission the launch passes, with nothing done. */
export function replyFromCheck(parsed: Extract<ParsedHerdrRequest, { ok: true }>, result: HerdrCheckResult): DiHerdrReply {
  if (!result.available) return reply(parsed.seq, parsed.op, 'refused', result.refusal, result.detail, null);
  return reply(parsed.seq, parsed.op, 'ok', null, null, {
    agent: result.agent,
    identityBasis: result.identityBasis,
    workspaceId: result.workspaceId,
    windowMinCols: result.window.cols,
    windowMinRows: result.window.rows,
    workspaceFocus: 'not_performed',
    matchedPaneId: result.matchedPaneId,
    paneFocus: 'not_performed',
    terminal: result.terminal,
    terminalLabel: result.label,
    terminalState: null,
  });
}

/** `launch`: the route's real outcome. A refusal carries the route's own detail text unchanged. */
export function replyFromLaunch(parsed: Extract<ParsedHerdrRequest, { ok: true }>, result: HerdrLaunchResult): DiHerdrReply {
  if (!result.launched) return reply(parsed.seq, parsed.op, 'refused', result.refusal, result.detail, null);
  return reply(parsed.seq, parsed.op, 'ok', null, null, {
    agent: result.agent,
    identityBasis: result.identityBasis,
    workspaceId: result.workspaceId,
    windowMinCols: result.window.cols,
    windowMinRows: result.window.rows,
    workspaceFocus: 'focused',
    matchedPaneId: result.matchedPaneId,
    paneFocus: result.paneFocus,
    terminal: result.terminal.terminal,
    terminalLabel: result.terminal.label,
    terminalState: result.terminal.state,
  });
}

/** The problem as a log line may carry it: the route's refusal codes and this module's own codes, nothing else. */
function loggable(problem: string | null, known: ReadonlySet<string>): string {
  if (problem === null) return 'ok';
  return known.has(problem) || OWN_CODES.has(problem) ? problem : 'unrecognised_code';
}

/** Refusals after which a launcher file, or a moved view, may exist: they count as an attempt for the launch interval. */
const ATTEMPT_REFUSALS: ReadonlySet<string> = new Set(['launcher_not_written', 'focus_not_established', 'launch_failed']);
const ROUTE_CODES: ReadonlySet<string> = new Set([
  'target_invalid',
  'terminal_unavailable',
  'no_binding',
  'identity_not_verified',
  'session_unknown',
  'listing_unreadable',
  'not_live',
  'live_more_than_once',
  'workspace_unknown',
  'geometry_unknown',
  'launcher_not_written',
  'focus_not_established',
  'launch_failed',
]);

export interface HerdrRelayDeps {
  /** The three flags, validated: null when the office was started without them. */
  config: HerdrConfig | null;
  /** The directory launcher files are written in: inside the office's own state root. */
  launcherDir: string;
  /** The office's latest catalog view, read at the moment of each request. */
  getView(): OfficeView;
  /** The real Node APIs (herdrProcess.ts), or stand-ins in tests. */
  apis: NodeLaunchApis;
  dirFs: LauncherDirFs;
  /**
   * The office's EXISTING state write guard (diStateRoot.ts `makeDiStateWriteGuard(<the validated physical state root>)`). REQUIRED, with no default: it is applied to the launcher directory before it is
   * looked at, before it is created and after, and to the launcher FILE before it is written, so a redirected ancestor, a symlinked directory or a symlinked file cannot carry a runnable file out of the
   * state root. It resolves physically AT THE MOMENT of each use, so a root redirected after startup is caught too.
   */
  contain: StateContainment;
  nowMs(): number;
  log(line: string): void;
}

export class DiHerdrRequests {
  private inFlight = false;
  private lastAttemptMs: number | null = null;

  constructor(private readonly deps: HerdrRelayDeps) {}

  /** The effects for ONE request: the real ones, with the launcher directory checked (and created owner-only) right before the launcher is written. */
  private effectsFor(missionRef: string): HerdrLaunchEffects {
    const base = nodeHerdrLaunchEffects(this.deps.apis, async () => herdrBindingFromView(this.deps.getView(), missionRef));
    return {
      ...base,
      writeLauncher: async (filePath, text, options) => {
        // 1. the directory: contained, then created owner-only if missing, then contained again, then owned/closed
        const problem = launcherDirProblem(this.deps.launcherDir, this.deps.dirFs, true, this.deps.contain);
        if (problem !== null) throw new Error(problem);
        // 2. the FILE, immediately before it is written: its parent is resolved physically now and must still be inside the root, and the file itself must not be a link
        try {
          this.deps.contain(filePath);
        } catch (err) {
          throw new Error(`the launcher file ${filePath} is refused by the state-root containment guard (${(err as Error).message})`);
        }
        await base.writeLauncher(filePath, text, options);
      },
    };
  }

  /** Answer one `diHerdrRequest` to the asking connection only. */
  async handle(msg: Record<string, unknown>, send: (message: Record<string, unknown>) => void): Promise<void> {
    const answer = (r: DiHerdrReply): void => send(r as unknown as Record<string, unknown>);
    const parsed = parseHerdrRequest(msg);
    if (!parsed.ok) {
      this.deps.log(`[Pixel Agents DI] herdr request refused: ${parsed.op ?? 'unknown'} invalid`);
      const refusal = invalidRequestReply(parsed);
      if (refusal !== null) answer(refusal);
      return;
    }
    const { config } = this.deps;
    if (config === null) {
      answer(notConfiguredReply(parsed));
      return;
    }
    if (this.inFlight) {
      answer(reply(parsed.seq, parsed.op, 'refused', 'office_busy', 'Another Herdr request from this office is still running. Nothing was done for this request; ask again when it has answered.', null));
      return;
    }
    const now = this.deps.nowMs();
    if (parsed.op === 'launch' && this.lastAttemptMs !== null && now - this.lastAttemptMs < MIN_LAUNCH_INTERVAL_MS) {
      answer(
        reply(
          parsed.seq,
          parsed.op,
          'refused',
          'launch_rate_limited',
          `A launch was handled ${Math.max(0, Math.round((now - this.lastAttemptMs) / 100) / 10)} s ago, and one click opens one window. Nothing was done for this request; wait a moment and click again if you want another.`,
          null,
        ),
      );
      return;
    }
    // Before any route step: a launcher directory that is not safe to write a runnable file into is refused here, with nothing created.
    const directory = launcherDirProblem(this.deps.launcherDir, this.deps.dirFs, false, this.deps.contain);
    if (directory !== null) {
      answer(reply(parsed.seq, parsed.op, 'refused', 'launcher_directory_unusable', `Nothing was opened: ${directory}.`, null));
      this.deps.log(`[Pixel Agents DI] herdr request: ${parsed.op} refused launcher_directory_unusable`);
      return;
    }
    const target: HerdrAttachTarget = {
      herdrBin: config.bin,
      home: config.home,
      socketPath: config.socketPath,
      launcherDir: this.deps.launcherDir,
      terminal: 'terminal',
    };
    this.inFlight = true;
    let out: DiHerdrReply;
    try {
      const launcher = createHerdrLauncher(target, this.effectsFor(parsed.missionRef));
      if (parsed.op === 'status') {
        out = replyFromCheck(parsed, await launcher.check());
      } else {
        const result = await launcher.launch();
        if (result.launched || ATTEMPT_REFUSALS.has(result.refusal)) this.lastAttemptMs = this.deps.nowMs();
        out = replyFromLaunch(parsed, result);
      }
    } catch (err) {
      if (parsed.op === 'launch') this.lastAttemptMs = this.deps.nowMs();
      out = reply(
        parsed.seq,
        parsed.op,
        'refused',
        'herdr_route_failed',
        `The Herdr route stopped on an unexpected error (${err instanceof Error ? err.message : 'not an Error'}).${parsed.op === 'launch' ? ' What it had done before stopping is not known: the operator\'s view may have moved and a launcher file may have been written.' : ' Nothing was changed.'}`,
        null,
      );
    } finally {
      this.inFlight = false;
    }
    this.deps.log(`[Pixel Agents DI] herdr request: ${out.op} ${out.state} ${loggable(out.problem, ROUTE_CODES)}`);
    answer(out);
  }
}
