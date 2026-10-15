"""The Mission State COMPOSER of query contract version 2: Mission Core's OWN
read-only observation report (``MissionService.observe``) for each Mission
Core mission, over observation inputs this module materializes from
already-admitted reads. It lives OUTSIDE ``mission/`` on purpose: it hands
Mission Core plain validated data, exactly the seam ``normalize_inputs``
exists for.

READ-ONLY, structurally. Mission Core is reached through ONE call,
``MissionService.observe``, over ``ReadOnlyMissionStore``, whose ``load`` is
one bounded, no-follow read of the registered store file through the store's
OWN load boundary (``mission.store.accept_loaded_document``) and whose
``lock`` and ``save`` REFUSE. The delivery layer is reached through ONE
call, ``validated_receipt`` (pure validation of a document this module
loaded); never an attesting, recording or transport path. Nothing here
writes, registers, mints, reserves or repairs anything.

THE CURSOR IS SAMPLED FIRST. ``sample`` reads each registered Mission Core
store ONCE (load 1) BEFORE the runtime capture and before ANY report is
collected, and takes every mission's head cursor from it. ``compose``, after
every report of a source is collected, reads the store ONCE more (load 2)
and runs ``observe`` over it with the SAMPLED cursor passed UNCHANGED, so a
head that moved during collection is reported ``moved`` and those reports
contradict but never resolve. A mission with no sampled cursor is observed
with none (its reports can never resolve).

The four kinds, each REPORTED only on its own admitted read, otherwise
``{"unavailable": <reason>}`` (never ``unknown``, never coerced):

- task: the capture's own read of the mission's root ``task.json`` (the
  chain observation), only when that root was observed AND its current task
  is the mission's task. The status maps by EXACT literal into
  ACTIVE / BLOCKED / COMPLETE / FAILED / NOT_STARTED; every other literal
  (COMPLETED, ABANDONED, IDLE, ERROR, MALFORMED, anything) refuses.
- review: the capture's admitted canonical review record, same agreement.
  An admitted record whose gate PARSED a verdict reports APPROVE or REJECT;
  an UNPARSED gate (MISSING / MALFORMED) is unavailable, stated as "no valid
  token was parsed, not a rejection"; a task that is not ACTIVE may carry
  stale review fields, so it is unavailable too; a task that records no
  review while ACTIVE reports NONE. Review and candidate are INDEPENDENT
  facts: nothing binds a review round to candidate bytes.
- candidate: every recorded ``REPOSITORY_PATH`` artifact locator under the
  mission's own workspace, re-read and re-hashed through the hardened
  state reader (``state_artifact``; contained relative paths only, 64 KiB);
  reported only when every one was read. ``baseline_digest_sha256`` is ALWAYS
  None: no admitted read observes a baseline, and copying the recorded
  anchor would assert the very no-drift it is there to test.
- delivery: only through an ATTESTED receipt artifact's marker (delivery id
  and step) and only from an explicitly registered delivery store (bounded,
  private-file read); ``validated_receipt`` then decides ABSENT / INVALID,
  and Mission artifact linkage VALID / AMBIGUOUS. A validated receipt with
  no matching registered artifact is unavailable, never ABSENT.
"""

import copy
import os
import stat

from mission import journal, record, reconciliation, service, state, store
from mission_sources import contract_v2, fileread, sources
from pr_delivery import authorization, mission_parent
from target_runtime import prepare as prepare_module
from target_runtime import state_artifact

#: The delivery store file (``pr_delivery.store.STORE_FILE_NAME``) and its
#: own schema version; re-declared, pinned equal by test, because this
#: module may not import the store (it constructs nothing of pr_delivery).
DELIVERY_STORE_FILE = "pr_delivery.json"
DELIVERY_STORE_SCHEMA_VERSION = 1
MAX_DELIVERY_STORE_BYTES = 4194304
MAX_LOCATOR_PARTS = 16
_OPEN_MODE_BITS = 0o077
#: The task status literals the report vocabulary admits, EXACTLY.
TASK_REPORTS = reconciliation.TASK_REPORTS
#: Herdr literals known to fall OUTSIDE it: refused, named.
KNOWN_UNMAPPED_TASK_STATUSES = ("COMPLETED", "ABANDONED", "IDLE", "ERROR",
                                "MALFORMED")


