/**
 * The Mission reception desk (item 06): ONE attention view, derived SERVER-SIDE as a PURE function
 * of the office view. It projects existing canonical records only, reads nothing, writes nothing
 * and carries no control: the "route to act" is truthful text plus the exact canonical
 * identifiers an operator needs to act through the existing route, outside this office.
 *
 * Every category resolves, PER MISSION, to exactly one of:
 * - present:     a canonical field reports it (each cause kept distinguishable);
 * - known_empty: every field the category reads was readable and reports none;
 * - unsupported: not reported at this contract version, or by this source kind;
 * - unavailable: could not be read now (with the reason) — never "none".
 * A category headline adds `partial` for a mix, so a mix never reads as "none".
 *
 * Decisions use canonical fields only — never a display name, an objective, a timestamp or a
 * snapshot difference — and NEVER `lifecycleState` (the source's native literal, display only).
 * Member `activity` is runtime activity: `done` is not completion and is never read as such.
 */
import type {
  DiObservedValue,
  DiReception,
  DiReceptionCategory,
  DiReceptionCategoryId,
  DiReceptionCause,
  DiReceptionEntry,
  DiReceptionGap,
  DiReceptionNotice,
  DiReceptionOutcome,
  DiReportEntry,
} from '../../../core/src/messages.js';
import { DI_RECEPTION_MAX_ENTRIES } from '../constants.js';
import type { WorkflowPhase } from './catalogContract.js';
import type {
  MissionPresentation,
  MissionV2Presentation,
  Observation,
  OfficeView,
} from './officeView.js';

type CellOutcome = Exclude<DiReceptionOutcome, 'partial'>;

/** One canonical field read for one Mission: causes found, or why it could not say "none". */
interface Probe {
  causes: DiReceptionCause[];
  unknown: string[];
  unsupported: string[];
}

interface Ctx {
  /** Why the catalog answer is not fresh (null when fresh). */
  envelope: string | null;
  /** Why the runtime observation is not fresh (null when fresh). */
  runtime: string | null;
}

interface CategoryDef {
  id: DiReceptionCategoryId;
  label: string;
  statement: string;
  backing: string;
  route: string;
  /** Mission State / canonical review / declared-phase categories exist only in contract v2. */
  v2Only: boolean;
  /** For a v2-only category: its own contract-v1 reason and statement (else the default). */
  v1?: { reason: string; statement: string };
  /** null: no field of the catalog contract (v1 or v2) carries this category. */
  probes: Array<(m: MissionPresentation, ctx: Ctx) => Probe> | null;
}

export const RECEPTION_V1_REASON =
  'contract_v1:mission_state_and_canonical_review_are_contract_v2_blocks';
export const RECEPTION_NOT_IN_CONTRACT = 'not_in_catalog_contract:v1_and_v2';
/** Contract v1 carries no declared workflow phase (`missions[].workflow_phase` is v2-only). */
const RECEPTION_V1_WORKFLOW_PHASE_REASON = 'contract_v1:workflow_phase_is_a_contract_v2_field';
export const RECEPTION_V1_AUTHORIZATION_REASON =
  'contract_v1:pending_decisions_and_workflow_phase_are_contract_v2_fields';
/** Mission Core's own information holds (mission/observation.py INFORMATION_HOLDS): never blocking. */
const INFORMATION_HOLDS: ReadonlySet<string> = new Set(['review_unavailable', 'review_unknown']);
const INFORMATION_HOLD_LABEL = 'information hold (not blocking, per Mission Core)';
const REVIEW_INTEGRITY_CORROBORATED = 'linkage_corroborated';

const DISMISSAL_STATEMENT =
  'Hiding an entry is a preference of this view only: it writes nothing, resolves nothing and ' +
  'approves nothing. The entry returns while its record is present (always on reload) and as ' +
  'soon as its canonical content changes.';

// ── small builders ───────────────────────────────────────────────

const NONE: Probe = { causes: [], unknown: [], unsupported: [] };
const unknown = (reason: string): Probe => ({ causes: [], unknown: [reason], unsupported: [] });
const unsupported = (reason: string): Probe => ({ causes: [], unknown: [], unsupported: [reason] });
const found = (...causes: DiReceptionCause[]): Probe => ({ causes, unknown: [], unsupported: [] });
const d = (label: string, value: string | null): DiReportEntry => ({ label, value });
const yn = (v: boolean | null): string | null => (v === null ? null : String(v));
const num = (v: number | null): string | null => (v === null ? null : String(v));
const stale = (...reasons: Array<string | null | false>): string[] =>
  reasons.filter((r): r is string => typeof r === 'string');

/** A field absent for this Mission: not applicable to its kind is unsupported; else unknown. */
function absent(prefix: string, state: string, reason: string | null): Probe {
  const why = `${prefix}:${reason ?? state}`;
  return state === 'not_applicable' ? unsupported(why) : unknown(why);
}

