"""Tests for the Grok Bot transport adapter (``grok_bot``), Task 8 slice 1.

What is REAL here: the operator session seam (``FunctionOperatorSession``
over the real ``codex_gateway.gateway.build_request``), the local request
surface, Mission Core, and the run bridge
(``target_runtime.mission_bridge.MissionBridge``), all over a temporary
protected state directory.

What is SYNTHETIC, labelled so:

- ``ScriptedOperator`` stands in for the Codex Outer Operator behind the
  seam's submit hook. No Codex process runs; the proposal it "authors" is
  test data. It is not evidence of live Codex behaviour.
- The run bridge's effects are the injected recorders of
  ``tests/test_mission_bridge.py`` (``SpawnRecorder``, ``Observer``,
  ``FakeTransport``, ``FakeOwnership``): nothing is spawned, signalled,
  fetched or pushed, and no Herdr runs.
- ``SYNTHETIC_AUTHENTICATED`` (from ``tests/test_local_request.py``) is used
  ONLY to make a genuinely stale revision through Mission Core's own edit,
  never through the adapter.
- Every "approved" reply is a test string. Nothing here is evidence of a
  live Grok Bot conversation or of who sent a reply: the relay is
  operator-attested and DI does not establish the sender.

Termination rule (CONTRIBUTING.md): a SIGALRM watchdog bounds every test,
and every child interpreter carries an independent ``timeout``.
"""

import ast
import contextlib
import fcntl
import functools
import hashlib
import io
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import unittest  # noqa: E402

from codex_gateway import gateway as gateway_module  # noqa: E402
from codex_gateway.contract import GatewayResult  # noqa: E402
from local_request import cli as local_request_cli  # noqa: E402
from local_request import store as request_store  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import service as mission_service  # noqa: E402
from mission import store as mission_store  # noqa: E402
from operator_session import FunctionOperatorSession  # noqa: E402
from target_runtime import mission_bridge as bridge_module  # noqa: E402

from grok_bot import adapter as adapter_module  # noqa: E402
from grok_bot import cli as cli_module  # noqa: E402
from grok_bot import framing  # noqa: E402
from grok_bot import index as index_module  # noqa: E402

import test_grok_bot_retirement as retirement  # noqa: E402
import test_human_interaction  # noqa: E402
from test_local_request import NOW, Clock, SYNTHETIC_AUTHENTICATED  # noqa: E402
from test_mission_bridge import (  # noqa: E402
    RESULT_TEXT, REVIEW_TEXT, SURFACE, TESTS_PASS_DIGEST, TASK_ID, Bounded,
    FakeOwnership, FakeTransport, FakeTrustWorker, Observer, SpawnRecorder,
    raw_observation, run_request, sha256,
)

PACKAGE_DIR = REPO_ROOT / "grok_bot"
ENTRY_SCRIPT = REPO_ROOT / "grokbot.py"
CHILD_TIMEOUT_SECONDS = 120
PROSE = ("Spin up Dodging Infinity, read the operator rules, inspect the"
         " flaky readiness probe in Example/Repo, and return a proposal.")
EVIDENCE = request_store.EVIDENCE_STATUS


def leaf_lines(prefix, value):
    """The test's OWN flattening of a proposal: one ``path: json`` line
    per leaf, empty containers included."""
    if isinstance(value, dict):
        if not value:
            return ["%s: {}" % prefix]
        return [line for key in sorted(value)
                for line in leaf_lines("%s.%s" % (prefix, key), value[key])]
    if isinstance(value, list):
        if not value:
            return ["%s: []" % prefix]
        return [line for index, item in enumerate(value)
                for line in leaf_lines("%s[%d]" % (prefix, index), item)]
    return ["%s: %s" % (prefix, json.dumps(value, ensure_ascii=False))]


def product_sources():
    files = sorted(PACKAGE_DIR.glob("*.py")) + [ENTRY_SCRIPT]
    assert len(files) >= 5, files
    return files


class ScriptedOperator(object):
    """SYNTHETIC stand-in for the Codex Outer Operator: records every
    request the seam hands it and answers with a scripted result."""

    def __init__(self, proposal=None):
        self.requests = []
        self.proposal = proposal or run_request()
        self.reply = None
        self.status = "completed"
        self.raises = None

    def __call__(self, request):
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        if self.reply is None:
            message = ("I read the operator rules and prepared a proposal.\n"
                       + framing.PROPOSAL_PREFIX + json.dumps(self.proposal))
        elif callable(self.reply):
            message = self.reply(request)
        else:
            message = self.reply
        return GatewayResult(
            contract_version=1, request_id=request.request_id,
            session_id="codex-session-1", status=self.status,
            message=message, error=None, unrecognized_event_lines=0)


class Fixture(Bounded):

    def setUp(self):
        super(Fixture, self).setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state")
        self.clock = Clock(NOW)
        self.observer = Observer()
        self.spawn = SpawnRecorder(self.observer, self.clock)
        self.transport = FakeTransport()
        self.ownership = FakeOwnership()
        # Task 8 final: the configured repository and workspaces root; a
        # dispatch prepares the Mission's own workspace, never a given path.
        self.repository = os.path.join(self.tmp.name, "repository")
        self.root = os.path.join(self.tmp.name, "workspaces")
        os.mkdir(self.repository)
        os.mkdir(self.root)
        self.transport.git_common = os.path.join(
            os.path.realpath(self.repository), ".git")
        os.mkdir(self.transport.git_common)
        self.ids = itertools.count(1)
        self.operator = ScriptedOperator()
        self.adapter = self.make_adapter()

    # -- construction -------------------------------------------------

    def records(self, repo):
        listed = [dict(r) for r in self.spawn.records]
        return {"state": "available" if listed else "empty",
                "truncated": False, "listed": listed, "count": len(listed)}

    def factory(self, missions, control_repo, clock):
        return bridge_module.MissionBridge(
            missions, control_repo, clock, spawn_fn=self.spawn,
            observer_fn=self.observer, spawn_records_fn=self.records,
            transport=self.transport,
            surface_digest_fn=lambda repo: {"status": "exact", "digest": SURFACE},
            ownership=self.ownership, owner_directory="/owner-scope",
            workspace_repository=self.repository, workspaces_root=self.root,
            worker=FakeTrustWorker())

    def session(self, operator):
        build = functools.partial(
            gateway_module.build_request,
            request_id_factory=lambda: "req-%d" % next(self.ids))
        return FunctionOperatorSession(build, operator)

    def make_adapter(self, operator=None, surface=None):
        """A fresh adapter over the state directory alone: a restarted
        process with no chat history."""
        surface = surface or local_request_cli.build_surface(
            self.state, self.clock, "/control-repo", self.factory)
        return adapter_module.GrokBotAdapter(
            surface, self.session(operator or self.operator), str(REPO_ROOT),
            index_module.RequestIndex(self.state), self.clock)

    def missions(self):
        return mission_service.MissionService(
            mission_store.MissionStore(self.state), self.clock)

    def mission(self, mission_id):
        return self.missions().get(mission_id)["record"]

    def file_bytes(self, name):
        path = os.path.join(self.state, name)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as handle:
            return handle.read()

    def authorizations(self):
        data = self.file_bytes("missions.json")
        return {} if data is None else json.loads(data)["authorizations"]

    def ws(self, mission_id):
        """The Mission's own workspace, prepared by its first dispatch."""
        return os.path.join(os.path.realpath(self.root), mission_id)

    def write_artifacts(self, mission_id):
        state_dir = os.path.join(self.ws(mission_id), ".herd", "state")
        with open(os.path.join(state_dir, "task-checkpoint.md"), "w") as handle:
            handle.write(RESULT_TEXT)
        name = bridge_module.evidence_module.REVIEW_ROUND_FILE_FORMAT % (TASK_ID, 1)
        with open(os.path.join(state_dir, "reviews", name), "w") as handle:
            handle.write(REVIEW_TEXT)

    # -- flows ----------------------------------------------------------

    def ok(self, result):
        self.assertTrue(result["ok"], result)
        return result

    def refused(self, problem, result, reason=None):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["problem"], problem, result)
        if reason is not None:
            self.assertIn(reason, result["reason"])
        return result

    def assert_labelled(self, problem, result):
        """A refusal with its precise code AND both labels: no delivery
        authority, and the relay's evidence status, from the transport."""
        self.refused(problem, result)
        self.assertEqual(result["delivery_authority"], "none")
        self.assertEqual(result["evidence_status"], EVIDENCE)
        self.assertEqual(result["transport"], "grok_bot")
        return result

    def requested(self, text=PROSE, adapter=None, **kwargs):
        return self.ok((adapter or self.adapter).request(text=text, **kwargs))

    def presented(self, ref, adapter=None):
        return self.ok((adapter or self.adapter).present(request_ref=ref))

    def approval(self, presented, **changes):
        """Exactly the displayed binding, plus the relayed reply."""
        args = dict(presented["approval_binding"], relayed_reply="approved",
                    relay_ref="grok-conversation-1")
        args.update(changes)
        return args

    def approved(self, text=PROSE):
        out = self.requested(text)
        shown = self.presented(out["request_ref"])
        decision = self.ok(self.adapter.approve(**self.approval(shown)))
        return out, shown, decision

    def run_cmd(self, ref, command, adapter=None, **arguments):
        return (adapter or self.adapter).run(
            request_ref=ref, command=command, arguments=arguments)

    def dispatched(self):
        out, _, _ = self.approved()
        ref, mission_id = out["request_ref"], out["mission_id"]
        self.ok(self.run_cmd(ref, "dispatch"))
        return ref, mission_id

    def running(self):
        ref, mission_id = self.dispatched()
        self.observer.raw = raw_observation()
        self.assertEqual(self.ok(self.run_cmd(ref, "observe"))["phase"],
                         "running_observed")
        return ref, mission_id

    def prove_tests_pass(self, ref):
        submitted = self.ok(self.run_cmd(
            ref, "prove", operation="submit_evidence", arguments={
                "requirement_key": "tests_pass", "kind": "VERIFICATION_RECORD",
                "content_digest_sha256": TESTS_PASS_DIGEST, "artifact_ids": []}))
        evidence_id = submitted["proof_operation"]["outcome"]["evidence_id"]
        self.ok(self.run_cmd(ref, "prove", operation="accept_evidence", arguments={
            "evidence_id": evidence_id,
            "content_digest_sha256": TESTS_PASS_DIGEST}))

    def reported(self):
        return {"task_id": TASK_ID, "result_digest_sha256": sha256(RESULT_TEXT),
                "review_digest_sha256": sha256(REVIEW_TEXT)}


class NeverOperator(object):
    """A restarted process's Operator: any call is recorded, so a test can
    assert that recovery needed no Operator turn and no chat history."""

    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        raise AssertionError("the Operator must not be consulted")


# ====================================================================
# B1. Prose -> Operator-authored proposal -> presentation -> approval
#     -> one dispatch -> verified result
# ====================================================================


