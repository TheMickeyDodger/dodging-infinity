"""The verification-evidence projection (DI-REMOTE-2 gap A, I1).

``collect_verification_evidence`` assembles, from the PROTECTED
workflow record and injected read-only seams, the complete binding
set a verification decision must rest on — so that Herdr lifecycle
status alone can never again be the only structural evidence behind
a verified mission result. This module only COLLECTS and VALIDATES:
wiring the projection into the Broker's ``verified_result`` gates
and into the fresh verification turn's prompt are separate,
later increments.

Authority and containment posture:

- Every value is resolved from the protected record (``entry``) or
  read through an injected seam (the git transport, the read-only
  Herdr observer) on paths resolved from the record. NO caller
  parameter can supply a path, URL, baseline, or command.
- Every target file read goes through the ONE hardened read
  primitive (``target_runtime.prepare.read_workspace_instruction``)
  — reused, never cloned. This module adds only a containment check
  for the fixed ``.herd/state`` artifact directories the primitive's
  separator-free allowlist could not express: the directory chain is
  required to resolve to itself (no symlinked component) BEFORE and
  AFTER the read, and the read itself is still the primitive.
  STANDING CONSTRAINT (lead ruling, task 20260826-113247 I1): the
  re-check after the read discards content on any chain change, and
  the residual double-swap race (a component swapped out AND back
  entirely between the two checks, requiring an active hostile
  process inside a lease whose task has already STOPPED, against
  content that is subordinate evidence unable to satisfy any
  structural gate) is ACCEPTED AND DISCLOSED — it is deliberately
  NOT closed, because closing it needs an openat-based directory
  walk, a second hardened read primitive, reopening the structural
  class this repository already closed ("path-following reads ->
  one hardened primitive"). Do not add a second read path.
- Observation support is SOURCE-SCOPED (supervisor ruling R-6): an
  observation supports a decision iff no diagnostic whose ``source``
  is in that DECISION'S consumed-source set carries a
  completeness-demoting state. The production observer runs with
  ``probe_agents=False``, and a dispatched target always lists
  agents, so the RAW global completeness of a production
  observation is EXPECTED to be ``PARTIAL`` (an ``agents``
  ``unavailable`` diagnostic — a defect of the prior accepted task
  20260826-022933's global gate, which could never pass in
  production). That agents-unprobed PARTIAL weakens none of the
  evidence this layer consumes (``task``/``reviews``/``artifacts``
  plus the projection-failure fallback ``observation``); a demoting
  diagnostic in any CONSUMED source still fails closed exactly as
  before. The raw global completeness value stays recorded
  unaltered in the ``observation`` binding and must never be
  rewritten by the scoped decision; the scoped decision lives in
  its own explicitly-named fields (``supports_verification``,
  ``blocking_sources``). NOTE for renderers: the projection's own
  top-level ``completeness`` is the PROJECTION'S
  (every-binding-exact) state, never herd's observation
  completeness — any human-facing rendering must label the two
  distinctly and must never print COMPLETE for herd's observation
  where herd said PARTIAL.
- Target-authored bytes (checkpoint, reviewer file, marker
  extractions) are SUBORDINATE untrusted data: they are never parsed
  for control tokens here, never feed a transition, and can never
  satisfy or weaken a structural gate. The canonical Reviewer
  DECISION comes only from the observer's canonical
  ``Protocol token:`` parse (``reviews.listed[].decision``), never
  from this module's own text scan.
- Every count, flag, and digest is EXACT or explicitly refused with
  its own status; a lower bound is never emitted under a field name
  that promises an exact count, and a partial digest is never
  emitted at all. Bounds are hard module constants, never derived
  from input, never configurable.

Per-binding status vocabulary (closed; unmapped statuses RAISE in
the renderer map — no silent default):

- ``exact``: the binding holds the exact value(s) it names.
- ``refused_over_bound``: the source exceeds a hard bound; no value.
- ``refused_unreadable``: the source exists but could not be read
  faithfully (I/O failure, non-UTF-8, non-regular file, hardlink,
  containment violation); no value.
- ``refused_absent``: the source is cleanly absent.
- ``refused_incomplete``: the source was visible only through a
  degraded or truncated observation (observer ``PARTIAL``, truncated
  listing, or a prerequisite binding that is itself not exact);
  a partial view is never presented as evidence.
- ``not_produced``: the source was read exactly and the marker is
  genuinely absent (test/mutation evidence only) — an explicit
  honest state, never inferred and never presented as evidence of
  testing.

The projection's own ``completeness`` is ``COMPLETE`` ONLY when
every binding carries an acceptable status: ``exact`` everywhere,
except the two marker bindings where the explicit ``not_produced``
is also acceptable (the mission text itself names "recorded
mutation evidence or explicit not_produced"). Anything else is
``PARTIAL`` with one diagnostic per failing binding.
"""

import os
import re

from workflow_authority import rendering
from workflow_authority.digest import (
    DigestError,
    control_policy_digest,
    framed_digest,
)

from target_runtime import dispatch as dispatch_module
from target_runtime import prepare as prepare_module
from target_runtime.git_transport import (
    CAPTURE_CAPTURED,
    GitTransportError,
    MAX_DIFF_RETAINED_BYTES,
)

EVIDENCE_SCHEMA_VERSION = 1

# -- the closed per-binding status vocabulary ------------------------

BINDING_EXACT = "exact"
BINDING_REFUSED_OVER_BOUND = "refused_over_bound"
BINDING_REFUSED_UNREADABLE = "refused_unreadable"
BINDING_REFUSED_ABSENT = "refused_absent"
BINDING_REFUSED_INCOMPLETE = "refused_incomplete"
BINDING_NOT_PRODUCED = "not_produced"

BINDING_STATUSES = (
    BINDING_EXACT,
    BINDING_REFUSED_OVER_BOUND,
    BINDING_REFUSED_UNREADABLE,
    BINDING_REFUSED_ABSENT,
    BINDING_REFUSED_INCOMPLETE,
    BINDING_NOT_PRODUCED,
)

# The EXPLICIT status -> summary-line map (the
# ``_INSTRUCTION_STATUS_LINES`` discipline): every closed status has
# an entry; an unmapped status RAISES rather than falling through a
# silent default. This is the seed the prompt-rendering increment
# builds on.
_BINDING_STATUS_LINES = {
    BINDING_EXACT: (
        lambda name: "%s: exact" % name
    ),
    BINDING_REFUSED_OVER_BOUND: (
        lambda name: "%s: REFUSED — exceeds a hard bound; no exact"
        " value and no digest is reported" % name
    ),
    BINDING_REFUSED_UNREADABLE: (
        lambda name: "%s: REFUSED — could not be read faithfully;"
        " no value is reported" % name
    ),
    BINDING_REFUSED_ABSENT: (
        lambda name: "%s: REFUSED — the source is absent" % name
    ),
    BINDING_REFUSED_INCOMPLETE: (
        lambda name: "%s: REFUSED — visible only through a degraded"
        " or truncated observation; a partial view is never"
        " evidence" % name
    ),
    BINDING_NOT_PRODUCED: (
        lambda name: "%s: not produced — the marker is absent from"
        " the source (an explicit state, NOT evidence of testing)"
        % name
    ),
}


def binding_status_line(name, binding):
    """The one summary line for a binding's status, from the explicit
    map. An unmapped status raises: a new status added without a
    rendering decision must fail loudly, never describe itself with
    someone else's words."""
    status = binding["status"]
    try:
        line = _BINDING_STATUS_LINES[status]
    except KeyError:
        raise ValueError(
            "no rendered line for binding status %r; every status"
            " must have an explicit entry" % (status,)
        )
    return line(name)


# -- the closed binding set ------------------------------------------

PROJECTION_BINDINGS = (
    "workflow",
    "target",
    "approved_baseline",
    "acceptance",
    "delivery_authority",
    "dispatch",
    "live_origin",
    "live_head",
    "changed_paths",
    "diff",
    "observation",
    "target_task",
    "review_decision",
    "review_file",
    "reviewer_identity",
    "checkpoint",
    "checkpoint_mtime",
    "test_evidence",
    "mutation_evidence",
    "control_policy",
    "baseline_match",
    "protected_surface",
    "control_worktree",
)

# Statuses acceptable for projection COMPLETE, per binding. Every
# binding requires ``exact``; the two marker bindings additionally
# accept the explicit ``not_produced`` (the mission's own vocabulary
# for a target that recorded no such evidence).
_MARKER_BINDINGS = ("test_evidence", "mutation_evidence")
ACCEPTABLE_STATUSES = {
    name: (
        (BINDING_EXACT, BINDING_NOT_PRODUCED)
        if name in _MARKER_BINDINGS else (BINDING_EXACT,)
    )
    for name in PROJECTION_BINDINGS
}