function display(o: Observation<string>): DiObservedValue {
  switch (o.state) {
    case 'observed':
      return { state: 'observed', value: o.value, reason: null };
    case 'known_empty':
    case 'not_recorded':
      return { state: o.state, value: null, reason: null };
    case 'projected_reason':
    case 'not_applicable':
    case 'unavailable':
      return { state: o.state, value: null, reason: o.reason };
  }
}

/** The report was collected at a cursor the document has since left: it never resolves. */
function reportMoved(v2: MissionV2Presentation): string | null {
  return v2.missionState.provenance.collectionStatus === 'moved'
    ? 'report_collected_at_moved_cursor'
    : null;
}

function reviewDetails(v2: MissionV2Presentation): DiReportEntry[] {
  const r = v2.canonicalReview;
  return [
    d('round', num(r.round)),
    d(
      'reviewer',
      r.reviewerLogical === null
        ? null
        : `${r.reviewerLogical} (${r.reviewerAgent ?? 'agent not recorded'})`,
    ),
    d('protocol token', r.protocolToken),
    d('gate', r.gate),
    d('verdict', r.verdict),
    d('integrity', r.integrity),
    d('currency', r.currency),
    d('task status', r.taskStatus),
  ];
}

function holdDetails(holds: string[], total: number | null): DiReportEntry[] {
  const omitted = total === null ? 0 : total - holds.length;
  return [
    d('holds total', total === null ? 'unknown (not a list)' : String(total)),
    ...holds.map((h) => d(INFORMATION_HOLDS.has(h) ? INFORMATION_HOLD_LABEL : 'hold', h)),
    ...(omitted > 0 ? [d('omitted by the display cap', String(omitted))] : []),
  ];
}

// ── probes: one canonical field each ─────────────────────────────

/** v2: Mission Core's blockers fact (active blockers, hard_active). */
function blockersFact(m: MissionPresentation, ctx: Ctx): Probe {
  if (m.v2 === null) return unsupported(`mission_core_blockers:${RECEPTION_V1_REASON}`);
  const b = m.v2.attention.blockers;
  if (b.state !== 'observed') return absent('mission_core_blockers', b.state, b.reason);
  if (b.hardActive === true || (b.activeTotal ?? 0) > 0) {
    const omitted = (b.activeTotal ?? 0) - b.active.length;
    return found({
      cause: 'mission_core_blockers',
      label: 'Mission Core: active blockers on this Mission',
      details: [
        d('hard blocker active', yn(b.hardActive)),
        d(
          'active blockers',
          b.activeTotal === null
            ? 'unknown (not a list)'
            : omitted > 0
              ? `${b.activeTotal} (showing ${b.active.length}; ${omitted} omitted by the display cap)`
              : String(b.activeTotal),
        ),
        ...b.active.map((item) => d('blocker', item)),
      ],
      staleReasons: stale(
        ctx.envelope,
        b.freshness === 'stale' && 'blockers_fact_stale',
        reportMoved(m.v2),
      ),
    });
  }
  if (b.hardActive === null || b.activeTotal === null)
    return unknown('mission_core_blockers:incomplete');
  return NONE;
}

/** v1 and v2: a member's reported runtime activity is exactly `blocked`. */
function memberActivity(m: MissionPresentation, ctx: Ctx): Probe {
  const roster = m.members;
  if (roster.state === 'known_empty') return NONE;
  if (roster.state !== 'observed') {
    const why =
      roster.state === 'not_recorded' ? 'not_recorded' : `${roster.state}:${roster.reason}`;
    return unknown(`member_activity:${why}`);
  }
  const blocked = roster.value.filter((x) => x.activityKnown && x.activity === 'blocked');
  // `unknown` / `unknown_future` / an unrecognized value say nothing either way: never "not blocked".
  const unread = new Map<string, number>();
  for (const x of roster.value) {
    if (!x.activityKnown || x.activity === 'unknown' || x.activity === 'unknown_future')
      unread.set(x.activity, (unread.get(x.activity) ?? 0) + 1);
  }
  const unreadReason =
    unread.size === 0
      ? null
      : `member_activity:${[...unread].map(([a, n]) => `${a}:${n}_member(s)`).join(',')}`;
  if (blocked.length === 0) return unreadReason === null ? NONE : unknown(unreadReason);
  return {
    causes: [
      {
        cause: 'member_activity_blocked',
        label: 'A member reports blocked activity (runtime activity, as reported)',
        details: blocked.map((x) =>
          d(
            'member',
            `${x.memberRef} · ${x.logical} (${x.role}) · ${x.agent} · ${x.classification} (${x.reason})`,
          ),
        ),
        staleReasons: stale(ctx.envelope, ctx.runtime),
      },
    ],
    unknown: unreadReason === null ? [] : [unreadReason],
    unsupported: [],
  };
}

