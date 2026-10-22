"""D1: the explicitly registered, READ-ONLY reader of the Grok Bot's delivery
presentations (``target_runtime.mission_state.DeliveryPresentationsReader``,
merged into the Mission State composer's module) and its version 2 envelope
block.

What is REAL here: the reader, the version 2 contract validator, the owner
(``CatalogService``) and its argument parsing, the canonical pr_delivery
authorization validator behind ``mission_parent.validated_attestation``, and the
records of ``tests/_pr_delivery_pre_change_goldens`` (a real Dots-attested
record, its presented proposal and a real ``local_terminal`` record, generated
once by pr_delivery's own code).

What is SYNTHETIC, labelled so: every Grok state directory below is built by
this module (a document of the shape ``grok_bot.index`` defines, in a private
temporary directory), including the secret-bearing maps, whose contents are the
sentinel ``FORBIDDEN``: the reader must never read, validate, copy or echo them.
Mission views are minimal dicts. Nothing here touches a live Grok state,
delivery store or catalog.

Each case discriminates against its near neighbour: an unexpired presentation
with no matching authorization is ``awaiting_no_recorded_authorization`` (and
says nothing about an approval being usable), while the same presentation over a
store with a matching VALID record is ``authorized``; a tampered record whose
attested digest matches is NOT authorized (the source becomes unavailable); a
valid record with no attestation authorizes nothing and is not malformed; a NaN
or infinity nested anywhere in a proposal or a delivery record makes the source
unavailable BEFORE any digest or validation call, with no exception escaping.
"""

import ast
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from grok_bot import index as grok_index  # noqa: E402
from mission_sources import contract  # noqa: E402
from mission_sources import contract_v2 as v2  # noqa: E402
from mission_sources import fileread  # noqa: E402
from mission_sources import query_v2  # noqa: E402
from pr_delivery import authorization as delivery_auth  # noqa: E402
from pr_delivery import mission_parent  # noqa: E402
from pr_delivery import store as delivery_store  # noqa: E402
from target_runtime import catalog  # noqa: E402
from target_runtime import catalog_http  # noqa: E402
from target_runtime import mission_state as ms  # noqa: E402
from workflow_authority.digest import json_digest  # noqa: E402
import _pr_delivery_pre_change_goldens as goldens_module  # noqa: E402
import test_catalog_v2 as v2_tests  # noqa: E402
import test_mission_core as core_tests  # noqa: E402

NOW = 1_800_000_000
FORBIDDEN = "SYNTHETIC-FORBIDDEN-SENTINEL"
GOLDENS = goldens_module.goldens()
DOTS_RECORD = GOLDENS["dots_record"]
TERMINAL_RECORD = GOLDENS["local_terminal_record"]
GOLDEN_PROPOSAL = GOLDENS["present_dots_document"]["delivery_proposal"]
GOLDEN_DIGEST = GOLDENS["present_dots_document"]["proposal_digest_sha256"]
# json.dumps writes these as the bare tokens NaN / Infinity / -Infinity, which json.loads accepts.
NON_FINITE = (("NaN", float("nan")), ("Infinity", float("inf")),
              ("-Infinity", float("-inf")))


def private_dir(root, name):
    path = os.path.join(root, name)
    os.mkdir(path)
    os.chmod(path, 0o700)
    return path


def write_private(path, value):
    text = value if isinstance(value, str) else json.dumps(value)
    with open(path, "w") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def proposal(expires_in=3600, presented_ago=60, mission=None, url=None,
             mode=None):
    """A presented proposal built from the golden one, with the times and the
    allowlisted binding facts changed (its digest follows its content)."""
    value = copy.deepcopy(GOLDEN_PROPOSAL)
    value["presented_at"] = NOW - presented_ago
    value["expires_at"] = NOW + expires_in
    value["binding"]["mission"] = mission
    if url is not None:
        value["binding"]["repository"]["repository_url"] = url
    if mode is not None:
        value["binding"]["mode"] = mode
    return value


def receipts_of(*proposals):
    out = {}
    for item in proposals:
        out[json_digest(item)] = {"proposal": item,
                                  "display_digest_sha256": "d" * 64}
    return out


def grok_document(receipts=None, schema_version=1, forbidden_ok=True):
    """The Grok Bot index document, with secret-bearing maps beside the one
    the reader may use. With ``forbidden_ok`` False those maps are MALFORMED
    (the reader must not care: it does not read them)."""
    document = {"schema_version": schema_version}
    if forbidden_ok:
        document["requests"] = {"a" * 64: {
            "state": "PROPOSED", "request_ref": "lr-" + "1" * 32,
            "recorded_at": 1, "recovery": {
                "seal": "hmac-sha256-xor-v1", "sealed_capability": FORBIDDEN}}}
        document["presentations"] = {"lr-" + "1" * 32: {
            "binding": {"note": FORBIDDEN}, "display_digest_sha256": "b" * 64,
            "presented_at": 1}}
        document["approval_consumption"] = {"c" * 64: {
            "state": "CONSUMED", "failures": 0, "updated_at": 1,
            "note": FORBIDDEN}}
        document["workflow_requests"] = {"gr-" + "2" * 32: {
            "workflow_id": "wf-forbidden", "recorded_at": 1,
            "capability_sha256": FORBIDDEN, "capability_expires_at": 2}}
    else:
        document["requests"] = FORBIDDEN
        document["presentations"] = [FORBIDDEN]
        document["approval_consumption"] = [1, 2, FORBIDDEN]
        document["workflow_requests"] = None
    if receipts is not None:
        document["delivery_presentations"] = receipts
    return document


def store_document(*records):
    return {"pr_delivery_store_schema_version": 1,
            "deliveries": dict((r["delivery_id"], r) for r in records)}


def mission(source, native, kind=contract.KIND_WORKFLOW_STORE):
    """A minimal catalog Mission view; ``kind`` is its source's kind, which the
    response states in its own source block (the reader is handed ``kinds``)."""
    return {"mission_ref": "%s:%s" % (source, native), "native_id": native,
            "source_id": source, "kind": kind}


def kinds_of(missions):
    return dict((m["source_id"], m["kind"]) for m in missions)