PROJECTION_COMPLETE = "COMPLETE"
PROJECTION_PARTIAL = "PARTIAL"

_TOP_LEVEL_KEYS = (
    "schema_version",
    "collected_at",
    "bindings",
    "completeness",
    "diagnostics",
)

# -- hard bounds (module constants, never input-derived) -------------

# Bounded changed-path listing; the EXACT total always rides
# alongside, so the truncation flag never hides a count.
MAX_CHANGED_PATHS_LISTED = 200
MAX_LISTED_PATH_CHARS = 4096
# Marker capture bounds; a bound hit is flagged explicitly.
MAX_MARKER_CHARS = 4000
MAX_MARKER_LINES = 120
# Bounded diagnostic detail text.
MAX_DIAGNOSTIC_DETAIL_CHARS = 500

# -- fixed target .herd/state artifact surface (ruling R-1(b)) -------

# The FIXED allowlist of target state artifacts this module reads,
# all through the one hardened primitive. The checkpoint is the ONLY
# marker source; the reviewer round file supplies identity and
# bounded evidence text only (its DECISION comes from the observer's
# canonical parse).
CHECKPOINT_FILE_NAME = "task-checkpoint.md"
MARKER_SOURCE_FILES = (CHECKPOINT_FILE_NAME,)
_STATE_SUBDIRS = (".herd", "state")
_REVIEWS_SUBDIRS = (".herd", "state", "reviews")

# The reviewer round artifact vocabulary, derived from the canonical
# writer (herdctl ``review-decision``): the file is named
# ``{task_id}-round-{round:02d}.md`` and its preamble carries
# ``Reviewer: `{logical}` / `{session}``` before the
# ``## Transcript`` marker. A contract test runs the REAL writer and
# pins both, so drift in the writer fails the suite here.
REVIEW_ROUND_FILE_FORMAT = "%s-round-%02d.md"
REVIEWER_LINE_PREFIX = "Reviewer: "
TRANSCRIPT_MARKER = "## Transcript"
_REVIEWER_IDENTITY_RE = re.compile(r"^`([^`\n]*)` / `([^`\n]*)`$")

# Conservative closed alphabet for an observed task id used to NAME a
# review round file. Herd ids are date-hex ("20260826-...-8e2c95");
# anything outside this set (path separators, dots, the observer's
# truncation ellipsis) refuses the read before any filesystem call.
_TASK_ID_ALPHABET = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)

# -- marker vocabulary (ruling R-1) ----------------------------------

# Hard-constant marker tuples. A marker matches a line when the
# line's key — the stripped text, with leading markdown ``#`` marks
# removed, case-folded — starts with the marker AND the marker is
# followed by a boundary: for a HEADER line (the stripped line began
# with ``#``) end-of-line, whitespace, ``:`` or ``(``; for a
# non-header line ONLY end-of-line or ``:`` (a label), so an
# ordinary sentence beginning with the word ("Tests pass") never
# starts a capture. Capture runs from the marker line to the next
# marker line of either tuple, the next header line, or the hard
# line/char bound, whichever comes first; a bound hit is flagged
# explicitly. The FIRST match wins. No match on an exactly-read
# checkpoint -> the explicit ``not_produced``.
TEST_EVIDENCE_MARKERS = ("verification", "test evidence", "tests")
MUTATION_EVIDENCE_MARKERS = ("mutation",)
_HEADER_BOUNDARY_CHARS = (" ", "\t", ":", "(")
_LABEL_BOUNDARY_CHARS = (":",)

# -- protected control surface ---------------------------------------

# Fixed roots of the control repository's protected surfaces. The
# digest is framed (path AND content, length-prefixed — see
# workflow_authority.digest) over ONLY ``.py``/``.md`` regular files
# outside ``__pycache__``: the Runtime itself imports herdr modules,
# which can write ``herdr/__pycache__/*.pyc``, and a digest that
# drifted on interpreter cache files would report a tampered control
# surface on a healthy system.
PROTECTED_SURFACE_ROOTS = ("herdr", "herdctl.py", "roles")
_PROTECTED_SUFFIXES = (".py", ".md")
_EXCLUDED_DIR_NAMES = ("__pycache__",)
MAX_PROTECTED_SURFACE_FILES = 512
MAX_PROTECTED_SURFACE_FILE_BYTES = 2097152
MAX_PROTECTED_SURFACE_TOTAL_BYTES = 16777216

_PROTECTED_PATH_PREFIXES = ("herdr/", "roles/")
_PROTECTED_EXACT_PATHS = ("herdctl.py",)


# -- source-scoped observation support (supervisor ruling R-6) -------

# The observer's complete diagnostic-source vocabulary. Pinned by a
# contract test that DERIVES it from the observer's own source (the
# `_note(diags, "<source>", ...)` call sites) with an anti-vacuity
# guard — a drifted or hand-typo'd entry fails the suite.
OBSERVE_DIAGNOSTIC_SOURCES = (
    "agents",
    "artifacts",
    # I6 (R-46/R-55/R-61) added four observer sources. Re-derived
    # DELIBERATELY here rather than loosened: this tuple is what a
    # consumer binds against, and a vocabulary that grew silently
    # would let a consumer keep reading a diagnostic set it no longer
    # covers.
    "checkpoint",
    "children",
    "config",
    "mission",
    "observation",
    "recent_tasks",
    "repository",
    "reviews",
    "roles",
    "runtime",
    "task",
    "turns",
    "vintage",
)

# The observer's completeness-DEMOTING diagnostic states ("truth
# exists that could not be seen"). Pinned equal to the observer's
# own `_PARTIAL_STATES` by a contract test. Cleanly-observed
# absences ("missing"/"empty") and exact-total truncation
# disclosures (state "available") do not demote globally and do not
# block here either: the bindings themselves represent absence and
# honour explicit truncation flags precisely.
OBSERVE_BLOCKING_STATES = ("malformed", "unreadable", "unavailable")

# The verification decision's consumed-source set: exactly the
# sources whose sections this layer reads — `task` (target task
# id/lifecycle status), `reviews` (canonical Reviewer round +
# decision), `artifacts` (checkpoint mtime/presence) — plus
# `observation`, the observer's fallback source meaning the
# projection machinery itself failed, which every consumer
# necessarily consumes because every section arrives through it.
# Deliberately NOT consumed: `agents` (unprobed in production, read
# by nothing here), `config`, `mission`, `repository`, `runtime`,
# `recent_tasks`, `children` (children is I5 reconciliation's
# source, not verification's).
VERIFICATION_CONSUMED_SOURCES = (
    "artifacts",
    "observation",
    "reviews",
    "task",
)

# The reconcile decision's declared consumed-source set (I5, R-6
# condition 3): `children` names the independently projected CONTROL
# repository spawn-record source; `task` names the LEASED workspace's
# own canonical task identity; `observation` is the canonical
# projection-failure fallback. The control source has its own closed
# clean/degraded shape and is gated directly by Broker; the canonical
# source-scoped support primitive gates the lease observation with this
# conservative registered set. Deliberately NOT global completeness: a
# production observation is globally PARTIAL whenever agents are
# unprobed, and a global gate here would make every production
# reconciliation permanently BLOCKED (the E-1 defect in objective B's
# clothes).
RECONCILE_CONSUMED_SOURCES = (
    "children",
    "observation",
    "task",
)

# The REGISTRY of declared consumed-source sets (R-6 condition 3):
# `observation_supports` REFUSES a set that is not registered here,
# so every consumer inherits the same derivation and anti-vacuity
# validation the registry test applies to every entry.
CONSUMED_SOURCE_SETS = {
    "verification": VERIFICATION_CONSUMED_SOURCES,
    "reconcile_dispatch": RECONCILE_CONSUMED_SOURCES,
}

# Bound on the blocking-diagnostics list returned to callers; the
# support DECISION is computed over every diagnostic regardless.
MAX_BLOCKING_DIAGNOSTICS = 64


