"""The record-link prerequisite: each version 2 Mission's DECLARED delivery
link (``missions[].delivery_link``), composed by ``query_v2`` for EVERY Mission
and read from the registered delivery store by
``target_runtime.mission_state.DeliveryLinksReader``.

The rule:
- Only a workflow-store Mission can be linked, and only by a delivery record
  whose OWN ``mission.workflow_id`` equals the Mission's ``native_id``.
- A Mission of any other model (Mission Core included) is
  ``unlinked_model_not_recorded``: NOT KNOWN, because no delivery record names
  such a Mission. It is never "no match", and never linked by a record whose
  workflow id happens to equal its id.
- Strict multiplicity: two records naming the workflow, or two registered
  workflow Missions holding the id, are ``unlinked_ambiguous``, with no id.
- Uniqueness is claimed only over a FULLY inspected Mission side; otherwise the
  state is ``unlinked_sources_incomplete``, never "no match".
- AT MOST ONE delivery id, set exactly when linked, of the ceremony's own shape;
  never a list or a count. ``basis`` names the rule, and never an engineering
  task id, a receipt or "newest".

What is REAL: contract_v2, query_v2, sources._reading, the reader, the delivery
layer's unchanged validator (behind ``mission_parent.validated_attestation``)
and its minting function ``new_authorization``. The pre-change golden records
are also real: they are read as DATA from tests/_pr_delivery_pre_change_goldens.py,
whose JSON literal is checked against its pinned digest, and that module is
never imported. What is SYNTHETIC: the Mission views (minimal dicts), the
registry document, and every delivery store and Grok state directory, built in
a private TemporaryDirectory.

CONTAINMENT is this test's own obligation; its runner is not a sandbox. BEFORE
any product module is imported:
- an audit hook is installed that raises on socket creation, process start and
  signals to another process;
- socket.socket, subprocess.Popen, os.fork, os.posix_spawn and the os.exec*
  family are replaced with raisers.
Every test asserts in its cleanup that none was reached, and one test asserts
that no other test module was imported.
"""

import os
import socket
import subprocess
import sys

BLOCKED_EVENTS = frozenset((
    "socket.__new__", "subprocess.Popen", "os.system", "os.exec",
    "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "os.kill",
    "os.killpg",
))
REACHED = []


def _audit(event, args):
    if event in BLOCKED_EVENTS:
        REACHED.append(event)
        raise RuntimeError("test_catalog_delivery_link: blocked %s" % event)


sys.addaudithook(_audit)


class _RefusedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        REACHED.append("socket.socket")
        raise RuntimeError("test_catalog_delivery_link: socket is refused")


class _RefusedPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        REACHED.append("subprocess.Popen")
        raise RuntimeError("test_catalog_delivery_link: Popen is refused")


def _raiser(name):
    def refuse(*args, **kwargs):
        REACHED.append(name)
        raise RuntimeError("test_catalog_delivery_link: %s is refused" % name)
    refuse.refused_name = name
    return refuse


EXEC_NAMES = ("execv", "execve", "execl", "execle", "execlp", "execlpe",
              "execvp", "execvpe")
socket.socket = _RefusedSocket
subprocess.Popen = _RefusedPopen
os.fork = _raiser("os.fork")
os.posix_spawn = _raiser("os.posix_spawn")
for _name in EXEC_NAMES:
    setattr(os, _name, _raiser("os." + _name))

import ast  # noqa: E402
import copy  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import signal  # noqa: E402
import stat  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = os.path.realpath(str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT))

from mission_sources import contract  # noqa: E402
from mission_sources import contract_v2 as v2  # noqa: E402
from mission_sources import query_v2  # noqa: E402
from mission_sources import sources as sources_module  # noqa: E402
from pr_delivery import authorization as delivery_auth  # noqa: E402
from target_runtime import delivery_handoff  # noqa: E402
from target_runtime import mission_state as ms  # noqa: E402

NOW = 1_800_000_000
WATCHDOG_SECONDS = 60
GOLDENS_FILE = REPO_ROOT / "tests" / "_pr_delivery_pre_change_goldens.py"
W1 = "wf-0001"
W2 = "wf-0002"
MC_NATIVE = "mc-0001"
ID_A = "prd-" + "0a" * 12
ID_B = "prd-" + "0b" * 12
ID_C = "prd-" + "0c" * 12
LEGACY_ID = "legacy-0001"
MISSION_DIGEST = "d" * 64

NOT_RECORDED = {"state": "unlinked_model_not_recorded", "delivery_id": None,
                "basis": None}


def unlinked(state):
    return {"state": state, "delivery_id": None, "basis": None}


def linked(delivery_id):
    return {"state": "linked", "delivery_id": delivery_id,
            "basis": "record_mission_binding"}


def golden_documents():
    """The golden records, read as DATA: the JSON literal of the goldens file,
    checked against the digest that file pins. The module is not imported."""
    tree = ast.parse(GOLDENS_FILE.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("GOLDEN_JSON",
                                           "GOLDEN_JSON_SHA256")):
            values[node.targets[0].id] = ast.literal_eval(node.value)
    text = values["GOLDEN_JSON"]
    if hashlib.sha256(text.encode()).hexdigest() != (
            values["GOLDEN_JSON_SHA256"]):
        raise AssertionError("the golden records are not the pinned text")
    return json.loads(text)