class Roots(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="di-d1-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.chmod(self.root, 0o700)
        self.grok = private_dir(self.root, "grok")
        self.deliveries = private_dir(self.root, "deliveries")

    def put_grok(self, document, name=ms.PRESENTATIONS_FILE):
        write_private(os.path.join(self.grok, name), document)

    def put_store(self, document):
        write_private(os.path.join(self.deliveries, ms.DELIVERY_STORE_FILE),
                      document)

    def reader(self, with_store=True, now=NOW):
        return ms.DeliveryPresentationsReader(
            self.grok, self.deliveries if with_store else None,
            lambda: now)

    def block(self, missions=(), with_store=True, inspection=None, now=NOW):
        missions = list(missions)
        return self.reader(with_store, now).block(
            missions, inspection or v2.INSPECTION_COMPLETE, kinds_of(missions))

    def by_digest(self, block):
        return dict((e["proposal_digest_sha256"], e) for e in block["entries"])


class PinTests(unittest.TestCase):

    def test_every_redeclared_constant_equals_its_owner(self):
        self.assertEqual(ms.PRESENTATIONS_FILE, grok_index.INDEX_FILE_NAME)
        self.assertEqual(ms.PRESENTATIONS_SCHEMA_VERSION,
                         grok_index.SCHEMA_VERSION)
        self.assertEqual(ms.MAX_DELIVERY_PRESENTATIONS,
                         grok_index.MAX_DELIVERY_RECEIPTS)
        self.assertEqual(v2.MAX_DELIVERY_PRESENTATIONS,
                         grok_index.MAX_DELIVERY_RECEIPTS)
        self.assertEqual(ms.RECEIPT_KEYS, grok_index.DELIVERY_RECEIPT_KEYS)
        self.assertEqual(ms.PROPOSAL_KEYS, grok_index.DELIVERY_PROPOSAL_KEYS)
        self.assertEqual(ms.DELIVERY_STORE_FILE, delivery_store.STORE_FILE_NAME)
        self.assertEqual(ms.DELIVERY_STORE_SCHEMA_VERSION,
                         delivery_store.STORE_SCHEMA_VERSION)
        self.assertEqual(ms.MAX_DELIVERY_RECORDS,
                         delivery_store.MAX_PR_DELIVERY_RECORDS)
        self.assertEqual(ms.PRESENTATION_MODES, delivery_auth.MODES)
        self.assertEqual(ms.WORKFLOW_STORE_KIND, contract.KIND_WORKFLOW_STORE)

    def test_the_new_bounds_are_pinned_by_exact_value(self):
        self.assertEqual(ms.MAX_PRESENTATIONS_FILE_BYTES, 8388608)
        self.assertEqual(ms.MAX_DELIVERY_RECORDS, 64)
        self.assertEqual(ms.MAX_REPOSITORY_URL_CHARS, 512)
        self.assertEqual(ms.MAX_WORKFLOW_ID_CHARS, 128)
        self.assertEqual(ms.MAX_EPOCH_SECONDS, 253402300799)
        self.assertEqual(ms.MAX_CANONICAL_DEPTH, 16)
        self.assertEqual(ms.MAX_CANONICAL_NODES, 262144)
        self.assertEqual(v2.MAX_DELIVERY_PRESENTATIONS, 16)
        self.assertEqual(v2.DELIVERY_PRESENTATIONS_SOURCE_ID,
                         "grok_delivery_presentations")
        self.assertEqual(v2.DELIVERY_PRESENTATIONS_KEYS, (
            "state", "problem", "source_id", "delivery_store", "coverage",
            "total", "entries"))
        self.assertEqual(v2.DELIVERY_PRESENTATION_ENTRY_KEYS, (
            "source_id", "proposal_digest_sha256", "presented_at",
            "expires_at", "mode", "repository_url", "binding_mission",
            "state", "matching_deliveries", "link"))
        self.assertEqual(v2.RESPONSE_KEYS_V2[-1], "delivery_presentations")

    def test_new_python_is_python_3_9_grammar(self):
        # CI runs Python 3.9 and 3.13: no `match`, no 3.10+ syntax.
        for name in ("target_runtime/mission_state.py",
                     "pr_delivery/mission_parent.py",
                     "tests/test_delivery_presentations.py",
                     "mission_panel/draft.py", "mission_panel/journal.py",
                     "mission_panel/service.py",
                     "mission_sources/contract_v2.py",
                     "mission_sources/query_v2.py"):
            source = (REPO_ROOT / name).read_text()
            ast.parse(source, filename=name, feature_version=(3, 9))

    def test_a_default_process_loads_neither_the_reader_nor_any_delivery_module(
            self):
        probe = ("import sys\nsys.path.insert(0, sys.argv[1])\n"
                 "import target_runtime.catalog, target_runtime.catalog_http\n"
                 "print(sorted(m for m in ('target_runtime.mission_state',"
                 " 'pr_delivery', 'grok_bot') if m in sys.modules))\n")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", probe,
                                 str(REPO_ROOT)], capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.stdout.strip(), "[]", result.stderr)

    def test_the_separate_module_is_gone_and_nothing_imports_it(self):
        self.assertFalse((REPO_ROOT / "target_runtime"
                          / "delivery_presentations.py").exists())
        for path in list((REPO_ROOT / "target_runtime").glob("*.py")) + list(
                (REPO_ROOT / "mission_sources").glob("*.py")):
            self.assertNotIn("delivery_presentations import",
                             path.read_text(), path.name)
            self.assertNotIn("import delivery_presentations",
                             path.read_text(), path.name)


class ReaderProbeTests(unittest.TestCase):
    """The reader is read-only, bounded, takes one map, and projects a closed
    set of keys. Plain looks at this one module's source plus runs with a spy;
    nothing here is a scanner."""

    NAMES = ("_hex64", "_is_version", "_epoch", "_plain_canonical",
             "_repository_url", "_mission_binding", "_link",
             "DeliveryPresentationsReader")

    def reader_nodes(self):
        tree = ast.parse((REPO_ROOT / "target_runtime"
                          / "mission_state.py").read_text())
        return [n for n in tree.body if getattr(n, "name", None) in self.NAMES]

    def test_the_reader_names_none_of_the_forbidden_grok_keys(self):
        nodes = self.reader_nodes()
        self.assertEqual(len(nodes), len(self.NAMES))
        literals = set()
        for node in nodes:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    literals.add(sub.value)
        for key in ("requests", "presentations", "workflow_requests",
                    "workflow_presentations", "approval_consumption",
                    "recovery", "seal", "arming", "capability"):
            self.assertNotIn(key, literals, key)
        for text in literals:
            for part in ("recovery", "seal", "arming", "capab", "consumption"):
                self.assertNotIn(part, text.lower(), text)
        # the ONE map it takes, by name
        self.assertIn("delivery_presentations", literals)

    def test_the_reader_has_no_write_or_open_call_and_no_broad_except(self):
        forbidden = set(["open", "write", "writelines", "replace", "rename",
                         "unlink", "remove", "mkdir", "makedirs", "chmod",
                         "Popen", "run", "system", "save", "lock"])
        for node in self.reader_nodes():
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    name = getattr(sub.func, "id", getattr(sub.func, "attr", None))
                    self.assertNotIn(name, forbidden, name)
                if isinstance(sub, ast.ExceptHandler):
                    # never a bare except and never Exception / BaseException
                    caught = [getattr(t, "id", getattr(t, "attr", None))
                              for t in (sub.type.elts if isinstance(
                                  sub.type, ast.Tuple) else [sub.type])]
                    self.assertNotIn(None, caught)
                    self.assertFalse(set(caught) & set(["Exception",
                                                        "BaseException"]), caught)

    def test_only_the_two_pinned_files_are_read_bounded_and_no_follow(self):
        root = tempfile.mkdtemp(prefix="di-d1-spy-")
        self.addCleanup(shutil.rmtree, root, True)
        os.chmod(root, 0o700)
        grok, deliveries = private_dir(root, "g"), private_dir(root, "d")
        write_private(os.path.join(grok, ms.PRESENTATIONS_FILE),
                      grok_document(receipts_of(proposal())))
        write_private(os.path.join(deliveries, ms.DELIVERY_STORE_FILE),
                      store_document(DOTS_RECORD))
        # a decoy beside them that must never be touched
        write_private(os.path.join(grok, "grok_secret_recovery.json"), FORBIDDEN)
        real = fileread.read_private_file
        seen = []

        def spy(path, bound):
            seen.append((os.path.basename(path), bound))
            return real(path, bound)
        with mock.patch.object(fileread, "read_private_file", spy):
            block = ms.DeliveryPresentationsReader(
                grok, deliveries, lambda: NOW).block([], v2.INSPECTION_COMPLETE, {})
        self.assertEqual(block["state"], "observed")
        self.assertEqual(seen, [
            (ms.PRESENTATIONS_FILE, ms.MAX_PRESENTATIONS_FILE_BYTES),
            (ms.DELIVERY_STORE_FILE, ms.MAX_DELIVERY_STORE_BYTES)])
        # and the real reader of those bytes refuses a symlink (no-follow)
        link_dir = private_dir(root, "l")
        os.symlink(os.path.join(grok, ms.PRESENTATIONS_FILE),
                   os.path.join(link_dir, ms.PRESENTATIONS_FILE))
        refused = ms.DeliveryPresentationsReader(
            link_dir, None, lambda: NOW).block([], v2.INSPECTION_COMPLETE, {})
        self.assertEqual((refused["state"], refused["problem"]),
                         ("unavailable", "presentations_unreadable"))

    def test_the_output_keys_are_exactly_the_closed_contract_keys(self):
        root = tempfile.mkdtemp(prefix="di-d1-keys-")
        self.addCleanup(shutil.rmtree, root, True)
        os.chmod(root, 0o700)
        grok, deliveries = private_dir(root, "g"), private_dir(root, "d")
        write_private(os.path.join(grok, ms.PRESENTATIONS_FILE),
                      grok_document(receipts_of(proposal(), GOLDEN_PROPOSAL)))
        write_private(os.path.join(deliveries, ms.DELIVERY_STORE_FILE),
                      store_document(DOTS_RECORD))
        block = ms.DeliveryPresentationsReader(
            grok, deliveries, lambda: NOW).block([], v2.INSPECTION_COMPLETE, {})
        self.assertEqual(tuple(sorted(block)), tuple(sorted(
            v2.DELIVERY_PRESENTATIONS_KEYS)))
        self.assertEqual(tuple(sorted(block["coverage"])), tuple(sorted(
            v2.DELIVERY_COVERAGE_KEYS)))
        for entry in block["entries"]:
            self.assertEqual(tuple(sorted(entry)), tuple(sorted(
                v2.DELIVERY_PRESENTATION_ENTRY_KEYS)))
            self.assertEqual(tuple(sorted(entry["link"])), tuple(sorted(
                v2.DELIVERY_LINK_KEYS)))
            self.assertNotIn("expires_exact", entry)