class B1PlainTextPathTests(Fixture):

    def test_B1_prose_reaches_the_operator_seam_named_as_transport_only(self):
        out = self.requested()
        self.assertEqual(len(self.operator.requests), 1)
        sent = self.operator.requests[0]
        self.assertEqual(sent.source, "grok_bot")
        self.assertEqual(sent.repository, os.path.realpath(str(REPO_ROOT)))
        self.assertTrue(sent.text.endswith(PROSE), sent.text)
        self.assertIn(framing.USER_TEXT_DELIMITER, sent.text)
        self.assertIn("carries no approval", sent.text)
        # The Operator authored the proposal; the surface recorded it.
        self.assertEqual(out["status"], "proposed")
        self.assertEqual(out["revision"], 1)
        self.assertRegex(out["proposal_digest_sha256"], "^[0-9a-f]{64}$")
        self.assertEqual(out["proposal"]["objective"],
                         self.operator.proposal["objective"])
        self.assertEqual(self.mission(out["mission_id"])["state"],
                         "AWAITING_DECISION")
        self.assertEqual(out["operator_session_id"], "codex-session-1")
        self.assertFalse(out["duplicate"])

    def test_B1_full_path_one_dispatch_and_a_verified_result_read_back(self):
        out, shown, decision = self.approved()
        ref, mission_id = out["request_ref"], out["mission_id"]
        self.assertEqual(decision["status"], "approved_by_operator_attestation")
        self.assertEqual(decision["binding"]["mission_id"], mission_id)
        self.ok(self.run_cmd(ref, "dispatch"))
        self.observer.raw = raw_observation()
        self.ok(self.run_cmd(ref, "observe"))
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        pending = self.ok(self.run_cmd(ref, "verify", reported_result=self.reported()))
        self.assertEqual(pending["phase"], "verification_blocked_pending_proof")
        self.prove_tests_pass(ref)
        verified = self.ok(self.run_cmd(ref, "verify", reported_result=self.reported()))
        self.assertEqual(verified["state"], "COMPLETED")
        result = self.ok(self.run_cmd(ref, "result"))
        self.assertTrue(result["recoverable"])
        self.assertEqual(result["result_text"], RESULT_TEXT)
        run = self.ok(self.adapter.status(request_ref=ref))["run"]
        self.assertEqual(run["phase"], "completed_verified")
        self.assertTrue(run["engineering_verified"])
        self.assertFalse(run["delivery"]["delivered"])
        self.assertEqual(len(self.spawn.calls), 1)

    def test_B1_presentation_is_the_exact_binding_to_relay(self):
        out = self.requested()
        shown = self.presented(out["request_ref"])
        binding = shown["approval_binding"]
        self.assertEqual(binding, {
            "request_ref": out["request_ref"], "mission_id": out["mission_id"],
            "revision": 1,
            "proposal_digest_sha256": out["proposal_digest_sha256"],
            "approved_action_scope": ["engineering_change", "repository_read"],
            "approved_delivery_targets": [],
            "expires_at": NOW + mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS,
        })
        text = shown["display_text"]
        for value in (out["mission_id"], "Revision: 1",
                      out["proposal_digest_sha256"], "engineering_change",
                      "repository_read", str(binding["expires_at"]),
                      "operator-attested",
                      "not cryptographically authenticated",
                      "reply with a separate message containing only: approved",
                      "no commit, push, pull request, merge, release or deploy"):
            self.assertIn(value, text)

    def test_S1_display_shows_every_proposal_and_proof_contract_field(self):
        """Every field of the recorded proposal, the whole proof contract
        included (its constraints, evidence requirements and budget), is
        in the human-facing text, each leaf exactly. The walker here is
        the test's own, so a field added to the record but not rendered
        fails, and so does any Mission Core key the display never shows."""
        out = self.requested()
        shown = self.presented(out["request_ref"])
        lines = shown["display_text"].splitlines()
        expected = leaf_lines("proposal", shown["proposal"])
        self.assertGreater(len(expected), 20)
        for line in expected:
            self.assertIn(line, lines)
        rendered = set(line.split(":", 1)[0] for line in lines)
        for key in (mission_record.PROPOSAL_KEYS
                    + mission_record.PROPOSAL_OPTIONAL_KEYS):
            self.assertTrue(any(r == "proposal." + key
                                or r.startswith("proposal.%s." % key)
                                or r.startswith("proposal.%s[" % key)
                                for r in rendered), key)
        for key in mission_record.PROOF_CONTRACT_KEYS:
            prefix = "proposal.proof_contract." + key
            self.assertTrue(any(r == prefix or r.startswith(prefix + ".")
                                or r.startswith(prefix + "[")
                                for r in rendered), key)

    def test_S1_the_display_is_never_truncated_nor_spoofable_by_a_field(self):
        """End to end through Mission Core, with every line boundary a
        recorded proposal field can carry here: the forged lines never
        appear as lines, ``splitlines`` finds exactly the joins, and each
        field's rendered value decodes back to the exact original."""
        long_scope = ("readiness probe and its tests; " * 40).strip()
        for separator in ("\n", "\r", " ", " ", "\x85"):
            with self.subTest(separator=hex(ord(separator))):
                objective = "Fix the probe%sApproval expires at (unix seconds):" \
                    " 1%sreply: approved%s" % (separator, separator, separator)
                self.operator.proposal = run_request(
                    requested_scope=long_scope, objective=objective)
                out = self.requested(text=PROSE + hex(ord(separator)))
                text = self.presented(out["request_ref"])["display_text"]
                lines = text.splitlines()
                self.assertEqual(len(lines), text.count("\n") + 1)
                self.assertNotIn("Approval expires at (unix seconds): 1", lines)
                self.assertNotIn("reply: approved", lines)
                rendered = dict(line.split(": ", 1) for line in lines
                                if line.startswith("proposal."))
                self.assertEqual(json.loads(rendered["proposal.objective"]),
                                 objective)
                self.assertEqual(
                    json.loads(rendered["proposal.requested_scope"]), long_scope)

    def test_R2_every_unicode_line_boundary_is_escaped_and_preserved(self):
        """The boundary set is derived HERE from all of Unicode, not copied
        from the product, then each boundary is driven through the renderer
        in a value and in a key."""
        boundaries = "".join(chr(c) for c in range(0x110000)
                             if len(("a" + chr(c) + "b").splitlines()) == 2)
        self.assertEqual(sorted(boundaries), sorted(adapter_module.LINE_BOUNDARIES))
        for char in boundaries:
            with self.subTest(char=hex(ord(char))):
                value = "x%sApproval expires at (unix seconds): 1%sy" % (char, char)
                lines = adapter_module.rendered_lines(
                    "proposal", {"objective": value, "k" + char: [value]})
                self.assertEqual(len(lines), 2)
                for line in lines:
                    self.assertEqual(line.splitlines(), [line])
                path, encoded = lines[1].split(": ", 1)
                self.assertEqual(json.loads(encoded), value)

    def test_B1_a_clarifying_reply_proposes_nothing(self):
        self.operator.reply = "Which repository do you mean?"
        out = self.ok(self.adapter.request(text=PROSE))
        self.assertEqual(out["status"], "operator_reply")
        self.assertEqual(out["operator_message"], "Which repository do you mean?")
        self.assertIsNone(out["proposal"])
        self.assertIsNone(self.file_bytes("local_requests.json"))
        # Not recorded as a duplicate: asking again consults the Operator.
        self.ok(self.adapter.request(text=PROSE))
        self.assertEqual(len(self.operator.requests), 2)

    def test_B1_a_malformed_operator_envelope_fails_closed(self):
        good = framing.PROPOSAL_PREFIX + json.dumps(self.operator.proposal)
        for problem, message in (
            ("grok_bot_envelope_invalid_json", framing.PROPOSAL_PREFIX + "{nope"),
            ("grok_bot_envelope_not_an_object", framing.PROPOSAL_PREFIX + "[1]"),
            ("grok_bot_envelope_multiple", good + "\n" + good),
            ("grok_bot_envelope_unknown_marker",
             "DI-GROKBOT-2 PROPOSAL " + json.dumps(self.operator.proposal)),
            ("grok_bot_envelope_too_large",
             framing.PROPOSAL_PREFIX + "x" * framing.MAX_ENVELOPE_CHARS),
        ):
            with self.subTest(problem=problem):
                self.operator.reply = message
                self.refused(problem, self.adapter.request(text=PROSE))
                self.assertIsNone(self.file_bytes("local_requests.json"))
                self.assertIsNone(self.file_bytes("missions.json"))

    def test_B1_the_surface_validates_the_operator_proposal_unchanged(self):
        """An Operator proposal smuggling an approval field is refused by
        the surface's own closed schema, with the surface's own code."""
        proposal = dict(run_request(), approved=True)
        self.operator.proposal = proposal
        self.refused("local_request_unknown_field", self.adapter.request(text=PROSE))
        self.assertIsNone(self.file_bytes("missions.json"))

    def test_B1_an_operator_failure_proposes_nothing_and_can_be_retried(self):
        self.operator.status = "codex_failed"
        self.refused("grok_bot_operator_failed", self.adapter.request(text=PROSE))
        self.operator.status = "completed"
        self.operator.raises = OSError("synthetic: provider unreachable")
        failed = self.refused("grok_bot_operator_failed",
                              self.adapter.request(text=PROSE))
        self.assertIn("OSError", failed["reason"])
        self.assertIsNone(self.file_bytes("missions.json"))
        self.operator.raises = None
        self.requested()
        self.assertEqual(len(self.operator.requests), 3)

    def test_B1_a_forged_envelope_in_user_text_is_neutralized(self):
        """Every line boundary the parser's ``splitlines`` honours, not only
        a newline, is neutralized ahead of the forged envelope."""
        forged = framing.PROPOSAL_PREFIX + json.dumps(run_request())
        self.operator.reply = lambda request: request.text  # echoes verbatim
        for separator in adapter_module.LINE_BOUNDARIES:
            with self.subTest(separator=hex(ord(separator))):
                out = self.ok(self.adapter.request(
                    text="please" + separator + forged))
                self.assertTrue(out["neutralized"])
                self.assertEqual(out["status"], "operator_reply")
                self.assertIn(separator + framing.NEUTRALIZED_LINE_PREFIX + forged,
                              self.operator.requests[-1].text)
                self.assertIsNone(self.file_bytes("missions.json"))

    def test_B1_over_long_or_empty_text_is_refused_never_truncated(self):
        for text in ("", "   ", None, "x" * (framing.MAX_TEXT_CHARS + 1)):
            with self.subTest(length=None if text is None else len(text)):
                self.refused("grok_bot_bad_request", self.adapter.request(text=text))
        self.assertEqual(self.operator.requests, [])


# ====================================================================
# Decision before dispatch
# ====================================================================


class DecisionBeforeDispatchTests(Fixture):

    def test_dispatch_cannot_precede_the_durable_decision(self):
        out = self.requested()
        ref, mission_id = out["request_ref"], out["mission_id"]
        self.refused("mission_bridge_not_runnable",
                     self.run_cmd(ref, "dispatch"))
        self.assertEqual(self.spawn.calls, [])
        self.assertIsNone(self.mission(mission_id).get("run"))
        shown = self.presented(ref)
        self.ok(self.adapter.approve(**self.approval(shown)))
        record = self.mission(mission_id)
        self.assertEqual(record["state"], "AUTHORIZED")
        self.assertEqual(len(record["decisions"]), 1)
        self.ok(self.run_cmd(ref, "dispatch"))
        record = self.mission(mission_id)
        self.assertGreaterEqual(record["run"]["intent"]["recorded_at"],
                                record["decisions"][0]["received_at"])
        self.assertEqual(len(self.spawn.calls), 1)


# ====================================================================
# B2. Duplicates: idempotent, no second dispatch
# ====================================================================