GOLDENS = golden_documents()
TERMINAL = GOLDENS["local_terminal_record"]
DOTS = GOLDENS["dots_record"]
GOLDEN_PROPOSAL = GOLDENS["present_dots_document"]["delivery_proposal"]
GOLDEN_DIGEST = GOLDENS["present_dots_document"]["proposal_digest_sha256"]


def record(delivery_id, workflow_id):
    """A VALID delivery record minted by the delivery layer's own
    ``new_authorization`` from the golden local-terminal authority, with its
    ``mission`` binding naming ``workflow_id`` (None: no Mission)."""
    mode = TERMINAL["mode"]
    authority = dict((key, copy.deepcopy(TERMINAL[key]))
                     for key in delivery_auth.authority_keys(mode)
                     if key not in ("schema_version", "delivery_id"))
    authority["mission"] = None if workflow_id is None else {
        "workflow_id": workflow_id,
        "mission_authorization_digest_sha256": MISSION_DIGEST}
    return delivery_auth.new_authorization(delivery_id, authority, NOW)


def tampered(delivery_id, minted_for, names):
    """A record minted for ``minted_for`` whose binding was then changed to
    ``names`` without re-authorization: its authority digest no longer
    matches, so the delivery layer refuses it."""
    value = record(delivery_id, minted_for)
    value["mission"]["workflow_id"] = names
    return value


def mission_core_view(native, source_id="mc"):
    return {
        "mission_ref": "%s:%s" % (source_id, native), "source_id": source_id,
        "native_id": native,
        "lifecycle": {"model": contract.LIFECYCLE_MODEL_MISSION_CORE,
                      "state": "RUNNING", "standing": contract.STANDING_ACTIVE},
        "producer": {"declared": "synthetic", "recorded": {
            "transport": "t", "principal_kind": "k"}},
        "revision": 1, "objective": "SYNTHETIC", "updated_at": NOW,
        "execution": {"task_id": None, "identity_source": "start_result",
                      "workspace_realpath": None, "run_started": True,
                      "receipt_recorded": True, "consistent": True,
                      "record_ambiguous": False},
        "native": {"paused": False, "cancel_requested": False},
    }


def workflow_view(native, source_id="wf"):
    view = mission_core_view(native, source_id)
    view["lifecycle"] = {"model": contract.LIFECYCLE_MODEL_WORKFLOW_STORE,
                         "state": "DISPATCHED",
                         "standing": contract.STANDING_ACTIVE}
    return view


def empty_snapshot():
    return {"schema": contract.SNAPSHOT_SCHEMA,
            "version": contract.SNAPSHOT_VERSION, "observed_at": NOW,
            "producer": "synthetic", "tasks": [], "agents": []}


def empty_chains():
    return {"schema": v2.CHAIN_OBSERVATION_SCHEMA,
            "version": v2.CHAIN_OBSERVATION_VERSION, "observed_at": NOW,
            "producer": "synthetic", "roots": [], "unavailable": [],
            "over_capacity": []}


class StubRegistry(object):
    """The one registry method ``query_v2`` calls, over a document built
    here; the product's own ``validate_document`` still checks it."""

    def __init__(self, directory, document):
        self.directory = directory
        self._document = document

    def load_measured(self, absent_is_empty=True):
        return self._document, len(json.dumps(self._document))


class Contained(unittest.TestCase):
    """A SIGALRM watchdog (the termination rule) and, in cleanup, the
    containment check: no blocked event or raiser was reached."""

    def setUp(self):
        def expired(signum, frame):
            raise AssertionError("watchdog: test exceeded %d s"
                                 % WATCHDOG_SECONDS)
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(WATCHDOG_SECONDS)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)
        self.addCleanup(self.assert_contained)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)

    def assert_contained(self):
        self.assertEqual(REACHED, [], "a blocked event or raiser was reached")

    def private_dir(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(path)
        os.chmod(path, 0o700)
        return path

    def write_private(self, path, value):
        text = value if isinstance(value, str) else json.dumps(value)
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, 0o600)

    def fresh_dir(self, prefix):
        """A new private (0700) directory under this test's root."""
        return tempfile.mkdtemp(prefix=prefix + "-", dir=self.root)

    def store(self, records, name="deliveries", schema_version=1):
        path = self.fresh_dir(name)
        self.write_private(os.path.join(path, ms.DELIVERY_STORE_FILE), {
            "pr_delivery_store_schema_version": schema_version,
            "deliveries": dict((r["delivery_id"], r) for r in records)})
        return path

    def reader(self, store_path):
        return ms.DeliveryLinksReader(store_path, lambda: NOW).bindings

    def answer(self, views, hook=None, unread=()):
        """A product-built version 2 response: one registered source per
        source id named (a workflow store when it starts with "wf"), each read
        fresh with its views, except a source in ``unread``, which is read
        unavailable and shows no Mission."""
        source_ids = sorted(set([view["source_id"] for view in views])
                            | set(unread))
        base = self.fresh_dir("answer")
        registered = []
        for source_id in source_ids:
            store_dir = os.path.join(base, "sources", source_id)
            os.makedirs(store_dir, mode=0o700)
            registered.append({
                "source_id": source_id,
                "kind": (contract.KIND_WORKFLOW_STORE
                         if source_id.startswith("wf")
                         else contract.KIND_MISSION_CORE),
                "store_dir": store_dir,
                "producer": "local_request", "max_age_seconds": 60,
                "registered_at": NOW})

        def read(source, now, budget):
            if source["source_id"] in unread:
                return sources_module._reading(
                    source, now, contract.HEALTH_UNAVAILABLE,
                    sources_module.PROBLEM_STORE_UNREADABLE)
            return sources_module._reading(
                source, now, contract.HEALTH_FRESH, None,
                [view for view in views
                 if view["source_id"] == source["source_id"]])

        registry = StubRegistry(os.path.join(base, "registry"), {
            "mission_sources_registry_schema_version": 1,
            "sources": registered})
        return query_v2.query_v2(
            registry, empty_snapshot(), empty_chains(), NOW,
            readers={contract.KIND_MISSION_CORE: read,
                     contract.KIND_WORKFLOW_STORE: read},
            delivery_links=hook)

    def links(self, response):
        return dict((m["mission_ref"], m.get("delivery_link"))
                    for m in response["missions"])