def observation_supports(raw_observation, consumed_sources):
    """Whether one raw observation supports one decision (R-6).

    ``(supported, blocking_diagnostics)``: supported is True iff the
    observation is a well-formed projection (dict, completeness in
    the observer's closed COMPLETE/PARTIAL domain, diagnostics a
    list) AND no diagnostic whose ``source`` is in
    ``consumed_sources`` carries a demoting state. The consumed set
    is a PARAMETER and must be a registered member of
    ``CONSUMED_SOURCE_SETS`` — an unregistered set raises, so every
    consumer's set passes the same derivation validation.
    ``blocking_diagnostics`` carries bounded copies of the blocking
    diagnostics (source/state/detail); the decision itself is never
    bounded.
    """
    consumed = tuple(consumed_sources)
    if consumed not in {
        tuple(value) for value in CONSUMED_SOURCE_SETS.values()
    }:
        raise ValueError(
            "consumed-source set %r is not registered in"
            " CONSUMED_SOURCE_SETS; every consumer must declare its"
            " set there so it passes the shared derivation"
            " validation" % (consumed,)
        )

    def synthetic(detail):
        return [{
            "source": "observation",
            "state": "unavailable",
            "detail": detail[:MAX_DIAGNOSTIC_DETAIL_CHARS],
        }]

    if not isinstance(raw_observation, dict):
        return False, synthetic("no observation")
    if raw_observation.get("completeness") not in (
        "COMPLETE", "PARTIAL"
    ):
        return False, synthetic(
            "observation completeness is outside the observer's"
            " closed domain"
        )
    diagnostics = raw_observation.get("diagnostics")
    if not isinstance(diagnostics, list):
        return False, synthetic(
            "observation carries no diagnostics list"
        )
    blocking = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        if diagnostic.get("source") not in consumed:
            continue
        if diagnostic.get("state") not in OBSERVE_BLOCKING_STATES:
            continue
        if len(blocking) < MAX_BLOCKING_DIAGNOSTICS:
            detail = diagnostic.get("detail")
            blocking.append({
                "source": diagnostic.get("source"),
                "state": diagnostic.get("state"),
                "detail": (
                    detail[:MAX_DIAGNOSTIC_DETAIL_CHARS]
                    if isinstance(detail, str) else None
                ),
            })
        else:
            # The DECISION still sees every diagnostic; only the
            # returned list is bounded. Represent the overflow with
            # a final synthetic marker rather than dropping it
            # silently.
            blocking[-1] = {
                "source": diagnostic.get("source"),
                "state": diagnostic.get("state"),
                "detail": "further blocking diagnostics elided"
                " (bound %d)" % MAX_BLOCKING_DIAGNOSTICS,
            }
    return not blocking, blocking


class EvidenceError(Exception):
    """A projection failed validation; message is actionable."""

    def __init__(self, message, problem):
        super(EvidenceError, self).__init__(message)
        self.problem = problem


PROBLEM_NOT_AN_OBJECT = "evidence_not_an_object"
PROBLEM_SCHEMA_VERSION = "evidence_schema_version"
PROBLEM_UNKNOWN_KEY = "evidence_unknown_key"
PROBLEM_MISSING_KEY = "evidence_missing_key"
PROBLEM_BAD_TYPE = "evidence_bad_type"
PROBLEM_BAD_VALUE = "evidence_bad_value"
PROBLEM_TOO_LARGE = "evidence_too_large"
PROBLEM_COMPLETENESS = "evidence_completeness_inconsistent"


# -- the hardened state-artifact read --------------------------------

# The primitive's per-file status -> binding status, EXPLICIT and
# closed (no silent default: an unmapped primitive status raises in
# ``_binding_status_for_read``, and a test proves the map covers the
# primitive's ENTIRE closed status set). The primitive's exact status
# is always carried alongside as ``read_status`` so no refusal reason
# is ever collapsed away.
_READ_STATUS_TO_BINDING = {
    prepare_module.INSTRUCTION_READ: BINDING_EXACT,
    prepare_module.INSTRUCTION_ABSENT: BINDING_REFUSED_ABSENT,
    prepare_module.INSTRUCTION_REFUSED_OVER_BOUND: (
        BINDING_REFUSED_OVER_BOUND
    ),
    prepare_module.INSTRUCTION_REFUSED_UNREADABLE: (
        BINDING_REFUSED_UNREADABLE
    ),
    prepare_module.INSTRUCTION_REFUSED_NON_UTF8: (
        BINDING_REFUSED_UNREADABLE
    ),
    prepare_module.INSTRUCTION_REFUSED_NOT_REGULAR: (
        BINDING_REFUSED_UNREADABLE
    ),
    prepare_module.INSTRUCTION_REFUSED_ESCAPES: (
        BINDING_REFUSED_UNREADABLE
    ),
    prepare_module.INSTRUCTION_REFUSED_HARDLINK: (
        BINDING_REFUSED_UNREADABLE
    ),
}


def _binding_status_for_read(read_status):
    try:
        return _READ_STATUS_TO_BINDING[read_status]
    except KeyError:
        raise ValueError(
            "no binding status for read status %r; every primitive"
            " status must have an explicit entry" % (read_status,)
        )


def _resolved_chain(root, subdirs):
    """The fixed subdirectory chain under the lease, or None.

    Requires every component of ``root/subdirs...`` to resolve to
    ITSELF (``os.path.realpath`` identity), so a symlinked ``.herd``,
    ``state`` or ``reviews`` component — which the separator-free
    hardened primitive cannot see — can never carry a read outside
    the lease. A missing directory resolves to itself and passes;
    the primitive then reports the file cleanly absent.
    """
    path = root
    for component in subdirs:
        path = os.path.join(path, component)
        if os.path.realpath(path) != path:
            return None
    return path


def read_state_artifact(workspace_realpath, subdirs, name):
    """One hardened read of a fixed ``.herd/state`` artifact.

    REUSES the one hardened primitive
    (``prepare.read_workspace_instruction``) for the open/read
    itself — this wrapper only (a) refuses a ``name`` carrying any
    path separator before touching the filesystem, (b) requires the
    fixed directory chain to resolve to itself before AND after the
    read (content from a chain that changed underneath the read is
    discarded), and (c) returns the primitive's exact
    ``(status, byte_count, digest, text)`` shape.
    """
    if os.sep in name or "/" in name or "\x00" in name:
        return (
            prepare_module.INSTRUCTION_REFUSED_ESCAPES,
            None, None, None,
        )
    root = os.path.realpath(workspace_realpath)
    directory = _resolved_chain(root, subdirs)
    if directory is None:
        return (
            prepare_module.INSTRUCTION_REFUSED_ESCAPES,
            None, None, None,
        )
    result = prepare_module.read_workspace_instruction(
        directory, name
    )
    if _resolved_chain(root, subdirs) != directory:
        # The chain changed while we were reading: whatever was read
        # is of unknown provenance and is DISCARDED.
        return (
            prepare_module.INSTRUCTION_REFUSED_ESCAPES,
            None, None, None,
        )
    return result


# -- marker extraction (ruling R-1) ----------------------------------

def _line_marker_key(line):
    """(key, is_header) for one checkpoint line."""
    stripped = line.strip()
    is_header = stripped.startswith("#")
    if is_header:
        stripped = stripped.lstrip("#").strip()
    return stripped.casefold(), is_header


def _matches_marker(line, markers):
    key, is_header = _line_marker_key(line)
    boundary = (
        _HEADER_BOUNDARY_CHARS if is_header else _LABEL_BOUNDARY_CHARS
    )
    for marker in markers:
        if not key.startswith(marker):
            continue
        if len(key) == len(marker) or key[len(marker)] in boundary:
            return True
    return False


def _is_any_marker_or_header(line):
    key, is_header = _line_marker_key(line)
    if is_header and key:
        return True
    return _matches_marker(
        line, TEST_EVIDENCE_MARKERS + MUTATION_EVIDENCE_MARKERS
    )