/** v1 and v2: Mission Core's pause flag (DI progression, not external work). */
function pauseFlag(m: MissionPresentation, ctx: Ctx): Probe {
  const p = m.paused;
  if (p.state === 'observed') {
    return p.value
      ? found({
          cause: 'di_progression_paused',
          label: 'DI progression paused (Mission Core pause flag) — not external work suspended',
          details: [d('paused', 'true')],
          staleReasons: stale(ctx.envelope),
        })
      : NONE;
  }
  return absent('di_progression_paused', p.state, 'reason' in p ? p.reason : null);
}

/** v2: Mission Core's holds on verified success (information holds listed, never blocking). */
function completionHolds(m: MissionPresentation, ctx: Ctx): Probe {
  if (m.v2 === null) return unsupported(`completion_holds:${RECEPTION_V1_REASON}`);
  const c = m.v2.missionState.completion;
  if (c.state !== 'observed') return absent('completion_holds', c.state, c.reason);
  if (c.holdsTotal === null) return unknown('completion_holds:not_a_list');
  if (c.holds.some((h) => !INFORMATION_HOLDS.has(h))) {
    return found({
      cause: 'completion_holds',
      label: 'Mission Core: holds withholding verified success',
      details: holdDetails(c.holds, c.holdsTotal),
      staleReasons: stale(ctx.envelope, reportMoved(m.v2)),
    });
  }
  if (c.holds.length < c.holdsTotal) return unknown('completion_holds:truncated_by_display_cap');
  return NONE;
}

/** v2: Mission Core flags a source report contradicting the canonical record. */
function contradicted(m: MissionPresentation, ctx: Ctx): Probe {
  if (m.v2 === null) return unsupported(`completion:${RECEPTION_V1_REASON}`);
  const c = m.v2.missionState.completion;
  if (c.state !== 'observed') return absent('completion', c.state, c.reason);
  if (c.contradicted === null) return unknown('completion:contradicted_not_recorded');
  if (!c.contradicted) return NONE;
  return found({
    cause: 'completion_contradicted',
    label: 'Mission Core: a source report contradicts the canonical record',
    details: [
      d('contradicted', 'true'),
      d('reported complete', yn(c.reportedComplete)),
      d('closure verified', yn(c.closureVerified)),
      d('verified success', yn(c.verifiedSuccess)),
      ...holdDetails(c.holds, c.holdsTotal),
    ],
    staleReasons: stale(ctx.envelope, reportMoved(m.v2)),
  });
}

/**
 * v2: verified success is false WHILE a verified closure stands — the present claim was withdrawn.
 * Without a closure there is nothing verified to fail: "not yet verified" is not a failure.
 */
function verifiedSuccessWithdrawn(m: MissionPresentation, ctx: Ctx): Probe {
  if (m.v2 === null) return unsupported(`completion:${RECEPTION_V1_REASON}`);
  const c = m.v2.missionState.completion;
  if (c.state !== 'observed') return absent('completion', c.state, c.reason);
  if (c.closureVerified === null) return unknown('completion:closure_verified_not_recorded');
  if (!c.closureVerified) return NONE;
  if (c.verifiedSuccess === null) return unknown('completion:verified_success_not_recorded');
  if (c.verifiedSuccess) return NONE;
  return found({
    cause: 'verified_success_withdrawn',
    label: 'Mission Core: a verified closure stands, but present verified success is withdrawn',
    details: [
      d('closure verified', 'true'),
      d('verified success', 'false'),
      ...holdDetails(c.holds, c.holdsTotal),
    ],
    staleReasons: stale(ctx.envelope, reportMoved(m.v2)),
  });
}

/** Mission Core's evidence-failure holds (mission/observation.py HOLD_* names). */
const EVIDENCE_CONTRADICTED_HOLD = 'evidence_contradicted';
const EVIDENCE_DRIFT_HOLDS: ReadonlySet<string> = new Set([
  'candidate_drifted',
  'baseline_drifted',
]);

/**
 * v2: Mission Core's holds recording contradicted or drifted EVIDENCE. Mission Core raises
 * `contradicted` for these only when the Mission is verified or reported complete, so on an
 * unclosed, unreported Mission the hold is the only canonical record of it — read it directly.
 */
function evidenceFailureHolds(m: MissionPresentation, ctx: Ctx): Probe {
  if (m.v2 === null) return unsupported(`completion_holds:${RECEPTION_V1_REASON}`);
  const c = m.v2.missionState.completion;
  if (c.state !== 'observed') return absent('completion_holds', c.state, c.reason);
  if (c.holdsTotal === null) return unknown('completion_holds:not_a_list');
  const staleReasons = stale(ctx.envelope, reportMoved(m.v2));
  const causes: DiReceptionCause[] = [];
  if (c.holds.includes(EVIDENCE_CONTRADICTED_HOLD))
    causes.push({
      cause: 'evidence_contradicted_hold',
      label: 'Mission Core hold: proof evidence is contradicted or mismatched',
      details: [d('hold', EVIDENCE_CONTRADICTED_HOLD)],
      staleReasons,
    });
  const drifted = c.holds.filter((h) => EVIDENCE_DRIFT_HOLDS.has(h));
  if (drifted.length > 0)
    causes.push({
      cause: 'evidence_drifted_hold',
      label: 'Mission Core hold: accepted evidence drifted from the candidate or baseline',
      details: drifted.map((h) => d('hold', h)),
      staleReasons,
    });
  // A list cut by the display cap may hide one of these holds: never read that as "none".
  if (causes.length === 0 && c.holds.length < c.holdsTotal)
    return unknown('completion_holds:truncated_by_display_cap');
  return { causes, unknown: [], unsupported: [] };
}

