"""The producer-neutral mission CATALOG endpoint (increment 2, part 2b).

    dicatalog.py sources    --registry DIR
    dicatalog.py register   --registry DIR --source-id ID --kind KIND
                            --store-dir PATH --producer LABEL
                            [--max-age-seconds N]
    dicatalog.py unregister --registry DIR --source-id ID
    dicatalog.py task-source --registry DIR [--store-dir PATH] [--ensure]
    dicatalog.py query      --registry DIR [--herd-root PATH ...]
                            [--wait-seconds S]
    dicatalog.py serve      --registry DIR [--herd-root PATH ...]

What it serves: ONE versioned ``mission_sources`` query response (the
accepted contract, unchanged) over the sources an operator REGISTERED, with
the engineering runtime's facts injected from ``runtime_snapshot``'s owner.
Nothing is privileged: every registered source is read by the reader for
its kind, a newly registered source appears in the next refresh with no
code change, and each source's missions keep their OWN lifecycle model and
literal, source-qualified (``<source_id>:<native id>``); nothing merges
them.

PASSIVE, NOT CONTROL. The catalog READS the registered stores (canonical
Mission Core and the legacy Grok WorkflowStore alike). It controls nothing,
and reading a store implies no control of it: the typed Jarvis DI controls
act ONLY through the dedicated Jarvis local-request state and DI's native
gates (``jarvis_di``), and no control path over the legacy WorkflowStore
exists. Pinned: this module imports no control or delivery package.

Registration is explicit, inspectable DATA (``mission_sources.registry``):
``register`` and ``unregister`` are the only writers, and they write only
the registry file in the directory the operator names; ``sources`` prints
the records as they are. Nothing is discovered by scanning.

``task-source`` is the supported setup step for the DI-native task source
(``task_native``: the directory where ``herdctl init`` records every
repository, by default herdctl's own ``~/.config/dodging-infinity``), so a
team ``herdctl`` registers needs no registration of its own. Without
``--ensure`` it only READS the registry and prints one JSON line saying
whether that store is registered and, when it is not, the exact command to
run. With ``--ensure`` it registers ``TASK_SOURCE_ID`` through the
registry's own ``register`` when no ``task_native`` source names that store
(by real path, under any id); otherwise it writes nothing. A refusal is the
registry's own, reported with its problem and an action, never repaired. It
never creates the registry directory, and an unreadable registry is refused
before any lock is taken; one that becomes unreadable between the status read
and the writer's locked read is reported from THAT failure (``unknown``). After
an operational write failure (permission, disk, I/O) the registry is RE-READ
(read-only) and the report is built from that read, never from the earlier
status: when it still does not register the source the report is outcome
``failed`` / problem ``registry_not_writable`` and says the registry still reads
as it did; when the replacement had already landed (the atomic writer replaces
the file and only then syncs its directory) the report is ``failed`` /
``registry_write_unconfirmed``, names the source as registered, and says the new
registry was not rolled back and its durability was not confirmed; when the
registry cannot be re-read the report states that uncertainty (``unknown``, the
read's own health and problem, "may have been updated"). Always exit 5, always
one JSON line with an action, and nothing is repaired or rolled back.

The answer (``catalog_answer``) is one closed JSON object: ``endpoint`` and
``endpoint_version``; ``requested_at`` (the clock when the refresh's request
began); ``response`` (the query response, or null); ``problem`` and
``detail`` (null when a response is given); ``runtime_capture`` (whether a
runtime snapshot was configured and, if so, the capture's own outcome);
``served`` (``refreshed``, ``cached`` or ``none``); and
``retry_after_seconds`` (for a cached or throttled answer, how long until
the next refresh may start). A response whose registry, a source or the
runtime snapshot could not be read is still a response: the accepted core
reports each with its own health, absent is unavailable rather than
observed-empty, an unavailable snapshot yields no binding, unknown stays
unknown and idle is not completion. A consumer decides what may be shown as
live ONLY through ``mission_sources.contract.response_freshness``; the
endpoint never extends freshness (the query's ``now`` is read before its
reads begin and after the snapshot's own observation, and a cached answer
is returned UNCHANGED).

The caller contract (``CatalogService``, the long-lived owner):

- CADENCE, ENFORCED HERE. A REFRESH (one runtime capture plus one query)
  starts at most once per ``MIN_REFRESH_INTERVAL_SECONDS`` (the accepted
  contract's ``min_poll_interval_seconds``), on a MONOTONIC clock anchored to
  the last refresh START, never to the last request: a request can neither
  reset nor bypass it, and a failed or unfinished refresh counts like any
  other. Within the interval the owner returns the last refreshed answer
  EXACTLY as it was (its original ``requested_at`` and its response's own
  ``generated_at`` and ``valid_until``, never restamped), marked ``served``
  ``cached``, and only while ``response_freshness`` still reads it
  ``fresh``; otherwise ``catalog_refresh_throttled`` with no response.
- RESPONSIVE RETURN. A refreshing ``request(wait_seconds)`` (at most
  ``MAX_WAIT_SECONDS``) gives the runtime capture ``RUNTIME_SHARE`` of the
  wait (through its own owner, whose contract applies unchanged) and the
  query ONLY what remains of it, then returns; a query not finished by then
  answers ``catalog_query_not_finished_within_wait``, and its late result is
  never returned to anyone. "Within the wait" means up to thread scheduling
  and the runtime owner's own return; no other floor is added.
- ONE QUERY IN FLIGHT, NO QUEUE. While a request is refreshing, or a
  previous query thread is still running, a request starts nothing and
  returns ``catalog_slot_occupied``. The slot is the query thread's own
  liveness, so a query blocked in a read holds it until the read returns.
- An INTERRUPTED or failed start of the query thread does not prove that no
  thread began: the slot is then held as ``uncertain`` until that thread is
  seen to end, and for this owner's lifetime if it never ran.
- LIFETIME. ``shutdown()`` refuses all new work (``catalog_owner_shut_down``)
  and shuts the runtime owner down too, keeping responsibility for existing
  work; ``diagnostics()`` reports it ``pending`` or ``uncertain``, never as
  done; ``settle(timeout)`` waits for BOTH the query thread and the runtime
  owner's worker.

The query thread is a DAEMON thread, deliberately and unlike the runtime
owner's worker: it owns no process, takes no lock (the readers take none)
and writes nothing; its only work is bounded reads of regular files. A
process that exits while it is blocked in a read abandons that read, and
nothing it owns outlives the process (its descriptors close with it). The
runtime owner's workers own processes, so they are NOT daemons.

Lifetimes. ``serve`` is the endpoint for a UI or any polling client: ONE
long-lived owner (so the cadence holds across its requests) answering each
JSON request line with one JSON answer line. A request line is at most
``MAX_REQUEST_LINE_BYTES`` INCLUDING its newline: a complete longer line is
refused and serving continues; an UNTERMINATED line already past the bound
is refused once and ENDS the session (``EXIT_FRAME_REFUSED``), because
finding its end could need unbounded input. The answer stream is a private
copy of stdout; fd 1 points at stderr, so nothing else can write into it.
``query`` is ONE-SHOT, on a fresh owner (so repeated one-shots each refresh;
the cadence is the long-lived owner's): it answers within about its wait,
then, when herd roots are given, WAITS for the runtime owner's owned work to
END before it prints and exits, with NO time bound on a stalled exec or a
kernel stall (``runtime_snapshot``). It does NOT wait for a query thread
still blocked in a read (see above). A UI uses ``serve``.

Discovery (``--discover-roots``; Jarvis ``--catalog-discover-roots``): a
trusted launch switch. On EVERY refresh, a first bounded source pass is read
ONLY to choose roots: ``mission_sources.roster.discovery`` names the
workspaces of ACTIVE missions whose own records are consistent and
unambiguous, with a resolved task id and a recorded workspace, the task and
the workspace each claimed once; a workspace claimed twice is a reported
CONFLICT, never resolved by order. Those roots, with any manual roots, are
captured by the SAME runtime owner and slot (discovered roots TOLERANTLY: an
unavailable one is reported, over-capacity ones are listed, none silently
dropped). A SECOND source pass, after the capture, builds the answer; the
roster then reads any workspace the capture did not observe as
``workspace_not_observed`` (unverified), never as absent or present, and
presents a binding live ONLY when its canonical identity (``roster
.binding_identity``: source-qualified ref, model, declared producer, task
id, workspace, identity source) AGREES with the discovery pass; a binding
in only one pass, or changed between them, is unverified
(``binding_not_confirmed_by_both_passes``) and a later stable refresh
acquires it. This is agreement, not atomicity: a change reverted between
the passes (ABA) is not detected. The two
passes are SEQUENTIAL, not one atomic generation: a mission that appears
between them is reported not observed, and one that vanishes is simply not
in the answer. PER-REFRESH BUDGET: two passes, so the refresh reads at most
the SUM of two per-query budgets (``runtime_capture.refresh_budget``:
2 x 64 MiB of store input, 2 x 2048 mission views, 2 x the registry bound),
not one. An observed agent or path never supplies a root or a member.

Stated limits: the registered stores and the registry are read in-process,
so a read the kernel never returns (for example on a hung network mount)
blocks the query thread and holds the slot; callers are still answered.
The runtime residuals are ``runtime_snapshot``'s, by name. Writes:
``register``, ``unregister`` and ``task-source --ensure`` write the
registry, through the registry's own writer; nothing else here writes
anything. No model is invoked anywhere on this path.
"""