def extract_marker_evidence(text, markers, source_name):
    """One marker binding from an exactly-read source text.

    Returns the closed binding shape: ``exact`` with the captured
    text (bounded, ``bound_hit`` flagged) for the FIRST matching
    marker line, or the explicit ``not_produced`` when no line
    matches. The extracted content is subordinate target-authored
    text for judgement only — nothing here parses it further.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if _matches_marker(line, markers):
            start = index
            break
    if start is None:
        return {
            "status": BINDING_NOT_PRODUCED,
            "text": None,
            "bound_hit": False,
            "source": source_name,
        }
    # The char bound binds the SEED LINE TOO (round-01 F-1): the
    # marker line is target-authored and can be up to the hardened
    # primitive's whole file bound, so an unbounded seed would let
    # checkpoint text produce a capture the module's own validator
    # refuses — target bytes denying the projection. A truncated
    # seed sets bound_hit exactly like the accumulation path,
    # including when the marker line is the LAST line of the file.
    seed = lines[start]
    bound_hit = len(seed) > MAX_MARKER_CHARS
    if bound_hit:
        seed = seed[:MAX_MARKER_CHARS]
    captured = [seed]
    chars = len(seed)
    for line in lines[start + 1:]:
        if _is_any_marker_or_header(line):
            break
        if (
            len(captured) >= MAX_MARKER_LINES
            or chars + 1 + len(line) > MAX_MARKER_CHARS
        ):
            bound_hit = True
            break
        captured.append(line)
        chars += 1 + len(line)
    return {
        "status": BINDING_EXACT,
        "text": "\n".join(captured),
        "bound_hit": bound_hit,
        "source": source_name,
    }


# -- protected control surface ---------------------------------------

def protected_surface_digest(control_realpath):
    """Deterministic framed digest of the control repository's
    protected surfaces.

    Closed shape (all keys always present):
    ``status``/``digest``/``file_count``/``total_bytes``/``detail``.
    ``exact`` carries the digest and EXACT counts; every refusal
    carries NO digest and no exact-named count — a digest over a
    partial or over-bound surface is never emitted (``detail`` may
    describe observed magnitudes in explicitly lower-bound words).
    """

    def refusal(status, detail):
        return {
            "status": status,
            "digest": None,
            "file_count": None,
            "total_bytes": None,
            "detail": detail[:MAX_DIAGNOSTIC_DETAIL_CHARS],
        }

    root = os.path.realpath(control_realpath)
    relative_paths = []
    for root_name in PROTECTED_SURFACE_ROOTS:
        path = os.path.join(root, root_name)
        if os.path.islink(path):
            return refusal(
                BINDING_REFUSED_UNREADABLE,
                "protected root %s is a symlink" % root_name,
            )
        if os.path.isfile(path):
            if root_name.endswith(_PROTECTED_SUFFIXES):
                relative_paths.append(root_name)
            continue
        if not os.path.isdir(path):
            return refusal(
                BINDING_REFUSED_ABSENT,
                "protected root %s is missing; a digest over a"
                " partial surface is never emitted" % root_name,
            )
        for base, dirs, names in os.walk(path):
            dirs[:] = sorted(
                d for d in dirs if d not in _EXCLUDED_DIR_NAMES
            )
            for file_name in sorted(names):
                if not file_name.endswith(_PROTECTED_SUFFIXES):
                    continue
                full = os.path.join(base, file_name)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                relative = os.path.relpath(full, root)
                relative_paths.append(
                    relative.replace(os.sep, "/")
                )
    relative_paths.sort()
    if len(relative_paths) > MAX_PROTECTED_SURFACE_FILES:
        return refusal(
            BINDING_REFUSED_OVER_BOUND,
            "at least %d protected-surface files (hard bound %d);"
            " refused, not partially digested"
            % (len(relative_paths), MAX_PROTECTED_SURFACE_FILES),
        )
    parts = []
    total_bytes = 0
    for relative in relative_paths:
        try:
            with open(os.path.join(root, relative), "rb") as handle:
                data = handle.read(
                    MAX_PROTECTED_SURFACE_FILE_BYTES + 1
                )
        except OSError:
            return refusal(
                BINDING_REFUSED_UNREADABLE,
                "protected-surface file %s could not be read; a"
                " digest over a partial surface is never emitted"
                % relative,
            )
        if len(data) > MAX_PROTECTED_SURFACE_FILE_BYTES:
            return refusal(
                BINDING_REFUSED_OVER_BOUND,
                "protected-surface file %s exceeds the per-file"
                " hard bound (%d bytes)"
                % (relative, MAX_PROTECTED_SURFACE_FILE_BYTES),
            )
        total_bytes += len(data)
        if total_bytes > MAX_PROTECTED_SURFACE_TOTAL_BYTES:
            return refusal(
                BINDING_REFUSED_OVER_BOUND,
                "protected surface exceeds the total hard bound"
                " (%d bytes)" % MAX_PROTECTED_SURFACE_TOTAL_BYTES,
            )
        parts.append(relative.encode("utf-8"))
        parts.append(data)
    return {
        "status": BINDING_EXACT,
        "digest": framed_digest(parts),
        "file_count": len(relative_paths),
        "total_bytes": total_bytes,
        "detail": None,
    }


def _porcelain_line_names_protected_path(line):
    """Whether one porcelain line names a protected control path.

    Porcelain v1: two status columns, a space, then the path (rename
    lines carry ``old -> new``; git quotes unusual paths, so a
    leading quote is stripped before the prefix check — the literal
    prefix must still appear, so this can only widen detection,
    never narrow it)."""
    if len(line) < 4:
        return False
    for candidate in line[3:].split(" -> "):
        path = candidate.strip()
        if path.startswith('"'):
            path = path[1:]
        if path in _PROTECTED_EXACT_PATHS:
            return True
        if path.startswith(_PROTECTED_PATH_PREFIXES):
            return True
    return False


# -- the projection ---------------------------------------------------

def _diagnostic(binding_name, status, detail):
    return {
        "binding": binding_name,
        "status": status,
        "detail": (detail or "")[:MAX_DIAGNOSTIC_DETAIL_CHARS],
    }


def projection_completeness(bindings):
    """(completeness, diagnostics) recomputed from the bindings.

    The SINGLE source for the completeness rule: ``COMPLETE`` only
    when every binding's status is in its acceptable set; every
    failing binding contributes one diagnostic."""
    diagnostics = []
    for name in PROJECTION_BINDINGS:
        status = bindings[name]["status"]
        if status not in ACCEPTABLE_STATUSES[name]:
            diagnostics.append(
                _diagnostic(
                    name, status,
                    "binding %s is %s, not %s" % (
                        name, status,
                        " or ".join(ACCEPTABLE_STATUSES[name]),
                    ),
                )
            )
    completeness = (
        PROJECTION_COMPLETE if not diagnostics else PROJECTION_PARTIAL
    )
    return completeness, diagnostics


def _record_bindings(entry):
    """Bindings resolved purely from the protected record — always
    exact: the record was validated by every layer that stored it."""
    authorization = entry["mission_authorization"]
    acceptance = {"status": BINDING_EXACT}
    for key in rendering.AUTHORITY_CONTENT_KEYS:
        acceptance[key] = authorization[key]
    return {
        "workflow": {
            "status": BINDING_EXACT,
            "workflow_id": entry["workflow_id"],
            "handoff_revision": entry["handoff"]["revision"],
        },
        "target": {
            "status": BINDING_EXACT,
            "canonical_host": entry["target"]["canonical_host"],
            "owner": entry["target"]["owner"],
            "repo": entry["target"]["repo"],
            "canonical_url": entry["target"]["canonical_url"],
            # Explicit null for a repository-only target — an
            # inapplicable binding is REPRESENTED, never absent.
            "issue_or_pr": entry["target"]["issue_or_pr"],
        },
        "approved_baseline": {
            "status": BINDING_EXACT,
            "commit_sha": entry["approved_baseline"]["commit_sha"],
            # DISPLAY ONLY: the stored ref grammar is looser than
            # git's; it is never passed to git anywhere.
            "ref_display": entry["approved_baseline"]["ref"],
        },
        "acceptance": acceptance,
        "delivery_authority": {
            "status": BINDING_EXACT,
            "value": entry["delivery_authority"],
        },
        "dispatch": {
            "status": BINDING_EXACT,
            "dispatch_count": dispatch_module.dispatch_count(entry),
            "handoff_digest_sha256": (
                entry["handoff"]["digest_sha256"]
            ),
        },
    }


def _refused(status, **fields):
    fields["status"] = status
    return fields


def _live_git_bindings(transport, lease_path):
    """live_origin / live_head / changed_paths / diff from the
    injected transport on the LEASED workspace realpath."""
    if lease_path is None:
        absent = BINDING_REFUSED_ABSENT
        return {
            "live_origin": _refused(absent, url=None),
            "live_head": _refused(absent, commit_sha=None),
            "changed_paths": _empty_changed_paths(absent),
            "diff": _empty_diff(absent),
        }
    bindings = {}
    try:
        bindings["live_origin"] = {
            "status": BINDING_EXACT,
            "url": transport.remote_url(lease_path).strip(),
        }
    except GitTransportError:
        bindings["live_origin"] = _refused(
            BINDING_REFUSED_UNREADABLE, url=None
        )
    try:
        bindings["live_head"] = {
            "status": BINDING_EXACT,
            "commit_sha": transport.head_commit(lease_path).strip(),
        }
    except GitTransportError:
        bindings["live_head"] = _refused(
            BINDING_REFUSED_UNREADABLE, commit_sha=None
        )
    bindings["changed_paths"] = _changed_paths_binding(
        transport, lease_path
    )
    bindings["diff"] = _diff_binding(transport, lease_path)
    return bindings


def _empty_changed_paths(status, total_bytes_lower_bound=None):
    return {
        "status": status,
        "total_count": None,
        "staged_count": None,
        "worktree_modified_count": None,
        "untracked_count": None,
        "listed": None,
        "listing_truncated": None,
        "total_bytes_lower_bound": total_bytes_lower_bound,
    }


def _changed_paths_binding(transport, lease_path):
    """The porcelain inventory: EXACT total entry count, exact
    per-category counts (categories can overlap: an entry can be
    both staged and worktree-modified; untracked directories
    collapse to one entry under git's default), and a bounded
    listing whose truncation flag always rides WITH the exact
    total."""
    try:
        capture = transport.status_porcelain_readonly(lease_path)
    except GitTransportError:
        return _empty_changed_paths(BINDING_REFUSED_UNREADABLE)
    if capture["status"] != CAPTURE_CAPTURED:
        return _empty_changed_paths(
            BINDING_REFUSED_OVER_BOUND,
            total_bytes_lower_bound=capture.get(
                "total_bytes_lower_bound"
            ),
        )
    lines = capture["text"].splitlines()
    staged = 0
    worktree_modified = 0
    untracked = 0
    for line in lines:
        if len(line) < 3:
            continue
        if line.startswith("??"):
            untracked += 1
            continue
        if line[0] not in " ?":
            staged += 1
        if line[1] not in " ?":
            worktree_modified += 1
    return {
        "status": BINDING_EXACT,
        "total_count": len(lines),
        "staged_count": staged,
        "worktree_modified_count": worktree_modified,
        "untracked_count": untracked,
        "listed": lines[:MAX_CHANGED_PATHS_LISTED],
        "listing_truncated": len(lines) > MAX_CHANGED_PATHS_LISTED,
        "total_bytes_lower_bound": None,
    }


def _empty_diff(status, total_bytes_lower_bound=None):
    return {
        "status": status,
        "retained_bytes": None,
        "retained_text": None,
        "retained_text_lossy": None,
        "truncated": None,
        "total_bytes": None,
        "digest": None,
        "total_bytes_lower_bound": total_bytes_lower_bound,
    }


def _diff_binding(transport, lease_path):
    try:
        capture = transport.diff_head(lease_path)
    except GitTransportError:
        return _empty_diff(BINDING_REFUSED_UNREADABLE)
    if capture["status"] != CAPTURE_CAPTURED:
        return _empty_diff(
            BINDING_REFUSED_OVER_BOUND,
            total_bytes_lower_bound=capture.get(
                "total_bytes_lower_bound"
            ),
        )
    return {
        "status": BINDING_EXACT,
        "retained_bytes": capture["retained_bytes"],
        "retained_text": capture["retained_text"],
        "retained_text_lossy": capture["retained_text_lossy"],
        "truncated": capture["truncated"],
        "total_bytes": capture["total_bytes"],
        "digest": capture["digest"],
        "total_bytes_lower_bound": None,
    }


def _observation_bindings(observer, lease_path):
    """observation / target_task / review_decision /
    checkpoint_mtime from ONE observer call, plus the raw pieces the
    file-read bindings need (task id, latest round)."""
    empty_task = {"task_id": None, "task_status": None}
    empty_review = {"round": None, "decision": None}
    empty_mtime = {"mtime": None, "size": None}
    empty_observation = {
        "completeness": None,
        "supports_verification": None,
        "blocking_sources": None,
    }
    if lease_path is None:
        return (
            {
                "observation": _refused(
                    BINDING_REFUSED_ABSENT, **empty_observation
                ),
                "target_task": _refused(
                    BINDING_REFUSED_ABSENT, **empty_task
                ),
                "review_decision": _refused(
                    BINDING_REFUSED_ABSENT, **empty_review
                ),
                "checkpoint_mtime": _refused(
                    BINDING_REFUSED_ABSENT, **empty_mtime
                ),
            },
            None, None,
        )
    try:
        raw = observer(lease_path)
    except Exception:
        raw = None
    if not isinstance(raw, dict):
        return (
            {
                "observation": _refused(
                    BINDING_REFUSED_UNREADABLE, **empty_observation
                ),
                "target_task": _refused(
                    BINDING_REFUSED_UNREADABLE, **empty_task
                ),
                "review_decision": _refused(
                    BINDING_REFUSED_UNREADABLE, **empty_review
                ),
                "checkpoint_mtime": _refused(
                    BINDING_REFUSED_UNREADABLE, **empty_mtime
                ),
            },
            None, None,
        )
    # The R-6 source-scoped decision: computed ONCE per collection
    # by the SHARED primitive, over the verification consumed-source
    # set. The raw global completeness is recorded EXACTLY as herd
    # reported it and is never rewritten by this decision.
    supported, blocking = observation_supports(
        raw, VERIFICATION_CONSUMED_SOURCES
    )
    completeness = raw.get("completeness")
    if completeness in ("COMPLETE", "PARTIAL"):
        # The real projection's closed completeness vocabulary; any
        # other shape is not an exact observation of anything.
        observation = {
            "status": BINDING_EXACT,
            "completeness": completeness,
            "supports_verification": supported,
            "blocking_sources": sorted(
                {d["source"] for d in blocking
                 if isinstance(d.get("source"), str)}
            ),
        }
    else:
        observation = _refused(
            BINDING_REFUSED_UNREADABLE, **empty_observation
        )

    task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
    task_state = task.get("state")
    task_id = task.get("id")
    task_status = task.get("status")
    if not supported:
        target_task = _refused(
            BINDING_REFUSED_INCOMPLETE, **empty_task
        )
    elif task_state == "missing":
        target_task = _refused(BINDING_REFUSED_ABSENT, **empty_task)
    elif (
        task_state == "available"
        and isinstance(task_id, str) and task_id
        and isinstance(task_status, str) and task_status
    ):
        target_task = {
            "status": BINDING_EXACT,
            "task_id": task_id,
            "task_status": task_status,
        }
    else:
        target_task = _refused(
            BINDING_REFUSED_INCOMPLETE, **empty_task
        )

    reviews = (
        raw.get("reviews")
        if isinstance(raw.get("reviews"), dict) else {}
    )
    listed = reviews.get("listed")
    listed = listed if isinstance(listed, list) else []
    if not supported:
        review_decision = _refused(
            BINDING_REFUSED_INCOMPLETE, **empty_review
        )
    elif reviews.get("state") in ("missing", "empty"):
        review_decision = _refused(
            BINDING_REFUSED_ABSENT, **empty_review
        )
    elif (
        reviews.get("state") == "available"
        and reviews.get("truncated") is False
        and listed
        and isinstance(listed[-1], dict)
        and isinstance(listed[-1].get("round"), int)
        and not isinstance(listed[-1].get("round"), bool)
        and listed[-1]["round"] >= 1
    ):
        review_decision = {
            "status": BINDING_EXACT,
            "round": listed[-1]["round"],
            # The canonical decision (Protocol token parse). None is
            # a faithful value: the canonical record exists but holds
            # no valid decision.
            "decision": listed[-1].get("decision"),
        }
    else:
        # A truncated listing, a degraded reviews source, or a
        # malformed latest entry is never evidence.
        review_decision = _refused(
            BINDING_REFUSED_INCOMPLETE, **empty_review
        )

    artifacts = (
        raw.get("artifacts")
        if isinstance(raw.get("artifacts"), dict) else {}
    )
    artifact_entry = None
    for item in (
        artifacts.get("listed")
        if isinstance(artifacts.get("listed"), list) else []
    ):
        if (
            isinstance(item, dict)
            and item.get("name") == CHECKPOINT_FILE_NAME
        ):
            artifact_entry = item
            break
    if not supported:
        checkpoint_mtime = _refused(
            BINDING_REFUSED_INCOMPLETE, **empty_mtime
        )
    elif artifact_entry is None or not artifact_entry.get("present"):
        # N-1 (round-01, non-blocking): on herd's pre-try fallback
        # observation (every section {"state": "unavailable"}) this
        # branch would call a never-observed source "absent" — but
        # that path is effectively unreachable in production
        # (observe()'s fallback needs Path(repo).expanduser() to
        # raise, which an absolute realpath containing no '~' does
        # not), and target_task/review_decision refuse
        # refused_incomplete on it regardless, so nothing launders
        # to COMPLETE.
        checkpoint_mtime = _refused(
            BINDING_REFUSED_ABSENT, **empty_mtime
        )
    elif isinstance(artifact_entry.get("mtime"), int) and (
        not isinstance(artifact_entry.get("mtime"), bool)
    ):
        checkpoint_mtime = {
            "status": BINDING_EXACT,
            "mtime": artifact_entry["mtime"],
            "size": (
                artifact_entry.get("size")
                if isinstance(artifact_entry.get("size"), int)
                and not isinstance(artifact_entry.get("size"), bool)
                else None
            ),
        }
    else:
        checkpoint_mtime = _refused(
            BINDING_REFUSED_INCOMPLETE, **empty_mtime
        )

    latest_round = (
        review_decision["round"]
        if review_decision["status"] == BINDING_EXACT else None
    )
    bound_task_id = (
        target_task["task_id"]
        if target_task["status"] == BINDING_EXACT else None
    )
    return (
        {
            "observation": observation,
            "target_task": target_task,
            "review_decision": review_decision,
            "checkpoint_mtime": checkpoint_mtime,
        },
        bound_task_id, latest_round,
    )


def _read_binding(lease_path, subdirs, name):
    """A file-read binding through the hardened primitive: status,
    the primitive's exact read_status, and exact accounting."""
    if lease_path is None:
        return {
            "status": BINDING_REFUSED_ABSENT,
            "read_status": None,
            "byte_count": None,
            "digest": None,
            "text": None,
        }
    read_status, byte_count, digest, text = read_state_artifact(
        lease_path, subdirs, name
    )
    return {
        "status": _binding_status_for_read(read_status),
        "read_status": read_status,
        "byte_count": byte_count,
        "digest": digest,
        "text": text,
    }


def _review_file_bindings(lease_path, task_id, latest_round):
    """review_file (name + bounded text + digest) and
    reviewer_identity, both from the canonical round artifact named
    by the observer's task id and latest round."""

    def unresolved(status):
        return (
            {
                "status": status,
                "read_status": None,
                "name": None,
                "byte_count": None,
                "digest": None,
                "text": None,
            },
            {
                "status": status,
                "logical": None,
                "session": None,
            },
        )

    if lease_path is None:
        return unresolved(BINDING_REFUSED_ABSENT)
    if task_id is None or latest_round is None:
        # The file cannot be identified without an exact task id and
        # an exact canonical round: a guessed name is never read.
        return unresolved(BINDING_REFUSED_INCOMPLETE)
    if any(ch not in _TASK_ID_ALPHABET for ch in task_id):
        return unresolved(BINDING_REFUSED_UNREADABLE)
    name = REVIEW_ROUND_FILE_FORMAT % (task_id, latest_round)
    read = _read_binding(lease_path, _REVIEWS_SUBDIRS, name)
    review_file = {
        "status": read["status"],
        "read_status": read["read_status"],
        "name": name,
        "byte_count": read["byte_count"],
        "digest": read["digest"],
        "text": read["text"],
    }
    if read["status"] != BINDING_EXACT:
        identity = {
            "status": read["status"],
            "logical": None,
            "session": None,
        }
        return review_file, identity
    parsed = parse_reviewer_identity(read["text"])
    if parsed is None:
        identity = {
            "status": BINDING_NOT_PRODUCED,
            "logical": None,
            "session": None,
        }
    else:
        identity = {
            "status": BINDING_EXACT,
            "logical": parsed[0],
            "session": parsed[1],
        }
    return review_file, identity


def parse_reviewer_identity(text):
    """(logical, session) from a canonical reviewer round artifact,
    or None. Parses ONLY the preamble before the ``## Transcript``
    marker (transcript prose can never supply an identity), matching
    the canonical writer's exact ``Reviewer: `x` / `y``` line — the
    format a contract test pins against the real writer. Never the
    decision: that comes from the observer's canonical parse."""
    if text.startswith(TRANSCRIPT_MARKER):
        return None
    marker = text.find("\n" + TRANSCRIPT_MARKER)
    if marker < 0:
        return None
    for line in text[:marker].splitlines():
        stripped = line.strip()
        if not stripped.startswith(REVIEWER_LINE_PREFIX):
            continue
        matched = _REVIEWER_IDENTITY_RE.match(
            stripped[len(REVIEWER_LINE_PREFIX):]
        )
        if matched is None:
            return None
        return matched.group(1), matched.group(2)
    return None


def _marker_binding_from_checkpoint(checkpoint, markers):
    if checkpoint["status"] != BINDING_EXACT:
        # The source could not be read exactly, so whether evidence
        # was produced is UNKNOWN — the checkpoint's own refusal
        # status is propagated; ``not_produced`` is reserved for a
        # marker genuinely absent from an exactly-read source.
        return {
            "status": checkpoint["status"],
            "text": None,
            "bound_hit": None,
            "source": CHECKPOINT_FILE_NAME,
        }
    return extract_marker_evidence(
        checkpoint["text"], markers, CHECKPOINT_FILE_NAME
    )


def _control_bindings(entry, transport, control_realpath):
    """control_policy / protected_surface / control_worktree."""
    recorded = entry["control_identity"]["policy_digest_sha256"]
    try:
        live = control_policy_digest(control_realpath)
        control_policy = {
            "status": BINDING_EXACT,
            "live_digest": live,
            "recorded_digest": recorded,
            "match": live == recorded,
        }
    except DigestError:
        control_policy = {
            "status": BINDING_REFUSED_UNREADABLE,
            "live_digest": None,
            "recorded_digest": recorded,
            "match": None,
        }
    surface = protected_surface_digest(control_realpath)
    protected_surface = {
        "status": surface["status"],
        "digest": surface["digest"],
        "file_count": surface["file_count"],
        "total_bytes": surface["total_bytes"],
    }
    try:
        capture = transport.status_porcelain_readonly(
            os.path.realpath(control_realpath)
        )
    except GitTransportError:
        capture = None
    if capture is None:
        control_worktree = {
            "status": BINDING_REFUSED_UNREADABLE,
            "protected_dirty_count": None,
            "dirty_total_count": None,
            "clean": None,
        }
    elif capture["status"] != CAPTURE_CAPTURED:
        control_worktree = {
            "status": BINDING_REFUSED_OVER_BOUND,
            "protected_dirty_count": None,
            "dirty_total_count": None,
            "clean": None,
        }
    else:
        lines = capture["text"].splitlines()
        protected_dirty = sum(
            1 for line in lines
            if _porcelain_line_names_protected_path(line)
        )
        control_worktree = {
            "status": BINDING_EXACT,
            "protected_dirty_count": protected_dirty,
            "dirty_total_count": len(lines),
            "clean": protected_dirty == 0,
        }
    return {
        "control_policy": control_policy,
        "protected_surface": protected_surface,
        "control_worktree": control_worktree,
    }


def collect_verification_evidence(entry, transport, observer,
                                  control_realpath, now):
    """Build the complete verification-evidence projection.

    ``entry`` is the protected workflow record; ``transport`` and
    ``observer`` are the SAME injected seams the Broker holds (so
    hermetic tests never touch a real target or spawn a process);
    ``control_realpath`` is the Runtime's pinned control repository.
    The caller supplies no path, URL, baseline, or command. The
    result always validates against ``validate_projection``.
    """
    lease = entry.get("workspace_lease")
    lease_path = (
        lease["path_realpath"]
        if isinstance(lease, dict)
        and isinstance(lease.get("path_realpath"), str)
        else None
    )
    bindings = {}
    bindings.update(_record_bindings(entry))
    bindings.update(_live_git_bindings(transport, lease_path))
    observation_bindings, task_id, latest_round = (
        _observation_bindings(observer, lease_path)
    )
    bindings.update(observation_bindings)
    checkpoint = _read_binding(
        lease_path, _STATE_SUBDIRS, CHECKPOINT_FILE_NAME
    )
    bindings["checkpoint"] = checkpoint
    review_file, reviewer_identity = _review_file_bindings(
        lease_path, task_id, latest_round
    )
    bindings["review_file"] = review_file
    bindings["reviewer_identity"] = reviewer_identity
    bindings["test_evidence"] = _marker_binding_from_checkpoint(
        checkpoint, TEST_EVIDENCE_MARKERS
    )
    bindings["mutation_evidence"] = _marker_binding_from_checkpoint(
        checkpoint, MUTATION_EVIDENCE_MARKERS
    )
    bindings.update(
        _control_bindings(entry, transport, control_realpath)
    )
    live_head = bindings["live_head"]
    if live_head["status"] == BINDING_EXACT:
        bindings["baseline_match"] = {
            "status": BINDING_EXACT,
            "match": live_head["commit_sha"] == (
                entry["approved_baseline"]["commit_sha"]
            ),
        }
    else:
        bindings["baseline_match"] = {
            "status": live_head["status"],
            "match": None,
        }
    completeness, diagnostics = projection_completeness(bindings)
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "collected_at": now,
        "bindings": bindings,
        "completeness": completeness,
        "diagnostics": diagnostics,
    }


