"""Focused tests for Task 8 slice S-III: the MISSION-ORIGIN workflow
record kind and its typed linkage, INERT.

Sections: K1 the record kind (validation, digest binding, cross-kind
substitution, linkage shape and bounds, forged render lines, the
handoff-text/receipt non-authority), K2 v2 compatibility (render bytes,
the optional key at load, strict save), K3 mixed Mission/Telegram
stores through the REAL Telegram adapter (supersession, callback,
placeholder creation/claim/edit, both result lanes, /status: no crash,
no mutation, no send for Mission records; v2 exactly as before), K4
Runtime/Broker inertness (never claimed, advanced, performed,
dispatched, recovered or released under the default CLI wiring, direct
Broker construction and direct Runtime calls; zero spawns, store bytes
unchanged; capability accounting is PHASE-SPECIFIC per R-01: the
Runtime pass and a direct ``advance_workflow`` mint nothing and consume
nothing, while a direct ``perform`` presenting a valid authentic token
consumes exactly that token once before the kind refusal, with zero
other effects), K5 store pruning unchanged by the new kind.

No production code constructs a Mission-origin record; every fixture
here is built by the test helper ``mission_core_record`` over the
PRODUCTION renderer.
"""

import copy
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import record as mission_record  # noqa: E402
from target_runtime import broker as broker_module  # noqa: E402
from target_runtime import runtime as runtime_module  # noqa: E402
from telegram_operator import mission as mission_module  # noqa: E402
from workflow_authority import digest as wa_digest  # noqa: E402
from workflow_authority import record as wa_record  # noqa: E402
from workflow_authority import rendering as wa_rendering  # noqa: E402
from workflow_authority import store as wa_store  # noqa: E402

from test_workflow_authority import (  # noqa: E402
    MISSION_CORE_LINKAGE, make_record, mission_core_record,
)

CORE = wa_record.APPROVAL_KIND_MISSION_CORE
V2 = wa_record.APPROVAL_KIND_MISSION_V2
LINK = wa_record.MISSION_AUTHORITY_KEY


def rebind(entry):
    """Recompute the digest-bound rendered text from the record's own
    fields (what a writer would do); returns the same entry."""
    rendered = wa_rendering.render_record_text(entry)
    entry["mission_authorization"]["rendered_text"] = rendered
    entry["mission_authorization"]["digest_sha256"] = (
        wa_digest.text_digest(rendered))
    return entry


def problem_of(entry):
    try:
        wa_record.validate_record(entry)
    except wa_record.RecordError as exc:
        return exc.problem
    return None


# ====================================================================
# K1. The record kind
# ====================================================================


