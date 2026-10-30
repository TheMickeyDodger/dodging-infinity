"""Assemble one VERSION 2 query response (``contract_v2``): the version 1
assembly (``query``), unchanged in what it reads and how it degrades, plus
the chain observation, the version 2 roster (``chains``), the Mission State
projection and the admitted canonical review record.

Pure like ``query``: the runtime snapshot and the chain observation are
injected VALUES, and the Mission State blocks come from an injected
``compose`` callable (the producer lives outside this package). ``compose``
is called once per Mission Core source with that source's record and the
views it may observe; at most ``contract_v2.MAX_OBSERVED_MISSIONS`` views per
response are offered (active missions first, then by ``mission_ref``),
every other Mission Core view is reported ``unavailable``
(``mission_state_over_refresh_limit``), never silently dropped. A
``compose`` that raises or returns a block of the wrong shape yields an
explicit ``unavailable`` block for those views, never a crash and never a
partial block. A WorkflowStore view has no Mission State
(``not_applicable``), never an empty one.

The canonical review record is projected only through agreement, all of it
required: a fresh chain observation; the mission's own resolved task id and
workspace; that workspace observed by the capture; and that root's CURRENT
task being the mission's task. The root's review block (decided inside the
capture, linkage and corroboration included) is then carried verbatim, with
``candidate_applicability`` ``unproven``.

Every mission's ``delivery_link`` comes from an injected ``delivery_links``
callable (the registered delivery store's reader lives outside this
package), called once per response. Without one, every workflow mission says no
store is registered; a callable that raises or returns the wrong shape makes the
workflow missions ``unlinked_reader_failed``, never a crash. A mission of any
other model (Mission Core included) is ``unlinked_model_not_recorded`` either
way, except when ``repository_delivery`` is set: a mission of
``contract_v2.REPOSITORY_LINK_MODEL`` is linked by its workspace origin
(``repository_url_of``) against ``delivery_repositories``, or says why it
is not.

The whole response is validated by ``contract_v2.validate_response_v2``
before it is returned: an assembly defect is a raised ``ContractError``,
never a malformed response handed to a consumer.

Reads are bounded by ONE shared ``RefreshReadBudget`` (round 3, B2): every
source read of a version 2 pass HOLDS what it may pull before it is made,
against its stage's own share and the shared total, and a source the
budget cannot hold is ``unavailable`` (``query_input_budget_exhausted``)
without being read, never an empty source.
"""

from mission_sources import chains as chains_module
from mission_sources import contract
from mission_sources import contract_v2
from mission_sources import fileread
from mission_sources import query as query_module
from mission_sources import registry as registry_module
from mission_sources import roster
from mission_sources import sources as sources_module


def limits_v2():
    """The version 1 limits, plus every bound version 2 adds."""
    value = query_module.limits()
    value.update({
        "max_herd_roots": contract_v2.MAX_HERD_ROOTS_V2,
        "max_child_records": contract_v2.MAX_CHILD_RECORDS,
        "max_chain_depth": contract_v2.MAX_CHAIN_DEPTH,
        "max_observed_missions": contract_v2.MAX_OBSERVED_MISSIONS,
        "max_review_preamble_bytes": contract_v2.MAX_REVIEW_PREAMBLE_BYTES,
        "max_review_preamble_lines": contract_v2.MAX_REVIEW_PREAMBLE_LINES,
        "reported_freshness_bound_seconds": (
            contract_v2.REPORTED_FRESHNESS_BOUND_SECONDS),
    })
    return value


def valid_until_v2(now, max_age_seconds, reading, chain_reading,
                   source_blocks):
    """``query.valid_until``, shortened further by the chain observation's
    own expiry when it is fresh."""
    value = query_module.valid_until(now, max_age_seconds, reading,
                                     source_blocks)
    if chain_reading.is_live():
        value = min(value, chain_reading.observed_at
                    + chain_reading.max_age_seconds)
    return value


