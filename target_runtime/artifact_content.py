"""The evidence library's ONE admitted CONTENT class (item 2, original 10):
a Mission Core RESULT ARTIFACT recorded with a ``REPOSITORY_PATH`` locator
and a content digest, read ON DEMAND, by reference, never by path.

What a caller names: a Mission ref (``<source_id>:mn-<hex>``), an artifact
id (``mf-<hex>``) and the journal digest the caller LISTED (the
``mission_state.report.provenance.journal_digest_sha256`` of the catalog
answer it displayed). Never a path, a root or a URL: the locator is taken
from the Mission Core record itself, and the workspace from the record's own
execution binding.

Before ANY workspace byte is read, all of this must hold, or the answer
REFUSES with its own code and no content:

- CORROBORATION: the catalog owner's last refreshed answer is still fresh
  (the served version's own freshness rule), lists the Mission exactly once
  as a Mission Core Mission whose binding is ``bound`` (the existing
  corroboration: ACTIVE, a fresh runtime snapshot, its task observed once at
  its own workspace), with a resolved, consistent task and workspace, an
  observed Mission State, and the requested journal digest as its listed
  head. Any other binding state (unbound, disappeared, ambiguous,
  unverified, not applicable) refuses and is NAMED; nothing resolves
  through a newer identity.
- THE RECORD, re-read now (one bounded, no-follow load through the store's
  own load boundary, ``mission_state.load_store_document``): the source is
  registered as Mission Core, the Mission is present, its head is STILL the
  listed digest (else ``mission_moved_since_listing``), its execution task
  and workspace are the corroborated ones, and the workspace is one
  resolved root.
- THE ARTIFACT: recorded exactly once, ``REPOSITORY_PATH`` (every other
  locator kind has no content here and is refused by kind), recorded
  ``available``, WITH a content digest (without one, nothing ties bytes at
  the path to the record: a path resemblance is never an association), and
  a locator that is a contained relative path (the composer's own rule,
  ``mission_state._locator_parts``, used unchanged).
- THE DECLARED CLASS (``declared_basis``), a SEPARATE question from
  linkage: a recorded reference with a matching digest proves the bytes ARE
  the recorded artifact; it never proves they are evidence that may be
  shown. The admission basis is what the CANONICAL RECORD DECLARES the
  artifact to be — never its path, name or extension, which its recorder
  chooses. Exactly two declarations are supported:

  * ``contract_required_artifact``: the APPROVED proof contract of the
    Mission's latest activation (re-derived by Mission Core's own
    ``store.activation_contract`` from the approved proposal, digest-bound)
    declares a required artifact with this key AND role, and pins exactly
    this content digest (Mission Core requires every required artifact to
    pin one): the human-approved contract names these very bytes as a
    deliverable;
  * ``accepted_evidence_artifact``: an evidence record of a VERIFYING kind
    (``record.SATISFYING_EVIDENCE_KINDS``), ACCEPTED by a separate operation
    and not invalidated, references this artifact.

  An artifact with neither is unclassifiable: ``artifact_class_not_declared``,
  refused before any content byte is held or read. A submitted but not
  accepted record, an invalidated one, a non-verifying kind, a contract
  digest other than the recorded one, or a contract that does not resolve
  declares nothing.
- DISCLOSURE (``disclosure_problem``), layered ON TOP of a declared class
  (it only ever refuses more): any HIDDEN component (``.herd`` — Herdr's
  private state, reviews and evidence —, ``.git``, ``.env``, any
  dot-prefixed name) is ``locator_in_private_location``; a credential-like
  name or key-material extension in ANY component is
  ``locator_credential_like``; and the content must be in a TEXT FORMAT
  shown as text (``.md .txt .html .htm .json .xml .csv .tsv`` by its final
  extension) or it is ``locator_format_not_supported``. The extension is a
  FORMAT gate only, never the class. A traversal stays its own refusal
  (``locator_not_a_contained_relative_path``).

RESIDUAL LIMIT, stated plainly: the basis covers what the canonical record
DECLARES. It cannot detect a secret written INSIDE an artifact the approved
contract or an accepted evidence record legitimately declares; no universal
secret detection is claimed. A human-approved contract that pins a file, or
an independently accepted evidence record that names one, is the authority
for showing it.

The read is the EXISTING hardened reader, unchanged
(``state_artifact.read_state_artifact`` over ``prepare``'s primitive: no
symlinked component before or after the read, no-follow leaf, regular file,
one link, at most 64 KiB, UTF-8). Then the bytes must hash to the RECORDED
digest (else ``content_changed_since_recorded``), carry no control or
bidirectional-override character, and before release the REGISTRY and the
record must be read AGAIN: the Mission's source still registered exactly as
when the read was admitted (a COMPLETED Mission's: its source, every
observing source and the whole listed source universe, as listed), the head
still at the listed digest and the corroborating answer still fresh and
unchanged; otherwise the content is DISCARDED. The owner need not refresh
for a removal, repoint or registration change to be seen. Content is returned as TEXT
with a media label (``text/html`` for an ``.html``/``.htm`` locator); the
label is presentation only: no consumer may render, execute or fetch from
it.

ACCOUNTING (the composer's reviewed ledger, ``query_v2.RefreshReadBudget``):
ONE fixed per-request budget, ``MAX_REQUEST_READ_BYTES`` = the registry bound,
two store bounds and the file bound, each plus the overrun byte (UNCHANGED).
Every read is HELD before it is made; a read that cannot be held is never
attempted (``request_read_budget_exhausted``); a successful read settles to
its exact bytes; a refused read keeps its whole hold (the composer's
candidate rule), so the charge is conservative, never under-stated. The
registry is read TWICE (before admission and before release) within that
same cap: settled reads return their unused room, and BEFORE the file is
read the remaining budget must cover the file AND both re-reads at their
bounds, or the request is refused there, before any content byte (so it
holds whenever the first registry and store reads together are at most one
store bound plus one byte). No cap is raised; the catalog refresh budget is
not touched.

Nothing here writes, registers, repairs, executes or fetches anything; it
opens no file the record does not name, and (the disclosure policy above)
no ``.herd`` state, review, transcript or evidence file, no hidden file and
no credential-like file, even when the record names it.
"""

