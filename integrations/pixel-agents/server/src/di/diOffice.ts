/**
 * The DI office's backend consumer and office projection.
 *
 * Loop: read the backend's discovery file (read-only) -> GET /catalog?wait_seconds=<policy>
 * with the bearer token -> parse the envelope -> read the serve-time verdict -> build
 * the OfficeView -> project LIVE members into the agent store as characters.
 *
 * - Cadence: the next request starts after the latest response COMPLETES, waiting the
 *   LARGER of our consumer policy (DI_CONSUMER_POLL_INTERVAL_SECONDS) and the backend's
 *   declared `limits.min_poll_interval_seconds`; one request in flight, never queued — see
 *   the invariant at scheduleNext. The backend's own cache answers within its interval
 *   (`served: "cached"`); nothing here restamps it.
 * - A local re-evaluation every second applies the inclusive `now <= valid_until`
 *   floor to the LAST answer, so a response expires on time even between polls. It
 *   can only take liveness away; it never extends a response or keeps members alive.
 * - Characters exist exactly while their member is live. Numbers come from the
 *   identity map and are stable, so a member that returns gets its own character back.
 * - Nothing here starts, supervises or owns the backend. An absent discovery file or an
 *   unreachable adapter is an honest "unavailable" state that the next poll re-checks.
 * - Log lines carry problem codes and counts only — never the token, never content.
 */

import type { DiOfficeState } from '../../../core/src/messages.js';
import type { AgentStateStore } from '../agentStateStore.js';
import {
  DEFAULT_MAX_CONTEXT_TOKENS,
  DI_CATALOG_WAIT_SECONDS,
  DI_CONSUMER_POLL_INTERVAL_SECONDS,
} from '../constants.js';
import type { AgentState } from '../types.js';
import type { BackendResult } from './backendClient.js';
import { requestBackend } from './backendClient.js';
import type { BackendEndpoint, DiscoveryResult } from './backendDiscovery.js';
import { readBackendDiscovery } from './backendDiscovery.js';
import type { QueryContractVersion } from './catalogContract.js';
import {
  DEFAULT_MIN_POLL_INTERVAL_SECONDS,
  DEFAULT_QUERY_CONTRACT_VERSION,
} from './catalogContract.js';
import { parseEnvelope } from './catalogProjection.js';
import { DiIdentityMap } from './identityMap.js';
import { buildDiOfficeStateMessage } from './officeStateMessage.js';
import type { MemberPresentation, MissionPresentation, OfficeView } from './officeView.js';
import { buildOfficeView, unavailableView } from './officeView.js';
import type { ServeTimeVerdict } from './serveTimeVerdict.js';
import { readServeTimeVerdict } from './serveTimeVerdict.js';

const REQUEST_TIMEOUT_MS = 15_000;
const REEVALUATE_INTERVAL_MS = 1_000;

export interface DiOfficeDeps {
  readDiscovery: (filePath: string) => DiscoveryResult;
  request: (endpoint: BackendEndpoint) => Promise<BackendResult>;
  nowMs: () => number;
  log: (line: string) => void;
}

export interface DiOfficeOptions {
  store: AgentStateStore;
  discoveryPath: string;
  /** The ONE catalog contract version this process reads (launch-time; default 1). */
  contractVersion?: QueryContractVersion;
  /** EXPLICIT fixture preview (--fixture-preview): every office message carries fixtureMode. */
  fixturePreview?: boolean;
  deps?: Partial<DiOfficeDeps>;
}

interface LastAnswer {
  envelope: Parameters<typeof buildOfficeView>[0];
  verdict: ServeTimeVerdict;
}

export class DiOffice {
  private readonly store: AgentStateStore;
  private readonly discoveryPath: string;
  private readonly contractVersion: QueryContractVersion;
  private readonly fixturePreview: boolean;
  private readonly deps: DiOfficeDeps;
  private readonly identities = new DiIdentityMap();
  /** Live characters: number -> member ref. */
  private readonly characters = new Map<number, string>();
  private view: OfficeView;
  private lastAnswer: LastAnswer | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private reevaluateTimer: ReturnType<typeof setInterval> | null = null;
  private inFlight = false;
  private stopped = true;
  private lastLogged = '';