# -- validation -------------------------------------------------------

def _fail(problem, message):
    raise EvidenceError(message, problem)


def _require_dict(value, location):
    if not isinstance(value, dict):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be an object, not %s"
            % (location, type(value).__name__),
        )


def _require_closed_keys(value, allowed, location):
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        _fail(
            PROBLEM_UNKNOWN_KEY,
            "%s has unknown keys: %s (the key set is closed)"
            % (location, ", ".join(repr(k) for k in unknown)),
        )
    missing = sorted(set(allowed) - set(value))
    if missing:
        _fail(
            PROBLEM_MISSING_KEY,
            "%s is missing required keys: %s"
            % (location, ", ".join(repr(k) for k in missing)),
        )


def _require_status(value, location):
    if value not in BINDING_STATUSES:
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must be one of %s; got %r"
            % (location, ", ".join(BINDING_STATUSES), value),
        )


def _require_opt_str(value, location, max_chars, allow_empty=True):
    if value is None:
        return
    if not isinstance(value, str):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be a string or null, not %s"
            % (location, type(value).__name__),
        )
    if not allow_empty and not value:
        _fail(PROBLEM_BAD_VALUE, "%s must be non-empty" % location)
    if len(value) > max_chars:
        _fail(
            PROBLEM_TOO_LARGE,
            "%s is %d characters; the hard bound is %d (refused,"
            " never truncated)" % (location, len(value), max_chars),
        )