class CanonicalInputTests(unittest.TestCase):
    """``_plain_canonical`` and its bounds."""

    check = staticmethod(ms._plain_canonical)

    @staticmethod
    def nested(levels):
        value = []
        for _ in range(levels - 1):
            value = [value]
        return value

    def test_plain_canonical_data_is_accepted_at_any_shape_within_the_bounds(self):
        for value in (1, "x", None, True, 1.5, 1e308, {}, [], {"a": [1, {"b": None}]},
                      {"a": {"b": {"c": [True, 2.5, "s"]}}}):
            self.assertTrue(self.check(value), value)

    def test_nothing_that_has_no_canonical_json_form_is_accepted(self):
        for label, value in (
                ("non-string key", {1: "a"}), ("tuple", (1, 2)), ("set", set([1])),
                ("bytes", b"x"), ("object", object()),
                ("non-string key, nested", {"a": [{2: 3}]}),
                ("tuple, nested", {"a": [(1,)]})):
            self.assertFalse(self.check(value), label)

    def test_nan_and_both_infinities_are_refused_at_every_depth(self):
        for name, bad in NON_FINITE:
            for label, value in (("root", bad), ("in a list", [bad]),
                                 ("in a dict", {"a": bad}),
                                 ("deep", {"a": [{"b": [{"c": bad}]}]})):
                self.assertFalse(self.check(value), (name, label))
        self.assertTrue(self.check([float("1e308"), -1e308, 0.0]))

    def test_exactly_at_the_node_bound_is_accepted_and_one_past_it_is_refused(self):
        limit = ms.MAX_CANONICAL_NODES      # values in all, the root included
        self.assertTrue(self.check([0] * (limit - 1)))
        self.assertFalse(self.check([0] * limit))
        self.assertFalse(self.check([0] * (limit + 1)))
        self.assertTrue(self.check(dict(("k%d" % i, 0) for i in range(limit - 1))))
        self.assertFalse(self.check(dict(("k%d" % i, 0) for i in range(limit))))
        self.assertFalse(self.check(dict(("k%d" % i, 0) for i in range(limit + 1))))
        # the bound is over ALL values, not per container
        half = limit // 2
        self.assertTrue(self.check([[0] * (half - 1), [0] * (half - 2)]))
        self.assertFalse(self.check([[0] * half, [0] * half]))

    def test_the_depth_bound_is_exact(self):
        depth = ms.MAX_CANONICAL_DEPTH
        self.assertTrue(self.check(self.nested(depth)))
        self.assertFalse(self.check(self.nested(depth + 1)))
        self.assertTrue(self.check({"a": self.nested(depth - 1)}))
        self.assertFalse(self.check({"a": self.nested(depth)}))

    def test_an_oversized_flat_container_is_refused_before_anything_is_queued(self):
        # If the children were queued before being charged, refusing this list would
        # allocate a million queue entries (tens of MiB). Charged first, it allocates
        # nothing: one length check. No timeout is involved, only allocation.
        flat = [{}] * 1_000_000
        tracemalloc.start()
        try:
            refused = not self.check(flat)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(refused)
        self.assertLess(peak, 65536, peak)
        flat_dict = dict.fromkeys(["k%d" % i for i in range(ms.MAX_CANONICAL_NODES + 1)], {})
        tracemalloc.start()
        try:
            refused = not self.check(flat_dict)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(refused)
        self.assertLess(peak, 65536, peak)

    def test_a_pure_python_check_imports_nothing(self):
        tree = ast.parse((REPO_ROOT / "target_runtime" / "mission_state.py").read_text())
        function = [n for n in tree.body if getattr(n, "name", None) == "_plain_canonical"][0]
        self.assertFalse([n for n in ast.walk(function)
                          if isinstance(n, (ast.Import, ast.ImportFrom))])