import argparse
import json
import os
import shlex
import sys
import threading
import time

from mission_sources import contract as contract_module
from mission_sources import contract_v2 as contract_v2_module
from mission_sources import query as query_module
from mission_sources import query_v2 as query_v2_module
from mission_sources import registry as registry_module
from mission_sources import roster as roster_module
from target_runtime import herdr_task_source
from target_runtime import runtime_snapshot as snapshot_module

ENDPOINT = "di.mission_catalog"
ENDPOINT_VERSION = 1
ANSWER_KEYS = ("endpoint", "endpoint_version", "requested_at", "response",
               "problem", "detail", "runtime_capture", "served",
               "retry_after_seconds")
MAX_WAIT_SECONDS = 30
DEFAULT_WAIT_SECONDS = 10
#: The share of a refreshing request's wait the runtime capture may use.
RUNTIME_SHARE = 0.5
#: With discovery: the share the discovery read may use, before the capture.
DISCOVERY_SHARE = 0.25
#: The enforced cadence: the accepted contract's declared poll interval.
MIN_REFRESH_INTERVAL_SECONDS = contract_module.MIN_POLL_INTERVAL_SECONDS
MAX_REQUEST_LINE_BYTES = 4096

SERVED_REFRESHED = "refreshed"
SERVED_CACHED = "cached"
SERVED_NONE = "none"

PROBLEM_SLOT_OCCUPIED = "catalog_slot_occupied"
PROBLEM_QUERY_PENDING = "catalog_query_not_finished_within_wait"
PROBLEM_OWNER_SHUT_DOWN = "catalog_owner_shut_down"
PROBLEM_QUERY_FAILED = "catalog_query_failed"
PROBLEM_REFRESH_THROTTLED = "catalog_refresh_throttled"
PROBLEM_BAD_REQUEST = "catalog_bad_request"
PROBLEM_NO_RUNTIME_ROOT = "catalog_no_runtime_root"

WORK_SETTLED = snapshot_module.WORK_SETTLED
WORK_PENDING = snapshot_module.WORK_PENDING
WORK_UNCERTAIN = snapshot_module.WORK_UNCERTAIN

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 4
EXIT_REGISTRATION_REFUSED = 5
EXIT_FRAME_REFUSED = 6

# -- the DI-native task source (``task-source``) ---------------------------

#: The record ``task-source --ensure`` registers: herdctl's repository
#: registry as the ``task_native`` source, read by
#: ``herdr_task_source.read_herdr_task``.
TASK_SOURCE_ID = "di_herdr_tasks"
TASK_SOURCE_PRODUCER = "herdctl"
#: Where ``herdctl init`` writes ``repos.json`` (``herdr.registry``, which
#: this module may not import; parity pinned by test), under the home
#: directory.
HERDCTL_STORE_PARTS = (".config", "dodging-infinity")
TASK_SOURCE_REGISTERED = "registered"
TASK_SOURCE_NOT_REGISTERED = "not_registered"
#: The registry could not be read: whether the source is registered is
#: UNKNOWN, never "not registered".
TASK_SOURCE_UNKNOWN = "unknown"
TASK_SOURCE_KEYS = ("registry", "registry_health", "registry_problem",
                    "store_dir", "task_native_sources", "state", "source",
                    "outcome", "problem", "detail", "action")
OUTCOME_REFUSED = "refused"
#: The registry (or its directory) could not be WRITTEN for an operational reason (permission, disk, I/O),
#: as opposed to a refusal the registry itself decided: nothing was written, and nothing is repaired.
OUTCOME_FAILED = "failed"
PROBLEM_REGISTRY_DIRECTORY_ABSENT = "registry_directory_absent"
PROBLEM_REGISTRY_NOT_WRITABLE = "registry_not_writable"
#: A write failed and a fresh read shows the registry NOT as it was (the replacement landed before a later
#: step failed), or cannot be read: whether it changed is established by that read or stays explicit
#: uncertainty, never "nothing changed" and never a rollback that did not happen.
PROBLEM_REGISTRY_WRITE_UNCONFIRMED = "registry_write_unconfirmed"


def refresh_budget(source_passes):
    """The honest PER-REFRESH aggregate: ``source_passes`` sequential
    bounded source queries (2 with discovery, else 1), each within the
    accepted per-query limits (``query.limits()``). The refresh totals are
    the SUM of the per-pass budgets, stated as such; they are not one
    single-query limit."""
    per_pass = query_module.limits()
    return {"source_passes": source_passes,
            "per_pass_max_registry_bytes": per_pass["max_registry_bytes"],
            "per_pass_max_query_input_bytes": per_pass["max_query_input_bytes"],
            "per_pass_max_query_missions": per_pass["max_query_missions"],
            "sum_max_registry_bytes": source_passes
            * per_pass["max_registry_bytes"],
            "sum_max_query_input_bytes": source_passes
            * per_pass["max_query_input_bytes"],
            "sum_max_query_missions": source_passes
            * per_pass["max_query_missions"]}


def catalog_answer(requested_at, response, problem, detail, runtime_capture,
                   served=SERVED_NONE, retry_after_seconds=None):
    return {"endpoint": ENDPOINT, "endpoint_version": ENDPOINT_VERSION,
            "requested_at": requested_at, "response": response,
            "problem": problem, "detail": detail,
            "runtime_capture": runtime_capture, "served": served,
            "retry_after_seconds": retry_after_seconds}


def _native(capture, names, over_bound):
    """The capture facts ``herdr_task_source.runtime_agents`` needs, held
    in memory only: the requested names, those left out, the native stable
    field the projection returned for them (None when not observed), and
    the roots the capture reported over its capacity."""
    roots = (capture.roots or {}) if capture is not None else {}
    return {
        "requested": list(names or ()),
        "names_over_bound": over_bound,
        "native_cwd": None if capture is None or not capture.is_available()
        else capture.native_cwd,
        "over_capacity": list(roots.get("over_capacity") or ()),
    }


def _runtime_agents_hook(observation, native):
    def hook(missions, reading, now, sources):
        return herdr_task_source.runtime_agents(observation, missions,
                                                reading, now, native, sources)
    return hook