class VocabularyTests(Contained):

    def test_the_link_shape_and_its_closed_vocabulary_are_pinned_by_exact_value(
            self):
        self.assertIn("delivery_link", v2.MISSION_KEYS_V2)
        self.assertEqual(v2.MISSION_LINK_KEYS,
                         ("state", "delivery_id", "basis"))
        self.assertEqual(v2.MISSION_LINK_STATES, (
            "linked", "unlinked_model_not_recorded",
            "unlinked_store_not_registered", "unlinked_store_unavailable",
            "unlinked_reader_failed", "unlinked_no_record",
            "unlinked_ambiguous", "unlinked_sources_incomplete",
            "unlinked_id_not_addressable",
            "unlinked_task_worktree_not_recorded"))
        self.assertEqual(v2.MISSION_LINK_BASES, (
            "record_mission_binding", "record_task_worktree_binding"))

    def test_the_older_origin_only_values_are_neither_produced_nor_accepted(
            self):
        # Origin-only matching is a WEAKER claim than the task-worktree one
        # (every worktree of an origin shares it). The producer must not make
        # it again, and its validator must refuse it, so it can never be
        # confused with the stronger association.
        self.assertNotIn("record_repository_binding", v2.MISSION_LINK_BASES)
        self.assertNotIn("unlinked_repository_not_recorded",
                         v2.MISSION_LINK_STATES)

    def test_no_basis_names_an_engineering_task_a_receipt_or_a_newest_pick(
            self):
        # the bases iterated are asserted first: a loop over nothing asserts nothing
        self.assertEqual(len(v2.MISSION_LINK_BASES), 2)
        # An exact allow-list, not a general relaxation: the ONLY basis that
        # may name a task is the Herdr task's own (its sealed task id and its
        # worktree together; the workflow Mission is never linked on one).
        # No basis may name a receipt, an attestation, or a newest/latest pick.
        names_a_task = ("record_task_worktree_binding",)
        for basis in v2.MISSION_LINK_BASES:
            for word in ("receipt", "attest", "newest", "latest"):
                self.assertNotIn(word, basis.lower(), basis)
            for word in ("task", "engineering"):
                if basis in names_a_task:
                    continue
                self.assertNotIn(word, basis.lower(), basis)
        self.assertEqual(
            [b for b in v2.MISSION_LINK_BASES if "task" in b.lower()],
            list(names_a_task))

    def test_the_delivery_id_shape_is_exactly_the_ceremony_shape(self):
        samples = (ID_A, DOTS["delivery_id"], "prd-" + "A" * 24,
                   "prd-" + "0" * 23, "prd-" + "0" * 25, ID_A + "\n",
                   "PRD-" + "0" * 24, LEGACY_ID, "", None, 5, ["prd-"])
        for sample in samples:
            self.assertEqual(v2.is_delivery_id(sample),
                             delivery_auth.is_presented_delivery_id(sample),
                             repr(sample))
        self.assertTrue(v2.is_delivery_id(ID_A))
        self.assertTrue(v2.is_delivery_id(DOTS["delivery_id"]))
        self.assertFalse(v2.is_delivery_id(LEGACY_ID))


class CompositionTests(Contained):

    def test_without_a_registered_store_workflow_missions_say_so_and_mission_core_is_not_known(
            self):
        response = self.answer([mission_core_view(MC_NATIVE),
                                workflow_view(W1)])
        self.assertEqual(self.links(response), {
            "mc:" + MC_NATIVE: NOT_RECORDED,
            "wf:" + W1: unlinked("unlinked_store_not_registered")})
        v2.validate_response_v2(response)

    def test_a_failing_or_malformed_reader_is_reader_failed_never_a_crash(
            self):
        def raising():
            raise RuntimeError("synthetic reader failure")
        for hook in (raising, lambda: ["not", "a", "map"],
                     lambda: {W1: ID_A}, lambda: {W1: []},
                     lambda: {W1: [5]}, lambda: {5: [ID_A]}):
            response = self.answer([mission_core_view(MC_NATIVE),
                                    workflow_view(W1)], hook=hook)
            self.assertEqual(self.links(response), {
                "mc:" + MC_NATIVE: NOT_RECORDED,
                "wf:" + W1: unlinked("unlinked_reader_failed")}, hook)
            v2.validate_response_v2(response)


