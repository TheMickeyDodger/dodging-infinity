"""The DI-REMOTE-2 acceptance narrative, end to end (ruling N-1).

From simulated **Approve Mission** to dispatch with NO manual step in
between: the one human action in this file is the single Telegram
Approve callback. Everything after it happens inside
``runtime_module.process_once`` — the exact function the installed
``dirun`` service loops on — driven here the way the LaunchAgent
drives it. The test asserts that the automation chain fires, that the
control repository is byte-untouched (tree-hashed before and after,
``.git`` internals included), and that no delivery action occurs
anywhere (the target fixture's repository is byte-untouched too, the
transport records only read/materialize verbs — no delivery or
mutation verb, asserted over the full argv log — and the spawn
surface is exactly the three supervisor-first fields).

Hermetic (Ruling 2): injected Telegram API, injected gateway,
fixture-backed git transport, fake role turn, recorded spawn — no
network, no Telegram, no Codex, no child engineering process. The
REAL bridge and the REAL spawn are exercised elsewhere at the
injected control-plane boundary; the first live dispatch remains a
human-supervised action (disclosed in every increment report).

DocsAccuracyPinTests pins the shipped documentation to what actually
shipped — including the A0 telemetry limitation VERBATIM and the
v1-state migration break — so a doc edit that silently drops or
paraphrases a required disclosure fails the suite.
"""

import inspect
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import unittest

from telegram_operator import adapter as adapter_module
from telegram_operator import mission
from workflow_authority import record as wa_record
from workflow_authority import store as wa_store
from workflow_authority.digest import control_policy_digest

from target_runtime import broker as broker_module
from target_runtime import dispatch as dispatch_module
from target_runtime import evidence as evidence_module
from target_runtime import git_transport as git_transport_module
from target_runtime import runtime as runtime_module

