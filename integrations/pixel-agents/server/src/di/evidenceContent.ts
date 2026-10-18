/**
 * Item 2 (original 10) — the evidence library's CONTENT path, office side.
 *
 * A client asks with `diEvidenceRead`: an ADMITTED REFERENCE (Mission ref, artifact id and the
 * journal digest of the listing it was shown) — never a path, root or URL. The office:
 *
 * 1. parses it EXACTLY (four keys, the backend's own grammar) — anything else is refused unread;
 * 2. admits it against its CURRENT listing: the Mission is listed once, its content reads are
 *    open (Mission Core, bound, the office live), the listing's journal digest is the one the
 *    client saw, and the artifact is listed there as requestable;
 * 3. asks the backend's /artifact route (one read at a time, at most one per
 *    EVIDENCE_MIN_INTERVAL_MS), which re-checks the binding, containment, disclosure class and
 *    digest against the record itself;
 * 4. RE-CHECKS the backend's answer before relaying it: closed key sets, the same reference, a
 *    known problem code, and for admitted content the text's own sha256 recomputed here must equal
 *    the recorded digest, within MAX_EVIDENCE_TEXT_BYTES;
 * 5. re-admits against the listing NOW: an answer whose listing moved while it was read is
 *    discarded, never relayed.
 *
 * The answer goes to the asking connection only, as data. Nothing here renders, executes,
 * follows, fetches or writes anything; the text is untrusted and only ever shown as text.
 */

import * as crypto from 'crypto';

import type { DiEvidenceContent } from '../../../core/src/messages.js';
import type { ArtifactReference } from './backendClient.js';
import { isArtifactReference } from './backendClient.js';
import type { OfficeView } from './officeView.js';

/** At most one backend content read per this interval (one is in flight at most, always). */
export const EVIDENCE_MIN_INTERVAL_MS = 1_000;
export const EVIDENCE_REQUEST_TIMEOUT_MS = 10_000;
/** The backend's own per-file bound (prepare.MAX_INSTRUCTION_FILE_BYTES), never raised here. */
export const MAX_EVIDENCE_TEXT_BYTES = 65_536;
const MAX_DETAIL_CHARS = 240;
const MAX_LOCATOR_CHARS = 2_048;

/** Refusals decided by the OFFICE (before or after asking the backend). */
export const OFFICE_EVIDENCE_PROBLEMS = [
  'mission_not_listed',
  'mission_listed_more_than_once',
  'evidence_not_projected',
  'content_reads_unavailable',
  'listing_moved',
  'reference_not_listed',
  'reference_has_no_content',
  'read_in_progress',
  'read_rate_limited',
  'content_reads_not_supported_by_this_backend',
  'backend_discovery_unavailable',
  'backend_unavailable',
  'backend_refused_request',
  'backend_answer_invalid',
  'listing_moved_during_read',
  // Item 2: the backend read under another EXECUTION basis than the one the office listed
  // (bound vs historical completed execution) — never relayed.
  'execution_basis_mismatch',
] as const;
export type OfficeEvidenceProblem = (typeof OFFICE_EVIDENCE_PROBLEMS)[number];

/**
 * The backend's CLOSED refusal vocabulary (target_runtime.artifact_content.PROBLEMS). Parity is
 * pinned by tests/test_artifact_content.py, which reads the list between these markers.
 */