import os

from mission import journal
from mission import record as mission_record
from mission import state as state_module
from mission import store as store_module
from mission_sources import contract
from mission_sources import contract_v2
from mission_sources import query_v2
from mission_sources import registry as registry_module
from mission_sources import sources as sources_module
from target_runtime import prepare as prepare_module
from target_runtime import state_artifact

SCHEMA = "di.evidence.artifact_content"
VERSION = 1
ANSWER_KEYS = ("schema", "version", "state", "problem", "detail",
               "mission_ref", "artifact_id", "cursor", "artifact", "content",
               "observed_at", "accounting")
ARTIFACT_KEYS = ("key", "role", "locator_kind", "locator",
                 "content_digest_sha256", "recorded_at", "admission_basis",
                 "execution_basis")
#: The EXECUTION a content read stands on (beside the declared CLASS):
#: a currently BOUND Mission (the original path, unchanged), or a COMPLETED
#: Mission under the historical execution identity its record still proves.
EXECUTION_ACTIVE = "active_binding"
EXECUTION_HISTORICAL = "historical_completed_execution"
#: The two declarations of a supported evidence class (``declared_basis``).
BASIS_CONTRACT_REQUIRED = "contract_required_artifact"
BASIS_ACCEPTED_EVIDENCE = "accepted_evidence_artifact"
CONTENT_KEYS = ("text", "byte_count", "digest_sha256", "media")
ACCOUNTING_KEYS = ("max_bytes", "charged_bytes", "reads_refused", "exhausted")

STATE_ADMITTED = "admitted"
STATE_REFUSED = "refused"
MEDIA_TEXT = "text/plain"
MEDIA_HTML = "text/html"
_HTML_SUFFIXES = (".html", ".htm")

#: The hardened reader's own bound, UNCHANGED (never raised here).
MAX_CONTENT_BYTES = prepare_module.MAX_INSTRUCTION_FILE_BYTES
#: The registry, two store loads and the file, each with its overrun byte.
MAX_REQUEST_READ_BYTES = ((registry_module.MAX_REGISTRY_BYTES + 1)
                      + 2 * (sources_module.MAX_STORE_READ_BYTES + 1)
                      + (MAX_CONTENT_BYTES + 1))
MAX_DETAIL_CHARS = 240

#: The request grammar. ``catalog_http`` re-declares it (it may not import
#: this module); parity is pinned by test.
MISSION_REF_PATTERN = r"[a-z0-9][a-z0-9._-]{0,63}:mn-[0-9a-f]{32}"
ARTIFACT_ID_PATTERN = r"mf-[0-9a-f]{32}"
CURSOR_PATTERN = r"[0-9a-f]{64}"

PROBLEM_CONTRACT_VERSION = "content_needs_contract_version_2"
PROBLEM_SHUT_DOWN = "owner_shut_down"
PROBLEM_BUSY = "content_read_in_progress"
PROBLEM_NOT_CORROBORATED = "no_fresh_catalog_answer"
PROBLEM_MISSION_NOT_LISTED = "mission_not_listed"
PROBLEM_MISSION_AMBIGUOUS = "mission_listed_more_than_once"
PROBLEM_SOURCE_KIND = "source_kind_has_no_artifact_content"
PROBLEM_NOT_BOUND = "mission_binding_not_bound"
PROBLEM_EXECUTION_UNRESOLVED = "mission_task_or_workspace_unresolved"
PROBLEM_STATE_NOT_OBSERVED = "mission_state_not_observed"
PROBLEM_CURSOR_NOT_LISTED = "cursor_not_the_listed_cursor"
PROBLEM_REGISTRY = "registry_unreadable"
PROBLEM_SOURCE_NOT_REGISTERED = "source_not_registered"
PROBLEM_STORE = "mission_store_unavailable"
PROBLEM_MISSION_ABSENT = "mission_absent_from_store"
PROBLEM_CURSOR_MOVED = "mission_moved_since_listing"
PROBLEM_BINDING_CHANGED = "mission_task_or_workspace_changed"
PROBLEM_WORKSPACE_UNRESOLVED = "workspace_not_one_resolved_root"
PROBLEM_ARTIFACT_ABSENT = "artifact_not_recorded"
PROBLEM_ARTIFACT_AMBIGUOUS = "artifact_recorded_more_than_once"
PROBLEM_LOCATOR_KIND = "locator_kind_has_no_content"
PROBLEM_RECORDED_UNAVAILABLE = "artifact_recorded_unavailable"
PROBLEM_NO_DIGEST = "artifact_has_no_recorded_digest"
PROBLEM_LOCATOR_UNSAFE = "locator_not_a_contained_relative_path"
PROBLEM_CLASS_NOT_DECLARED = "artifact_class_not_declared"
PROBLEM_PRIVATE_LOCATION = "locator_in_private_location"
PROBLEM_CREDENTIAL_LIKE = "locator_credential_like"
PROBLEM_FORMAT_UNSUPPORTED = "locator_format_not_supported"
PROBLEM_FILE_MISSING = "file_missing"
PROBLEM_OVERSIZED = "file_over_read_bound"
PROBLEM_BINARY = "content_not_text"
PROBLEM_NOT_REGULAR = "file_not_regular"
PROBLEM_ESCAPES = "path_escapes_workspace"
PROBLEM_HARDLINK = "file_hardlinked"
PROBLEM_UNREADABLE = "file_unreadable"
PROBLEM_DIGEST_MISMATCH = "content_changed_since_recorded"
PROBLEM_MOVED_DURING_READ = "mission_moved_during_read"
PROBLEM_BUDGET = "request_read_budget_exhausted"
#: Completed-Mission (historical) reads only: the listed execution identity
#: is not established (the identity's own code in the detail); the listing
#: cannot prove the task and workspace are claimed once (a source not fresh
#: or not fully listed); another listed Mission claims the same task or
#: workspace; the source registration is not the listed one; the record's
#: identity is not the listed one (before, or while, the file was read).
PROBLEM_HISTORICAL_IDENTITY = "historical_identity_not_established"
PROBLEM_HISTORICAL_UNVERIFIABLE = "historical_uniqueness_unverifiable"
PROBLEM_HISTORICAL_CONFLICT = "historical_identity_conflict"
PROBLEM_REGISTRATION_CHANGED = "source_registration_changed"
PROBLEM_HISTORICAL_CHANGED = "historical_identity_changed"
PROBLEM_HISTORICAL_CHANGED_DURING_READ = (
    "historical_identity_changed_during_read")