class ReadOnlyStoreError(Exception):
    """A write was asked of the read-only store; nothing was written."""


class _Unavailable(Exception):
    def __init__(self, reason):
        super(_Unavailable, self).__init__(reason)
        self.reason = reason


class _BudgetExhausted(_Unavailable):
    """The SHARED refresh read budget could not cover this read."""


#: The reason text every budget-exhausted read carries (its stable code).
BUDGET_EXHAUSTED = "refresh_read_budget_exhausted"


#: A candidate re-hash read's FIXED bound (the hardened reader pulls at
#: most this plus the one byte that detects an overrun).
_CANDIDATE_READ_BOUND = prepare_module.MAX_INSTRUCTION_FILE_BYTES


def _budget_exhausted():
    return _BudgetExhausted("the shared refresh read budget is exhausted (%s)"
                            % BUDGET_EXHAUSTED)


def _bounded_read(path, bound, budget):
    """``fileread.read_private_file`` of at most ``bound`` bytes, HELD
    against ``budget`` (a ``query_v2`` refresh stage, or the shared refresh
    budget itself) when one is given: refused WITHOUT reading when it cannot
    be held; a
    success settles to its exact bytes; a failure keeps its whole hold
    unless refused before any byte was read. Raises ``fileread.FileRefused``
    or ``_BudgetExhausted``."""
    if budget is None:
        return fileread.read_private_file(path, bound)
    hold = budget.hold(bound)
    if hold is None:
        raise _budget_exhausted()
    try:
        data = fileread.read_private_file(path, hold.allowance)
    except fileread.FileRefused as exc:
        hold.refused(exc.problem)
        if exc.problem == fileread.PROBLEM_FILE_TOO_LARGE and (
                hold.allowance < bound):
            budget.note_refused()
            raise _budget_exhausted()
        raise
    hold.settle(len(data))
    return data


def load_store_document(store_dir, budget=None):
    """ONE bounded, no-follow read of a Mission Core store file, validated
    by the store's OWN load boundary; raises ``_Unavailable`` (or
    ``_BudgetExhausted`` when the shared ``budget`` cannot hold it)."""
    try:
        mode = os.stat(store_dir).st_mode
    except OSError:
        raise _Unavailable("the Mission Core store directory is missing")
    if not stat.S_ISDIR(mode) or mode & _OPEN_MODE_BITS:
        raise _Unavailable("the Mission Core store directory is not private")
    path = os.path.join(store_dir, store.MISSIONS_FILE_NAME)
    try:
        data = _bounded_read(path, sources.MAX_STORE_READ_BYTES, budget)
        document = fileread.load_bounded_json(data)
    except (fileread.FileRefused, fileread.JsonRefused) as exc:
        raise _Unavailable("the Mission Core store could not be read (%s)"
                           % exc.problem)
    if type(document) is not dict or document.get(
            "mission_store_schema_version") != (
                store.MISSION_STORE_SCHEMA_VERSION):
        raise _Unavailable("the Mission Core store schema is not supported")
    try:
        return store.accept_loaded_document(document, path)
    except store.MissionStoreError:
        raise _Unavailable("the Mission Core store refused validation")


class ReadOnlyMissionStore(object):
    """Mission Core's store for ONE observe phase: the document read once
    (load 2), served to every ``observe`` of the phase; ``lock`` and
    ``save`` REFUSE."""

    def __init__(self, document):
        self._document = document

    def load(self):
        return copy.deepcopy(self._document)

    def lock(self):
        raise ReadOnlyStoreError("the composer's store is read-only")

    def save(self, document):
        raise ReadOnlyStoreError("the composer's store is read-only")