class CatalogService(object):
    """THE long-lived catalog owner (see the module's caller contract).
    ``monotonic`` exists for hermetic cadence tests; production passes
    nothing and gets ``time.monotonic``."""

    def __init__(self, registry_directory, clock, herd_roots=None,
                 listing_argv=None, monotonic=None, discover=False,
                 contract_version=contract_v2_module.DEFAULT_CONTRACT_VERSION,
                 delivery_store=None, git_transport=None,
                 delivery_presentations=None):
        if contract_version not in contract_v2_module.CONTRACT_VERSIONS or (
                type(contract_version) is not int):
            raise ValueError("contract_version must be one of %s"
                             % (contract_v2_module.CONTRACT_VERSIONS,))
        if delivery_store is not None and contract_version == 1:
            raise ValueError("a delivery store is read only by query contract"
                             " version 2")
        if delivery_presentations is not None and contract_version == 1:
            raise ValueError("delivery presentations are read only by query"
                             " contract version 2")
        # ONE version per owner, for its whole life: selection is a launch
        # decision, never a per-request one.
        self._contract_version = contract_version
        self._freshness = contract_v2_module.FRESHNESS_BY_VERSION[
            contract_version]
        self._composer = None
        self._presentations = None
        self._delivery_links = None
        self._mission_state = None
        if contract_version != 1:
            # Imported HERE, only when a version 2 owner is built: a
            # default (version 1) process loads neither Mission Core's
            # state service nor any delivery module (the accepted import
            # boundary, unchanged).
            from target_runtime import mission_state as mission_state_module
            self._composer = mission_state_module.MissionStateComposer(
                clock, delivery_store)
            # The same module, kept for the live code review's finished-
            # execution provider (``_finished_execution``): no second import.
            self._mission_state = mission_state_module
            # D1: the explicitly registered, read-only reader of the Grok
            # Bot's delivery presentations; it lives in the same module as the
            # composer above (one lazy import, nothing new), and is built only
            # when registered.
            self._presentations = None
            if delivery_presentations is not None:
                self._presentations = (
                    mission_state_module.DeliveryPresentationsReader(
                        delivery_presentations, delivery_store, clock).block)
            # The read-only reader behind every Mission's delivery_link, over
            # the SAME registered store (same module, nothing new), built
            # only when a store is registered; without it every workflow
            # Mission says no store is registered (Mission Core is not known
            # either way).
            if delivery_store is not None:
                self._delivery_links = (
                    mission_state_module.DeliveryLinksReader(
                        delivery_store, clock).bindings)
        self._registry = registry_module.SourceRegistry(registry_directory)
        self._clock = clock
        self._monotonic = monotonic or time.monotonic
        self._manual_roots = list(herd_roots or [])
        self._discover = discover is True
        if self._manual_roots or self._discover:
            self._runtime = snapshot_module.SnapshotService(
                self._manual_roots, clock, listing_argv)
        elif listing_argv is not None:
            raise ValueError("a listing argv needs herd roots or discovery")
        else:
            self._runtime = None
        self._mutex = threading.Lock()
        self._accepting = True
        self._in_request = False
        self._worker = None
        self._uncertain = False
        self._last_refresh = None
        self._cached = None
        # The evidence library's content reads (``artifact_content``): ONE
        # at a time per owner; a second is refused, never queued.
        self._content_slot = threading.Lock()
        # The live code review's reads (``code_review``): the same rule. The
        # git boundary is constructor-injected; None means the real one,
        # built only when a review is first asked of a version 2 owner.
        self._review_slot = threading.Lock()
        self._git_transport = git_transport

    def _held(self):
        return self._uncertain or (self._worker is not None
                                   and self._worker.is_alive())

    def diagnostics(self):
        with self._mutex:
            running = self._worker is not None and self._worker.is_alive()
            work = (WORK_PENDING if running else
                    WORK_UNCERTAIN if self._uncertain else WORK_SETTLED)
            state = {"accepting": self._accepting,
                     "in_request": self._in_request, "query_work": work,
                     "seconds_since_refresh": None
                     if self._last_refresh is None else round(
                         self._monotonic() - self._last_refresh, 3)}
        state["runtime"] = (None if self._runtime is None
                            else self._runtime.diagnostics())
        if self._contract_version != 1:
            state["contract_version"] = self._contract_version
        return state

    @property
    def contract_version(self):
        """The ONE query contract version this owner serves."""
        return self._contract_version

    def response_freshness(self, response, now):
        """The consumer operation of the version this owner serves
        (``contract.response_freshness`` for 1,
        ``contract_v2.response_freshness_v2`` for 2)."""
        return self._freshness(response, now)

    def artifact_content(self, mission_ref, artifact_id, cursor):
        """The evidence library's ONE content read
        (``target_runtime.artifact_content``): a recorded Mission Core
        result artifact, by reference, corroborated by this owner's last
        refreshed answer. Version 2 owners only; refused after shutdown;
        one read at a time (a second is refused, never queued). Always an
        answer; never starts a refresh."""
        # Imported here: a version 1 process stays within its accepted
        # import boundary until a content read is asked of it.
        from target_runtime import artifact_content as content_module
        if self._contract_version == 1:
            return content_module.refusal(
                content_module.PROBLEM_CONTRACT_VERSION, "this owner serves"
                " query contract version 1, which admits no artifact"
                " content", mission_ref, artifact_id, cursor, self._now())
        with self._mutex:
            accepting = self._accepting
        if not accepting:
            return content_module.refusal(
                content_module.PROBLEM_SHUT_DOWN, "this owner was shut down"
                " and takes no new work", mission_ref, artifact_id, cursor,
                self._now())
        if not self._content_slot.acquire(False):
            return content_module.refusal(
                content_module.PROBLEM_BUSY, "another content read is in"
                " progress; nothing was read", mission_ref, artifact_id,
                cursor, self._now())
        try:
            return content_module.read(
                self._registry, self._corroborating_answer, self._freshness,
                self._now, mission_ref, artifact_id, cursor)
        finally:
            self._content_slot.release()

    def code_review(self, mission_ref, task_id, snapshot=None, file_id=None):
        """The live code review's ONE read (``target_runtime.code_review``):
        what is changed in the workspace of a currently ACTIVE assignment (or
        of a COMPLETED Mission whose recorded execution and workspace the
        review admits), corroborated by this owner's last refreshed answer
        and the registry read now. With no ``file_id`` the overview; with one (and the
        ``snapshot`` it was listed in) that file's diff. Version 2 owners
        only; refused after shutdown; one read at a time (a second is
        refused, never queued). Always an answer; never starts a refresh;
        never writes to the workspace it reads."""
        # Imported here: a version 1 process stays within its accepted
        # import boundary until a review is asked of it.
        from target_runtime import code_review as review_module
        view = (review_module.VIEW_OVERVIEW if file_id is None
                else review_module.VIEW_FILE)
        if self._contract_version == 1:
            return review_module.refusal(
                review_module.PROBLEM_CONTRACT_VERSION, "this owner serves"
                " query contract version 1, which corroborates no assignment"
                " workspace", view, mission_ref, task_id, snapshot, file_id,
                self._now())
        with self._mutex:
            accepting = self._accepting
        if not accepting:
            return review_module.refusal(
                review_module.PROBLEM_SHUT_DOWN, "this owner was shut down"
                " and takes no new work", view, mission_ref, task_id,
                snapshot, file_id, self._now())
        if not self._review_slot.acquire(False):
            return review_module.refusal(
                review_module.PROBLEM_BUSY, "another review read is in"
                " progress; nothing was read", view, mission_ref, task_id,
                snapshot, file_id, self._now())
        try:
            if self._git_transport is None:
                self._git_transport = review_module.default_transport()
            return review_module.read(
                self._registry, self._corroborating_answer, self._freshness,
                self._now, self._git_transport, mission_ref, task_id,
                snapshot, file_id, finished=self._finished_execution())
        finally:
            self._review_slot.release()

    def _finished_execution(self):
        """The live code review's finished-execution provider: the evidence
        library's record-side admission of a COMPLETED Mission
        (``artifact_content.historical_execution``), given the SAME held
        answer the review judged, so both stand on one answer. Only the
        review's own corroboration of a TERMINAL assignment ever calls it;
        an active assignment never does."""
        # Imported here, as ``artifact_content`` does: nothing is loaded
        # until a review is asked of a version 2 owner.
        from target_runtime import artifact_content as content_module
        mission_state = self._mission_state

        def provider(held, mission_ref):
            return content_module.historical_execution(
                mission_state, self._registry, held, self._freshness,
                self._now, mission_ref)
        return provider

    def _corroborating_answer(self):
        """The last refreshed answer, as held (judged by the caller)."""
        with self._mutex:
            return self._cached

    def shutdown(self):
        """Refuse all new work; keep responsibility for existing work."""
        with self._mutex:
            self._accepting = False
        if self._runtime is not None:
            self._runtime.shutdown()
        return self.diagnostics()

    def settle(self, timeout=None):
        """Wait at most ``timeout`` (None: no bound) for the query thread,
        then for the runtime owner's worker; return ``diagnostics()``."""
        with self._mutex:
            worker = self._worker
        if worker is not None:
            try:
                worker.join(timeout)
            except RuntimeError:
                pass        # never started: an uncertain hold stands
        return self.settle_runtime(timeout)

    def settle_runtime(self, timeout=None):
        """Wait at most ``timeout`` (None: no bound) for the runtime owner's
        worker ONLY (it owns processes); return ``diagnostics()``."""
        if self._runtime is not None:
            self._runtime.settle(timeout)
        return self.diagnostics()

    def request(self, wait_seconds=DEFAULT_WAIT_SECONDS):
        if type(wait_seconds) not in (int, float) or not (
                0 < wait_seconds <= MAX_WAIT_SECONDS):
            raise ValueError("wait_seconds must be in (0, %d]"
                             % MAX_WAIT_SECONDS)
        deadline = time.monotonic() + wait_seconds
        requested_at = self._now()
        with self._mutex:
            if not self._accepting:
                return catalog_answer(
                    requested_at, None, PROBLEM_OWNER_SHUT_DOWN,
                    "this owner was shut down and takes no new work;"
                    " nothing was started", None)
            if self._in_request or self._held():
                return catalog_answer(
                    requested_at, None, PROBLEM_SLOT_OCCUPIED,
                    "a previous catalog refresh is outstanding; nothing was"
                    " started", None)
            if self._last_refresh is not None:
                since = self._monotonic() - self._last_refresh
                if since < MIN_REFRESH_INTERVAL_SECONDS:
                    return self._within_interval(
                        requested_at, MIN_REFRESH_INTERVAL_SECONDS - since)
            # The cadence anchor: the refresh START, taken before anything
            # starts, so a failed or unfinished refresh counts too.
            self._last_refresh = self._monotonic()
            self._in_request = True
        try:
            answer = self._request(requested_at, deadline, wait_seconds)
        finally:
            with self._mutex:
                self._in_request = False
        if answer["response"] is not None:
            with self._mutex:
                self._cached = answer
        return answer

    def _within_interval(self, requested_at, retry_after):
        """With the mutex held: the last refreshed answer UNCHANGED while
        the accepted freshness rule still reads it fresh; else throttled."""
        retry_after = round(retry_after, 3)
        cached = self._cached
        if cached is not None and self._freshness(
                cached["response"], requested_at)[0] == (
                    contract_module.HEALTH_FRESH):
            return dict(cached, served=SERVED_CACHED,
                        retry_after_seconds=retry_after)
        return catalog_answer(
            requested_at, None, PROBLEM_REFRESH_THROTTLED,
            "no refresh starts within %s s of the last one, and no still"
            "-fresh refreshed answer is held; nothing was started"
            % MIN_REFRESH_INTERVAL_SECONDS, None,
            retry_after_seconds=retry_after)

    def _now(self):
        now = self._clock()
        if type(now) is not int or now < 0:
            raise TypeError("clock must return a non-negative int")
        return now

    def _capture_runtime(self, wait_seconds, discovery, observation=None):
        """``(snapshot, runtime_capture, observed_roots, native)``. With
        discovery, ``observed_roots`` is the set the capture actually
        observed (the roster then reads any other workspace as not
        observed); without it, None (the manual-root semantics, unchanged).
        ``native``: the in-memory facts ``herdr_task_source.runtime_agents``
        needs (``_native``), never part of an answer."""
        names, over_bound = (observation.stable_names() if observation
                             else (None, 0))
        if self._runtime is None:
            return None, {"configured": False}, None, _native(
                None, names, over_bound)
        discovered = list(discovery["roots"]) if discovery else []
        runtime = {"configured": True, "discovery": discovery,
                   "registered_roots": list(self._manual_roots)}
        if not self._manual_roots and not discovered:
            runtime.update(available=False, problem=PROBLEM_NO_RUNTIME_ROOT,
                           detail="no registered root, and no canonical"
                           " binding names a root to observe; nothing was"
                           " started", observed_at=None, roots=None)
            return None, runtime, (set() if self._discover else None), \
                _native(None, names, over_bound)
        capture = self._runtime.request(wait_seconds * RUNTIME_SHARE,
                                        discovered, stable_names=names)
        runtime.update(available=capture.is_available(),
                       problem=capture.problem, detail=capture.detail,
                       observed_at=capture.observed_at, roots=capture.roots)
        observed = None
        if self._discover:
            captured = (capture.roots or {}).get("captured")
            observed = set(captured if captured is not None
                           else self._manual_roots) \
                if capture.is_available() else set()
        return capture.snapshot, runtime, observed, _native(
            capture, names, over_bound)

    def _request(self, requested_at, deadline, wait_seconds):
        if self._contract_version != 1:
            return self._request_v2(requested_at, deadline, wait_seconds)
        discovery = None
        observation = herdr_task_source.Observation()
        if self._discover:
            # ONE bounded read whose only use is choosing the roots to
            # observe; the answer is built from a SECOND read after the
            # capture (sequential reads, not one atomic generation).
            started = time.monotonic()
            pre = self._run_query(None, self._now(), started
                                  + wait_seconds * DISCOVERY_SHARE, None,
                                  readers=observation.pass_readers())
            if "response" not in pre:
                return catalog_answer(requested_at, None, pre["problem"],
                                      pre["detail"], None)
            discovery = self._anchored(roster_module.discovery(
                pre["response"]["missions"],
                roster_module.canonical_sources_complete(
                    pre["response"]["sources"])), observation)
        snapshot, runtime, observed, native = self._capture_runtime(
            wait_seconds, discovery, observation if self._discover else None)
        # ``now`` for the query: after the snapshot's own observation and
        # before any read of the registry or a store.
        # The answering pass must AGREE with the discovery pass: a binding
        # seen in only one pass, or changed between them, is unverified.
        confirmed = discovery["bindings"] if discovery else None
        outcome = self._run_query(
            snapshot, self._now(), deadline, observed, confirmed,
            readers=observation.pass_readers(),
            runtime_agents=_runtime_agents_hook(observation, native))
        if "response" not in outcome:
            return catalog_answer(requested_at, None, outcome["problem"],
                                  outcome["detail"], runtime)
        runtime = dict(runtime, refresh_budget=refresh_budget(
            2 if self._discover else 1))
        return catalog_answer(requested_at, outcome["response"], None, None,
                              runtime, served=SERVED_REFRESHED)

    def _anchored(self, discovery, observation):
        """``discovery`` plus ``listing_anchor``: when NO root is to be
        observed (no registered root, no root discovered), the ONE
        task-native root (``Observation.anchor_root``) the capture observes
        so that a listing exists to verify runtime roles against; never a
        membership, and disclosed in the answer's runtime capture."""
        anchor = None
        if not self._manual_roots and not discovery["roots"]:
            anchor = observation.anchor_root()
        if anchor is not None:
            discovery = dict(discovery, roots=[anchor])
        return dict(discovery, listing_anchor=anchor)

    def _run_query(self, snapshot, now, join_until, observed_roots,
                   confirmed_bindings=None, readers=None,
                   runtime_agents=None):
        """One source query on the query thread (the slot), joined until
        ``join_until`` at the latest: ``{"response"}`` or ``{"problem",
        "detail"}``."""
        outcome = {}
        worker = threading.Thread(target=self._query,
                                  args=(snapshot, now, outcome,
                                        observed_roots, confirmed_bindings,
                                        readers, runtime_agents),
                                  name="mission-catalog-query", daemon=True)
        with self._mutex:
            self._worker = worker
            try:
                worker.start()
            except BaseException:
                # An interrupted or failed start does not PROVE that no
                # thread began: hold until it is seen to end.
                if not outcome.get("ended"):
                    self._uncertain = True
                raise
        worker.join(max(join_until - time.monotonic(), 0))
        if worker.is_alive():
            return {"problem": PROBLEM_QUERY_PENDING,
                    "detail": "the query did not finish within the wait; it"
                    " keeps the slot until it ends, and its result will not"
                    " be returned"}
        if "failed" in outcome:
            return {"problem": PROBLEM_QUERY_FAILED,
                    "detail": outcome["failed"]}
        return {"response": outcome["response"]}

    def _query(self, snapshot, now, outcome, observed_roots=None,
               confirmed_bindings=None, readers=None, runtime_agents=None):
        try:
            outcome["response"] = query_module.query(
                self._registry, snapshot, now,
                readers=readers or herdr_task_source.READERS,
                observed_roots=observed_roots,
                confirmed_bindings=confirmed_bindings,
                runtime_agents=runtime_agents)
        except Exception as exc:                          # noqa: BLE001
            # Could not look: reported, never shown as a response.
            outcome["failed"] = "the query raised %s" % type(exc).__name__
        finally:
            with self._mutex:
                outcome["ended"] = True
                self._uncertain = False

    # -- query contract version 2 (an owner launched for it) --------------
    #
    # The same cadence, slot, wait shares and cache. A version 2 refresh is:
    # (1) a PRE pass on the query thread: the discovery pass (with
    # --discover-roots) and the composer's load 1 of every registered Mission
    # Core store, which samples every head cursor BEFORE any report is
    # collected; (2) ONE chain capture by the same runtime owner and slot;
    # (3) the version 2 query on the query thread, whose composer collects
    # the reports, reads each Mission Core store once more (load 2) and runs
    # Mission Core's own observe with the sampled cursors unchanged.

    def _run_job(self, job, join_until):
        """``_run_query``'s slot and join semantics, for any ONE job on the
        query thread: ``{"response"}`` or ``{"problem", "detail"}``."""
        outcome = {}

        def work():
            try:
                outcome["response"] = job()
            except Exception as exc:                      # noqa: BLE001
                outcome["failed"] = "the query raised %s" % (
                    type(exc).__name__)
            finally:
                with self._mutex:
                    outcome["ended"] = True
                    self._uncertain = False

        worker = threading.Thread(target=work, name="mission-catalog-query",
                                  daemon=True)
        with self._mutex:
            self._worker = worker
            try:
                worker.start()
            except BaseException:
                if not outcome.get("ended"):
                    self._uncertain = True
                raise
        worker.join(max(join_until - time.monotonic(), 0))
        if worker.is_alive():
            return {"problem": PROBLEM_QUERY_PENDING,
                    "detail": "the query did not finish within the wait; it"
                    " keeps the slot until it ends, and its result will not"
                    " be returned"}
        if "failed" in outcome:
            return {"problem": PROBLEM_QUERY_FAILED,
                    "detail": outcome["failed"]}
        return {"response": outcome["response"]}

    def _pre_v2(self, budget, observation):
        """``(discovery, samples)``: the discovery pass (or None) and the
        composer's load 1 of every registered Mission Core store, BOTH held
        against the refresh's shared read ``budget``."""
        discovery = None
        if self._discover:
            views, complete = query_v2_module.discovery_input(
                self._registry, self._now(), budget.pass_budget(),
                observation.pass_readers())
            discovery = self._anchored(
                roster_module.discovery(views, complete), observation)
        try:
            document, _ = self._registry.load_measured(absent_is_empty=False)
            registered = document["sources"]
        except registry_module.RegistryError:
            registered = []
        return discovery, self._composer.sample(registered, budget.stage())

    def _expected_reviews(self, discovery, samples):
        """The task each CAPTURED root's canonical review must belong to,
        for the roots the capture will take (registered, then discovered,
        within the capture's own capacity)."""
        expected = self._composer.expected_reviews(samples)
        order = list(self._manual_roots) + (
            list(discovery["roots"]) if discovery else [])
        kept = []
        for root in order:
            if root in expected and root not in kept:
                kept.append(root)
        return dict((root, expected[root])
                    for root in kept[:snapshot_module.MAX_HERD_ROOTS])

    def _capture_chains(self, wait_seconds, discovery, samples,
                        observation=None):
        """``_capture_runtime`` for a CHAIN request: ``(snapshot,
        chain_observation, runtime_capture, observed_roots, native)``."""
        names, over_bound = (observation.stable_names() if observation
                             else (None, 0))
        if self._runtime is None:
            return None, None, {"configured": False}, None, _native(
                None, names, over_bound)
        discovered = list(discovery["roots"]) if discovery else []
        runtime = {"configured": True, "discovery": discovery,
                   "registered_roots": list(self._manual_roots)}
        if not self._manual_roots and not discovered:
            runtime.update(available=False, problem=PROBLEM_NO_RUNTIME_ROOT,
                           detail="no registered root, and no canonical"
                           " binding names a root to observe; nothing was"
                           " started", observed_at=None, roots=None)
            return None, None, runtime, (set() if self._discover
                                         else None), _native(
                None, names, over_bound)
        capture = self._runtime.request(
            wait_seconds * RUNTIME_SHARE, discovered,
            chains={"expected_reviews": self._expected_reviews(discovery,
                                                               samples)},
            stable_names=names)
        runtime.update(available=capture.is_available(),
                       problem=capture.problem, detail=capture.detail,
                       observed_at=capture.observed_at, roots=capture.roots)
        observed = None
        if self._discover:
            captured = (capture.roots or {}).get("captured")
            observed = set(captured if captured is not None
                           else self._manual_roots) \
                if capture.is_available() else set()
        return (capture.snapshot, capture.chain_observation, runtime,
                observed, _native(capture, names, over_bound))

    def _request_v2(self, requested_at, deadline, wait_seconds):
        started = time.monotonic()
        # ONE shared read budget for the WHOLE refresh: the ALREADY STATED
        # per-refresh total, enforced against every read attempt of every
        # stage (round 3, B2; ``query_v2.RefreshReadBudget``).
        stated = refresh_budget(4 if self._discover else 3)
        budget = query_v2_module.RefreshReadBudget(
            stated["sum_max_query_input_bytes"])
        observation = herdr_task_source.Observation()
        pre = self._run_job(lambda: self._pre_v2(budget, observation),
                            started + wait_seconds * DISCOVERY_SHARE)
        if "response" not in pre:
            return catalog_answer(requested_at, None, pre["problem"],
                                  pre["detail"], None)
        discovery, samples = pre["response"]
        snapshot, chains, runtime, observed, native = self._capture_chains(
            wait_seconds, discovery, samples,
            observation if self._discover else None)
        confirmed = discovery["bindings"] if discovery else None
        now = self._now()
        collection = budget.stage()

        def compose(source, views, chain_reading):
            return self._composer.compose(samples, source, views,
                                          chain_reading, collection)

        outcome = self._run_job(lambda: query_v2_module.query_v2(
            self._registry, snapshot, chains, now,
            readers=observation.pass_readers(), observed_roots=observed,
            confirmed_bindings=confirmed, compose=compose,
            budget=budget.pass_budget(),
            runtime_agents=_runtime_agents_hook(observation, native),
            delivery_presentations=self._presentations,
            delivery_links=self._delivery_links),
            deadline)
        if "response" not in outcome:
            return catalog_answer(requested_at, None, outcome["problem"],
                                  outcome["detail"], runtime)
        # Passes: discovery (with --discover-roots), the composer's load 1,
        # the query, and the composer's load 2 (Mission Core stores only).
        runtime = dict(runtime, refresh_budget=dict(
            stated, shared_input=budget.summary()))
        return catalog_answer(requested_at, outcome["response"], None, None,
                              runtime, served=SERVED_REFRESHED)