def _require_opt_int(value, location, minimum=0):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be an integer or null (bool is not accepted),"
            " not %r" % (location, value),
        )
    if value < minimum:
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must be >= %d; got %d" % (location, minimum, value),
        )


def _require_opt_bool(value, location):
    if value is not None and not isinstance(value, bool):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be a boolean or null, not %s"
            % (location, type(value).__name__),
        )


def _require_opt_hex(value, location, length=64):
    if value is None:
        return
    if not isinstance(value, str) or len(value) != length or any(
        ch not in "0123456789abcdef" for ch in value
    ):
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must be exactly %d lowercase hex characters or null"
            % (location, length),
        )


# Per-binding closed key sets and typed field validators. Each entry
# maps a binding name to ``(keys, validator)``; a binding outside
# this registry cannot appear, and a registry entry without a
# binding fails the missing-key check.
_MAX_READ_TEXT_CHARS = prepare_module.MAX_INSTRUCTION_FILE_BYTES


def _validate_read_shape(binding, location):
    _require_status(binding["status"], location + ".status")
    read_status = binding["read_status"]
    if read_status is not None and read_status not in (
        prepare_module.INSTRUCTION_STATUSES
    ):
        _fail(
            PROBLEM_BAD_VALUE,
            "%s.read_status %r is not a primitive status"
            % (location, read_status),
        )
    _require_opt_int(binding["byte_count"], location + ".byte_count")
    _require_opt_hex(binding["digest"], location + ".digest")
    _require_opt_str(
        binding["text"], location + ".text", _MAX_READ_TEXT_CHARS
    )