// BEGIN ARTIFACT_CONTENT_PROBLEMS
export const ARTIFACT_CONTENT_PROBLEMS = [
  'content_needs_contract_version_2',
  'owner_shut_down',
  'content_read_in_progress',
  'no_fresh_catalog_answer',
  'mission_not_listed',
  'mission_listed_more_than_once',
  'source_kind_has_no_artifact_content',
  'mission_binding_not_bound',
  'mission_task_or_workspace_unresolved',
  'mission_state_not_observed',
  'cursor_not_the_listed_cursor',
  'registry_unreadable',
  'source_not_registered',
  'mission_store_unavailable',
  'mission_absent_from_store',
  'mission_moved_since_listing',
  'mission_task_or_workspace_changed',
  'workspace_not_one_resolved_root',
  'artifact_not_recorded',
  'artifact_recorded_more_than_once',
  'locator_kind_has_no_content',
  'artifact_recorded_unavailable',
  'artifact_has_no_recorded_digest',
  'locator_not_a_contained_relative_path',
  'artifact_class_not_declared',
  'locator_in_private_location',
  'locator_credential_like',
  'locator_format_not_supported',
  'file_missing',
  'file_over_read_bound',
  'content_not_text',
  'file_not_regular',
  'path_escapes_workspace',
  'file_hardlinked',
  'file_unreadable',
  'content_changed_since_recorded',
  'mission_moved_during_read',
  'request_read_budget_exhausted',
  'historical_identity_not_established',
  'historical_uniqueness_unverifiable',
  'historical_identity_conflict',
  'source_registration_changed',
  'historical_identity_changed',
  'historical_identity_changed_during_read',
] as const;
// END ARTIFACT_CONTENT_PROBLEMS

const SCHEMA = 'di.evidence.artifact_content';
const ANSWER_KEYS = [
  'accounting',
  'artifact',
  'artifact_id',
  'content',
  'cursor',
  'detail',
  'mission_ref',
  'observed_at',
  'problem',
  'schema',
  'state',
  'version',
];
const ARTIFACT_KEYS = [
  'admission_basis',
  'content_digest_sha256',
  'execution_basis',
  'key',
  'locator',
  'locator_kind',
  'recorded_at',
  'role',
];
/**
 * The EXECUTION a read stood on (target_runtime.artifact_content EXECUTION_*): a currently bound
 * Mission, or a COMPLETED Mission's historical recorded execution. Kept apart from the class.
 */
export const EXECUTION_BASES = ['active_binding', 'historical_completed_execution'] as const;
export type ExecutionBasis = (typeof EXECUTION_BASES)[number];
/**
 * What the canonical record DECLARED the artifact to be (target_runtime.artifact_content
 * BASIS_*): a required artifact the approved contract pins, and/or an artifact an accepted
 * verifying evidence record names. Never a path or extension.
 */
export const ADMISSION_BASES = [
  'accepted_evidence_artifact',
  'contract_required_artifact',
] as const;
export type AdmissionBasis = (typeof ADMISSION_BASES)[number];
const CONTENT_KEYS = ['byte_count', 'digest_sha256', 'media', 'text'];
const ACCOUNTING_KEYS = ['charged_bytes', 'exhausted', 'max_bytes', 'reads_refused'];
const MEDIA = ['text/plain', 'text/html'] as const;
const DIGEST = /^[0-9a-f]{64}$/;

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const exactKeys = (v: Json, keys: string[]): boolean =>
  Object.keys(v).sort().join('\u0000') === [...keys].sort().join('\u0000');
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v);
const intOrNull = (v: unknown): boolean => v === null || isInt(v);

/** A diEvidenceRead, EXACTLY: four keys, the backend's grammar; null otherwise. */
export function parseEvidenceRead(msg: Json): ArtifactReference | null {
  if (!exactKeys(msg, ['type', 'missionRef', 'artifactId', 'cursor'])) return null;
  if (msg.type !== 'diEvidenceRead') return null;
  const ref = { missionRef: msg.missionRef, artifactId: msg.artifactId, cursor: msg.cursor };
  if (!isArtifactReference(ref as Partial<ArtifactReference>)) return null;
  return ref as ArtifactReference;
}

export type Admission =
  { ok: true } | { ok: false; problem: OfficeEvidenceProblem; detail: string };

function refuse(problem: OfficeEvidenceProblem, detail: string): Admission {
  return { ok: false, problem, detail };
}