#: The CLOSED problem vocabulary (a consumer maps every one; no default).
PROBLEMS = (
    PROBLEM_CONTRACT_VERSION, PROBLEM_SHUT_DOWN, PROBLEM_BUSY,
    PROBLEM_NOT_CORROBORATED, PROBLEM_MISSION_NOT_LISTED,
    PROBLEM_MISSION_AMBIGUOUS, PROBLEM_SOURCE_KIND, PROBLEM_NOT_BOUND,
    PROBLEM_EXECUTION_UNRESOLVED, PROBLEM_STATE_NOT_OBSERVED,
    PROBLEM_CURSOR_NOT_LISTED, PROBLEM_REGISTRY,
    PROBLEM_SOURCE_NOT_REGISTERED, PROBLEM_STORE, PROBLEM_MISSION_ABSENT,
    PROBLEM_CURSOR_MOVED, PROBLEM_BINDING_CHANGED,
    PROBLEM_WORKSPACE_UNRESOLVED, PROBLEM_ARTIFACT_ABSENT,
    PROBLEM_ARTIFACT_AMBIGUOUS, PROBLEM_LOCATOR_KIND,
    PROBLEM_RECORDED_UNAVAILABLE, PROBLEM_NO_DIGEST, PROBLEM_LOCATOR_UNSAFE,
    PROBLEM_CLASS_NOT_DECLARED, PROBLEM_PRIVATE_LOCATION,
    PROBLEM_CREDENTIAL_LIKE, PROBLEM_FORMAT_UNSUPPORTED,
    PROBLEM_FILE_MISSING, PROBLEM_OVERSIZED, PROBLEM_BINARY,
    PROBLEM_NOT_REGULAR, PROBLEM_ESCAPES, PROBLEM_HARDLINK,
    PROBLEM_UNREADABLE, PROBLEM_DIGEST_MISMATCH, PROBLEM_MOVED_DURING_READ,
    PROBLEM_BUDGET, PROBLEM_HISTORICAL_IDENTITY,
    PROBLEM_HISTORICAL_UNVERIFIABLE, PROBLEM_HISTORICAL_CONFLICT,
    PROBLEM_REGISTRATION_CHANGED, PROBLEM_HISTORICAL_CHANGED,
    PROBLEM_HISTORICAL_CHANGED_DURING_READ,
)
#: The registration fields a listing's source block and the registry share.
_REGISTRATION_FIELDS = ("source_id", "kind", "store_dir", "producer",
                        "max_age_seconds")

#: ONE execution, observed through a second source kind (the Lead's
#: same-execution decision). The ONE supported OBSERVATION class is the
#: task-native reader's (``target_runtime.herdr_task_source``; parity pinned
#: by test): a registered ``task_native`` source, model ``herdr.task/1``,
#: recorded producer schema ``di.catalog.herdr_task`` version 1, identity
#: source ``herdr.task_json``. Such a row OBSERVES an execution; it records no
#: Mission and supplies NO read authority.
OBSERVATION_KIND = contract.KIND_TASK_NATIVE
OBSERVATION_LIFECYCLE_MODEL = "herdr.task/1"
OBSERVATION_SCHEMA = "di.catalog.herdr_task"
OBSERVATION_SCHEMA_VERSION = 1
OBSERVATION_IDENTITY_SOURCE = "herdr.task_json"
#: The native task statuses that do NOT contradict a COMPLETED canonical
#: execution (whose verification held "target stopped" and "target
#: succeeded"): Herdr's successful terminal statuses. Any other status —
#: active, failed, abandoned, aborted, error, or an unrecognised literal —
#: contradicts it, or cannot be shown not to, and the row competes.
OBSERVATION_COMPLETED_STATUSES = ("COMPLETE", "COMPLETED")

#: The TEXT FORMATS content is shown in (a format gate on top of a declared
#: class, never the class itself): the final extension, lower-cased.
TEXT_FORMATS = (".md", ".txt", ".html", ".htm", ".json", ".xml", ".csv",
                ".tsv")