# -- the endpoint processes ------------------------------------------------

def _request_from_line(line):
    """A request line: one JSON object, ``{"request": "catalog"}`` with an
    optional ``wait_seconds``, or ``{"request": "diagnostics"}``."""
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if type(value) is not dict or value.get("request") not in (
            "catalog", "diagnostics"):
        return None
    allowed = {"request", "wait_seconds"} if value["request"] == "catalog" \
        else {"request"}
    if not set(value) <= allowed:
        return None
    wait = value.get("wait_seconds", DEFAULT_WAIT_SECONDS)
    if type(wait) not in (int, float) or not 0 < wait <= MAX_WAIT_SECONDS:
        return None
    return value["request"], wait


def _write_line(out_fd, value):
    data = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    while data:
        data = data[os.write(out_fd, data):]


def serve(owner, in_stream, out_fd, clock):
    """Answer each request line with one answer line on ``out_fd`` until end
    of input; then shut the owner down (existing work stays its
    responsibility). Every read is bounded: a line is read with a limit of
    ``MAX_REQUEST_LINE_BYTES + 1``, and an unterminated over-long line ENDS
    the session instead of being read on to its newline."""
    try:
        while True:
            line = in_stream.readline(MAX_REQUEST_LINE_BYTES + 1)
            if not line:
                return EXIT_OK
            if len(line) > MAX_REQUEST_LINE_BYTES:
                ended = not line.endswith(b"\n")
                _write_line(out_fd, catalog_answer(
                    clock(), None, PROBLEM_BAD_REQUEST,
                    "a request line exceeds %d bytes%s"
                    % (MAX_REQUEST_LINE_BYTES,
                       "; it is unterminated, so the session ends"
                       if ended else ""), None))
                if ended:
                    return EXIT_FRAME_REFUSED
                continue
            if not line.strip():
                continue
            parsed = _request_from_line(line)
            if parsed is None:
                answer = catalog_answer(
                    clock(), None, PROBLEM_BAD_REQUEST,
                    'expected {"request": "catalog"} with an optional'
                    ' wait_seconds, or {"request": "diagnostics"}', None)
            elif parsed[0] == "diagnostics":
                answer = {"endpoint": ENDPOINT,
                          "endpoint_version": ENDPOINT_VERSION,
                          "diagnostics": owner.diagnostics()}
            else:
                answer = owner.request(parsed[1])
            _write_line(out_fd, answer)
    finally:
        owner.shutdown()