def _state_block(state, problem, detail=None):
    return {"state": state, "problem": problem, "detail": detail,
            "report": None, "inputs": None}


def _review_block(state, problem, task_status=None):
    return {"state": state, "problem": problem, "record": None, "gate": None,
            "verdict": None, "currency": None, "task_status": task_status,
            "integrity": None, contract_v2.REVIEW_FINDINGS_KEY: None,
            "candidate_applicability":
                contract_v2.CANDIDATE_APPLICABILITY_UNPROVEN}


def canonical_review(mission, source_kind, chain_reading):
    """The mission's admitted canonical review record (see the module).

    A Mission Core record and a task-native record (a plain Herdr task)
    both take the chain observation's review of the bound workspace. The
    capture binds an unclaimed root to its own task record id, which is
    how that task's review under its state directory is read. No review
    recorded is ``not_recorded``. A workspace the capture did not observe
    is ``unavailable``. Any other source kind has no canonical review.
    """
    task_native = source_kind == contract.KIND_TASK_NATIVE
    if source_kind != contract.KIND_MISSION_CORE and not task_native:
        return _review_block(contract_v2.REVIEW_NOT_APPLICABLE,
                             contract_v2.PROBLEM_REVIEW_KIND_NOT_APPLICABLE)
    if not chain_reading.is_live():
        return _review_block(contract_v2.REVIEW_UNAVAILABLE,
                             contract_v2.PROBLEM_CHAIN_OBSERVATION_NOT_FRESH)
    execution = mission["execution"]
    if execution["task_id"] is None or (
            execution["workspace_realpath"] is None) or (
                not execution["consistent"]) or execution["record_ambiguous"]:
        return _review_block(contract_v2.REVIEW_NOT_APPLICABLE,
                             contract_v2.PROBLEM_MISSION_NOT_BOUND_TO_ROOT)
    entry = chain_reading.root(execution["workspace_realpath"])
    if entry is None:
        return _review_block(contract_v2.REVIEW_UNAVAILABLE,
                             contract_v2.PROBLEM_REVIEW_ROOT_NOT_OBSERVED)
    if entry["task"] is None or entry["task"]["task_id"] != execution[
            "task_id"]:
        return _review_block(contract_v2.REVIEW_REFUSED,
                             contract_v2.PROBLEM_REVIEW_TASK_MISMATCH)
    block = dict(entry["review"])
    block["candidate_applicability"] = (
        contract_v2.CANDIDATE_APPLICABILITY_UNPROVEN)
    return block


def _observable(missions, kinds):
    """The Mission Core views offered to ``compose``, bounded and ordered:
    active first, then by ``mission_ref``."""
    candidates = sorted(
        (m for m in missions
         if kinds[m["source_id"]] == contract.KIND_MISSION_CORE),
        key=lambda m: (m["lifecycle"]["standing"] != contract.STANDING_ACTIVE,
                       m["mission_ref"]))
    return candidates[:contract_v2.MAX_OBSERVED_MISSIONS]


def _mission_states(missions, kinds, sources_by_id, chain_reading, compose):
    """``{mission_ref: mission_state block}`` for every mission."""
    states = {}
    for mission in missions:
        if kinds[mission["source_id"]] != contract.KIND_MISSION_CORE:
            states[mission["mission_ref"]] = _state_block(
                contract_v2.MISSION_STATE_NOT_APPLICABLE,
                contract_v2.PROBLEM_MISSION_STATE_KIND)
        elif compose is None:
            states[mission["mission_ref"]] = _state_block(
                contract_v2.MISSION_STATE_UNAVAILABLE,
                contract_v2.PROBLEM_MISSION_STATE_NOT_CONFIGURED)
        else:
            states[mission["mission_ref"]] = _state_block(
                contract_v2.MISSION_STATE_UNAVAILABLE,
                contract_v2.PROBLEM_MISSION_STATE_OVER_LIMIT)
    if compose is None:
        return states
    by_source = {}
    for mission in _observable(missions, kinds):
        by_source.setdefault(mission["source_id"], []).append(mission)
    for source_id in sorted(by_source):
        views = by_source[source_id]
        try:
            composed = compose(sources_by_id[source_id], views, chain_reading)
        except Exception as exc:                          # noqa: BLE001
            composed = None
            detail = "the composer raised %s" % type(exc).__name__
        else:
            detail = "the composer returned no block for this mission"
        for view in views:
            ref = view["mission_ref"]
            block = composed.get(ref) if type(composed) is dict else None
            try:
                contract_v2._validate_mission_state(block, ref)
            except contract.ContractError:
                block = _state_block(contract_v2.MISSION_STATE_UNAVAILABLE,
                                     contract_v2.PROBLEM_MISSION_STATE_RETURN,
                                     detail)
            states[ref] = block
    return states