/** v2: the admitted canonical review record — refused, integrity not corroborated, or REJECT. */
function canonicalReviewFailure(m: MissionPresentation, ctx: Ctx): Probe {
  if (m.v2 === null) return unsupported(`canonical_review:${RECEPTION_V1_REASON}`);
  const v2 = m.v2;
  const r = v2.canonicalReview;
  const staleReasons = stale(
    ctx.envelope,
    r.currency === 'possibly_stale' && 'review_currency_possibly_stale',
  );
  switch (r.state) {
    case 'not_recorded':
      return NONE;
    case 'not_applicable':
      return unsupported(`canonical_review_not_applicable:${r.problem ?? 'no_problem_recorded'}`);
    case 'unavailable':
      return unknown(`canonical_review_unavailable:${r.problem ?? 'no_problem_recorded'}`);
    case 'refused':
      return found({
        cause: 'review_record_refused',
        label: 'The canonical review record was refused (it was not admitted)',
        details: [d('state', 'refused'), d('problem', r.problem)],
        staleReasons,
      });
    case 'admitted': {
      const probe: Probe = { causes: [], unknown: [], unsupported: [] };
      if (r.integrity === null) probe.unknown.push('canonical_review_integrity_not_recorded');
      else if (r.integrity !== REVIEW_INTEGRITY_CORROBORATED)
        probe.causes.push({
          cause: 'review_integrity_not_corroborated',
          label: 'The canonical review record is not linkage-corroborated',
          details: reviewDetails(v2),
          staleReasons,
        });
      if (r.gate === 'parsed') {
        if (r.verdict === 'REJECT')
          probe.causes.push({
            cause: 'review_verdict_reject',
            label: 'The canonical review verdict is REJECT (gate parsed)',
            details: reviewDetails(v2),
            staleReasons,
          });
        else if (r.verdict !== 'APPROVE')
          probe.unknown.push(`canonical_review_verdict_unrecognized:${String(r.verdict)}`);
      } else if (r.gate !== 'unparsed') {
        // `unparsed` means no valid token was parsed: NOT a rejection.
        probe.unknown.push(`canonical_review_gate_unrecognized:${String(r.gate)}`);
      }
      return probe;
    }
    default:
      return unknown(`canonical_review_state_unrecognized:${String(r.state)}`);
  }
}

/** v2: the task source reports COMPLETE while Mission Core has not verified success. */
function reportedCompleteNotVerified(m: MissionPresentation, ctx: Ctx): Probe {
  if (m.v2 === null) return unsupported(`completion:${RECEPTION_V1_REASON}`);
  const v2 = m.v2;
  const c = v2.missionState.completion;
  if (c.state !== 'observed') return absent('completion', c.state, c.reason);
  if (c.reportedComplete === null) return unknown('completion:reported_complete_not_recorded');
  if (!c.reportedComplete || c.verifiedSuccess === true) return NONE;
  const r = v2.canonicalReview;
  return found({
    cause: 'reported_complete_not_verified',
    label:
      'The task source reports COMPLETE — a report, not a verification; Mission Core has not verified success',
    details: [
      d('reported complete (task source report)', 'true'),
      d('verified success', yn(c.verifiedSuccess)),
      d('closure verified', yn(c.closureVerified)),
      d('review state', r.state),
      d('review gate', r.gate),
      d('review verdict', r.verdict),
      d('review task status', r.taskStatus),
      d('candidate applicability', r.candidateApplicability),
      d('review currency', r.currency),
    ],
    staleReasons: stale(ctx.envelope, reportMoved(v2)),
  });
}

/**
 * v2: the producer's DECLARED workflow phase (`missions[].workflow_phase`) is exactly `phase`. Read
 * from that declared field only — never from the display-only `lifecycleState`. The gate is the
 * lifecycle model (sourceKindOf derives `sourceKind` from `lifecycle.model`): a model without a
 * workflow phase (Mission Core) implies nothing (NONE). Under contract v1 there is no declared
 * phase (unsupported); a v2 producer that predates the field is unknown, never "none".
 */