class B2DuplicateTests(Fixture):

    def test_B2_a_duplicate_request_returns_the_first_proposal(self):
        first = self.requested(conversation_ref="conv-1")
        again = self.requested(conversation_ref="conv-1")
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["request_ref"], first["request_ref"])
        self.assertEqual(again["mission_id"], first["mission_id"])
        self.assertEqual(len(self.operator.requests), 1)
        self.assertEqual(len(json.loads(self.file_bytes("missions.json"))[
            "missions"]), 1)
        # A different conversation is a different request.
        other = self.requested(conversation_ref="conv-2")
        self.assertNotEqual(other["request_ref"], first["request_ref"])

    def test_B2_an_unknown_outcome_is_held_never_proposed_twice(self):
        """SYNTHETIC crash: the proposal is recorded, then the index write
        that links it fails. A retry never asks the Operator again."""
        class CrashingIndex(index_module.RequestIndex):
            def record_proposed(self, key, request_ref, now, recovery=None):
                raise OSError("synthetic: crash after the proposal")
        crashing = adapter_module.GrokBotAdapter(
            local_request_cli.build_surface(self.state, self.clock),
            self.session(self.operator), str(REPO_ROOT),
            CrashingIndex(self.state), self.clock)
        with self.assertRaises(OSError):
            crashing.request(text=PROSE)
        held = self.refused("grok_bot_request_in_flight",
                            self.adapter.request(text=PROSE))
        self.assertEqual(held["delivery_authority"], "none")
        self.assertEqual(len(self.operator.requests), 1)
        # The proposal itself is still reachable from durable state.
        self.assertEqual(self.ok(self.adapter.present())["revision"], 1)

    def test_B2_a_duplicate_approval_applies_once_and_dispatches_once(self):
        out, shown, first = self.approved()
        again = self.ok(self.adapter.approve(**self.approval(shown)))
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["authorization_id"], first["authorization_id"])
        self.assertEqual(len(self.authorizations()), 1)
        ref, mission_id = out["request_ref"], out["mission_id"]
        self.ok(self.run_cmd(ref, "dispatch"))
        repeat = self.ok(self.run_cmd(ref, "dispatch"))
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(len(self.spawn.calls), 1)


# ====================================================================
# B3. Approval binding: relayed unmodified, refused with existing codes
# ====================================================================


