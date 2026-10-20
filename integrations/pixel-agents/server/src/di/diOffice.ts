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

import { DI_RUNTIME_ZONE_REF } from '../../../core/src/constants.js';
import type { DiOfficeState } from '../../../core/src/messages.js';
import type { AgentStateStore } from '../agentStateStore.js';
import {
  DEFAULT_MAX_CONTEXT_TOKENS,
  DI_CATALOG_WAIT_SECONDS,
  DI_CONSUMER_POLL_INTERVAL_SECONDS,
} from '../constants.js';
import type { AgentState } from '../types.js';
import type { ArtifactReference, BackendResult, CodeReviewQuery } from './backendClient.js';
import { requestBackend } from './backendClient.js';
import type { BackendEndpoint, DiscoveryResult } from './backendDiscovery.js';
import { readBackendDiscovery } from './backendDiscovery.js';
import type { QueryContractVersion } from './catalogContract.js';
import {
  DEFAULT_MIN_POLL_INTERVAL_SECONDS,
  DEFAULT_QUERY_CONTRACT_VERSION,
} from './catalogContract.js';
import { parseEnvelope } from './catalogProjection.js';
import {
  admitAgainstView as admitReviewAgainstView,
  backendMessage as reviewBackendMessage,
  backendQuery,
  CODE_REVIEW_MIN_INTERVAL_MS,
  CODE_REVIEW_REQUEST_TIMEOUT_MS,
  officeRefusal as reviewOfficeRefusal,
  parseCodeReviewAnswer,
  parseCodeReviewRead,
} from './codeReview.js';
import {
  admitAgainstView,
  backendMessage,
  EVIDENCE_MIN_INTERVAL_MS,
  EVIDENCE_REQUEST_TIMEOUT_MS,
  executionBasisListed,
  officeRefusal,
  parseArtifactContentAnswer,
  parseEvidenceRead,
} from './evidenceContent.js';
import type { CharacterIdentity } from './identityMap.js';
import { DiIdentityMap } from './identityMap.js';
import { buildDiOfficeStateMessage } from './officeStateMessage.js';
import type { MemberPresentation, OfficeView, RuntimeAgentPresentation } from './officeView.js';
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
  /**
   * item 2: the backend's /artifact read. Defaults to the real client ONLY with the real catalog
   * client; a substituted backend (fixture preview, tests) without it has no content reads.
   */
  requestArtifact?: (endpoint: BackendEndpoint, ref: ArtifactReference) => Promise<BackendResult>;
  /**
   * item 3: the backend's /code_review read. Defaults to the real client ONLY with the real
   * catalog client; a substituted backend (fixture preview, tests) without it has no reviews.
   */
  requestReview?: (endpoint: BackendEndpoint, query: CodeReviewQuery) => Promise<BackendResult>;
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
  /** item 2: one content read at a time, at most one per EVIDENCE_MIN_INTERVAL_MS. */
  private evidenceInFlight = false;
  private lastEvidenceReadMs: number | null = null;
  /** item 3: one review read at a time, at most one per CODE_REVIEW_MIN_INTERVAL_MS. */
  private reviewInFlight = false;
  private lastReviewReadMs: number | null = null;
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
      requestArtifact:
        options.deps?.requestArtifact ??
        (options.deps?.request === undefined
          ? (endpoint, ref) =>
              requestBackend(endpoint, 'artifact', {
                artifact: ref,
                timeoutMs: EVIDENCE_REQUEST_TIMEOUT_MS,
              })
          : undefined),
      requestReview:
        options.deps?.requestReview ??
        (options.deps?.request === undefined
          ? (endpoint, query) =>
              requestBackend(endpoint, 'code_review', {
                review: query,
                timeoutMs: CODE_REVIEW_REQUEST_TIMEOUT_MS,
              })
          : undefined),
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
    const message = buildDiOfficeStateMessage(
      this.view,
      (member) => this.characterIdFor(member),
      (agent) => this.runtimeCharacterIdFor(agent),
    );
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

  private runtimeCharacterIdFor(agent: RuntimeAgentPresentation): number | null {
    const id = this.identities.numberFor(runtimeIdentity(agent));
    return this.characters.has(id) ? id : null;
  }

  /**
   * item 2: ONE evidence content read for the client that asked (`send` is that connection
   * only). Admitted against the CURRENT listing before the backend is asked, and again after it
   * answers; every outcome is exactly one diEvidenceContent. A malformed message is ignored here
   * (the message dispatcher refuses it before this is reached). Logs carry codes only.
   */
  async readEvidence(
    msg: Record<string, unknown>,
    send: (message: Record<string, unknown>) => void,
  ): Promise<void> {
    const ref = parseEvidenceRead(msg);
    if (ref === null) return;
    const reply = (m: ReturnType<typeof officeRefusal>): void =>
      send(m as unknown as Record<string, unknown>);
    if (this.evidenceInFlight) {
      reply(officeRefusal(ref, 'read_in_progress', 'another content read is in progress'));
      return;
    }
    const now = this.deps.nowMs();
    if (
      this.lastEvidenceReadMs !== null &&
      now - this.lastEvidenceReadMs < EVIDENCE_MIN_INTERVAL_MS
    ) {
      reply(officeRefusal(ref, 'read_rate_limited', 'at most one content read per second'));
      return;
    }
    const admitted = admitAgainstView(this.view, ref);
    if (!admitted.ok) {
      reply(officeRefusal(ref, admitted.problem, admitted.detail));
      return;
    }
    const requestArtifact = this.deps.requestArtifact;
    if (requestArtifact === undefined) {
      reply(
        officeRefusal(
          ref,
          'content_reads_not_supported_by_this_backend',
          'this office reads a substituted backend with no content route',
        ),
      );
      return;
    }
    const discovery = this.deps.readDiscovery(this.discoveryPath);
    if (!discovery.ok) {
      reply(officeRefusal(ref, 'backend_discovery_unavailable', discovery.problem));
      return;
    }
    this.evidenceInFlight = true;
    this.lastEvidenceReadMs = now;
    try {
      let result: BackendResult;
      try {
        result = await requestArtifact(discovery.endpoint, ref);
      } catch {
        result = { kind: 'transport_error', problem: 'backend_network_error' };
      }
      let answer: ReturnType<typeof officeRefusal>;
      if (result.kind === 'transport_error') {
        answer = officeRefusal(ref, 'backend_unavailable', result.problem);
      } else if (result.status !== 200) {
        answer = officeRefusal(ref, 'backend_refused_request', `HTTP ${result.status}`);
      } else {
        const parsed = parseArtifactContentAnswer(result.body, ref);
        answer = parsed.ok
          ? backendMessage(ref, parsed.answer)
          : officeRefusal(ref, parsed.problem, parsed.detail);
      }
      if (answer.state === 'admitted') {
        // The listing may have moved while the backend read: never relay against a new one.
        const again = admitAgainstView(this.view, ref);
        if (!again.ok) {
          answer = officeRefusal(ref, 'listing_moved_during_read', again.detail);
        } else {
          // Item 2: the backend must have read under the EXECUTION the office lists for this
          // Mission now (bound vs historical completed execution); anything else is not relayed.
          const listed = executionBasisListed(this.view, ref);
          if (answer.artifact?.executionBasis !== listed) {
            answer = officeRefusal(
              ref,
              'execution_basis_mismatch',
              `the backend read under ${answer.artifact?.executionBasis ?? 'none'}; the office lists ${listed}`,
            );
          }
        }
      }
      // Codes only: never the reference's content, locator or text.
      this.deps.log(
        `[Pixel Agents DI] evidence read: ${answer.decidedBy} ${answer.problem ?? 'admitted'}`,
      );
      reply(answer);
    } finally {
      this.evidenceInFlight = false;
    }
  }

  /**
   * item 3: ONE live code review read for the client that asked (`send` is that connection only).
   * Admitted against the CURRENT listing before the backend is asked, and again after it answers;
   * every outcome is exactly one diCodeReview. A malformed message is ignored here (the message
   * dispatcher refuses it before this is reached). Logs carry codes only — never a path, a
   * branch, a file name or a line of a patch.
   */
  async readCodeReview(
    msg: Record<string, unknown>,
    send: (message: Record<string, unknown>) => void,
  ): Promise<void> {
    const ref = parseCodeReviewRead(msg);
    if (ref === null) return;
    const reply = (m: ReturnType<typeof reviewOfficeRefusal>): void =>
      send(m as unknown as Record<string, unknown>);
    if (this.reviewInFlight) {
      reply(reviewOfficeRefusal(ref, 'read_in_progress', 'another review read is in progress'));
      return;
    }
    const now = this.deps.nowMs();
    if (
      this.lastReviewReadMs !== null &&
      now - this.lastReviewReadMs < CODE_REVIEW_MIN_INTERVAL_MS
    ) {
      reply(reviewOfficeRefusal(ref, 'read_rate_limited', 'reviews are read at a bounded rate'));
      return;
    }
    const admitted = admitReviewAgainstView(this.view, ref);
    if (!admitted.ok) {
      reply(reviewOfficeRefusal(ref, admitted.problem, admitted.detail));
      return;
    }
    const requestReview = this.deps.requestReview;
    if (requestReview === undefined) {
      reply(
        reviewOfficeRefusal(
          ref,
          'reviews_not_supported_by_this_backend',
          'this office reads a substituted backend with no review route',
        ),
      );
      return;
    }
    const discovery = this.deps.readDiscovery(this.discoveryPath);
    if (!discovery.ok) {
      reply(reviewOfficeRefusal(ref, 'backend_discovery_unavailable', discovery.problem));
      return;
    }
    this.reviewInFlight = true;
    this.lastReviewReadMs = now;
    try {
      let result: BackendResult;
      try {
        result = await requestReview(discovery.endpoint, backendQuery(ref));
      } catch {
        result = { kind: 'transport_error', problem: 'backend_network_error' };
      }
      let answer: ReturnType<typeof reviewOfficeRefusal>;
      if (result.kind === 'transport_error') {
        answer = reviewOfficeRefusal(ref, 'backend_unavailable', result.problem);
      } else if (result.status !== 200) {
        answer = reviewOfficeRefusal(ref, 'backend_refused_request', `HTTP ${result.status}`);
      } else {
        const parsed = parseCodeReviewAnswer(result.body, ref);
        answer = parsed.ok
          ? reviewBackendMessage(ref, parsed.answer)
          : reviewOfficeRefusal(ref, parsed.problem, parsed.detail);
      }
      if (answer.state === 'observed') {
        // The listing may have moved while the backend read: never relay against a new one.
        const again = admitReviewAgainstView(this.view, ref);
        if (!again.ok) {
          answer = reviewOfficeRefusal(ref, 'listing_moved_during_read', again.detail);
        }
      }
      // Codes only: never the reference, a path, a branch or any patch text.
      this.deps.log(
        `[Pixel Agents DI] code review read: ${answer.decidedBy} ${answer.problem ?? 'observed'}`,
      );
      reply(answer);
    } finally {
      this.reviewInFlight = false;
    }
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

  /**
   * Characters exist exactly for LIVE members and for VERIFIED runtime agents (item 07: not team
   * members, seated in their own zone); everything else is removed. One physical agent renders
   * once: the producer already excludes a runtime agent a team represents, and a runtime agent
   * whose exact (agent, session) is a live member's is skipped here too.
   */
  private projectCharacters(view: OfficeView): void {
    const wanted = new Map<number, { ref: string; folderName: string }>();
    const memberIdentities = new Set<string>();
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
          wanted.set(id, { ref: member.memberRef, folderName: mission.missionRef });
          if (member.sessionRecorded.state === 'observed')
            memberIdentities.add(JSON.stringify([member.agent, member.sessionRecorded.value]));
        }
      }
      for (const agent of view.runtimeAgents.agents) {
        if (!agent.presence.live) continue;
        if (memberIdentities.has(JSON.stringify([agent.agent, agent.session]))) continue;
        wanted.set(this.identities.numberFor(runtimeIdentity(agent)), {
          ref: agent.agentRef,
          folderName: DI_RUNTIME_ZONE_REF,
        });
      }
    }
    for (const id of [...this.characters.keys()]) {
      if (!wanted.has(id)) {
        this.characters.delete(id);
        this.store.delete(id);
      }
    }
    for (const [id, { ref, folderName }] of wanted) {
      if (this.characters.has(id)) continue;
      this.characters.set(id, ref);
      this.store.set(id, makeCharacterState(id, ref, folderName));
    }
  }
}

/** item 07: the canonical identity tuple of a runtime agent's character. */
function runtimeIdentity(agent: RuntimeAgentPresentation): CharacterIdentity {
  return {
    coverage: 'runtime',
    sourceId: agent.sourceId,
    missionRef: agent.rootRef,
    taskId: null,
    memberRef: agent.agentRef,
  };
}

/** A store entry for one live character. It carries identity only — no transcript, no tools. */
function makeCharacterState(id: number, ref: string, folderName: string): AgentState {
  return {
    id,
    sessionId: ref,
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
    folderName,
    lastDataAt: 0,
    linesProcessed: 0,
    seenUnknownRecordTypes: new Set(),
    hookDelivered: false,
    contextTokens: 0,
    maxContextTokens: DEFAULT_MAX_CONTEXT_TOKENS,
  };
}