# -- the DI-native task source: the supported setup step -------------------

def default_task_store_dir():
    """The directory where ``herdctl init`` writes ``repos.json`` for this
    user (``~/.config/dodging-infinity``), or None when the home directory
    does not resolve to an absolute path."""
    home = os.path.expanduser("~")
    if not os.path.isabs(home):
        return None
    return os.path.normpath(os.path.join(home, *HERDCTL_STORE_PARTS))


def _ensure_command(registry_directory, store_dir):
    """The exact command that registers ``store_dir`` (``--store-dir`` only
    when it is not the default)."""
    text = "python3 dicatalog.py task-source --registry %s" % shlex.quote(
        registry_directory)
    if store_dir != default_task_store_dir():
        text += " --store-dir %s" % shlex.quote(store_dir)
    return text + " --ensure"


def _task_source_action(problem, registry, store_dir):
    """The operator's next step for ``problem``, naming exact commands."""
    directory = shlex.quote(registry.directory)
    path = shlex.quote(registry.path)
    ensure = _ensure_command(registry.directory, store_dir)
    sources = "python3 dicatalog.py sources --registry %s" % directory
    remove = ("python3 dicatalog.py unregister --registry %s --source-id"
              % directory)
    again = ", then run again: " + ensure
    if problem in (contract_module.PROBLEM_TASK_NATIVE_SOURCE_NOT_REGISTERED,
                   registry_module.PROBLEM_REGISTRY_ABSENT):
        return ensure
    if problem == PROBLEM_REGISTRY_DIRECTORY_ABSENT:
        return ("create the registry directory, then register the source:"
                " mkdir -m 700 %s && %s" % (directory, ensure))
    if problem == registry_module.PROBLEM_REGISTRY_PERMISSIONS:
        return ("the registry file is group- or other-accessible, so it was"
                " refused and not used; check who changed it and that every"
                " source in it is yours (cat %s), restrict it (chmod 600 %s)"
                % (path, path) + again)
    if problem == registry_module.PROBLEM_REGISTRY_UNREADABLE:
        return ("the registry file is a symbolic link, not a regular file,"
                " or unreadable, and was left as it is; put the private"
                " regular file it should be at %s" % path + again)
    if problem == registry_module.PROBLEM_REGISTRY_MALFORMED:
        return ("the registry file is not a valid source registry and was"
                " left as it is; restore %s from a copy you trust, or move it"
                " aside and register your sources again" % path + again)
    if problem == registry_module.PROBLEM_REGISTRY_INCOMPATIBLE:
        return ("the registry file has another schema version and was left"
                " as it is; read it with the release that wrote it, or move"
                " %s aside and register your sources again" % path + again)
    if problem == registry_module.PROBLEM_SOURCE_ID_CONFLICT:
        return ("the id %s is already registered for another source;"
                " nothing was changed. Inspect it (%s); only if that record"
                " is wrong, remove it (%s %s)"
                % (TASK_SOURCE_ID, sources, remove, TASK_SOURCE_ID) + again)
    if problem == registry_module.PROBLEM_REGISTRY_FULL:
        return ("the registry holds its maximum of %d sources; nothing was"
                " changed. Remove one you no longer need (%s ID; see %s)"
                % (registry_module.MAX_REGISTERED_SOURCES, remove, sources)
                + again)
    if problem == registry_module.PROBLEM_REGISTRY_SIZE:
        return ("the registry would exceed its %d-byte bound; nothing was"
                " changed. Remove a source you no longer need (%s ID; see"
                " %s)" % (registry_module.MAX_REGISTRY_BYTES, remove, sources)
                + again)
    if problem == PROBLEM_REGISTRY_NOT_WRITABLE:
        return ("the registry could not be written and still reads as it did; it was not repaired."
                " Check who owns the directory and that you may write there (ls -ld %s), and free"
                " disk space if that is the cause" % directory + again)
    if problem == PROBLEM_REGISTRY_WRITE_UNCONFIRMED:
        check = "python3 dicatalog.py task-source --registry %s" % directory
        if store_dir != default_task_store_dir():
            check += " --store-dir %s" % shlex.quote(store_dir)
        return ("the write reported an error, so the registry may have been updated; nothing was rolled"
                " back. Inspect it (%s) and check the source (%s); run %s again only if the source is"
                " not listed" % (sources, check, ensure))
    if problem == registry_module.PROBLEM_SOURCE_INVALID:
        return ("pass --store-dir as an absolute, normalised path (no"
                " trailing slash, no . or .. part); nothing was changed")
    return "nothing was changed; inspect the registry: " + sources


