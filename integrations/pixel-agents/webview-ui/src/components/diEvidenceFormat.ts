/**
 * Item 2 (original 10) — plain wording and selection rules for the evidence library. Pure: no
 * DOM, no transport. Every state keeps its own words (empty, not reported, unsupported, stale,
 * refused, unavailable are never collapsed), and every code stays visible behind the words.
 */
import type {
  DiEvidenceArtifact,
  DiEvidenceArtifactRecord,
  DiEvidenceArtifactSection,
  DiEvidenceContent,
  DiEvidenceLibrary,
  DiEvidenceReceiptSection,
  DiEvidenceRecordSection,
  DiEvidenceReviewSection,
} from '../../../core/src/messages.js';

/** The most text the viewer renders (the backend's own per-file bound; never raised here). */
export const DI_EVIDENCE_RENDER_MAX_CHARS = 65_536;

type AnySection =
  | DiEvidenceArtifactSection
  | DiEvidenceRecordSection
  | DiEvidenceReviewSection
  | DiEvidenceReceiptSection;

/** One section's state in plain words; the reason code follows in parentheses. */
export function formatEvidenceSection(section: AnySection): string {
  const why = section.reason ? ` (${section.reason})` : '';
  switch (section.state) {
    case 'observed':
      return section.total !== null
        ? `${section.total} recorded`
        : `${section.items.length} shown; more are recorded than the listing carries`;
    case 'known_empty':
      return 'none recorded';
    case 'not_recorded':
      return 'not reported by the producer';
    case 'not_applicable':
      return `not supported for this source kind${why}`;
    case 'unavailable':
      return `could not be read${why}`;
    case 'refused':
      return `refused by the producer${why}`;
    case 'projected_reason':
      return `not listed${why}`;
  }
}

/** Mission Core's own candidate comparison, never more. */
export function plainCandidate(candidate: DiEvidenceArtifact['candidate']): string {
  switch (candidate) {
    case 'matches_observed':
      return 'matches the observed candidate';
    case 'differs_from_observed':
      return 'DIFFERS from the observed candidate';
    case 'not_observed':
      return 'candidate applicability not observed';
  }
}

const CONTENT_REASONS: Readonly<Record<string, string>> = {
  locator_kind_has_no_content: 'its locator names no workspace file',
  artifact_recorded_unavailable: 'recorded as not available',
  artifact_has_no_recorded_digest: 'no digest is recorded, so nothing ties a file to it',
};

/** Whether a row can ask for content, in plain words. Never a promise it will be shown. */
export function plainContentRequest(a: DiEvidenceArtifact): string {
  if (a.content.state === 'requestable') return 'content: ask the backend (it decides)';
  const reason = a.content.reason ?? 'unknown';
  return `no content: ${CONTENT_REASONS[reason] ?? reason}`;
}