class K1RecordKindTests(unittest.TestCase):

    def test_K1_mission_core_record_validates_binds_and_round_trips(self):
        entry = mission_core_record()
        self.assertIsNone(problem_of(entry))
        self.assertTrue(wa_record.is_mission_core_kind(entry))
        self.assertFalse(wa_record.is_telegram_kind(entry))
        self.assertIsNone(entry["telegram"])
        text = entry["mission_authorization"]["rendered_text"]
        self.assertEqual(entry["mission_authorization"]["digest_sha256"],
                         wa_digest.text_digest(text))
        expected_line = wa_rendering.APPROVAL_LINE_MISSION_CORE % (
            entry[LINK]["mission_id"], entry[LINK]["revision"],
            entry[LINK]["authorization_id"], entry[LINK]["decision_id"],
            entry[LINK]["authorization_digest_sha256"])
        self.assertIn(expected_line, text.splitlines())
        self.assertNotIn("telegram user", text)
        # Round trip through the store: validated on save and on load,
        # byte-identical.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = wa_store.WorkflowStore(tmp)
            document = wa_store.default_document()
            ok, problem, _ = wa_store.add_workflow(document, entry)
            self.assertTrue(ok, problem)
            store.save(document)
            self.assertEqual(store.load()["workflows"]["wf-0001"], entry)
            self.assertEqual(store.read().document["workflows"]["wf-0001"],
                             entry)

    def test_K1b_cross_kind_substitution_is_refused(self):
        v2 = make_record()
        core = mission_core_record()
        cases = {
            "v2 record carrying a linkage": (
                dict(v2, **{LINK: dict(MISSION_CORE_LINKAGE)})),
            "v2 kind stamped on a Mission-origin record": (
                copy.deepcopy(core)),
            "Mission kind stamped on a v2 record": copy.deepcopy(v2),
            "Mission record with a Telegram identity": copy.deepcopy(core),
            "Mission record without its linkage": copy.deepcopy(core),
            "Mission record with a null linkage": copy.deepcopy(core),
            "Mission record with a chat-bound placeholder": (
                copy.deepcopy(core)),
        }
        cases["v2 kind stamped on a Mission-origin record"]["approval"][
            "approval_kind"] = V2
        cases["Mission kind stamped on a v2 record"]["approval"][
            "approval_kind"] = CORE
        cases["Mission record with a Telegram identity"]["telegram"] = (
            copy.deepcopy(v2["telegram"]))
        del cases["Mission record without its linkage"][LINK]
        cases["Mission record with a null linkage"][LINK] = None
        cases["Mission record with a chat-bound placeholder"][
            "result_placeholder"] = {
                "state": wa_record.PLACEHOLDER_REQUIRED, "chat_id": 1001,
                "message_id": None, "requested_at": 1, "sent_at": None,
                "bound_at": None, "text_digest": None}
        for label, entry in cases.items():
            self.assertEqual(problem_of(entry), wa_record.PROBLEM_KIND_LINKAGE,
                             label)
            # Re-rendering cannot rescue a cross-kind shape either: the
            # kind/linkage invariant is refused before the binding.
            rebound = copy.deepcopy(entry)
            try:
                rebind(rebound)
            except (ValueError, KeyError, TypeError):
                continue  # the renderer refuses the shape outright
            self.assertEqual(problem_of(rebound),
                             wa_record.PROBLEM_KIND_LINKAGE, label)
        # A v2 record with a null Telegram identity is a v2 record with
        # a bad Telegram block, exactly as before.
        broken = copy.deepcopy(v2)
        broken["telegram"] = None
        self.assertEqual(problem_of(broken), wa_record.PROBLEM_BAD_TYPE)

    def test_K1c_linkage_shape_grammar_and_bounds(self):
        core = mission_core_record()
        for key in wa_record.MISSION_AUTHORITY_KEYS:
            entry = copy.deepcopy(core)
            del entry[LINK][key]
            self.assertEqual(problem_of(entry), wa_record.PROBLEM_MISSING_KEY,
                             key)
        entry = copy.deepcopy(core)
        entry[LINK]["extra"] = 1
        self.assertEqual(problem_of(entry), wa_record.PROBLEM_UNKNOWN_KEY)
        entry = copy.deepcopy(core)
        entry[LINK] = "mn-" + "1" * 32
        self.assertEqual(problem_of(entry), wa_record.PROBLEM_BAD_TYPE)
        grammar = {
            "mission_id": ("ma-" + "1" * 32, "mn-" + "1" * 31, "mn-" + "1" * 33,
                           "mn-" + "A" * 32, "mn_" + "1" * 32, 12, None,
                           "mn-" + "1" * 31 + "\n"),
            "authorization_id": ("mn-" + "2" * 32, "ma-" + "2" * 31, 7,
                                 "ma-" + "g" * 32),
            "decision_id": ("md-" + "3" * 31, "ma-" + "3" * 32, ""),
        }
        for key, values in grammar.items():
            for value in values:
                entry = copy.deepcopy(core)
                entry[LINK][key] = value
                self.assertEqual(problem_of(entry),
                                 wa_record.PROBLEM_MISSION_ID_GRAMMAR,
                                 (key, value))
        for value in (0, -1, True, "1", None):
            entry = copy.deepcopy(core)
            entry[LINK]["revision"] = value
            self.assertIn(problem_of(entry),
                          (wa_record.PROBLEM_BAD_TYPE,
                           wa_record.PROBLEM_BAD_VALUE), value)
        for value in ("4" * 63, "4" * 65, "F" * 64, 4, None, "4" * 63 + "\n"):
            entry = copy.deepcopy(core)
            entry[LINK]["authorization_digest_sha256"] = value
            self.assertIn(problem_of(entry),
                          (wa_record.PROBLEM_BAD_TYPE,
                           wa_record.PROBLEM_BAD_VALUE,
                           wa_record.PROBLEM_TOO_LARGE), value)
        # The grammar is the Mission Core's, exactly (cross-module pin;
        # workflow_authority never imports mission).
        self.assertEqual(wa_record.MISSION_CORE_ID_HEX_CHARS,
                         mission_record.ID_HEX_CHARS)
        self.assertEqual(wa_record.MISSION_CORE_MISSION_ID_PREFIX,
                         mission_record.MISSION_ID_PREFIX)
        self.assertEqual(wa_record.MISSION_CORE_AUTHORIZATION_ID_PREFIX,
                         mission_record.AUTHORIZATION_ID_PREFIX)
        self.assertEqual(wa_record.MISSION_CORE_DECISION_ID_PREFIX,
                         mission_record.DECISION_ID_PREFIX)
        for prefix in ("mn", "ma", "md"):
            minted = mission_record.mint_id(prefix)
            entry = copy.deepcopy(core)
            key = {"mn": "mission_id", "ma": "authorization_id",
                   "md": "decision_id"}[prefix]
            entry[LINK][key] = minted
            self.assertIsNone(problem_of(rebind(entry)), prefix)
            self.assertIsNone(mission_record.id_problem(entry[LINK][key], prefix))

    def test_K1d_altered_linkage_breaks_the_render_binding(self):
        core = mission_core_record()
        alterations = {
            "mission_id": "mn-" + "9" * 32,
            "revision": core[LINK]["revision"] + 1,
            "authorization_id": "ma-" + "9" * 32,
            "decision_id": "md-" + "9" * 32,
            "authorization_digest_sha256": "9" * 64,
        }
        for key, value in alterations.items():
            entry = copy.deepcopy(core)
            entry[LINK][key] = value
            self.assertEqual(problem_of(entry),
                             wa_record.PROBLEM_RENDER_BINDING, key)
            # Only a re-render (a new digest, a new authorized text)
            # makes the altered block valid again: the block IS bound.
            rebound = rebind(entry)
            self.assertIsNone(problem_of(rebound), key)
            self.assertNotEqual(rebound["mission_authorization"]["digest_sha256"],
                                core["mission_authorization"]["digest_sha256"])

    def test_K1e_forged_render_lines_with_matching_self_digests_refused(self):
        core = mission_core_record()
        v2 = make_record()
        core_line = [line for line in
                     core["mission_authorization"]["rendered_text"].splitlines()
                     if line.startswith("approved by:")][0]
        v2_line = [line for line in
                   v2["mission_authorization"]["rendered_text"].splitlines()
                   if line.startswith("approved by:")][0]

        def forged(entry, old_line, new_line):
            entry = copy.deepcopy(entry)
            text = entry["mission_authorization"]["rendered_text"].replace(
                old_line, new_line, 1)
            self.assertNotEqual(text, entry["mission_authorization"]["rendered_text"])
            entry["mission_authorization"]["rendered_text"] = text
            entry["mission_authorization"]["digest_sha256"] = (
                wa_digest.text_digest(text))  # the self-digest matches
            return entry

        other_mission = wa_rendering.APPROVAL_LINE_MISSION_CORE % (
            "mn-" + "9" * 32, 1, core[LINK]["authorization_id"],
            core[LINK]["decision_id"], core[LINK]["authorization_digest_sha256"])
        # A Mission record whose text names another Mission.
        self.assertEqual(problem_of(forged(core, core_line, other_mission)),
                         wa_record.PROBLEM_RENDER_BINDING)
        # A Mission record whose text carries the Telegram line.
        self.assertEqual(problem_of(forged(core, core_line, v2_line)),
                         wa_record.PROBLEM_RENDER_BINDING)
        # A v2 record whose text carries a Mission line.
        self.assertEqual(problem_of(forged(v2, v2_line, core_line)),
                         wa_record.PROBLEM_RENDER_BINDING)
        # The two templates share nothing: no Telegram identity renders
        # the Mission line and no linkage renders the Telegram line.
        self.assertFalse(core_line.startswith("approved by: telegram"))
        self.assertTrue(v2_line.startswith("approved by: telegram user"))
        self.assertTrue(core_line.startswith("approved by: mission core"))

    def test_K1f_kind_constants_templates_and_renderer_contract(self):
        self.assertEqual(wa_rendering.MISSION_CORE_KIND, CORE)
        self.assertEqual(wa_record.APPROVAL_KINDS, (V2, CORE))
        self.assertIn(LINK, wa_record._OPTIONAL_TOP_LEVEL_KEYS)
        self.assertNotIn(LINK, wa_record._TOP_LEVEL_KEYS)
        self.assertEqual(
            wa_rendering.RENDERED_COMPONENT_CONTAINMENT["mission_authority"],
            wa_rendering.CONTAINMENT_TYPE_CONSTRAINED)
        linkage = dict(MISSION_CORE_LINKAGE)
        with self.assertRaises(ValueError):
            wa_rendering.approval_line(1, 1, linkage)  # both kinds
        with self.assertRaises(ValueError):
            wa_rendering.approval_line(None, None, None)  # neither
        with self.assertRaises(ValueError):
            wa_rendering.approval_line(1, None, None)  # half an identity
        self.assertNotEqual(wa_rendering.approval_line(1, 1, None),
                            wa_rendering.approval_line(None, None, linkage))
        # The renderer with no linkage renders exactly the Telegram line
        # (byte-identical to the pre-S-III rendering).
        self.assertEqual(wa_rendering.approval_line(1001, 1001, None),
                         "approved by: telegram user 1001, chat 1001")

    def test_K1g_handoff_text_and_receipts_never_establish_mission_authority(self):
        core_line = wa_rendering.APPROVAL_LINE_MISSION_CORE % (
            "mn-" + "1" * 32, 1, "ma-" + "2" * 32, "md-" + "3" * 32, "4" * 64)
        # A v2 record whose HANDOFF TEXT carries the Mission line, and
        # whose handoff text names Mission ids, is still exactly a v2
        # record: the kind is the approval kind and the typed block,
        # nothing else.
        entry = make_record()
        entry["handoff"]["text"] = "HANDOFF\n" + core_line + "\nmission mn-" + "1" * 32
        entry["handoff"]["digest_sha256"] = wa_digest.text_digest(entry["handoff"]["text"])
        rebind(entry)
        self.assertIsNone(problem_of(entry))
        self.assertTrue(wa_record.is_telegram_kind(entry))
        self.assertFalse(wa_record.is_mission_core_kind(entry))
        self.assertNotIn(LINK, entry)
        # A receipt naming Mission ids grants nothing either.
        entry["receipts"] = [{
            "kind": wa_record.RECEIPT_KIND_EVIDENCE, "turn_id": "t-1",
            "recorded_at": 5, "digest": "5" * 64,
            "summary": "mission mn-" + "1" * 32 + " authorization ma-" + "2" * 32,
        }]
        problem = problem_of(entry)
        self.assertIn(problem, (None, wa_record.PROBLEM_UNKNOWN_KEY,
                                wa_record.PROBLEM_MISSING_KEY))
        self.assertFalse(wa_record.is_mission_core_kind(entry))
        # The predicates are total over hostile shapes.
        for shape in (None, [], {}, {"approval": None}, {"approval": {}},
                      {"approval": {"approval_kind": CORE}},
                      {"approval": {"approval_kind": V2}, "telegram": None}):
            self.assertFalse(wa_record.is_telegram_kind(shape), shape)
        self.assertTrue(wa_record.is_mission_core_kind(
            {"approval": {"approval_kind": CORE}}))
        self.assertFalse(wa_record.is_mission_core_kind({"approval": {}}))