/** Is this reference listed, requestable, at the listing the client saw — NOW? */
export function admitAgainstView(view: OfficeView, ref: ArtifactReference): Admission {
  if (view.missions.state !== 'observed') {
    return refuse('content_reads_unavailable', `no Missions are observed (${view.missions.state})`);
  }
  const found = view.missions.value.filter((m) => m.missionRef === ref.missionRef);
  if (found.length === 0) return refuse('mission_not_listed', 'the office does not list it now');
  if (found.length > 1) return refuse('mission_listed_more_than_once', 'listed more than once');
  const library = found[0].evidence;
  if (library === null) return refuse('evidence_not_projected', 'this office projects no library');
  if (library.contentReads.state !== 'available') {
    return refuse(
      'content_reads_unavailable',
      `content reads are closed for this Mission (${library.contentReads.reason ?? 'unknown'})`,
    );
  }
  if (library.cursor !== ref.cursor) {
    return refuse('listing_moved', 'the Mission moved since this listing; select it again');
  }
  const artifact = library.artifacts.items.find((a) => a.artifactId === ref.artifactId);
  if (artifact === undefined) {
    return refuse('reference_not_listed', 'the artifact is not listed for this Mission now');
  }
  if (artifact.content.state !== 'requestable') {
    return refuse(
      'reference_has_no_content',
      `this artifact has no content to read (${artifact.content.reason ?? 'unknown'})`,
    );
  }
  return { ok: true };
}

/** The EXECUTION basis the office lists NOW for the Mission a reference names ('none' if none). */
export function executionBasisListed(
  view: OfficeView,
  ref: ArtifactReference,
): ExecutionBasis | 'none' {
  if (view.missions.state !== 'observed') return 'none';
  const found = view.missions.value.filter((m) => m.missionRef === ref.missionRef);
  if (found.length !== 1 || found[0].evidence === null) return 'none';
  return found[0].evidence.execution.basis;
}

export interface ParsedContentAnswer {
  state: 'admitted' | 'refused';
  problem: string | null;
  detail: string | null;
  artifact: {
    key: string | null;
    role: string;
    locatorKind: string;
    locator: string;
    contentDigest: string;
    recordedAt: number | null;
    admissionBasis: AdmissionBasis[];
    executionBasis: ExecutionBasis;
  } | null;
  content: {
    text: string;
    byteCount: number;
    digest: string;
    media: (typeof MEDIA)[number];
  } | null;
  observedAt: number | null;
}

const invalid = (): { ok: false; problem: 'backend_answer_invalid'; detail: string } => ({
  ok: false,
  problem: 'backend_answer_invalid',
  detail: 'the backend answer is not exactly the content answer for this reference',
});