function workflowPhaseIs(
  m: MissionPresentation,
  ctx: Ctx,
  phase: WorkflowPhase,
  cause: Omit<DiReceptionCause, 'details' | 'staleReasons'>,
): Probe {
  if (m.sourceKind !== 'workflow_store') return NONE;
  if (m.v2 === null) return unsupported(`workflow_phase:${RECEPTION_V1_WORKFLOW_PHASE_REASON}`);
  const w = m.v2.workflowPhase;
  if (w.state === 'not_reported') return unknown('workflow_phase:not_reported_by_this_producer');
  // The projection refuses `not_applicable` on a workflow record; kept total, never "none".
  if (w.state === 'not_applicable') return unknown(`workflow_phase:not_applicable:${w.problem}`);
  if (!w.phase.known) return unknown(`workflow_phase_unrecognized:${String(w.phase.raw)}`);
  if (w.phase.value !== phase) return NONE;
  return found({
    ...cause,
    details: [d('workflow phase', w.phase.value), d('lifecycle model', m.lifecycleModel)],
    staleReasons: stale(ctx.envelope),
  });
}

/** v1 and v2: a workflow-authority workflow in its NEEDS_REAUTHORIZATION phase. */
function workflowReauthorization(m: MissionPresentation, ctx: Ctx): Probe {
  return workflowPhaseIs(m, ctx, 'NEEDS_REAUTHORIZATION', {
    cause: 'workflow_needs_reauthorization',
    label: 'Workflow authority: this workflow needs reauthorization (phase NEEDS_REAUTHORIZATION)',
  });
}

/** v1 and v2: a workflow-authority workflow in its terminal, fail-closed BLOCKED phase. */
function workflowBlocked(m: MissionPresentation, ctx: Ctx): Probe {
  return workflowPhaseIs(m, ctx, 'BLOCKED', {
    cause: 'workflow_phase_blocked',
    label: 'Workflow authority: this workflow is BLOCKED (a terminal, fail-closed phase)',
  });
}

/** v2: Mission Core's pending_decisions.awaiting_decision (its own boolean, not a literal). */
function awaitingDecision(m: MissionPresentation, ctx: Ctx): Probe {
  // A Mission Core fact: a workflow-authority record answers through its own typed phase instead.
  if (m.sourceKind === 'workflow_store') return NONE;
  if (m.sourceKind === 'unrecognized')
    return unknown('pending_decisions:lifecycle_model_unrecognized');
  if (m.v2 === null) return unsupported(`pending_decisions:${RECEPTION_V1_REASON}`);
  const p = m.v2.attention.pendingDecisions;
  if (p.state !== 'observed') return absent('pending_decisions', p.state, p.reason);
  if (p.awaitingDecision === null)
    return unknown('pending_decisions:awaiting_decision_not_recorded');
  if (!p.awaitingDecision) return NONE;
  return found({
    cause: 'awaiting_decision',
    label: 'Mission Core: this Mission awaits a human decision',
    details: [
      d('awaiting decision', 'true'),
      d('revision', num(p.revision)),
      d('live authorization', yn(p.liveAuthorization)),
      d('next permitted step', p.nextPermittedStep),
    ],
    staleReasons: stale(
      ctx.envelope,
      p.freshness === 'stale' && 'pending_decisions_fact_stale',
      reportMoved(m.v2),
    ),
  });
}

// ── the six categories (item 06's order) ─────────────────────────