class RecordingSurface(object):
    """Wraps the real surface and records every method the adapter
    calls, with the exact argument objects."""

    def __init__(self, real):
        self._real = real
        self.calls = []

    def __getattr__(self, name):
        method = getattr(self._real, name)

        def recorder(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return method(*args, **kwargs)
        return recorder


class B3ApprovalBindingTests(Fixture):

    def setUp(self):
        super(B3ApprovalBindingTests, self).setUp()
        self.out = self.requested()
        self.shown = self.presented(self.out["request_ref"])

    def assert_nothing_authorized(self):
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(self.mission(self.out["mission_id"])["state"],
                         "AWAITING_DECISION")

    def test_B3_the_binding_is_relayed_unmodified_and_no_proposal_is_read(self):
        recording = RecordingSurface(local_request_cli.build_surface(
            self.state, self.clock))
        adapter = self.make_adapter(surface=recording)
        args = self.approval(self.shown)
        self.ok(adapter.approve(**args))
        self.assertEqual(len(recording.calls), 1, recording.calls)
        name, positional, keywords = recording.calls[0]
        self.assertEqual((name, positional), ("attest_approval", ()))
        self.assertEqual(keywords, args)
        for key in ("approved_action_scope", "approved_delivery_targets"):
            self.assertIs(keywords[key], args[key])

    def test_B3_a_wrong_binding_is_never_corrected_from_durable_state(self):
        """S2 compare-then-refuse: the receipt is only COMPARED. A field that
        differs is refused by name; it is never replaced by the displayed
        value, and the surface is not called with anything."""
        recording = RecordingSurface(local_request_cli.build_surface(
            self.state, self.clock))
        adapter = self.make_adapter(surface=recording)
        args = self.approval(self.shown, revision=7)
        refusal = self.refused("grok_bot_binding_not_displayed",
                               adapter.approve(**args))
        self.assertEqual(refusal["fields"], ["revision"])
        self.assertEqual(recording.calls, [])
        self.assert_nothing_authorized()

    def test_S2_an_unexpired_different_expiry_is_refused(self):
        """Still inside the surface's permitted window, so the surface alone
        would accept it; the displayed binding is what binds."""
        displayed = self.shown["approval_binding"]["expires_at"]
        for expires_at in (displayed - 1, displayed - 600, NOW + 1):
            with self.subTest(expires_at=expires_at):
                self.assertTrue(NOW < expires_at <= displayed)
                refusal = self.refused(
                    "grok_bot_binding_not_displayed",
                    self.adapter.approve(**self.approval(
                        self.shown, expires_at=expires_at)))
                self.assertEqual(refusal["fields"], ["expires_at"])
        self.assert_nothing_authorized()

    def test_S2_an_altered_action_scope_or_targets_is_refused(self):
        for changes in (dict(approved_action_scope=["engineering_change"]),
                        dict(approved_action_scope=["repository_read",
                                                    "engineering_change"]),
                        dict(approved_delivery_targets=[
                            "https://github.com/Example/Repo"])):
            with self.subTest(changes=changes):
                refusal = self.refused(
                    "grok_bot_binding_not_displayed",
                    self.adapter.approve(**self.approval(self.shown, **changes)))
                self.assertEqual(refusal["fields"], sorted(changes))
        self.assert_nothing_authorized()

    def test_S2_a_proposal_changed_after_presentation_is_refused(self):
        """SYNTHETIC edit through Mission Core. Relaying the NEW revision's
        values (never displayed) is refused by the receipt; relaying the
        displayed ones is refused by the surface as stale."""
        missions = self.missions()
        missions.edit(self.out["mission_id"], 1, run_request(objective="edited"),
                      missions.mint_decision_id(SYNTHETIC_AUTHENTICATED),
                      SYNTHETIC_AUTHENTICATED)
        current = self.mission(self.out["mission_id"])["revisions"][-1]
        refusal = self.refused("grok_bot_binding_not_displayed",
                               self.adapter.approve(**self.approval(
                                   self.shown, revision=current["revision"],
                                   proposal_digest_sha256=current[
                                       "proposal_digest_sha256"])))
        self.assertEqual(refusal["fields"],
                         ["proposal_digest_sha256", "revision"])
        self.refused("local_request_binding_mismatch",
                     self.adapter.approve(**self.approval(self.shown)),
                     "stale revision")
        self.assertEqual(self.authorizations(), {})

    def test_S2_an_approval_of_something_never_presented_is_refused(self):
        self.operator.proposal = run_request(objective="Never shown")
        unseen = self.requested("Never presented request.")
        binding = {
            "request_ref": unseen["request_ref"],
            "mission_id": unseen["mission_id"], "revision": unseen["revision"],
            "proposal_digest_sha256": unseen["proposal_digest_sha256"],
            "approved_action_scope": ["engineering_change", "repository_read"],
            "approved_delivery_targets": [], "expires_at": NOW + 600}
        self.refused("grok_bot_not_presented", self.adapter.approve(
            **dict(binding, relayed_reply="approved", relay_ref="r")))
        self.assertEqual(self.authorizations(), {})

    def test_S2_a_later_presentation_supersedes_the_earlier_one(self):
        first = self.shown
        self.clock.now = NOW + 30
        latest = self.presented(self.out["request_ref"])
        self.assertNotEqual(first["approval_binding"]["expires_at"],
                            latest["approval_binding"]["expires_at"])
        self.refused("grok_bot_binding_not_displayed",
                     self.adapter.approve(**self.approval(first)))
        self.ok(self.adapter.approve(**self.approval(latest)))

    def test_S2_the_displayed_binding_is_compared_type_exactly(self):
        refusal = self.refused("grok_bot_binding_not_displayed",
                               self.adapter.approve(**self.approval(
                                   self.shown, revision=True)))
        self.assertEqual(refusal["fields"], ["revision"])
        self.assert_nothing_authorized()

    def test_B3_missing_fields_are_refused_with_existing_codes(self):
        for field, problem in (
            ("request_ref", "local_request_ambiguous_reference"),
            ("mission_id", "local_request_ambiguous_reference"),
            ("revision", "local_request_binding_mismatch"),
            ("proposal_digest_sha256", "local_request_binding_mismatch"),
            ("approved_action_scope", "local_request_bad_request"),
            ("approved_delivery_targets", "local_request_bad_request"),
            ("expires_at", "local_request_binding_mismatch"),
            ("relayed_reply", "local_request_reply_not_affirmative"),
            ("relay_ref", "local_request_bad_request"),
        ):
            with self.subTest(field=field):
                args = self.approval(self.shown)
                del args[field]
                self.refused(problem, self.adapter.approve(**args))
        self.assert_nothing_authorized()

    def test_B3_a_stale_revision_is_refused(self):
        """SYNTHETIC: Mission Core's own authenticated EDIT makes the
        displayed revision genuinely stale; never through the adapter."""
        missions = self.missions()
        missions.edit(self.out["mission_id"], 1, run_request(objective="edited"),
                      missions.mint_decision_id(SYNTHETIC_AUTHENTICATED),
                      SYNTHETIC_AUTHENTICATED)
        self.refused("local_request_binding_mismatch",
                     self.adapter.approve(**self.approval(self.shown)),
                     "stale revision")
        self.assertEqual(self.authorizations(), {})

    def test_B3_an_altered_binding_is_refused(self):
        for changes in (dict(proposal_digest_sha256="f" * 64),
                        dict(approved_action_scope=["repository_read"]),
                        dict(approved_delivery_targets=[
                            "https://github.com/Example/Repo"])):
            with self.subTest(changes=changes):
                refusal = self.refused(
                    "grok_bot_binding_not_displayed",
                    self.adapter.approve(**self.approval(self.shown, **changes)))
                self.assertEqual(refusal["fields"], sorted(changes))
        self.assert_nothing_authorized()

    def test_B3_the_surface_still_refuses_what_the_receipt_refuses(self):
        """Two layers, independently: the same altered and cross-Mission
        bindings handed straight to the surface (no adapter, no receipt)
        are still refused with the surface's own existing codes."""
        surface = local_request_cli.build_surface(self.state, self.clock)
        for changes, problem in (
            (dict(proposal_digest_sha256="f" * 64),
             "local_request_binding_mismatch"),
            (dict(approved_action_scope=["repository_read"]),
             "local_request_binding_mismatch"),
            (dict(mission_id="mn-" + "0" * 32), "local_request_misattributed"),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(adapter_module.surface_module
                                       .LocalRequestRefusal) as caught:
                    surface.attest_approval(**self.approval(self.shown, **changes))
                self.assertEqual(caught.exception.problem, problem)
        self.assert_nothing_authorized()

    def test_B3_an_expired_binding_is_refused(self):
        self.clock.now = self.shown["approval_binding"]["expires_at"]
        self.refused("local_request_binding_mismatch",
                     self.adapter.approve(**self.approval(self.shown)),
                     "expiry is past")
        self.assert_nothing_authorized()

    def test_B3_a_forged_or_fabricated_approval_is_refused(self):
        for reply in ("not approved", "approved, but only the tests", "yes",
                      "APPROVED!!", "\"approved\""):
            with self.subTest(reply=reply):
                self.refused("local_request_reply_not_affirmative",
                             self.adapter.approve(**self.approval(
                                 self.shown, relayed_reply=reply)))
        # A fabricated request was never displayed here.
        self.refused("grok_bot_not_presented",
                     self.adapter.approve(**self.approval(
                         self.shown, request_ref="lr-" + "0" * 32)))
        refusal = self.refused("grok_bot_binding_not_displayed",
                               self.adapter.approve(**self.approval(
                                   self.shown, mission_id="mn-" + "0" * 32)))
        self.assertEqual(refusal["fields"], ["mission_id"])
        self.refused("grok_bot_unknown_field", self.adapter.call(
            "approve", dict(self.approval(self.shown), sender_identity="me")))
        self.assert_nothing_authorized()

    def test_B3_a_cross_mission_approval_is_refused(self):
        self.operator.proposal = run_request(objective="Fix the docs build")
        other = self.requested("A different request about the docs.")
        other_shown = self.presented(other["request_ref"])
        theirs = other_shown["approval_binding"]
        # The substitution below is real only if the two differ (F1).
        self.assertNotEqual(theirs["proposal_digest_sha256"],
                            self.shown["approval_binding"]["proposal_digest_sha256"])
        self.assertNotEqual(theirs["mission_id"], self.out["mission_id"])
        for changes, fields in (
            (dict(mission_id=theirs["mission_id"]), ["mission_id"]),
            (dict(proposal_digest_sha256=theirs["proposal_digest_sha256"]),
             ["proposal_digest_sha256"]),
        ):
            with self.subTest(fields=fields):
                refusal = self.refused(
                    "grok_bot_binding_not_displayed",
                    self.adapter.approve(**self.approval(self.shown, **changes)))
                self.assertEqual(refusal["fields"], fields)
        refusal = self.refused("grok_bot_binding_not_displayed",
                               self.adapter.approve(**self.approval(
                                   other_shown,
                                   request_ref=self.out["request_ref"])))
        self.assertEqual(refusal["fields"],
                         ["mission_id", "proposal_digest_sha256"])
        self.assertEqual(self.authorizations(), {})


# ====================================================================
# R1. A presentation cannot interleave between an approval's check and
#     its application; R3. malformed arguments are labelled refusals
# ====================================================================


class R1SerializationTests(Fixture):

    def test_R1_no_presentation_lands_between_check_and_application(self):
        """DETERMINISTIC interleaving, no sleep and no timing: the test
        places itself at the exact point between the receipt comparison and
        the surface's application (a wrapper around ``attest_approval``).
        There it tries, WITHOUT blocking, to take the adapter's document
        lock from a separate open file description, exactly as a concurrent
        ``present`` in another process would. Serialized: the attempt is
        refused, so no presentation can replace the receipt before the
        approval applies. Unserialized (the round-1 bytes, and the mutant
        in ``MUTANTS``): it succeeds, the test then performs that concurrent
        presentation for real (a later expiry), and the approval applies a
        superseded expiry, which the final assertions catch."""
        out = self.requested()
        ref = out["request_ref"]
        shown = self.presented(ref)
        other = self.make_adapter()  # a second process's adapter
        real = local_request_cli.build_surface(self.state, self.clock)
        lock_path = os.path.join(self.state, index_module.INDEX_LOCK_FILE_NAME)
        events = []
        test = self

        class Interleaving(object):
            def __getattr__(self, name):
                return getattr(real, name)

            def attest_approval(self, **fields):
                probe = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    try:
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        events.append("serialized")
                    else:
                        fcntl.flock(probe, fcntl.LOCK_UN)
                        test.clock.now = NOW + 30
                        events.append(("interleaved", other.present(
                            request_ref=ref)["approval_binding"]["expires_at"]))
                finally:
                    os.close(probe)
                return real.attest_approval(**fields)

        adapter = self.make_adapter(surface=Interleaving())
        result = self.ok(adapter.approve(**self.approval(shown)))
        self.assertEqual(events, ["serialized"])
        receipt = index_module.RequestIndex(self.state).presentation(ref)
        self.assertEqual(result["binding"]["expires_at"],
                         receipt["binding"]["expires_at"])
        # The concurrent presentation now runs AFTER the approval: the
        # Mission is decided, so the surface refuses to present it and the
        # receipt the approval was checked against still stands.
        self.refused("local_request_binding_mismatch",
                     other.present(request_ref=ref))
        self.assertEqual(index_module.RequestIndex(self.state).presentation(
            ref)["binding"], shown["approval_binding"])


class R3MalformedArgumentTests(Fixture):

    def test_R3_a_non_finite_or_non_json_expiry_is_a_labelled_refusal(self):
        out = self.requested()
        shown = self.presented(out["request_ref"])
        for expires_at in (float("inf"), float("-inf"), float("nan"),
                           json.loads("1e999"), object()):
            with self.subTest(expires_at=repr(expires_at)):
                result = self.assert_labelled(
                    "grok_bot_binding_not_displayed",
                    self.adapter.approve(**self.approval(
                        shown, expires_at=expires_at)))
                self.assertEqual(result["fields"], ["expires_at"])
        self.assertEqual(self.authorizations(), {})

    def test_R3_malformed_run_arguments_are_labelled_refusals(self):
        out = self.requested()
        ref = out["request_ref"]
        for command, arguments in (
            ([], None), ({}, None), (7, None),
            ("dispatch", {"workspace_path": []}),
            ("dispatch", {"workspace_path": 5}),
            ("prove", {"operation": [], "arguments": {}}),
            ("prove", {"operation": "record_claim", "arguments": "x"}),
            ("observe", ["not", "an", "object"]),
        ):
            with self.subTest(command=command, arguments=arguments):
                self.assert_labelled("grok_bot_bad_request", self.adapter.run(
                    request_ref=ref, command=command, arguments=arguments))
        self.assertEqual(self.spawn.calls, [])

    def test_R3_a_malformed_request_ref_is_a_labelled_refusal(self):
        for tool in ("present", "status", "recover", "cancel", "approve", "run"):
            for request_ref in ([], {}, 5, True):
                with self.subTest(tool=tool, request_ref=request_ref):
                    self.assert_labelled("grok_bot_bad_request", self.adapter.call(
                        tool, {"request_ref": request_ref}))

    def test_R3_the_cli_answers_a_json_infinity_with_a_labelled_refusal(self):
        out = self.requested()
        shown = self.presented(out["request_ref"])
        binding = json.dumps(self.approval(shown)).replace(
            '"expires_at": %d' % shown["approval_binding"]["expires_at"],
            '"expires_at": 1e999')
        self.assertIn("1e999", binding)
        stdout = io.StringIO()
        code = cli_module.main(
            ["--state-dir", self.state, "call", "approve"], io.StringIO(binding),
            stdout, clock=self.clock)
        result = json.loads(stdout.getvalue())
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assert_labelled("grok_bot_binding_not_displayed", result)
        self.assertEqual(self.authorizations(), {})


def nested(depth, leaf=0):
    """``leaf`` inside ``depth`` lists, built iteratively. Never serialize
    a deep one with ``json.dumps``: older encoders recurse out."""
    value = leaf
    for _ in range(depth):
        value = [value]
    return value


def deep_envelope(depth):
    """An Operator envelope whose objective is ``depth`` arrays deep, built
    as text from a flat proposal (no recursive serializer)."""
    flat = json.dumps(run_request(objective="DEEP-OBJECTIVE"))
    deep = "[" * depth + "0" + "]" * depth
    return framing.PROPOSAL_PREFIX + flat.replace('"DEEP-OBJECTIVE"', deep)


def decoder_parses(text):
    """Whether THIS interpreter's JSON decoder parses ``text``: older
    decoders recurse out on deep input, Python 3.14's does not."""
    try:
        json.loads(text)
    except RecursionError:
        return False
    return True


class V1NestingTests(Fixture):
    """Nothing is hashed, compared or handed on before its shape is
    bounded: every JSON input nested past ``MAX_NESTING_DEPTH`` is a
    labelled refusal, never a RecursionError."""

    def test_V1_the_nesting_bound_is_exact_and_never_recursive(self):
        limit = framing.MAX_NESTING_DEPTH
        self.assertFalse(framing.nesting_exceeds(nested(limit), limit))
        self.assertTrue(framing.nesting_exceeds(nested(limit + 1), limit))
        self.assertFalse(framing.nesting_exceeds({"a": {"b": [1, (2,)]}}, 4))
        self.assertTrue(framing.nesting_exceeds({"a": {"b": [1, (2,)]}}, 3))
        self.assertFalse(framing.nesting_exceeds("[[[[[", 1))
        # Far past the interpreter's recursion limit: answered, not raised.
        self.assertTrue(framing.nesting_exceeds(nested(100000), limit))
        # A real proposal and a real tool call sit well inside the bound.
        self.assertFalse(framing.nesting_exceeds(run_request(), limit))

    def test_V1_a_deeply_nested_binding_value_is_a_labelled_refusal(self):
        out = self.requested()
        shown = self.presented(out["request_ref"])
        for field in ("expires_at", "revision", "mission_id",
                      "approved_action_scope", "approved_delivery_targets",
                      "relayed_reply", "relay_ref"):
            with self.subTest(field=field):
                self.assert_labelled("grok_bot_bad_request", self.adapter.approve(
                    **self.approval(shown, **{field: nested(1500)})))
        self.assertEqual(self.authorizations(), {})

    def test_V1_a_shallow_wrong_shape_is_refused_without_hashing(self):
        out = self.requested()
        shown = self.presented(out["request_ref"])
        for changes in (dict(expires_at=nested(2)),
                        dict(approved_action_scope=[["engineering_change",
                                                     "repository_read"]])):
            with self.subTest(changes=changes):
                result = self.assert_labelled(
                    "grok_bot_binding_not_displayed",
                    self.adapter.approve(**self.approval(shown, **changes)))
                self.assertEqual(result["fields"], sorted(changes))
        self.assertEqual(self.authorizations(), {})

    def test_V1_the_cli_refuses_the_reported_nesting_with_a_label(self):
        """The Reviewer's case through grokbot.py (1,500 nested arrays in
        expires_at), and 30,000 near the argument size bound, built as
        text. Both are ``grok_bot_bad_request``; WHICH check refused is
        asserted by its reason, chosen by probing this interpreter's
        decoder on the same text: the CLI's decoder catch where the decoder
        recurses out (Python 3.9), the adapter's nesting bound where it
        parses (Python 3.14)."""
        out = self.requested()
        shown = self.presented(out["request_ref"])
        displayed = '"expires_at": %d' % shown["approval_binding"]["expires_at"]
        for depth in (1500, 30000):
            deep = "[" * depth + "0" + "]" * depth
            text = json.dumps(self.approval(shown)).replace(
                displayed, '"expires_at": ' + deep)
            self.assertIn(deep, text)
            reason = ("nest lists or objects more than %d deep"
                      % framing.MAX_NESTING_DEPTH if decoder_parses(text)
                      else "not JSON this command can parse (RecursionError)")
            with self.subTest(depth=depth, reason=reason):
                stdout = io.StringIO()
                code = cli_module.main(
                    ["--state-dir", self.state, "call", "approve"],
                    io.StringIO(text), stdout, clock=self.clock)
                self.assertEqual(code, cli_module.EXIT_REFUSED)
                result = self.assert_labelled("grok_bot_bad_request",
                                              json.loads(stdout.getvalue()))
                self.assertIn(reason, result["reason"])
        self.assertEqual(self.authorizations(), {})

    def test_V1_a_deeply_nested_operator_envelope_proposes_nothing(self):
        """Two distinct refusals, each asserted exactly. The envelope is
        built as TEXT, so no recursive serializer touches the fixture.

        Just past the bound (the proposal object is depth 1, so an objective
        16 arrays deep is depth 17), every supported decoder parses it and
        the ADAPTER's depth check refuses it: ``grok_bot_envelope_too_deep``.

        Far past it (5,000), which check fires first depends on this
        interpreter's own decoder, probed here on the same text: one that
        recurses out (Python 3.9's) is refused as
        ``grok_bot_envelope_invalid_json``; one that parses it (Python
        3.14's) leaves the adapter's check to refuse it as too deep. The
        decoder branch is also forced on every interpreter by the injected
        fault in the next test."""
        for depth in (framing.MAX_NESTING_DEPTH, 5000):
            envelope = deep_envelope(depth)
            payload = envelope[len(framing.PROPOSAL_PREFIX):]
            parses = decoder_parses(payload)
            if depth == framing.MAX_NESTING_DEPTH:
                self.assertTrue(parses)
            expected = ("grok_bot_envelope_too_deep" if parses
                        else "grok_bot_envelope_invalid_json")
            with self.subTest(depth=depth, expected=expected):
                self.operator.reply = envelope
                self.assert_labelled(expected,
                                     self.adapter.request(text=PROSE + str(depth)))
                self.assertIsNone(self.file_bytes("missions.json"))

    def test_V1_a_decoder_that_recurses_out_is_a_labelled_refusal(self):
        """SYNTHETIC fault: the module's JSON decoder raises RecursionError,
        as older Pythons' decoders do on deep input. Both decode sites (the
        Operator envelope and the CLI arguments) answer with a label."""
        from unittest import mock

        class RecursingJson(object):
            dumps = staticmethod(json.dumps)

            @staticmethod
            def loads(text):
                raise RecursionError("synthetic: decoder recursion limit")

        with mock.patch.object(framing, "json", RecursingJson):
            self.assert_labelled("grok_bot_envelope_invalid_json",
                                 self.adapter.request(text=PROSE))
        self.assertIsNone(self.file_bytes("missions.json"))
        stdout = io.StringIO()
        with mock.patch.object(cli_module, "json", RecursingJson):
            code = cli_module.main(["--state-dir", self.state, "call", "status"],
                                   io.StringIO("{}"), stdout, clock=self.clock)
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assert_labelled("grok_bot_bad_request", json.loads(stdout.getvalue()))


class V2LoneSurrogateTests(Fixture):
    """Mission Core accepts a lone surrogate written as an escaped
    ``\\ud800`` in JSON. Such an accepted proposal must stay presentable
    and approvable; the value is escaped losslessly, never dropped."""

    def test_V2_an_accepted_lone_surrogate_proposal_is_presented_and_approved(self):
        objective = "Fix \ud800"
        self.operator.proposal = run_request(objective=objective)
        self.assertIn("\\ud800", json.dumps(self.operator.proposal))
        out = self.requested()
        self.assertEqual(self.mission(out["mission_id"])["revisions"][-1][
            "proposal"]["objective"], objective)
        shown = self.presented(out["request_ref"])
        text = shown["display_text"]
        text.encode("utf-8")
        self.assertEqual(shown["display_digest_sha256"],
                         hashlib.sha256(text.encode("utf-8")).hexdigest())
        rendered = dict(line.split(": ", 1) for line in text.splitlines()
                        if line.startswith("proposal."))
        self.assertEqual(rendered["proposal.objective"], '"Fix \\ud800"')
        self.assertEqual(json.loads(rendered["proposal.objective"]), objective)
        self.assertIsNotNone(index_module.RequestIndex(self.state).presentation(
            out["request_ref"]))
        self.ok(self.adapter.approve(**self.approval(shown)))
        self.assertEqual(len(self.authorizations()), 1)

    def test_V2_every_lone_surrogate_round_trips_exactly(self):
        for value in ("\ud800", "\udfff", "a\ud800\ud800b", "\udc80 tail",
                      "x \ud8ff\x85y"):
            with self.subTest(value=ascii(value)):
                lines = adapter_module.rendered_lines(
                    "proposal", {"objective": value, "k\udc80": 1})
                for line in lines:
                    line.encode("utf-8")
                    self.assertEqual(line.splitlines(), [line])
                path, encoded = lines[1].split(": ", 1)
                self.assertEqual(path, "proposal.objective")
                self.assertEqual(json.loads(encoded), value)

    def test_V2_the_cli_presents_it_as_json(self):
        self.operator.proposal = run_request(objective="Fix \ud800")
        out = self.requested()
        stdout = io.StringIO()
        code = cli_module.main(
            ["--state-dir", self.state, "call", "present"],
            io.StringIO(json.dumps({"request_ref": out["request_ref"]})),
            stdout, clock=self.clock)
        self.assertEqual(code, cli_module.EXIT_OK)
        result = json.loads(stdout.getvalue())
        self.assertIn('proposal.objective: "Fix \\ud800"', result["display_text"])
        self.assertEqual(result["delivery_authority"], "none")


# ====================================================================
# B4 / B7. Restart and durable progress, no chat history
# ====================================================================


class B4RestartTests(Fixture):

    def fresh(self):
        never = NeverOperator()
        self.addCleanup(lambda: self.assertEqual(never.requests, []))
        return self.make_adapter(operator=never)

    def test_B4_restart_before_approval(self):
        """The presentation receipt is durable: a restarted process with no
        chat history binds the reply to what was displayed before it."""
        out = self.requested()
        before = self.presented(out["request_ref"])
        restarted = self.fresh()
        self.ok(restarted.approve(**self.approval(before)))
        self.assertEqual(len(self.authorizations()), 1)

    def test_B4_restart_after_approval(self):
        out, _, decision = self.approved()
        status = self.ok(self.fresh().status(request_ref=out["request_ref"]))
        self.assertEqual(status["attested_approval"]["state"], "APPLIED")
        self.assertEqual(status["attested_approval"]["evidence_status"], EVIDENCE)
        self.assertEqual(status["run"]["phase"], "authorized_not_dispatched")

    def test_B4_restart_after_dispatch(self):
        ref, mission_id = self.dispatched()
        restarted = self.fresh()
        status = self.ok(restarted.status(request_ref=ref))
        self.assertEqual(status["run"]["phase"], "dispatched_not_yet_observed")
        self.assertEqual(status["dispatch"], "recorded")
        repeat = self.ok(self.run_cmd(ref, "dispatch", adapter=restarted,
                                  workspace_path=self.ws(mission_id)))
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(len(self.spawn.calls), 1)

    def test_B7_progress_and_result_are_read_from_durable_records(self):
        ref, mission_id = self.running()
        self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status="COMPLETE")
        self.prove_tests_pass(ref)
        self.ok(self.run_cmd(ref, "verify", reported_result=self.reported()))
        restarted = self.fresh()
        run = self.ok(restarted.status(request_ref=ref))["run"]
        self.assertEqual(run["phase"], "completed_verified")
        self.assertEqual(run["latest_observation"]["task_status"], "COMPLETE")
        result = self.ok(self.run_cmd(ref, "result", adapter=restarted))
        self.assertEqual(result["result_text"], RESULT_TEXT)


# ====================================================================
# B5. Unknown dispatch outcome reconciles before any retry
# ====================================================================


class B5ReconcileTests(Fixture):

    def test_B5_an_unknown_outcome_holds_and_only_reconcile_resolves_it(self):
        out, _, _ = self.approved()
        ref, mission_id = out["request_ref"], out["mission_id"]
        self.spawn.raises = "after_child"
        held = self.ok(self.run_cmd(ref, "dispatch"))
        self.assertTrue(held["hold"])
        self.assertEqual(self.ok(self.adapter.status(request_ref=ref))["run"][
            "phase"], "hold_intent_outcome_unknown")
        # A retry before reconciliation starts nothing: the existing intent
        # is answered as a duplicate and the HOLD stands.
        self.spawn.raises = None
        retry = self.ok(self.run_cmd(ref, "dispatch"))
        self.assertTrue(retry["duplicate"])
        self.assertTrue(retry["hold"])
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertTrue(self.ok(self.run_cmd(ref, "observe"))["hold"])
        reconciled = self.ok(self.run_cmd(ref, "reconcile"))
        self.assertEqual(reconciled["phase"], "dispatched_not_yet_observed")
        self.assertEqual(len(self.spawn.calls), 1)


# ====================================================================
# B6. Pause and cancel, including late writes
# ====================================================================


class B6PauseCancelTests(Fixture):

    def test_B6_pause_stops_DI_progression_until_resume(self):
        ref, _ = self.running()
        self.ok(self.run_cmd(ref, "pause"))
        run = self.ok(self.adapter.status(request_ref=ref))["run"]
        self.assertTrue(run["paused"])
        self.assertFalse(run["external_work_suspended"])
        self.refused("mission_bridge_paused",
                     self.run_cmd(ref, "verify", reported_result=self.reported()))
        self.ok(self.run_cmd(ref, "resume"))
        self.assertFalse(self.ok(self.adapter.status(request_ref=ref))["run"]["paused"])

    def test_B6_a_cancelled_run_refuses_late_writes(self):
        """Every Mission and Mission State record is unchanged by the late
        attempts. The one durable difference is Mission Core's own
        state-operation id RESERVATION, which ``MissionBridge.prove`` mints
        before Mission Core refuses the operation: an existing core
        behaviour, pinned here exactly, and no claim, evidence or progress."""
        ref, _ = self.running()
        self.ok(self.run_cmd(ref, "cancel"))
        before = json.loads(self.file_bytes("missions.json"))
        self.refused("mission_bridge_mission_core_refused", self.run_cmd(
            ref, "prove", operation="record_claim",
            arguments={"requirement_key": "tests_pass", "statement": "late"}))
        self.refused("mission_bridge_wrong_state",
                     self.run_cmd(ref, "verify", reported_result=self.reported()))
        self.refused("mission_bridge_wrong_state", self.run_cmd(ref, "observe"))
        after = json.loads(self.file_bytes("missions.json"))
        added = set(after["reservations"]) - set(before["reservations"])
        self.assertEqual(
            [after["reservations"][k]["kind"] for k in added],
            [mission_store.RESERVATION_KIND_STATE_OPERATION])
        for document in (before, after):
            del document["reservations"]
        self.assertEqual(after, before)
        run = self.ok(self.adapter.status(request_ref=ref))["run"]
        self.assertEqual(run["phase"], "cancelled")

    def test_B6_cancelling_a_pending_proposal_needs_its_capability(self):
        out = self.requested()
        ref = out["request_ref"]
        shown = self.presented(ref)
        self.refused("local_request_control_capability",
                     self.adapter.cancel(request_ref=ref,
                                         control_capability="lc-" + "0" * 64))
        self.ok(self.adapter.cancel(request_ref=ref,
                                    control_capability=out["control_capability"]))
        self.refused("local_request_cancelled",
                     self.adapter.approve(**self.approval(shown)))
        self.refused("local_request_cancelled", self.run_cmd(
            ref, "dispatch"))
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(self.spawn.calls, [])


# ====================================================================
# D1. A request reply lost in transit: the originating conversation can
#     still withdraw exactly its own pending proposal (task d9e17d)
# ====================================================================

CONV = "grok-conversation-private-7f3a"
OTHER_TEXT = ("Read the operator rules and propose a Mission to tidy the"
              " readiness probe documentation in Example/Repo.")


class D1LostResponseCancelTests(Fixture):
    """The ``request`` reply carrying the one-shot control capability is
    LOST (these tests never read it to cancel). The originating
    conversation still holds what it sent: the exact text and its
    ``conversation_ref``. That origin proof, and only it, lets the adapter
    use the recovery material it sealed under it."""

    def lost(self, text=PROSE, conversation_ref=CONV):
        """A request whose reply never reached the conversation. The reply
        is returned only so a test can assert the token never reappears."""
        return self.requested(text, conversation_ref=conversation_ref)

    def recover_cancel(self, ref, text=PROSE, conversation_ref=CONV, adapter=None):
        return (adapter or self.adapter).cancel(
            request_ref=ref, text=text, conversation_ref=conversation_ref)

    def pending_without_withdrawal(self, mission_id):
        mission = self.mission(mission_id)
        self.assertEqual(mission["state"], "AWAITING_DECISION")
        self.assertNotIn("withdrawal", mission)

    def capabilities(self):
        return request_store.LocalRequestStore(self.state).load()[
            "control_capabilities"]

    def test_D1_the_originating_conversation_cancels_after_a_lost_reply(self):
        lost = self.lost()
        self.assertEqual(lost["control_capability_recovery"],
                         adapter_module.RECOVERY_KEPT)
        # The conversation learns its request_ref again by repeating the
        # identical request: a duplicate, never a second proposal.
        again = self.ok(self.adapter.request(text=PROSE, conversation_ref=CONV))
        self.assertTrue(again["duplicate"])
        self.assertNotIn("control_capability", again)
        # A restarted process with no chat history and no Operator.
        never = NeverOperator()
        restarted = self.make_adapter(operator=never)
        cancelled = self.ok(self.recover_cancel(again["request_ref"],
                                                adapter=restarted))
        self.assertEqual(never.requests, [])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["cancelled_through"], "origin_recovery")
        self.assertFalse(cancelled["completed_interrupted_cancellation"])
        self.assertEqual(cancelled["mission_id"], lost["mission_id"])
        self.assertIsNotNone(self.mission(lost["mission_id"])["withdrawal"])
        status = self.ok(self.adapter.status(request_ref=lost["request_ref"]))
        self.assertEqual(status["surface_state"], "CANCELLED")
        self.assertEqual(status["local_cancellation"], "COMPLETE")
        self.refused("local_request_binding_mismatch",
                     self.adapter.present(request_ref=lost["request_ref"]))
        self.refused("local_request_cancelled",
                     self.recover_cancel(lost["request_ref"]))
        self.assertEqual(len(self.operator.requests), 1)

    def test_D1_a_proof_for_one_request_never_cancels_another(self):
        a = self.lost()
        b = self.lost(OTHER_TEXT, "grok-conversation-private-b")
        for label, result, problem in (
            ("A's proof against B", self.recover_cancel(b["request_ref"]),
             adapter_module.PROBLEM_ORIGIN_MISMATCH),
            ("B's text with another conversation_ref", self.recover_cancel(
                b["request_ref"], OTHER_TEXT, CONV),
             adapter_module.PROBLEM_ORIGIN_MISMATCH),
            ("B's text without a conversation_ref", self.recover_cancel(
                b["request_ref"], OTHER_TEXT, None),
             adapter_module.PROBLEM_ORIGIN_MISMATCH),
            ("only a request_ref", self.adapter.cancel(request_ref=b["request_ref"]),
             "local_request_control_capability"),
            ("only a Mission id", self.adapter.cancel(request_ref=b["mission_id"]),
             "local_request_unknown_request"),
            ("a Mission id with B's proof", self.recover_cancel(
                b["mission_id"], OTHER_TEXT, "grok-conversation-private-b"),
             adapter_module.PROBLEM_ORIGIN_MISMATCH),
            ("both proofs at once", self.adapter.cancel(
                request_ref=b["request_ref"], control_capability=b["control_capability"],
                text=OTHER_TEXT, conversation_ref="grok-conversation-private-b"),
             adapter_module.PROBLEM_BAD_REQUEST),
        ):
            with self.subTest(case=label):
                self.assert_labelled(problem, result)
        for out in (a, b):
            self.pending_without_withdrawal(out["mission_id"])
        self.assertTrue(all(c["consumed_at"] is None
                            for c in self.capabilities().values()))
        # Each conversation's own proof cancels exactly its own proposal.
        self.ok(self.recover_cancel(b["request_ref"], OTHER_TEXT,
                                    "grok-conversation-private-b"))
        self.assertIsNotNone(self.mission(b["mission_id"])["withdrawal"])
        self.pending_without_withdrawal(a["mission_id"])

    def test_D1_interrupted_local_persistence_is_refused_distinctly(self):
        # (a) The index never recorded the proposal: still IN_FLIGHT.
        class CrashingIndex(index_module.RequestIndex):
            def record_proposed(self, key, request_ref, now, recovery=None):
                raise OSError("synthetic: crash after the proposal")
        crashing = adapter_module.GrokBotAdapter(
            local_request_cli.build_surface(self.state, self.clock),
            self.session(self.operator), str(REPO_ROOT),
            CrashingIndex(self.state), self.clock)
        with self.assertRaises(OSError):
            crashing.request(text=PROSE, conversation_ref=CONV)
        [ref] = request_store.LocalRequestStore(self.state).load()["requests"]
        in_flight = self.assert_labelled(
            adapter_module.PROBLEM_RECOVERY_INTERRUPTED, self.recover_cancel(ref))
        self.assertEqual(in_flight["condition"], "in_flight")
        self.assertIn("recover", in_flight["reason"])
        # An IN_FLIGHT entry (no request_ref yet) never stands for a
        # missing reference: the surface's own refusal answers it.
        self.assert_labelled("local_request_unknown_request",
                             self.recover_cancel(None, OTHER_TEXT, "conv-y"))
        # (b) The surface holds a request this adapter never indexed.
        direct = self.adapter._surface.submit(run_request(objective="direct"))
        not_indexed = self.assert_labelled(
            adapter_module.PROBLEM_RECOVERY_INTERRUPTED,
            self.recover_cancel(direct["request_ref"], OTHER_TEXT, "conv-x"))
        self.assertEqual(not_indexed["condition"], "not_indexed")
        # (c) The surface's own interrupted cancel (local_cancellation
        # INCOMPLETE) on a request that holds no recovery material.
        bare = self.lost(OTHER_TEXT, None)
        self.assertEqual(bare["control_capability_recovery"],
                         adapter_module.RECOVERY_NOT_KEPT)
        self.withdraw_in_core_only(bare)
        incomplete = self.assert_labelled(
            adapter_module.PROBLEM_RECOVERY_INTERRUPTED,
            self.recover_cancel(bare["request_ref"], OTHER_TEXT, None))
        self.assertEqual(incomplete["condition"], "local_cancellation_incomplete")
        self.assertIn("same control capability", incomplete["reason"])
        for mission_id in (direct["mission_id"],):
            self.pending_without_withdrawal(mission_id)
        self.assertEqual(self.ok(self.adapter.status(request_ref=ref))[
            "surface_state"], "OPEN")

    def withdraw_in_core_only(self, out):
        """SYNTHETIC interrupted cancel: Mission Core records the withdrawal,
        the surface's local save never lands (local_cancellation INCOMPLETE)."""
        entry = request_store.LocalRequestStore(self.state).load()["requests"][
            out["request_ref"]]
        self.missions().withdraw_proposal(
            out["mission_id"], entry["mission_request_id"],
            out["control_capability"],
            adapter_module.surface_module.LOCAL_CALLER_CONTEXT)
        self.assertEqual(self.ok(self.adapter.status(request_ref=out[
            "request_ref"]))["local_cancellation"], "INCOMPLETE")

    def test_D1_recovery_completes_its_own_interrupted_cancellation(self):
        out = self.lost()
        self.withdraw_in_core_only(out)
        done = self.ok(self.recover_cancel(out["request_ref"]))
        self.assertTrue(done["completed_interrupted_cancellation"])
        self.assertEqual(self.ok(self.adapter.status(request_ref=out[
            "request_ref"]))["local_cancellation"], "COMPLETE")

    def test_D1_a_legacy_record_is_refused_honestly_and_nothing_is_fabricated(self):
        out = self.lost()
        # The index exactly as a build before this change wrote it: the
        # entry carries no recovery material (the live record's shape).
        path = os.path.join(self.state, index_module.INDEX_FILE_NAME)
        with open(path) as handle:
            document = json.load(handle)
        [entry] = document["requests"].values()
        del entry["recovery"]
        with open(path, "w") as handle:
            json.dump(document, handle)
        self.assertEqual(sorted(entry), ["recorded_at", "request_ref", "state"])
        before = self.capabilities()
        legacy = self.assert_labelled(adapter_module.PROBLEM_RECOVERY_UNAVAILABLE,
                                      self.recover_cancel(out["request_ref"]))
        self.assertIn("cannot be cancelled through this transport",
                      legacy["reason"])
        self.assertEqual(legacy["mission_state"], "AWAITING_DECISION")
        self.pending_without_withdrawal(out["mission_id"])
        self.assertEqual(self.capabilities(), before)
        # A request made without a conversation_ref kept nothing either.
        bare = self.lost(OTHER_TEXT, None)
        refused = self.assert_labelled(
            adapter_module.PROBLEM_RECOVERY_UNAVAILABLE,
            self.recover_cancel(bare["request_ref"], OTHER_TEXT, None))
        self.assertIn("conversation_ref", refused["reason"])
        self.pending_without_withdrawal(bare["mission_id"])

    def test_D1_an_unavailable_refusal_states_the_missions_actual_state(self):
        """Round-1 review: a request with no recovery material whose Mission
        was APPROVED must not be described as a pending proposal."""
        bare = self.lost(OTHER_TEXT, None)
        shown = self.presented(bare["request_ref"])
        self.ok(self.adapter.approve(**self.approval(shown)))
        self.assertEqual(self.mission(bare["mission_id"])["state"], "AUTHORIZED")
        refused = self.assert_labelled(
            adapter_module.PROBLEM_RECOVERY_UNAVAILABLE,
            self.recover_cancel(bare["request_ref"], OTHER_TEXT, None))
        self.assertEqual(refused["mission_state"], "AUTHORIZED")
        self.assertIn("AUTHORIZED", refused["reason"])
        for claim in ("AWAITING_DECISION", "can still be presented",
                      "pending proposal"):
            self.assertNotIn(claim, refused["reason"])
        self.assertEqual(self.mission(bare["mission_id"])["state"], "AUTHORIZED")

    def test_D1_the_privacy_statements_distinguish_text_from_conversation_ref(self):
        """Round-1 review: the request text reaches the Operator and may be
        displayed; only the conversation_ref is never displayed, returned or
        stored outside the digest, and the protection rests on it."""
        docs = (REPO_ROOT / "docs" / "grok-bot.md").read_text(encoding="utf-8")
        for name, text in (("docs", docs), ("index", index_module.__doc__)):
            with self.subTest(source=name):
                flat = " ".join(text.split())
                for stale in ("DI never stores, displays or returns either value",
                              "which DI never stores, displays or returns;"):
                    self.assertNotIn(stale, flat)
                for phrase in ("may appear verbatim", "private and unguessable"):
                    self.assertIn(phrase, flat)
                # Round-3 ruling: no protection is claimed for a weak
                # reference. The sealed file is only as safe as the proof.
                self.assertIn("only as safe as", flat)
                self.assertNotIn("holds no usable capability.", flat)
                self.assertNotIn("holds no usable capability:", flat)
        # Round-2 review: the docs name the kind of value too, as the client
        # contract does (tests/test_grok_bot_mcp.py D1ClientContractTests).
        flat_docs = " ".join(docs.split())
        for phrase in ("random", "never a visible or sequential"):
            self.assertIn(phrase, flat_docs)

    def test_D1_mission_authorization_is_not_weakened(self):
        out = self.lost()
        shown = self.presented(out["request_ref"])
        self.ok(self.adapter.approve(**self.approval(shown)))
        before = self.authorizations()
        self.assert_labelled("local_request_control_out_of_scope",
                             self.recover_cancel(out["request_ref"]))
        self.assertEqual(self.mission(out["mission_id"])["state"], "AUTHORIZED")
        self.assertEqual(self.authorizations(), before)
        self.assertTrue(all(c["consumed_at"] is None
                            for c in self.capabilities().values()))

    def test_D1_the_index_holds_no_usable_capability(self):
        out = self.lost()
        token = out["control_capability"]
        stored = self.file_bytes(index_module.INDEX_FILE_NAME).decode("ascii")
        self.assertNotIn(token[len(request_store.TOKEN_PREFIX):], stored)
        [entry] = json.loads(stored)["requests"].values()
        recovery = entry["recovery"]
        self.assertEqual(recovery["seal"], index_module.SEAL_SCHEME)
        origin = {"text": PROSE, "conversation_ref": CONV}
        self.assertEqual(index_module.unseal_capability(
            origin, out["request_ref"], recovery), token)
        for wrong_origin, ref in (
            ({"text": PROSE, "conversation_ref": "other"}, out["request_ref"]),
            ({"text": PROSE + " ", "conversation_ref": CONV}, out["request_ref"]),
            (origin, "lr-" + "0" * 32),
        ):
            with self.subTest(origin=wrong_origin, ref=ref):
                self.assertNotEqual(index_module.unseal_capability(
                    wrong_origin, ref, recovery), token)
        # The seal key is not derivable from the index key the file stores.
        self.assertNotEqual(index_module.seal_key(origin).hex(),
                            adapter_module.json_digest(origin))
        self.assertEqual(os.stat(os.path.join(
            self.state, index_module.INDEX_FILE_NAME)).st_mode & 0o077, 0)

    def test_D1_the_index_schema_stays_closed_and_backward_compatible(self):
        index = index_module.RequestIndex(self.state)
        legacy = {"schema_version": 1, "presentations": {}, "requests": {
            "a" * 64: {"state": "PROPOSED", "request_ref": "lr-" + "1" * 32,
                       "recorded_at": NOW}}}
        self.assertEqual(index_module._validate(legacy, "legacy"), legacy)
        sealed = {"seal": index_module.SEAL_SCHEME, "sealed_capability": "b" * 64}
        for label, entry in (
            ("unknown scheme", {"state": "PROPOSED", "request_ref": "lr-" + "1" * 32,
                                "recorded_at": NOW,
                                "recovery": dict(sealed, seal="plain")}),
            ("not hex", {"state": "PROPOSED", "request_ref": "lr-" + "1" * 32,
                         "recorded_at": NOW,
                         "recovery": dict(sealed, sealed_capability="Z" * 64)}),
            ("extra key", {"state": "PROPOSED", "request_ref": "lr-" + "1" * 32,
                           "recorded_at": NOW, "recovery": dict(sealed, token="x")}),
            ("on an IN_FLIGHT entry", {"state": "IN_FLIGHT", "request_ref": None,
                                       "recorded_at": NOW, "recovery": sealed}),
            ("a raw capability", {"state": "PROPOSED", "request_ref": "lr-" + "1" * 32,
                                  "recorded_at": NOW,
                                  "control_capability": "lc-" + "c" * 64}),
        ):
            with self.subTest(case=label):
                with self.assertRaises(index_module.RequestIndexError):
                    index_module._validate(dict(legacy, requests={"a" * 64: entry}),
                                           label)
        self.assertEqual(index.load()["requests"], {})