class ReaderTests(Contained):

    def linked_answer(self, records, views=None, unread=()):
        if views is None:
            views = [mission_core_view(MC_NATIVE), workflow_view(W1)]
        response = self.answer(views, hook=self.reader(self.store(records)),
                               unread=unread)
        v2.validate_response_v2(response)
        return self.links(response)

    def test_one_record_naming_the_workflow_links_exactly_that_mission(self):
        self.assertEqual(self.linked_answer([
            record(ID_A, W1), record(ID_B, None), record(ID_C, W2)]), {
                "mc:" + MC_NATIVE: NOT_RECORDED, "wf:" + W1: linked(ID_A)})

    def test_a_record_naming_a_mission_core_id_never_links_that_mission(self):
        self.assertEqual(self.linked_answer([record(ID_A, MC_NATIVE)]), {
            "mc:" + MC_NATIVE: NOT_RECORDED,
            "wf:" + W1: unlinked("unlinked_no_record")})

    def test_no_record_naming_the_workflow_is_no_record(self):
        self.assertEqual(self.linked_answer([
            record(ID_B, W2), record(ID_C, None)]), {
                "mc:" + MC_NATIVE: NOT_RECORDED,
                "wf:" + W1: unlinked("unlinked_no_record")})

    def test_two_records_naming_the_workflow_are_ambiguous_with_no_id(self):
        self.assertEqual(self.linked_answer([
            record(ID_A, W1), record(ID_B, W1)]), {
                "mc:" + MC_NATIVE: NOT_RECORDED,
                "wf:" + W1: unlinked("unlinked_ambiguous")})

    def test_two_registered_workflow_missions_holding_the_id_are_ambiguous(
            self):
        self.assertEqual(self.linked_answer(
            [record(ID_A, W1)],
            views=[workflow_view(W1, "wf"), workflow_view(W1, "wf2")]), {
                "wf:" + W1: unlinked("unlinked_ambiguous"),
                "wf2:" + W1: unlinked("unlinked_ambiguous")})

    def test_an_unread_workflow_source_makes_a_unique_match_not_known(self):
        complete = self.linked_answer([record(ID_A, W1)])
        self.assertEqual(complete["wf:" + W1], linked(ID_A))
        incomplete = self.answer(
            [mission_core_view(MC_NATIVE), workflow_view(W1)],
            hook=self.reader(self.store([record(ID_A, W1)], name="again")),
            unread=("wf2",))
        v2.validate_response_v2(incomplete)
        self.assertEqual(self.links(incomplete), {
            "mc:" + MC_NATIVE: NOT_RECORDED,
            "wf:" + W1: unlinked("unlinked_sources_incomplete")})

    def test_a_unique_record_id_outside_the_ceremony_shape_is_not_addressable(
            self):
        self.assertEqual(self.linked_answer([record(LEGACY_ID, W1)]), {
            "mc:" + MC_NATIVE: NOT_RECORDED,
            "wf:" + W1: unlinked("unlinked_id_not_addressable")})

    def test_an_unreadable_or_invalid_store_is_unavailable_never_a_crash(self):
        views = [mission_core_view(MC_NATIVE), workflow_view(W1)]
        missing = os.path.join(self.root, "absent")
        open_mode = self.store([record(ID_A, W1)], name="open")
        os.chmod(open_mode, 0o755)
        not_json = self.private_dir("not-json")
        self.write_private(os.path.join(not_json, ms.DELIVERY_STORE_FILE),
                           "{not json")
        wrong_schema = self.store([record(ID_A, W1)], name="schema",
                                  schema_version=2)
        altered = self.store([record(ID_A, W1), tampered(ID_B, W1, W2)],
                             name="tampered")
        for path in (missing, open_mode, not_json, wrong_schema, altered):
            response = self.answer(views, hook=self.reader(path))
            v2.validate_response_v2(response)
            self.assertEqual(self.links(response), {
                "mc:" + MC_NATIVE: NOT_RECORDED,
                "wf:" + W1: unlinked("unlinked_store_unavailable")}, path)

    def test_the_reader_returns_ids_per_workflow_and_writes_nothing(self):
        path = self.store([record(ID_A, W1), record(ID_B, W1),
                           record(ID_C, None)])

        def observed():
            seen = {}
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                info = os.lstat(full)
                with open(full, "rb") as handle:
                    seen[name] = (handle.read(), info.st_mtime_ns,
                                  stat.S_IMODE(info.st_mode))
            return seen, stat.S_IMODE(os.lstat(path).st_mode)
        before = observed()
        bindings = self.reader(path)()
        self.assertEqual(observed(), before)
        self.assertEqual(sorted(bindings), [W1])
        self.assertEqual(sorted(bindings[W1]), sorted([ID_A, ID_B]))

    def test_every_link_carries_at_most_one_id_and_never_a_list_or_count(self):
        for records in ([record(ID_A, W1)], [record(ID_A, W1),
                                             record(ID_B, W1)], []):
            links = self.linked_answer(records)
            # the links iterated are asserted first: one per Mission shown
            self.assertEqual(sorted(links), sorted(["mc:" + MC_NATIVE,
                                                    "wf:" + W1]))
            for link in links.values():
                self.assertIsInstance(link, dict)
                self.assertEqual(sorted(link),
                                 ["basis", "delivery_id", "state"])
                self.assertTrue(link["delivery_id"] is None
                                or type(link["delivery_id"]) is str, link)
                self.assertTrue(link["basis"] is None
                                or type(link["basis"]) is str, link)