class SourceSample(object):
    """Load 1 of one Mission Core source: its document (or the reason it
    could not be read) and every mission's head cursor."""

    def __init__(self, source, document, reason, budget_exhausted=False):
        self.source = source
        self.document = document
        self.reason = reason
        self.budget_exhausted = budget_exhausted
        self.cursors = {}
        if document is not None:
            for mission_id, mission in document["missions"].items():
                self.cursors[mission_id] = journal.head_cursor(
                    mission, document["mission_state"].get(mission_id))

    def state_of(self, mission_id):
        if self.document is None:
            return None
        if mission_id not in self.document["missions"]:
            return None
        found = self.document["mission_state"].get(mission_id)
        return found if found is not None else state.new_state_record(
            mission_id, 0)

    def expected_reviews(self):
        """``{workspace: task_id}`` for this source's missions with a
        resolved, consistent execution (the version 1 view's own
        derivation, ``sources._mission_core_view``)."""
        found = {}
        if self.document is None:
            return found
        for _, mission in sorted(self.document["missions"].items()):
            execution = sources._mission_core_view(self.source,
                                                   mission)["execution"]
            if execution["task_id"] is not None and execution[
                    "workspace_realpath"] is not None and execution[
                        "consistent"]:
                found.setdefault(execution["workspace_realpath"],
                                 []).append(execution["task_id"])
        return found


def _unavailable(reason):
    return {"unavailable": reason[:reconciliation.MAX_FINDING_DETAIL_CHARS]}


def _locator_parts(locator):
    """A recorded ``REPOSITORY_PATH`` locator as contained relative
    components, or None (absolute, empty, ``.``/``..`` or NUL refuse)."""
    if type(locator) is not str or not locator or "\x00" in locator or (
            "\\" in locator) or locator.startswith("/"):
        return None
    parts = tuple(locator.split("/"))
    if len(parts) > MAX_LOCATOR_PARTS or any(
            part in ("", ".", "..") for part in parts):
        return None
    return parts