const CATEGORIES: CategoryDef[] = [
  {
    id: 'blockers',
    label: 'Blockers',
    statement:
      "Mission Core's active blockers and its holds on verified success, members reporting " +
      'blocked activity, the DI-progression pause flag, and a workflow-authority workflow whose ' +
      'declared phase is BLOCKED. Each cause is listed separately.',
    backing:
      'contract v2: mission_state.report.blockers (active, hard_active), completion.holds and the ' +
      'declared missions[].workflow_phase (workflow_authority.workflow_phase/2 records only); ' +
      'contract v1 and v2: members[].activity, native.paused (Mission Core)',
    route:
      "Outside this office: act through the Mission's operator and its Herdr team. This desk is " +
      'read-only; hiding an entry resolves nothing.',
    v2Only: false,
    probes: [blockersFact, memberActivity, pauseFlag, workflowBlocked, completionHolds],
  },
  {
    id: 'clarification',
    label: 'Clarification',
    statement:
      'Not reported: no field of the catalog query contract, at contract version 1 or 2, ' +
      'carries a clarification request, so this desk cannot show one. Coordination routing ' +
      'can record CLARIFICATION_REQUIRED, but the catalog does not read coordination records ' +
      'and no running service writes them yet (it depends on the Operator request route).',
    backing:
      'none at contract versions 1 and 2 (coordination route records are not projected by ' +
      'the catalog)',
    route: '',
    v2Only: false,
    probes: null,
  },
  {
    id: 'authorization',
    label: 'Authorization / reauthorization',
    statement:
      'A Mission Core Mission awaiting a human decision (its own pending_decisions fact), or a ' +
      'workflow-authority workflow whose DECLARED phase is NEEDS_REAUTHORIZATION. The two ' +
      'models stay separate: Mission Core has no workflow phase, and nothing about one is read ' +
      'into the other. The display-only lifecycle state is never decided on.',
    backing:
      'contract v2: mission_state.report.pending_decisions (awaiting_decision, revision, ' +
      'live_authorization, next_permitted_step), Mission Core records; contract v2: the ' +
      'declared missions[].workflow_phase, workflow_authority.workflow_phase/2 records',
    route:
      'Outside this office: a decision or reauthorization is made through the existing ' +
      'Operator decision route for this Mission. This desk is read-only; it cannot approve, and ' +
      'hiding an entry is not a decision.',
    v2Only: true,
    v1: {
      reason: RECEPTION_V1_AUTHORIZATION_REASON,
      statement:
        "Not supported at contract version 1: Mission Core's pending_decisions and the declared " +
        'workflow phase are contract version 2 fields. This office process reads contract ' +
        'version 1, selected at launch; a native lifecycle literal is display only.',
    },
    probes: [awaitingDecision, workflowReauthorization],
  },
  {
    id: 'failed_verification',
    label: 'Failed verification',
    statement:
      'Mission Core reports a contradiction or a withdrawn verified success, or holds ' +
      'contradicted or drifted evidence (evidence_contradicted, candidate_drifted, ' +
      'baseline_drifted — read directly, since Mission Core flags those as contradicted only ' +
      'once a Mission is verified or reported complete), or the canonical review record was ' +
      'refused, is not linkage-corroborated, or carries a parsed REJECT. Not yet verified is ' +
      'not a failure; an unparsed review gate is not a rejection.',
    backing:
      'contract v2: mission_state.report.completion (contradicted, closure_verified, ' +
      'verified_success, holds) and admitted_artifacts.canonical_review (state, problem, ' +
      'integrity, gate, verdict)',
    route:
      "Outside this office: investigate through the Mission's established Herdr review and " +
      'Mission Core verification route. This desk is read-only.',
    v2Only: true,
    probes: [contradicted, verifiedSuccessWithdrawn, evidenceFailureHolds, canonicalReviewFailure],
  },
  {
    id: 'ready_for_review',
    label: 'Results ready for review',
    statement:
      'The task source reports COMPLETE while Mission Core has not verified success. That is a ' +
      'report — not task, Mission, verification or delivery completion.',
    backing:
      'contract v2: mission_state.report.completion (reported_complete, verified_success) with ' +
      'canonical_review gate, task_status and candidate_applicability shown verbatim',
    route:
      "Outside this office: review and verify through the Mission's established Herdr review " +
      'and Mission Core verification route. This desk is read-only.',
    v2Only: true,
    probes: [reportedCompleteNotVerified],
  },
  {
    id: 'delivery_decision',
    label: 'Delivery awaiting a separate decision',
    statement:
      'Not reported: no record of a delivery awaiting a separate decision exists to project. ' +
      'A delivery proposal is presented for the decision but not stored; PR delivery stores ' +
      'only authorizations made after approval; and the catalog delivery facts (contract ' +
      'version 1 or 2) describe receipts already recorded.',
    backing:
      'none at contract versions 1 and 2, and no stored producer record of a pending delivery ' +
      'decision',
    route: '',
    v2Only: false,
    probes: null,
  },
];

// ── derivation ───────────────────────────────────────────────────

/**
 * A runtime observation that is not fresh: shown, so member activity reads as unknown at a glance.
 * DISPLAY ONLY — it never blocks a "none" in a category that reads Mission records alone (the
 * member-activity probe already reports its own unknown).
 */
function runtimeNotices(view: OfficeView): DiReceptionNotice[] {
  if (view.connection.state !== 'answered' || view.freshness === null) return [];
  if (view.freshness.runtime.fresh) return [];
  return [
    {
      reason: `runtime_not_fresh:${view.freshness.runtime.reason ?? 'reason_not_given'}`,
      statement:
        'The runtime observation is not fresh: member activity and liveness are unknown, never ' +
        '"none". Categories that read only Mission records are unaffected.',
    },
  ];
}

/** Gaps that forbid any category from claiming \"none\". */
function noticesOf(view: OfficeView): DiReceptionNotice[] {
  if (view.connection.state === 'unavailable') {
    return [
      {
        reason: `connection_unavailable:${view.connection.reason}`,
        statement: 'No catalog answer: nothing here may be read as "none".',
      },
    ];
  }
  const notices: DiReceptionNotice[] = [];
  if (view.freshness?.envelope.fresh !== true) {
    notices.push({
      reason: `envelope_not_fresh:${view.freshness?.envelope.reason ?? view.notLiveReason ?? 'not_projected'}`,
      statement:
        'The catalog answer is not fresh: what it reports is stale, and "none" is not claimed.',
    });
  }
  if (view.registryHealth !== null && view.registryHealth !== 'fresh') {
    notices.push({
      reason: `registry_${view.registryHealth}`,
      statement:
        'The source registry is not fresh: a source may be missing, so "none" is not claimed.',
    });
  }
  for (const s of view.sources) {
    if (s.health === 'fresh') continue;
    notices.push({
      reason: `source_${s.health}:${s.sourceId}${s.problem ? `:${s.problem}` : ''}`,
      statement:
        `Source ${s.sourceId} (${s.kind}, producer ${s.producerDeclared}) is ${s.health}: its ` +
        'Missions may be absent. An absent source is not an empty one.',
    });
  }
  return notices;
}