  constructor(options: DiOfficeOptions) {
    this.store = options.store;
    this.discoveryPath = options.discoveryPath;
    this.contractVersion = options.contractVersion ?? DEFAULT_QUERY_CONTRACT_VERSION;
    this.fixturePreview = options.fixturePreview === true;
    this.deps = {
      readDiscovery: options.deps?.readDiscovery ?? readBackendDiscovery,
      request:
        options.deps?.request ??
        ((endpoint) =>
          requestBackend(endpoint, 'catalog', {
            waitSeconds: DI_CATALOG_WAIT_SECONDS,
            timeoutMs: REQUEST_TIMEOUT_MS,
          })),
      nowMs: options.deps?.nowMs ?? Date.now,
      log: options.deps?.log ?? ((line) => console.log(line)),
    };
    this.view = unavailableView('not_started', this.nowSeconds(), this.contractVersion);
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.reevaluateTimer = setInterval(() => this.reevaluate(), REEVALUATE_INTERVAL_MS);
    void this.poll();
  }

  stop(): void {
    this.stopped = true;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    if (this.reevaluateTimer) clearInterval(this.reevaluateTimer);
    this.pollTimer = null;
    this.reevaluateTimer = null;
  }

  getView(): OfficeView {
    return this.view;
  }

  /** Characters currently projected (number -> member ref), for diagnostics and tests. */
  liveCharacters(): ReadonlyMap<number, string> {
    return this.characters;
  }

  /** Send the current office state to one newly connected (authenticated) client. */
  sendOfficeState(send: (message: Record<string, unknown>) => void): void {
    send(this.officeStateMessage() as unknown as Record<string, unknown>);
  }

  /** The additive `diOfficeState` message for the current view. */
  officeStateMessage(): DiOfficeState {
    const message = buildDiOfficeStateMessage(this.view, (member) => this.characterIdFor(member));
    return this.fixturePreview ? { ...message, fixtureMode: true } : message;
  }

  private characterIdFor(member: MemberPresentation): number | null {
    // An unrecognized chain is never live and never gets an identity.
    if (member.coverage === 'unrecognized') return null;
    const id = this.identities.numberFor({
      coverage: member.coverage,
      sourceId: member.sourceId,
      missionRef: member.missionRef,
      taskId: member.taskId,
      memberRef: member.memberRef,
    });
    return this.characters.has(id) ? id : null;
  }

  /** One poll cycle. Public for tests; the loop schedules it itself. */
  async poll(): Promise<void> {
    if (this.inFlight) return;
    this.inFlight = true;
    try {
      const discovery = this.deps.readDiscovery(this.discoveryPath);
      if (!discovery.ok) {
        this.lastAnswer = null;
        this.apply(
          unavailableView(
            `discovery:${discovery.problem}`,
            this.nowSeconds(),
            this.contractVersion,
          ),
        );
        return;
      }
      const result = await this.deps.request(discovery.endpoint);
      this.consume(result);
    } catch {
      this.lastAnswer = null;
      this.apply(unavailableView('consumer_error', this.nowSeconds(), this.contractVersion));
    } finally {
      this.inFlight = false;
      this.scheduleNext();
    }
  }

  private consume(result: BackendResult): void {
    const now = this.nowSeconds();
    if (result.kind === 'transport_error') {
      this.lastAnswer = null;
      this.apply(unavailableView(`transport:${result.problem}`, now, this.contractVersion));
      return;
    }
    if (result.status !== 200) {
      this.lastAnswer = null;
      this.apply(unavailableView(`http_${result.status}`, now, this.contractVersion));
      return;
    }
    if (result.bodyProblem) {
      this.lastAnswer = null;
      this.apply(unavailableView(result.bodyProblem, now, this.contractVersion));
      return;
    }
    const parsed = parseEnvelope(result.body);
    if (!parsed.ok) {
      this.lastAnswer = null;
      this.apply(
        unavailableView(
          `${parsed.verdict.health}:${parsed.verdict.problem}`,
          now,
          this.contractVersion,
        ),
      );
      return;
    }
    const verdict = readServeTimeVerdict(result.headers);
    this.lastAnswer = { envelope: parsed.envelope, verdict };
    this.apply(buildOfficeView(parsed.envelope, verdict, now, this.contractVersion));
  }

  /** Re-apply the local floor to the last answer (never extends anything). */
  private reevaluate(): void {
    if (!this.lastAnswer || !this.view.live) return;
    const next = buildOfficeView(
      this.lastAnswer.envelope,
      this.lastAnswer.verdict,
      this.nowSeconds(),
      this.contractVersion,
    );
    if (!next.live) this.apply(next);
  }