class ValidatorTests(Contained):

    def test_the_validator_accepts_the_rule_and_refuses_what_breaks_it(self):
        response = self.answer([mission_core_view(MC_NATIVE),
                                workflow_view(W1)])
        core, workflow = None, None
        for index, mission in enumerate(response["missions"]):
            if mission["source_id"] == "mc":
                core = index
            else:
                workflow = index

        def with_links(core_link, workflow_link):
            value = copy.deepcopy(response)
            for index, link in ((core, core_link), (workflow, workflow_link)):
                if link is None:
                    value["missions"][index].pop("delivery_link", None)
                else:
                    value["missions"][index]["delivery_link"] = link
            return value
        # The positive control: the rule's own shapes validate.
        for core_link, workflow_link in (
                (NOT_RECORDED, linked(ID_A)),
                (NOT_RECORDED, unlinked("unlinked_ambiguous"))):
            try:
                v2.validate_response_v2(with_links(core_link, workflow_link))
            except contract.ContractError as exc:
                self.fail("the rule's own shape is refused: %s" % exc)
        refused = (
            ("no link at all", with_links(None, None)),
            ("linked without an id", with_links(NOT_RECORDED, {
                "state": "linked", "delivery_id": None,
                "basis": "record_mission_binding"})),
            ("linked with an id outside the ceremony shape", with_links(
                NOT_RECORDED, linked(LEGACY_ID))),
            ("linked with a list of ids", with_links(NOT_RECORDED, {
                "state": "linked", "delivery_id": [ID_A],
                "basis": "record_mission_binding"})),
            ("linked without a basis", with_links(NOT_RECORDED, {
                "state": "linked", "delivery_id": ID_A, "basis": None})),
            ("linked on an engineering task id", with_links(NOT_RECORDED, {
                "state": "linked", "delivery_id": ID_A,
                "basis": "engineering_task_id"})),
            ("unlinked with an id", with_links(NOT_RECORDED, {
                "state": "unlinked_no_record", "delivery_id": ID_A,
                "basis": None})),
            ("unlinked with a basis", with_links(NOT_RECORDED, {
                "state": "unlinked_no_record", "delivery_id": None,
                "basis": "record_mission_binding"})),
            ("a Mission Core Mission linked", with_links(
                linked(ID_A), unlinked("unlinked_no_record"))),
            ("a Mission Core Mission given a workflow reason", with_links(
                unlinked("unlinked_no_record"), unlinked("unlinked_no_record"))),
            ("a workflow Mission said to be of an unrecorded model",
             with_links(NOT_RECORDED, NOT_RECORDED)),
            ("an undeclared state", with_links(NOT_RECORDED, unlinked(
                "unlinked_newest"))),
            ("an extra key", with_links(NOT_RECORDED, dict(
                linked(ID_A), count=1))),
            ("a missing key", with_links(NOT_RECORDED, {
                "state": "unlinked_no_record", "delivery_id": None})),
        )
        for name, value in refused:
            with self.assertRaises(contract.ContractError, msg=name):
                v2.validate_response_v2(value)


class PresentationsRegressionTests(Contained):
    """The presentations reader shares the store's one validation pass with
    the link reader; its outcomes are unchanged."""

    def block(self, records):
        grok = self.private_dir("grok")
        self.write_private(os.path.join(grok, ms.PRESENTATIONS_FILE), {
            "schema_version": ms.PRESENTATIONS_SCHEMA_VERSION,
            "delivery_presentations": {GOLDEN_DIGEST: {
                "proposal": GOLDEN_PROPOSAL,
                "display_digest_sha256": "d" * 64}}})
        reader = ms.DeliveryPresentationsReader(grok, self.store(records),
                                                lambda: NOW)
        return reader.block([], v2.INSPECTION_COMPLETE, {})

    def test_one_attested_record_still_authorizes_its_presented_proposal(self):
        block = self.block([DOTS, record(ID_A, W1)])
        self.assertEqual(block["state"], "observed")
        self.assertEqual([(e["proposal_digest_sha256"], e["state"],
                           e["matching_deliveries"])
                          for e in block["entries"]],
                         [(GOLDEN_DIGEST, "authorized", 1)])

    def test_a_tampered_record_still_makes_the_presentations_unavailable(self):
        block = self.block([DOTS, tampered(ID_A, W1, W2)])
        self.assertEqual((block["state"], block["problem"]),
                         ("unavailable", "delivery_store_invalid"))