def _unreadable_report(registry, store_dir, exc):
    """The report for a registry that could NOT be read, built from that failure alone: whether the
    source is registered is ``unknown``, the health and ``registry_problem`` are the failure's own, and
    nothing about any earlier read is carried over."""
    report = dict.fromkeys(TASK_SOURCE_KEYS)
    report.update(registry=registry.directory, store_dir=store_dir)
    next_step = exc.problem
    if exc.problem == registry_module.PROBLEM_REGISTRY_ABSENT and (
            not os.path.isdir(registry.directory)):
        next_step = PROBLEM_REGISTRY_DIRECTORY_ABSENT
    report.update(
        registry_health=query_module._REGISTRY_PROBLEM_HEALTH.get(
            exc.problem, contract_module.HEALTH_MALFORMED),
        registry_problem=exc.problem, state=TASK_SOURCE_UNKNOWN,
        problem=exc.problem, detail=str(exc),
        action=_task_source_action(next_step, registry, store_dir))
    return report


def task_source_status(registry, store_dir):
    """READ-ONLY: is a ``task_native`` source registered for ``store_dir``
    (by real path, under any id)? One report (``TASK_SOURCE_KEYS``); a
    registry that cannot be read is ``unknown`` with its own problem and
    health, never an empty registry."""
    report = dict.fromkeys(TASK_SOURCE_KEYS)
    report.update(registry=registry.directory, store_dir=store_dir)
    try:
        document = registry.load(absent_is_empty=False)
    except registry_module.RegistryError as exc:
        return _unreadable_report(registry, store_dir, exc)
    natives = [dict(s) for s in document["sources"]
               if s["kind"] == contract_module.KIND_TASK_NATIVE]
    wanted = os.path.realpath(store_dir)
    matching = [s for s in natives
                if os.path.realpath(s["store_dir"]) == wanted]
    report.update(registry_health=contract_module.HEALTH_FRESH,
                  task_native_sources=natives)
    if matching:
        report.update(state=TASK_SOURCE_REGISTERED, source=matching[0])
        return report
    problem = contract_module.PROBLEM_TASK_NATIVE_SOURCE_NOT_REGISTERED
    report.update(
        state=TASK_SOURCE_NOT_REGISTERED, problem=problem,
        detail="no task_native source names %s, so the teams herdctl"
        " registers there are not discovered" % store_dir,
        action=_task_source_action(problem, registry, store_dir))
    return report