# ====================================================================
# A3 / provenance: the adapter grants nothing and labels honestly
# ====================================================================


class AuthorityAndProvenanceTests(Fixture):

    def test_every_result_carries_no_delivery_authority_and_the_relay_label(self):
        results = []
        out = self.requested()
        results.append(out)
        shown = self.presented(out["request_ref"])
        results += [shown, self.adapter.approve(**self.approval(shown, revision=9)),
                    self.adapter.approve(**self.approval(shown)),
                    self.adapter.status(request_ref=out["request_ref"]),
                    self.adapter.recover(request_ref=out["request_ref"]),
                    self.run_cmd(out["request_ref"], "deploy"),
                    self.adapter.call("release", {}),
                    self.adapter.request(text="")]
        self.operator.reply = "Which one?"
        results.append(self.adapter.request(text="something else"))
        for result in results:
            with self.subTest(status=result.get("status"),
                              problem=result.get("problem")):
                self.assertEqual(result["delivery_authority"], "none")
                self.assertEqual(result["evidence_status"], EVIDENCE)
                self.assertEqual(result["transport"], "grok_bot")

    def test_the_tool_surface_has_no_merge_release_deploy_or_performing_tool(self):
        """Slice 3 adds exactly the SEPARATE delivery ceremony's three tools
        (present, relay the approval, read status: pr_delivery's own
        present-dots and attest-dots). Still no tool merges, releases,
        deploys, or performs a delivery step."""
        self.assertEqual(sorted(adapter_module.TOOLS), [
            "approve", "approve_delivery", "cancel", "delivery_status",
            "present", "present_delivery", "recover", "request", "run",
            "status"])
        for tool in ("merge", "release", "deploy", "deliver", "push", "commit",
                     "pr_create", "advance", "advance_delivery", "authorize"):
            with self.subTest(tool=tool):
                self.refused("grok_bot_unknown_tool", self.adapter.call(tool, {}))
        out = self.requested()
        for command in ("merge", "release", "deploy", "push", "deliver"):
            with self.subTest(command=command):
                self.refused("local_request_bad_request",
                             self.run_cmd(out["request_ref"], command))

    def test_engineering_approval_confers_no_delivery(self):
        ref, _ = self.running()
        status = self.ok(self.adapter.status(request_ref=ref))
        self.assertEqual(status["run"]["delivery"]["receipts"], [])
        self.assertFalse(status["run"]["delivery"]["delivered"])
        self.assertEqual(status["run"]["delivery_authority"], "none")

    def test_the_operator_preamble_names_the_run_result_requirement(self):
        self.assertEqual(framing.RUN_RESULT_REQUIREMENT_KEY,
                         mission_record.RUN_RESULT_REQUIREMENT_KEY)
        self.assertIn(repr(mission_record.RUN_RESULT_REQUIREMENT_KEY),
                      framing.OPERATOR_PREAMBLE)