/** Plain words for every refusal code either side may send; the code always follows. */
const REFUSALS: Readonly<Record<string, string>> = {
  // office
  mission_not_listed: 'the office no longer lists this Mission',
  mission_listed_more_than_once: 'the Mission is listed more than once',
  evidence_not_projected: 'this office projects no evidence library',
  content_reads_unavailable: 'content reads are closed for this Mission now',
  listing_moved: 'the Mission moved since this listing; select it again',
  reference_not_listed: 'the artifact is not listed now',
  reference_has_no_content: 'this artifact has no content to read',
  read_in_progress: 'another content read is in progress',
  read_rate_limited: 'at most one content read per second',
  content_reads_not_supported_by_this_backend: 'this backend has no content route',
  backend_discovery_unavailable: 'the backend could not be found',
  backend_unavailable: 'the backend did not answer',
  backend_refused_request: 'the backend refused the request',
  backend_answer_invalid: 'the backend answer did not check out; nothing is shown',
  listing_moved_during_read: 'the Mission moved while it was read; the content was discarded',
  execution_basis_mismatch:
    'the backend read under another execution than the one listed; nothing is shown',
  // backend
  content_needs_contract_version_2: 'the backend serves no content in this contract version',
  owner_shut_down: 'the backend is shutting down',
  content_read_in_progress: 'the backend is busy with another read',
  no_fresh_catalog_answer: 'nothing currently corroborates the Mission',
  source_kind_has_no_artifact_content: 'this source kind has no artifact content',
  herdr_task_has_no_artifact_bodies:
    'a Herdr task has no Mission Core artifact bodies; a recorded review is in the review section',
  mission_binding_not_bound: 'the Mission is not currently bound',
  mission_task_or_workspace_unresolved: 'the Mission task or workspace is unresolved',
  mission_state_not_observed: 'the Mission State was not observed',
  cursor_not_the_listed_cursor: 'the listing is not the one the backend corroborates',
  registry_unreadable: 'the source registry could not be read',
  source_not_registered: 'the source is not registered now',
  mission_store_unavailable: 'the Mission store could not be read',
  mission_absent_from_store: 'the Mission is not in its store now',
  mission_moved_since_listing: 'the Mission moved since this listing; select it again',
  mission_task_or_workspace_changed: 'the Mission task or workspace changed',
  workspace_not_one_resolved_root: 'the workspace is not one resolved root',
  artifact_not_recorded: 'the artifact is not recorded',
  artifact_recorded_more_than_once: 'the artifact id is recorded more than once',
  locator_kind_has_no_content: 'its locator names no workspace file',
  artifact_recorded_unavailable: 'recorded as not available',
  artifact_has_no_recorded_digest: 'no digest is recorded',
  locator_not_a_contained_relative_path: 'its path leaves the workspace or is unsafe; never opened',
  artifact_class_not_declared:
    'the record does not declare it evidence (not a contract-pinned deliverable, not named by accepted verification); never read',
  locator_in_private_location: 'it is in a private location (such as .herd); never read',
  locator_credential_like: 'it looks like credentials or key material; never read',
  locator_format_not_supported: 'it is not in a text format shown here; never read',
  file_missing: 'the file is missing',
  file_over_read_bound: 'the file is over the 64 KiB read bound',
  content_not_text: 'the file is not plain text (binary, control or bidi-override characters)',
  file_not_regular: 'not a regular file (for example a symbolic link); never followed',
  path_escapes_workspace: 'a directory on its path leads outside the workspace',
  file_hardlinked: 'the file is hard-linked; refused',
  file_unreadable: 'the file could not be read',
  content_changed_since_recorded:
    'the file changed since it was recorded; not shown as the artifact',
  mission_moved_during_read: 'the Mission moved while the file was read; discarded',
  request_read_budget_exhausted: 'the read budget for this request ran out',
  historical_identity_not_established:
    "the completed Mission's recorded execution is not established",
  historical_uniqueness_unverifiable:
    'the listing is incomplete, so its task and workspace cannot be shown to be claimed once',
  historical_identity_conflict: 'another listed Mission claims the same task or workspace',
  source_registration_changed: 'the source registration changed since this listing',
  historical_identity_changed:
    "the completed Mission's recorded execution changed since this listing; select it again",
  historical_identity_changed_during_read:
    "the completed Mission's recorded execution changed while it was read; discarded",
};

/**
 * Why a COMPLETED Mission's recorded execution is or is not established (the backend's
 * `execution_*` codes; parity is pinned by tests/test_artifact_content.py between these markers).
 */
// BEGIN EXECUTION_IDENTITY_CODES
const EXECUTION_IDENTITY_WORDS: Record<string, string> = {
  execution_mission_not_completed: 'the Mission is not COMPLETED',
  execution_run_absent: 'the record has no run',
  execution_run_not_verified: 'its run was not verified',
  execution_intent_absent: 'its run intent is not recorded',
  execution_receipt_task_absent: 'its run receipt names no task',
  execution_prepared_binding_absent:
    'it has no prepared workspace binding (an older record; never upgraded)',
  execution_binding_not_prepared: 'its workspace binding was not prepared',
  execution_binding_disagrees: 'its workspace binding and run intent disagree',
  execution_record_shape_invalid: 'its run record has an unexpected shape',
};
// END EXECUTION_IDENTITY_CODES