#: Refused in ANY component (lower-cased substring), before the class is
#: decided: names that suggest credentials or key material. These only ever
#: refuse; they never admit.
CREDENTIAL_NAME_PARTS = (
    "secret", "credential", "password", "passwd", "token", "apikey",
    "api_key", "api-key", "private_key", "private-key", "privatekey",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "keystore", "keychain",
)
KEY_MATERIAL_EXTENSIONS = (
    ".pem", ".key", ".p12", ".pfx", ".der", ".crt", ".cer", ".gpg", ".asc",
    ".kdbx", ".jks", ".ppk", ".ovpn",
)

#: Every status of the hardened reader but ``read``, mapped EXPLICITLY (a
#: new reader status without an entry fails a test: no silent default).
READ_REFUSALS = {
    prepare_module.INSTRUCTION_ABSENT: PROBLEM_FILE_MISSING,
    prepare_module.INSTRUCTION_REFUSED_OVER_BOUND: PROBLEM_OVERSIZED,
    prepare_module.INSTRUCTION_REFUSED_UNREADABLE: PROBLEM_UNREADABLE,
    prepare_module.INSTRUCTION_REFUSED_NON_UTF8: PROBLEM_BINARY,
    prepare_module.INSTRUCTION_REFUSED_NOT_REGULAR: PROBLEM_NOT_REGULAR,
    prepare_module.INSTRUCTION_REFUSED_ESCAPES: PROBLEM_ESCAPES,
    prepare_module.INSTRUCTION_REFUSED_HARDLINK: PROBLEM_HARDLINK,
}

#: Characters admitted text may not carry: C0 controls but tab, newline and
#: carriage return; DEL; C1 controls; and the bidirectional embedding,
#: override and isolate controls (they reorder what a reader SEES).
_REFUSED_CHARACTERS = frozenset(
    [chr(c) for c in range(0x00, 0x20) if c not in (0x09, 0x0A, 0x0D)]
    + [chr(0x7F)] + [chr(c) for c in range(0x80, 0xA0)]
    + [chr(c) for c in range(0x202A, 0x202F)]
    + [chr(c) for c in range(0x2066, 0x206A)])


class RequestReadBudget(query_v2.RefreshReadBudget):
    """ONE content request's read ledger: the catalog's reviewed refresh
    ledger (hold before I/O, settle exact on success, keep the hold on a
    failure), over this request's own fixed total."""

    def __init__(self):
        query_v2.RefreshReadBudget.__init__(self, MAX_REQUEST_READ_BYTES)


class _Refused(Exception):
    def __init__(self, problem, detail):
        super(_Refused, self).__init__(problem)
        self.problem = problem
        self.detail = detail


def _accounting(budget):
    if budget is None:
        return {"max_bytes": MAX_REQUEST_READ_BYTES, "charged_bytes": 0,
                "reads_refused": 0, "exhausted": False}
    summary = budget.summary()
    return dict((key, summary[key]) for key in ACCOUNTING_KEYS)


def _answer(state, problem, detail, mission_ref, artifact_id, cursor,
            artifact, content, observed_at, budget):
    return {
        "schema": SCHEMA, "version": VERSION, "state": state,
        "problem": problem,
        "detail": None if detail is None else detail[:MAX_DETAIL_CHARS],
        "mission_ref": mission_ref, "artifact_id": artifact_id,
        "cursor": cursor, "artifact": artifact, "content": content,
        "observed_at": observed_at, "accounting": _accounting(budget)}


def refusal(problem, detail, mission_ref, artifact_id, cursor,
            observed_at=None, budget=None):
    """A refused answer: no artifact, no content."""
    if problem not in PROBLEMS:
        raise ValueError("an undeclared problem code: %r" % (problem,))
    return _answer(STATE_REFUSED, problem, detail, mission_ref, artifact_id,
                   cursor, None, None, observed_at, budget)


def _budget_exhausted():
    return _Refused(PROBLEM_BUDGET, "this request's fixed read budget cannot"
                    " hold the next read; it was not attempted")