class ValidatedAttestationTests(unittest.TestCase):
    """The one seam function behind the reader (``mission_parent``)."""

    def test_a_valid_attested_record_names_its_proposal_digest(self):
        self.assertEqual(mission_parent.validated_attestation(DOTS_RECORD),
                         (DOTS_RECORD["delivery_id"], GOLDEN_DIGEST))

    def test_a_valid_record_with_no_attestation_names_none_and_is_not_malformed(self):
        self.assertNotIn("attestation", TERMINAL_RECORD["human_authorization"])
        self.assertEqual(mission_parent.validated_attestation(TERMINAL_RECORD),
                         (TERMINAL_RECORD["delivery_id"], None))

    def test_a_tampered_record_that_names_the_matching_digest_raises(self):
        tampered = copy.deepcopy(DOTS_RECORD)
        tampered["committer"]["name"] = "Somebody Else"      # an authority field
        self.assertEqual(
            tampered["human_authorization"]["attestation"]["proposal_digest_sha256"],
            GOLDEN_DIGEST)
        with self.assertRaises(delivery_auth.AuthorizationError):
            mission_parent.validated_attestation(tampered)
        swapped = copy.deepcopy(DOTS_RECORD)
        swapped["human_authorization"]["attestation"]["proposal_digest_sha256"] = "f" * 64
        with self.assertRaises(delivery_auth.AuthorizationError):
            mission_parent.validated_attestation(swapped)

    def test_malformed_records_raise_the_contracts_own_error(self):
        for label, mutate in {
                "a missing field": lambda r: r.pop("steps"),
                "no human_authorization": lambda r: r.pop("human_authorization"),
                "an attestation that is not a map": lambda r: r[
                    "human_authorization"].update(attestation="yes"),
                "a malformed digest": lambda r: r["human_authorization"][
                    "attestation"].update(proposal_digest_sha256="not-a-digest"),
                "a bool schema version": lambda r: r.update(schema_version=True),
                "an unknown mode": lambda r: r.update(mode="nope")}.items():
            with self.subTest(label):
                record = copy.deepcopy(DOTS_RECORD)
                mutate(record)
                with self.assertRaises(delivery_auth.AuthorizationError):
                    mission_parent.validated_attestation(record)
        for label, document in (("a string", "oops"), ("an empty map", {}),
                                ("a list", []), ("None", None)):
            with self.subTest(label), self.assertRaises(delivery_auth.AuthorizationError):
                mission_parent.validated_attestation(document)

    def test_it_does_not_mutate_the_callers_document_and_takes_no_step(self):
        record = copy.deepcopy(DOTS_RECORD)
        before = json.dumps(record, sort_keys=True)
        mission_parent.validated_attestation(record)
        self.assertEqual(json.dumps(record, sort_keys=True), before)
        with self.assertRaises(TypeError):
            mission_parent.validated_attestation(record, "COMMIT")

    def test_the_whole_program_pin_fails_a_planted_mutant(self):
        good = core_tests._product_sources()
        seam = good[core_tests.ATTEST_SEAM_FILE]
        self.assertEqual(core_tests.attestation_confinement_problems(good), [])
        start = seam.index("def validated_attestation(")
        end = seam.index("def attest_validated_receipt(")
        body = seam[start:end]
        mutants = {
            "validation skipped": body.replace(
                "    delivery_authorization.validate_authorization(copied)\n", ""),
            "the caller's document is read, not the copy": body.replace(
                'copied["human_authorization"]["attestation"]',
                'delivery_document["human_authorization"]["attestation"]'),
            "a .get call replaces the membership test": body.replace(
                'if "attestation" not in copied["human_authorization"]:',
                'if copied["human_authorization"].get("attestation") is None:'),
            "a step parameter is added": body.replace(
                "validated_attestation(delivery_document)",
                "validated_attestation(delivery_document, step)"),
            "an extra returned value": body.replace(
                'return (copied["delivery_id"], None)',
                'return (copied["delivery_id"], None, copied)'),
        }
        for label, mutated in mutants.items():
            with self.subTest(label):
                self.assertNotEqual(mutated, body, label)
                problems = core_tests.attestation_confinement_problems(
                    dict(good, **{core_tests.ATTEST_SEAM_FILE:
                                  seam[:start] + mutated + seam[end:]}))
                self.assertTrue(any("validated_attestation" in p for p in problems),
                                (label, problems))


class StateTests(Roots):

    def test_awaiting_authorized_and_expired_are_told_apart_from_actual_data(self):
        awaiting = proposal(expires_in=3600, presented_ago=30)
        expired = proposal(expires_in=-5, presented_ago=7200,
                           url="https://github.com/octo/expired")
        self.put_grok(grok_document(receipts_of(
            awaiting, expired, GOLDEN_PROPOSAL)))
        self.put_store(store_document(DOTS_RECORD))
        block = self.block()
        self.assertEqual((block["state"], block["problem"], block["total"]),
                         ("observed", None, 3))
        states = dict((d, e["state"]) for d, e in self.by_digest(block).items())
        self.assertEqual(states[json_digest(awaiting)],
                         "awaiting_no_recorded_authorization")
        self.assertEqual(states[json_digest(expired)],
                         "expired_without_authorization")
        self.assertEqual(states[GOLDEN_DIGEST], "authorized")
        by = self.by_digest(block)
        self.assertEqual(by[GOLDEN_DIGEST]["matching_deliveries"], 1)
        self.assertEqual(by[json_digest(awaiting)]["matching_deliveries"], 0)
        for entry in block["entries"]:
            self.assertEqual(entry["source_id"], "grok_delivery_presentations")
        v2.validate_delivery_presentations(block)

    def test_a_decline_is_never_inferred(self):
        self.put_grok(grok_document(receipts_of(
            proposal(expires_in=-1, presented_ago=900))))
        self.put_store(store_document(DOTS_RECORD))
        [entry] = self.block()["entries"]
        self.assertEqual(entry["state"], "expired_without_authorization")
        self.assertNotIn("declin", json.dumps(entry).lower())
        self.assertNotIn("reject", json.dumps(entry).lower())

    def test_unknown_when_no_delivery_store_is_registered(self):
        awaiting = proposal()
        self.put_grok(grok_document(receipts_of(awaiting, GOLDEN_PROPOSAL)))
        block = self.block(with_store=False)
        self.assertEqual(block["state"], "observed")
        self.assertEqual(block["delivery_store"], "not_registered")
        for entry in block["entries"]:
            self.assertEqual((entry["state"], entry["matching_deliveries"]),
                             ("unknown", None))
        self.assertNotIn("authorized", [e["state"] for e in block["entries"]])

    def test_a_valid_record_with_no_attestation_authorizes_nothing_and_is_valid(self):
        self.put_grok(grok_document(receipts_of(proposal())))
        self.put_store(store_document(TERMINAL_RECORD))
        block = self.block()
        self.assertEqual(block["state"], "observed")
        [entry] = block["entries"]
        self.assertEqual((entry["state"], entry["matching_deliveries"]),
                         ("awaiting_no_recorded_authorization", 0))

    def test_known_empty_for_the_grok_route_only_with_coverage_stated(self):
        for receipts in (None, {}):
            with self.subTest(receipts=receipts):
                self.put_grok(grok_document(receipts))
                self.put_store(store_document())
                block = self.block()
                self.assertEqual((block["state"], block["total"],
                                  block["entries"]), ("observed", 0, []))
                self.assertEqual(block["coverage"], {
                    "route": "grok_bot", "max_entries": 16,
                    "direct_cli": "not_stored"})

    def test_a_copied_record_under_another_id_is_not_a_second_authorization(self):
        twin = copy.deepcopy(DOTS_RECORD)
        twin["delivery_id"] = "prd-" + "1" * 24
        with self.assertRaises(delivery_auth.AuthorizationError):
            delivery_auth.validate_authorization(twin)
        self.put_grok(grok_document(receipts_of(GOLDEN_PROPOSAL)))
        self.put_store(store_document(DOTS_RECORD, twin))
        self.assertEqual(self.block()["problem"], "delivery_store_invalid")

    def test_float_times_are_canonical_and_the_state_is_decided_exactly(self):
        # pr_delivery stamps time.time(): the golden proposal's times are floats.
        self.assertIsInstance(GOLDEN_PROPOSAL["expires_at"], float)
        fractional = proposal()
        fractional["presented_at"] = NOW - 10.75
        fractional["expires_at"] = NOW + 0.5
        self.put_grok(grok_document(receipts_of(fractional)))
        self.put_store(store_document())
        [entry] = self.block()["entries"]
        self.assertEqual((entry["presented_at"], entry["expires_at"]),
                         (NOW - 11, NOW))
        self.assertIsInstance(entry["expires_at"], int)
        self.assertNotIn("expires_exact", entry)
        # now = NOW is BEFORE the exact NOW + 0.5, though the shown second is NOW
        self.assertEqual(entry["state"], "awaiting_no_recorded_authorization")
        later = self.block(now=NOW + 0.5)
        self.assertEqual(later["entries"][0]["state"],
                         "expired_without_authorization")

    def test_non_finite_bool_negative_and_absurd_times_are_refused(self):
        self.put_store(store_document())
        for label, value in (("infinity", float("inf")),
                             ("not a number", float("nan")),
                             ("negative", -0.5), ("bool", False),
                             ("beyond year 9999", 253402300800)):
            with self.subTest(label):
                item = proposal()
                item["expires_at"] = value
                self.put_grok(grok_document({
                    "%064x" % 1: {"proposal": item,
                                  "display_digest_sha256": "d" * 64}}))
                block = self.block()
                self.assertEqual((block["state"], block["problem"]),
                                 ("unavailable", "presentations_invalid"))