# -- the shared refresh read budget (round 3, B2) ---------------------------

#: ``sources`` problems that arise only BEFORE any byte of a store file is
#: read (no store directory or file, open permissions, no reader for the
#: kind): such a read's hold is released. Every other unsettled read KEEPS
#: its hold: it may have pulled its bound plus the overrun-detection byte
#: before failing.
_SOURCE_ZERO_BYTE_REFUSALS = frozenset((
    sources_module.PROBLEM_STORE_DIRECTORY_MISSING,
    sources_module.PROBLEM_STORE_FILE_ABSENT,
    sources_module.PROBLEM_STORE_PERMISSIONS_OPEN,
    sources_module.PROBLEM_UNKNOWN_KIND))
#: The same, as ``fileread`` names them, for a read made directly.
_FILE_ZERO_BYTE_REFUSALS = frozenset((
    fileread.PROBLEM_FILE_ABSENT, fileread.PROBLEM_FILE_SYMLINK,
    fileread.PROBLEM_FILE_NOT_REGULAR, fileread.PROBLEM_FILE_PERMISSIONS_OPEN))


class _Ledger(object):

    def __init__(self, total):
        self.total = total
        self.charged = 0

    @property
    def remaining(self):
        return self.total - self.charged


class RefreshReadBudget(_Ledger):
    """ONE shared input byte budget for a WHOLE version 2 refresh. Its
    ``total`` is the refresh's ALREADY STATED aggregate (the owner's
    ``refresh_budget`` ``sum_max_query_input_bytes``: its counted stages
    times the accepted per-pass bound); it is ENFORCED against every read
    ATTEMPT of every owner-side stage. Each counted stage - the discovery
    pass, the composer's load 1, the query pass, the composer's collection
    and load 2 - reads through its own ``stage()`` (``pass_budget()`` for a
    source pass), holding against BOTH that stage's per-pass share and this
    total, so no stage can spend another stage's share. A read HOLDS the
    most it can pull from disk (its bound plus the byte that detects an
    overrun) BEFORE it is made; a read that succeeds settles to the exact
    bytes it returned; a read that fails KEEPS its whole hold, unless its
    refusal is known to come before any byte is read. A read that cannot
    be held is REFUSED without being attempted, and counted."""

    def __init__(self, total):
        _Ledger.__init__(self, total)
        self.refused_reads = 0

    def note_refused(self):
        self.refused_reads += 1

    def stage(self):
        return StageBudget(self)

    def hold(self, bound, fixed=False, also=None):
        """A ``ReadHold`` for one read of at most ``bound`` bytes (which may
        pull ``bound + 1``), its bound shrunk to what remains unless
        ``fixed``; ``also``: one more ledger held alike (a pass's own
        bound). None (refused, and counted) when it cannot be held."""
        ledgers = [self] if also is None else [self, also]
        room = min(ledger.remaining for ledger in ledgers)
        allowance = bound if fixed else min(bound, room - 1)
        if allowance < 0 or allowance + 1 > room:
            self.refused_reads += 1
            return None
        return ReadHold(ledgers, allowance)

    def pass_budget(self):
        return PassBudget(self.stage())

    def summary(self):
        return {"max_bytes": self.total, "charged_bytes": self.charged,
                "reads_refused": self.refused_reads,
                "exhausted": self.refused_reads > 0}