def _corroborated(answer, freshness, now, mission_ref, cursor):
    """The corroborated binding of ``mission_ref`` in the owner's last
    refreshed answer, or ``_Refused``."""
    if answer is None or answer.get("response") is None:
        raise _Refused(PROBLEM_NOT_CORROBORATED, "no refreshed catalog answer"
                       " is held; nothing corroborates the Mission yet")
    response = answer["response"]
    health, problem = freshness(response, now)
    if health != contract.HEALTH_FRESH:
        raise _Refused(PROBLEM_NOT_CORROBORATED, "the last catalog answer is"
                       " %s (%s) now; it corroborates nothing"
                       % (health, problem))
    listed = [m for m in response["missions"]
              if m["mission_ref"] == mission_ref]
    if not listed:
        raise _Refused(PROBLEM_MISSION_NOT_LISTED, "the last catalog answer"
                       " does not list this Mission")
    if len(listed) > 1:
        raise _Refused(PROBLEM_MISSION_AMBIGUOUS, "the last catalog answer"
                       " lists this Mission more than once")
    mission = listed[0]
    if mission["lifecycle"]["model"] != contract.LIFECYCLE_MODEL_MISSION_CORE:
        raise _Refused(PROBLEM_SOURCE_KIND, "only a Mission Core record names"
                       " result artifacts with content; this Mission is %s"
                       % mission["lifecycle"]["model"])
    binding = mission["binding"]
    historical = binding["state"] != contract.BINDING_BOUND
    if historical and not (
            mission["lifecycle"]["state"] == mission_record.STATE_COMPLETED
            and binding["state"] == contract.BINDING_NOT_APPLICABLE
            and binding["reason"] == contract_v2.PROBLEM_MISSION_NOT_ACTIVE):
        raise _Refused(PROBLEM_NOT_BOUND, "the Mission's binding is %s (%s);"
                       " content is read only for a currently bound Mission,"
                       " or a COMPLETED one under its recorded execution"
                       % (binding["state"], binding["reason"]))
    execution = mission["execution"]
    if execution["task_id"] is None or execution[
            "workspace_realpath"] is None or not execution["consistent"] or (
                execution["record_ambiguous"]) or (
                    not historical
                    and binding["task_id"] != execution["task_id"]) or (
                        historical and not (execution["run_started"]
                                            and execution["receipt_recorded"])):
        raise _Refused(PROBLEM_EXECUTION_UNRESOLVED, "the Mission's task and"
                       " workspace are not resolved consistently")
    block = mission["mission_state"]
    if block["state"] != contract_v2.MISSION_STATE_OBSERVED or (
            block["report"] is None):
        raise _Refused(PROBLEM_STATE_NOT_OBSERVED, "the Mission State was not"
                       " observed (%s)" % block["problem"])
    journal_digest = block["report"]["provenance"]["journal_digest_sha256"]
    if not historical:
        if journal_digest != cursor:
            raise _Refused(PROBLEM_CURSOR_NOT_LISTED, "the requested journal"
                           " digest is not the head the last answer lists")
        return {"basis": EXECUTION_ACTIVE,
                "source_id": mission["source_id"],
                "native_id": mission["native_id"],
                "task_id": execution["task_id"],
                "workspace": execution["workspace_realpath"],
                "journal": journal_digest}
    identity = block.get(contract_v2.EXECUTION_IDENTITY_KEY)
    if identity is None or identity["state"] != (
            contract_v2.EXECUTION_IDENTITY_ESTABLISHED):
        raise _Refused(PROBLEM_HISTORICAL_IDENTITY, "the COMPLETED Mission's"
                       " execution identity is not established (%s)"
                       % ("not reported" if identity is None
                          else identity["problem"]))
    if identity["cursor_sha256"] != cursor:
        raise _Refused(PROBLEM_CURSOR_NOT_LISTED, "the requested cursor is not"
                       " the historical cursor the last answer lists")
    return {"basis": EXECUTION_HISTORICAL,
            "source_id": mission["source_id"],
            "native_id": mission["native_id"],
            "task_id": execution["task_id"],
            "workspace": execution["workspace_realpath"],
            "journal": journal_digest,
            "identity": identity["identity_sha256"],
            "registration": _listed_registration(response,
                                                 mission["source_id"]),
            "universe": _universe(response["sources"]),
            "observations": _claimed_once(response, mission)}


def _listed_registration(response, source_id):
    """The listing's registration of ``source_id``, or ``_Refused``: the
    source must be listed once, FRESH, with every one of its Missions."""
    blocks = [b for b in response["sources"] if b["source_id"] == source_id]
    if len(blocks) != 1:
        raise _Refused(PROBLEM_HISTORICAL_UNVERIFIABLE, "the listing does not"
                       " carry exactly one block for the Mission's source")
    return dict((field, blocks[0][field]) for field in _REGISTRATION_FIELDS)


def _registration(entry):
    """The fields that identify one source's registration."""
    return dict((field, entry[field]) for field in _REGISTRATION_FIELDS)


def _universe(entries):
    """EVERY source a listing or the registry names, as registrations in
    ``source_id`` order (a duplicate stays, and so differs)."""
    return sorted((_registration(entry) for entry in entries),
                  key=lambda registration: registration["source_id"])


def _registrations_hold(registered, bound, when):
    """``_Refused`` unless the registry document ``registered`` (read NOW)
    registers the COMPLETED Mission's source and every observing source
    EXACTLY as the listing named them (``source_registration_changed``), and
    registers no universe other than the LISTED one: a source added,
    removed or changed since the listing is one the uniqueness check never
    saw, so uniqueness is unverifiable until a refresh lists it
    (``historical_uniqueness_unverifiable``); an unlisted source is never
    assumed benign."""
    for listed in [bound["registration"]] + [
            o["registration"] for o in bound["observations"]]:
        found = [s for s in registered["sources"]
                 if s["source_id"] == listed["source_id"]]
        if len(found) != 1 or _registration(found[0]) != listed:
            raise _Refused(PROBLEM_REGISTRATION_CHANGED, "source %s is not"
                           " registered as the listing named it %s"
                           % (listed["source_id"], when))
    now = _universe(registered["sources"])
    if now != bound["universe"]:
        listed_ids = set(r["source_id"] for r in bound["universe"])
        now_ids = set(r["source_id"] for r in now)
        raise _Refused(PROBLEM_HISTORICAL_UNVERIFIABLE, "the registered"
                       " sources are not the listed ones %s (%d added, %d"
                       " removed, %d changed); uniqueness was shown over the"
                       " listed sources only: refresh and select it again"
                       % (when, len(now_ids - listed_ids),
                          len(listed_ids - now_ids),
                          sum(1 for r in now if r["source_id"] in listed_ids
                              and r not in bound["universe"])))


def _covers_the_read_and_its_revalidation(budget):
    """Whether the fixed per-request budget can still hold the file read AND
    the re-reads before release (the registry, a fixed hold, and the store),
    each at its bound with its overrun byte, as they would be held."""
    return budget.remaining >= (
        (MAX_CONTENT_BYTES + 1) + (registry_module.MAX_REGISTRY_BYTES + 1)
        + (sources_module.MAX_STORE_READ_BYTES + 1))