/** The backend's answer, re-checked; anything not exactly right is `backend_answer_invalid`. */
export function parseArtifactContentAnswer(
  body: unknown,
  ref: ArtifactReference,
):
  | { ok: true; answer: ParsedContentAnswer }
  | { ok: false; problem: 'backend_answer_invalid'; detail: string } {
  if (!isObject(body) || !exactKeys(body, ANSWER_KEYS)) return invalid();
  if (body.schema !== SCHEMA || body.version !== 1) return invalid();
  if (
    body.mission_ref !== ref.missionRef ||
    body.artifact_id !== ref.artifactId ||
    body.cursor !== ref.cursor
  ) {
    return invalid();
  }
  const accounting = body.accounting;
  if (!isObject(accounting) || !exactKeys(accounting, ACCOUNTING_KEYS)) return invalid();
  if (
    !isInt(accounting.max_bytes) ||
    !isInt(accounting.charged_bytes) ||
    !isInt(accounting.reads_refused) ||
    typeof accounting.exhausted !== 'boolean'
  ) {
    return invalid();
  }
  if (!intOrNull(body.observed_at)) return invalid();
  const detail = body.detail;
  if (detail !== null && (typeof detail !== 'string' || detail.length > MAX_DETAIL_CHARS)) {
    return invalid();
  }
  const observedAt = body.observed_at as number | null;
  if (body.state === 'refused') {
    if (body.artifact !== null || body.content !== null || typeof detail !== 'string') {
      return invalid();
    }
    const problem = body.problem;
    if (!(ARTIFACT_CONTENT_PROBLEMS as readonly unknown[]).includes(problem)) return invalid();
    return {
      ok: true,
      answer: {
        state: 'refused',
        problem: problem as string,
        detail,
        artifact: null,
        content: null,
        observedAt,
      },
    };
  }
  if (body.state !== 'admitted' || body.problem !== null || detail !== null) return invalid();
  const a = body.artifact;
  const c = body.content;
  if (!isObject(a) || !exactKeys(a, ARTIFACT_KEYS)) return invalid();
  if (!isObject(c) || !exactKeys(c, CONTENT_KEYS)) return invalid();
  if (
    (a.key !== null && typeof a.key !== 'string') ||
    typeof a.role !== 'string' ||
    a.locator_kind !== 'REPOSITORY_PATH' ||
    typeof a.locator !== 'string' ||
    a.locator.length > MAX_LOCATOR_CHARS ||
    typeof a.content_digest_sha256 !== 'string' ||
    !DIGEST.test(a.content_digest_sha256) ||
    !intOrNull(a.recorded_at)
  ) {
    return invalid();
  }
  const basis = a.admission_basis;
  if (
    !Array.isArray(basis) ||
    basis.length === 0 ||
    basis.length > ADMISSION_BASES.length ||
    new Set(basis).size !== basis.length ||
    !basis.every((b) => (ADMISSION_BASES as readonly unknown[]).includes(b))
  ) {
    return invalid();
  }
  if (!(EXECUTION_BASES as readonly unknown[]).includes(a.execution_basis)) return invalid();
  if (typeof c.text !== 'string' || !isInt(c.byte_count) || typeof c.digest_sha256 !== 'string') {
    return invalid();
  }
  const bytes = Buffer.byteLength(c.text, 'utf8');
  if (bytes !== c.byte_count || bytes > MAX_EVIDENCE_TEXT_BYTES) return invalid();
  // The bridge's OWN check: these bytes are the recorded artifact, or nothing is relayed.
  const digest = crypto.createHash('sha256').update(c.text, 'utf8').digest('hex');
  if (digest !== c.digest_sha256 || digest !== a.content_digest_sha256) return invalid();
  if (!(MEDIA as readonly unknown[]).includes(c.media)) return invalid();
  return {
    ok: true,
    answer: {
      state: 'admitted',
      problem: null,
      detail: null,
      artifact: {
        key: a.key as string | null,
        role: a.role,
        locatorKind: a.locator_kind,
        locator: a.locator,
        contentDigest: a.content_digest_sha256,
        recordedAt: a.recorded_at as number | null,
        admissionBasis: [...(basis as AdmissionBasis[])],
        executionBasis: a.execution_basis as ExecutionBasis,
      },
      content: { text: c.text, byteCount: bytes, digest, media: c.media as (typeof MEDIA)[number] },
      observedAt,
    },
  };
}

/** A refusal the office decided: no artifact, no content. */
export function officeRefusal(
  ref: ArtifactReference,
  problem: OfficeEvidenceProblem,
  detail: string,
): DiEvidenceContent {
  return {
    type: 'diEvidenceContent',
    ...ref,
    state: 'refused',
    decidedBy: 'office',
    problem,
    detail: detail.slice(0, MAX_DETAIL_CHARS),
    artifact: null,
    content: null,
    observedAt: null,
  };
}

/** The backend's re-checked answer, relayed as data. */
export function backendMessage(
  ref: ArtifactReference,
  answer: ParsedContentAnswer,
): DiEvidenceContent {
  return {
    type: 'diEvidenceContent',
    ...ref,
    state: answer.state,
    decidedBy: 'backend',
    problem: answer.problem,
    detail: answer.detail,
    artifact:
      answer.artifact === null
        ? null
        : { ...answer.artifact, admissionBasis: [...answer.artifact.admissionBasis] },
    content: answer.content === null ? null : { ...answer.content },
    observedAt: answer.observedAt,
  };
}