class WorktreeFixtures(Contained):
    """Real worktree layouts of one origin, records minted FROM them, Herdr
    task views over them, and the catalog's own composition over all three.
    Hermetic: no process is started, nothing outside this test's temporary
    directory is read or written."""

    #: the origin the golden record was minted for: every worktree here shares it
    ORIGIN = TERMINAL["repository"]["repository_url"]

    def layout(self):
        """A main clone and two worktrees of it, as git lays them out on
        disk (no process is started): ``.git`` directory with the origin in
        its config; each worktree's ``.git`` FILE naming its own gitdir under
        ``.git/worktrees``, which points back with ``commondir``."""
        main = self.private_dir("main")
        os.makedirs(os.path.join(main, ".git"))
        self.write_private(os.path.join(main, ".git", "config"),
                           '[remote "origin"]\n\turl = %s.git\n' % self.ORIGIN)
        out = {}
        for name in ("wt-a", "wt-b"):
            workspace = self.private_dir(name)
            gitdir = os.path.join(main, ".git", "worktrees", name)
            os.makedirs(gitdir)
            self.write_private(os.path.join(gitdir, "commondir"), "../..\n")
            self.write_private(os.path.join(workspace, ".git"),
                               "gitdir: %s\n" % gitdir)
            out[name] = (workspace, os.path.realpath(gitdir))
        return out

    def minted(self, delivery_id, git_dir, task_id):
        """A VALID record (the delivery layer's own ``new_authorization``)
        minted FROM the worktree whose git dir is ``git_dir`` for the Herdr
        task ``task_id``: those two sealed fields are what the ceremony
        itself records."""
        mode = TERMINAL["mode"]
        authority = dict((key, copy.deepcopy(TERMINAL[key]))
                         for key in delivery_auth.authority_keys(mode)
                         if key not in ("schema_version", "delivery_id"))
        authority["mission"] = None
        authority["repository"]["git_dir_realpath"] = git_dir
        authority["workflow_identity"]["engineering_task_id"] = task_id
        return delivery_auth.new_authorization(delivery_id, authority, NOW)

    def herdr_view(self, workspace, task_id, source_id="hd"):
        from target_runtime import herdr_task_source as hts
        source = {"source_id": source_id, "producer": "synthetic"}
        return hts._view(source, [workspace], os.path.realpath(workspace),
                         {"id": task_id, "status": "IN_PROGRESS"})

    def composed(self, records, views):
        """The catalog's composition with ITS real hooks, over a store holding
        ``records`` and a task-native source holding ``views``."""
        reader = ms.DeliveryLinksReader(self.store(records), lambda: NOW)
        base = self.fresh_dir("answer")
        by_source = {}
        for view in views:
            by_source.setdefault(view["source_id"], []).append(view)
        registered = []
        for source_id in sorted(by_source):
            store_dir = os.path.join(base, "sources", source_id)
            os.makedirs(store_dir, mode=0o700)
            registered.append({
                "source_id": source_id, "kind": contract.KIND_TASK_NATIVE,
                "store_dir": store_dir, "producer": "local_request",
                "max_age_seconds": 60, "registered_at": NOW})

        def read(source, now, budget):
            return sources_module._reading(
                source, now, contract.HEALTH_FRESH, None,
                by_source[source["source_id"]])

        registry = StubRegistry(os.path.join(base, "registry"), {
            "mission_sources_registry_schema_version": 1,
            "sources": registered})
        response = query_v2.query_v2(
            registry, empty_snapshot(), empty_chains(), NOW,
            readers={contract.KIND_TASK_NATIVE: read},
            delivery_links=reader.bindings,
            delivery_task_worktrees=reader.task_worktrees,
            task_worktree_of=ms.task_worktree_for_mission,
            task_worktree_delivery=True,
            delivery_handoff_of=delivery_handoff.handoff_for_mission)
        self.response = response
        return self.links(response)


class HerdrTaskWorktreeLinkTests(WorktreeFixtures):
    """A Herdr task links to the one delivery record that seals ITS OWN task id
    and ITS OWN worktree's git directory. The origin is never the key. Mission
    Core stays unrecorded. Addressing off keeps the previous Herdr state."""

    KEY = ("/repo/.git/worktrees/wt-a", "task-a")

    def herdr(self):
        return {"lifecycle": {"model": "herdr.task/1"}, "native_id": "t@k",
                "mission_ref": "h:t"}

    def link(self, **kwargs):
        args = {"task_worktree": None, "by_task_worktree": {},
                "task_worktree_holders": {}, "task_worktree_addressable": True}
        args.update(kwargs)
        return v2.mission_delivery_link(
            self.herdr(), None, {}, {}, v2.INSPECTION_COMPLETE, **args)

    def test_a_unique_task_and_worktree_links_the_herdr_task(self):
        self.assertEqual(
            self.link(task_worktree=self.KEY, by_task_worktree={self.KEY: [ID_A]},
                      task_worktree_holders={self.KEY: 1}),
            {"state": "linked", "delivery_id": ID_A,
             "basis": "record_task_worktree_binding"})

    def test_the_reader_keys_records_by_their_sealed_worktree_and_task(self):
        minted = self.minted(
            ID_A, "/repo/.git/worktrees/wt-a", "task-a")
        self.assertEqual(
            ms.DeliveryLinksReader(self.store([minted]), lambda: NOW)
            .task_worktrees(), {self.KEY: [ID_A]})
        # the origin is in the record, and is NOT a key
        self.assertIn("repository_url", minted["repository"])

    def test_the_mission_key_is_its_own_worktree_git_dir_and_task_id(self):
        wt = self.layout()
        view = self.herdr_view(wt["wt-a"][0], "task-a")
        self.assertEqual(ms.task_worktree_for_mission(view),
                         (wt["wt-a"][1], "task-a"))
        # a plain clone: its own .git directory
        clone = self.private_dir("clone")
        os.makedirs(os.path.join(clone, ".git"))
        from target_runtime.workspace_origin import workspace_git_dir
        self.assertEqual(workspace_git_dir(clone),
                         os.path.realpath(os.path.join(clone, ".git")))
        # not a model it addresses, no task, no git directory
        self.assertIsNone(ms.task_worktree_for_mission(workflow_view("w")))
        idle = dict(view, execution=dict(view["execution"], task_id=None))
        self.assertIsNone(ms.task_worktree_for_mission(idle))
        bare = self.private_dir("bare")
        self.assertIsNone(workspace_git_dir(bare))
        self.assertIsNone(workspace_git_dir("relative/path"))
        self.assertIsNone(workspace_git_dir(None))

    def test_no_key_two_records_and_two_tasks_stay_unlinked(self):
        self.assertEqual(self.link(), {
            "state": "unlinked_task_worktree_not_recorded",
            "delivery_id": None, "basis": None})
        for bad in (("", "task-a"), ("/g", ""), ("/g",), "task-a", ["/g", "t"]):
            self.assertEqual(self.link(task_worktree=bad)["state"],
                             "unlinked_task_worktree_not_recorded", repr(bad))
        key = ("/g", "t")
        self.assertEqual(self.link(
            task_worktree=key, by_task_worktree={key: [ID_A, ID_B]},
            task_worktree_holders={key: 1})["state"], "unlinked_ambiguous")
        self.assertEqual(self.link(
            task_worktree=key, by_task_worktree={key: [ID_A]},
            task_worktree_holders={key: 2})["state"], "unlinked_ambiguous")
        self.assertEqual(self.link(
            task_worktree=key, by_task_worktree={},
            task_worktree_holders={key: 1})["state"], "unlinked_no_record")
        # a record for the SAME worktree but ANOTHER task, or the same task in
        # ANOTHER worktree, is not this task's
        self.assertEqual(self.link(
            task_worktree=key, by_task_worktree={("/g", "other"): [ID_A],
                                                 ("/h", "t"): [ID_B]},
            task_worktree_holders={key: 1})["state"], "unlinked_no_record")

    def test_mission_core_is_never_linked_by_task_worktree_addressing(self):
        self.assertEqual(v2.mission_delivery_link(
            self.herdr(), None, {}, {}, v2.INSPECTION_COMPLETE), {
                "state": "unlinked_model_not_recorded",
                "delivery_id": None, "basis": None})
        core = {"lifecycle": {"model": contract.LIFECYCLE_MODEL_MISSION_CORE},
                "native_id": "mn", "mission_ref": "m"}
        self.assertEqual(v2.mission_delivery_link(
            core, None, {}, {}, v2.INSPECTION_COMPLETE,
            task_worktree=("/g", "t"), by_task_worktree={("/g", "t"): [ID_A]},
            task_worktree_holders={("/g", "t"): 1},
            task_worktree_addressable=True)["state"],
            "unlinked_model_not_recorded")

    def test_no_store_is_not_registered_once_a_herdr_task_can_be_addressed(self):
        self.assertEqual(v2.mission_delivery_link(
            self.herdr(), v2.MISSION_LINK_STORE_NOT_REGISTERED, None, {},
            v2.INSPECTION_COMPLETE, task_worktree_addressable=True), {
                "state": "unlinked_store_not_registered",
                "delivery_id": None, "basis": None})