def _competes(response, mission, other):
    """None when ``other`` is a supported task-native OBSERVATION of
    ``mission``'s OWN execution; otherwise why it is a competing claim.
    Decided ONLY from the typed facts the producers emit (registered source
    kind, lifecycle model, recorded producer schema, identity source, task,
    workspace, consistency, status) — never from a source alias, display
    name, label, timestamp or native-id substring, and never by inferring a
    fact a row lacks."""
    try:
        blocks = [b for b in response["sources"]
                  if b["source_id"] == other["source_id"]]
        if len(blocks) != 1 or blocks[0]["kind"] != OBSERVATION_KIND:
            return ("it is a claim of a %s source, not an observation"
                    % (blocks[0]["kind"] if len(blocks) == 1 else
                       "not-singly-listed"))
        recorded = other["producer"]["recorded"]
        execution = other["execution"]
        if other["lifecycle"]["model"] != OBSERVATION_LIFECYCLE_MODEL or (
                type(recorded) is not dict) or recorded.get(
                    "schema") != OBSERVATION_SCHEMA or recorded.get(
                        "schema_version") != OBSERVATION_SCHEMA_VERSION or (
                            execution["identity_source"]
                            != OBSERVATION_IDENTITY_SOURCE):
            return "its provenance is not the supported task-native observation"
        if execution["task_id"] != mission["execution"]["task_id"] or (
                execution["workspace_realpath"]
                != mission["execution"]["workspace_realpath"]):
            return "it names a different task or a different workspace"
        if execution["consistent"] is not True or execution[
                "record_ambiguous"] is not False or execution[
                    "run_started"] is not True:
            return "its observation is not consistent and unambiguous"
        if other["lifecycle"]["state"] not in OBSERVATION_COMPLETED_STATUSES:
            return ("its task status %r contradicts the completed execution"
                    % str(other["lifecycle"]["state"])[:40])
    except (KeyError, TypeError, AttributeError):
        return "it lacks the typed facts of an observation"
    return None


def _observation(response, other):
    """What a qualifying observation is re-checked by: its row and its
    source's listed registration."""
    return {"mission_ref": other["mission_ref"],
            "registration": _listed_registration(response,
                                                 other["source_id"]),
            "state": other["lifecycle"]["state"],
            "task_id": other["execution"]["task_id"],
            "workspace": other["execution"]["workspace_realpath"]}


def _claimed_once(response, mission):
    """The listed OBSERVATIONS of this execution, or ``_Refused``: no OTHER
    listed row CLAIMS this Mission's task or workspace, decided only over a
    COMPLETE listing (every source fresh and fully listed). Uniqueness is
    never inferred from a partial or unavailable one.

    DUPLICATE OBSERVATIONS: EVERY other row sharing the task OR the
    workspace is judged on its own (``_competes``); one that is not a
    supported observation of this very execution refuses the read. Rows
    that ARE are never chosen among, merged or treated as authority: any
    number of them may observe the one execution (each agreeing exactly on
    task and workspace, and none contradicting it), and all of them are
    re-checked before release. A genuine second canonical claim (Mission
    Core or Workflow), even with an identical task and path, always
    refuses."""
    for source in response["sources"]:
        listed = sum(1 for m in response["missions"]
                     if m["source_id"] == source["source_id"])
        if source["health"] != contract.HEALTH_FRESH or (
                listed != source["record_count"]):
            raise _Refused(PROBLEM_HISTORICAL_UNVERIFIABLE, "source %s is %s"
                           " with %s of %s Missions listed; the task and"
                           " workspace cannot be shown to be claimed once"
                           % (source["source_id"], source["health"], listed,
                              source["record_count"]))
    task = mission["execution"]["task_id"]
    workspace = mission["execution"]["workspace_realpath"]
    observations = []
    for other in response["missions"]:
        if other["mission_ref"] == mission["mission_ref"]:
            continue
        if task != other["execution"]["task_id"] and workspace != other[
                "execution"]["workspace_realpath"]:
            continue
        why = _competes(response, mission, other)
        if why is not None:
            raise _Refused(PROBLEM_HISTORICAL_CONFLICT, "another listed row"
                           " (%s) shares the task or workspace and %s"
                           % (other["mission_ref"], why))
        observations.append(_observation(response, other))
    return sorted(observations, key=lambda o: o["mission_ref"])


def _same_identity(mission_state, source, mission, head, bound, cursor,
                   problem, when):
    """``_Refused`` (``problem``) unless the record's execution identity NOW
    is established, is the listed one, and with ``head`` makes ``cursor``."""
    found = mission_state.execution_identity(source, mission)
    if found["state"] != contract_v2.EXECUTION_IDENTITY_ESTABLISHED or (
            found["identity_sha256"] != bound["identity"]) or (
                mission_state.historical_cursor(
                    head, found["identity_sha256"]) != cursor):
        raise _Refused(problem, "the COMPLETED Mission's execution identity"
                       " (%s) is not the listed one %s"
                       % (found["problem"] or "changed", when))


def _registry_now(registry, budget):
    """The registry document, read ONCE per request within its hold."""
    hold = budget.hold(registry_module.MAX_REGISTRY_BYTES, fixed=True)
    if hold is None:
        raise _budget_exhausted()
    try:
        document, count = registry.load_measured(absent_is_empty=False)
        hold.settle(count)
    except registry_module.RegistryError as exc:
        raise _Refused(PROBLEM_REGISTRY, "the source registry could not be"
                       " read (%s)" % exc.problem)
    finally:
        if not hold.closed:
            hold.failed()
    return document


def _registered_source(document, source_id):
    found = [s for s in document["sources"] if s["source_id"] == source_id]
    if not found:
        raise _Refused(PROBLEM_SOURCE_NOT_REGISTERED, "the Mission's source is"
                       " not registered now")
    if found[0]["kind"] != contract.KIND_MISSION_CORE:
        raise _Refused(PROBLEM_SOURCE_KIND, "the source is registered as %s"
                       % found[0]["kind"])
    return found[0]