def _binding_validators():
    def workflow(b, loc):
        _require_opt_str(
            b["workflow_id"], loc + ".workflow_id", 128,
            allow_empty=False,
        )
        _require_opt_int(b["handoff_revision"],
                         loc + ".handoff_revision", minimum=1)

    def target(b, loc):
        for key in ("canonical_host", "owner", "repo"):
            _require_opt_str(b[key], "%s.%s" % (loc, key), 255,
                             allow_empty=False)
        _require_opt_str(b["canonical_url"], loc + ".canonical_url",
                         2048, allow_empty=False)
        issue_or_pr = b["issue_or_pr"]
        if issue_or_pr is not None:
            _require_dict(issue_or_pr, loc + ".issue_or_pr")
            _require_closed_keys(
                issue_or_pr, ("kind", "number"),
                loc + ".issue_or_pr",
            )

    def approved_baseline(b, loc):
        # Git commit ids: 40 hex (the record layer pins the same).
        _require_opt_hex(b["commit_sha"], loc + ".commit_sha",
                         length=40)
        _require_opt_str(b["ref_display"], loc + ".ref_display", 512)

    def acceptance(b, loc):
        for key in rendering.AUTHORITY_CONTENT_KEYS:
            _require_opt_str(b[key], "%s.%s" % (loc, key), 8000)

    def delivery_authority(b, loc):
        _require_opt_str(b["value"], loc + ".value", 64)

    def dispatch(b, loc):
        _require_opt_int(b["dispatch_count"], loc + ".dispatch_count")
        _require_opt_hex(
            b["handoff_digest_sha256"],
            loc + ".handoff_digest_sha256",
        )

    def live_origin(b, loc):
        _require_opt_str(b["url"], loc + ".url", 2048)

    def live_head(b, loc):
        _require_opt_hex(b["commit_sha"], loc + ".commit_sha",
                         length=40)

    def changed_paths(b, loc):
        for key in ("total_count", "staged_count",
                    "worktree_modified_count", "untracked_count",
                    "total_bytes_lower_bound"):
            _require_opt_int(b[key], "%s.%s" % (loc, key))
        _require_opt_bool(
            b["listing_truncated"], loc + ".listing_truncated"
        )
        listed = b["listed"]
        if listed is not None:
            if not isinstance(listed, list):
                _fail(
                    PROBLEM_BAD_TYPE,
                    "%s.listed must be a list or null" % loc,
                )
            if len(listed) > MAX_CHANGED_PATHS_LISTED:
                _fail(
                    PROBLEM_TOO_LARGE,
                    "%s.listed has %d entries; the hard bound is %d"
                    % (loc, len(listed), MAX_CHANGED_PATHS_LISTED),
                )
            for index, item in enumerate(listed):
                _require_opt_str(
                    item, "%s.listed[%d]" % (loc, index),
                    MAX_LISTED_PATH_CHARS,
                )
                if item is None:
                    _fail(
                        PROBLEM_BAD_VALUE,
                        "%s.listed[%d] must be a string" % (loc, index),
                    )

    def diff(b, loc):
        for key in ("retained_bytes", "total_bytes",
                    "total_bytes_lower_bound"):
            _require_opt_int(b[key], "%s.%s" % (loc, key))
        _require_opt_str(
            b["retained_text"], loc + ".retained_text",
            MAX_DIFF_RETAINED_BYTES,
        )
        _require_opt_bool(
            b["retained_text_lossy"], loc + ".retained_text_lossy"
        )
        _require_opt_bool(b["truncated"], loc + ".truncated")
        _require_opt_hex(b["digest"], loc + ".digest")
        if b["status"] != BINDING_EXACT and (
            b["digest"] is not None or b["total_bytes"] is not None
        ):
            _fail(
                PROBLEM_BAD_VALUE,
                "%s carries a digest or exact total under a refusal"
                " status; a partial digest must never be emitted"
                % loc,
            )

    def observation(b, loc):
        _require_opt_str(b["completeness"], loc + ".completeness", 32)
        _require_opt_bool(
            b["supports_verification"],
            loc + ".supports_verification",
        )
        blocking_sources = b["blocking_sources"]
        if blocking_sources is not None:
            if not isinstance(blocking_sources, list):
                _fail(
                    PROBLEM_BAD_TYPE,
                    "%s.blocking_sources must be a list or null"
                    % loc,
                )
            for index, source in enumerate(blocking_sources):
                if source not in OBSERVE_DIAGNOSTIC_SOURCES:
                    _fail(
                        PROBLEM_BAD_VALUE,
                        "%s.blocking_sources[%d] %r is not an"
                        " observer diagnostic source"
                        % (loc, index, source),
                    )

    def target_task(b, loc):
        _require_opt_str(b["task_id"], loc + ".task_id", 256)
        _require_opt_str(b["task_status"], loc + ".task_status", 256)

    def review_decision(b, loc):
        _require_opt_int(b["round"], loc + ".round", minimum=1)
        decision = b["decision"]
        if decision is not None and decision not in (
            "APPROVE", "REJECT"
        ):
            _fail(
                PROBLEM_BAD_VALUE,
                "%s.decision must be APPROVE, REJECT, or null; got"
                " %r" % (loc, decision),
            )

    def review_file(b, loc):
        _validate_read_shape(b, loc)
        _require_opt_str(b["name"], loc + ".name", 512)

    def reviewer_identity(b, loc):
        _require_opt_str(b["logical"], loc + ".logical", 256)
        _require_opt_str(b["session"], loc + ".session", 256)

    def checkpoint(b, loc):
        _validate_read_shape(b, loc)

    def checkpoint_mtime(b, loc):
        _require_opt_int(b["mtime"], loc + ".mtime")
        _require_opt_int(b["size"], loc + ".size")

    def marker(b, loc):
        _require_opt_str(b["text"], loc + ".text", MAX_MARKER_CHARS)
        _require_opt_bool(b["bound_hit"], loc + ".bound_hit")
        if b["source"] not in MARKER_SOURCE_FILES:
            _fail(
                PROBLEM_BAD_VALUE,
                "%s.source must be one of the fixed marker sources"
                " %s; got %r"
                % (loc, ", ".join(MARKER_SOURCE_FILES), b["source"]),
            )
        if b["status"] == BINDING_NOT_PRODUCED and b["text"] is not (
            None
        ):
            _fail(
                PROBLEM_BAD_VALUE,
                "%s is not_produced but carries text; an absent"
                " marker never has content" % loc,
            )

    def control_policy(b, loc):
        _require_opt_hex(b["live_digest"], loc + ".live_digest")
        _require_opt_hex(b["recorded_digest"],
                         loc + ".recorded_digest")
        _require_opt_bool(b["match"], loc + ".match")

    def baseline_match(b, loc):
        _require_opt_bool(b["match"], loc + ".match")

    def protected_surface(b, loc):
        _require_opt_hex(b["digest"], loc + ".digest")
        _require_opt_int(b["file_count"], loc + ".file_count")
        _require_opt_int(b["total_bytes"], loc + ".total_bytes")
        if b["status"] != BINDING_EXACT and b["digest"] is not None:
            _fail(
                PROBLEM_BAD_VALUE,
                "%s carries a digest under a refusal status; a"
                " partial digest must never be emitted" % loc,
            )

    def control_worktree(b, loc):
        _require_opt_int(b["protected_dirty_count"],
                         loc + ".protected_dirty_count")
        _require_opt_int(b["dirty_total_count"],
                         loc + ".dirty_total_count")
        _require_opt_bool(b["clean"], loc + ".clean")

    return {
        "workflow": (
            ("status", "workflow_id", "handoff_revision"), workflow,
        ),
        "target": (
            ("status", "canonical_host", "owner", "repo",
             "canonical_url", "issue_or_pr"), target,
        ),
        "approved_baseline": (
            ("status", "commit_sha", "ref_display"), approved_baseline,
        ),
        "acceptance": (
            ("status",) + rendering.AUTHORITY_CONTENT_KEYS, acceptance,
        ),
        "delivery_authority": (("status", "value"), delivery_authority),
        "dispatch": (
            ("status", "dispatch_count", "handoff_digest_sha256"),
            dispatch,
        ),
        "live_origin": (("status", "url"), live_origin),
        "live_head": (("status", "commit_sha"), live_head),
        "changed_paths": (
            ("status", "total_count", "staged_count",
             "worktree_modified_count", "untracked_count", "listed",
             "listing_truncated", "total_bytes_lower_bound"),
            changed_paths,
        ),
        "diff": (
            ("status", "retained_bytes", "retained_text",
             "retained_text_lossy", "truncated", "total_bytes",
             "digest", "total_bytes_lower_bound"),
            diff,
        ),
        "observation": (
            ("status", "completeness", "supports_verification",
             "blocking_sources"),
            observation,
        ),
        "target_task": (
            ("status", "task_id", "task_status"), target_task,
        ),
        "review_decision": (
            ("status", "round", "decision"), review_decision,
        ),
        "review_file": (
            ("status", "read_status", "name", "byte_count", "digest",
             "text"),
            review_file,
        ),
        "reviewer_identity": (
            ("status", "logical", "session"), reviewer_identity,
        ),
        "checkpoint": (
            ("status", "read_status", "byte_count", "digest", "text"),
            checkpoint,
        ),
        "checkpoint_mtime": (
            ("status", "mtime", "size"), checkpoint_mtime,
        ),
        "test_evidence": (
            ("status", "text", "bound_hit", "source"), marker,
        ),
        "mutation_evidence": (
            ("status", "text", "bound_hit", "source"), marker,
        ),
        "control_policy": (
            ("status", "live_digest", "recorded_digest", "match"),
            control_policy,
        ),
        "baseline_match": (("status", "match"), baseline_match),
        "protected_surface": (
            ("status", "digest", "file_count", "total_bytes"),
            protected_surface,
        ),
        "control_worktree": (
            ("status", "protected_dirty_count", "dirty_total_count",
             "clean"),
            control_worktree,
        ),
    }