class ReadHold(object):
    """One read's hold on its ledgers (see ``RefreshReadBudget``)."""

    def __init__(self, ledgers, allowance):
        self.allowance = allowance
        self.closed = False
        self._ledgers = ledgers
        self._held = allowance + 1
        for ledger in ledgers:
            ledger.charged += self._held

    def settle(self, pulled):
        """The read SUCCEEDED, pulling exactly ``pulled`` bytes."""
        self._close(self._held - min(max(pulled, 0), self._held))

    def failed(self, before_any_byte=False):
        """The read FAILED: the whole hold is kept, unless the refusal is
        known to come ``before_any_byte``."""
        self._close(self._held if before_any_byte else 0)

    def refused(self, problem):
        """The read was refused with the ``fileread`` code ``problem``."""
        self.failed(problem in _FILE_ZERO_BYTE_REFUSALS)

    def _close(self, returned):
        if not self.closed:
            self.closed = True
            for ledger in self._ledgers:
                ledger.charged -= returned


class StageBudget(object):
    """One counted stage of a refresh: every hold is against BOTH this
    stage's accepted per-pass bound (``MAX_QUERY_INPUT_BYTES``) and the
    shared ``RefreshReadBudget``."""

    def __init__(self, shared):
        self._shared = shared
        self._ledger = _Ledger(sources_module.MAX_QUERY_INPUT_BYTES)

    def hold(self, bound, fixed=False):
        return self._shared.hold(bound, fixed, also=self._ledger)

    def note_refused(self):
        self._shared.note_refused()


class PassBudget(object):
    """One version 2 source pass, in the shape ``sources`` charges
    (``bytes_remaining``, ``missions_remaining``): each source read HOLDS
    through its ``StageBudget``. Read through ``read_source``."""

    def __init__(self, stage):
        self._stage = stage
        self.missions_remaining = sources_module.MAX_QUERY_MISSIONS
        self._hold = None

    @property
    def bytes_remaining(self):
        # ``sources`` reads this to size its ONE read, and again to charge
        # a successful one; no read is sized outside an open hold.
        if self._hold is None or self._hold.closed:
            raise RuntimeError("a store read outside its hold")
        return self._hold.allowance

    @bytes_remaining.setter
    def bytes_remaining(self, value):
        self._hold.settle(self._hold.allowance - value)

    def read_source(self, source, now, readers=None):
        """``sources.read_source`` under ONE hold; a source the budget
        cannot hold is ``unavailable`` (``query_input_budget_exhausted``)
        WITHOUT being read."""
        hold = self._stage.hold(sources_module.MAX_STORE_READ_BYTES)
        if hold is None:
            return sources_module._reading(
                source, now, contract.HEALTH_UNAVAILABLE,
                sources_module.PROBLEM_QUERY_INPUT_BUDGET)
        self._hold = hold
        result = None
        try:
            result = sources_module.read_source(source, now, self, readers)
        finally:
            self._hold = None
            if not hold.closed:
                hold.failed(result is not None and result["problem"] in (
                    _SOURCE_ZERO_BYTE_REFUSALS))
        if result["problem"] == sources_module.PROBLEM_QUERY_INPUT_BUDGET:
            self._stage.note_refused()
        return result


def _pass_budget(budget):
    return (RefreshReadBudget(sources_module.MAX_QUERY_INPUT_BYTES)
            .pass_budget() if budget is None else budget)


