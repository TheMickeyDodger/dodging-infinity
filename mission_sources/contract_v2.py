"""Query contract VERSION 2 of ``mission_sources``: the recorded child task
chains, the Mission State projection and the one admitted artifact content
class, added beside the accepted version 1 contract, which is unchanged.

Selection. Version 2 is selected EXPLICITLY, at LAUNCH, per process: the
catalog endpoint takes ``--contract-version`` (``1``, the default, or
``2``) and serves exactly that version for its whole life. Nothing selects
a version per request: a ``contract_version`` query field is not a
selector (the adapter refuses any query field but ``wait_seconds``), and
there is no automatic or time-based change of the default. Comparing the
two versions therefore takes TWO processes, one launched with each. A
version 1 consumer that meets a version 2 response reads it
``incompatible`` (``contract.response_freshness``), the designed, honest
refusal; ``response_freshness_v2`` is the version 2 consumer operation.

What version 2 adds, and nothing else:

- ``chain_observation`` (top level): the health block of the injected chain
  observation (below), taken by the same capture as the runtime snapshot.
- ``missions[].members[]``: ``member_ref`` is TASK-QUALIFIED
  (``<mission_ref>#<task_id>#<logical>``), and every member carries
  ``chain`` (``direct`` or ``child``) and ``chain_task_id``. Members of
  every bound chain are classified against claims counted across ALL bound
  tasks, so one live agent or session is never represented twice.
- ``missions[].task_chains[]``: one entry per chain, the direct chain first.
  ``binding`` reuses the version 1 binding vocabulary; ``membership_end`` is
  the herdr task rule (``herdr.vintage.is_terminal_status``: COMPLETE,
  COMPLETED, ABANDONED and FAILED end LIVE MEMBERSHIP); ``dependency`` is
  the DIFFERENT dependency rule (``herdr.dependencies``: only a child task
  whose status is COMPLETE SATISFIES a dependency). The two rules answer
  different questions and are never substituted: a FAILED or ABANDONED child
  ENDS membership (its roles are not live) AND remains an UNSATISFIED
  dependency. A recorded child status (written when the child was started)
  is never lifecycle truth: liveness and terminality come only from the
  child's OWN root, observed by the capture.
- ``missions[].children_record``: what the parent root's children record
  said, with four outcomes kept apart: a valid record holding no child for
  the current task is ``observed_empty`` (carrying the writer caveat: the
  writer is a non-atomic read-modify-write that resets the record to empty
  on any parse error, so an observed empty is weaker evidence than it
  looks); a missing file, a zero-byte file and unparseable content are each
  ``unavailable`` with their OWN problem code. Only the direct task's
  children are followed (depth 1); a child's own recorded children are
  counted (``descendants_not_followed``) and never captured.
- ``missions[].mission_state``: Mission Core's OWN read-only observation
  report (``mission.observation``), verbatim, built over caller-materialized
  inputs whose head cursor was sampled BEFORE any report was collected and
  passed unchanged, so a head that moved during collection stays ``moved``
  and those reports never resolve. ``candidate_applicability`` is always
  ``unproven`` (nothing binds a review round to candidate bytes); the
  candidate's ``baseline_digest_sha256`` is always None and the baseline is
  ``unobserved`` (a recorded anchor is never presented as an observation).
  Without an activation contract the report's ``proof``, ``readiness``,
  ``dependencies`` and ``budget`` values are None: NOT APPLICABLE, never
  "none recorded".
- ``missions[].workflow_phase``: the workflow phase DECLARED as its own typed
  field (``state`` observed / not_applicable, ``problem``, ``phase`` of the
  closed ``WORKFLOW_PHASES``), so a consumer never branches on the
  display-only ``lifecycle.state``. Only a ``workflow_authority.workflow_phase/2``
  record has one (its record's own phase); Mission Core is ``not_applicable``
  (``lifecycle_model_has_no_workflow_phase``) and is never given one.
- ``missions[].delivery_link``: which ONE delivery record, if any, the
  catalog's registered delivery store links to the Mission (``state``,
  ``delivery_id``, ``basis``; see ``MISSION_LINK_KEYS``). Only a workflow
  Mission can be linked: by the one record whose own ``mission.workflow_id``
  equals its ``native_id``, while every registered workflow source was read
  fresh and no other workflow Mission holds that id. Mission Core, and every
  other model, is ``unlinked_model_not_recorded``: not known, because no
  delivery record names such a Mission. At most one id, never a list or a
  count; a link authorizes nothing.
- ``missions[].admitted_artifacts.canonical_review``: NARROW CANONICAL REVIEW
  RECORD CONTENT INSPECTION, the ONLY admitted content class. It is reached
  from the task record's own canonical reference (``review_rounds``,
  ``last_review_decision``, ``last_review_file``), never from a registered
  artifact, and never by opening the recorded path: the file is opened by
  descent from the pinned root through the EXPECTED components
  ``.herd/state/reviews/<task_id>-round-<NN>.md``; the recorded path is only
  compared for equality. Only the preamble before the ``## Transcript`` line
  is read (byte by byte, so no transcript byte is ever read); exactly four
  fields project: ``round``, ``reviewer_logical``, ``reviewer_agent`` and
  ``protocol_token`` (APPROVE, REJECT or MISSING). Corroboration is the
  writer's own pairing of TWO vocabularies (the preamble carries the RAW
  token or MISSING; the task carries the PARSED decision or MALFORMED):
  APPROVE/APPROVE, REJECT/REJECT and MISSING/MALFORMED are accepted;
  anything else refuses with a precise reason, and an out-of-enum raw token
  is never emitted. ``gate`` (``parsed`` / ``unparsed``) is the GATE fact and
  ``verdict`` the REVIEW fact, kept apart: ``unparsed`` (MISSING/MALFORMED)
  means the gate could not parse a valid token; it is NOT a rejection and
  says nothing about the candidate. ``currency`` is ``possibly_stale`` when
  the task is not ACTIVE (the writer updates the task record only while
  ACTIVE). INTEGRITY is ``linkage_corroborated``: no recorded digest exists
  for a canonical review file, so its bytes are NOT digest-anchored. The
  mode rule refuses a group- or other-WRITABLE file (``0o022``), a
  deliberate, reasoned deviation from the private-file rule (``0o077``):
  review files are written world-readable (0644), so the private-file rule
  would refuse every real one; the threat here is a forged verdict, which
  needs WRITE access, not disclosure. Evidence and result artifact CONTENT
  is not admitted here (the evidence library reads it through its own route,
  ``target_runtime.artifact_content``). Item 2 E2 adds an OPTIONAL
  ``findings`` block to an admitted review: the reviewer-DECLARED public
  findings the local review gate validated and persisted beside the round
  file, re-validated by the reader (``FINDINGS_KEYS``, ``validate_findings``,
  ``validate_findings_record``); never transcript text.

The chain observation is an injected VALUE, like the runtime snapshot; its
producer (the capture) lives outside this package. Shape, version 1::

    {"schema": "di.mission_sources.chain_observation", "version": 1,
     "observed_at": <int>, "producer": <str>,
     "roots": [{"root": <str>, "tier": registered|discovered|child,
                "task": None | {"task_id", "status", "membership_ended",
                                "dependency_complete"},
                "children": {"state", "problem", "caveat", "record_count",
                             "other_parent_count", "records": [...]},
                "review": {"state", "problem", "record", "gate", "verdict",
                           "currency", "task_status", "integrity"}}],
     "unavailable": [{"root", "tier", "problem", "detail"}],
     "over_capacity": [{"root", "tier"}]}

Every key is required (closed sets); validation refuses and never repairs.
Nothing here reads a clock, a file or the network.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from mission_sources import contract

# -- versioned contract -------------------------------------------------

QUERY_CONTRACT_VERSION_V2 = 2
#: The versions a catalog process may be launched to serve, one per process.
CONTRACT_VERSIONS = (contract.QUERY_CONTRACT_VERSION,
                     QUERY_CONTRACT_VERSION_V2)
DEFAULT_CONTRACT_VERSION = contract.QUERY_CONTRACT_VERSION

RESPONSE_KEYS_V2 = ("contract", "contract_version", "generated_at",
                    "max_age_seconds", "valid_until", "limits", "registry",
                    "sources", "runtime_snapshot", "chain_observation",
                    "missions", "unmatched_runtime_agent_count",
                    "runtime_agents", "delivery_presentations")

# -- hard bounds (never derived from input) -----------------------------

#: Children recorded for ONE parent task that the capture projects.
MAX_CHILD_RECORDS = 32
#: Only the direct task's children are followed.
MAX_CHAIN_DEPTH = 1
#: Mission State reports built per refresh (each one Mission Core read).
MAX_OBSERVED_MISSIONS = 16
#: The canonical review preamble: bytes before the marker line, and lines.
MAX_REVIEW_PREAMBLE_BYTES = 4096
MAX_REVIEW_PREAMBLE_LINES = 16
#: A canonical review round is exactly two digits in its file name.
MAX_REVIEW_ROUND = 99
MAX_REVIEWER_NAME_CHARS = 128
#: The capture's own root capacity (``runtime_snapshot.MAX_HERD_ROOTS``);
#: re-declared because this package does not import the producer, and
#: pinned equal by test.
MAX_HERD_ROOTS_V2 = 16
#: The most missions one response carries (``sources.MAX_QUERY_MISSIONS``,
#: re-declared and pinned equal by test).
MAX_RESPONSE_MISSIONS = 2048
#: A reported Mission State answer is stale after this long
#: (``mission.reconciliation.REPORTED_FRESHNESS_BOUND_SECONDS``, re-declared
#: and pinned equal by test).
REPORTED_FRESHNESS_BOUND_SECONDS = 600

# -- chain observation ----------------------------------------------------

CHAIN_OBSERVATION_SCHEMA = "di.mission_sources.chain_observation"
CHAIN_OBSERVATION_VERSION = 1
CHAIN_OBSERVATION_KEYS = ("schema", "version", "observed_at", "producer",
                          "roots", "unavailable", "over_capacity")
ROOT_KEYS = ("root", "tier", "task", "children", "review")
ROOT_TASK_KEYS = ("task_id", "status", "membership_ended",
                  "dependency_complete")
UNAVAILABLE_ROOT_KEYS = ("root", "tier", "problem", "detail")
OVER_CAPACITY_ROOT_KEYS = ("root", "tier")

TIER_REGISTERED = "registered"
TIER_DISCOVERED = "discovered"
TIER_CHILD = "child"
TIERS = (TIER_REGISTERED, TIER_DISCOVERED, TIER_CHILD)

# The children record of one root (the fourth fixed state file).
CHILDREN_FILE = "children.json"
CHILDREN_KEYS = ("state", "problem", "caveat", "record_count",
                 "other_parent_count", "records")
CHILD_RECORD_KEYS = ("parent_task_id", "child_task_id", "repo",
                     "workspace_id", "dependency", "declared_agents")
DECLARED_AGENT_KEYS = ("logical", "agent")
CHILDREN_OBSERVED = "observed"
CHILDREN_OBSERVED_EMPTY = "observed_empty"
CHILDREN_UNAVAILABLE = "unavailable"
CHILDREN_NOT_APPLICABLE = "not_applicable"
CHILDREN_STATES = (CHILDREN_OBSERVED, CHILDREN_OBSERVED_EMPTY,
                   CHILDREN_UNAVAILABLE)
MISSION_CHILDREN_STATES = CHILDREN_STATES + (CHILDREN_NOT_APPLICABLE,)
CAVEAT_WRITER_RESET = "writer_resets_record_to_empty_on_parse_error"
PROBLEM_CHILDREN_ABSENT = "children_record_absent"
PROBLEM_CHILDREN_ZERO_BYTES = "children_record_zero_bytes"
PROBLEM_CHILDREN_NOT_JSON = "children_record_not_json"
PROBLEM_CHILDREN_SHAPE = "children_record_shape_invalid"
PROBLEM_CHILDREN_OVER_LIMIT = "children_record_over_limit"
PROBLEM_CHILDREN_REFUSED = "children_record_read_refused"
PROBLEM_CHILDREN_NO_TASK = "children_record_without_current_task"
CHILDREN_PROBLEMS = (PROBLEM_CHILDREN_ABSENT, PROBLEM_CHILDREN_ZERO_BYTES,
                     PROBLEM_CHILDREN_NOT_JSON, PROBLEM_CHILDREN_SHAPE,
                     PROBLEM_CHILDREN_OVER_LIMIT, PROBLEM_CHILDREN_REFUSED,
                     PROBLEM_CHILDREN_NO_TASK)
# Mission-level reasons a parent's children record does not apply.
PROBLEM_CHAIN_OBSERVATION_NOT_FRESH = "chain_observation_not_fresh"
PROBLEM_PARENT_ROOT_NOT_OBSERVED = "parent_root_not_observed"
PROBLEM_PARENT_TASK_MISMATCH = "parent_task_mismatch"
PROBLEM_MISSION_NOT_BOUND_TO_ROOT = "mission_execution_unresolved"
PROBLEM_MISSION_NOT_ACTIVE = "mission_not_active"
#: A task-native record that DEFERS to another record (``roster.deferrals``):
#: its root's children are followed by that record, never twice.
PROBLEM_CHILDREN_FOLLOWED_ELSEWHERE = (
    "children_followed_by_the_record_it_defers_to")
MISSION_CHILDREN_PROBLEMS = CHILDREN_PROBLEMS + (
    PROBLEM_CHAIN_OBSERVATION_NOT_FRESH, PROBLEM_PARENT_ROOT_NOT_OBSERVED,
    PROBLEM_PARENT_TASK_MISMATCH, PROBLEM_MISSION_NOT_BOUND_TO_ROOT,
    PROBLEM_MISSION_NOT_ACTIVE, PROBLEM_CHILDREN_FOLLOWED_ELSEWHERE)

# The canonical review record (the ONE admitted content class).
REVIEW_KEYS = ("state", "problem", "record", "gate", "verdict", "currency",
               "task_status", "integrity")
REVIEW_RECORD_KEYS = ("round", "reviewer_logical", "reviewer_agent",
                      "protocol_token")
REVIEW_ADMITTED = "admitted"
REVIEW_REFUSED = "refused"
REVIEW_NOT_RECORDED = "not_recorded"
REVIEW_NOT_APPLICABLE = "not_applicable"
REVIEW_UNAVAILABLE = "unavailable"
REVIEW_STATES = (REVIEW_ADMITTED, REVIEW_REFUSED, REVIEW_NOT_RECORDED,
                 REVIEW_NOT_APPLICABLE)
MISSION_REVIEW_STATES = REVIEW_STATES + (REVIEW_UNAVAILABLE,)
TOKEN_APPROVE = "APPROVE"
TOKEN_REJECT = "REJECT"
TOKEN_MISSING = "MISSING"
PROTOCOL_TOKENS = (TOKEN_APPROVE, TOKEN_REJECT, TOKEN_MISSING)
DECISION_MALFORMED = "MALFORMED"
TASK_REVIEW_DECISIONS = (TOKEN_APPROVE, TOKEN_REJECT, DECISION_MALFORMED)
#: The writer's own pairing of the preamble's RAW token with the task's
#: PARSED decision; every other pair refuses.
CORROBORATED_PAIRS = ((TOKEN_APPROVE, TOKEN_APPROVE),
                      (TOKEN_REJECT, TOKEN_REJECT),
                      (TOKEN_MISSING, DECISION_MALFORMED))
GATE_PARSED = "parsed"
GATE_UNPARSED = "unparsed"
GATES = (GATE_PARSED, GATE_UNPARSED)
VERDICTS = (TOKEN_APPROVE, TOKEN_REJECT)
CURRENCY_CURRENT = "current"
CURRENCY_POSSIBLY_STALE = "possibly_stale"
CURRENCIES = (CURRENCY_CURRENT, CURRENCY_POSSIBLY_STALE)
#: The task status under which the writer keeps the review fields current.
REVIEW_CURRENT_TASK_STATUS = "ACTIVE"
INTEGRITY_LINKAGE_CORROBORATED = "linkage_corroborated"
CANDIDATE_APPLICABILITY_UNPROVEN = "unproven"
PROBLEM_REVIEW_NO_BINDING = "review_root_not_bound_to_a_mission_task"
PROBLEM_REVIEW_TASK_ABSENT = "review_task_record_absent"
PROBLEM_REVIEW_TASK_MISMATCH = "review_task_mismatch"
PROBLEM_REVIEW_REFERENCE_INCOMPLETE = "review_reference_incomplete"
PROBLEM_REVIEW_ROUND_INVALID = "review_round_invalid"
PROBLEM_REVIEW_ROUND_OUT_OF_CLASS = "review_round_out_of_filename_class"
PROBLEM_REVIEW_DECISION_UNKNOWN = "review_task_decision_unknown"
PROBLEM_REVIEW_FILE_OUTSIDE = "review_file_outside_reviews_directory"
PROBLEM_REVIEW_FILE_NAME = "review_file_name_mismatch"
PROBLEM_REVIEW_NOT_CONTAINED = "review_path_not_contained"
PROBLEM_REVIEW_ABSENT = "review_file_absent"
PROBLEM_REVIEW_NOT_REGULAR = "review_file_not_regular"
PROBLEM_REVIEW_HARDLINK = "review_file_hardlinked"
PROBLEM_REVIEW_WRITABLE = "review_file_writable_by_group_or_other"
PROBLEM_REVIEW_UNREADABLE = "review_file_unreadable"
PROBLEM_REVIEW_OVER_BOUND = "review_preamble_over_bound"
PROBLEM_REVIEW_NO_MARKER = "review_transcript_marker_absent"
PROBLEM_REVIEW_NOT_UTF8 = "review_preamble_not_utf8"
PROBLEM_REVIEW_SHAPE = "review_preamble_shape_invalid"
PROBLEM_REVIEW_ROUND_DISAGREES = "review_round_disagrees"
PROBLEM_REVIEW_TOKEN_OUT_OF_ENUM = "review_raw_token_out_of_enum"
PROBLEM_REVIEW_DISAGREES = "review_decision_disagrees"
PROBLEM_REVIEW_ROOT_CHANGED = "review_root_identity_changed"
REVIEW_PROBLEMS = (
    PROBLEM_REVIEW_NO_BINDING, PROBLEM_REVIEW_TASK_ABSENT,
    PROBLEM_REVIEW_TASK_MISMATCH, PROBLEM_REVIEW_REFERENCE_INCOMPLETE,
    PROBLEM_REVIEW_ROUND_INVALID, PROBLEM_REVIEW_ROUND_OUT_OF_CLASS,
    PROBLEM_REVIEW_DECISION_UNKNOWN, PROBLEM_REVIEW_FILE_OUTSIDE,
    PROBLEM_REVIEW_FILE_NAME, PROBLEM_REVIEW_NOT_CONTAINED,
    PROBLEM_REVIEW_ABSENT, PROBLEM_REVIEW_NOT_REGULAR,
    PROBLEM_REVIEW_HARDLINK, PROBLEM_REVIEW_WRITABLE,
    PROBLEM_REVIEW_UNREADABLE, PROBLEM_REVIEW_OVER_BOUND,
    PROBLEM_REVIEW_NO_MARKER, PROBLEM_REVIEW_NOT_UTF8, PROBLEM_REVIEW_SHAPE,
    PROBLEM_REVIEW_ROUND_DISAGREES, PROBLEM_REVIEW_TOKEN_OUT_OF_ENUM,
    PROBLEM_REVIEW_DISAGREES, PROBLEM_REVIEW_ROOT_CHANGED,
)
# -- reviewer-declared PUBLIC findings (item 2 E2) ----------------------------
#
# An ADMITTED review block MAY carry ``findings`` (absent: a producer that
# predates the field; an additive key). It is read ONLY from the validated
# sibling record the local review gate persists
# (``<task>-round-NN.findings.json``), bound by digest to the task record and
# by round, reviewer and preamble digest to the admitted preamble; no
# transcript byte is ever read. Four states, never collapsed: NOT RECORDED
# (``findings_legacy_record_predates_field`` for a task record written before
# the field existed, else the writer's own not-recorded code), EMPTY (a valid
# declaration of no findings), PRESENT, and REFUSED (the writer's or the
# reader's code). The vocabulary below RE-DECLARES ``target_runtime.review_findings``
# (this package does not import ``herdr``); parity is pinned by test.
REVIEW_FINDINGS_KEY = "findings"
FINDINGS_KEYS = ("state", "problem", "count", "items", "candidate_sha256",
                 "routed_at", "linkage", "provenance")
FINDING_ITEM_KEYS = ("index", "severity", "area", "line", "summary")
#: The task reference state of a persisted record (the writer's outcome).
FINDINGS_REFERENCE_RECORDED = "recorded"
FINDINGS_NOT_RECORDED = "not_recorded"
FINDINGS_EMPTY = "empty"
FINDINGS_PRESENT = "present"
FINDINGS_REFUSED = "refused"
FINDINGS_STATES = (FINDINGS_NOT_RECORDED, FINDINGS_EMPTY, FINDINGS_PRESENT,
                   FINDINGS_REFUSED)
FINDINGS_SEVERITIES = ("blocker", "non_blocking")
MAX_FINDINGS = 32
MAX_FINDING_SUMMARY_CHARS = 280
MAX_FINDING_AREA_CHARS = 200
FINDINGS_LINKAGE = "route_nonce_echoed_in_routed_reviewer_output"
FINDINGS_PROVENANCE = (
    "Reviewer-declared public findings: model-authored text the routed"
    " reviewer's own output declared in the findings envelope, captured by the"
    " local review gate and bound to this task, round, reviewer and routed"
    " candidate by the route's single-use nonce it echoed. This is not a"
    " cryptographic proof of reviewer identity, not a verdict and not approval"
    " authority, and it does not prove that the reviewed bytes are the"
    " candidate's; the verdict is the round's protocol token alone.")
FINDINGS_NOT_RECORDED_PROBLEMS = ("findings_declaration_not_routed",
                                  "findings_declaration_missing",
                                  "findings_declaration_only_in_example")
FINDINGS_WRITER_PROBLEMS = (
    "findings_declaration_ambiguous", "findings_declaration_incomplete",
    "findings_declaration_malformed", "findings_declaration_over_bound",
    "findings_declaration_count_mismatch", "findings_declaration_wrong_task",
    "findings_declaration_wrong_round",
    "findings_declaration_wrong_reviewer",
    "findings_declaration_wrong_candidate", "findings_route_malformed",
    "findings_route_not_for_this_task", "findings_route_not_for_this_round",
    "findings_route_not_for_this_reviewer", "findings_capture_over_bound",
    "findings_capture_unavailable", "findings_writer_error")
PROBLEM_FINDINGS_LEGACY = "findings_legacy_record_predates_field"
PROBLEM_FINDINGS_REFERENCE = "findings_reference_malformed"
PROBLEM_FINDINGS_ROUND = "findings_round_mismatch"
PROBLEM_FINDINGS_REVIEWER = "findings_reviewer_mismatch"
PROBLEM_FINDINGS_TASK = "findings_task_mismatch"
PROBLEM_FINDINGS_PREAMBLE = "findings_preamble_mismatch"
PROBLEM_FINDINGS_COUNT = "findings_count_mismatch"
PROBLEM_FINDINGS_DIGEST_MISMATCH = "findings_record_digest_mismatch"
PROBLEM_FINDINGS_OVER_BOUND = "findings_record_over_bound"
PROBLEM_FINDINGS_SHAPE = "findings_record_shape_invalid"
PROBLEM_FINDINGS_NOT_JSON = "findings_record_not_json"
PROBLEM_FINDINGS_ABSENT = "findings_record_absent"
PROBLEM_FINDINGS_NOT_CONTAINED = "findings_record_not_contained"
PROBLEM_FINDINGS_NOT_REGULAR = "findings_record_not_regular"
PROBLEM_FINDINGS_HARDLINK = "findings_record_hardlinked"
PROBLEM_FINDINGS_WRITABLE = "findings_record_writable_by_group_or_other"
PROBLEM_FINDINGS_UNREADABLE = "findings_record_unreadable"
PROBLEM_FINDINGS_ROOT_CHANGED = "findings_root_identity_changed"
#: The capture's ONE printed value would exceed its read cap with these
#: findings in it: they are withheld whole (never cut) so the capture stands.
PROBLEM_FINDINGS_CAPTURE_BUDGET = "findings_capture_over_budget"
FINDINGS_READER_PROBLEMS = (
    PROBLEM_FINDINGS_REFERENCE, PROBLEM_FINDINGS_ROUND,
    PROBLEM_FINDINGS_REVIEWER, PROBLEM_FINDINGS_TASK,
    PROBLEM_FINDINGS_PREAMBLE, PROBLEM_FINDINGS_COUNT,
    PROBLEM_FINDINGS_DIGEST_MISMATCH, PROBLEM_FINDINGS_OVER_BOUND,
    PROBLEM_FINDINGS_SHAPE, PROBLEM_FINDINGS_NOT_JSON,
    PROBLEM_FINDINGS_ABSENT, PROBLEM_FINDINGS_NOT_CONTAINED,
    PROBLEM_FINDINGS_NOT_REGULAR, PROBLEM_FINDINGS_HARDLINK,
    PROBLEM_FINDINGS_WRITABLE, PROBLEM_FINDINGS_UNREADABLE,
    PROBLEM_FINDINGS_ROOT_CHANGED, PROBLEM_FINDINGS_CAPTURE_BUDGET)
FINDINGS_REFUSED_PROBLEMS = FINDINGS_WRITER_PROBLEMS + FINDINGS_READER_PROBLEMS
#: The persisted record (re-declared from ``target_runtime.review_findings``; parity
#: pinned by test, including accept/refuse agreement on the same inputs).
FINDINGS_RECORD_SCHEMA = "di.review.findings_record"
FINDINGS_RECORD_VERSION = 1
FINDINGS_RECORD_KEYS = ("schema", "version", "task_id", "round",
                        "reviewer_logical", "reviewer_agent",
                        "candidate_sha256", "route_nonce", "routed_at",
                        "preamble_sha256", "linkage", "provenance", "count",
                        "findings")
MAX_FINDING_LINE_NUMBER = 9999999
MAX_FINDINGS_AGGREGATE_BYTES = 8192
MAX_FINDINGS_RECORD_BYTES = 32768

# Mission-level reasons the canonical review does not project.
PROBLEM_REVIEW_ROOT_NOT_OBSERVED = "review_root_not_observed"
PROBLEM_REVIEW_KIND_NOT_APPLICABLE = "source_kind_has_no_canonical_review"
MISSION_REVIEW_PROBLEMS = REVIEW_PROBLEMS + (
    PROBLEM_CHAIN_OBSERVATION_NOT_FRESH, PROBLEM_REVIEW_ROOT_NOT_OBSERVED,
    PROBLEM_MISSION_NOT_BOUND_TO_ROOT, PROBLEM_REVIEW_KIND_NOT_APPLICABLE)

# -- task chains ----------------------------------------------------------

CHAIN_DIRECT = "direct"
CHAIN_CHILD = "child"
CHAINS = (CHAIN_DIRECT, CHAIN_CHILD)
TASK_CHAIN_KEYS = ("chain", "chain_task_id", "parent_task_id", "root",
                   "binding", "membership_end", "dependency", "record",
                   "descendants_not_followed")
CHAIN_ROOT_KEYS = ("path", "observation", "problem")
ROOT_OBSERVED = "observed"
ROOT_UNAVAILABLE = "unavailable"
ROOT_OVER_CAPACITY = "over_capacity"
ROOT_NOT_CAPTURED = "not_captured"
ROOT_UNKNOWN = "unknown"
ROOT_OBSERVATIONS = (ROOT_OBSERVED, ROOT_UNAVAILABLE, ROOT_OVER_CAPACITY,
                     ROOT_NOT_CAPTURED, ROOT_UNKNOWN)
MEMBERSHIP_END_KEYS = ("ended", "status", "rule")
MEMBERSHIP_RULE = ("terminal_status_ends_membership:"
                   "COMPLETE|COMPLETED|ABANDONED|FAILED")
DEPENDENCY_KEYS = ("recorded", "resolution", "rule")
DEPENDENCY_RULE = "dependency_satisfied_only_by_status:COMPLETE"
DEPENDENCY_SATISFIED = "satisfied"
DEPENDENCY_UNSATISFIED = "unsatisfied"
DEPENDENCY_UNKNOWN = "unknown"
DEPENDENCY_RESOLUTIONS = (DEPENDENCY_SATISFIED, DEPENDENCY_UNSATISFIED,
                          DEPENDENCY_UNKNOWN)
CHAIN_RECORD_KEYS = ("child_task_id", "repo", "workspace_id",
                     "declared_agents")
BINDING_KEYS = ("state", "reason", "task_id")
#: Version 2 binding reasons, beside the version 1 roster reasons.
REASON_MEMBERSHIP_ENDED = "chain_membership_ended"
REASON_CHILD_RECORD_STALE = "child_record_stale"
REASON_CHILD_RECORD_CYCLE = "child_record_cycle"
REASON_CHILD_RECORD_INCOMPLETE = "child_record_incomplete"
#: A child chain whose PARENT mission binding is not confirmed unique by
#: BOTH source passes (round 3, B1): unverified, never live.
REASON_PARENT_BINDING_UNCONFIRMED = "parent_binding_not_confirmed"

# -- members --------------------------------------------------------------

MEMBER_KEYS_V2 = ("member_ref", "mission_ref", "task_id", "logical", "role",
                  "agent", "provider", "pane_id", "workspace_id", "session",
                  "classification", "reason", "activity", "observed_at",
                  "chain", "chain_task_id") + contract.UNREPORTED_FACTS
MEMBER_SESSION_KEYS = ("recorded", "live")

# -- the mission view -----------------------------------------------------

MISSION_VIEW_KEYS = ("mission_ref", "source_id", "native_id", "lifecycle",
                     "producer", "revision", "objective", "updated_at",
                     "execution", "native")
MISSION_KEYS_V2 = MISSION_VIEW_KEYS + (
    "binding", "members", "task_chains", "children_record", "mission_state",
    "admitted_artifacts", "workflow_phase", "delivery_link")

# -- the declared workflow phase (version 2) ----------------------------------

#: ``missions[].workflow_phase``: the workflow phase DECLARED as its own typed
#: field, so no consumer ever branches on the display-only
#: ``lifecycle.state``. Only a ``workflow_authority.workflow_phase/2`` record
#: has one (``observed``: its record's own phase); every other model — Mission
#: Core included — has none and is ``not_applicable``, never given one.
WORKFLOW_PHASE_KEYS = ("state", "problem", "phase")
WORKFLOW_PHASE_OBSERVED = "observed"
WORKFLOW_PHASE_NOT_APPLICABLE = "not_applicable"
WORKFLOW_PHASE_STATES = (WORKFLOW_PHASE_OBSERVED,
                         WORKFLOW_PHASE_NOT_APPLICABLE)
PROBLEM_WORKFLOW_PHASE_MODEL = "lifecycle_model_has_no_workflow_phase"
#: The closed phase vocabulary of ``workflow_authority.workflow_phase/2``
#: (``workflow_authority.record.PHASES``; parity pinned by test, since this
#: package does not import ``workflow_authority``).
WORKFLOW_PHASES = (
    "PLANNED", "AUTHORIZED", "WORKSPACE_READY", "PREPARED", "VALIDATED",
    "DISPATCHED", "VERIFIED", "COMPLETED", "BLOCKED",
    "NEEDS_REAUTHORIZATION",
)
LIFECYCLE_KEYS = ("model", "state", "standing")
EXECUTION_KEYS = ("task_id", "identity_source", "workspace_realpath",
                  "run_started", "receipt_recorded", "consistent",
                  "record_ambiguous")
MISSION_CHILDREN_KEYS = ("state", "problem", "caveat", "record_count",
                         "other_parent_count")

# -- the Mission State projection ---------------------------------------

MISSION_STATE_KEYS = ("state", "problem", "detail", "report", "inputs")
MISSION_STATE_OBSERVED = "observed"
MISSION_STATE_UNAVAILABLE = "unavailable"
MISSION_STATE_NOT_APPLICABLE = "not_applicable"
MISSION_STATE_STATES = (MISSION_STATE_OBSERVED, MISSION_STATE_UNAVAILABLE,
                        MISSION_STATE_NOT_APPLICABLE)
INPUTS_KEYS = ("cursor", "kinds", "candidate_applicability", "baseline")
CURSOR_SAMPLED = "sampled_before_collection"
CURSOR_NOT_SAMPLED = "not_sampled"
CURSOR_STATES = (CURSOR_SAMPLED, CURSOR_NOT_SAMPLED)
BASELINE_UNOBSERVED = "unobserved"
REPORT_KINDS = ("candidate", "delivery", "review", "task")
KIND_INPUT_KEYS = ("supplied", "reason")
SUPPLIED_REPORTED = "reported"
SUPPLIED_UNAVAILABLE = "unavailable"
SUPPLIED_UNKNOWN = "unknown"
SUPPLIED_STATES = (SUPPLIED_REPORTED, SUPPLIED_UNAVAILABLE, SUPPLIED_UNKNOWN)
#: The keys of Mission Core's own observation report
#: (``mission.observation.OBSERVATION_KEYS``; parity pinned by test, since
#: this package does not import ``mission`` outside its reader).
OBSERVATION_REPORT_KEYS = (
    "mission_id", "observed_at", "cursor", "revision", "sequence", "phase",
    "pending_decisions", "progress", "contract", "task", "review",
    "candidate", "proof", "evidence", "artifacts", "blockers",
    "dependencies", "readiness", "budget", "continuation",
    "delivery_receipts", "delivery", "completion", "reconciliation", "time",
    "provenance",
)
PROBLEM_MISSION_STATE_OVER_LIMIT = "mission_state_over_refresh_limit"
PROBLEM_MISSION_STATE_KIND = "source_kind_has_no_mission_state"
PROBLEM_MISSION_STATE_NOT_CONFIGURED = "mission_state_not_configured"
PROBLEM_MISSION_STATE_SOURCE = "mission_state_source_unavailable"
PROBLEM_MISSION_STATE_REFUSED = "mission_state_observation_refused"
PROBLEM_MISSION_STATE_RETURN = "mission_state_composer_return_invalid"
#: Round 3, B2: the shared refresh read budget could not cover load 2.
PROBLEM_MISSION_STATE_BUDGET = "mission_state_refresh_read_budget_exhausted"
MISSION_STATE_PROBLEMS = (
    PROBLEM_MISSION_STATE_OVER_LIMIT, PROBLEM_MISSION_STATE_KIND,
    PROBLEM_MISSION_STATE_NOT_CONFIGURED, PROBLEM_MISSION_STATE_SOURCE,
    PROBLEM_MISSION_STATE_REFUSED, PROBLEM_MISSION_STATE_RETURN,
    PROBLEM_MISSION_STATE_BUDGET)

#: Item 2 (completed-Mission artifacts): an OPTIONAL key of an OBSERVED
#: Mission Core ``mission_state`` block, the HISTORICAL execution identity
#: the composer derived from the same record (``target_runtime.mission_state.
#: execution_identity``). ``established`` only for a COMPLETED record with a
#: verified run, intent, receipt task and a PREPARED digest-bound workspace
#: agreeing exactly; ``cursor_sha256`` then binds the journal head AND that
#: identity, and is what a completed Mission's content read must present.
#: Absent: a producer that does not report it (nothing is inferred).
EXECUTION_IDENTITY_KEY = "execution_identity"
EXECUTION_IDENTITY_KEYS = ("state", "problem", "identity_sha256",
                           "cursor_sha256")
EXECUTION_IDENTITY_ESTABLISHED = "established"
EXECUTION_IDENTITY_NOT_ESTABLISHED = "not_established"
EXECUTION_IDENTITY_NOT_APPLICABLE = "not_applicable"
EXECUTION_IDENTITY_STATES = (EXECUTION_IDENTITY_ESTABLISHED,
                             EXECUTION_IDENTITY_NOT_ESTABLISHED,
                             EXECUTION_IDENTITY_NOT_APPLICABLE)
PROBLEM_EXECUTION_NOT_COMPLETED = "execution_mission_not_completed"
PROBLEM_EXECUTION_RUN_ABSENT = "execution_run_absent"
PROBLEM_EXECUTION_NOT_VERIFIED = "execution_run_not_verified"
PROBLEM_EXECUTION_INTENT_ABSENT = "execution_intent_absent"
PROBLEM_EXECUTION_RECEIPT_TASK_ABSENT = "execution_receipt_task_absent"
#: A record (e.g. legacy) with no prepared workspace binding: never upgraded.
PROBLEM_EXECUTION_BINDING_ABSENT = "execution_prepared_binding_absent"
PROBLEM_EXECUTION_BINDING_NOT_PREPARED = "execution_binding_not_prepared"
PROBLEM_EXECUTION_BINDING_DISAGREES = "execution_binding_disagrees"
PROBLEM_EXECUTION_RECORD_SHAPE = "execution_record_shape_invalid"
EXECUTION_IDENTITY_PROBLEMS = (
    PROBLEM_EXECUTION_RUN_ABSENT, PROBLEM_EXECUTION_NOT_VERIFIED,
    PROBLEM_EXECUTION_INTENT_ABSENT, PROBLEM_EXECUTION_RECEIPT_TASK_ABSENT,
    PROBLEM_EXECUTION_BINDING_ABSENT, PROBLEM_EXECUTION_BINDING_NOT_PREPARED,
    PROBLEM_EXECUTION_BINDING_DISAGREES, PROBLEM_EXECUTION_RECORD_SHAPE)
_DIGEST_PATTERN = r"[0-9a-f]{64}"

ADMITTED_ARTIFACTS_KEYS = ("canonical_review",)
MISSION_REVIEW_KEYS = REVIEW_KEYS + ("candidate_applicability",)

CHAIN_OBSERVATION_BLOCK_KEYS = ("health", "problem", "observed_at",
                                "max_age_seconds", "producer", "root_count",
                                "unavailable_count", "over_capacity_count")
PROBLEM_CHAIN_OBSERVATION_ABSENT = "chain_observation_absent"
PROBLEM_CHAIN_OBSERVATION_SHAPE = "chain_observation_shape_invalid"
PROBLEM_CHAIN_OBSERVATION_SCHEMA = "chain_observation_schema_unsupported"


# -- the Grok delivery presentations block (D1; version 2 only) ----------

#: The identity of the ONE explicitly registered source of presentations.
DELIVERY_PRESENTATIONS_SOURCE_ID = "grok_delivery_presentations"
#: ``grok_bot.index.MAX_DELIVERY_RECEIPTS`` (and the reader's own bound),
#: re-declared and pinned equal by test.
MAX_DELIVERY_PRESENTATIONS = 16
DELIVERY_PRESENTATIONS_KEYS = ("state", "problem", "source_id",
                               "delivery_store", "coverage", "total",
                               "entries")
DELIVERY_PRESENTATIONS_NOT_REGISTERED = "not_registered"
DELIVERY_PRESENTATIONS_OBSERVED = "observed"
DELIVERY_PRESENTATIONS_UNAVAILABLE = "unavailable"
DELIVERY_PRESENTATIONS_STATES = (DELIVERY_PRESENTATIONS_NOT_REGISTERED,
                                 DELIVERY_PRESENTATIONS_OBSERVED,
                                 DELIVERY_PRESENTATIONS_UNAVAILABLE)
#: Why the block is unavailable (closed; the reader names no path or content).
DELIVERY_PRESENTATIONS_PROBLEMS = (
    "presentations_directory_missing", "presentations_directory_not_private",
    "presentations_file_absent", "presentations_unreadable",
    "presentations_json_invalid", "presentations_schema_unsupported",
    "presentations_invalid", "delivery_store_unreadable",
    "delivery_store_invalid", "reader_failed")
DELIVERY_STORE_REGISTERED = "registered"
DELIVERY_STORE_NOT_REGISTERED = "not_registered"
DELIVERY_STORE_STATES = (DELIVERY_STORE_REGISTERED,
                         DELIVERY_STORE_NOT_REGISTERED)
#: What the reader covers, stated in every block: the Grok route only, the
#: most recent ``max_entries``, and direct ``present-dots`` proposals are not
#: stored so they cannot appear.
DELIVERY_COVERAGE_KEYS = ("route", "max_entries", "direct_cli")
DELIVERY_COVERAGE_ROUTE = "grok_bot"
DELIVERY_COVERAGE_DIRECT_CLI = "not_stored"
DELIVERY_PRESENTATION_ENTRY_KEYS = (
    "source_id", "proposal_digest_sha256", "presented_at", "expires_at",
    "mode", "repository_url", "binding_mission", "state",
    "matching_deliveries", "link")
DELIVERY_MODES = ("pull_request", "pr_update")
DELIVERY_BINDING_MISSION_KEYS = ("workflow_id", "authorization_digest_sha256")
DELIVERY_STATE_AWAITING = "awaiting_no_recorded_authorization"
DELIVERY_STATE_AUTHORIZED = "authorized"
DELIVERY_STATE_EXPIRED = "expired_without_authorization"
DELIVERY_STATE_UNKNOWN = "unknown"
DELIVERY_ENTRY_STATES = (DELIVERY_STATE_AWAITING, DELIVERY_STATE_AUTHORIZED,
                         DELIVERY_STATE_EXPIRED, DELIVERY_STATE_UNKNOWN)
DELIVERY_LINK_KEYS = ("state", "mission_ref", "authorization_digest")
LINK_LINKED = "linked"
LINK_UNLINKED_NO_MISSION_BINDING = "unlinked_no_mission_binding"
LINK_UNLINKED_NO_REGISTERED_MISSION = "unlinked_no_registered_mission"
LINK_UNLINKED_AMBIGUOUS = "unlinked_ambiguous"
#: The Mission side could not be (fully) inspected, so "no registered Mission"
#: is NOT known: the registry failed to load, or a workflow-store source was
#: not read fresh this refresh. Never reported as "no match".
LINK_UNLINKED_REGISTRY_UNAVAILABLE = "unlinked_registry_unavailable"
LINK_UNLINKED_SOURCES_INCOMPLETE = "unlinked_sources_incomplete"
DELIVERY_LINK_STATES = (LINK_LINKED, LINK_UNLINKED_NO_MISSION_BINDING,
                        LINK_UNLINKED_NO_REGISTERED_MISSION,
                        LINK_UNLINKED_AMBIGUOUS,
                        LINK_UNLINKED_REGISTRY_UNAVAILABLE,
                        LINK_UNLINKED_SOURCES_INCOMPLETE)
#: What the Mission side of one refresh could inspect, handed to the
#: presentations hook beside the missions.
INSPECTION_COMPLETE = "complete"
INSPECTION_SOURCES_INCOMPLETE = "sources_incomplete"
INSPECTION_REGISTRY_UNAVAILABLE = "registry_unavailable"
INSPECTIONS = (INSPECTION_COMPLETE, INSPECTION_SOURCES_INCOMPLETE,
               INSPECTION_REGISTRY_UNAVAILABLE)
#: ``corroborated`` / ``differs`` are reserved for a catalog that carries a
#: workflow Mission's authorization digest; this one does not.
AUTHORIZATION_NOT_COMPARABLE = "not_comparable"
AUTHORIZATION_CHECKS = ("corroborated", "differs", AUTHORIZATION_NOT_COMPARABLE)

# -- the Mission's delivery link (version 2) ----------------------------------

#: ``missions[].delivery_link``: which ONE delivery record, if any, the
#: catalog's registered delivery store links to this Mission, DECLARED with the
#: rule that established it, so a consumer never guesses a delivery for a
#: Mission:
#: - only a ``workflow_authority.workflow_phase/2`` Mission can be linked, and
#:   only by a record whose OWN ``mission.workflow_id`` (one of the record's
#:   sealed authority keys) equals its ``native_id``. Delivery records name no
#:   Mission of any other model, so such a Mission (Mission Core included) is
#:   ``unlinked_model_not_recorded``: NOT KNOWN, never "no match";
#: - strict multiplicity: several records naming the workflow, or several
#:   registered workflow Missions holding the id, are ``unlinked_ambiguous``;
#:   none is chosen, and "newest" is never a rule;
#: - a unique match is claimed only when every registered workflow-store
#:   source was read fresh (``unlinked_sources_incomplete`` otherwise);
#: - ``delivery_id`` is set EXACTLY when linked: at most one, of the shape a
#:   delivery ceremony mints, never a list or a count. ``basis`` names the rule
#:   (only ``record_mission_binding``), never an engineering task id or an
#:   attested receipt.
#: A link selects which delivery's status may be read; it authorizes nothing.
#: The catalog's registered store is not shown to be the store any other
#: reader (the office's delivery adapter) uses.
MISSION_LINK_KEYS = ("state", "delivery_id", "basis")
MISSION_LINK_LINKED = "linked"
MISSION_LINK_MODEL_NOT_RECORDED = "unlinked_model_not_recorded"
MISSION_LINK_STORE_NOT_REGISTERED = "unlinked_store_not_registered"
MISSION_LINK_STORE_UNAVAILABLE = "unlinked_store_unavailable"
MISSION_LINK_READER_FAILED = "unlinked_reader_failed"
MISSION_LINK_NO_RECORD = "unlinked_no_record"
MISSION_LINK_AMBIGUOUS = LINK_UNLINKED_AMBIGUOUS
MISSION_LINK_SOURCES_INCOMPLETE = LINK_UNLINKED_SOURCES_INCOMPLETE
MISSION_LINK_ID_NOT_ADDRESSABLE = "unlinked_id_not_addressable"
MISSION_LINK_STATES = (MISSION_LINK_LINKED, MISSION_LINK_MODEL_NOT_RECORDED,
                       MISSION_LINK_STORE_NOT_REGISTERED,
                       MISSION_LINK_STORE_UNAVAILABLE,
                       MISSION_LINK_READER_FAILED, MISSION_LINK_NO_RECORD,
                       MISSION_LINK_AMBIGUOUS,
                       MISSION_LINK_SOURCES_INCOMPLETE,
                       MISSION_LINK_ID_NOT_ADDRESSABLE)
MISSION_LINK_BASIS_RECORD_BINDING = "record_mission_binding"
MISSION_LINK_BASES = (MISSION_LINK_BASIS_RECORD_BINDING,)
#: The delivery id a ceremony mints (``pr_delivery.authorization``'s
#: ``DELIVERY_ID_PREFIX`` and ``DELIVERY_ID_HEX_CHARS``: ``prd-`` and 24
#: lowercase hex; parity pinned by test, since this package does not import
#: ``pr_delivery``). The delivery layer's validator admits any id of its
#: alphabet; only this shape can be addressed by a consumer. Implemented
#: without ``re.compile`` so the mission_sources call guard (which forbids
#: the name ``compile``) stays honest.
_DELIVERY_ID_PREFIX = "prd-"
_DELIVERY_ID_HEX_CHARS = 24


def is_delivery_id(value):
    """Whether ``value`` is a delivery id of the shape a ceremony mints."""
    if type(value) is not str or not value.startswith(_DELIVERY_ID_PREFIX):
        return False
    hex_part = value[len(_DELIVERY_ID_PREFIX):]
    return (
        len(hex_part) == _DELIVERY_ID_HEX_CHARS
        and all(ch in "0123456789abcdef" for ch in hex_part)
    )


def mission_delivery_link(mission, unavailable, bound, holders, inspection):
    """The DECLARED ``delivery_link`` block of one mission view (version 2).

    ``unavailable``: None when the registered delivery store was read and
    every record in it validated, else the unlinked state saying why it was
    not (``MISSION_LINK_STORE_NOT_REGISTERED``, ``…_STORE_UNAVAILABLE`` or
    ``…_READER_FAILED``). ``bound``: ``{workflow id: [delivery ids]}`` of the
    validated records whose own binding names that workflow. ``holders``:
    ``{native id: count}`` of this response's workflow Missions.
    ``inspection``: what the Mission side could inspect (``INSPECTIONS``)."""
    def unlinked(state):
        return {"state": state, "delivery_id": None, "basis": None}
    if mission["lifecycle"]["model"] != contract.LIFECYCLE_MODEL_WORKFLOW_STORE:
        return unlinked(MISSION_LINK_MODEL_NOT_RECORDED)
    if unavailable is not None:
        return unlinked(unavailable)
    found = bound.get(mission["native_id"], [])
    if not found:
        return unlinked(MISSION_LINK_NO_RECORD)
    if len(found) > 1 or holders.get(mission["native_id"], 0) > 1:
        return unlinked(MISSION_LINK_AMBIGUOUS)
    if inspection != INSPECTION_COMPLETE:
        return unlinked(MISSION_LINK_SOURCES_INCOMPLETE)
    if not is_delivery_id(found[0]):
        return unlinked(MISSION_LINK_ID_NOT_ADDRESSABLE)
    return {"state": MISSION_LINK_LINKED, "delivery_id": found[0],
            "basis": MISSION_LINK_BASIS_RECORD_BINDING}


def _validate_mission_delivery_link(value, lifecycle, location):
    _closed(value, MISSION_LINK_KEYS, location)
    state = _member(value["state"], MISSION_LINK_STATES, location + ".state")
    workflow = lifecycle["model"] == contract.LIFECYCLE_MODEL_WORKFLOW_STORE
    if workflow == (state == MISSION_LINK_MODEL_NOT_RECORDED):
        raise _shape("%s: exactly the Missions whose model no delivery record "
                     "names are unlinked_model_not_recorded" % location)
    if state == MISSION_LINK_LINKED:
        if not is_delivery_id(value["delivery_id"]):
            raise _shape("%s: a linked Mission carries one delivery id of "
                         "the shape a ceremony mints" % location)
        _member(value["basis"], MISSION_LINK_BASES, location + ".basis")
        return
    if value["delivery_id"] is not None or value["basis"] is not None:
        raise _shape("%s: an unlinked Mission carries no delivery id and no "
                     "basis" % location)


def delivery_presentations_block(state, problem, delivery_store, total,
                                 entries):
    """One ``delivery_presentations`` block, validated before it is returned."""
    return validate_delivery_presentations({
        "state": state,
        "problem": problem,
        "source_id": DELIVERY_PRESENTATIONS_SOURCE_ID,
        "delivery_store": delivery_store,
        "coverage": {"route": DELIVERY_COVERAGE_ROUTE,
                     "max_entries": MAX_DELIVERY_PRESENTATIONS,
                     "direct_cli": DELIVERY_COVERAGE_DIRECT_CLI},
        "total": total,
        "entries": entries})


def delivery_presentations_not_registered():
    """The block of a process launched without ``--delivery-presentations``."""
    return delivery_presentations_block(
        DELIVERY_PRESENTATIONS_NOT_REGISTERED, None,
        DELIVERY_STORE_NOT_REGISTERED, None, [])


def _hex64(value, location):
    if type(value) is not str or len(value) != 64 or set(value) - set(
            "0123456789abcdef"):
        raise _shape("%s must be a lowercase sha256 hex digest" % location)
    return value


def validate_delivery_presentations(value):
    """Closed, deep validation of one block; raises ``contract.ContractError``."""
    _closed(value, DELIVERY_PRESENTATIONS_KEYS, "delivery_presentations")
    state = _member(value["state"], DELIVERY_PRESENTATIONS_STATES,
                    "delivery_presentations.state")
    problem = _member(value["problem"], DELIVERY_PRESENTATIONS_PROBLEMS,
                      "delivery_presentations.problem", True)
    if (state == DELIVERY_PRESENTATIONS_UNAVAILABLE) != (problem is not None):
        raise _shape("delivery_presentations.problem is set exactly when the"
                     " block is unavailable")
    if value["source_id"] != DELIVERY_PRESENTATIONS_SOURCE_ID:
        raise _shape("delivery_presentations.source_id is not the registered"
                     " source")
    _member(value["delivery_store"], DELIVERY_STORE_STATES,
            "delivery_presentations.delivery_store")
    coverage = _closed(value["coverage"], DELIVERY_COVERAGE_KEYS,
                       "delivery_presentations.coverage")
    if coverage != {"route": DELIVERY_COVERAGE_ROUTE,
                    "max_entries": MAX_DELIVERY_PRESENTATIONS,
                    "direct_cli": DELIVERY_COVERAGE_DIRECT_CLI}:
        raise _shape("delivery_presentations.coverage is not the stated"
                     " coverage")
    total = _int(value["total"], "delivery_presentations.total", True)
    entries = _list(value["entries"], "delivery_presentations.entries",
                    MAX_DELIVERY_PRESENTATIONS)
    if state != DELIVERY_PRESENTATIONS_OBSERVED:
        if total is not None or entries:
            raise _shape("a block that is not observed carries no entries")
    elif total != len(entries):
        raise _shape("delivery_presentations.total must equal the entries")
    digests = []
    for index, entry in enumerate(entries):
        where = "delivery_presentations.entries[%d]" % index
        _closed(entry, DELIVERY_PRESENTATION_ENTRY_KEYS, where)
        if entry["source_id"] != DELIVERY_PRESENTATIONS_SOURCE_ID:
            raise _shape("%s.source_id is not the registered source" % where)
        digests.append(_hex64(entry["proposal_digest_sha256"],
                              where + ".proposal_digest_sha256"))
        presented = _int(entry["presented_at"], where + ".presented_at")
        expires = _int(entry["expires_at"], where + ".expires_at")
        if presented < 0 or expires < 0:
            raise _shape("%s times must not be negative" % where)
        _member(entry["mode"], DELIVERY_MODES, where + ".mode")
        _text(entry["repository_url"], where + ".repository_url", True)
        mission = entry["binding_mission"]
        if mission is not None:
            _closed(mission, DELIVERY_BINDING_MISSION_KEYS,
                    where + ".binding_mission")
            _text(mission["workflow_id"], where + ".binding_mission.workflow_id")
            if mission["authorization_digest_sha256"] is not None:
                _hex64(mission["authorization_digest_sha256"],
                       where + ".binding_mission.authorization_digest_sha256")
        _member(entry["state"], DELIVERY_ENTRY_STATES, where + ".state")
        matching = _int(entry["matching_deliveries"],
                        where + ".matching_deliveries", True)
        if (entry["state"] == DELIVERY_STATE_UNKNOWN) != (matching is None):
            raise _shape("%s.matching_deliveries is unset exactly when the"
                         " state is unknown" % where)
        if entry["state"] == DELIVERY_STATE_AUTHORIZED and not matching:
            raise _shape("%s is authorized without a matching delivery"
                         % where)
        link = _closed(entry["link"], DELIVERY_LINK_KEYS, where + ".link")
        _member(link["state"], DELIVERY_LINK_STATES, where + ".link.state")
        _member(link["authorization_digest"], AUTHORIZATION_CHECKS,
                where + ".link.authorization_digest")
        _text(link["mission_ref"], where + ".link.mission_ref", True)
        if (link["state"] == LINK_LINKED) != (link["mission_ref"] is not None):
            raise _shape("%s.link.mission_ref is set exactly when linked"
                         % where)
    if len(digests) != len(set(digests)):
        raise _shape("delivery_presentations.entries repeat a proposal digest")
    return value


# -- exact-type helpers (refuse, never repair) --------------------------

def _shape(message):
    return contract.ContractError(contract.PROBLEM_SNAPSHOT_SHAPE, message)


def _bool(value, location, optional=False):
    if value is None and optional:
        return None
    if type(value) is not bool:
        raise _shape("%s must be a bool" % location)
    return value


def _member(value, choices, location, optional=False):
    if value is None and optional:
        return None
    if type(value) is not str or value not in choices:
        raise _shape("%s must be one of %s" % (location, list(choices)))
    return value


def _int(value, location, optional=False):
    if value is None and optional:
        return None
    return contract.require_int(value, location)


def _text(value, location, optional=False):
    return contract.require_text(value, location, optional)


def _closed(value, keys, location):
    return contract.require_closed_mapping(value, keys, location)


def _list(value, location, max_items):
    return contract.require_list(value, location, max_items)


# -- the chain observation value ----------------------------------------

def validate_child_record(value, location):
    _closed(value, CHILD_RECORD_KEYS, location)
    _text(value["parent_task_id"], location + ".parent_task_id")
    _text(value["child_task_id"], location + ".child_task_id", True)
    _text(value["repo"], location + ".repo", True)
    _text(value["workspace_id"], location + ".workspace_id", True)
    _bool(value["dependency"], location + ".dependency", True)
    agents = value["declared_agents"]
    if agents is not None:
        for index, agent in enumerate(_list(agents, location
                                            + ".declared_agents",
                                            contract.MAX_TASK_BINDINGS)):
            where = "%s.declared_agents[%d]" % (location, index)
            _closed(agent, DECLARED_AGENT_KEYS, where)
            _text(agent["logical"], where + ".logical")
            _text(agent["agent"], where + ".agent")
    return value


def validate_children(value, location, states=CHILDREN_STATES,
                      problems=CHILDREN_PROBLEMS, with_records=True):
    keys = CHILDREN_KEYS if with_records else MISSION_CHILDREN_KEYS
    _closed(value, keys, location)
    state = _member(value["state"], states, location + ".state")
    if state in (CHILDREN_OBSERVED, CHILDREN_OBSERVED_EMPTY):
        if value["problem"] is not None:
            raise _shape("%s.problem must be null when observed" % location)
        _int(value["record_count"], location + ".record_count")
        _int(value["other_parent_count"], location + ".other_parent_count")
    else:
        _member(value["problem"], problems, location + ".problem")
        _int(value["record_count"], location + ".record_count", True)
        _int(value["other_parent_count"], location + ".other_parent_count",
             True)
    if state == CHILDREN_OBSERVED_EMPTY:
        if value["caveat"] != CAVEAT_WRITER_RESET or value["record_count"]:
            raise _shape("%s: an observed empty carries the writer caveat"
                         " and no record" % location)
    elif value["caveat"] is not None:
        raise _shape("%s.caveat is only for an observed empty" % location)
    if with_records:
        records = _list(value["records"], location + ".records",
                        MAX_CHILD_RECORDS)
        if state != CHILDREN_OBSERVED and records:
            raise _shape("%s carries records while %s" % (location, state))
        if state == CHILDREN_OBSERVED and (
                not records or len(records) != value["record_count"]):
            raise _shape("%s.records must hold every matched record"
                         % location)
        for index, record in enumerate(records):
            validate_child_record(record, "%s.records[%d]" % (location,
                                                               index))
    return value


def validate_review_record(value, location):
    _closed(value, REVIEW_RECORD_KEYS, location)
    number = _int(value["round"], location + ".round")
    if not 1 <= number <= MAX_REVIEW_ROUND:
        raise _shape("%s.round must be in 1..%d" % (location,
                                                    MAX_REVIEW_ROUND))
    for field in ("reviewer_logical", "reviewer_agent"):
        text = _text(value[field], "%s.%s" % (location, field))
        if len(text) > MAX_REVIEWER_NAME_CHARS:
            raise _shape("%s.%s is too long" % (location, field))
    _member(value["protocol_token"], PROTOCOL_TOKENS,
            location + ".protocol_token")
    return value


def validate_review(value, location, states=REVIEW_STATES,
                    problems=REVIEW_PROBLEMS, keys=REVIEW_KEYS):
    """One canonical review block: only an ADMITTED block carries a record,
    a gate, a currency and the integrity label, and its verdict is present
    exactly when the gate parsed; the gate fact and the verdict are never
    collapsed. ``findings`` is an OPTIONAL additive key (absent: an older
    producer): null unless the review is admitted, then a closed findings
    block (``validate_findings``)."""
    has_findings = REVIEW_FINDINGS_KEY in value
    _closed(value, keys + ((REVIEW_FINDINGS_KEY,) if has_findings else ()),
            location)
    state = _member(value["state"], states, location + ".state")
    if has_findings:
        if state == REVIEW_ADMITTED:
            validate_findings(value[REVIEW_FINDINGS_KEY],
                              location + ".findings")
        elif value[REVIEW_FINDINGS_KEY] is not None:
            raise _shape("%s.findings must be null when %s"
                         % (location, state))
    if state == REVIEW_ADMITTED:
        if value["problem"] is not None:
            raise _shape("%s.problem must be null when admitted" % location)
        record = validate_review_record(value["record"], location
                                        + ".record")
        gate = _member(value["gate"], GATES, location + ".gate")
        token = record["protocol_token"]
        if (gate == GATE_PARSED) != (token in VERDICTS):
            raise _shape("%s.gate disagrees with the token" % location)
        if gate == GATE_PARSED:
            if value["verdict"] != token:
                raise _shape("%s.verdict must be the parsed token"
                             % location)
        elif value["verdict"] is not None:
            raise _shape("%s: an unparsed gate has no verdict" % location)
        _member(value["currency"], CURRENCIES, location + ".currency")
        _text(value["task_status"], location + ".task_status", True)
        if (value["currency"] == CURRENCY_CURRENT) != (
                value["task_status"] == REVIEW_CURRENT_TASK_STATUS):
            raise _shape("%s.currency must follow the task status"
                         % location)
        if value["integrity"] != INTEGRITY_LINKAGE_CORROBORATED:
            raise _shape("%s.integrity must be %s" % (
                location, INTEGRITY_LINKAGE_CORROBORATED))
    else:
        for field in ("record", "gate", "verdict", "integrity"):
            if value[field] is not None:
                raise _shape("%s.%s must be null when %s"
                             % (location, field, state))
        if state == REVIEW_NOT_RECORDED:
            if value["problem"] is not None:
                raise _shape("%s.problem must be null when not recorded"
                             % location)
            _member(value["currency"], CURRENCIES, location + ".currency")
            _text(value["task_status"], location + ".task_status", True)
        else:
            _member(value["problem"], problems, location + ".problem")
            if value["currency"] is not None:
                raise _shape("%s.currency must be null when %s"
                             % (location, state))
            _text(value["task_status"], location + ".task_status", True)
    return value


def validate_findings(value, location):
    """One findings block of an ADMITTED review (see FINDINGS_KEYS): the four
    states kept apart; only EMPTY and PRESENT carry a candidate, linkage and
    provenance, and only PRESENT carries items."""
    _closed(value, FINDINGS_KEYS, location)
    state = _member(value["state"], FINDINGS_STATES, location + ".state")
    if state in (FINDINGS_NOT_RECORDED, FINDINGS_REFUSED):
        allowed = ((PROBLEM_FINDINGS_LEGACY,) + FINDINGS_NOT_RECORDED_PROBLEMS
                   if state == FINDINGS_NOT_RECORDED
                   else FINDINGS_REFUSED_PROBLEMS)
        _member(value["problem"], allowed, location + ".problem")
        for field in ("count", "candidate_sha256", "routed_at", "linkage",
                      "provenance"):
            if value[field] is not None:
                raise _shape("%s.%s must be null when %s"
                             % (location, field, state))
        if value["items"] != []:
            raise _shape("%s.items must be empty when %s" % (location, state))
        return value
    if value["problem"] is not None:
        raise _shape("%s.problem must be null when %s" % (location, state))
    items = _list(value["items"], location + ".items", MAX_FINDINGS)
    count = _int(value["count"], location + ".count")
    if count != len(items) or (state == FINDINGS_EMPTY) != (count == 0):
        raise _shape("%s.count disagrees with its items and state" % location)
    candidate = _text(value["candidate_sha256"],
                      location + ".candidate_sha256")
    if len(candidate) != 64 or any(c not in "0123456789abcdef"
                                   for c in candidate):
        raise _shape("%s.candidate_sha256 must be 64 hex" % location)
    if _int(value["routed_at"], location + ".routed_at") < 0:
        raise _shape("%s.routed_at must be >= 0" % location)
    if value["linkage"] != FINDINGS_LINKAGE or (
            value["provenance"] != FINDINGS_PROVENANCE):
        raise _shape("%s: linkage and provenance are fixed texts" % location)
    for index, item in enumerate(items, 1):
        where = "%s.items[%d]" % (location, index - 1)
        _closed(item, FINDING_ITEM_KEYS, where)
        if _int(item["index"], where + ".index") != index:
            raise _shape("%s.index must be %d" % (where, index))
        _member(item["severity"], FINDINGS_SEVERITIES, where + ".severity")
        area = _text(item["area"], where + ".area", True)
        if area is not None and len(area) > MAX_FINDING_AREA_CHARS:
            raise _shape("%s.area is too long" % where)
        line = _int(item["line"], where + ".line", True)
        if line is not None and (line < 1 or area is None):
            raise _shape("%s.line needs an area and must be >= 1" % where)
        summary = _text(item["summary"], where + ".summary")
        if len(summary) > MAX_FINDING_SUMMARY_CHARS:
            raise _shape("%s.summary is too long" % where)
    return value


_HEX16 = r"[0-9a-f]{16}"
_HEX64 = r"[0-9a-f]{64}"
_FINDINGS_NAME = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
_FINDINGS_TASK = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
_FINDINGS_AREA = r"[A-Za-z0-9_][A-Za-z0-9._/-]*"
_FINDINGS_REFUSED_CHARACTERS = frozenset(
    [chr(c) for c in range(0x00, 0x20)] + [chr(0x7F)]
    + [chr(c) for c in range(0x80, 0xA0)]
    + [chr(c) for c in range(0x202A, 0x202F)]
    + [chr(c) for c in range(0x2066, 0x206A)])


def _finding_record_ok(finding, index):
    if type(finding) is not dict or set(finding) != set(FINDING_ITEM_KEYS):
        return False
    area, line, summary = finding["area"], finding["line"], finding["summary"]
    return (type(finding["index"]) is int and finding["index"] == index
            and finding["severity"] in FINDINGS_SEVERITIES
            and (area is None or (type(area) is str
                                  and len(area) <= MAX_FINDING_AREA_CHARS
                                  and re.fullmatch(_FINDINGS_AREA, area)
                                  is not None
                                  and ".." not in area.split("/")
                                  and "" not in area.split("/")))
            and (line is None or (type(line) is int and area is not None
                                  and 1 <= line <= MAX_FINDING_LINE_NUMBER))
            and type(summary) is str and summary
            and len(summary) <= MAX_FINDING_SUMMARY_CHARS
            and summary == summary.strip()
            and not any(c in _FINDINGS_REFUSED_CHARACTERS for c in summary))


def validate_findings_record(record):
    """``record`` when it is EXACTLY the persisted findings record; raises
    ``ContractError`` otherwise (never repairs)."""
    def refuse(why):
        raise _shape("findings record: %s" % why)
    if type(record) is not dict or set(record) != set(FINDINGS_RECORD_KEYS):
        refuse("keys")
    if record["schema"] != FINDINGS_RECORD_SCHEMA or (
            record["version"] != FINDINGS_RECORD_VERSION):
        refuse("schema")
    for name, pattern in (("task_id", _FINDINGS_TASK),
                          ("reviewer_logical", _FINDINGS_NAME),
                          ("reviewer_agent", _FINDINGS_NAME),
                          ("candidate_sha256", _HEX64),
                          ("route_nonce", _HEX16),
                          ("preamble_sha256", _HEX64)):
        if type(record[name]) is not str or re.fullmatch(
                pattern, record[name]) is None:
            refuse(name)
    if type(record["round"]) is not int or record["round"] < 1:
        refuse("round")
    if type(record["routed_at"]) is not int or record["routed_at"] < 0:
        refuse("routed_at")
    if record["linkage"] != FINDINGS_LINKAGE or (
            record["provenance"] != FINDINGS_PROVENANCE):
        refuse("linkage or provenance")
    findings = record["findings"]
    if type(findings) is not list or len(findings) > MAX_FINDINGS or (
            type(record["count"]) is not int) or record["count"] != len(
                findings):
        refuse("count")
    for index, finding in enumerate(findings, 1):
        if not _finding_record_ok(finding, index):
            refuse("finding %d" % index)
    if sum(len(f["summary"].encode("utf-8"))
           + len((f["area"] or "").encode("utf-8"))
           for f in findings) > MAX_FINDINGS_AGGREGATE_BYTES:
        refuse("aggregate")
    return record


def _root_task(value, location):
    if value is None:
        return None
    _closed(value, ROOT_TASK_KEYS, location)
    _text(value["task_id"], location + ".task_id")
    _text(value["status"], location + ".status", True)
    _bool(value["membership_ended"], location + ".membership_ended")
    _bool(value["dependency_complete"], location + ".dependency_complete")
    return value


def _root_entry(value, location):
    _closed(value, ROOT_KEYS, location)
    _text(value["root"], location + ".root")
    _member(value["tier"], TIERS, location + ".tier")
    _root_task(value["task"], location + ".task")
    validate_children(value["children"], location + ".children")
    validate_review(value["review"], location + ".review")
    return value


@dataclass(frozen=True)
class ChainReading(object):
    """The validated reading of one injected chain observation. ``roots``
    maps a captured root to its validated entry; populated only when the
    value was well-formed and compatible (``fresh`` or ``stale``)."""
    health: str
    problem: Optional[str]
    observed_at: Optional[int]
    max_age_seconds: int
    producer: Optional[str]
    roots: Tuple[Tuple[str, dict], ...]
    unavailable: Tuple[dict, ...]
    over_capacity: Tuple[dict, ...]

    def is_live(self):
        return self.health == contract.HEALTH_FRESH

    def root(self, path):
        for root, entry in self.roots:
            if root == path:
                return entry
        return None

    def root_observation(self, path):
        """``(observation, problem)`` of one root path in this reading."""
        if self.root(path) is not None:
            return ROOT_OBSERVED, None
        for item in self.unavailable:
            if item["root"] == path:
                return ROOT_UNAVAILABLE, item["problem"]
        for item in self.over_capacity:
            if item["root"] == path:
                return ROOT_OVER_CAPACITY, None
        return ROOT_NOT_CAPTURED, None

    def describe(self):
        return {"health": self.health, "problem": self.problem,
                "observed_at": self.observed_at,
                "max_age_seconds": self.max_age_seconds,
                "producer": self.producer, "root_count": len(self.roots),
                "unavailable_count": len(self.unavailable),
                "over_capacity_count": len(self.over_capacity)}


def _degraded_chains(health, problem, max_age_seconds, observed_at=None,
                     producer=None):
    return ChainReading(health, problem, observed_at, max_age_seconds,
                        producer, (), (), ())


def read_chain_observation(value, now,
                           max_age_seconds=contract.DEFAULT_MAX_AGE_SECONDS):
    """Validate one injected chain observation; never raises for bad input
    (``contract.read_runtime_snapshot``'s rules: None is ``unavailable``,
    another schema or version ``incompatible``, any shape violation
    ``malformed``, a future ``observed_at`` ``malformed``)."""
    contract.require_int(now, "now")
    contract.require_max_age(max_age_seconds, "max_age_seconds")
    if value is None:
        return _degraded_chains(contract.HEALTH_UNAVAILABLE,
                                PROBLEM_CHAIN_OBSERVATION_ABSENT,
                                max_age_seconds)
    if type(value) is not dict:
        return _degraded_chains(contract.HEALTH_MALFORMED,
                                PROBLEM_CHAIN_OBSERVATION_SHAPE,
                                max_age_seconds)
    if value.get("schema") != CHAIN_OBSERVATION_SCHEMA or type(
            value.get("version")) is not int or value.get(
                "version") != CHAIN_OBSERVATION_VERSION:
        return _degraded_chains(contract.HEALTH_INCOMPATIBLE,
                                PROBLEM_CHAIN_OBSERVATION_SCHEMA,
                                max_age_seconds)
    try:
        _closed(value, CHAIN_OBSERVATION_KEYS, "chain_observation")
        observed_at = contract.require_int(value["observed_at"],
                                           "chain_observation.observed_at")
        producer = _text(value["producer"], "chain_observation.producer")
        roots = []
        seen = set()
        for index, entry in enumerate(_list(
                value["roots"], "chain_observation.roots",
                MAX_HERD_ROOTS_V2)):
            where = "chain_observation.roots[%d]" % index
            _root_entry(entry, where)
            if entry["root"] in seen:
                raise _shape("%s.root is listed twice" % where)
            seen.add(entry["root"])
            roots.append((entry["root"], entry))
        unavailable = []
        for index, entry in enumerate(_list(
                value["unavailable"], "chain_observation.unavailable",
                MAX_HERD_ROOTS_V2)):
            where = "chain_observation.unavailable[%d]" % index
            _closed(entry, UNAVAILABLE_ROOT_KEYS, where)
            _text(entry["root"], where + ".root")
            _member(entry["tier"], TIERS, where + ".tier")
            _text(entry["problem"], where + ".problem")
            _text(entry["detail"], where + ".detail", True)
            unavailable.append(entry)
        over = []
        for index, entry in enumerate(_list(
                value["over_capacity"], "chain_observation.over_capacity",
                MAX_HERD_ROOTS_V2 * MAX_CHILD_RECORDS)):
            where = "chain_observation.over_capacity[%d]" % index
            _closed(entry, OVER_CAPACITY_ROOT_KEYS, where)
            _text(entry["root"], where + ".root")
            _member(entry["tier"], TIERS, where + ".tier")
            over.append(entry)
    except contract.ContractError as exc:
        return _degraded_chains(contract.HEALTH_MALFORMED, exc.problem,
                                max_age_seconds)
    health, problem = contract.freshness(observed_at, now, max_age_seconds)
    if health == contract.HEALTH_MALFORMED:
        return _degraded_chains(health, problem, max_age_seconds,
                                observed_at, producer)
    return ChainReading(health, problem, observed_at, max_age_seconds,
                        producer, tuple(roots), tuple(unavailable),
                        tuple(over))



# -- response envelope and the version 2 consumer operation ---------------

def envelope_v2(generated_at, max_age_seconds, valid_until, limits, registry,
                sources, runtime_snapshot, chain_observation, missions,
                unmatched_runtime_agent_count, runtime_agents=None,
                delivery_presentations=None):
    """Assemble one version 2 response (``contract.envelope``'s rules, plus
    the chain observation block; ``runtime_agents`` as in version 1)."""
    contract.require_int(generated_at, "generated_at")
    contract.require_max_age(max_age_seconds, "max_age_seconds")
    contract.require_int(valid_until, "valid_until")
    if not generated_at <= valid_until <= generated_at + max_age_seconds:
        raise contract.ContractError(
            contract.PROBLEM_RESPONSE_SHAPE,
            "valid_until must lie within the envelope bound")
    return {
        "contract": contract.QUERY_CONTRACT,
        "contract_version": QUERY_CONTRACT_VERSION_V2,
        "generated_at": generated_at,
        "max_age_seconds": max_age_seconds,
        "valid_until": valid_until,
        "limits": limits,
        "registry": registry,
        "sources": sources,
        "runtime_snapshot": runtime_snapshot,
        "chain_observation": chain_observation,
        "missions": missions,
        "unmatched_runtime_agent_count": unmatched_runtime_agent_count,
        "runtime_agents": contract.validate_runtime_agents(
            runtime_agents if runtime_agents is not None
            else contract.runtime_agents_block(
                contract.RUNTIME_AGENTS_NOT_APPLICABLE,
                contract.PROBLEM_NO_TASK_NATIVE_SOURCE)),
        "delivery_presentations": (
            delivery_presentations if delivery_presentations is not None
            else delivery_presentations_not_registered()),
    }


def response_freshness_v2(response, now):
    """THE version 2 consumer operation, with ``contract.response_freshness``'s
    rule unchanged except the version and the key set: ``incompatible`` for
    any other contract or version (a version 1 response included),
    ``malformed`` for a wrong shape, the registry's own health when it was
    degraded, ``stale`` past ``valid_until``, else ``fresh``."""
    contract.require_int(now, "now")
    if type(response) is not dict:
        return contract.HEALTH_MALFORMED, contract.PROBLEM_RESPONSE_SHAPE
    if response.get("contract") != contract.QUERY_CONTRACT or type(
            response.get("contract_version")) is not int or response.get(
                "contract_version") != QUERY_CONTRACT_VERSION_V2:
        return (contract.HEALTH_INCOMPATIBLE,
                contract.PROBLEM_RESPONSE_CONTRACT)
    if set(response) != set(RESPONSE_KEYS_V2):
        return contract.HEALTH_MALFORMED, contract.PROBLEM_RESPONSE_SHAPE
    max_age = response.get("max_age_seconds")
    if type(max_age) is not int or not (
            contract.MIN_MAX_AGE_SECONDS <= max_age
            <= contract.MAX_MAX_AGE_SECONDS):
        return contract.HEALTH_MALFORMED, contract.PROBLEM_RESPONSE_SHAPE
    registry = response.get("registry")
    if type(registry) is not dict or registry.get(
            "health") not in contract.HEALTH_STATES:
        return contract.HEALTH_MALFORMED, contract.PROBLEM_RESPONSE_SHAPE
    if registry["health"] != contract.HEALTH_FRESH:
        return registry["health"], contract.PROBLEM_REGISTRY_DEGRADED
    generated_at = response.get("generated_at")
    valid_until = response.get("valid_until")
    if type(generated_at) is not int or type(valid_until) is not int or \
            not generated_at <= valid_until <= generated_at + max_age:
        return contract.HEALTH_MALFORMED, contract.PROBLEM_RESPONSE_SHAPE
    if generated_at > now:
        return contract.HEALTH_MALFORMED, contract.PROBLEM_OBSERVED_IN_FUTURE
    if now > valid_until:
        return contract.HEALTH_STALE, contract.PROBLEM_RESPONSE_EXPIRED
    return contract.HEALTH_FRESH, contract.PROBLEM_NONE


#: The serve-time freshness operation of each launchable version.
FRESHNESS_BY_VERSION = {
    contract.QUERY_CONTRACT_VERSION: contract.response_freshness,
    QUERY_CONTRACT_VERSION_V2: response_freshness_v2,
}


# -- deep validation of a whole version 2 response ------------------------

def _validate_binding(value, location):
    _closed(value, BINDING_KEYS, location)
    _member(value["state"], contract.BINDING_STATES, location + ".state")
    _text(value["reason"], location + ".reason")
    _text(value["task_id"], location + ".task_id", True)


def _validate_member(value, mission_ref, location):
    _closed(value, MEMBER_KEYS_V2, location)
    if value["mission_ref"] != mission_ref:
        raise _shape("%s.mission_ref is not its mission's" % location)
    task_id = _text(value["task_id"], location + ".task_id")
    logical = _text(value["logical"], location + ".logical")
    if value["member_ref"] != "%s#%s#%s" % (mission_ref, task_id, logical):
        raise _shape("%s.member_ref must be task-qualified" % location)
    _member(value["chain"], CHAINS, location + ".chain")
    if value["chain_task_id"] != task_id:
        raise _shape("%s.chain_task_id must be the member's task" % location)
    _member(value["classification"], contract.MEMBER_CLASSIFICATIONS,
            location + ".classification")
    _member(value["activity"], contract.ACTIVITIES, location + ".activity")
    _closed(value["session"], MEMBER_SESSION_KEYS, location + ".session")
    _text(value["agent"], location + ".agent")
    _text(value["reason"], location + ".reason")
    _int(value["observed_at"], location + ".observed_at")
    for fact in contract.UNREPORTED_FACTS:
        if value[fact] != contract.UNKNOWN:
            raise _shape("%s.%s must stay unknown" % (location, fact))


def _validate_chain(value, location):
    _closed(value, TASK_CHAIN_KEYS, location)
    chain = _member(value["chain"], CHAINS, location + ".chain")
    _text(value["chain_task_id"], location + ".chain_task_id", True)
    _text(value["parent_task_id"], location + ".parent_task_id", True)
    _closed(value["root"], CHAIN_ROOT_KEYS, location + ".root")
    _text(value["root"]["path"], location + ".root.path", True)
    _member(value["root"]["observation"], ROOT_OBSERVATIONS,
            location + ".root.observation")
    _text(value["root"]["problem"], location + ".root.problem", True)
    _validate_binding(value["binding"], location + ".binding")
    end = _closed(value["membership_end"], MEMBERSHIP_END_KEYS,
                  location + ".membership_end")
    _bool(end["ended"], location + ".membership_end.ended", True)
    _text(end["status"], location + ".membership_end.status", True)
    if end["rule"] != MEMBERSHIP_RULE:
        raise _shape("%s.membership_end.rule" % location)
    _int(value["descendants_not_followed"],
         location + ".descendants_not_followed", True)
    if chain == CHAIN_DIRECT:
        if value["dependency"] is not None or value["record"] is not None:
            raise _shape("%s: the direct chain has no dependency or record"
                         % location)
        return
    dependency = _closed(value["dependency"], DEPENDENCY_KEYS,
                         location + ".dependency")
    _bool(dependency["recorded"], location + ".dependency.recorded", True)
    _member(dependency["resolution"], DEPENDENCY_RESOLUTIONS,
            location + ".dependency.resolution")
    if dependency["rule"] != DEPENDENCY_RULE:
        raise _shape("%s.dependency.rule" % location)
    record = _closed(value["record"], CHAIN_RECORD_KEYS, location + ".record")
    _text(record["child_task_id"], location + ".record.child_task_id", True)
    _text(record["repo"], location + ".record.repo", True)
    _text(record["workspace_id"], location + ".record.workspace_id", True)


def _validate_execution_identity(value, location):
    _closed(value, EXECUTION_IDENTITY_KEYS, location)
    state = _member(value["state"], EXECUTION_IDENTITY_STATES,
                    location + ".state")
    if state == EXECUTION_IDENTITY_ESTABLISHED:
        if value["problem"] is not None or not all(
                type(value[key]) is str
                and re.fullmatch(_DIGEST_PATTERN, value[key]) is not None
                for key in ("identity_sha256", "cursor_sha256")):
            raise _shape("%s: an established identity has two digests and"
                         " no problem" % location)
        return
    if value["identity_sha256"] is not None or (
            value["cursor_sha256"] is not None):
        raise _shape("%s: only an established identity has digests"
                     % location)
    if state == EXECUTION_IDENTITY_NOT_APPLICABLE:
        if value["problem"] != PROBLEM_EXECUTION_NOT_COMPLETED:
            raise _shape("%s.problem" % location)
        return
    _member(value["problem"], EXECUTION_IDENTITY_PROBLEMS,
            location + ".problem")


def _validate_mission_state(value, location):
    optional = (EXECUTION_IDENTITY_KEY,) if (
        type(value) is dict and EXECUTION_IDENTITY_KEY in value) else ()
    _closed(value, MISSION_STATE_KEYS + optional, location)
    state = _member(value["state"], MISSION_STATE_STATES, location + ".state")
    if optional and state != MISSION_STATE_OBSERVED:
        raise _shape("%s carries an execution identity only when observed"
                     % location)
    if optional:
        _validate_execution_identity(value[EXECUTION_IDENTITY_KEY],
                                     location + "." + EXECUTION_IDENTITY_KEY)
    if state == MISSION_STATE_OBSERVED:
        if value["problem"] is not None:
            raise _shape("%s.problem must be null when observed" % location)
        _closed(value["report"], OBSERVATION_REPORT_KEYS,
                location + ".report")
        inputs = _closed(value["inputs"], INPUTS_KEYS, location + ".inputs")
        _member(inputs["cursor"], CURSOR_STATES, location + ".inputs.cursor")
        kinds = _closed(inputs["kinds"], REPORT_KINDS,
                        location + ".inputs.kinds")
        for kind in REPORT_KINDS:
            where = "%s.inputs.kinds.%s" % (location, kind)
            entry = _closed(kinds[kind], KIND_INPUT_KEYS, where)
            supplied = _member(entry["supplied"], SUPPLIED_STATES,
                               where + ".supplied")
            _text(entry["reason"], where + ".reason",
                  supplied != SUPPLIED_UNAVAILABLE)
        if inputs["candidate_applicability"] != (
                CANDIDATE_APPLICABILITY_UNPROVEN):
            raise _shape("%s.inputs.candidate_applicability" % location)
        if inputs["baseline"] != BASELINE_UNOBSERVED:
            raise _shape("%s.inputs.baseline" % location)
        return
    _member(value["problem"], MISSION_STATE_PROBLEMS, location + ".problem")
    _text(value["detail"], location + ".detail", True)
    if value["report"] is not None or value["inputs"] is not None:
        raise _shape("%s carries no report or inputs when %s"
                     % (location, state))


def workflow_phase(mission):
    """The DECLARED ``workflow_phase`` block of one mission view (version 2).
    A ``workflow_authority.workflow_phase/2`` view carries its record's own
    ``phase`` (the workflow reader places it in ``lifecycle.state``,
    ``sources._workflow_view``); it is declared here as a typed field of the
    closed ``WORKFLOW_PHASES``. Every other model has no workflow phase:
    ``not_applicable``, never one. A phase outside the vocabulary refuses."""
    lifecycle = mission["lifecycle"]
    if lifecycle["model"] != contract.LIFECYCLE_MODEL_WORKFLOW_STORE:
        return {"state": WORKFLOW_PHASE_NOT_APPLICABLE,
                "problem": PROBLEM_WORKFLOW_PHASE_MODEL, "phase": None}
    if lifecycle["state"] not in WORKFLOW_PHASES:
        raise _shape("%s: workflow phase outside the declared vocabulary"
                     % mission["mission_ref"])
    return {"state": WORKFLOW_PHASE_OBSERVED, "problem": None,
            "phase": lifecycle["state"]}


def _validate_workflow_phase(value, lifecycle, location):
    _closed(value, WORKFLOW_PHASE_KEYS, location)
    _member(value["state"], WORKFLOW_PHASE_STATES, location + ".state")
    workflow = lifecycle["model"] == contract.LIFECYCLE_MODEL_WORKFLOW_STORE
    if value["state"] == WORKFLOW_PHASE_OBSERVED:
        if not workflow:
            raise _shape("%s: only a workflow_authority.workflow_phase/2 "
                         "record has a workflow phase" % location)
        _member(value["phase"], WORKFLOW_PHASES, location + ".phase")
        if value["phase"] != lifecycle["state"] or value["problem"] is not None:
            raise _shape("%s: the declared phase is the record's own phase, "
                         "with no problem" % location)
        return
    if (workflow or value["phase"] is not None
            or value["problem"] != PROBLEM_WORKFLOW_PHASE_MODEL):
        raise _shape("%s: only a model without a workflow phase is "
                     "not_applicable, with its problem and no phase"
                     % location)


def validate_mission_v2(value, location):
    _closed(value, MISSION_KEYS_V2, location)
    mission_ref = _text(value["mission_ref"], location + ".mission_ref")
    _closed(value["lifecycle"], LIFECYCLE_KEYS, location + ".lifecycle")
    _member(value["lifecycle"]["standing"], contract.STANDINGS,
            location + ".lifecycle.standing")
    _validate_workflow_phase(value["workflow_phase"], value["lifecycle"],
                             location + ".workflow_phase")
    _validate_mission_delivery_link(value["delivery_link"], value["lifecycle"],
                                    location + ".delivery_link")
    _closed(value["execution"], EXECUTION_KEYS, location + ".execution")
    _validate_binding(value["binding"], location + ".binding")
    for index, member in enumerate(_list(
            value["members"], location + ".members",
            contract.MAX_TASK_BINDINGS * (1 + MAX_CHILD_RECORDS))):
        _validate_member(member, mission_ref,
                         "%s.members[%d]" % (location, index))
    refs = [m["member_ref"] for m in value["members"]]
    if len(refs) != len(set(refs)):
        raise _shape("%s.members repeat a member_ref" % location)
    chains = _list(value["task_chains"], location + ".task_chains",
                   1 + MAX_CHILD_RECORDS)
    if not chains or chains[0].get("chain") != CHAIN_DIRECT or any(
            c.get("chain") != CHAIN_CHILD for c in chains[1:]):
        raise _shape("%s.task_chains: the direct chain first, then children"
                     % location)
    for index, chain in enumerate(chains):
        _validate_chain(chain, "%s.task_chains[%d]" % (location, index))
    validate_children(value["children_record"],
                      location + ".children_record",
                      MISSION_CHILDREN_STATES, MISSION_CHILDREN_PROBLEMS,
                      with_records=False)
    _validate_mission_state(value["mission_state"],
                            location + ".mission_state")
    artifacts = _closed(value["admitted_artifacts"], ADMITTED_ARTIFACTS_KEYS,
                        location + ".admitted_artifacts")
    review = validate_review(artifacts["canonical_review"],
                             location + ".admitted_artifacts"
                             ".canonical_review", MISSION_REVIEW_STATES,
                             MISSION_REVIEW_PROBLEMS, MISSION_REVIEW_KEYS)
    if review["candidate_applicability"] != CANDIDATE_APPLICABILITY_UNPROVEN:
        raise _shape("%s: candidate applicability is always unproven"
                     % location)
    return value


def validate_response_v2(response):
    """Deep, closed-shape validation of one whole version 2 response; raises
    ``contract.ContractError``. The envelope rule is ``response_freshness_v2``
    (judged here at the response's own ``generated_at``); every block
    version 2 adds is checked key by key."""
    if type(response) is not dict:
        raise _shape("a response must be an object")
    generated_at = response.get("generated_at")
    if type(generated_at) is not int:
        raise _shape("generated_at must be an int")
    _health, problem = response_freshness_v2(response, generated_at)
    if problem in (contract.PROBLEM_RESPONSE_SHAPE,
                   contract.PROBLEM_RESPONSE_CONTRACT,
                   contract.PROBLEM_OBSERVED_IN_FUTURE):
        # A degraded registry is a valid response; a wrong envelope is not.
        raise contract.ContractError(problem,
                                     "the version 2 envelope is invalid")
    _closed(response["chain_observation"], CHAIN_OBSERVATION_BLOCK_KEYS,
            "chain_observation")
    _member(response["chain_observation"]["health"], contract.HEALTH_STATES,
            "chain_observation.health")
    _closed(response["runtime_snapshot"], (
        "health", "problem", "observed_at", "max_age_seconds", "producer",
        "task_count", "agent_count"), "runtime_snapshot")
    if type(response["limits"]) is not dict:
        raise _shape("limits must be an object")
    missions = _list(response["missions"], "missions",
                     MAX_RESPONSE_MISSIONS)
    refs = []
    for index, mission in enumerate(missions):
        validate_mission_v2(mission, "missions[%d]" % index)
        refs.append(mission["mission_ref"])
    if len(refs) != len(set(refs)):
        raise _shape("missions repeat a mission_ref")
    _int(response["unmatched_runtime_agent_count"],
         "unmatched_runtime_agent_count", True)
    contract.validate_runtime_agents(response["runtime_agents"])
    validate_delivery_presentations(response["delivery_presentations"])
    return response