  /**
   * INVARIANT — scheduling is DELIBERATELY response-COMPLETION-relative: this runs in
   * poll()'s `finally`, after the awaited request has finished, so the next request
   * starts `min_poll_interval_seconds` after the previous RESPONSE, not after the
   * previous start. The backend throttles any refresh that starts less than its
   * declared minimum after its last refresh START (`since < min`), so completion-relative
   * scheduling keeps the backend's `since` at the interval PLUS the previous request's
   * duration — strictly above the threshold. Making this start-relative (e.g. a
   * fixed-rate timer to "fix drift") would put every poll on the backend's cadence
   * threshold, where scheduling jitter decides between a refresh and a cached answer.
   *
   * The interval is the LARGER of two different facts: our consumer policy floor
   * (DI_CONSUMER_POLL_INTERVAL_SECONDS) and the backend's DECLARED minimum
   * (`limits.min_poll_interval_seconds`; the v3 default only before any response has
   * declared one). Replacing theirs with ours would silently poll faster than a backend
   * that ever declares a larger minimum.
   * Pinned by diOffice.test.ts "scheduling is completion-relative" and the policy tests.
   */
  private scheduleNext(): void {
    if (this.stopped) return;
    const seconds = Math.max(
      DI_CONSUMER_POLL_INTERVAL_SECONDS,
      this.view.minPollIntervalSeconds ?? DEFAULT_MIN_POLL_INTERVAL_SECONDS,
    );
    this.pollTimer = setTimeout(() => void this.poll(), seconds * 1000);
  }

  private nowSeconds(): number {
    return Math.floor(this.deps.nowMs() / 1000);
  }

  private apply(view: OfficeView): void {
    this.view = view;
    this.projectCharacters(view);
    this.store.broadcast(this.officeStateMessage() as unknown as Record<string, unknown>);
    const missions = view.missions.state === 'observed' ? view.missions.value.length : 0;
    const line =
      `[Pixel Agents DI] office: ${view.live ? 'LIVE' : 'not live'}` +
      (view.notLiveReason ? ` (${view.notLiveReason})` : '') +
      ` missions=${view.missions.state === 'observed' ? missions : view.missions.state}` +
      ` characters=${this.characters.size} coverage=direct_receipt_task`;
    if (line !== this.lastLogged) {
      this.lastLogged = line;
      this.deps.log(line);
    }
  }

  /** Characters exist exactly for LIVE members; everything else is removed. */
  private projectCharacters(view: OfficeView): void {
    const wanted = new Map<number, { mission: MissionPresentation; member: MemberPresentation }>();
    if (view.live && view.missions.state === 'observed') {
      for (const mission of view.missions.value) {
        if (!mission.presence.live || mission.members.state !== 'observed') continue;
        for (const member of mission.members.value) {
          if (!member.presence.live || member.coverage === 'unrecognized') continue;
          const id = this.identities.numberFor({
            coverage: member.coverage,
            sourceId: member.sourceId,
            missionRef: member.missionRef,
            taskId: member.taskId,
            memberRef: member.memberRef,
          });
          wanted.set(id, { mission, member });
        }
      }
    }
    for (const id of [...this.characters.keys()]) {
      if (!wanted.has(id)) {
        this.characters.delete(id);
        this.store.delete(id);
      }
    }
    for (const [id, { mission, member }] of wanted) {
      if (this.characters.has(id)) continue;
      this.characters.set(id, member.memberRef);
      this.store.set(id, makeCharacterState(id, mission, member));
    }
  }
}

/** A store entry for one live member. It carries identity only — no transcript, no tools. */
function makeCharacterState(
  id: number,
  mission: MissionPresentation,
  member: MemberPresentation,
): AgentState {
  return {
    id,
    sessionId: member.memberRef,
    isExternal: true,
    projectDir: '',
    jsonlFile: '',
    fileOffset: 0,
    lineBuffer: '',
    activeToolIds: new Set(),
    activeToolStatuses: new Map(),
    activeToolNames: new Map(),
    activeSubagentToolIds: new Map(),
    activeSubagentToolNames: new Map(),
    backgroundAgentToolIds: new Set(),
    isWaiting: false,
    permissionSent: false,
    hadToolsInTurn: false,
    folderName: mission.missionRef,
    lastDataAt: 0,
    linesProcessed: 0,
    seenUnknownRecordTypes: new Set(),
    hookDelivered: false,
    contextTokens: 0,
    maxContextTokens: DEFAULT_MAX_CONTEXT_TOKENS,
  };
}