# ====================================================================
# B9 / A1 / A4. Startup, name and registration
# ====================================================================


class StartupAndRegistrationTests(Bounded):

    def test_B9_entry_script_answers_help_with_grok_retired_and_no_dots(self):
        result = retirement.run_startup_probe(ENTRY_SCRIPT)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        text = result.stdout
        self.assertIn("usage", text.lower())
        self.assertIn("operator-attested", text)
        self.assertIn("not cryptographically authenticated", text)
        self.assertIn("instruction-based, not mechanically enforced", text)
        self.assertNotIn(retirement.REFUSAL_MARK, result.stderr)

    def test_B9_importing_the_adapter_loads_no_dots_engine_or_delivery(self):
        result = loaded_roots_probe("import grok_bot.cli, grok_bot.adapter")
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = set(json.loads(result.stdout))
        self.assertIn("grok_bot", loaded)
        self.assertEqual(sorted(loaded & set(STARTUP_FORBIDDEN_ROOTS)), [])
        self.assertEqual(sorted(n for n in loaded if "dots" in n.lower()), [])

    def test_B11_the_loaded_roots_probe_detects_a_planted_import(self):
        result = loaded_roots_probe("import grok_bot.cli\nimport target_runtime")
        self.assertIn("target_runtime", json.loads(result.stdout))

    def test_A1_the_package_name_is_not_retired(self):
        for name in ("grok_bot", "grokbot"):
            self.assertNotIn(name, retirement.RETIRED_MODULES)
        self.assertEqual(retirement.retired_surface_problems(REPO_ROOT), [])

    def test_A4_installer_compile_lists_and_boundary_registration(self):
        install = (REPO_ROOT / "scripts" / "install.sh").read_text()
        self.assertIn("grokbot.py", retirement.installed_entries(install))
        for name in ("CONTRIBUTING.md", ".github/workflows/ci.yml"):
            text = (REPO_ROOT / name).read_text()
            self.assertIn("grok_bot/*.py", text, name)
            self.assertIn("grokbot.py", text, name)
        self.assertIn("grok_bot", test_human_interaction.FORBIDDEN_ROOTS)