def discovery_input(source_registry, now, budget=None, readers=None):
    """``(views, canonical_complete)`` for the version 2 discovery pass: the
    mission views of every FRESH registered source (the same views the
    version 1 query classifies), read under ``budget`` (a ``PassBudget``; a
    standalone one when None) with ``readers`` (``sources.read_source``),
    and whether every CANONICAL source was read fresh
    (``roster.canonical_sources_complete``). ``([], False)`` when the
    registry cannot be loaded. ``roster.discovery`` takes both directly."""
    try:
        document, _ = source_registry.load_measured(absent_is_empty=False)
    except registry_module.RegistryError:
        return [], False
    budget = _pass_budget(budget)
    views, blocks = [], []
    for source in sorted(document["sources"], key=lambda s: s["source_id"]):
        result = budget.read_source(source, now, readers)
        if result["health"] == contract.HEALTH_FRESH:
            views.extend(result["missions"])
        blocks.append(result)
    return views, roster.canonical_sources_complete(blocks)


def source_views(source_registry, now, budget=None, readers=None):
    """The views of ``discovery_input`` alone (``[]`` when the registry
    cannot be loaded)."""
    return discovery_input(source_registry, now, budget, readers)[0]


def assemble_v2(registry_document, runtime_snapshot, chain_observation, now,
                max_age_seconds=contract.DEFAULT_MAX_AGE_SECONDS, readers=None,
                registry_directory=None, registry_bytes=None,
                observed_roots=None, confirmed_bindings=None, compose=None,
                budget=None, runtime_agents=None, delivery_presentations=None,
                delivery_links=None, delivery_repositories=None,
                repository_url_of=None, repository_delivery=False):
    """One version 2 response over an already loaded (and validated)
    registry. ``runtime_agents``: as ``query.assemble``.
    ``delivery_presentations``: None (not registered) or a callable taking
    this response's missions, what the Mission side could inspect
    (``contract_v2.INSPECTIONS``) and the response's own ``{source_id: kind}``,
    and returning its ``delivery_presentations`` block (D1; see
    ``target_runtime.mission_state.DeliveryPresentationsReader``); a failure of
    the hook is an unavailable block, never an empty one. ``delivery_links``:
    None (no delivery store registered) or a callable taking nothing and
    returning the registered store's ``{workflow id: [delivery ids]}``, or None
    when the store is unavailable (``target_runtime.mission_state.
    DeliveryLinksReader.bindings``); every mission then carries its
    ``delivery_link`` (``contract_v2.mission_delivery_link``), and a failure of
    the hook is ``unlinked_reader_failed``, never a crash.
    ``repository_delivery``: when set, a mission of
    ``contract_v2.REPOSITORY_LINK_MODEL`` is addressed by repository.
    ``delivery_repositories`` returns ``{identity: [delivery ids]}``
    (or None when the store is unavailable). ``repository_url_of(mission)``
    returns that task's workspace origin identity, or None. ``observed_roots``
    and ``confirmed_bindings``: see
    ``roster.classify``; ``compose``: see the module; ``budget``: this
    pass's ``PassBudget`` (the owner's, of its SHARED refresh budget; a
    standalone one when None)."""
    registry_module.validate_document(registry_document)
    reading = contract.read_runtime_snapshot(runtime_snapshot, now,
                                             max_age_seconds)
    chain_reading = contract_v2.read_chain_observation(chain_observation, now,
                                                       max_age_seconds)
    source_blocks = []
    views = []
    kinds = {}
    sources_by_id = {}
    budget = _pass_budget(budget)
    for source in sorted(registry_document["sources"],
                         key=lambda s: s["source_id"]):
        kinds[source["source_id"]] = source["kind"]
        sources_by_id[source["source_id"]] = source
        result = budget.read_source(source, now, readers)
        if result["health"] == contract.HEALTH_FRESH:
            views.extend(result["missions"])
        block = dict(result)
        del block["missions"]
        source_blocks.append(block)
    missions, unmatched = chains_module.classify_v2(
        views, reading, chain_reading, observed_roots, confirmed_bindings,
        canonical_complete=roster.canonical_sources_complete(source_blocks))
    states = _mission_states(missions, kinds, sources_by_id, chain_reading,
                             compose)
    inspection = _inspection(source_blocks)
    unavailable, bound = _delivery_bindings(delivery_links)
    repositories = None
    if repository_delivery and unavailable is None:
        repositories, repo_unavailable = _repository_bindings(
            delivery_repositories)
        if repo_unavailable is not None:
            unavailable = repo_unavailable
            bound = None
    holders = {}
    for mission in missions:
        if mission["lifecycle"]["model"] == (
                contract.LIFECYCLE_MODEL_WORKFLOW_STORE):
            holders[mission["native_id"]] = holders.get(
                mission["native_id"], 0) + 1
    resolved_urls = {}
    repository_holders = {}
    if repository_delivery:
        for mission in missions:
            if mission["lifecycle"]["model"] != contract_v2.REPOSITORY_LINK_MODEL:
                continue
            url = None
            if unavailable is None and repository_url_of is not None:
                try:
                    url = repository_url_of(mission)
                except Exception:                          # noqa: BLE001
                    url = None
            if type(url) is not str or not url:
                url = None
            resolved_urls[mission["mission_ref"]] = url
            if url is not None:
                repository_holders[url] = repository_holders.get(url, 0) + 1
    for mission in missions:
        mission["mission_state"] = states[mission["mission_ref"]]
        mission["admitted_artifacts"] = {
            "canonical_review": canonical_review(
                mission, kinds[mission["source_id"]], chain_reading)}
        mission["workflow_phase"] = contract_v2.workflow_phase(mission)
        mission["delivery_link"] = contract_v2.mission_delivery_link(
            mission, unavailable, bound, holders, inspection,
            repository_url=resolved_urls.get(mission["mission_ref"]),
            by_repository=repositories,
            repository_holders=repository_holders,
            repository_addressable=repository_delivery)
    registry = query_module._registry_block(
        contract.HEALTH_FRESH, None, len(registry_document["sources"]),
        registry_directory, registry_bytes)
    response = contract_v2.envelope_v2(
        now, max_age_seconds,
        valid_until_v2(now, max_age_seconds, reading, chain_reading,
                       source_blocks), limits_v2(),
        registry, source_blocks, reading.describe(), chain_reading.describe(),
        missions, unmatched,
        None if runtime_agents is None
        else runtime_agents(missions, reading, now, source_blocks),
        _delivery_presentations_block(
            delivery_presentations, missions, inspection,
            dict((block["source_id"], block["kind"])
                 for block in source_blocks)))
    return contract_v2.validate_response_v2(response)