class UnavailableTests(Roots):

    def assertUnavailable(self, problem, block=None):
        block = block if block is not None else self.block()
        self.assertEqual((block["state"], block["problem"], block["total"],
                          block["entries"]),
                         ("unavailable", problem, None, []))
        v2.validate_delivery_presentations(block)
        return block

    def test_a_presentations_source_that_cannot_be_read_is_unavailable_never_empty(
            self):
        self.put_store(store_document())
        self.assertUnavailable("presentations_file_absent")
        self.put_grok("{not json")
        self.assertUnavailable("presentations_json_invalid")
        self.put_grok("[1, 2]")
        self.assertUnavailable("presentations_schema_unsupported")
        shutil.rmtree(self.grok)
        self.assertUnavailable("presentations_directory_missing")
        os.mkdir(self.grok)
        os.chmod(self.grok, 0o755)
        self.assertUnavailable("presentations_directory_not_private")

    def test_schema_version_true_is_not_integer_one_in_either_file(self):
        self.put_store(store_document())
        self.put_grok(grok_document({}, schema_version=True))
        self.assertUnavailable("presentations_schema_unsupported")
        self.put_grok(grok_document({}, schema_version=2))
        self.assertUnavailable("presentations_schema_unsupported")
        self.put_grok(grok_document({}))
        bad = store_document()
        bad["pr_delivery_store_schema_version"] = True
        self.put_store(bad)
        self.assertUnavailable("delivery_store_invalid")
        bad["pr_delivery_store_schema_version"] = 2
        self.put_store(bad)
        self.assertUnavailable("delivery_store_unreadable")

    def test_an_invalid_presentations_map_is_refused_whole(self):
        good = proposal()
        cases = {
            "not a map": [],
            "17 receipts": dict(
                (json_digest(proposal(presented_ago=i)),
                 {"proposal": proposal(presented_ago=i),
                  "display_digest_sha256": "d" * 64}) for i in range(17)),
            "key is not the digest of the proposal": {
                "e" * 64: {"proposal": good, "display_digest_sha256": "d" * 64}},
            "key is not a digest": {
                "nope": {"proposal": good, "display_digest_sha256": "d" * 64}},
            "extra receipt key": {json_digest(good): {
                "proposal": good, "display_digest_sha256": "d" * 64,
                "extra": 1}},
            "bad display digest": {json_digest(good): {
                "proposal": good, "display_digest_sha256": "xyz"}},
        }
        for label, receipts in cases.items():
            with self.subTest(label):
                self.put_grok(grok_document(receipts))
                self.put_store(store_document())
                self.assertUnavailable("presentations_invalid")

    def test_a_malformed_proposal_is_refused(self):
        def one(change):
            item = proposal()
            change(item)
            return grok_document({json_digest(item): {
                "proposal": item, "display_digest_sha256": "d" * 64}})
        cases = {
            "unknown mode": lambda p: p["binding"].update(mode="push"),
            "no mode": lambda p: p["binding"].pop("mode"),
            "string time": lambda p: p.update(expires_at="soon"),
            "bool time": lambda p: p.update(presented_at=True),
            "negative time": lambda p: p.update(expires_at=-1),
            "extra proposal key": lambda p: p.update(extra=1),
            "binding not a map": lambda p: p.update(binding=[]),
            "mission shape": lambda p: p["binding"].update(mission={"x": 1}),
            "mission workflow id": lambda p: p["binding"].update(mission={
                "workflow_id": "NOT VALID!",
                "mission_authorization_digest_sha256": None}),
            "mission digest": lambda p: p["binding"].update(mission={
                "workflow_id": "wf-ok",
                "mission_authorization_digest_sha256": "zz"}),
        }
        self.put_store(store_document())
        for label, change in cases.items():
            with self.subTest(label):
                self.put_grok(one(change))
                self.assertUnavailable("presentations_invalid")

    def test_a_non_finite_number_nested_anywhere_in_a_proposal_is_refused_before_hashing(
            self):
        self.put_store(store_document())
        for name, bad in NON_FINITE:
            for label, place in (
                    ("in the binding's candidate", lambda p, b: p["binding"][
                        "candidate"].update(injected=b)),
                    ("deep in a list in the binding", lambda p, b: p["binding"].update(
                        injected=[{"x": [{"y": b}]}])),
                    ("in the evidence", lambda p, b: p["binding"]["evidence"].update(
                        injected=b)),
                    ("a top-level number beside the times", lambda p, b: p[
                        "binding"].update(extra_number=b))):
                with self.subTest("%s %s" % (name, label)):
                    item = proposal()
                    place(item, bad)
                    # json_digest would raise DigestError on this; the pre-check must come first,
                    # so the key can be ANY digest: no exception may escape either way.
                    self.put_grok(grok_document({
                        "%064x" % 7: {"proposal": item,
                                      "display_digest_sha256": "d" * 64}}))
                    self.assertUnavailable("presentations_invalid")

    def test_a_non_finite_number_nested_in_a_delivery_record_is_refused_before_validation(
            self):
        self.put_grok(grok_document(receipts_of(GOLDEN_PROPOSAL)))
        for name, bad in NON_FINITE:
            for label, place in (
                    ("inside steps", lambda r, b: r["steps"].update(injected={"n": [b]})),
                    ("inside human_authorization", lambda r, b: r[
                        "human_authorization"].update(injected=b)),
                    ("at the top", lambda r, b: r.update(injected=[[b]]))):
                with self.subTest("%s %s" % (name, label)):
                    record = copy.deepcopy(DOTS_RECORD)
                    place(record, bad)
                    self.put_store(store_document(record))
                    block = self.assertUnavailable("delivery_store_invalid")
                    self.assertNotIn("authorized", json.dumps(block))
        # and a valid record in the same position still validates
        self.put_store(store_document(DOTS_RECORD))
        self.assertEqual(self.block()["entries"][0]["state"], "authorized")

    def test_the_store_is_unavailable_when_unreadable_and_invalid_when_malformed(self):
        self.put_grok(grok_document(receipts_of(proposal())))
        self.assertUnavailable("delivery_store_unreadable")          # no file
        self.put_store("{broken")
        self.assertUnavailable("delivery_store_unreadable")          # not JSON
        self.put_store(store_document())
        os.chmod(self.deliveries, 0o755)
        self.assertUnavailable("delivery_store_unreadable")          # not private
        os.chmod(self.deliveries, 0o700)
        for label, mutate in {
                "unknown top-level key": lambda d: d.update(extra=1),
                "65 records": lambda d: d.update(deliveries=dict(
                    ("prd-%024d" % i, {}) for i in range(65))),
        }.items():
            with self.subTest(label):
                document = store_document()
                mutate(document)
                self.put_store(document)
                self.assertUnavailable("delivery_store_invalid")

    def test_a_tampered_record_whose_attested_digest_matches_is_not_authorized(self):
        self.put_grok(grok_document(receipts_of(GOLDEN_PROPOSAL)))
        self.put_store(store_document(DOTS_RECORD))
        self.assertEqual(self.block()["entries"][0]["state"], "authorized")
        tampered = copy.deepcopy(DOTS_RECORD)
        tampered["committer"]["name"] = "Somebody Else"
        self.put_store(store_document(tampered))
        block = self.assertUnavailable("delivery_store_invalid")
        self.assertNotIn("authorized", json.dumps(block))
        self.assertNotIn("Somebody Else", json.dumps(block))        # no echo
        swapped = copy.deepcopy(DOTS_RECORD)
        swapped["human_authorization"]["attestation"]["proposal_digest_sha256"] = "f" * 64
        self.put_store(store_document(swapped))
        self.assertUnavailable("delivery_store_invalid")

    def test_malformed_or_missing_record_fields_are_unavailable_never_skipped(self):
        self.put_grok(grok_document(receipts_of(GOLDEN_PROPOSAL)))
        mutations = {
            "a missing field": lambda r: r.pop("steps"),
            "no human_authorization": lambda r: r.pop("human_authorization"),
            "a non-dict record": lambda r: None,
            "an attestation that is not a map": lambda r: r[
                "human_authorization"].update(attestation="yes"),
            "a malformed digest in the attestation": lambda r: r[
                "human_authorization"]["attestation"].update(
                    proposal_digest_sha256="not-a-digest"),
            "a bool schema version": lambda r: r.update(schema_version=True),
            "an unknown mode": lambda r: r.update(mode="nope"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                record = copy.deepcopy(DOTS_RECORD)
                mutate(record)
                document = store_document(DOTS_RECORD)
                document["deliveries"][DOTS_RECORD["delivery_id"]] = (
                    record if label != "a non-dict record" else "oops")
                self.put_store(document)
                self.assertUnavailable("delivery_store_invalid")
        document = store_document(DOTS_RECORD)
        document["deliveries"] = {"prd-" + "9" * 24: DOTS_RECORD}
        self.put_store(document)
        self.assertUnavailable("delivery_store_invalid")

    def test_no_error_echoes_content(self):
        secret = proposal()
        secret["binding"]["note"] = FORBIDDEN
        self.put_grok(grok_document({json_digest(proposal()): {
            "proposal": secret, "display_digest_sha256": "d" * 64}}))
        self.put_store(store_document())
        block = self.assertUnavailable("presentations_invalid")
        self.assertNotIn(FORBIDDEN, json.dumps(block))


class LinkTests(Roots):

    def test_a_presentation_links_to_a_mission_only_by_one_exact_workflow_id(self):
        binding = {"workflow_id": "wf-linked-1",
                   "mission_authorization_digest_sha256": "e" * 64}
        self.put_grok(grok_document(receipts_of(proposal(mission=binding))))
        self.put_store(store_document())
        linked = self.block([mission("legacy", "wf-linked-1")])
        [entry] = linked["entries"]
        self.assertEqual(entry["link"], {
            "state": "linked", "mission_ref": "legacy:wf-linked-1",
            "authorization_digest": "not_comparable"})
        self.assertEqual(entry["binding_mission"], {
            "workflow_id": "wf-linked-1",
            "authorization_digest_sha256": "e" * 64})
        for label, missions, state in (
                ("no registered Mission", [mission("legacy", "wf-other")],
                 "unlinked_no_registered_mission"),
                ("the same id in TWO sources",
                 [mission("a", "wf-linked-1"), mission("b", "wf-linked-1")],
                 "unlinked_ambiguous"),
                ("a Mission Core Mission with that native id",
                 [mission("core", "wf-linked-1", contract.KIND_MISSION_CORE)],
                 "unlinked_no_registered_mission")):
            with self.subTest(label):
                [entry] = self.block(missions)["entries"]
                self.assertEqual(entry["link"]["state"], state)
                self.assertIsNone(entry["link"]["mission_ref"])
                self.assertEqual(entry["link"]["authorization_digest"],
                                 "not_comparable")

    def test_a_null_mission_binding_is_an_explicit_source_qualified_unlinked_entry(
            self):
        self.put_grok(grok_document(receipts_of(proposal(mission=None))))
        self.put_store(store_document())
        [entry] = self.block([mission("legacy", "wf-anything")])["entries"]
        self.assertEqual(entry["link"]["state"], "unlinked_no_mission_binding")
        self.assertIsNone(entry["binding_mission"])
        self.assertEqual(entry["source_id"], "grok_delivery_presentations")

    def test_two_presentations_for_one_workflow_stay_two_entries(self):
        binding = {"workflow_id": "wf-linked-1",
                   "mission_authorization_digest_sha256": None}
        a = proposal(mission=binding, presented_ago=100)
        b = proposal(mission=binding, presented_ago=50)
        self.put_grok(grok_document(receipts_of(a, b)))
        self.put_store(store_document())
        block = self.block([mission("legacy", "wf-linked-1")])
        self.assertEqual(len(block["entries"]), 2)
        self.assertEqual(len(set(e["proposal_digest_sha256"]
                                 for e in block["entries"])), 2)
        self.assertEqual([e["link"]["state"] for e in block["entries"]],
                         ["linked", "linked"])

    def test_a_mission_side_that_was_not_fully_inspected_never_reads_as_no_match(self):
        binding = {"workflow_id": "wf-linked-1",
                   "mission_authorization_digest_sha256": None}
        self.put_grok(grok_document(receipts_of(
            proposal(mission=binding, presented_ago=90),
            proposal(mission=None, presented_ago=40))))
        self.put_store(store_document())
        one = [mission("legacy", "wf-linked-1")]
        two = [mission("a", "wf-linked-1"), mission("b", "wf-linked-1")]
        incomplete = v2.INSPECTION_SOURCES_INCOMPLETE
        unavailable = v2.INSPECTION_REGISTRY_UNAVAILABLE

        def states(missions, inspection):
            block = self.block(missions, inspection=inspection)
            self.assertEqual(block["state"], "observed")
            return [e["link"]["state"] for e in block["entries"]]
        self.assertEqual(states([], v2.INSPECTION_COMPLETE), [
            "unlinked_no_registered_mission", "unlinked_no_mission_binding"])
        self.assertEqual(states([], incomplete), [
            "unlinked_sources_incomplete", "unlinked_no_mission_binding"])
        self.assertEqual(states(one, incomplete), [
            "unlinked_sources_incomplete", "unlinked_no_mission_binding"])
        self.assertEqual(states(one, v2.INSPECTION_COMPLETE), [
            "linked", "unlinked_no_mission_binding"])
        self.assertEqual(states(two, incomplete), [
            "unlinked_ambiguous", "unlinked_no_mission_binding"])
        self.assertEqual(states([], unavailable), [
            "unlinked_registry_unavailable", "unlinked_no_mission_binding"])
        for inspection in v2.INSPECTIONS:
            block = self.block(one, inspection=inspection)
            self.assertEqual([e["state"] for e in block["entries"]],
                             ["awaiting_no_recorded_authorization"] * 2)

    def test_only_an_unread_workflow_store_source_makes_the_inspection_incomplete(
            self):
        def block(kind, health):
            return {"kind": kind, "health": health}
        fresh, stale = contract.HEALTH_FRESH, contract.HEALTH_STALE
        workflow, core = (contract.KIND_WORKFLOW_STORE,
                          contract.KIND_MISSION_CORE)
        self.assertEqual(query_v2._inspection([]), v2.INSPECTION_COMPLETE)
        self.assertEqual(query_v2._inspection(
            [block(workflow, fresh), block(core, stale)]),
            v2.INSPECTION_COMPLETE)
        for unread in (stale, contract.HEALTH_UNAVAILABLE,
                       contract.HEALTH_MALFORMED):
            self.assertEqual(query_v2._inspection(
                [block(core, fresh), block(workflow, unread)]),
                v2.INSPECTION_SOURCES_INCOMPLETE)


class DegradedRegistryTests(Roots):
    """The presentations source is registered, and read, on its own: a
    Mission registry that cannot be loaded changes only what can be linked."""

    class BrokenRegistry(object):
        directory = "/synthetic/registry"

        def load_measured(self, absent_is_empty=False):
            from mission_sources import registry as registry_module
            raise registry_module.RegistryError(
                registry_module.PROBLEM_REGISTRY_ABSENT, "synthetic")

    def response(self, hook):
        return query_v2.query_v2(self.BrokenRegistry(), None, None, NOW,
                                 delivery_presentations=hook)

    def test_a_registered_source_stays_registered_and_readable_with_unknown_links(
            self):
        binding = {"workflow_id": "wf-linked-1",
                   "mission_authorization_digest_sha256": None}
        self.put_grok(grok_document(receipts_of(
            proposal(mission=binding), GOLDEN_PROPOSAL)))
        self.put_store(store_document(DOTS_RECORD))
        response = self.response(self.reader().block)
        self.assertNotEqual(response["registry"]["health"], contract.HEALTH_FRESH)
        self.assertEqual(response["missions"], [])
        block = response["delivery_presentations"]
        self.assertEqual((block["state"], block["problem"], block["total"]),
                         ("observed", None, 2))
        self.assertEqual(block["delivery_store"], "registered")
        by_state = dict((e["state"], e) for e in block["entries"])
        self.assertEqual(sorted(by_state), [
            "authorized", "awaiting_no_recorded_authorization"])
        bound = [e for e in block["entries"] if e["binding_mission"]]
        self.assertEqual([e["link"]["state"] for e in bound],
                         ["unlinked_registry_unavailable"])
        self.assertNotIn("unlinked_no_registered_mission", json.dumps(block))
        v2.validate_response_v2(response)

    def test_registration_and_availability_do_not_depend_on_the_registry(self):
        response = self.response(None)
        self.assertEqual(response["delivery_presentations"]["state"],
                         "not_registered")
        self.put_store(store_document())
        response = self.response(self.reader().block)
        block = response["delivery_presentations"]
        self.assertEqual((block["state"], block["problem"]), (
            "unavailable", "presentations_file_absent"))
        self.put_grok(grok_document({}))
        block = self.response(self.reader().block)["delivery_presentations"]
        self.assertEqual((block["state"], block["total"]), ("observed", 0))


class SecrecyTests(Roots):

    def test_the_forbidden_maps_are_never_read_validated_or_echoed(self):
        awaiting = proposal(url="https://user:pw@github.com/octo/repo")
        self.put_grok(grok_document(receipts_of(awaiting, GOLDEN_PROPOSAL)))
        self.put_store(store_document(DOTS_RECORD))
        text = json.dumps(self.block())
        self.assertNotIn(FORBIDDEN, text)
        for private in ("requests", "approval_consumption", "recovery",
                        "capability", "sealed", "hmac"):
            self.assertNotIn(private, text, private)

    def test_malformed_forbidden_maps_do_not_make_the_reader_refuse_because_it_does_not_read_them(
            self):
        self.put_grok(grok_document(receipts_of(proposal()),
                                    forbidden_ok=False))
        self.put_store(store_document())
        block = self.block()
        self.assertEqual((block["state"], block["total"]), ("observed", 1))
        self.assertNotIn(FORBIDDEN, json.dumps(block))

    def test_only_the_allowlisted_facts_are_projected(self):
        self.put_grok(grok_document(receipts_of(GOLDEN_PROPOSAL)))
        self.put_store(store_document(DOTS_RECORD))
        [entry] = self.block()["entries"]
        self.assertEqual(set(entry), set(v2.DELIVERY_PRESENTATION_ENTRY_KEYS))
        self.assertEqual(entry["repository_url"],
                         "https://github.com/octo/repo")
        text = json.dumps(entry)
        binding = GOLDEN_PROPOSAL["binding"]
        for private in (binding["repository"]["realpath"],
                        binding["repository"]["git_dir_realpath"],
                        binding["committer"]["email"],
                        binding["remote"]["url_exact"],
                        binding["pr_content"]["title"]):
            self.assertNotIn(str(private), text)

    def test_a_repository_url_with_credentials_or_odd_characters_is_withheld(self):
        for url in ("https://user:pw@github.com/octo/repo",
                    "https://github.com/octo/repo?token=abc",
                    "ssh://github.com/octo/repo", "x" * 600, ""):
            with self.subTest(url=url[:30]):
                self.put_grok(grok_document(receipts_of(proposal(url=url))))
                self.put_store(store_document())
                [entry] = self.block()["entries"]
                self.assertIsNone(entry["repository_url"])


class ContractTests(unittest.TestCase):

    def test_every_version_2_envelope_carries_the_block_not_registered_by_default(
            self):
        block = v2.delivery_presentations_not_registered()
        self.assertEqual(block["state"], "not_registered")
        self.assertEqual(block["entries"], [])
        self.assertIsNone(block["total"])

    def test_the_validator_refuses_what_it_should(self):
        base = v2.delivery_presentations_block("observed", None, "registered",
                                               0, [])
        entry = {
            "source_id": "grok_delivery_presentations",
            "proposal_digest_sha256": "a" * 64, "presented_at": 1,
            "expires_at": 2, "mode": "pull_request", "repository_url": None,
            "binding_mission": None, "state": "awaiting_no_recorded_authorization",
            "matching_deliveries": 0,
            "link": {"state": "unlinked_no_mission_binding",
                     "mission_ref": None, "authorization_digest": "not_comparable"}}
        ok = dict(base, total=1, entries=[entry])
        v2.validate_delivery_presentations(ok)
        cases = {
            "an extra key": dict(ok, extra=1),
            "an unknown state": dict(ok, state="gone"),
            "a problem on an observed block": dict(ok, problem="presentations_invalid"),
            "no problem on an unavailable block": dict(
                ok, state="unavailable", total=None, entries=[]),
            "total that is not the entries": dict(ok, total=2),
            "entries on a block that is not observed": dict(
                ok, state="not_registered"),
            "a repeated digest": dict(ok, total=2, entries=[entry, entry]),
            "authorized without a delivery": dict(ok, entries=[dict(
                entry, state="authorized", matching_deliveries=0)]),
            "unknown with a count": dict(ok, entries=[dict(
                entry, state="unknown", matching_deliveries=0)]),
            "a link without its Mission": dict(ok, entries=[dict(
                entry, link=dict(entry["link"], state="linked"))]),
            "a Mission without a link": dict(ok, entries=[dict(
                entry, link=dict(entry["link"], mission_ref="a:b"))]),
            "another coverage": dict(ok, coverage={
                "route": "grok_bot", "max_entries": 17,
                "direct_cli": "not_stored"}),
            "another source": dict(ok, source_id="somebody_else"),
            "more than 16 entries": dict(ok, total=17, entries=[
                dict(entry, proposal_digest_sha256="%064x" % i)
                for i in range(17)]),
        }
        for label, value in cases.items():
            with self.subTest(label), self.assertRaises(
                    contract.ContractError):
                v2.validate_delivery_presentations(value)

    def test_the_assemble_hook_never_turns_a_failure_into_an_empty_list(self):
        complete = v2.INSPECTION_COMPLETE
        self.assertIsNone(query_v2._delivery_presentations_block(
            None, [], complete, {}))

        def raises(_missions, _inspection, _kinds):
            raise RuntimeError(FORBIDDEN)
        block = query_v2._delivery_presentations_block(raises, [], complete, {})
        self.assertEqual((block["state"], block["problem"]),
                         ("unavailable", "reader_failed"))
        self.assertNotIn(FORBIDDEN, json.dumps(block))
        block = query_v2._delivery_presentations_block(
            lambda _m, _i, _k: {"state": "observed"}, [], complete, {})
        self.assertEqual(block["problem"], "reader_failed")
        seen = []
        query_v2._delivery_presentations_block(
            lambda m, i, k: seen.append((m, i, k)) or v2.delivery_presentations_not_registered(),
            ["m"], complete, {"s": "workflow_store"})
        self.assertEqual(seen, [(["m"], complete, {"s": "workflow_store"})])


class OwnerTests(v2_tests.V2Fixture, Roots):
    """The registered reader through the REAL owner and its arguments."""

    def setUp(self):
        v2_tests.V2Fixture.setUp(self)
        Roots.setUp(self)

    def tearDown(self):
        Roots.tearDown(self) if hasattr(Roots, "tearDown") else None

    def owner_with(self, presentations=True, store=True):
        owner = catalog.CatalogService(
            self.registry.directory, self.clock, None,
            self.plan(stdout=v2_tests.listing_stdout(
                [v2_tests.engine_row()])),
            monotonic=self.ticks, discover=True, contract_version=2,
            delivery_store=self.deliveries if store else None,
            delivery_presentations=self.grok if presentations else None)

        def end():
            owner.shutdown()
            owner.settle(10)
        self.addCleanup(end)
        return owner

    def test_the_owner_serves_the_registered_block_and_not_registered_otherwise(
            self):
        self.running()
        self.put_grok(grok_document(receipts_of(proposal(), GOLDEN_PROPOSAL)))
        self.put_store(store_document(DOTS_RECORD))
        response = self.owner_with().request(catalog.MAX_WAIT_SECONDS)[
            "response"]
        self.assertIsNotNone(response)
        v2.validate_response_v2(response)
        block = response["delivery_presentations"]
        self.assertEqual((block["state"], block["total"]), ("observed", 2))
        self.assertEqual(sorted(e["state"] for e in block["entries"]),
                         ["authorized", "awaiting_no_recorded_authorization"])
        plain = self.owner_with(presentations=False).request(
            catalog.MAX_WAIT_SECONDS)["response"]
        self.assertEqual(plain["delivery_presentations"]["state"],
                         "not_registered")
        self.assertEqual(plain["delivery_presentations"]["entries"], [])

    def test_the_owner_builds_the_reader_from_the_existing_lazy_composer_import(self):
        source = (REPO_ROOT / "target_runtime" / "catalog.py").read_text()
        self.assertEqual(source.count("import mission_state as mission_state_module"), 1)
        self.assertIn("mission_state_module.DeliveryPresentationsReader", source)
        self.assertNotIn("delivery_presentations import", source)

    def test_a_corrupt_registry_leaves_the_presentations_registered_and_read(
            self):
        self.running()
        binding = {"workflow_id": "wf-linked-1",
                   "mission_authorization_digest_sha256": None}
        self.put_grok(grok_document(receipts_of(proposal(mission=binding))))
        self.put_store(store_document())
        path = self.registry.path
        with open(path, "w") as handle:
            handle.write("{ not a registry")
        os.chmod(path, 0o600)
        response = self.owner_with().request(catalog.MAX_WAIT_SECONDS)[
            "response"]
        self.assertIsNotNone(response)
        self.assertNotEqual(response["registry"]["health"],
                            contract.HEALTH_FRESH)
        block = response["delivery_presentations"]
        self.assertEqual((block["state"], block["total"]), ("observed", 1))
        self.assertEqual(block["entries"][0]["link"]["state"],
                         "unlinked_registry_unavailable")
        v2.validate_response_v2(response)

    def test_version_1_refuses_the_flag_exactly_as_it_refuses_the_delivery_store(
            self):
        with self.assertRaises(ValueError):
            catalog.CatalogService(self.registry.directory, self.clock, None,
                                   None, delivery_presentations=self.grok)
        with self.assertRaises(ValueError):
            catalog.CatalogService(self.registry.directory, self.clock, None,
                                   None, delivery_store=self.deliveries)
        for main, label in ((catalog.main, "catalog"), (catalog_http.main,
                                                        "catalog_http")):
            with self.subTest(label):
                argv = (["query", "--registry", self.directory]
                        if label == "catalog" else
                        ["--registry", self.directory, "--discovery-file",
                         os.path.join(self.root, "d.json"),
                         "--discover-roots"])
                self.assertEqual(
                    main(argv + ["--contract-version", "1",
                                 "--delivery-presentations", self.grok]),
                    catalog.EXIT_USAGE)
                self.assertEqual(
                    main(argv + ["--contract-version", "2",
                                 "--delivery-presentations", "relative/dir"]),
                    catalog.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