def _registry_directory_exists(registry):
    """The courtesy precheck: does the registry directory exist? Not the guarantee: the writer itself
    refuses to create it (``register(create_directory=False)``)."""
    return os.path.isdir(registry.directory)


def _directory_absent_report(registry, store_dir):
    """The refusal for a registry directory that does not exist (this step never creates it), built from
    a fresh read so nothing stale is carried over."""
    return dict(
        task_source_status(registry, store_dir), outcome=OUTCOME_REFUSED,
        problem=PROBLEM_REGISTRY_DIRECTORY_ABSENT,
        detail="the registry directory %s does not exist; this step does"
        " not create it" % registry.directory,
        action=_task_source_action(PROBLEM_REGISTRY_DIRECTORY_ABSENT,
                                   registry, store_dir))


def _write_failure_report(registry, store_dir, exc):
    """The report for an OPERATIONAL write failure (permission, disk, I/O). The registry is NEVER repaired,
    and nothing is assumed about what the failed write left behind: the atomic writer replaces the file
    and only THEN syncs the directory, so an error can arrive with the new registry already in place. The
    current state is therefore RE-READ (read-only) and the report built from that read: still not
    registered (the failure came before the replacement: said so), registered now (the replacement
    landed: not rolled back, said so), or unreadable (explicit uncertainty). Always a failure."""
    what = "%s: %s" % (type(exc).__name__, exc.strerror or exc)
    now = task_source_status(registry, store_dir)
    if now["state"] == TASK_SOURCE_NOT_REGISTERED:
        # the same read that proves it: the registry still does not register the source
        return dict(now, outcome=OUTCOME_FAILED,
                    problem=PROBLEM_REGISTRY_NOT_WRITABLE,
                    detail="%s; the registry still reads as it did" % what,
                    action=_task_source_action(PROBLEM_REGISTRY_NOT_WRITABLE,
                                               registry, store_dir))
    if now["state"] == TASK_SOURCE_REGISTERED:
        detail = ("%s, reported after the registry was replaced: it now registers the source, was not"
                  " rolled back, and the write's durability was not confirmed" % what)
    else:
        detail = ("%s; the registry cannot be read now, so it may have been updated" % what)
    return dict(now, outcome=OUTCOME_FAILED,
                problem=PROBLEM_REGISTRY_WRITE_UNCONFIRMED, detail=detail,
                action=_task_source_action(PROBLEM_REGISTRY_WRITE_UNCONFIRMED,
                                           registry, store_dir))


def ensure_task_source(registry, store_dir, now):
    """Register ``TASK_SOURCE_ID`` for ``store_dir`` through the registry's
    own ``register`` when no ``task_native`` source names that store;
    otherwise write nothing (``unchanged``). An unreadable registry is
    refused before any lock is taken; a missing registry directory is
    refused, never created; every refusal is the registry's own."""
    report = task_source_status(registry, store_dir)
    if report["state"] == TASK_SOURCE_REGISTERED:
        return dict(report, outcome=registry_module.OUTCOME_UNCHANGED)
    if report["registry_problem"] not in (
            None, registry_module.PROBLEM_REGISTRY_ABSENT):
        return dict(report, outcome=OUTCOME_REFUSED)
    if not _registry_directory_exists(registry):
        return _directory_absent_report(registry, store_dir)
    try:
        # the writer is told NEVER to create the directory: the precheck above can be overtaken by a
        # directory that disappears before the lock is taken
        result = registry.register(
            TASK_SOURCE_ID, contract_module.KIND_TASK_NATIVE, store_dir,
            TASK_SOURCE_PRODUCER, contract_module.DEFAULT_MAX_AGE_SECONDS,
            now, create_directory=False)
    except registry_module.RegistryError as exc:
        if exc.problem in query_module._REGISTRY_PROBLEM_HEALTH:
            # the WRITER's locked read failed (the registry changed since the status read): the report
            # is rebuilt from that failure, never the stale "fresh, not registered"
            return dict(_unreadable_report(registry, store_dir, exc),
                        outcome=OUTCOME_REFUSED)
        return dict(report, outcome=OUTCOME_REFUSED, problem=exc.problem,
                    detail=str(exc), action=_task_source_action(
                        exc.problem, registry, store_dir))
    except FileNotFoundError as exc:
        if not _registry_directory_exists(registry):
            return _directory_absent_report(registry, store_dir)
        return _write_failure_report(registry, store_dir, exc)
    except OSError as exc:
        return _write_failure_report(registry, store_dir, exc)
    return dict(task_source_status(registry, store_dir),
                outcome=result["outcome"])