from test_mission import (
    MissionHarness,
    ROUTING_SIGNAL_ENVELOPE,
    mission_document,
    mission_envelope,
    planning_result,
)
from test_target_runtime import (
    FakeGitTransport,
    FakeRoleTurn,
    FakeRoleTurnResult,
    make_git_repo,
    tree_hash,
)
from test_telegram_operator import (
    NOW,
    FakeGatewayResult,
    cb_update,
    msg_update,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The EXACT driving sentence — byte-for-byte the input the human
# sends. Note: unitaryfoundation, issue 2802 (NOT unitaryfund/702).
EXACT_SENTENCE = (
    "I want to solve"
    " https://github.com/unitaryfoundation/mitiq/issues/2802."
    " Go do it."
)
MITIQ_URL = "https://github.com/unitaryfoundation/mitiq"
MITIQ_OWNER = "unitaryfoundation"
MITIQ_ISSUE = 2802

# The bounded handoff the control chain dispatches: objective +
# constraints + rules, and NO technical solution — the corrective
# brief, never an engineering plan. (The engineering plan is the FAKE
# TARGET SUPERVISOR's job; see TARGET_SUPERVISOR_STRATEGY.)
MITIQ_HANDOFF = (
    "Resolve issue #2802 in mitiq: investigate the actual cause,"
    " preserve the repository contribution rules, add the necessary"
    " verification, and prepare the result for review."
)

# The FIRST strategy-bearing artifact in the whole run — produced by
# the fake target Supervisor, target-side ONLY. If this string ever
# appears in a control-chain artifact (the mission, the handoff, a
# control-chain prompt/context, or the control repo), the control
# chain has authored engineering, which it must never do (H4).
TARGET_SUPERVISOR_STRATEGY = (
    "TARGET-SUPERVISOR STRATEGY: reproduce the #2802 density-matrix"
    " mismatch, bisect mitiq/interface, add a regression test under"
    " mitiq/tests/, then run the suite to green."
)
# The target herd's Lead / Executor / Reviewer evidence — target-side
# artifacts, the source of the verified result the human finally sees.
TARGET_LEAD_EVIDENCE = "LEAD: decomposed #2802 into reproduce+fix+verify."
TARGET_EXEC_EVIDENCE = "EXECUTOR: patched mitiq/interface; added a test."
TARGET_REVIEWER_SUMMARY = (
    "REVIEWER: #2802 resolved — regression test added and the full"
    " mitiq suite passes; acceptance criteria met."
)


def read_doc(name):
    with open(os.path.join(REPO_ROOT, name), encoding="utf-8") as f:
        return f.read()


class MitiqNarrativeTests(unittest.TestCase):
    """The DI-REMOTE-2 capstone: ONE hermetic narrative from the EXACT
    driving sentence to the Telegram verified result.

    Built ONCE in setUpClass (a fresh restrictive planning turn that
    PRODUCES the Mission Authorization; one human Approve; then the
    installed `dirun run` loop, exercised as process_once, carrying the
    workflow the whole way with NO manual step) and then asserted point
    by point — one NAMED test per acceptance point, so a point that
    silently stops being exercised FAILS rather than passing as theatre.

    Hermetic (Ruling 2): injected Telegram API, injected gateway,
    fixture git transport, fake control-chain role turns, recorded
    spawn. The target engineering is a FAKE TARGET HERD fixture whose
    Supervisor produces the first strategy; the observation is the REAL
    `herdr.observe` over the leased workspace, so completion is read the
    way production reads it. No network, Telegram, GitHub, Codex, or
    child-Herdr process runs. The first LIVE dispatch stays a human
    step: nothing here runs the target's real work (the spawn is
    recorded only), and no code path makes it automatic.
    """

    MAX_RUNTIME_PASSES = 8

    # ---- one narrative, built once ------------------------------------
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        base = cls._tmp.name
        cls.control = os.path.realpath(os.path.join(base, "control"))
        # Since I3 the control fixture carries the protected surfaces
        # (herdr/, herdctl.py, roles/): dispatch stamps the
        # protected-surface baseline receipt over them and refuses
        # fail-closed when a root is missing.
        make_git_repo(cls.control, {
            "AGENTS.md": "control agents contract\n",
            "OPERATOR_PROTOCOL.md": "control operator protocol\n",
            "herdctl.py": "print('control cli stub')\n",
            os.path.join("herdr", "core.py"): "VALUE = 1\n",
            os.path.join("roles", "executor.md"): "executor role\n",
        })
        # The target fixture standing in for unitaryfoundation/mitiq,
        # carrying the REAL contribution rules the handoff-validation
        # turn must be shown.
        cls.target_fixture = os.path.join(base, "mitiq-fixture")
        cls.contributing = (
            "mitiq contribution rules: sign the CLA, add tests,"
            " keep public APIs stable.\n"
        )
        cls.baseline = make_git_repo(cls.target_fixture, {
            "README.md": "mitiq readme\n",
            "CONTRIBUTING.md": cls.contributing,
        })
        cls.state_dir = os.path.join(base, "state")
        os.makedirs(cls.state_dir)
        cls.workspaces = os.path.join(base, "workspaces")
        cls.control_before = tree_hash(cls.control)
        cls.target_before = tree_hash(cls.target_fixture)

        # --- Phone side: the EXACT sentence drives a fresh planning
        # turn that produces the mission; the LEGACY gateway turn
        # returns only the routing signal (no authority). ---
        cls.harness = MissionHarness(cls.state_dir, repository=cls.control)
        cls.harness.gateway_script.append(
            FakeGatewayResult(None, message=ROUTING_SIGNAL_ENVELOPE)
        )
        cls.harness.planning_script.append(
            planning_result(mission_envelope(cls._mitiq_document()))
        )
        cls.harness.adapter.process_update(msg_update(1, EXACT_SENTENCE))
        cls.harness.drain_worker()
        cls.mission_offer_text = cls.harness.mission_sends()[-1]["text"]
        cls.bound = cls.harness.bound_message_id()
        cls.offered_record = cls._fresh_entry(cls)

        # Snapshot of everything the CONTROL CHAIN produced/showed
        # BEFORE any target work — for the "first strategy-bearing
        # artifact" proof.
        cls.claimable_before_approval = list(
            runtime_module.claimable_workflows(cls.state_dir)
        )

        # --- The ONE human action: Approve. ---
        cls.harness.adapter.process_update(
            cb_update(10, "A:wf-0001", message_id=cls.bound)
        )
        cls.authorized_record = cls._fresh_entry(cls)
        # A SECOND approve attempt (a replay) — recorded to prove
        # exactly-one consumption.
        cls.second_approve_answers_before = len(cls.harness.answers())
        cls.harness.adapter.process_update(
            cb_update(11, "A:wf-0001", message_id=cls.bound)
        )
        cls.after_second_approve = cls._fresh_entry(cls)

        # --- The automation, driven exactly as `dirun run` drives it.
        broker, transport, role_turn, spawn_requests = (
            cls._build_runtime_broker()
        )
        cls.transport = transport
        cls.role_turn = role_turn
        cls.spawn_requests = spawn_requests

        # PASS 1: materialize -> prepare -> validate -> dispatch ->
        # verify (target has NOT started; it waits at DISPATCHED).
        cls.pass1 = runtime_module.process_once(broker)
        cls.after_pass1 = cls._fresh_entry(cls)
        cls.lease_path = spawn_requests[0][1]["target_repo"]

        # --- The FAKE TARGET HERD runs: its Supervisor authors the
        # first strategy, its Lead/Executor/Reviewer leave evidence,
        # and the task reaches COMPLETE — all target-side. ---
        cls._run_fake_target_herd(cls.lease_path)

        # PASS 2: verify (now COMPLETE via the REAL observe) ->
        # complete.
        cls.pass2 = runtime_module.process_once(broker)
        cls.completed_record = cls._fresh_entry(cls)

        # --- The adapter delivers the verified result to Telegram. ---
        cls.harness.adapter.deliver_pending_results()
        cls.delivered_record = cls._fresh_entry(cls)

        cls.control_after = tree_hash(cls.control)
        cls.target_after = tree_hash(cls.target_fixture)

    # ---- fixtures -----------------------------------------------------
    @classmethod
    def _mitiq_document(cls):
        return mission_document(
            objective="Resolve issue #2802 in mitiq",
            control={
                "repository_realpath": cls.control,
                "policy_digest_sha256": control_policy_digest(
                    cls.control
                ),
            },
            target={
                "canonical_host": "github.com",
                "owner": MITIQ_OWNER,
                "repo": "mitiq",
                "canonical_url": MITIQ_URL,
            },
            issue_or_pr={"kind": "issue", "number": MITIQ_ISSUE},
            baseline={
                "ref": "refs/heads/main", "commit_sha": cls.baseline
            },
            handoff={"revision": 2, "text": MITIQ_HANDOFF},
        )

    @classmethod
    def _build_runtime_broker(cls):
        from herdr.observe import observe
        transport = FakeGitTransport({MITIQ_URL: cls.target_fixture})
        role_turn = FakeRoleTurn(
            FakeRoleTurnResult(
                outcome="request_dispatch",
                turn={"turn_id": "turn-hv",
                      "role": "handoff_validation",
                      "process_id": 4242},
            )
        )
        # The verification turn's verified result is the target
        # Reviewer's own summary — the evidence originates target-side.
        role_turn.verification_result = FakeRoleTurnResult(
            outcome="verified_result",
            turn={"turn_id": "turn-verify", "role": "verification",
                  "process_id": 4243},
            detail=TARGET_REVIEWER_SUMMARY,
        )
        spawn_requests = []

        def spawn_recorder(parent_repo, request):
            # RECORDED ONLY — the child target herd is never actually
            # launched here; the live dispatch is a human step.
            spawn_requests.append((parent_repo, dict(request)))
            return {"task_id": "mitiq-2802",
                    "target_repo": os.path.join(
                        os.path.realpath(cls.workspaces), "wf-0001")}

        # The REAL read-only projection over the leased workspace — the
        # observation is exactly what production computes (I5 closure),
        # not a shape a double asserts into being.
        cls._observe_clock = [NOW]

        def observer(lease_repo):
            return observe(lease_repo, now=cls._observe_clock[0],
                           probe_agents=False)

        broker = broker_module.TargetBroker(
            store_directory=cls.state_dir,
            control_repository_realpath=cls.control,
            transport=transport,
            workspaces_root=cls.workspaces,
            role_turn_fn=role_turn,
            spawn_fn=spawn_recorder,
            clock=lambda: NOW,
            observer_fn=observer,
        )
        return broker, transport, role_turn, spawn_requests

    @classmethod
    def _run_fake_target_herd(cls, lease_path):
        # The fake target Supervisor is the FIRST component to author an
        # engineering plan; its herd leaves Lead/Executor/Reviewer
        # evidence, and the task reaches COMPLETE. All of this lands in
        # the target's OWN workspace, never the control chain.
        state = os.path.join(lease_path, ".herd", "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "supervisor-strategy.md"), "w") as f:
            f.write(TARGET_SUPERVISOR_STRATEGY + "\n")
        with open(os.path.join(state, "lead-evidence.md"), "w") as f:
            f.write(TARGET_LEAD_EVIDENCE + "\n")
        with open(os.path.join(state, "executor-evidence.md"), "w") as f:
            f.write(TARGET_EXEC_EVIDENCE + "\n")
        with open(os.path.join(state, "reviewer-evidence.md"), "w") as f:
            f.write(TARGET_REVIEWER_SUMMARY + "\n")
        with open(os.path.join(state, "task.json"), "w") as f:
            f.write(json.dumps({
                "id": "mitiq-2802", "status": "COMPLETE",
                "started_at": 1, "completed_at": 2,
                "description": "resolve mitiq #2802",
            }))
        # I3: the artifacts a REAL target herd produces and the
        # verification evidence now BINDS — the lifecycle checkpoint
        # (herd's own completion contract requires it) and the
        # canonical review round file (written by the target's own
        # review-decision step). Without them verification refuses
        # durably; with them the narrative's evidence chain is the
        # real one: target-produced review evidence, never
        # independent verification.
        with open(
            os.path.join(state, "task-checkpoint.md"), "w"
        ) as f:
            f.write(
                "# Task Checkpoint — mitiq-2802\n\n"
                "## Outcome\nCOMPLETE. Issue 2802 resolved.\n\n"
                "## Verification\n- mitiq test suite green\n\n"
                "## Mutation evidence\n- 4/4 mutants KILLED\n"
            )
        reviews = os.path.join(state, "reviews")
        os.makedirs(reviews, exist_ok=True)
        with open(
            os.path.join(reviews, "mitiq-2802-round-01.md"), "w"
        ) as f:
            f.write(
                "# Reviewer round 1\n\n"
                "Reviewer: `reviewer1` / `mitiq-rev-session`\n\n"
                "Protocol token: `APPROVE`\n\n"
                "## Transcript\n\nHERD_DECISION: APPROVE\n"
            )

    def _fresh_entry(self):
        document = wa_store.WorkflowStore(self.state_dir).load()
        return document["workflows"]["wf-0001"]

    def _control_chain_texts(self):
        """Every artifact the CONTROL CHAIN authored or was shown —
        the surface that must carry NO engineering plan."""
        texts = [
            self.mission_offer_text,
            self.offered_record["mission_authorization"]["rendered_text"],
            self.offered_record["human_intent"],
            self.spawn_requests[0][1]["task"],
        ]
        for _role, ctx in self.role_turn.contexts:
            if ctx is not None:
                texts.append(json.dumps(ctx))
        for _role, obs in self.role_turn.observations:
            if obs is not None:
                texts.append(json.dumps(obs))
        return texts

    # ---- one NAMED assertion per acceptance point ---------------------
    def test_point01_fresh_planning_semantics(self):
        # The RESTRICTIVE planning turn produced the Mission
        # Authorization; the legacy gateway envelope carried no
        # authority. Regression that would fail: mission content
        # sourced from the gateway body, or no fresh planning turn
        # recorded on the record it produced.
        self.assertEqual(self.harness.planning_calls[0][0], EXACT_SENTENCE)
        # The gateway turn saw the sentence but returned ONLY the
        # routing signal (its body carries no authority).
        self.assertIn(
            EXACT_SENTENCE, self.harness.gateway_requests[0].text
        )
        self.assertNotIn(
            "ROUTING SIGNAL ONLY",
            self.offered_record["mission_authorization"]["rendered_text"],
        )
        # The fresh planning turn's identity is recorded on the record.
        self.assertEqual(
            [t["role"] for t in self.offered_record["codex_turns"]],
            ["planning"],
        )

    def test_point02_complete_displayed_mission(self):
        # What the human actually SEES: the rendered Mission
        # Authorization, carrying objective, the exact target, the
        # issue, and the verbatim quoted human intent. Regression:
        # a section dropped from the displayed text.
        text = self.mission_offer_text
        self.assertIn(mission.MISSION_MESSAGE_HEADER, text)
        self.assertIn("Resolve issue #2802 in mitiq", text)
        self.assertIn(MITIQ_URL, text)
        self.assertIn("issue #2802", text)
        # The exact sentence is shown, quoted verbatim.
        self.assertIn(EXACT_SENTENCE, text)

    def test_point03_exactly_one_approval(self):
        # Exactly ONE approval is ever consumed; a replay is refused
        # and changes nothing. Regression: a second consumption, or a
        # replay silently accepted.
        self.assertEqual(
            self.authorized_record["phase"], wa_record.PHASE_AUTHORIZED
        )
        self.assertEqual(
            self.authorized_record["approval"]["consumed_at"], NOW
        )
        # The replay was answered with the EXACT already-consumed
        # refusal (dies on an evaluate-side removal) and did NOT
        # re-consume.
        self.assertGreater(
            len(self.harness.answers()),
            self.second_approve_answers_before,
        )
        self.assertEqual(
            self.harness.answers()[-1]["text"],
            "Mission decision refused (mission_already_consumed)."
            " Nothing was authorized or dispatched.",
        )
        self.assertEqual(
            self.after_second_approve["approval"]["consumed_at"], NOW
        )
        self.assertEqual(
            self.after_second_approve["phase"],
            wa_record.PHASE_AUTHORIZED,
        )

    def test_point04_automatic_preparation_no_manual_step(self):
        # After approval the whole forward chain runs inside
        # process_once with NO broker action performed by the test.
        # Regression: a step needing a manual poke, or the chain
        # stalling before dispatch.
        actions_pass1 = [a for a, _ in self.pass1["wf-0001"]]
        self.assertEqual(
            actions_pass1,
            [broker_module.ACTION_MATERIALIZE,
             broker_module.ACTION_PREPARE,
             broker_module.ACTION_VALIDATE_HANDOFF,
             broker_module.ACTION_DISPATCH,
             broker_module.ACTION_VERIFY],
        )
        for action, outcome in self.pass1["wf-0001"]:
            self.assertTrue(outcome.ok, (action, outcome.problem))
        # The claim existed only AFTER approval, never before.
        self.assertEqual(self.claimable_before_approval, [])

    def test_point05_handoff_validation_sees_real_instructions(self):
        # The handoff-validation turn was shown the ACTUAL target
        # contribution rules read from the leased workspace. Regression:
        # the turn judged the handoff blind to the target's real rules.
        hv_context = [
            ctx for role, ctx in self.role_turn.contexts
            if role == "handoff_validation"
        ][-1]
        by_name = {item["name"]: item for item in hv_context}
        self.assertIn("CONTRIBUTING.md", by_name)
        self.assertEqual(by_name["CONTRIBUTING.md"]["status"], "read")
        self.assertEqual(
            by_name["CONTRIBUTING.md"]["text"], self.contributing
        )

    def test_point06_exact_bounded_handoff_dispatch(self):
        # Exactly one spawn, supervisor-first surface, byte-identical
        # handoff. Regression: a mutated/extra dispatch, or handoff
        # bytes that drift from the stored record.
        self.assertEqual(len(self.spawn_requests), 1)
        parent_repo, request = self.spawn_requests[0]
        self.assertEqual(parent_repo, self.control)
        self.assertEqual(sorted(request), ["alias", "target_repo", "task"])
        self.assertEqual(request["task"], MITIQ_HANDOFF)
        self.assertEqual(
            request["task"].encode("utf-8"),
            self.completed_record["handoff"]["text"].encode("utf-8"),
        )
        self.assertEqual(
            request["alias"], dispatch_module.ALIAS_PREFIX + "wf-0001"
        )

    def test_point07_fake_supervisor_is_first_strategy_artifact(self):
        # The fake target Supervisor's strategy is the FIRST
        # strategy-bearing content in the run: it appears target-side
        # and NOWHERE in the control chain. Regression: the control
        # chain authoring or carrying an engineering plan (H4).
        strategy_file = os.path.join(
            self.lease_path, ".herd", "state", "supervisor-strategy.md"
        )
        with open(strategy_file) as f:
            self.assertIn(TARGET_SUPERVISOR_STRATEGY, f.read())
        for text in self._control_chain_texts():
            self.assertNotIn(
                TARGET_SUPERVISOR_STRATEGY, text,
                "an engineering plan leaked into a control-chain"
                " artifact",
            )
        # The dispatched handoff is a bounded brief, not a solution.
        self.assertNotIn(
            "regression test", self.spawn_requests[0][1]["task"].lower()
            .replace("verification", "")
        )

    def test_point08_target_herd_lead_exec_reviewer_evidence(self):
        # Lead / Executor / Reviewer evidence exists target-side, and
        # the verified result the human sees is the Reviewer's own
        # summary. Regression: the result fabricated control-side
        # rather than carried from the target herd.
        state = os.path.join(self.lease_path, ".herd", "state")
        for name, needle in (
            ("lead-evidence.md", TARGET_LEAD_EVIDENCE),
            ("executor-evidence.md", TARGET_EXEC_EVIDENCE),
            ("reviewer-evidence.md", TARGET_REVIEWER_SUMMARY),
        ):
            with open(os.path.join(state, name)) as f:
                self.assertIn(needle, f.read())
        self.assertEqual(
            self.completed_record["verified_result"]["summary"],
            TARGET_REVIEWER_SUMMARY,
        )

    def test_point09_fresh_status_and_verification_turns(self):
        # Verification ran as a FRESH turn (its own identity), after a
        # first pass that OBSERVED the target still running. Regression:
        # a reused/stale turn, or verification firing before the target
        # was observed complete.
        # Pass 1 verify observed the not-yet-started target and waited.
        pass1_verify = self.pass1["wf-0001"][-1]
        self.assertEqual(pass1_verify[0], broker_module.ACTION_VERIFY)
        self.assertEqual(pass1_verify[1].outcome, "target_running")
        self.assertEqual(
            self.after_pass1["phase"], wa_record.PHASE_DISPATCHED
        )
        # Pass 2 ran a fresh verification turn with its own identity.
        verify_turns = [
            t for t in self.completed_record["codex_turns"]
            if t["role"] == "verification"
        ]
        self.assertEqual(len(verify_turns), 1)
        self.assertEqual(verify_turns[0]["turn_id"], "turn-verify")
        # Every control-chain model consultation was a fresh role turn,
        # in order — and verification fired EXACTLY ONCE, only after the
        # target was observed complete (pass 1's wait ran no turn).
        self.assertEqual(
            [role for role, _wf, _now in self.role_turn.calls],
            ["prepare", "handoff_validation", "verification"],
        )

    def test_point10_telegram_verified_result(self):
        # The verified result reaches Telegram exactly once, naming the
        # exact target and carrying the Reviewer's summary; the durable
        # marker is DELIVERED. Regression: no delivery, a double
        # delivery, or a fabricated summary.
        delivered = [
            s for s in self.harness.sends()
            if adapter_module.RESULT_MESSAGE_HEADER in s["text"]
        ]
        self.assertEqual(len(delivered), 1)
        self.assertIn(TARGET_REVIEWER_SUMMARY, delivered[0]["text"])
        self.assertIn(MITIQ_URL, delivered[0]["text"])
        self.assertIn("issue #2802", delivered[0]["text"])
        self.assertEqual(
            self.delivered_record["result_delivery"]["state"],
            wa_record.DELIVERY_DELIVERED,
        )

    def test_point11_control_repository_byte_identical(self):
        # The control repository is byte-identical across the WHOLE
        # run (.git internals included). Regression: any write into the
        # control repo.
        self.assertEqual(self.control_after, self.control_before)

    def test_point12_no_delivery_action_anywhere(self):
        # No delivery verb is ever invoked: the target source repo is
        # byte-identical, the transport ran only read/materialize
        # verbs, delivery_authority stays none, and exactly one spawn
        # occurred. Regression: a push/commit/PR/tag/deploy anywhere.
        self.assertEqual(self.target_after, self.target_before)
        self.assertEqual(
            self.delivered_record["delivery_authority"], "none"
        )
        self.assertEqual(
            dispatch_module.dispatch_count(self.delivered_record), 1
        )
        forbidden = ("push", "commit", "tag", "merge", "pull")
        for argv in self.transport.argv_log:
            for verb in forbidden:
                self.assertNotIn(
                    verb, argv,
                    "a delivery/mutation verb reached the target: %r"
                    % (argv,),
                )
        # H3 belt: the final record still validates.
        wa_record.validate_record(self.delivered_record)

    def test_completed_phase_and_target_identity(self):
        # The narrative actually reached COMPLETED with a captured
        # durable target identity — so the twelve points describe a run
        # that finished, not one abandoned mid-lifecycle.
        self.assertEqual(
            self.completed_record["phase"], wa_record.PHASE_COMPLETED
        )
        self.assertIsNotNone(self.completed_record["target_engine"])
        actions_pass2 = [a for a, _ in self.pass2["wf-0001"]]
        self.assertEqual(
            actions_pass2,
            [broker_module.ACTION_VERIFY, broker_module.ACTION_COMPLETE],
        )
        # A COMPLETED workflow is terminal: no further LaunchAgent poll
        # can re-claim it, so no matter how long the service loops there
        # is never a second dispatch.
        claimable_ids = [
            wid for wid, _rev
            in runtime_module.claimable_workflows(self.state_dir)
        ]
        self.assertNotIn("wf-0001", claimable_ids)


class DirunAgentInstallerTests(unittest.TestCase):
    """The dirun LaunchAgent installer, exercised hermetically.

    The script is run with --home (redirected filesystem) and
    --no-load (no launchctl), so nothing touches the real user
    account; the generated plist is then parsed and pinned.
    """

    SCRIPT = os.path.join(REPO_ROOT, "scripts", "dirun-agent.sh")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        # A controlled PATH: a fake codex plus a real python3.
        self.fakebin = os.path.join(self.tmp.name, "fakebin")
        os.makedirs(self.fakebin)
        with open(os.path.join(self.fakebin, "codex"), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(self.fakebin, "codex"), 0o755)
        os.symlink(
            os.path.realpath(sys.executable),
            os.path.join(self.fakebin, "python3"),
        )
        self.env = {
            "HOME": self.home,
            "PATH": ":".join([self.fakebin, "/usr/bin", "/bin"]),
        }
        self.plist_path = os.path.join(
            self.home, "Library", "LaunchAgents",
            "com.dodginginfinity.dirun.plist",
        )

    def run_script(self, *args):
        return subprocess.run(
            ["bash", self.SCRIPT] + list(args),
            capture_output=True, text=True, env=self.env,
        )

    def test_install_writes_a_complete_pinned_job(self):
        config_dir = os.path.join(self.tmp.name, "cfg")
        os.makedirs(config_dir)
        config_path = os.path.join(config_dir, "config.json")
        with open(config_path, "w") as f:
            f.write("{}")
        completed = self.run_script(
            "install", "--home", self.home, "--no-load",
            "--config", config_path,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(os.path.exists(self.plist_path))
        # Owner-only: the job definition names paths a hostile local
        # account has no business editing.
        self.assertEqual(
            stat.S_IMODE(os.stat(self.plist_path).st_mode), 0o600
        )
        with open(self.plist_path, "rb") as f:
            job = plistlib.load(f)
        self.assertEqual(job["Label"], "com.dodginginfinity.dirun")
        arguments = job["ProgramArguments"]
        # Absolute python3, absolute entry script, the EXACT named
        # config, and the run subcommand — nothing else.
        self.assertTrue(os.path.isabs(arguments[0]))
        self.assertTrue(arguments[0].endswith("python3"))
        self.assertEqual(
            arguments[1], os.path.join(REPO_ROOT, "dirun.py")
        )
        self.assertEqual(
            arguments[2:], ["--config", config_path, "run"]
        )
        self.assertIs(job["RunAtLoad"], True)
        self.assertIs(job["KeepAlive"], True)
        self.assertEqual(job["ThrottleInterval"], 10)
        # The job PATH is EXACTLY the validated codex directory
        # followed by the fixed base list — the ambient PATH is never
        # passed through (exact value, so a passthrough mutant cannot
        # hide behind a coincidentally similar prefix).
        self.assertEqual(
            job["EnvironmentVariables"]["PATH"],
            self.fakebin + ":/usr/local/bin:/opt/homebrew/bin"
            ":/usr/bin:/bin:/usr/sbin:/sbin",
        )
        # Logs live in the named config's own directory, exactly like
        # the tgop agent: state, lock, and logs never split.
        self.assertEqual(
            job["StandardOutPath"],
            os.path.join(config_dir, "dirun.out.log"),
        )
        self.assertEqual(
            job["StandardErrorPath"],
            os.path.join(config_dir, "dirun.err.log"),
        )

    def test_missing_codex_fails_closed_with_nothing_installed(self):
        self.env["PATH"] = ":".join(["/usr/bin", "/bin"])
        completed = self.run_script(
            "install", "--home", self.home, "--no-load"
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("codex", completed.stderr)
        self.assertIn("Nothing was installed", completed.stderr)
        self.assertFalse(os.path.exists(self.plist_path))

    def test_uninstall_removes_exactly_the_installed_job(self):
        completed = self.run_script(
            "install", "--home", self.home, "--no-load"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(os.path.exists(self.plist_path))
        completed = self.run_script(
            "uninstall", "--home", self.home, "--no-load"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(os.path.exists(self.plist_path))
        # Uninstalling again is a clean no-op, not an error.
        completed = self.run_script(
            "uninstall", "--home", self.home, "--no-load"
        )
        self.assertEqual(completed.returncode, 0)


class DocsAccuracyPinTests(unittest.TestCase):
    """The shipped docs must describe what actually shipped.

    Each pin is a required DISCLOSURE: dropping or paraphrasing it is
    a suite failure, not an editorial choice.
    """

    # The A0 telemetry limitation, recorded VERBATIM (mission RULES;
    # I6 acceptance criterion 2). The words are pinned exactly.
    A0_SENTENCE = (
        "codex-cli 0.149.0 behaviorally denied both writes, but did"
        " not emit `command_execution` JSONL items for those denied"
        " attempts."
    )

    def test_a0_limitation_is_recorded_verbatim(self):
        self.assertIn(self.A0_SENTENCE, read_doc("SECURITY.md"))

    def test_migration_break_is_documented(self):
        # Undocumented since I1, user-visible: a v1 state.json FAILS
        # CLOSED until the human runs the migration.
        readme = read_doc("README.md")
        self.assertIn("tgop migrate-state", readme)
        self.assertIn(
            "fails closed until the human runs", readme
        )
        changelog = read_doc("CHANGELOG.md")
        self.assertIn("tgop migrate-state", changelog)

    def test_di_remote_2_is_not_claimed_for_v063(self):
        changelog = read_doc("CHANGELOG.md")
        self.assertIn("## Unreleased", changelog)
        unreleased, _, released = changelog.partition("## v0.6.3")
        self.assertTrue(released, "v0.6.3 section missing")
        self.assertIn("DI-REMOTE-2", unreleased)
        # Historical accuracy: no released section claims the v2
        # capability.
        self.assertNotIn("DI-REMOTE-2", released)

    def test_structure_only_limit_is_disclosed(self):
        self.assertIn(
            "validates STRUCTURE only", read_doc("SECURITY.md")
        )

    def test_operator_protocol_documents_the_v2_envelope(self):
        doc = read_doc("OPERATOR_PROTOCOL.md")
        self.assertIn("DI-REMOTE-2 RESPONSE", doc)
        self.assertIn("mission_authorization", doc)
        self.assertIn("role_outcome", doc)

    def test_contributing_lists_the_new_components(self):
        doc = read_doc("CONTRIBUTING.md")
        for token in (
            "workflow_authority/", "target_runtime/", "dirun.py",
        ):
            self.assertIn(token, doc)

    # ---- I7: every corrected claim pinned to a doc (J1) ---------------
    def test_changelog_and_readme_describe_route_b(self):
        # The route-(b) truth: the legacy turn's marker is a routing
        # signal with no authority; a SEPARATE fresh planning turn
        # produces the mission. The old route-(a) falsehood is gone.
        for name in ("CHANGELOG.md", "README.md"):
            doc = read_doc(name)
            self.assertIn("route (b)", doc, name)
            self.assertIn("ROUTING SIGNAL ONLY", doc.upper(), name)
            # The old false claim — the legacy turn RETURNING the
            # Mission Authorization envelope — must be gone.
            self.assertNotIn(
                "returns either a DI-REMOTE-1 plan envelope", doc, name
            )

    def test_full_lifecycle_and_verified_result_documented(self):
        # DISPATCHED -> VERIFIED -> COMPLETED is wired, with the
        # verified result returning to Telegram (no longer schema-only).
        changelog = read_doc("CHANGELOG.md")
        for token in ("DISPATCHED", "VERIFIED", "COMPLETED"):
            self.assertIn(token, changelog)
        self.assertIn("verified result", changelog.lower())
        operator = read_doc("OPERATOR_PROTOCOL.md")
        self.assertIn("verification turn", operator)

    def test_handoff_validation_shown_real_instructions_documented(self):
        operator = read_doc("OPERATOR_PROTOCOL.md")
        self.assertIn("actual bounded target instruction content", operator)

    def test_migrate_workflows_and_schema2_documented(self):
        # v1 records are RETIRED, never upgraded.
        for name in ("CHANGELOG.md", "README.md"):
            doc = read_doc(name)
            self.assertIn("tgop migrate-workflows", doc, name)
        changelog = read_doc("CHANGELOG.md")
        self.assertIn("schema-2", changelog)
        self.assertIn("RETIRED, never upgraded", changelog)

    def test_r2_is_authorization_scope_not_review_limit(self):
        for name in ("SECURITY.md", "OPERATOR_PROTOCOL.md"):
            doc = read_doc(name)
            self.assertIn("authorization-scope", doc.lower(), name)
            self.assertIn("review-round limit", doc.lower(), name)
            self.assertIn("NEEDS_REAUTHORIZATION", doc, name)

    def test_live_unverified_items_are_disclosed(self):
        # The four HUMAN validation items must be stated plainly, not
        # implied to work, somewhere in the shipped docs.
        blob = (read_doc("README.md") + read_doc("SECURITY.md")
                + read_doc("CHANGELOG.md")).lower()
        self.assertIn("live-unverified", blob)
        self.assertIn("first live", blob)
        self.assertIn("telegram", blob)
        self.assertIn("github", blob)
        self.assertIn("approval_policy", blob)

    def test_post_dispatch_leak_is_a_standing_constraint(self):
        # The point07 limitation is recorded as a standing constraint
        # on future work, so a later increment that re-reads target
        # files after dispatch knows the enumeration must grow.
        security = read_doc("SECURITY.md")
        self.assertIn("Standing constraint on future work", security)
        self.assertIn("POST-dispatch strategy leak", security)

    def test_changelog_argv_claim_is_accurate(self):
        # Round-13 F-1: the narrative's argv_log use is an ABSENCE
        # check; the full argv literals are pinned per-verb in the
        # Runtime suite. The doc must say that, and the old overstated
        # sentence must be gone.
        changelog = read_doc("CHANGELOG.md")
        self.assertIn(
            "every transport argv is pinned as an exact literal in the"
            " Runtime suite",
            changelog,
        )
        self.assertNotIn(
            "pins the full argv sequence of every operation", changelog
        )

    def test_exactly_once_discloses_the_not_retried_states(self):
        # Round-13 F-3: "exactly once" is misleading by omission unless
        # the two durable NEVER-auto-retried states (reserved, partial)
        # are disclosed. Both docs must say the result is not re-sent
        # automatically in those shapes.
        for name in ("README.md", "SECURITY.md"):
            # Normalize wrapping so line breaks inside a phrase don't
            # hide it.
            doc = " ".join(read_doc(name).lower().split())
            self.assertIn("re-sent automatically", doc, name)
            self.assertIn("reserved", doc, name)
            self.assertIn("partial", doc, name)
            # "exactly once" must never stand alone as the whole story:
            # the not-arrives caveat appears in the same document.
            self.assertIn("not that it always eventually arrives", doc, name)

    def test_operator_key_list_matches_enforced_schema(self):
        # Round-14 F-1, STRUCTURAL closure (same shape as I5's herd-
        # vocabulary contract test): the "EXACTLY these keys" list in
        # OPERATOR_PROTOCOL.md is PARSED and asserted set-equal to the
        # enforced ALLOWED_AUTHORIZATION_KEYS. Set equality drives BOTH
        # directions — a key in the code but not the doc leaves the code
        # side non-empty; a key in the doc but not the code leaves the
        # doc side non-empty; either fails. The prose spec can no longer
        # silently drift from the schema an Operator is refused against.
        import re
        from workflow_authority.authorization import (
            ALLOWED_AUTHORIZATION_KEYS,
        )
        doc = read_doc("OPERATOR_PROTOCOL.md")
        marker = "EXACTLY these keys:"
        start = doc.index(marker) + len(marker)
        segment = doc[start:doc.index(".", start)]
        doc_keys = set(re.findall(r"`([a-z_]+)`", segment))
        code_keys = set(ALLOWED_AUTHORIZATION_KEYS)
        self.assertEqual(
            doc_keys, code_keys,
            "OPERATOR_PROTOCOL key list drifted from"
            " ALLOWED_AUTHORIZATION_KEYS: doc-only=%s code-only=%s"
            % (sorted(doc_keys - code_keys),
               sorted(code_keys - doc_keys)),
        )
        # The three round-14 omissions are explicitly present.
        for key in ("execution_scope", "human_intent",
                    "unresolved_questions"):
            self.assertIn(key, doc_keys)
        # human_intent is named among the adapter-stamped null bindings
        # (the 120 chars leading into "must be null" list them).
        null_at = doc.index("must be null in the document")
        self.assertIn("`human_intent`", doc[null_at - 120:null_at])

    def test_planning_turn_answer_set_matches_adapter(self):
        # Round-15 F-1, STRUCTURAL closure (same shape as the key-list
        # pin): the fresh planning turn's documented answer set is
        # PARSED from OPERATOR_PROTOCOL.md and asserted set-equal to what
        # the adapter actually accepts (PLANNING_TURN_ACCEPTED_KINDS).
        # Set equality drives BOTH directions, so the sentence cannot
        # drift from _run_planning_and_offer either.
        import re
        from telegram_operator import adapter as adapter_module
        doc = read_doc("OPERATOR_PROTOCOL.md")
        marker = "answer set is EXACTLY these kinds:"
        start = doc.index(marker) + len(marker)
        segment = doc[start:doc.index(".", start)]
        doc_kinds = set(re.findall(r"`([a-z_]+)`", segment))
        code_kinds = set(adapter_module.PLANNING_TURN_ACCEPTED_KINDS)
        self.assertEqual(
            doc_kinds, code_kinds,
            "fresh-planning-turn answer set drifted from"
            " PLANNING_TURN_ACCEPTED_KINDS: doc-only=%s code-only=%s"
            % (sorted(doc_kinds - code_kinds),
               sorted(code_kinds - doc_kinds)),
        )
        # The old false attribution — the FRESH planning turn answering
        # with EITHER a v1 plan OR a v2 mission — must be gone.
        self.assertNotIn(
            "A fresh planning turn answers with EITHER", doc
        )
        # The either/or is now attributed to the LEGACY turn.
        self.assertIn(
            "The LEGACY Codex turn answers with either", doc
        )

    def test_operator_delivery_authority_literal_matches_constant(self):
        # Round-17 F-3, row 9 -> doc<->constant: the literal the doc
        # states ("the literal string `none`") is parsed and asserted
        # equal to DELIVERY_AUTHORITY_NONE. Drives both ways: a doc edit
        # (wrong literal) fails; a constant change (doc stale) fails.
        import re
        from workflow_authority.record import DELIVERY_AUTHORITY_NONE
        doc = read_doc("OPERATOR_PROTOCOL.md")
        m = re.search(r"the literal string `([a-z_]+)`", doc)
        self.assertIsNotNone(m, "delivery_authority literal sentence")
        self.assertEqual(m.group(1), DELIVERY_AUTHORITY_NONE)

    def test_operator_phase_list_matches_record_phases(self):
        # Round-17 F-3, row 13 -> doc<->constant: the phase names the
        # doc enumerates (the parenthetical containing PLANNED) are
        # parsed and asserted set-equal to record.PHASES. Both ways.
        import re
        from workflow_authority import record
        doc = read_doc("OPERATOR_PROTOCOL.md")
        idx = doc.index("PLANNED")
        segment = doc[idx:doc.index(")", idx)]
        doc_phases = set(re.findall(r"[A-Z][A-Z_]{3,}", segment))
        self.assertEqual(
            doc_phases, set(record.PHASES),
            "phase list drifted from record.PHASES: doc-only=%s"
            " code-only=%s" % (sorted(doc_phases - set(record.PHASES)),
                               sorted(set(record.PHASES) - doc_phases)),
        )

    def test_operator_handoff_outcome_vocab_matches_constant(self):
        # Round-17 F-3, row 16 -> doc<->constant: the three handoff-
        # validation outcomes the doc names are parsed and asserted
        # set-equal to protocol.HANDOFF_VALIDATION_OUTCOMES. Both ways.
        import re
        from telegram_operator import protocol
        doc = read_doc("OPERATOR_PROTOCOL.md")
        marker = "EXACTLY three outcomes:"
        start = doc.index(marker) + len(marker)
        segment = doc[start:doc.index(".", start)]
        doc_outcomes = set(re.findall(r"`([a-z_]+)`", segment))
        self.assertEqual(
            doc_outcomes, set(protocol.HANDOFF_VALIDATION_OUTCOMES),
            "outcome vocab drifted from HANDOFF_VALIDATION_OUTCOMES:"
            " doc-only=%s code-only=%s"
            % (sorted(doc_outcomes
                      - set(protocol.HANDOFF_VALIDATION_OUTCOMES)),
               sorted(set(protocol.HANDOFF_VALIDATION_OUTCOMES)
                      - doc_outcomes)),
        )

    def test_operator_r2_bound_matches_constant(self):
        # Round-18 F-2, row 20 -> doc<->constant: the R-2 follow-up
        # bound the section states ("AUTHORIZATION-SCOPE bound (N)") is
        # parsed and asserted equal to dispatch.MAX_FOLLOW_UP_DISPATCHES.
        # Drives both ways: a doc edit (wrong number) fails; a constant
        # change without the doc (the more dangerous direction) fails.
        import re
        from target_runtime import dispatch
        doc = read_doc("OPERATOR_PROTOCOL.md")
        m = re.search(r"AUTHORIZATION-SCOPE bound \((\d+)\)", doc)
        self.assertIsNotNone(m, "R-2 bound sentence")
        self.assertEqual(
            int(m.group(1)), dispatch.MAX_FOLLOW_UP_DISPATCHES
        )

    def test_planning_prompt_states_schema_not_bounds(self):
        # Round-16 F-1, ARTIFACT pin: the doc claims the fresh planning
        # prompt carries the key set and sub-shapes but NOT the bounds.
        # Assert that against the RENDERED PROMPT itself — the prompt has
        # the sub-shape keys and NONE of the bound numbers/words — so the
        # doc sentence cannot drift from the artifact it describes. (This
        # round's proof that a doc change needs measure-before-claim.)
        from codex_gateway.role_turn import render_planning_prompt
        prompt = render_planning_prompt("solve it", "/control/repo",
                                        "a" * 64)
        for key in ("control", "target", "issue_or_pr", "baseline",
                    "handoff"):
            self.assertIn(key, prompt)
        for absent in ("8000", "16384", "2000", "MAX_ENVELOPE_CHARS",
                       "MAX_OUTCOME_DETAIL_CHARS", "at most"):
            self.assertNotIn(
                absent, prompt,
                "the planning prompt states a bound it must not: %r"
                % absent,
            )

    def test_operator_bound_numbers_match_constants(self):
        # Round-16 F-1, DOC<->CONSTANT pin: the three bound numbers the
        # section states are asserted equal to the enforced constants,
        # each next to its own name — so a constant change (doc stale) or
        # a doc edit (number wrong) fails. The numbers are attributed
        # correctly: per-field authority bound, envelope cap, and the
        # role_outcome detail cap.
        from telegram_operator import mission as mission_module
        from telegram_operator import protocol as protocol_module
        flat = " ".join(read_doc("OPERATOR_PROTOCOL.md").split())
        self.assertIn(
            "per-field authority bound %d"
            % mission_module.MAX_AUTHORITY_FIELD_CHARS, flat)
        self.assertIn(
            "`MAX_ENVELOPE_CHARS` %d"
            % protocol_module.MAX_ENVELOPE_CHARS, flat)
        self.assertIn(
            "`MAX_OUTCOME_DETAIL_CHARS` %d"
            % protocol_module.MAX_OUTCOME_DETAIL_CHARS, flat)
        # The old false attribution (prompt carries "its bounds") is gone.
        self.assertNotIn("schema (including", flat)
        self.assertNotIn("and its bounds", flat)

    def test_operator_section_points_at_prompt_for_full_schema(self):
        # Round-15 (misleading by omission): the section names the key
        # set but not the sub-shapes/bounds, so it must point at the
        # planning prompt as the complete schema-and-bounds source.
        doc = read_doc("OPERATOR_PROTOCOL.md")
        flat = " ".join(doc.split())
        self.assertIn(
            "complete Mission Authorization key set and the", flat
        )
        for token in ("MAX_ENVELOPE_CHARS", "16384",
                      "MAX_OUTCOME_DETAIL_CHARS", "2000",
                      "is not the whole contract"):
            self.assertIn(token, flat)

    def test_ci_compiles_the_new_packages(self):
        # CI syntax coverage (D3) for the new packages, pinned so it
        # cannot silently regress.
        ci = read_doc(os.path.join(".github", "workflows", "ci.yml"))
        py_compile_line = [
            line for line in ci.splitlines()
            if "py_compile" in line
        ][-1]
        for token in ("workflow_authority/", "target_runtime/",
                      "dirun.py"):
            self.assertIn(token, py_compile_line)


class CorrectnessDocsPinTests(unittest.TestCase):
    """I6 (task 20260826-113247): every normative sentence added for
    the DI-REMOTE-2 correctness work is pinned IN THE SAME EDIT (the
    recorded normative-prose rule). doc<->code pins PARSE the
    document and assert against the code registry, so both a doc
    falsification and a code drift fail; framing pins assert the
    exact load-bearing phrases (a doc falsification fails; a code
    change alone is caught by the cited fact tests, not here). The
    full claim->pin map with honest per-row labels lives in the I6
    evidence artifact."""

    ALL_DOCS = ("OPERATOR_PROTOCOL.md", "README.md", "SECURITY.md",
                "CHANGELOG.md")

    @staticmethod
    def flat(name):
        return " ".join(read_doc(name).split())

    def test_conjunct_and_code_counts_match_the_gate_registry(self):
        # doc<->code: "eight conjuncts" / "ten independent problem
        # codes" in every doc, against the DERIVED registry shape —
        # ten gates/codes grouping into eight conjunct families.
        for name in self.ALL_DOCS:
            flat = self.flat(name).lower()
            self.assertIn("eight conjuncts", flat, name)
            self.assertIn("ten independent problem codes", flat, name)
        gate_names = [name for name, _ in
                      broker_module._VERIFICATION_GATES]
        self.assertEqual(len(gate_names), 10)
        self.assertEqual(
            len(broker_module.VERIFICATION_GATE_CODES), 10
        )
        conjunct_families = {name.split("_")[0]
                            for name in gate_names}
        self.assertEqual(len(conjunct_families), 8)

    def test_consumed_source_sets_match_the_registry(self):
        # doc<->code (both ways): the two registered consumed-source
        # sets are PARSED out of OPERATOR_PROTOCOL.md and asserted
        # equal to the code registry.
        flat = self.flat("OPERATOR_PROTOCOL.md")
        parsed = {
            key: tuple(part.strip() for part in body.split(","))
            for key, body in re.findall(
                r"`(verification|reconcile_dispatch):"
                r" ([a-z, ]+)`", flat
            )
        }
        self.assertEqual(
            parsed, dict(evidence_module.CONSUMED_SOURCE_SETS),
            "OPERATOR_PROTOCOL consumed-source sets drifted from"
            " evidence.CONSUMED_SOURCE_SETS",
        )

    def test_reconcile_block_causes_match_the_code_registry(self):
        # doc<->code (both ways): the five durable-block causes are
        # PARSED from the sentence after "stops durably BLOCKED:"
        # and mapped onto RECONCILE_BLOCK_CODES.
        flat = self.flat("OPERATOR_PROTOCOL.md")
        marker = "stops durably BLOCKED:"
        self.assertIn(marker, flat)
        sentence = flat.split(marker, 1)[1].split(".", 1)[0]
        causes = re.findall(r"`([a-z_]+)`", sentence)
        self.assertEqual(len(causes), 5, causes)
        self.assertEqual(
            {"broker_reconcile_" + cause for cause in causes},
            set(broker_module.RECONCILE_BLOCK_CODES),
        )

    def test_alias_prefix_matches_dispatch_constant(self):
        # doc<->code: the derived-alias prefix is parsed and must
        # equal dispatch.ALIAS_PREFIX.
        flat = self.flat("OPERATOR_PROTOCOL.md")
        match = re.search(r"derived alias \(`([a-z0-9-]+)`", flat)
        self.assertIsNotNone(match, "alias sentence missing")
        self.assertEqual(
            match.group(1), dispatch_module.ALIAS_PREFIX
        )

    def test_capacity_codes_match_constants(self):
        # doc<->code: the two truthful capacity codes appear
        # backticked, looked up FROM the constants (a constant
        # rename fails this without a doc edit, and vice versa).
        for name in ("OPERATOR_PROTOCOL.md", "SECURITY.md",
                     "CHANGELOG.md"):
            flat = self.flat(name)
            self.assertIn(
                "`%s`" % broker_module
                .PROBLEM_RECORD_CAPACITY_EXHAUSTED,
                flat, name,
            )
            self.assertIn(
                "`%s`" % runtime_module
                .PROBLEM_TURN_CAPACITY_EXHAUSTED,
                flat, name,
            )

    def test_surface_receipt_marker_matches_constant(self):
        # doc<->code: the receipt marker is quoted verbatim from the
        # constant in the protocol and security docs.
        for name in ("OPERATOR_PROTOCOL.md", "SECURITY.md",
                     "CHANGELOG.md"):
            self.assertIn(
                "`%s`" % dispatch_module.SURFACE_RECEIPT_MARKER,
                self.flat(name), name,
            )

    def test_seven_verb_transport_claim_matches_the_class(self):
        # doc<->code: "seven-verb" against the DERIVED public-method
        # count of the real transport class; the stale "five-verb"
        # must be gone everywhere.
        verbs = [
            name for name, _ in inspect.getmembers(
                git_transport_module.GitTransport,
                inspect.isfunction,
            ) if not name.startswith("_")
        ]
        self.assertEqual(len(verbs), 7, verbs)
        for name in ("SECURITY.md", "CHANGELOG.md"):
            flat = self.flat(name)
            self.assertIn("seven-verb git transport seam", flat,
                          name)
        for name in self.ALL_DOCS:
            self.assertNotIn("five-verb", self.flat(name), name)

    def test_never_sufficient_and_complete_alone_framing(self):
        # framing: the two load-bearing verification sentences in
        # every doc. Code side: I3VerificationGateTests
        # (test_lifecycle_complete_alone_never_reaches_verified,
        # test_refusal_matrix_every_gate_code_blocks_durably).
        for name in self.ALL_DOCS:
            flat = self.flat(name).lower()
            self.assertIn("necessary, never sufficient", flat, name)
            self.assertIn("complete alone can never", flat, name)

    def test_reviewer_approve_target_produced_never_independent(self):
        # framing: the review conjunct's honest description in every
        # doc. Code-side wording is separately scanned by
        # test_gate_wording_never_claims_independent_verification.
        for name in self.ALL_DOCS:
            flat = self.flat(name).lower()
            self.assertIn("target-produced", flat, name)
            self.assertIn("never independent verification", flat,
                          name)

    def test_r6_framing_pins(self):
        # framing: source-scoped completeness, the raw value
        # rendered unaltered, and the expected production PARTIAL.
        # Code side: test_scoped_support_advances_under_agents_
        # unprobed_partial, test_agents_unprobed_partial_advances_
        # every_terminal_status, test_completeness_lines_render_raw_
        # distinct_values.
        for name in self.ALL_DOCS:
            flat = self.flat(name).lower()
            self.assertIn("source-scoped", flat, name)
            self.assertIn("rendered unaltered", flat, name)
            self.assertIn("expected in production", flat, name)

    def test_r3_framing_pins(self):
        # framing: ruling R-3's boundary in every doc; "never
        # spawns" where the recovery flow is described in full.
        for name in self.ALL_DOCS:
            flat = self.flat(name).lower()
            self.assertIn("reads nothing outside this repository",
                          flat, name)
            self.assertIn("never binding evidence", flat, name)
            self.assertIn("accepted cost", flat, name)
        for name in ("OPERATOR_PROTOCOL.md", "README.md",
                     "SECURITY.md"):
            self.assertIn("never spawns", self.flat(name).lower(),
                          name)

    def test_attribution_is_stated_plainly(self):
        # doc-presence: the two inherited defects are attributed to
        # the accepted task by id in every doc, and the containment
        # class is attributed to its two increments where the story
        # is told in full.
        for name in self.ALL_DOCS:
            flat = self.flat(name)
            self.assertIn("20260826-022933", flat, name)
            self.assertIn("inherited", flat.lower(), name)
        for name in ("OPERATOR_PROTOCOL.md", "SECURITY.md",
                     "CHANGELOG.md"):
            self.assertIn("instance-wise", self.flat(name).lower(),
                          name)

    def test_i3b_recorded_only_as_deferred_candidate(self):
        # framing + honesty guard: the general stop-reason mechanism
        # is documented as DEFERRED, with the limitation stated —
        # never as shipped behaviour.
        for name in ("OPERATOR_PROTOCOL.md", "CHANGELOG.md"):
            flat = self.flat(name).lower()
            self.assertIn("deferred follow-up candidate", flat, name)
            self.assertIn("leave no record receipt today", flat,
                          name)
        # O-1 (round-12): the strictest true phrasing — those
        # results are DISCARDED by cli.py, so the operator-visible
        # surface is nothing at all.
        self.assertIn(
            "surface in runtime results (not in `/status` or"
            " console output)",
            self.flat("OPERATOR_PROTOCOL.md").lower(),
        )

    def test_pre_receipt_fails_closed_framing(self):
        # framing: pre-receipt workflows fail closed; never
        # retro-fitted. Code side:
        # test_pre_receipt_workflow_is_never_retrofitted.
        for name in ("OPERATOR_PROTOCOL.md", "SECURITY.md",
                     "CHANGELOG.md"):
            self.assertIn("fails closed at verification",
                          self.flat(name).lower(), name)
        for name in ("OPERATOR_PROTOCOL.md", "SECURITY.md"):
            self.assertIn("never retro-fitted",
                          self.flat(name).lower(), name)

    def test_recovery_determinism_framing(self):
        # framing: one deterministic recovery path per pass, decided
        # on durable state; the fresh turn's role name appears where
        # the flow is described. Code side: the I4 predicate truth
        # table and pacing tests.
        operator = self.flat("OPERATOR_PROTOCOL.md").lower()
        self.assertIn(
            "exactly one deterministic recovery path per pass",
            operator,
        )
        self.assertIn("durable state only", operator)
        for name in ("OPERATOR_PROTOCOL.md", "README.md",
                     "CHANGELOG.md"):
            self.assertIn("status_recovery", read_doc(name), name)

    def test_containment_framing(self):
        # framing: one workflow stops, the Runtime survives. Code
        # side: test_bound_record_survives_the_pass_after_a_bind and
        # the record-growth derivation tests.
        for name in self.ALL_DOCS:
            flat = self.flat(name).lower()
            self.assertIn("never kills the runtime", flat, name)


if __name__ == "__main__":
    unittest.main()