function cellOf(probes: Probe[]): {
  outcome: CellOutcome;
  reason: string | null;
  causes: DiReceptionCause[];
  alsoUnknown: string[];
} {
  const causes = probes.flatMap((p) => p.causes);
  const unknowns = [...new Set(probes.flatMap((p) => p.unknown))];
  const unsupporteds = [...new Set(probes.flatMap((p) => p.unsupported))];
  if (causes.length > 0)
    return {
      outcome: 'present',
      reason: null,
      causes,
      alsoUnknown: [...unknowns, ...unsupporteds],
    };
  if (unknowns.length > 0)
    return { outcome: 'unavailable', reason: unknowns[0], causes, alsoUnknown: [] };
  if (unsupporteds.length > 0)
    return { outcome: 'unsupported', reason: unsupporteds[0], causes, alsoUnknown: [] };
  return { outcome: 'known_empty', reason: null, causes, alsoUnknown: [] };
}

function entryOf(
  def: CategoryDef,
  m: MissionPresentation,
  causes: DiReceptionCause[],
  alsoUnknown: string[],
): DiReceptionEntry {
  const routeIdentifiers = [
    d('mission_ref', m.missionRef),
    d('source_id', m.sourceId),
    d('native_id', m.nativeId),
  ];
  if (def.id === 'authorization' && m.v2 !== null)
    routeIdentifiers.push(d('revision', num(m.v2.attention.pendingDecisions.revision)));
  if ((def.id === 'failed_verification' || def.id === 'ready_for_review') && m.v2 !== null) {
    // A canonical review record is identified by its task and round (…/<task_id>-round-<NN>.md).
    const direct = m.v2.taskChains.find((c) => c.chain === 'direct');
    routeIdentifiers.push(d('review task (direct chain)', direct?.chainTaskId ?? null));
    routeIdentifiers.push(d('review round', num(m.v2.canonicalReview.round)));
  }
  return {
    key: JSON.stringify([def.id, m.sourceId, m.nativeId]),
    // The canonical content AND the canonical record identifiers (review task and round,
    // decision revision), never freshness: a locally hidden entry returns when either changes,
    // so a hide of review round 1 can never hide round 2.
    signature: JSON.stringify({
      causes: causes.map((c) => [c.cause, c.label, c.details]),
      record: routeIdentifiers,
    }),
    category: def.id,
    missionRef: m.missionRef,
    sourceId: m.sourceId,
    nativeId: m.nativeId,
    sourceKind: m.sourceKind,
    producerDeclared: m.producerDeclared,
    objective: display(m.objective),
    lifecycleModel: m.lifecycleModel,
    lifecycleState: m.lifecycleState,
    standing: m.standing,
    bindingState: m.bindingState,
    bindingReason: m.bindingReason,
    live: m.presence.live,
    notLiveReason: m.presence.reason,
    occurrences: 1,
    identityConflict: false,
    causes,
    alsoUnknown,
    current: causes.every((c) => c.staleReasons.length === 0),
    route: def.route,
    routeIdentifiers,
  };
}

/** Collapse ONLY canonically identical records; same identity, different content: keep both, flagged. */
function dedupe(entries: DiReceptionEntry[]): DiReceptionEntry[] {
  const byKey = new Map<string, DiReceptionEntry[]>();
  for (const e of entries) {
    const group = byKey.get(e.key) ?? [];
    const same = group.find((x) => x.signature === e.signature);
    if (same) same.occurrences += 1;
    else group.push({ ...e });
    byKey.set(e.key, group);
  }
  const out: DiReceptionEntry[] = [];
  for (const group of byKey.values()) {
    for (const e of group) out.push(group.length > 1 ? { ...e, identityConflict: true } : e);
  }
  return out;
}

const cmp = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

function gapsOf(cells: Array<{ outcome: CellOutcome; reason: string | null }>): DiReceptionGap[] {
  const counts = new Map<string, DiReceptionGap>();
  for (const c of cells) {
    if (c.outcome !== 'unsupported' && c.outcome !== 'unavailable') continue;
    const reason = c.reason ?? 'no_reason_recorded';
    const k = `${c.outcome}\u0000${reason}`;
    const g = counts.get(k) ?? { outcome: c.outcome, reason, missions: 0 };
    g.missions += 1;
    counts.set(k, g);
  }
  return [...counts.values()].sort((a, b) => cmp(a.outcome, b.outcome) || cmp(a.reason, b.reason));
}