class TwoWorktreesOfOneOriginTests(WorktreeFixtures):
    """THE DEFECT, through the product's own composition (the catalog's real
    hooks: ``DeliveryLinksReader.repositories``, ``repository_url_for_mission``,
    ``query_v2``), over two REAL worktree layouts of ONE origin.

    Two Herdr tasks in two worktrees of one origin must each link to the
    record minted from THEIR OWN worktree, and neither may steal or obscure
    the other's delivery. Genuine ambiguity still refuses."""

    def test_each_task_links_to_the_record_from_its_own_worktree(self):
        wt = self.layout()
        record_a = self.minted(ID_A, wt["wt-a"][1], "task-a")
        record_b = self.minted(ID_B, wt["wt-b"][1], "task-b")
        view_a = self.herdr_view(wt["wt-a"][0], "task-a")
        view_b = self.herdr_view(wt["wt-b"][0], "task-b")
        links = self.composed([record_a, record_b], [view_a, view_b])
        self.assertEqual(links[view_a["mission_ref"]]["state"], "linked",
                         "task A is OBSCURED by the sibling worktree's record")
        self.assertEqual(links[view_a["mission_ref"]]["delivery_id"], ID_A)
        self.assertEqual(links[view_b["mission_ref"]]["state"], "linked",
                         "task B is OBSCURED by the sibling worktree's record")
        self.assertEqual(links[view_b["mission_ref"]]["delivery_id"], ID_B)

    def test_a_record_from_a_sibling_worktree_is_never_stolen(self):
        wt = self.layout()
        record_b = self.minted(ID_B, wt["wt-b"][1], "task-b")
        view_a = self.herdr_view(wt["wt-a"][0], "task-a")
        links = self.composed([record_b], [view_a])
        self.assertEqual(links[view_a["mission_ref"]],
                         unlinked("unlinked_no_record"),
                         "task A STOLE the record minted from worktree B")

    def test_a_record_for_another_task_in_the_same_worktree_is_not_this_tasks(
            self):
        wt = self.layout()
        record_old = self.minted(ID_C, wt["wt-a"][1], "task-old")
        view_a = self.herdr_view(wt["wt-a"][0], "task-a")
        links = self.composed([record_old], [view_a])
        self.assertEqual(links[view_a["mission_ref"]],
                         unlinked("unlinked_no_record"),
                         "a record for a different task was linked here")

    def mission_of(self, view):
        return [m for m in self.response["missions"]
                if m["mission_ref"] == view["mission_ref"]][0]

    def test_a_FRESH_task_with_no_record_still_declares_how_its_record_would_come_back(self):
        # The primary creation case: nothing is linked (the link's own basis is null by contract), yet the producer states the basis it links
        # by, so the return path can be told BEFORE any record exists. No circularity: the declaration does not depend on a match.
        wt = self.layout()
        view = self.herdr_view(wt["wt-a"][0], "task-a")
        links = self.composed([], [view])
        self.assertEqual(links[view["mission_ref"]], unlinked("unlinked_no_record"))
        block = self.mission_of(view)["delivery_handoff"]
        self.assertEqual(block["state"], "observed")
        self.assertEqual(block["link_basis"], "record_task_worktree_binding")
        self.assertEqual(block["git_dir_realpath"], wt["wt-a"][1])
        self.assertEqual(block["engineering_task_id"], "task-a")
        self.assertEqual(block["staged_candidate"], "not_determined")

    def test_THE_RETURN_PATH_a_record_made_from_this_task_in_this_worktree_is_the_one_this_task_links_to(self):
        # The ceremony seals repository.git_dir_realpath and workflow_identity.engineering_task_id. The mission now SURFACES the same two values,
        # so the return path is checkable in the catalog's own output, and the link follows from them.
        wt = self.layout()
        view = self.herdr_view(wt["wt-a"][0], "task-a")
        record = self.minted(ID_A, wt["wt-a"][1], "task-a")
        links = self.composed([record], [view])
        block = self.mission_of(view)["delivery_handoff"]
        self.assertEqual(
            (block["git_dir_realpath"], block["engineering_task_id"]),
            (record["repository"]["git_dir_realpath"], record["workflow_identity"]["engineering_task_id"]))
        self.assertEqual(links[view["mission_ref"]], {
            "state": "linked", "delivery_id": ID_A,
            "basis": block["link_basis"]})
        # and a sibling worktree's record, sealing ITS pair, is not this task's even though the origin is shared
        other = self.minted(ID_B, wt["wt-b"][1], "task-a")
        links = self.composed([other], [view])
        self.assertEqual(links[view["mission_ref"]], unlinked("unlinked_no_record"))

    def test_the_validator_refuses_the_older_origin_only_values_on_a_herdr_task(
            self):
        wt = self.layout()
        view_a = self.herdr_view(wt["wt-a"][0], "task-a")
        self.composed([self.minted(ID_A, wt["wt-a"][1], "task-a")], [view_a])
        response = self.response
        mission = response["missions"][0]
        self.assertEqual(mission["delivery_link"], {
            "state": "linked", "delivery_id": ID_A,
            "basis": "record_task_worktree_binding"})
        v2.validate_response_v2(response)  # the product's own shape is accepted
        for name, link in (
                ("the origin-only basis", {"state": "linked",
                                           "delivery_id": ID_A,
                                           "basis": "record_repository_binding"}),
                ("the origin-only state", unlinked(
                    "unlinked_repository_not_recorded")),
                ("an unknown basis", {"state": "linked", "delivery_id": ID_A,
                                      "basis": "record_worktree_maybe"}),
                ("the task-worktree basis on a workflow Mission", None)):
            value = copy.deepcopy(response)
            if link is None:
                value["missions"][0]["lifecycle"]["model"] = (
                    contract.LIFECYCLE_MODEL_WORKFLOW_STORE)
            else:
                value["missions"][0]["delivery_link"] = link
            with self.assertRaises(contract.ContractError, msg=name):
                v2.validate_response_v2(value)

    def test_genuine_ambiguity_still_refuses_and_never_picks(self):
        wt = self.layout()
        first = self.minted(ID_A, wt["wt-a"][1], "task-a")
        second = self.minted(ID_B, wt["wt-a"][1], "task-a")
        view_a = self.herdr_view(wt["wt-a"][0], "task-a")
        links = self.composed([first, second], [view_a])
        self.assertEqual(links[view_a["mission_ref"]]["state"],
                         "unlinked_ambiguous")
        self.assertIsNone(links[view_a["mission_ref"]]["delivery_id"])
        other = self.herdr_view(wt["wt-a"][0], "task-a", source_id="hd2")
        links = self.composed([self.minted(ID_C, wt["wt-a"][1], "task-a")],
                              [view_a, other])
        self.assertEqual(links[view_a["mission_ref"]]["state"],
                         "unlinked_ambiguous")
        self.assertEqual(links[other["mission_ref"]]["state"],
                         "unlinked_ambiguous")


class ContainmentTests(Contained):

    def test_no_other_test_module_was_imported_and_the_raisers_are_in_place(
            self):
        here = sys.modules[__name__]
        others = []
        for name, module in list(sys.modules.items()):
            path = getattr(module, "__file__", None)
            if module is here or not path:
                continue
            if os.path.dirname(os.path.realpath(path)) == TESTS_DIR:
                others.append(name)
        self.assertEqual(others, [])
        self.assertIs(socket.socket, _RefusedSocket)
        self.assertIs(subprocess.Popen, _RefusedPopen)
        self.assertEqual(os.fork.refused_name, "os.fork")
        self.assertEqual(os.posix_spawn.refused_name, "os.posix_spawn")
        for name in EXEC_NAMES:
            self.assertEqual(getattr(os, name).refused_name, "os." + name)

    def test_the_audit_hook_raises_on_a_blocked_event_without_doing_it(self):
        # sys.audit raises the event itself: no socket or process is made.
        for event in ("socket.__new__", "subprocess.Popen", "os.fork"):
            with self.assertRaises(RuntimeError):
                sys.audit(event)
            self.assertEqual(REACHED.pop(), event)


if __name__ == "__main__":
    unittest.main()