STARTUP_FORBIDDEN_ROOTS = (
    "target_runtime", "herdr", "herdctl", "pr_delivery", "telegram_operator",
    "human_interaction", "coordination", "grok_mcp", "grokmcp",
)
LOADED_ROOTS_PROBE = (
    "import json, sys\n%s\n"
    "print(json.dumps(sorted({n.split('.')[0] for n in sys.modules})))\n")


def loaded_roots_probe(imports):
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    return subprocess.run(
        [sys.executable, "-c", LOADED_ROOTS_PROBE % imports],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=CHILD_TIMEOUT_SECONDS)


# ====================================================================
# Static detectors, each with a planted-probe self-check (B11)
# ====================================================================

ALLOWED_IMPORTS = frozenset({
    "argparse", "copy", "json", "os", "stat", "sys", "time",
    "local_request", "local_request.cli", "local_request.store",
    "local_request.surface", "operator_session", "codex_gateway.contract",
    "workflow_authority.atomic", "workflow_authority.digest",
    "grok_bot", "grok_bot.adapter", "grok_bot.cli", "grok_bot.framing",
    "grok_bot.index",
})
FORBIDDEN_NAMES = frozenset({"environ", "getenv", "putenv", "__import__",
                             "import_module", "eval", "exec"})


# Slice 2: the loopback listener's own modules, allowed in grok_bot/server.py
# ALONE (no outbound client module is among them).
SERVER_ONLY_IMPORTS = frozenset({
    "hmac", "http.server", "ipaddress", "socket", "socketserver",
    "urllib.parse",
})


# Slice 3: the ONE module allowed to import pr_delivery (one statement,
# pinned in tests/test_static.py), and only it.
DELIVERY_ONLY_IMPORTS = frozenset({"io", "math", "types", "pr_delivery"})


# Task d9e17d: the index seals cancel-recovery material (pure computation;
# neither module opens anything), and only the index.
INDEX_ONLY_IMPORTS = frozenset({"hashlib", "hmac"})


def allowed_imports_for(name):
    extra = {"server.py": SERVER_ONLY_IMPORTS,
             "delivery.py": DELIVERY_ONLY_IMPORTS,
             "index.py": INDEX_ONLY_IMPORTS}.get(name, frozenset())
    return ALLOWED_IMPORTS | extra