export function deriveReception(view: OfficeView): DiReception {
  const ctx: Ctx = {
    envelope:
      view.freshness?.envelope.fresh === true
        ? null
        : `envelope_not_fresh:${view.freshness?.envelope.reason ?? view.notLiveReason ?? 'not_projected'}`,
    runtime:
      view.freshness?.runtime.fresh === true
        ? null
        : `runtime_not_fresh:${view.freshness?.runtime.reason ?? view.notLiveReason ?? 'not_projected'}`,
  };
  const notices = noticesOf(view);
  const readable = view.missions.state === 'observed' || view.missions.state === 'known_empty';
  const missions = view.missions.state === 'observed' ? view.missions.value : [];
  const missionsReason = 'reason' in view.missions ? view.missions.reason : view.missions.state;

  const categories: DiReceptionCategory[] = [];
  const present: DiReceptionEntry[] = [];
  for (const def of CATEGORIES) {
    const base = {
      category: def.id,
      label: def.label,
      statement: def.statement,
      backing: def.backing,
    };
    const structural =
      def.probes === null
        ? RECEPTION_NOT_IN_CONTRACT
        : def.v2Only && view.contractVersion === 1
          ? (def.v1?.reason ?? RECEPTION_V1_REASON)
          : null;
    if (structural !== null) {
      categories.push({
        ...base,
        statement:
          structural === RECEPTION_NOT_IN_CONTRACT
            ? def.statement
            : (def.v1?.statement ??
              `Not supported at contract version 1: ${def.label} comes from the Mission State ` +
                'report and the canonical review record, which are contract version 2 blocks. ' +
                'This office process reads contract version 1, selected at launch.'),
        outcome: 'unsupported',
        reason: structural,
        counts: { present: 0, knownEmpty: 0, unsupported: missions.length, unavailable: 0 },
        gaps:
          missions.length > 0
            ? [{ outcome: 'unsupported', reason: structural, missions: missions.length }]
            : [],
        entriesTotal: 0,
      });
      continue;
    }
    if (!readable) {
      categories.push({
        ...base,
        outcome: 'unavailable',
        reason: missionsReason,
        counts: { present: 0, knownEmpty: 0, unsupported: 0, unavailable: 0 },
        gaps: [],
        entriesTotal: 0,
      });
      continue;
    }
    const probes = def.probes ?? [];
    const cells = missions.map((m) => ({ m, ...cellOf(probes.map((p) => p(m, ctx))) }));
    const entries = dedupe(
      cells
        .filter((c) => c.outcome === 'present')
        .map((c) => entryOf(def, c.m, c.causes, c.alsoUnknown)),
    );
    present.push(...entries);
    const count = (o: CellOutcome) => cells.filter((c) => c.outcome === o).length;
    const counts = {
      present: count('present'),
      knownEmpty: count('known_empty'),
      unsupported: count('unsupported'),
      unavailable: count('unavailable'),
    };
    const gaps = gapsOf(cells);
    let outcome: DiReceptionOutcome;
    let reason: string | null = null;
    if (counts.present > 0) outcome = 'present';
    else if (counts.knownEmpty === cells.length) {
      // A "none" from a stale answer or with a source missing is not a "none".
      outcome = notices.length > 0 ? 'unavailable' : 'known_empty';
      reason = notices[0]?.reason ?? null;
    } else if (counts.unsupported === cells.length) {
      outcome = 'unsupported';
      reason = gaps[0]?.reason ?? null;
    } else if (counts.unavailable === cells.length) {
      outcome = 'unavailable';
      reason = gaps[0]?.reason ?? null;
    } else outcome = 'partial';
    categories.push({ ...base, outcome, reason, counts, gaps, entriesTotal: entries.length });
  }

  const order = (id: DiReceptionCategoryId) => CATEGORIES.findIndex((c) => c.id === id);
  const sorted = [...present].sort(
    (a, b) =>
      order(a.category) - order(b.category) ||
      cmp(a.sourceId, b.sourceId) ||
      cmp(a.nativeId, b.nativeId) ||
      cmp(a.signature, b.signature),
  );
  const poll = view.minPollIntervalSeconds;
  return {
    contractVersion: view.contractVersion,
    categories,
    entries: sorted.slice(0, DI_RECEPTION_MAX_ENTRIES),
    entriesTotal: sorted.length,
    entriesCap: DI_RECEPTION_MAX_ENTRIES,
    notices: [...notices, ...runtimeNotices(view)],
    minPollIntervalSeconds: poll,
    refreshStatement:
      'Refreshed with the office feed (no polling of its own)' +
      (poll === null ? '.' : `; the backend asks for at most one read every ${poll} s.`),
    dismissalStatement: DISMISSAL_STATEMENT,
  };
}