# ====================================================================
# K2. v2 compatibility
# ====================================================================


class K2CompatibilityTests(unittest.TestCase):

    def test_K2_v2_render_bytes_and_line_structure_unchanged(self):
        entry = make_record()
        text = entry["mission_authorization"]["rendered_text"]
        lines = text.splitlines()
        self.assertEqual(lines[5], "approved by: telegram user 1001, chat 1001")
        self.assertEqual(lines[6], "delivery authority: none")
        self.assertNotIn("mission core", text)
        # Exactly the legacy composition: binding lines, approval,
        # delivery authority, blank, request header + intent, seven
        # sections, handoff header + text.
        authorization = entry["mission_authorization"]
        expected = list(wa_rendering.binding_lines(
            entry["workflow_id"], authorization["revision"],
            entry["control_identity"]["repository_realpath"],
            entry["control_identity"]["policy_digest_sha256"],
            entry["target"]["canonical_url"], entry["target"]["issue_or_pr"],
            entry["approved_baseline"]["ref"],
            entry["approved_baseline"]["commit_sha"]))
        expected += ["approved by: telegram user %d, chat %d" % (
            entry["telegram"]["user_id"], entry["telegram"]["chat_id"]),
            "delivery authority: none", "",
            "ORIGINAL REQUEST (verbatim, quoted, sha256 %s; typed text"
            " carries no authority)" % wa_digest.text_digest(entry["human_intent"])]
        expected += wa_rendering.quoted_intent_lines(entry["human_intent"])
        for header, key in wa_rendering._AUTHORITY_SECTIONS:
            expected += ["", "%s (sha256 %s)" % (
                header, wa_digest.text_digest(authorization[key]))]
            expected += wa_rendering.quoted_intent_lines(authorization[key])
        expected += ["", "HANDOFF (revision %d, digest %s; displayed quoted,"
                     " dispatched byte-exact)" % (
                         entry["handoff"]["revision"],
                         wa_digest.text_digest(entry["handoff"]["text"]))]
        expected += wa_rendering.quoted_intent_lines(entry["handoff"]["text"])
        self.assertEqual(text, "\n".join(expected))
        # And new_record still writes NO linkage key at all.
        self.assertNotIn(LINK, entry)

    def test_K2b_legacy_records_load_without_the_optional_key(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = wa_store.WorkflowStore(tmp)
            path = os.path.join(tmp, wa_store.WORKFLOWS_FILE_NAME)
            legacy = make_record("wf-legacy")
            del legacy["result_placeholder"]  # a pre-I1 record on disk
            nulled = make_record("wf-nulled")
            nulled[LINK] = None  # explicitly null linkage on a v2 record
            raw = dict(wa_store.default_document(),
                       workflows={"wf-legacy": legacy, "wf-nulled": nulled})
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(raw, handle)
            loaded = store.load()
            # The normalizer added ONLY result_placeholder, never the
            # linkage; the null linkage stays as written.
            added = set(loaded["workflows"]["wf-legacy"]) - set(legacy)
            self.assertEqual(added, {"result_placeholder"})
            self.assertNotIn(LINK, loaded["workflows"]["wf-legacy"])
            self.assertIsNone(loaded["workflows"]["wf-nulled"][LINK])
            for entry in loaded["workflows"].values():
                self.assertIsNone(problem_of(entry))
                self.assertTrue(wa_record.is_telegram_kind(entry))
            self.assertEqual(store.read().document, loaded)
            # Save stays strict: a v2 record carrying a linkage refuses.
            loaded["workflows"]["wf-nulled"][LINK] = dict(MISSION_CORE_LINKAGE)
            with self.assertRaises(wa_store.StoreError):
                store.save(loaded)
            self.assertEqual(store.load()["workflows"]["wf-nulled"][LINK], None)

    def test_K2c_normalizer_never_touches_the_linkage(self):
        core = mission_core_record()
        before = copy.deepcopy(core)
        wa_store._normalize_additive_keys(core)
        self.assertEqual(core, before)
        stripped = copy.deepcopy(core)
        del stripped["result_placeholder"]
        wa_store._normalize_additive_keys(stripped)
        self.assertEqual(stripped, before)
        self.assertEqual(stripped[LINK], before[LINK])


# ====================================================================
# K3. Mixed Mission/Telegram stores through the REAL adapter
# ====================================================================

from test_di_remote_3_lifecycle import LifecycleCase  # noqa: E402
from test_di_remote_3_transport import api_ok  # noqa: E402
from test_mission import NOW, cb_update, msg_update  # noqa: E402
from telegram_operator import adapter as adapter_module  # noqa: E402

MISSION_PLANNED = "wf-mission-planned"
MISSION_DONE = "wf-mission-done"


def completed_mission_core_record(workflow_id):
    """A Mission-origin record driven to COMPLETED with a verified
    result and a NULL placeholder — exactly the shape that, before the
    kind guard, selected the legacy result lane."""
    entry = make_record(workflow_id)
    entry["approval"]["consumed_at"] = NOW
    entry["approval"]["consumed_by_update_id"] = 1
    entry["approval"]["decision"] = wa_record.DECISION_APPROVE
    for phase in (wa_record.PHASE_AUTHORIZED, wa_record.PHASE_WORKSPACE_READY,
                  wa_record.PHASE_PREPARED, wa_record.PHASE_VALIDATED,
                  wa_record.PHASE_DISPATCHED, wa_record.PHASE_VERIFIED,
                  wa_record.PHASE_COMPLETED):
        wa_record.apply_transition(entry, phase)
    entry["verified_result"] = {
        "summary": "mission verified", "recorded_at": NOW,
        "digest": wa_digest.text_digest("mission verified"),
    }
    return mission_core_record(entry)


class K3MixedStoreTelegramGuardTests(LifecycleCase):

    def _insert_mission_records(self, harness):
        planned = mission_core_record(make_record(MISSION_PLANNED))
        done = completed_mission_core_record(MISSION_DONE)
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            for entry in (planned, done):
                ok, problem, _ = wa_store.add_workflow(workflows, entry)
                self.assertTrue(ok, problem)
            harness.workflow_store.save(workflows)
        return {workflow_id: self.raw_on_disk(harness, workflow_id)
                for workflow_id in (MISSION_PLANNED, MISSION_DONE)}

    def _flow(self, harness, with_mission, probe_mission_callback=True):
        """The one Telegram flow: two missions (the second supersedes
        the first), an approval, the placeholder/result passes and a
        /status. With ``with_mission`` the store also holds the two
        Mission-origin records throughout. ``probe_mission_callback``
        additionally aims a decision callback at the Mission-origin
        record (an input only a mixed store can receive; K3b compares
        IDENTICAL inputs and leaves it out of both flows)."""
        harness.offer_mission(uid=1)  # wf-0001: PLANNED, unconsumed
        raw = self._insert_mission_records(harness) if with_mission else {}
        harness.offer_mission(uid=2)  # wf-0002: supersedes wf-0001
        bound = harness.bound_message_id()
        if with_mission and probe_mission_callback:
            # A decision callback aimed at the Mission-origin record.
            harness.adapter.process_update(
                cb_update(3, "A:" + MISSION_PLANNED, message_id=bound))
        harness.adapter.process_update(
            cb_update(4, "A:wf-0002", message_id=bound))
        harness.adapter.ensure_result_placeholders()
        harness.adapter.deliver_result_edits()
        harness.adapter.deliver_pending_results()
        harness.adapter.process_update(msg_update(5, "/status"))
        harness.drain_worker()
        return raw

    def _status_text(self, harness):
        return [s["text"] for s in harness.sends()
                if "Adapter state" in s["text"]][-1]

    def test_K3_mission_records_are_neither_crashed_on_mutated_nor_sent(self):
        harness = self.harness_with([api_ok({"message_id": 4242})])
        raw_before = self._flow(harness, with_mission=True)
        # (1) Nothing mutated either Mission record: byte-equal.
        for workflow_id, before in raw_before.items():
            self.assertEqual(self.raw_on_disk(harness, workflow_id), before,
                             workflow_id)
        on_disk = harness.fresh_workflows()["workflows"]
        self.assertIsNone(on_disk[MISSION_PLANNED]["result_placeholder"])
        self.assertIsNone(on_disk[MISSION_DONE]["result_placeholder"])
        self.assertIsNone(on_disk[MISSION_DONE]["result_delivery"])
        self.assertFalse(on_disk[MISSION_PLANNED]["approval"]["superseded"])
        self.assertEqual(on_disk[MISSION_PLANNED]["phase"], wa_record.PHASE_PLANNED)
        # (2) v2 behaviour intact: wf-0001 superseded by the newer
        # mission, wf-0002 approved, armed and placeholder-bound.
        self.assertTrue(on_disk["wf-0001"]["approval"]["superseded"])
        self.assertEqual(on_disk["wf-0001"]["phase"], wa_record.PHASE_BLOCKED)
        self.assertEqual(on_disk["wf-0002"]["phase"], wa_record.PHASE_AUTHORIZED)
        self.assertEqual(on_disk["wf-0002"]["result_placeholder"]["state"],
                         wa_record.PLACEHOLDER_BOUND)
        # (3) Sends: exactly one placeholder (wf-0002), no result for the
        # Mission record, no message naming a Mission record at all.
        self.assertEqual(len(harness.api.once_calls), 1)
        self.assertIn("wf-0002", harness.api.once_calls[0]["text"])
        for send in harness.sends():
            self.assertNotIn(MISSION_DONE, send["text"])
            self.assertNotIn(MISSION_PLANNED, send["text"])
            self.assertNotIn("mn-" + "1" * 32, send["text"])
            self.assertNotIn(adapter_module.RESULT_MESSAGE_HEADER, send["text"])
        # (4) /status: Mission records are FILTERED out of the rows, the
        # count and every detail; no other presentation of them exists.
        status = self._status_text(harness)
        self.assertIn("v2 mission workflows (exact, 2):", status)
        self.assertNotIn("Mission-origin", status)
        self.assertNotIn(MISSION_PLANNED, status)
        self.assertNotIn(MISSION_DONE, status)
        # (5) The callback evaluation refuses by kind before any
        # Telegram field is read.
        _, problem = mission_module.evaluate_mission_callback(
            harness.fresh_workflows(), MISSION_PLANNED, 42, 42,
            harness.repository, 1, NOW)
        self.assertEqual(problem, mission_module.PROBLEM_NOT_A_MISSION_APPROVAL)
        # (6) Supersession never touches a Mission record and counts
        # only Telegram ones.
        workflows = harness.fresh_workflows()
        count = mission_module.supersede_chat_missions(workflows, 42)
        self.assertEqual(count, 0)  # wf-0001 already superseded, wf-0002 consumed
        self.assertEqual(workflows["workflows"][MISSION_PLANNED],
                         on_disk[MISSION_PLANNED])

    def _outputs(self, harness):
        """EVERY Telegram output of a flow: the ordered transport
        timeline (sent and edited texts, callback answers, every verb),
        the once-sends, and the v2 records on disk."""
        timeline = [(verb, json.dumps(payload, sort_keys=True, default=str))
                    for verb, payload in harness.timeline
                    if verb not in ("wf-save", "gateway.submit", "planning")]
        return {
            "timeline": timeline,
            "sends": [s["text"] for s in harness.sends()],
            "edits": [json.dumps(e, sort_keys=True, default=str)
                      for e in harness.edits()],
            "answers": [json.dumps(a, sort_keys=True, default=str)
                        for a in harness.answers()],
            "once_calls": [dict(c) for c in harness.api.once_calls],
            "records": {workflow_id: self.raw_on_disk(harness, workflow_id)
                        for workflow_id in ("wf-0001", "wf-0002")},
        }

    def test_K3b_v2_records_behave_exactly_as_without_mission_records(self):
        # The SAME state directory, sequentially: first the v2-only
        # flow, then (after clearing the directory) the identical flow
        # over a mixed Mission+v2 store — so every output, the /status
        # text with its state path included, is compared in FULL, with
        # nothing stripped or normalized.
        import shutil
        plain = self.harness_with([api_ok({"message_id": 4242})])
        self._flow(plain, with_mission=False, probe_mission_callback=False)
        expected = self._outputs(plain)
        for name in os.listdir(self.tmp.name):
            path = os.path.join(self.tmp.name, name)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        mixed = self.harness_with([api_ok({"message_id": 4242})])
        raw_before = self._flow(mixed, with_mission=True,
                                probe_mission_callback=False)
        actual = self._outputs(mixed)
        self.assertEqual(actual, expected)
        # Anti-vacuity: the mixed flow really carried the Mission
        # records, untouched, alongside.
        for workflow_id, before in raw_before.items():
            self.assertEqual(self.raw_on_disk(mixed, workflow_id), before)
        self.assertEqual(sorted(mixed.fresh_workflows()["workflows"]),
                         ["wf-0001", "wf-0002", MISSION_DONE, MISSION_PLANNED])
        self.assertTrue(any("Adapter state" in text for text in actual["sends"]))
        self.assertEqual(len(actual["once_calls"]), 1)


# ====================================================================
# K4. Runtime/Broker inertness
# ====================================================================

from test_target_runtime import RuntimeCase, NOW as RUNTIME_NOW  # noqa: E402
from target_runtime import capability as capability_module  # noqa: E402


class K4InertnessTests(RuntimeCase):

    def mission_core_authorized(self, workflow_id="wf-mission"):
        return mission_core_record(self.authorized_record(workflow_id))

    def _lease(self):
        return {"lease_id": "lease-1", "path_realpath": self.control,
                "acquired_at": RUNTIME_NOW, "released_at": None}

    def _cli_config(self):
        """A dirun config INSIDE this case's store directory (the CLI
        takes the state directory from the config file's directory),
        satisfying the loader's private-directory guard."""
        os.chmod(self.store_dir, 0o700)
        path = os.path.join(self.store_dir, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"bot_token": "123:abc", "allowed_user_ids": [42],
                       "repository": self.control}, handle)
        os.chmod(path, 0o600)
        return path

    def test_K4_never_claimable_never_a_cleanup_candidate(self):
        self.put_record(self.mission_core_authorized("wf-mission"))
        self.put_record(self.authorized_record("wf-0001"))
        self.assertEqual(
            runtime_module.claimable_workflows(self.store_dir), [("wf-0001", 2)])
        # Terminal Mission record holding a lease: never a cleanup
        # candidate, while its v2 twin is one — the skip is by kind.
        done_core = completed_mission_core_record("wf-mission-done")
        done_core["workspace_lease"] = self._lease()
        done_core["control_identity"]["repository_realpath"] = self.control
        rebind(done_core)
        twin = self.authorized_record("wf-v2-done")
        for phase in (wa_record.PHASE_WORKSPACE_READY, wa_record.PHASE_PREPARED,
                      wa_record.PHASE_VALIDATED, wa_record.PHASE_DISPATCHED,
                      wa_record.PHASE_VERIFIED, wa_record.PHASE_COMPLETED):
            wa_record.apply_transition(twin, phase)
        twin["workspace_lease"] = self._lease()
        self.put_record(done_core)
        self.put_record(twin)
        self.assertEqual(
            runtime_module.terminal_cleanup_candidates(self.store_dir),
            [("wf-v2-done", 2)])

    def test_K4b_process_once_and_direct_advance_refuse_without_effect(self):
        self.put_record(self.mission_core_authorized("wf-mission"))
        store_before = self.store_bytes()
        self.assertEqual(runtime_module.process_once(self.broker), {})
        results = runtime_module.advance_workflow(self.broker, "wf-mission", 2)
        self.assertEqual(len(results), 1)
        label, outcome = results[0]
        self.assertEqual(label, runtime_module.REQUEST_LABEL_PREFIX + "kind")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.problem,
                         runtime_module.PROBLEM_MISSION_KIND_NOT_ENABLED)
        self.assertIn("wf-mission", outcome.detail)
        # No effect of any kind: no turn, no spawn, no capability
        # minted or consumed, the store byte-identical.
        self.assertEqual(self.role_turn.calls, [])
        self.assertEqual(self.spawn_requests, [])
        self.assertEqual(self.capability_entries(), {})
        self.assertEqual(self.store_bytes(), store_before)
        self.assertEqual(len(self.transport.calls), 0)
        # Task 8 S-IV (changed from S-III, justified in the S-IV
        # evidence): a Mission-origin record is a scope owner like any
        # other record — S-III's inert record named none because nothing
        # could ever advance it; S-IV advances it through the gate, so
        # the exclusivity check must see it (a v2 record aimed at the
        # same scope must conflict with it, not silently share it). With
        # no gate it stays where it is: the pre-dispatch owner, never a
        # task owner.
        self.assertEqual(
            {owner[3] for owner in runtime_module.current_scope_owners(self.store_dir)
             if owner[2] == "wf-mission"}, {"pre-dispatch"})

    def test_K4c_every_broker_action_refuses_by_kind_in_every_wiring(self):
        self.put_record(self.mission_core_authorized("wf-mission"))
        for action in broker_module.BROKER_ACTIONS:
            # R-01 preserved: a VALID authentic exact capability is
            # consumed exactly once (before the store/record gate), then
            # the kind gate refuses with zero workflow/model/spawn/release
            # effects and no other authority touched
            # (assert_zero_side_effect proves the ONLY consumed entry is
            # the presented token).
            self.assert_zero_side_effect(
                lambda action=action: self.perform("wf-mission", action),
                broker_module.PROBLEM_MISSION_KIND_NOT_ENABLED,
                allow_capability_consumption=True,
                label="default broker, %s" % action)
            consumed = self.capability_entries()[self.presented_capability]
            self.assertIsNotNone(consumed.get("consumed_at"), action)
            self.assertEqual(self.role_turn.calls, [], action)
            self.assertEqual(self.spawn_requests, [], action)
            # Raw, no capability: refused before the gate by the
            # canonical missing-capability problem, consuming nothing —
            # ONE invocation, under assertion.
            self.assert_zero_side_effect(
                lambda action=action: self.broker.perform("wf-mission", action, 2),
                capability_module.PROBLEM_CAPABILITY_MISSING,
                label="raw %s" % action)
        # A directly constructed Broker refuses the same way.
        other = self.broker_at(RUNTIME_NOW)
        token = capability_module.mint(
            self.store_dir, "wf-mission", broker_module.ACTION_MATERIALIZE, 2,
            RUNTIME_NOW)
        store_before = self.store_bytes()
        outcome = other.perform("wf-mission", broker_module.ACTION_MATERIALIZE, 2,
                                capability=token)
        self.assertEqual(outcome.problem, broker_module.PROBLEM_MISSION_KIND_NOT_ENABLED)
        self.assertEqual(self.store_bytes(), store_before)
        self.assertEqual(self.spawn_requests, [])
        # The DEFAULT CLI wiring (the one production Broker constructor,
        # with no Mission configuration) refuses too, and claims nothing.
        from target_runtime import cli
        namespace = cli._build_parser().parse_args(
            ["--config", self._cli_config(), "once"])
        production_broker, state_directory = cli._build_broker(namespace)
        self.assertEqual(state_directory, self.store_dir)
        self.assertEqual(runtime_module.claimable_workflows(self.store_dir), [])
        # The production Broker reads the real clock; mint at ITS now.
        token = capability_module.mint(
            self.store_dir, "wf-mission", broker_module.ACTION_DISPATCH, 2,
            production_broker._clock())
        store_before = self.store_bytes()
        outcome = production_broker.perform(
            "wf-mission", broker_module.ACTION_DISPATCH, 2, capability=token)
        self.assertEqual(outcome.problem, broker_module.PROBLEM_MISSION_KIND_NOT_ENABLED)
        self.assertEqual(self.store_bytes(), store_before)
        self.assertEqual(self.spawn_requests, [])

    def test_K4d_scope_owners_include_mission_records(self):
        # Task 8 S-IV (changed from S-III's "exclude", justified in the
        # S-IV evidence): a Mission-origin record that holds a target
        # engine OWNS that scope exactly as a v2 record does, so the
        # Runtime's exclusivity check sees both claimants of task t-9.
        core = self.mission_core_authorized("wf-mission")
        core["target_engine"] = {"alias": "target", "task_id": "t-9",
                                 "repo": self.control, "dispatched_at": RUNTIME_NOW}
        v2 = self.authorized_record("wf-0001")
        v2["target_engine"] = dict(core["target_engine"])
        self.put_record(core)
        self.put_record(v2)
        owners = runtime_module.current_scope_owners(self.store_dir)
        self.assertTrue(any(o[2] == "wf-0001" and o[3] == "t-9" for o in owners))
        self.assertTrue(any(o[2] == "wf-mission" and o[3] == "t-9" for o in owners))