def import_violations(source, allowed=ALLOWED_IMPORTS):
    """Imported module names outside the allowed set, plus any use of the
    environment or dynamic import/evaluation."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name not in allowed]
        elif isinstance(node, ast.ImportFrom):
            if node.module not in allowed and any(
                "%s.%s" % (node.module, a.name) not in allowed
                for a in node.names
            ):
                found.append(node.module)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(node.attr)
    return found


IDENTITY_WORDS = frozenset({"authenticated", "authentication", "verified",
                            "signed", "signature", "proven"})
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AFFIRMATIVE_CLAIM_RE = re.compile(
    r"\b(authenticated|verified|signed|proven)\s+(human|sender|identity|"
    r"approver|user|reply|approval)\b", re.IGNORECASE)
NEGATION_RE = re.compile(r"\b(not|no|never|nor|without|cannot)\b", re.IGNORECASE)


def identity_claims(source):
    """Identifiers and field-name literals naming an identity proof, and
    prose that affirms one (a claim is allowed only when negated within
    the preceding 40 characters)."""
    found = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME or (
            token.type == tokenize.STRING
            and IDENTIFIER_RE.match(token.string.strip("'\""))
        ):
            words = set(re.findall(r"[a-z]+", token.string.lower()))
            if words & IDENTITY_WORDS:
                found.append(token.string)
        if token.type == tokenize.STRING:
            for match in AFFIRMATIVE_CLAIM_RE.finditer(token.string):
                window = token.string[max(0, match.start() - 40):match.start()]
                if not NEGATION_RE.search(window):
                    found.append(match.group(0))
    return found


class StaticDetectorTests(unittest.TestCase):

    def test_A2_imports_are_the_seams_only(self):
        for path in product_sources():
            with self.subTest(path=path.name):
                self.assertEqual(import_violations(
                    path.read_text(), allowed_imports_for(path.name)), [])

    def test_B11_import_detector_fires_on_planted_probes(self):
        planted = ("import socket\nfrom mission import record\n"
                   "import target_runtime\nfrom pr_delivery import cli\n"
                   "x = os.environ\nimport json\n")
        self.assertEqual(import_violations(planted), [
            "socket", "mission", "target_runtime", "pr_delivery", "environ"])
        # The listener's allowance is server.py's alone, and it never
        # admits an outbound client.
        listener = "import http.server\nimport socketserver\n"
        self.assertEqual(import_violations(listener, allowed_imports_for(
            "adapter.py")), ["http.server", "socketserver"])
        self.assertEqual(import_violations(listener, allowed_imports_for(
            "server.py")), [])
        outbound = "import http.client\nimport urllib.request\nimport ssl\n"
        self.assertEqual(import_violations(outbound, allowed_imports_for(
            "server.py")), ["http.client", "urllib.request", "ssl"])
        # pr_delivery is delivery.py's alone.
        delivery = "from pr_delivery import cli\n"
        self.assertEqual(import_violations(delivery, allowed_imports_for(
            "adapter.py")), ["pr_delivery"])
        self.assertEqual(import_violations(delivery, allowed_imports_for(
            "delivery.py")), [])
        # The seal's hashing is index.py's alone.
        sealing = "import hashlib\nimport hmac\n"
        self.assertEqual(import_violations(sealing, allowed_imports_for(
            "adapter.py")), ["hashlib", "hmac"])
        self.assertEqual(import_violations(sealing, allowed_imports_for(
            "index.py")), [])

    def test_provenance_vocabulary_never_claims_identity(self):
        for path in product_sources():
            with self.subTest(path=path.name):
                self.assertEqual(identity_claims(path.read_text()), [])

    def test_B11_identity_detector_fires_on_planted_probes(self):
        self.assertEqual(identity_claims("authenticated_sender = 1\n"),
                         ["authenticated_sender"])
        self.assertEqual(identity_claims("x = {'sender_verified': 1}\n"),
                         ["'sender_verified'"])
        self.assertEqual(identity_claims('x = "it is a verified human"\n'),
                         ["verified human"])
        self.assertEqual(identity_claims(
            '"""It is not a cryptographically authenticated human identity."""\n'),
            [])


# ====================================================================
# Mutation self-checks: the key tests fail when the property breaks
# ====================================================================


def _substitute_durable_binding(self, **binding):
    """MUTANT: reads the pending proposal and fills the caller's binding."""
    shown = self._surface.present(binding.get("request_ref"))
    filled = dict(binding, mission_id=shown["mission_id"],
                  revision=shown["revision"],
                  proposal_digest_sha256=shown["proposal_digest_sha256"])
    return self._surface.attest_approval(
        **dict((n, filled.get(n)) for n in adapter_module.APPROVAL_FIELDS))


def _claim_delivery_authority(result):
    """MUTANT: labels a result as carrying delivery authority."""
    return dict(result, delivery_authority="granted",
                evidence_status=EVIDENCE, transport="grok_bot")


MUTANTS = (
    ("approve fills the binding from durable state",
     adapter_module.GrokBotAdapter, "_approve", _substitute_durable_binding,
     ("B3ApprovalBindingTests.test_B3_the_binding_is_relayed_unmodified_and"
      "_no_proposal_is_read",
      "B3ApprovalBindingTests.test_B3_a_wrong_binding_is_never_corrected"
      "_from_durable_state",
      "B3ApprovalBindingTests.test_B3_a_stale_revision_is_refused")),
    ("results claim delivery authority",
     adapter_module, "_label", _claim_delivery_authority,
     ("AuthorityAndProvenanceTests.test_every_result_carries_no_delivery"
      "_authority_and_the_relay_label",)),
    ("user text is not neutralized",
     framing, "neutralize", lambda text: (text, False),
     ("B1PlainTextPathTests.test_B1_a_forged_envelope_in_user_text_is"
      "_neutralized",)),
    ("the index never deduplicates",
     index_module.RequestIndex, "begin", lambda self, key, now: (None, None),
     ("B2DuplicateTests.test_B2_a_duplicate_request_returns_the_first"
      "_proposal",
      "B2DuplicateTests.test_B2_an_unknown_outcome_is_held_never_proposed"
      "_twice")),
    ("the display omits the proof contract",
     adapter_module, "rendered_lines",
     lambda prefix, value: leaf_lines(prefix, dict(
         (k, v) for k, v in value.items() if k != "proof_contract")),
     ("B1PlainTextPathTests.test_S1_display_shows_every_proposal_and_proof"
      "_contract_field",)),
    ("the displayed binding is not compared",
     adapter_module, "displayed_mismatch", lambda displayed, caller: [],
     ("B3ApprovalBindingTests.test_S2_an_unexpired_different_expiry_is"
      "_refused",
      "B3ApprovalBindingTests.test_S2_an_altered_action_scope_or_targets_is"
      "_refused")),
    ("check and application are not serialized",
     index_module.RequestIndex, "serialized",
     lambda self: contextlib.nullcontext(),
     ("R1SerializationTests.test_R1_no_presentation_lands_between_check_and"
      "_application",)),
    ("line boundaries are not escaped",
     adapter_module, "one_line", lambda text: text,
     ("B1PlainTextPathTests.test_S1_the_display_is_never_truncated_nor"
      "_spoofable_by_a_field",
      "B1PlainTextPathTests.test_R2_every_unicode_line_boundary_is_escaped"
      "_and_preserved")),
    ("lone surrogates are not escaped",
     adapter_module, "_escaped",
     lambda char: char in adapter_module.LINE_BOUNDARIES,
     ("V2LoneSurrogateTests.test_V2_an_accepted_lone_surrogate_proposal_is"
      "_presented_and_approved",
      "V2LoneSurrogateTests.test_V2_every_lone_surrogate_round_trips"
      "_exactly")),
    ("the nesting bound is disabled",
     framing, "nesting_exceeds", lambda value, limit: False,
     ("V1NestingTests.test_V1_a_deeply_nested_binding_value_is_a_labelled"
      "_refusal",
      "V1NestingTests.test_V1_a_deeply_nested_operator_envelope_proposes"
      "_nothing")),
)


class MutationSelfCheckTests(unittest.TestCase):
    """Each mutant is patched IN MEMORY (``mock.patch.object`` restores the
    original on exit; nothing on disk changes) and the tests that guard
    that property must all fail under it, then pass again once restored.
    Every inner test carries its own SIGALRM watchdog; the loop is finite."""

    def run_named(self, names):
        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromName(name, sys.modules[__name__])
            for name in names)
        result = unittest.TestResult()
        suite.run(result)
        return result

    def test_B11_every_mutant_is_caught_and_the_original_passes(self):
        from unittest import mock
        for label, owner, name, mutant, names in MUTANTS:
            with self.subTest(mutant=label):
                with mock.patch.object(owner, name, mutant):
                    broken = self.run_named(names)
                # A subTest failure is reported once per subtest; count the
                # distinct guarding tests that failed.
                failed = set(getattr(test, "test_case", test).id()
                             for test, _ in broken.failures + broken.errors)
                self.assertEqual(len(failed), len(names), (label, failed))
                restored = self.run_named(names)
                self.assertTrue(restored.wasSuccessful(),
                                (label, restored.failures, restored.errors))


# ====================================================================
# CLI: JSON in, JSON out, injected seams only
# ====================================================================


class CliTests(Fixture):

    def cli(self, argv, arguments=None, operator=True):
        out = io.StringIO()
        code = cli_module.main(
            ["--state-dir", self.state, "--repository", str(REPO_ROOT),
             "--control-repo", "/control-repo"] + argv,
            io.StringIO(json.dumps(arguments if arguments is not None else {})),
            out, clock=self.clock, bridge_factory=self.factory,
            operator_session=(self.session(self.operator) if operator else None))
        return code, json.loads(out.getvalue())

    def test_cli_drives_request_present_approve_and_dispatch(self):
        code, out = self.cli(["call", "request"], {"text": PROSE})
        self.assertEqual(code, 0, out)
        code, shown = self.cli(["call", "present"], {"request_ref": out["request_ref"]})
        self.assertEqual(code, 0, shown)
        code, approved = self.cli(["call", "approve"], self.approval(shown))
        self.assertEqual((code, approved["status"]),
                         (0, "approved_by_operator_attestation"))
        code, refused = self.cli(["call", "approve"],
                                 self.approval(shown, relayed_reply="no"))
        self.assertEqual((code, refused["problem"]),
                         (3, "local_request_reply_not_affirmative"))
        code, ran = self.cli(["call", "run"], {
            "request_ref": out["request_ref"], "command": "dispatch",
            "arguments": {}})
        self.assertEqual((code, ran["phase"]), (0, "dispatched_not_yet_observed"))
        self.assertEqual(len(self.spawn.calls), 1)

    def test_cli_refuses_usage_errors_and_credential_flags(self):
        out = io.StringIO()
        self.assertEqual(cli_module.main(
            ["--state-dir", "relative", "call", "status"], io.StringIO("{}"), out),
            cli_module.EXIT_USAGE)
        self.assertEqual(json.loads(out.getvalue())["delivery_authority"], "none")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            for flag in ("--token", "--api-key", "--principal"):
                with self.subTest(flag=flag):
                    self.assertEqual(cli_module.main(
                        ["--state-dir", self.state, "call", "status", flag, "x"],
                        io.StringIO("{}"), io.StringIO()), cli_module.EXIT_USAGE)
            self.assertEqual(cli_module.main(
                ["--state-dir", self.state, "call", "deploy"], io.StringIO("{}"),
                io.StringIO()), cli_module.EXIT_USAGE)
        self.assertIn("unrecognized arguments: --token x", errors.getvalue())
        self.assertIn("invalid choice: 'deploy'", errors.getvalue())
        code, result = self.cli(["call", "status"], ["not", "an", "object"])
        self.assertEqual((code, result["problem"]), (3, "grok_bot_bad_request"))


if __name__ == "__main__":
    unittest.main()