def _task_source(registry, args, clock):
    """``task-source``: one JSON report line; exit 0 only when the source is
    registered (check) or was registered or found unchanged (``--ensure``).
    """
    store_dir = args.store_dir or default_task_store_dir()
    if store_dir is None:
        print("dicatalog: the home directory is not absolute; pass"
              " --store-dir", file=sys.stderr)
        return EXIT_USAGE
    if args.ensure:
        report = ensure_task_source(registry, store_dir, clock())
        code = (EXIT_REGISTRATION_REFUSED
                if report["outcome"] in (OUTCOME_REFUSED, OUTCOME_FAILED)
                else EXIT_OK)
        if code != EXIT_OK:
            print("dicatalog: %s (%s): %s"
                  % (report["outcome"], report["problem"], report["detail"]),
                  file=sys.stderr)
    else:
        report = task_source_status(registry, store_dir)
        code = (EXIT_OK if report["state"] == TASK_SOURCE_REGISTERED
                else EXIT_UNAVAILABLE)
        if code != EXIT_OK:
            print("dicatalog: task_native source %s (%s): %s"
                  % (report["state"], report["problem"], report["detail"]),
                  file=sys.stderr)
    _emit(report)
    return code


def _parser():
    parser = argparse.ArgumentParser(
        prog="dicatalog.py",
        description="The producer-neutral mission catalog: explicit source"
        " registration and a read-only, versioned catalog endpoint. It reads"
        " registered sources and controls nothing.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
            ("sources", "print the registered sources as recorded"),
            ("register", "register ONE source (writes the registry only)"),
            ("unregister", "remove ONE source (writes the registry only)"),
            ("task-source", "report whether the DI-native task_native"
                            " source (herdctl's repository registry) is"
                            " registered; --ensure registers it (writes the"
                            " registry only)"),
            ("query", "ONE-SHOT: print one catalog answer as one JSON line"),
            ("serve", "LONG-LIVED: answer request lines on stdin, one JSON"
                      " answer line each, on stdout")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--registry", required=True, metavar="DIR")
        if name == "register":
            sub.add_argument("--source-id", required=True)
            sub.add_argument("--kind", required=True,
                             choices=contract_module.SOURCE_KINDS)
            sub.add_argument("--store-dir", required=True, metavar="PATH")
            sub.add_argument("--producer", required=True)
            sub.add_argument("--max-age-seconds", type=int,
                             default=contract_module.DEFAULT_MAX_AGE_SECONDS)
        elif name == "unregister":
            sub.add_argument("--source-id", required=True)
        elif name == "task-source":
            sub.add_argument("--store-dir", metavar="PATH",
                             help="the directory holding herdctl's"
                             " repos.json (default: ~/.config/"
                             "dodging-infinity, where herdctl init writes"
                             " it)")
            sub.add_argument("--ensure", action="store_true",
                             help="register it when absent; otherwise"
                             " write nothing")
        elif name in ("query", "serve"):
            sub.add_argument("--herd-root", action="append", metavar="PATH",
                             help="an absolute, resolved herd root for the"
                             " runtime snapshot (repeat for several); MANUAL"
                             " and strict")
            sub.add_argument("--discover-roots", action="store_true",
                             help="on every refresh, also observe the"
                             " workspace roots that canonical active"
                             " bindings in the REGISTERED sources name")
            sub.add_argument("--contract-version", type=int,
                             choices=contract_v2_module.CONTRACT_VERSIONS,
                             default=(
                                 contract_v2_module.DEFAULT_CONTRACT_VERSION),
                             help="the ONE query contract version this"
                             " process serves (default 1); comparing two"
                             " versions takes two processes")
            sub.add_argument("--delivery-store", metavar="DIR",
                             help="version 2 only: the explicitly registered"
                             " delivery store directory")
            sub.add_argument("--delivery-presentations", metavar="DIR",
                             help="version 2 only: the explicitly registered"
                             " Grok Bot state directory whose delivery"
                             " presentations are read (read-only; only the"
                             " delivery_presentations map)")
            if name == "query":
                sub.add_argument("--wait-seconds", type=float,
                                 default=DEFAULT_WAIT_SECONDS)
    return parser


def _version_options(args):
    """The owner's version options from parsed arguments; version 1 passes
    none, so a version 1 owner is built exactly as before."""
    if args.contract_version == contract_v2_module.DEFAULT_CONTRACT_VERSION:
        return {}
    return {"contract_version": args.contract_version,
            "delivery_store": args.delivery_store,
            "delivery_presentations": args.delivery_presentations}


def _emit(value):
    sys.stdout.write(json.dumps(value, sort_keys=True) + "\n")
    sys.stdout.flush()


def main(argv=None, clock=None, listing_argv=None, stdin=None):
    """``listing_argv`` exists for hermetic tests (a FAKE listing run through
    the same owned runner); production passes nothing and gets
    ``runtime_snapshot.LISTING_ARGV``."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else EXIT_USAGE
    clock = clock or snapshot_module.production_clock
    for path in [args.registry] + list(getattr(args, "herd_root", None)
                                       or []) + ([args.delivery_store] if (
                                           getattr(args, "delivery_store",
                                                   None)) else []) + (
            [args.delivery_presentations] if getattr(
                args, "delivery_presentations", None) else []) + (
            [args.store_dir] if args.command == "task-source"
            and args.store_dir is not None else []):
        if not os.path.isabs(path):
            print("dicatalog: paths must be absolute: %s" % path,
                  file=sys.stderr)
            return EXIT_USAGE
    if getattr(args, "delivery_store", None) and (
            args.contract_version == 1):
        print("dicatalog: --delivery-store needs --contract-version 2",
              file=sys.stderr)
        return EXIT_USAGE
    if getattr(args, "delivery_presentations", None) and (
            args.contract_version == 1):
        print("dicatalog: --delivery-presentations needs --contract-version"
              " 2", file=sys.stderr)
        return EXIT_USAGE
    registry = registry_module.SourceRegistry(args.registry)
    if args.command == "task-source":
        return _task_source(registry, args, clock)
    try:
        if args.command == "sources":
            _emit({"registry": args.registry,
                   "sources": registry.sources()})
            return EXIT_OK
        if args.command == "register":
            _emit(registry.register(args.source_id, args.kind,
                                    args.store_dir, args.producer,
                                    args.max_age_seconds, clock()))
            return EXIT_OK
        if args.command == "unregister":
            _emit(registry.unregister(args.source_id))
            return EXIT_OK
    except registry_module.RegistryError as exc:
        print("dicatalog: refused (%s): %s" % (exc.problem, exc),
              file=sys.stderr)
        return EXIT_REGISTRATION_REFUSED
    roots = args.herd_root or None
    discover = bool(getattr(args, "discover_roots", False))
    argv_for_listing = (listing_argv or snapshot_module.LISTING_ARGV) \
        if roots or discover else None
    if args.command == "serve":
        sys.stdout.flush()
        out_fd = os.dup(1)
        os.set_inheritable(out_fd, False)
        os.dup2(2, 1)
        owner = CatalogService(args.registry, clock, roots, argv_for_listing,
                               discover=discover, **_version_options(args))
        try:
            return serve(owner, stdin or sys.stdin.buffer, out_fd, clock)
        finally:
            os.close(out_fd)
    if not 0 < args.wait_seconds <= MAX_WAIT_SECONDS:
        print("dicatalog: --wait-seconds must be in (0, %d]"
              % MAX_WAIT_SECONDS, file=sys.stderr)
        return EXIT_USAGE
    owner = CatalogService(args.registry, clock, roots, argv_for_listing,
                           discover=discover, **_version_options(args))
    try:
        answer = owner.request(args.wait_seconds)
    finally:
        # ONE-SHOT: no new work; the runtime owner's owned work is waited
        # for (no time bound); a query thread blocked in a read is not.
        owner.shutdown()
        if roots or discover:
            owner.settle_runtime(None)
    if answer["problem"] is not None:
        print("dicatalog: no catalog (%s: %s)"
              % (answer["problem"], answer["detail"]), file=sys.stderr)
    _emit(answer)
    return EXIT_OK if answer["response"] is not None else EXIT_UNAVAILABLE