def _load(mission_state, store_dir, budget):
    try:
        return mission_state.load_store_document(store_dir, budget)
    except mission_state._BudgetExhausted:
        raise _budget_exhausted()
    except mission_state._Unavailable as exc:
        raise _Refused(PROBLEM_STORE, exc.reason)


def _head(document, native_id):
    """``(mission, state, head journal digest)``, or ``_Refused``."""
    mission = document["missions"].get(native_id)
    if mission is None:
        raise _Refused(PROBLEM_MISSION_ABSENT, "the Mission is not in its"
                       " store now")
    found = document["mission_state"].get(native_id)
    state = found if found is not None else state_module.new_state_record(
        native_id, 0)
    return mission, state, journal.head_cursor(mission, state)[
        "journal_digest_sha256"]


def _artifact(state, artifact_id):
    found = [a for a in state["artifacts"] if a["artifact_id"] == artifact_id]
    if not found:
        raise _Refused(PROBLEM_ARTIFACT_ABSENT, "the Mission records no"
                       " artifact with this id")
    if len(found) > 1:
        raise _Refused(PROBLEM_ARTIFACT_AMBIGUOUS, "the Mission records this"
                       " artifact id more than once")
    artifact = found[0]
    if artifact["locator_kind"] != state_module.LOCATOR_KIND_REPOSITORY_PATH:
        raise _Refused(PROBLEM_LOCATOR_KIND, "a %s locator names no file in"
                       " the workspace; it has no content here"
                       % artifact["locator_kind"])
    if artifact["available"] is not True:
        raise _Refused(PROBLEM_RECORDED_UNAVAILABLE, "the artifact is recorded"
                       " as not available")
    if artifact["content_digest_sha256"] is None:
        raise _Refused(PROBLEM_NO_DIGEST, "no digest is recorded, so nothing"
                       " ties bytes at the path to this record")
    return artifact


def disclosure_problem(parts):
    """``(problem, detail)`` when the contained locator ``parts`` may not be
    disclosed, else None. Pure: no filesystem access."""
    for part in parts:
        if part.startswith("."):
            return (PROBLEM_PRIVATE_LOCATION, "the locator passes through"
                    " %r, a hidden location%s; its content is never shown"
                    % (part[:40], " (Herdr's private state, reviews and"
                       " evidence)" if part == ".herd" else ""))
    for part in parts:
        lowered = part.lower()
        if any(word in lowered for word in CREDENTIAL_NAME_PARTS) or (
                lowered.endswith(KEY_MATERIAL_EXTENSIONS)):
            return (PROBLEM_CREDENTIAL_LIKE, "the locator component %r looks"
                    " like credentials or key material; its content is never"
                    " shown" % part[:40])
    if not parts[-1].lower().endswith(TEXT_FORMATS):
        return (PROBLEM_FORMAT_UNSUPPORTED, "content is shown only in a text"
                " format (%s); this file is not one" % " ".join(TEXT_FORMATS))
    return None


def declared_basis(document, mission, state, artifact):
    """The sorted declarations of a supported evidence class the canonical
    record makes for ``artifact`` (see the module); empty when none. Pure
    over the loaded document: nothing is read."""
    bases = []
    activation = state_module.latest_activation(state)
    if activation is not None and artifact["key"] is not None:
        try:
            contract = store_module.activation_contract(
                document, mission, activation, "activation")
        except mission_record.MissionError:
            contract = None
        if contract is not None and any(
                required["key"] == artifact["key"]
                and required["role"] == artifact["role"]
                and required["expected_content_digest_sha256"] == artifact[
                    "content_digest_sha256"]
                for required in contract["required_artifacts"]):
            bases.append(BASIS_CONTRACT_REQUIRED)
    if any(state_module.is_accepted(evidence)
           and evidence["kind"] in mission_record.SATISFYING_EVIDENCE_KINDS
           and artifact["artifact_id"] in evidence["artifact_ids"]
           for evidence in state["evidence"]):
        bases.append(BASIS_ACCEPTED_EVIDENCE)
    return sorted(bases)


def _text_problem(text):
    for character in text:
        if character in _REFUSED_CHARACTERS:
            return ("the file carries control or bidirectional-override"
                    " characters (U+%04X); it is not shown as text"
                    % ord(character))
    return None


def _media(locator):
    return MEDIA_HTML if locator.lower().endswith(_HTML_SUFFIXES) \
        else MEDIA_TEXT


def _read_file(root, parts, budget):
    hold = budget.hold(MAX_CONTENT_BYTES, fixed=True)
    if hold is None:
        raise _budget_exhausted()
    status = count = digest = text = None
    try:
        status, count, digest, text = state_artifact.read_state_artifact(
            root, parts[:-1], parts[-1])
    finally:
        # A digest means the reader returned exactly ``count`` bytes; any
        # other outcome keeps the whole hold (the composer's rule).
        if digest is not None and type(count) is int:
            hold.settle(count)
        else:
            hold.failed()
    if status != prepare_module.INSTRUCTION_READ:
        raise _Refused(READ_REFUSALS[status], "the hardened reader refused"
                       " the file (%s)" % status)
    return digest, count, text