# ====================================================================
# K5. Store pruning is unchanged by the new kind
# ====================================================================


class K5PruningTests(unittest.TestCase):

    def _entry(self, index, kind, terminal, created_at):
        entry = make_record("wf-%04d" % index)
        entry["approval"]["created_at"] = created_at
        entry["approval"]["expires_at"] = created_at + 900
        if terminal:
            wa_record.apply_transition(entry, wa_record.PHASE_BLOCKED)
        if kind == "core":
            entry = mission_core_record(entry)
        return entry

    def _document(self, core_positions):
        document = wa_store.default_document()
        for index in range(wa_store.MAX_WORKFLOW_RECORDS):
            kind = "core" if index in core_positions else "v2"
            entry = self._entry(index, kind, terminal=(index % 2 == 0),
                                created_at=1000 + index)
            ok, problem, _ = wa_store.add_workflow(document, entry)
            self.assertTrue(ok, problem)
        return document

    def test_K5_pruning_order_and_counts_do_not_depend_on_the_kind(self):
        core_positions = set(range(0, wa_store.MAX_WORKFLOW_RECORDS, 3))
        mixed = self._document(core_positions)
        plain = self._document(set())
        self.assertEqual(wa_store.store_counts(mixed), wa_store.store_counts(plain))
        newcomer = make_record("wf-new")
        # Task 8 S-V (R15-2): a Mission-origin record's canonical stop
        # obligations live in the Mission store, so pruning consults them
        # through the caller's ``protected`` read. WITHOUT that read no
        # Mission-origin record is pruned (fail closed): the oldest
        # TERMINAL v2 record goes instead and the terminal Mission-origin
        # wf-0000 stays.
        blind = copy.deepcopy(mixed)
        self.assertEqual(wa_store.add_workflow(blind, copy.deepcopy(newcomer)),
                         (True, None, 1))
        self.assertIn("wf-0000", blind["workflows"])
        self.assertNotIn("wf-0002", blind["workflows"])
        # With a canonical read that reports NOTHING owed, the order and the
        # counts do not depend on the kind.
        def nothing_owed(record):
            return False
        ok_mixed, problem_mixed, pruned_mixed = wa_store.add_workflow(
            mixed, copy.deepcopy(newcomer), protected=nothing_owed)
        ok_plain, problem_plain, pruned_plain = wa_store.add_workflow(
            plain, copy.deepcopy(newcomer), protected=nothing_owed)
        self.assertEqual((ok_mixed, problem_mixed, pruned_mixed),
                         (ok_plain, problem_plain, pruned_plain))
        self.assertEqual(sorted(mixed["workflows"]), sorted(plain["workflows"]))
        # The oldest terminal record was pruned whatever its kind, and
        # every active Mission-origin record survived.
        self.assertNotIn("wf-0000", mixed["workflows"])
        for index in core_positions:
            if index % 2 == 1:
                self.assertIn("wf-%04d" % index, mixed["workflows"])
                self.assertTrue(wa_record.is_mission_core_kind(
                    mixed["workflows"]["wf-%04d" % index]))
        # A store of only ACTIVE records refuses the newcomer identically.
        for document in (mixed, plain):
            for entry in list(document["workflows"].values()):
                if not wa_store.is_active(entry):
                    del document["workflows"][entry["workflow_id"]]
            while len(document["workflows"]) < wa_store.MAX_WORKFLOW_RECORDS:
                filler = make_record("wf-fill-%04d" % len(document["workflows"]))
                self.assertTrue(wa_store.add_workflow(document, filler)[0])
        full_mixed = wa_store.add_workflow(mixed, make_record("wf-last"))
        full_plain = wa_store.add_workflow(plain, make_record("wf-last"))
        self.assertEqual(full_mixed, full_plain)
        self.assertFalse(full_mixed[0])
        self.assertEqual(full_mixed[1], wa_store.PROBLEM_STORE_FULL)


if __name__ == "__main__":
    unittest.main()