export function plainExecutionIdentityCode(problem: string | null): string {
  if (problem === null) return 'no reason given';
  return `${EXECUTION_IDENTITY_WORDS[problem] ?? 'not established'} (${problem})`;
}

/** The execution an ADMITTED answer was read under, in plain words. */
export function plainExecutionBasis(basis: DiEvidenceArtifactRecord['executionBasis']): string {
  return basis === 'historical_completed_execution'
    ? "the completed Mission's historical recorded execution (not a live team)"
    : "the Mission's current binding";
}

/**
 * The EXECUTION a read of this Mission would stand on, in plain words — kept apart from the
 * listing's freshness, which the panel shows on its own line.
 */
export function plainExecution(execution: DiEvidenceLibrary['execution']): string {
  switch (execution.basis) {
    case 'active_binding':
      return "Read under: the Mission's current binding (a bound, active Mission).";
    case 'historical_completed_execution':
      return (
        `Read under: HISTORICAL recorded execution. This Mission is ${execution.lifecycleState ?? 'completed'}; ` +
        `its artifacts are read under the execution its canonical record still proves (task ` +
        `${execution.taskId ?? 'not recorded'}, a prepared, digest-bound workspace). No live team; ` +
        'nothing here speaks for the candidate as a whole.'
      );
    default:
      switch (execution.identityState) {
        case 'not_established':
          return `Not readable as history: ${plainExecutionIdentityCode(execution.identityProblem)}.`;
        case 'not_reported':
          return 'Not readable as history: this backend does not report a recorded execution.';
        case 'unexpected':
          return 'Not readable as history: the recorded execution block did not check out.';
        default:
          return 'Content is read only for a bound Mission, or a COMPLETED one with an established recorded execution.';
      }
  }
}

export function plainRefusal(problem: string | null): string {
  if (problem === null) return 'refused';
  return `${REFUSALS[problem] ?? 'refused'} (${problem})`;
}

/** What the viewer asked for: the admitted reference, exactly as the listing showed it. */
export interface EvidenceSelection {
  missionRef: string;
  artifactId: string;
  cursor: string;
}

/** Is `content` the answer to `selection`? (Reference equality on all three parts.) */
export function answersSelection(
  content: DiEvidenceContent | null,
  selection: EvidenceSelection | null,
): content is DiEvidenceContent {
  return (
    content !== null &&
    selection !== null &&
    content.missionRef === selection.missionRef &&
    content.artifactId === selection.artifactId &&
    content.cursor === selection.cursor
  );
}

/**
 * Whether an answer may still be SHOWN against the library listed NOW: only while the listing
 * is current and at the very journal digest the answer was read at. Otherwise it is hidden and
 * the reader is told why — never silently re-resolved against a newer listing.
 */
export function contentStillCurrent(
  content: DiEvidenceContent,
  library: DiEvidenceLibrary,
): { current: true } | { current: false; why: string } {
  if (library.currency !== 'current') {
    return { current: false, why: 'the listing is stale; content reads are closed' };
  }
  if (library.cursor !== content.cursor) {
    return { current: false, why: 'the Mission moved after this was read; select it again' };
  }
  return { current: true };
}

/**
 * A verification record's link count, its TRUE total first; when the office kept fewer than the
 * record carries (the display cap), it says how many and that the rest are not shown — a cut is
 * never silent (round 15 F4).
 */
export function plainLinkCount(shown: number, total: number): string {
  const noun = total === 1 ? 'recorded link' : 'recorded links';
  if (total <= shown) return `names ${total} ${noun}:`;
  return `names ${total} ${noun}, showing the first ${shown}; ${total - shown} not shown (display cap):`;
}

/** The text the viewer renders: bounded, and said so when bounded. */
export function renderedText(text: string): { text: string; truncated: boolean } {
  if (text.length <= DI_EVIDENCE_RENDER_MAX_CHARS) return { text, truncated: false };
  return { text: text.slice(0, DI_EVIDENCE_RENDER_MAX_CHARS), truncated: true };
}