def _delivery_bindings(hook):
    """``(unavailable, bound)`` for this refresh's delivery links (see
    ``contract_v2.mission_delivery_link``): the registered store's
    ``{workflow id: [delivery ids]}`` from ``hook``, or why there is none:
    not registered (no hook), unavailable (the hook returned None), or reader
    failed (it raised, or returned anything but that map). An empty map is
    returned only when the store was read and no record names a workflow."""
    if hook is None:
        return contract_v2.MISSION_LINK_STORE_NOT_REGISTERED, None
    try:
        bound = hook()
    except Exception:                                      # noqa: BLE001
        return contract_v2.MISSION_LINK_READER_FAILED, None
    if bound is None:
        return contract_v2.MISSION_LINK_STORE_UNAVAILABLE, None
    if type(bound) is not dict or not all(
            type(workflow_id) is str and type(ids) is list and ids
            and all(type(one) is str for one in ids)
            for workflow_id, ids in bound.items()):
        return contract_v2.MISSION_LINK_READER_FAILED, None
    return None, bound


def _repository_bindings(hook):
    """``(map or None, unavailable or None)`` for Herdr repository links.

    The map is ``{repository identity: [delivery ids]}``. A missing hook is
    an empty map (the store was read and no record named a repository). A
    hook that raises, or returns anything but that map, is
    ``unlinked_reader_failed``. None from the hook is
    ``unlinked_store_unavailable``."""
    if hook is None:
        return {}, None
    try:
        bound = hook()
    except Exception:                                      # noqa: BLE001
        return None, contract_v2.MISSION_LINK_READER_FAILED
    if bound is None:
        return None, contract_v2.MISSION_LINK_STORE_UNAVAILABLE
    if type(bound) is not dict or not all(
            type(identity) is str and type(ids) is list and ids
            and all(type(one) is str for one in ids)
            for identity, ids in bound.items()):
        return None, contract_v2.MISSION_LINK_READER_FAILED
    return bound, None