_BINDING_VALIDATORS = _binding_validators()

# Registry/tuple agreement is structural: a binding named in one
# place but not the other fails at import.
if tuple(sorted(_BINDING_VALIDATORS)) != tuple(
    sorted(PROJECTION_BINDINGS)
):
    raise AssertionError(
        "binding validator registry does not match"
        " PROJECTION_BINDINGS"
    )


def validate_projection(projection):
    """Validate one projection against the closed schema.

    Raises EvidenceError (actionable message + problem code) on the
    first failure. Also RECOMPUTES completeness from the bindings
    and requires agreement — a projection claiming COMPLETE over a
    refused binding (or PARTIAL over an all-exact one) can never
    validate.
    """
    location = "evidence projection"
    _require_dict(projection, location)
    version = projection.get("schema_version")
    if isinstance(version, bool) or version != (
        EVIDENCE_SCHEMA_VERSION
    ):
        _fail(
            PROBLEM_SCHEMA_VERSION,
            "%s has schema_version %r; this layer understands only"
            " %d" % (location, version, EVIDENCE_SCHEMA_VERSION),
        )
    _require_closed_keys(projection, _TOP_LEVEL_KEYS, location)
    collected_at = projection["collected_at"]
    if isinstance(collected_at, bool) or not isinstance(
        collected_at, (int, float)
    ) or collected_at < 0:
        _fail(
            PROBLEM_BAD_VALUE,
            "%s.collected_at must be a non-negative number"
            % location,
        )
    bindings = projection["bindings"]
    _require_dict(bindings, location + ".bindings")
    _require_closed_keys(
        bindings, PROJECTION_BINDINGS, location + ".bindings"
    )
    for name in PROJECTION_BINDINGS:
        loc = "%s.bindings.%s" % (location, name)
        binding = bindings[name]
        _require_dict(binding, loc)
        keys, validator = _BINDING_VALIDATORS[name]
        _require_closed_keys(binding, keys, loc)
        _require_status(binding["status"], loc + ".status")
        validator(binding, loc)
    completeness, diagnostics = projection_completeness(bindings)
    if projection["completeness"] != completeness:
        _fail(
            PROBLEM_COMPLETENESS,
            "%s.completeness is %r but the bindings recompute to"
            " %r; a claimed completeness that the bindings do not"
            " support is refused"
            % (location, projection["completeness"], completeness),
        )
    stated = projection["diagnostics"]
    if not isinstance(stated, list):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s.diagnostics must be a list" % location,
        )
    stated_names = set()
    for index, diag in enumerate(stated):
        where = "%s.diagnostics[%d]" % (location, index)
        _require_dict(diag, where)
        _require_closed_keys(
            diag, ("binding", "status", "detail"), where
        )
        if diag["binding"] not in PROJECTION_BINDINGS:
            _fail(
                PROBLEM_BAD_VALUE,
                "%s.binding %r is not a projection binding"
                % (where, diag["binding"]),
            )
        _require_status(diag["status"], where + ".status")
        _require_opt_str(
            diag["detail"], where + ".detail",
            MAX_DIAGNOSTIC_DETAIL_CHARS,
        )
        stated_names.add(diag["binding"])
    recomputed_names = {d["binding"] for d in diagnostics}
    if stated_names != recomputed_names:
        _fail(
            PROBLEM_COMPLETENESS,
            "%s.diagnostics names %s but the bindings recompute to"
            " %s; the diagnostic set must equal the failing-binding"
            " set" % (
                location, sorted(stated_names),
                sorted(recomputed_names),
            ),
        )
    return projection