class MissionStateComposer(object):
    """See the module. ``delivery_store`` is the explicitly registered
    delivery store directory, or None (delivery is then unavailable)."""

    def __init__(self, clock, delivery_store=None):
        self._clock = clock
        self._delivery_store = delivery_store

    # -- load 1 ----------------------------------------------------------

    def sample(self, registry_sources, budget=None):
        """``{source_id: SourceSample}`` for every registered Mission Core
        source: load 1, BEFORE any report is collected, debiting the shared
        refresh ``budget`` when one is given."""
        samples = {}
        for source in registry_sources:
            if source["kind"] != "mission_core":
                continue
            try:
                document = load_store_document(source["store_dir"], budget)
                samples[source["source_id"]] = SourceSample(source, document,
                                                            None)
            except _BudgetExhausted as exc:
                samples[source["source_id"]] = SourceSample(
                    source, None, exc.reason, budget_exhausted=True)
            except _Unavailable as exc:
                samples[source["source_id"]] = SourceSample(source, None,
                                                            exc.reason)
        return samples

    @staticmethod
    def expected_reviews(samples):
        """``{root: task_id}``: the task each root's canonical review must
        belong to, only for roots exactly ONE mission claims."""
        claimed = {}
        for sample in samples.values():
            for root, task_ids in sample.expected_reviews().items():
                claimed.setdefault(root, []).extend(task_ids)
        return dict((root, ids[0]) for root, ids in claimed.items()
                    if len(ids) == 1)

    # -- the four kinds ----------------------------------------------------

    @staticmethod
    def _agreement(view, chain_reading):
        """The root entry of the mission's own workspace when the capture
        observed it and its CURRENT task is the mission's task; raises
        ``_Unavailable`` otherwise."""
        execution = view["execution"]
        if not chain_reading.is_live():
            raise _Unavailable("the chain observation is not fresh")
        if execution["task_id"] is None or execution[
                "workspace_realpath"] is None or not execution[
                    "consistent"] or execution["record_ambiguous"]:
            raise _Unavailable("the mission's task and workspace are not"
                               " resolved consistently")
        entry = chain_reading.root(execution["workspace_realpath"])
        if entry is None:
            raise _Unavailable("the mission's workspace root was not observed")
        if entry["task"] is None or entry["task"]["task_id"] != execution[
                "task_id"]:
            raise _Unavailable("the observed root's current task is not the"
                               " mission's task")
        return entry

    def _task(self, view, chain_reading):
        entry = self._agreement(view, chain_reading)
        status = entry["task"]["status"]
        if status is None:
            raise _Unavailable("the task record carries no status")
        if status in TASK_REPORTS:
            return {"value": status, "observed_at": chain_reading.observed_at}
        if status in KNOWN_UNMAPPED_TASK_STATUSES:
            raise _Unavailable("task status %s is outside the report"
                               " vocabulary; refused, never coerced" % status)
        raise _Unavailable("an unknown task status literal is outside the"
                           " report vocabulary; refused, never coerced")

    def _review(self, view, chain_reading):
        entry = self._agreement(view, chain_reading)
        review = entry["review"]
        stale = review["currency"] == contract_v2.CURRENCY_POSSIBLY_STALE
        if review["state"] == contract_v2.REVIEW_ADMITTED:
            if stale:
                raise _Unavailable(
                    "the task is not ACTIVE, so its review fields may be"
                    " stale (the gate updates them only while ACTIVE)")
            if review["gate"] == contract_v2.GATE_PARSED:
                return {"value": review["verdict"],
                        "observed_at": chain_reading.observed_at}
            raise _Unavailable(
                "the review gate could not parse a valid token (MISSING /"
                " MALFORMED): no verdict was parsed; this is not a"
                " rejection")
        if review["state"] == contract_v2.REVIEW_NOT_RECORDED:
            if stale:
                raise _Unavailable("the task is not ACTIVE and records no"
                                   " review; a later review may be missing")
            return {"value": reconciliation.REVIEW_REPORT_NONE,
                    "observed_at": chain_reading.observed_at}
        raise _Unavailable("the canonical review record is %s (%s)"
                           % (review["state"], review["problem"]))

    def _candidate(self, view, mission_state, why=None, budget=None):
        execution = view["execution"]
        root = execution["workspace_realpath"]
        if mission_state is None:
            raise _Unavailable("the Mission Core record was not read before"
                               " collection (%s)" % why)
        if root is None or not execution["consistent"] or not (
                os.path.isabs(root)) or os.path.realpath(root) != root:
            raise _Unavailable("the mission's workspace is not one resolved"
                               " root")
        locators = {}
        for artifact in mission_state["artifacts"]:
            if artifact["locator_kind"] == state.LOCATOR_KIND_REPOSITORY_PATH \
                    and artifact["key"] is not None:
                locators.setdefault(artifact["key"], set()).add(
                    artifact["locator"])
        if len(locators) > reconciliation.MAX_OBSERVED_CANDIDATE_KEYS:
            raise _Unavailable("more recorded artifact keys than a candidate"
                               " report may carry")
        digests = {}
        for key in sorted(locators):
            seen = set()
            for locator in sorted(locators[key]):
                parts = _locator_parts(locator)
                if parts is None:
                    raise _Unavailable("a recorded locator is not a contained"
                                       " relative path")
                hold = None
                if budget is not None:
                    hold = budget.hold(_CANDIDATE_READ_BOUND, fixed=True)
                    if hold is None:
                        raise _budget_exhausted()
                status, count, digest, _ = state_artifact.read_state_artifact(
                    root, parts[:-1], parts[-1])
                if hold is not None:
                    # A digest means the reader returned exactly ``count``
                    # bytes; any other outcome keeps the whole hold.
                    if digest is not None and type(count) is int:
                        hold.settle(count)
                    else:
                        hold.failed()
                if digest is None or status not in (
                        prepare_module.INSTRUCTION_READ,
                        prepare_module.INSTRUCTION_REFUSED_NON_UTF8):
                    raise _Unavailable("a recorded artifact could not be read"
                                       " (%s)" % status)
                seen.add(digest)
            if len(seen) != 1:
                raise _Unavailable("one artifact key was observed with"
                                   " differing digests")
            digests[key] = seen.pop()
        return {"value": {"baseline_digest_sha256": None,
                          "artifact_digests": digests},
                "observed_at": self._clock()}

    def _delivery_document(self, budget=None):
        directory = self._delivery_store
        try:
            mode = os.stat(directory).st_mode
        except OSError:
            raise _Unavailable("the registered delivery store is missing")
        if not stat.S_ISDIR(mode) or mode & _OPEN_MODE_BITS:
            raise _Unavailable("the registered delivery store is not private")
        try:
            document = fileread.load_bounded_json(_bounded_read(
                os.path.join(directory, DELIVERY_STORE_FILE),
                MAX_DELIVERY_STORE_BYTES, budget))
        except (fileread.FileRefused, fileread.JsonRefused) as exc:
            raise _Unavailable("the delivery store could not be read (%s)"
                               % exc.problem)
        if type(document) is not dict or document.get(
                "pr_delivery_store_schema_version") != (
                    DELIVERY_STORE_SCHEMA_VERSION) or type(
                        document.get("deliveries")) is not dict:
            raise _Unavailable("the delivery store schema is not supported")
        return document

    def _delivery(self, mission_state, why=None, budget=None):
        if mission_state is None:
            raise _Unavailable("the Mission Core record was not read before"
                               " collection (%s)" % why)
        attested = [a for a in mission_state["artifacts"]
                    if state.receipt_attestation_of(a) is not None]
        if not attested:
            raise _Unavailable("no attested delivery receipt links this"
                               " mission to a delivery record")
        if self._delivery_store is None:
            raise _Unavailable("no delivery store is registered")
        latest = max(attested, key=lambda a: a["sequence"])
        marker = state.receipt_attestation_of(latest)
        delivery = self._delivery_document(budget)["deliveries"].get(
            marker["delivery_id"])
        if delivery is None:
            raise _Unavailable("the linked delivery record is not in the"
                               " registered delivery store")
        observed_at = self._clock()
        try:
            validated = mission_parent.validated_receipt(delivery,
                                                         marker["step"])
        except authorization.AuthorizationError:
            return {"value": {
                "status": reconciliation.DELIVERY_REPORT_INVALID,
                "receipt_artifact_id": latest["artifact_id"],
                "locator": latest["locator"],
                "receipt_digest_sha256": latest["content_digest_sha256"]},
                "observed_at": observed_at}
        except (KeyError, TypeError):
            raise _Unavailable("the linked delivery step is not readable")
        if validated is None:
            return {"value": {
                "status": reconciliation.DELIVERY_REPORT_ABSENT,
                "receipt_artifact_id": None, "locator": None,
                "receipt_digest_sha256": None}, "observed_at": observed_at}
        matches = [a for a in mission_state["artifacts"]
                   if a["locator_kind"] == (
                       state.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE)
                   and a["locator"] == validated.receipt_id
                   and a["content_digest_sha256"] == (
                       validated.receipt_digest_sha256)]
        if not matches:
            raise _Unavailable("the validated receipt has no registered"
                               " Mission artifact; it is not ABSENT")
        named = max(matches, key=lambda a: a["sequence"])
        return {"value": {
            "status": (reconciliation.DELIVERY_REPORT_VALID
                       if len(matches) == 1
                       else reconciliation.DELIVERY_REPORT_AMBIGUOUS),
            "receipt_artifact_id": named["artifact_id"],
            "locator": validated.receipt_id,
            "receipt_digest_sha256": validated.receipt_digest_sha256},
            "observed_at": observed_at}

    def _reports(self, view, sample, chain_reading, budget=None):
        mission_state = sample.state_of(view["native_id"])
        collectors = (
            ("task", lambda: self._task(view, chain_reading)),
            ("review", lambda: self._review(view, chain_reading)),
            ("candidate", lambda: self._candidate(view, mission_state,
                                                  sample.reason, budget)),
            ("delivery", lambda: self._delivery(mission_state,
                                                sample.reason, budget)),
        )
        reports, kinds = {}, {}
        for kind, collect in collectors:
            try:
                reports[kind] = collect()
                kinds[kind] = {"supplied": contract_v2.SUPPLIED_REPORTED,
                               "reason": None}
            except _Unavailable as exc:
                reports[kind] = _unavailable(exc.reason)
                kinds[kind] = {"supplied": contract_v2.SUPPLIED_UNAVAILABLE,
                               "reason": reports[kind]["unavailable"]}
        return reports, kinds

    # -- load 2 and observe -------------------------------------------------

    def compose(self, samples, source, views, chain_reading, budget=None):
        """``{mission_ref: mission_state block}`` for ``views`` of one
        Mission Core ``source``: every report collected first, then ONE
        fresh read (load 2, debiting the shared refresh ``budget``), then
        ``observe`` per mission with its SAMPLED cursor, unchanged. A load 2
        the budget cannot cover is BUDGET-EXHAUSTED, never an empty
        report."""
        sample = samples.get(source["source_id"]) or SourceSample(
            source, None, "the source was not sampled before collection")
        if sample.budget_exhausted:
            # Load 1 was refused for budget: the whole block says so; no
            # report is collected and no load 2 is attempted.
            return dict((view["mission_ref"], _block(
                contract_v2.MISSION_STATE_UNAVAILABLE,
                contract_v2.PROBLEM_MISSION_STATE_BUDGET, sample.reason))
                for view in views)
        collected = []
        for view in views:
            reports, kinds = self._reports(view, sample, chain_reading,
                                           budget)
            cursor = sample.cursors.get(view["native_id"])
            collected.append((view, cursor, reports, kinds))
        try:
            reader = ReadOnlyMissionStore(load_store_document(
                source["store_dir"], budget))
        except _BudgetExhausted as exc:
            return dict((view["mission_ref"], _block(
                contract_v2.MISSION_STATE_UNAVAILABLE,
                contract_v2.PROBLEM_MISSION_STATE_BUDGET, exc.reason))
                for view, _, _, _ in collected)
        except _Unavailable as exc:
            return dict((view["mission_ref"], _block(
                contract_v2.MISSION_STATE_UNAVAILABLE,
                contract_v2.PROBLEM_MISSION_STATE_SOURCE, exc.reason))
                for view, _, _, _ in collected)
        missions = service.MissionService(reader, self._clock)
        blocks = {}
        for view, cursor, reports, kinds in collected:
            inputs = {"cursor": copy.deepcopy(cursor), "reports": reports}
            try:
                report = missions.observe(view["native_id"], inputs)
            except (record.MissionError, store.MissionStoreError) as exc:
                blocks[view["mission_ref"]] = _block(
                    contract_v2.MISSION_STATE_UNAVAILABLE,
                    contract_v2.PROBLEM_MISSION_STATE_REFUSED,
                    "Mission Core refused the observation (%s)"
                    % getattr(exc, "problem", type(exc).__name__))
                continue
            blocks[view["mission_ref"]] = {
                "state": contract_v2.MISSION_STATE_OBSERVED,
                "problem": None, "detail": None, "report": report,
                "inputs": {
                    "cursor": (contract_v2.CURSOR_SAMPLED
                               if cursor is not None
                               else contract_v2.CURSOR_NOT_SAMPLED),
                    "kinds": kinds,
                    "candidate_applicability":
                        contract_v2.CANDIDATE_APPLICABILITY_UNPROVEN,
                    "baseline": contract_v2.BASELINE_UNOBSERVED}}
        return blocks


def _block(state_name, problem, detail=None):
    return {"state": state_name, "problem": problem, "detail": detail,
            "report": None, "inputs": None}