def _inspection(source_blocks):
    """Whether every registered workflow-store source was read fresh: only
    then is "no registered Mission has this workflow id" a known fact."""
    if all(block["health"] == contract.HEALTH_FRESH for block in source_blocks
           if block["kind"] == contract.KIND_WORKFLOW_STORE):
        return contract_v2.INSPECTION_COMPLETE
    return contract_v2.INSPECTION_SOURCES_INCOMPLETE


def _delivery_presentations_block(hook, missions, inspection, kinds):
    """The ``delivery_presentations`` block of one response: not registered
    without a hook; the hook's block when it validates; else an explicit
    unavailable block (never an empty list). The source's registration and
    availability are its own, independent of the Mission registry."""
    if hook is None:
        return None
    try:
        return contract_v2.validate_delivery_presentations(
            hook(missions, inspection, kinds))
    except Exception:                                      # noqa: BLE001
        return contract_v2.delivery_presentations_block(
            contract_v2.DELIVERY_PRESENTATIONS_UNAVAILABLE, "reader_failed",
            contract_v2.DELIVERY_STORE_NOT_REGISTERED, None, [])


def query_v2(source_registry, runtime_snapshot, chain_observation, now,
             max_age_seconds=contract.DEFAULT_MAX_AGE_SECONDS, readers=None,
             observed_roots=None, confirmed_bindings=None, compose=None,
             budget=None, runtime_agents=None, delivery_presentations=None,
             delivery_links=None, delivery_repositories=None,
             repository_url_of=None, repository_delivery=False):
    """``query.query`` for version 2: a registry that cannot be loaded
    degrades explicitly (its own health and problem, no sources, no
    missions) instead of raising or reading as empty."""
    try:
        document, size = source_registry.load_measured(absent_is_empty=False)
    except registry_module.RegistryError as exc:
        reading = contract.read_runtime_snapshot(runtime_snapshot, now,
                                                 max_age_seconds)
        chain_reading = contract_v2.read_chain_observation(
            chain_observation, now, max_age_seconds)
        registry = query_module._registry_block(
            query_module._REGISTRY_PROBLEM_HEALTH.get(
                exc.problem, contract.HEALTH_MALFORMED),
            exc.problem, None, source_registry.directory)
        response = contract_v2.envelope_v2(
            now, max_age_seconds,
            valid_until_v2(now, max_age_seconds, reading, chain_reading, []),
            limits_v2(), registry, [], reading.describe(),
            chain_reading.describe(), [], None,
            query_module.registry_failed_agents(runtime_agents),
            # The presentations source is registered (or not) on its own: a
            # registry that cannot be loaded changes only what can be linked.
            _delivery_presentations_block(
                delivery_presentations, [],
                contract_v2.INSPECTION_REGISTRY_UNAVAILABLE, {}))
        return contract_v2.validate_response_v2(response)
    return assemble_v2(document, runtime_snapshot, chain_observation, now,
                       max_age_seconds, readers, source_registry.directory,
                       size, observed_roots, confirmed_bindings, compose,
                       budget, runtime_agents, delivery_presentations,
                       delivery_links=delivery_links,
                       delivery_repositories=delivery_repositories,
                       repository_url_of=repository_url_of,
                       repository_delivery=repository_delivery)