def _admit(mission_state, registry, corroboration, freshness, clock,
           mission_ref, artifact_id, cursor, budget):
    bound = _corroborated(corroboration(), freshness, clock(), mission_ref,
                          cursor)
    historical = bound["basis"] == EXECUTION_HISTORICAL
    registered = _registry_now(registry, budget)
    source = _registered_source(registered, bound["source_id"])
    if historical:
        # The Mission's source AND every observing source registered NOW
        # exactly as the listing named them, and NO other source universe
        # than the listed one.
        _registrations_hold(registered, bound, "since it was listed")
    admitted = _registration(source)
    document = _load(mission_state, source["store_dir"], budget)
    mission, state, head = _head(document, bound["native_id"])
    if head != bound["journal"]:
        raise _Refused(PROBLEM_CURSOR_MOVED, "the Mission's journal moved"
                       " since it was listed; refresh and select it again")
    if historical:
        # The journal head does not cover the run, the source registration
        # or the lifecycle: the IDENTITY is compared too, never resolved
        # through a newer one.
        _same_identity(mission_state, source, mission, head, bound, cursor,
                       PROBLEM_HISTORICAL_CHANGED, "since it was listed")
    execution = sources_module._mission_core_view(source, mission)[
        "execution"]
    if (execution["task_id"], execution["workspace_realpath"]) != (
            bound["task_id"], bound["workspace"]) or not (
                execution["consistent"]):
        raise _Refused(PROBLEM_BINDING_CHANGED, "the record's task or"
                       " workspace is not the corroborated one")
    root = bound["workspace"]
    if not os.path.isabs(root) or os.path.realpath(root) != root:
        raise _Refused(PROBLEM_WORKSPACE_UNRESOLVED, "the workspace is not"
                       " one resolved root")
    artifact = _artifact(state, artifact_id)
    parts = mission_state._locator_parts(artifact["locator"])
    if parts is None:
        raise _Refused(PROBLEM_LOCATOR_UNSAFE, "the recorded locator is not a"
                       " contained relative path; it is never opened")
    # LINKAGE is not a CLASS, and a class is not DISCLOSURE: both decided
    # BEFORE any content byte is held or read, however genuine the record
    # and its digest are.
    basis = declared_basis(document, mission, state, artifact)
    if not basis:
        raise _Refused(PROBLEM_CLASS_NOT_DECLARED, "the record does not declare"
                       " this artifact a supported evidence class: it is"
                       " neither a required artifact the approved contract"
                       " pins nor named by an accepted verifying evidence"
                       " record")
    refused = disclosure_problem(parts)
    if refused is not None:
        raise _Refused(*refused)
    # The content is released only after the registry AND the store are
    # read again: when the budget cannot cover that at their bounds, refuse
    # NOW, before any content byte is held or read (no read is skipped).
    if not _covers_the_read_and_its_revalidation(budget):
        budget.note_refused()
        raise _budget_exhausted()
    digest, count, text = _read_file(root, parts, budget)
    if digest != artifact["content_digest_sha256"]:
        raise _Refused(PROBLEM_DIGEST_MISMATCH, "the file's sha256 is not the"
                       " recorded digest; it is not the recorded artifact")
    problem = _text_problem(text)
    if problem is not None:
        raise _Refused(PROBLEM_BINARY, problem)
    # REVALIDATE before anything is returned: the CURRENT registrations
    # (the registry read AGAIN, never the copy read before the file), the
    # record again (load 2) from the source registered NOW, then the
    # corroborating answer at the clock NOW. The owner need not have
    # refreshed: a removal, repoint or change is seen here.
    during = "while the file was read; the content is discarded"
    later_registry = _registry_now(registry, budget)
    try:
        source_now = _registered_source(later_registry, bound["source_id"])
    except _Refused as refused:
        raise _Refused(refused.problem, "%s, %s" % (refused.detail, during))
    if _registration(source_now) != admitted:
        raise _Refused(PROBLEM_REGISTRATION_CHANGED, "the Mission's source"
                       " registration changed %s" % during)
    if historical:
        _registrations_hold(later_registry, bound, during)
    later, _, after = _head(_load(mission_state, source_now["store_dir"],
                                  budget), bound["native_id"])
    if after != bound["journal"]:
        raise _Refused(PROBLEM_MOVED_DURING_READ, "the Mission's journal moved"
                       " while the file was read; the content is discarded")
    if historical:
        _same_identity(mission_state, source_now, later, after, bound, cursor,
                       PROBLEM_HISTORICAL_CHANGED_DURING_READ, during)
    if _corroborated(corroboration(), freshness, clock(), mission_ref,
                     cursor) != bound:
        raise _Refused(PROBLEM_BINDING_CHANGED, "the corroborating binding"
                       " changed while the file was read")
    return (dict([(key, artifact[key]) for key in ARTIFACT_KEYS
                  if key not in ("admission_basis", "execution_basis")]
                 + [("admission_basis", basis),
                    ("execution_basis", bound["basis"])]),
            {"text": text, "byte_count": count, "digest_sha256": digest,
             "media": _media(artifact["locator"])})


def read(registry, corroboration, freshness, clock, mission_ref, artifact_id,
         cursor):
    """One content request; ALWAYS an answer (admitted, or refused with its
    code and an honest read account). ``corroboration`` returns the owner's
    last refreshed answer (or None) each time it is called; ``freshness`` is
    the served version's consumer operation; ``clock`` the owner's clock."""
    # Version 2 only: Mission Core's service and the delivery adapter load
    # here, never in a version 1 process (the accepted import boundary).
    from target_runtime import mission_state
    budget = RequestReadBudget()
    try:
        artifact, content = _admit(mission_state, registry, corroboration,
                                   freshness, clock, mission_ref,
                                   artifact_id, cursor, budget)
    except _Refused as exc:
        return refusal(exc.problem, exc.detail, mission_ref, artifact_id,
                       cursor, clock(), budget)
    return _answer(STATE_ADMITTED, None, None, mission_ref, artifact_id,
                   cursor, artifact, content, clock(), budget)