export function plainAdmissionBasis(basis: readonly string[]): string {
  const words: Record<string, string> = {
    contract_required_artifact: 'a deliverable the approved contract pins',
    accepted_evidence_artifact: 'named by an accepted verification record',
  };
  return basis.map((b) => words[b] ?? b).join(' and ');
}

/**
 * item 2 E2: plain words for every findings code (the contract's writer, not-recorded and
 * reader codes; parity pinned by tests/test_review_findings.py, which reads this list).
 */
// BEGIN FINDINGS_CODES
export const FINDINGS_WORDS: Readonly<Record<string, string>> = {
  findings_legacy_record_predates_field: 'this round was recorded before declared findings existed',
  findings_declaration_not_routed: 'this review was not routed for a findings declaration',
  findings_declaration_missing: "the reviewer's output carried no declaration for this route",
  findings_declaration_only_in_example:
    "the reviewer's only envelope for this route was inside a code example, which is never a declaration",
  findings_declaration_ambiguous: 'more than one declaration for this route',
  findings_declaration_incomplete: 'the declaration was cut off or not closed',
  findings_declaration_malformed: 'the declaration was not in the declared format',
  findings_declaration_over_bound: 'the declaration exceeded its bounds (nothing was cut)',
  findings_declaration_count_mismatch: 'the declared count did not add up (a finding is missing)',
  findings_declaration_wrong_task: 'the declaration named another task',
  findings_declaration_wrong_round: 'the declaration named another round',
  findings_declaration_wrong_reviewer: 'the declaration named another reviewer',
  findings_declaration_wrong_candidate: 'the declaration named another candidate',
  findings_route_malformed: 'the routing record was malformed',
  findings_route_not_for_this_task: 'the routing record was for another task',
  findings_route_not_for_this_round: 'the routing record was for another round',
  findings_route_not_for_this_reviewer: 'the routing record was for another reviewer',
  findings_capture_over_bound: "the reviewer's output was over the capture bound",
  findings_capture_unavailable: "the reviewer's output could not be captured",
  findings_writer_error: 'the review gate could not record the declaration',
  findings_reference_malformed: "the task's findings reference was malformed",
  findings_round_mismatch: 'the findings record is for another round',
  findings_reviewer_mismatch: 'the findings record names another reviewer',
  findings_task_mismatch: 'the findings record names another task',
  findings_preamble_mismatch: "the findings record is not bound to this round's preamble",
  findings_count_mismatch: 'the findings record count disagrees with its reference',
  findings_record_digest_mismatch: 'the findings record changed after it was recorded',
  findings_record_over_bound: 'the findings record is over its size bound',
  findings_record_shape_invalid: 'the findings record is not the declared shape',
  findings_record_not_json: 'the findings record is not JSON',
  findings_record_absent: 'the findings record is missing',
  findings_record_not_contained: 'the findings record is a link or outside the review directory',
  findings_record_not_regular: 'the findings record is not a regular file',
  findings_record_hardlinked: 'the findings record is hard-linked',
  findings_record_writable_by_group_or_other: 'the findings record is writable by others',
  findings_record_unreadable: 'the findings record could not be read',
  findings_root_identity_changed: 'the review directory changed while it was read',
  findings_capture_over_budget:
    'withheld whole: the capture would have exceeded its read cap (nothing was cut)',
};
// END FINDINGS_CODES

export function plainFindingsCode(problem: string | null): string {
  if (problem === null) return 'no reason given';
  return `${FINDINGS_WORDS[problem] ?? 'unknown reason'} (${problem})`;
}

/** One line for a findings block's state; the list itself is rendered separately. */
export function plainFindingsState(
  state: string,
  problem: string | null,
  count: number | null,
): string {
  switch (state) {
    case 'present':
      return `${count ?? '?'} declared by the reviewer`;
    case 'empty':
      return 'the reviewer declared none for this round';
    case 'not_recorded':
      return `not recorded: ${plainFindingsCode(problem)}`;
    case 'refused':
      return `refused: ${plainFindingsCode(problem)}`;
    case 'not_reported':
      return 'not reported by this backend (it predates declared findings)';
    default:
      return 'the findings record did not check out; nothing is shown';
  }
}
