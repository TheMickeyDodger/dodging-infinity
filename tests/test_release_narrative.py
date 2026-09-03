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

import html
import html.parser
import inspect
import json
import os
import plistlib
import re
import shutil
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
import _scope_hygiene as scope_hygiene
from _di_remote2_surface import unit_digest
from test_workspace_trust import document_units

from target_runtime import broker as broker_module
from target_runtime import evidence_preservation as preserve_module
from target_runtime import dispatch as dispatch_module
from target_runtime import evidence as evidence_module
from target_runtime import git_transport as git_transport_module
from target_runtime import runtime as runtime_module
from target_runtime import workspace as workspace_module

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

# A representative driving sentence — byte-for-byte the input the human
# sends — using a generic external target and issue.
EXACT_SENTENCE = (
    "I want to solve"
    " https://github.com/example-org/external-target/issues/42."
    " Go do it."
)
TARGET_URL = "https://github.com/example-org/external-target"
TARGET_OWNER = "example-org"
TARGET_ISSUE = 42

# The bounded handoff the control chain dispatches: objective +
# constraints + rules, and NO technical solution — the corrective
# brief, never an engineering plan. (The engineering plan is the FAKE
# TARGET SUPERVISOR's job; see TARGET_SUPERVISOR_STRATEGY.)
TARGET_HANDOFF = (
    "Resolve issue #42 in the external target repository: investigate the actual cause,"
    " preserve the repository contribution rules, add the necessary"
    " verification, and prepare the result for review."
)

# The FIRST strategy-bearing artifact in the whole run — produced by
# the fake target Supervisor, target-side ONLY. If this string ever
# appears in a control-chain artifact (the mission, the handoff, a
# control-chain prompt/context, or the control repo), the control
# chain has authored engineering, which it must never do (H4).
TARGET_SUPERVISOR_STRATEGY = (
    "TARGET-SUPERVISOR STRATEGY: reproduce the target issue, isolate"
    " the defect, add a regression test, then run the suite to green."
)
# The target herd's Lead / Executor / Reviewer evidence — target-side
# artifacts, the source of the verified result the human finally sees.
TARGET_LEAD_EVIDENCE = "LEAD: decomposed the target issue into reproduce+fix+verify."
TARGET_EXEC_EVIDENCE = "EXECUTOR: patched the target interface; added a test."
TARGET_REVIEWER_SUMMARY = (
    "REVIEWER: external target issue resolved — regression test added and the full"
    " target suite passes; acceptance criteria met."
)


def read_doc(name):
    with open(os.path.join(REPO_ROOT, name), encoding="utf-8") as f:
        return f.read()


# ---- The historical external-target mountain: TERMINAL evidence -------
# This mountain is finished. It ran real target engineering, then
# EXPOSED GENUINE POST-DISPATCH POLICY DRIFT and CORRECTLY TERMINATED
# BLOCKED. Nothing downstream of that stop ever ran. Every identifier
# below is quoted verbatim from the durable evidence; the docs must
# carry them and must NOT overstate what they prove.
MOUNTAIN_WORKFLOW_ID = "wf-2c901885473fc4781bf82296"
MOUNTAIN_TARGET_TASK = "20260830-094026-9fef2d"
MOUNTAIN_BASELINE = "3e1833d930723ef4f7220698c98155a925591d4d"
MOUNTAIN_STOP_CODE = "broker_verification_policy_drift"

# ---- The SEPARATE Runtime stabilization: pushed, NOT integrated -------
# It closed the defects the mountain exposed. It is not on this branch,
# it has never been merged, and it has never been released.
STABILIZATION_SHA = "d8ec2af409e4086f985be03371a872a84a3767ec"
STABILIZATION_BRANCH = "fix/runtime-terminal-reconciliation"
STABILIZATION_TASK = "20260830-185309-4c3db7"

# ---- PR #14 clean-clone CI evidence -----------------------------------
CI_RUN_ID = "33330263889"
CI_COMMITS = (
    "52a97b71a3b5c9f20ff33d4feb1332284cd825b7",
    "4eea64f2a915e988dbfd73ad51dd9f6546bc6a8f",
)
# The run the roadmap used to cite. It is STALE and must be gone.
CI_STALE_RUN_ID = "33329676305"

# The one framing sentence every reconciled document must carry. It is
# the ANCHOR for the negative pins below: a document that never
# mentioned the mountain at all would satisfy "does not claim it
# passed" vacuously, so absence is only ever asserted alongside this
# presence.
TERMINAL_ANCHOR = (
    "exposed genuine post-dispatch policy drift and correctly"
    " terminated BLOCKED"
)

# Claims that were TRUE of nothing and must appear NOWHERE. Each is
# whitespace-normalized and lower-cased before comparison, because
# these documents hard-wrap prose.
FORBIDDEN_LIVE_CLAIMS = (
    "live-proven through active",
    "live-proven through real target herdr",
    "live-proven through target herdr",
    "live-proven foundation",
    "pending live validation",
    "the mountain passed",
    "mountain has passed",
    "di-remote-2 has been released",
    "di-remote-2 is now released",
    "main is stable",
    "main is now stable",
    "has been merged into",
    "stabilization is merged",
    "telegram git authority",
    "final telegram result was delivered",
    "reached verified",
    "the workflow completed",
    # ROUND-1 B1/B2 survivors. These were IN THE TREE while all 87
    # pins were green: the pins asserted PRESENCE of the corrected
    # framing and nothing asserted ABSENCE of the superseded framing.
    "through an `active` target task",
    "has not yet proved the final reviewer",
)

# Some superseded framings are a SHAPE, not a phrase: "production has
# not yet proved the final Reviewer" survives any number of rewordings
# ("does not prove the Reviewer", "never proved target COMPLETE"). A
# phrase list cannot close a shape, so these are patterns. Each is
# matched against the whitespace-normalized, lower-cased document.
FORBIDDEN_CLAIM_PATTERNS = (
    (
        "the final Reviewer / target COMPLETE claimed UNPROVEN"
        " (both DID happen on the historical mountain)",
        r"(?:not\s+yet\s+prove|has\s+not\s+prove|have\s+not\s+prove"
        r"|does\s+not\s+prove|do\s+not\s+prove|never\s+prove"
        r"|cannot\s+prove|fails?\s+to\s+prove)\w*\s+"
        r"(?:that\s+)?(?:the\s+)?(?:final\s+)?"
        r"(?:target\s+(?:herdr\s+)?complete|reviewer)",
    ),
    (
        "the final Reviewer listed among still-pending items"
        " (state word BEFORE the subject)",
        r"(?:still\s+)?(?:pending|unproven|live-unverified|unverified)"
        r"[^.]{0,60}?final\s+reviewer",
    ),
    (
        "the final Reviewer described as unproven"
        " (state word AFTER the subject)",
        # The mirror of the row above. Round-2 mutant N06 proved the
        # single-direction pattern SURVIVED "the final Reviewer remains
        # unproven": a shape pin must cover both word orders.
        r"final\s+reviewer(?:\s+approve)?[^.]{0,60}?"
        r"(?:remains?|is|are|stays?|stayed)\s+(?:still\s+)?"
        r"(?:unproven|unverified|live-unverified|pending)",
    ),
)

# Present-tense assertions of a mission that is TERMINAL. The roadmap
# is the document that carried one (round-1 B2).
FORBIDDEN_PRESENT_TENSE_PATTERNS = (
    (
        "asserts a currently ACTIVE mission",
        # Unqualified subject, present tense, ACTIVE. This is the exact
        # shape of the round-1 B2 survivor ("the historical mission is
        # ACTIVE"). Deliberately NOT extended to "is running": the
        # roadmap's own acceptance criterion legitimately says "while an
        # eight-hour mission is running" about FUTURE missions, and a
        # pin that fires on that would be pressure to weaken a
        # preserved requirement.
        r"(?:mission|mountain|workflow)\s+is\s+active",
    ),
    (
        "asserts the HISTORICAL mountain is still in flight",
        # Subject explicitly identified as the historical one, so any
        # present-tense in-flight verb is wrong here.
        r"(?:historical|external-target|di-remote-2)\s+"
        r"(?:mission|mountain|workflow)\s+(?:is|remains)\s+"
        r"(?:still\s+)?(?:active|running|in\s+flight|underway|ongoing)",
    ),
)

RECONCILED_DOCS = (
    "README.md",
    "CHANGELOG.md",
    "docs/remote-mission-fabric-roadmap.md",
)
# SECURITY.md carries the same evidence boundary in narrower form: it
# states the terminal framing but not the release gate or the CI run,
# so it joins the ANCHORED negative pin and nothing else.
CLAIM_DISCIPLINED_DOCS = RECONCILED_DOCS + ("SECURITY.md",)


def flat(text):
    """Whitespace-normalize hard-wrapped prose."""
    return " ".join(text.split())


def flat_lower(text):
    return flat(text.lower())


def find_flexible(text, phrase):
    """Index of ``phrase`` in ``text``, tolerating hard-wrap newlines.

    Returns -1 when absent, so callers fail by AUTHORED assertion
    rather than by a ValueError crash.
    """
    pattern = re.compile(r"\s+".join(re.escape(w) for w in phrase.split()))
    match = pattern.search(text)
    return match.start() if match else -1


class ReleaseNarrativeTests(unittest.TestCase):
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
    # The message id Telegram assigns the bot's own result placeholder.
    PLACEHOLDER_MESSAGE_ID = 7301

    # ---- the placeholder send seam (DI-REMOTE-3 R-6) -------------------
    #
    # `Adapter.ensure_result_placeholders` sends the placeholder through
    # `api.send_message_once` — the I2 single-attempt entry that never
    # retries and never chunks. This narrative's Telegram fake predates
    # that entry, so it is wrapped here: everything else is delegated
    # untouched, and the one new method is served by the REAL
    # `telegram_api.TelegramApi` over a transport that returns a
    # genuine Telegram `ok:true` body. The adapter therefore runs its
    # ORDINARY path against the REAL classifier — nothing about the
    # binding is hand-written into the record.
    class _PlaceholderSendApi(object):
        """The narrative's Telegram fake, plus the two DI-REMOTE-3
        entry points, each served by the REAL `telegram_api` client.

        `send_message_once` (I2) binds the placeholder;
        `edit_message_text` (I2) delivers the verified result INTO that
        bound object. Both run through a genuine
        `telegram_api.TelegramApi` over a recording transport that
        returns a real Telegram `ok:true` body, so the adapter
        exercises the real classifier — nothing about the delivery is
        hand-written into the record.

        `transport_calls` is the raw CALL LOG: every request that
        actually left the client, in order. Asserting on it rather
        than on state is what makes a fresh-send regression visible;
        a state-only check would not have caught R-15.
        """

        def __init__(self, inner, message_id):
            self._inner = inner
            self.placeholder_sends = []
            self.edit_calls = []
            self.transport_calls = []
            self._message_id = message_id
            from telegram_operator import telegram_api as _api

            def transport(url, payload_bytes, deadline_seconds):
                payload = json.loads(payload_bytes.decode("utf-8"))
                method = url.rsplit("/", 1)[-1]
                self.transport_calls.append((method, payload))
                if "message_id" in payload:
                    self.edit_calls.append(payload)
                else:
                    self.placeholder_sends.append(payload)
                body = json.dumps({
                    "ok": True,
                    "result": {"message_id": self._message_id},
                }).encode("utf-8")
                return 200, body

            self._real = _api.TelegramApi(
                "12345:NARRATIVE-TOKEN", transport=transport,
                sleeper=lambda seconds: None,
            )

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def send_message_once(self, chat_id, text):
            return self._real.send_message_once(chat_id, text)

        def edit_message_text(self, chat_id, message_id, text):
            return self._real.edit_message_text(
                chat_id, message_id, text
            )

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
        # The generic external target fixture,
        # carrying the REAL contribution rules the handoff-validation
        # turn must be shown.
        cls.target_fixture = os.path.join(base, "external-target-fixture")
        cls.contributing = (
            "external target contribution rules: sign the CLA, add tests,"
            " keep public APIs stable.\n"
        )
        cls.baseline = make_git_repo(cls.target_fixture, {
            "README.md": "external target readme\n",
            "CONTRIBUTING.md": cls.contributing,
        })
        cls.state_dir = os.path.join(base, "state")
        os.makedirs(cls.state_dir)
        cls.workspaces = os.path.join(base, "workspaces")
        # I1: injected user-global Claude configuration; within this
        # fixture the write target is this temp path, not the
        # developer's real ~/.claude.json.
        cls.claude_config = os.path.join(base, ".claude.json")
        # I1 round-01 C-1: HOME points at this fixture's base so the
        # dispatch-time re-verification resolves the child's config to
        # cls.claude_config (the real production derivation).
        from unittest.mock import patch as _mp
        _home = _mp.dict(os.environ, {"HOME": base})
        _home.start()
        cls.addClassCleanup(_home.stop)
        with open(cls.claude_config, "w", encoding="utf-8") as handle:
            json.dump({"projects": {}}, handle, indent=2)
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
            planning_result(mission_envelope(cls._target_document()))
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

        # --- DI-REMOTE-3 R-6: the initial-dispatch placeholder gate.
        #
        # Layer 1 (I3) stamps `result_placeholder = required` in the
        # SAME locked transaction that arms the mission, so a
        # go-forward workflow reaches VALIDATED with a placeholder that
        # is not yet bound, and the Broker REFUSES initial dispatch
        # until the adapter binds it. In production the two loops run
        # concurrently — the adapter's own poll loop calls
        # `ensure_result_placeholders()` — so the refusal is transient
        # and self-healing, and the ordering below is the REAL one:
        # the adapter binds, and the Runtime then runs its whole chain.
        #
        # This narrative previously approved and then called
        # `process_once` immediately, a sequence production can no
        # longer perform. Rather than merely surviving the gate, the
        # two probes below DOCUMENT it: dispatch is refused with ZERO
        # spawn requests while unbound, and succeeds once bound.
        cls.placeholder_after_approval = (
            cls._fresh_entry(cls)["result_placeholder"]
        )
        (cls.gated_pass, cls.gated_spawn_requests,
         cls.gated_entry) = cls._probe_dispatch_before_binding()

        # The BINDING, through the adapter's ORDINARY loop step — the
        # same call `Adapter.run` makes every poll iteration. Nothing
        # is written into the record by hand.
        cls.placeholder_api = cls._PlaceholderSendApi(
            cls.harness.adapter.api, cls.PLACEHOLDER_MESSAGE_ID
        )
        cls.harness.adapter.api = cls.placeholder_api
        cls.harness.adapter.ensure_result_placeholders()
        cls.placeholder_after_binding = (
            cls._fresh_entry(cls)["result_placeholder"]
        )

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
        # complete -> release_workspace. The release PRESERVES the
        # target herd's evidence before reclaiming the workspace, so
        # the artifact assertions below still find it.
        cls.pass2 = runtime_module.process_once(broker)
        cls.completed_record = cls._fresh_entry(cls)

        # --- The adapter delivers the verified result to Telegram. ---
        #
        # DI-REMOTE-3 R-15/R-16: this workflow carries a BOUND
        # placeholder (bound above through the ordinary adapter loop
        # step), so production delivers its result by EDITING that
        # object — never by a fresh send. `deliver_pending_results` is
        # the LEGACY at-most-once lane and is gated on
        # `result_placeholder is None`, so it CORRECTLY refuses this
        # record; both loop steps are driven here in the same order
        # `run()` drives them, and the assertions below pin that the
        # legacy lane produced nothing while the edit lane produced
        # exactly one edit.
        cls.sends_before_delivery = len(cls.harness.sends())
        # R-11: a seam that is missing or broken must fail this
        # narrative by an AUTHORED assertion (test_point10 below), not
        # by an ERROR cascade out of setUpClass — a crash is not a kill.
        cls.delivery_error = None
        try:
            cls.harness.adapter.deliver_result_edits()
            cls.harness.adapter.deliver_pending_results()
        except Exception as exc:  # pragma: no cover - defensive
            cls.delivery_error = "%s: %s" % (type(exc).__name__, exc)
        cls.delivered_record = cls._fresh_entry(cls)

        cls.control_after = tree_hash(cls.control)
        cls.target_after = tree_hash(cls.target_fixture)

    # ---- fixtures -----------------------------------------------------
    @classmethod
    def _target_document(cls):
        return mission_document(
            objective="Resolve issue #42 in the external target repository",
            control={
                "repository_realpath": cls.control,
                "policy_digest_sha256": control_policy_digest(
                    cls.control
                ),
            },
            target={
                "canonical_host": "github.com",
                "owner": TARGET_OWNER,
                "repo": "external-target",
                "canonical_url": TARGET_URL,
            },
            issue_or_pr={"kind": "issue", "number": TARGET_ISSUE},
            baseline={
                "ref": "refs/heads/main", "commit_sha": cls.baseline
            },
            handoff={"revision": 2, "text": TARGET_HANDOFF},
        )

    @classmethod
    def _probe_dispatch_before_binding(cls):
        """Run the REAL Runtime chain against this workflow while its
        placeholder is still `required`, on a COPY of the store.

        Why a copy: the chain would otherwise advance the narrative's
        own record through materialize/prepare/validate, and
        `test_point04` pins that ALL FIVE actions occur inside ONE
        `process_once` after approval — which is the true production
        ordering, because the adapter binds on its own poll loop long
        before the Runtime materializes. The copy lets the SAME record
        bytes, the SAME Broker code and the SAME gate be exercised in
        the unbound state without disturbing that ordering.

        Returns `(pass_result, spawn_requests, entry_from_state_file)`.
        """
        probe_state = os.path.join(cls._tmp.name, "probe-state")
        probe_workspaces = os.path.join(cls._tmp.name, "probe-workspaces")
        shutil.copytree(cls.state_dir, probe_state)
        transport = FakeGitTransport({TARGET_URL: cls.target_fixture})
        role_turn = FakeRoleTurn(
            FakeRoleTurnResult(
                outcome="request_dispatch",
                turn={"turn_id": "turn-hv",
                      "role": "handoff_validation",
                      "process_id": 4242},
            )
        )
        spawn_requests = []

        def spawn_recorder(parent_repo, request):
            # If the gate is doing its job this is NEVER called.
            spawn_requests.append((parent_repo, dict(request)))
            raise AssertionError(
                "the placeholder gate must refuse initial dispatch"
                " while the placeholder is unbound; a spawn was"
                " requested for %r" % (request.get("target_repo"),)
            )

        from herdr.observe import observe
        broker = broker_module.TargetBroker(
            store_directory=probe_state,
            control_repository_realpath=cls.control,
            transport=transport,
            workspaces_root=probe_workspaces,
            role_turn_fn=role_turn,
            claude_config_path=cls.claude_config,
            spawn_fn=spawn_recorder,
            clock=lambda: NOW,
            observer_fn=lambda path: observe(path),
        )
        result = runtime_module.process_once(broker)
        entry = wa_store.WorkflowStore(probe_state).load()[
            "workflows"
        ]["wf-0001"]
        return result, spawn_requests, entry

    @classmethod
    def _build_runtime_broker(cls):
        from herdr.observe import observe
        transport = FakeGitTransport({TARGET_URL: cls.target_fixture})
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
            repo = os.path.join(
                os.path.realpath(cls.workspaces), "wf-0001"
            )
            return {
                "repo": repo,
                "initialization": None,
                "runtime": {"workspace_id": "target-w"},
                "task": {"id": "target-issue-42", "status": "ACTIVE"},
                "policy": {},
                "parent_repo": parent_repo,
                "child_record": {
                    "requested_at": NOW,
                    "parent_repo": parent_repo,
                    "parent_task_id": None,
                    "dependency": False,
                    "repo": repo,
                    "task_id": "target-issue-42",
                    "task_status": "ACTIVE",
                    "workspace_id": "target-w",
                    "agents": {},
                },
            }

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
            claude_config_path=cls.claude_config,
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
                "id": "target-issue-42", "status": "COMPLETE",
                "started_at": 1, "completed_at": 2,
                "description": "resolve external target issue #42",
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
                "# Task Checkpoint — target-issue-42\n\n"
                "## Outcome\nCOMPLETE. External target issue resolved.\n\n"
                "## Verification\n- target test suite green\n\n"
                "## Mutation evidence\n- 4/4 mutants KILLED\n"
            )
        reviews = os.path.join(state, "reviews")
        os.makedirs(reviews, exist_ok=True)
        with open(
            os.path.join(reviews, "target-issue-42-round-01.md"), "w"
        ) as f:
            f.write(
                "# Reviewer round 1\n\n"
                "Reviewer: `reviewer1` / `target-rev-session`\n\n"
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
        self.assertIn("Resolve issue #42 in the external target repository", text)
        self.assertIn(TARGET_URL, text)
        self.assertIn("issue #42", text)
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

    def test_point03b_initial_dispatch_is_refused_until_bound(self):
        # DI-REMOTE-3 R-6, POSITIVELY pinned rather than merely
        # survived. Approval stamps `result_placeholder = required` in
        # the same locked transaction that arms the mission (Layer 1),
        # and the Broker then REFUSES initial dispatch until that
        # placeholder is durably bound — so a mission can never run
        # without an object to deliver its verified result into.
        #
        # Every assertion reads the STATE FILE (`_fresh_entry` and the
        # probe's own freshly loaded store), never in-memory adapter
        # state.
        self.assertIsNotNone(
            self.placeholder_after_approval,
            "approval must durably REQUEST the placeholder",
        )
        self.assertEqual(
            self.placeholder_after_approval["state"],
            wa_record.PLACEHOLDER_REQUIRED,
        )
        self.assertIsNone(
            self.placeholder_after_approval["message_id"]
        )
        # The chain runs, and stops at the gate.
        actions = [a for a, _ in self.gated_pass["wf-0001"]]
        self.assertIn(broker_module.ACTION_DISPATCH, actions)
        refusals = [
            outcome for action, outcome in self.gated_pass["wf-0001"]
            if action == broker_module.ACTION_DISPATCH
        ]
        self.assertEqual(len(refusals), 1)
        self.assertFalse(refusals[0].ok)
        self.assertEqual(
            refusals[0].problem,
            broker_module.PROBLEM_PLACEHOLDER_NOT_BOUND,
        )
        # ZERO spawn requests: nothing was launched at the target.
        self.assertEqual(self.gated_spawn_requests, [])
        # The refusal wrote nothing that advanced the workflow: it is
        # TRANSIENT and self-healing, not a dead end.
        self.assertEqual(
            self.gated_entry["phase"], wa_record.PHASE_VALIDATED
        )
        self.assertEqual(
            self.gated_entry["result_placeholder"]["state"],
            wa_record.PLACEHOLDER_REQUIRED,
        )

    def test_point03c_the_adapter_binds_and_then_dispatch_proceeds(self):
        # The other half of R-6: the adapter's OWN loop step binds the
        # placeholder — the same `ensure_result_placeholders()` call
        # `Adapter.run` makes every poll iteration — and the Runtime
        # then dispatches normally. In production both loops run
        # concurrently, which is why the refusal above is transient.
        self.assertEqual(
            self.placeholder_after_binding["state"],
            wa_record.PLACEHOLDER_BOUND,
        )
        self.assertEqual(
            self.placeholder_after_binding["message_id"],
            self.PLACEHOLDER_MESSAGE_ID,
        )
        self.assertIsNotNone(
            self.placeholder_after_binding["bound_at"]
        )
        self.assertIsNotNone(
            self.placeholder_after_binding["text_digest"]
        )
        # EXACTLY ONE placeholder message was sent, and it carries the
        # workflow id so a human can identify the object in the chat
        # (strategy §1.3).
        self.assertEqual(len(self.placeholder_api.placeholder_sends), 1)
        sent = self.placeholder_api.placeholder_sends[0]
        self.assertIn("wf-0001", sent["text"])
        self.assertEqual(
            sent["chat_id"],
            self.offered_record["telegram"]["chat_id"],
        )
        # And with it bound, the very next Runtime pass dispatched —
        # the spawn the rest of this narrative depends on.
        self.assertEqual(len(self.spawn_requests), 1)
        self.assertEqual(
            self.after_pass1["result_placeholder"]["state"],
            wa_record.PLACEHOLDER_BOUND,
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
        self.assertEqual(
            sorted(request), ["alias", "preset", "target_repo", "task"]
        )
        self.assertEqual(
            request["preset"], dispatch_module.DI_TARGET_EXECUTION_PRESET
        )
        self.assertEqual(
            dispatch_module.DI_TARGET_EXECUTION_PRESET, "all-claude"
        )
        self.assertEqual(request["task"], TARGET_HANDOFF)
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
        # R-37 AB-5: this reads the PRESERVED projection, not the
        # workspace and not a test-local snapshot. Terminal cleanup
        # reclaims the directory, and this assertion passes BECAUSE
        # THE EVIDENCE SURVIVES cleanup — which is the property the
        # mission needs for independent inspection, and which cleanup
        # was destroying.
        preserved = preserve_module.preserved_text(
            self.state_dir, "wf-0001", "supervisor-strategy.md"
        )
        self.assertIsNotNone(
            preserved,
            "the target Supervisor's strategy did not survive"
            " terminal cleanup; an inspector has nothing to inspect",
        )
        self.assertIn(TARGET_SUPERVISOR_STRATEGY, preserved)
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
        for name, needle in (
            ("lead-evidence.md", TARGET_LEAD_EVIDENCE),
            ("executor-evidence.md", TARGET_EXEC_EVIDENCE),
            ("reviewer-evidence.md", TARGET_REVIEWER_SUMMARY),
        ):
            preserved = preserve_module.preserved_text(
                self.state_dir, "wf-0001", name
            )
            self.assertIsNotNone(
                preserved,
                "%s did not survive terminal cleanup" % name,
            )
            self.assertIn(needle, preserved)
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
        # RETARGETED under RULING R-16, and STRICTLY STRONGER than the
        # regression it replaces.
        #
        # This used to assert a FRESH send plus a DELIVERY_DELIVERED
        # marker — the pre-placeholder delivery model. Since R-12 this
        # workflow binds a real placeholder through the ordinary
        # adapter path, and since R-15 the legacy at-most-once lane is
        # gated on `result_placeholder is None`, so production delivers
        # this result by EDITING the bound object. A test asserting a
        # fresh send would pin a sequence production can no longer
        # perform.
        #
        # Every substantive claim of the original survives — the result
        # reaches Telegram exactly once, names the exact target, and
        # carries the Reviewer's own summary — now asserted against the
        # EDIT, plus the task's HEADLINE guarantee: exactly ONE visible
        # result object and ZERO fresh sends, end to end on a real
        # mission.
        #
        # Regression: no delivery, a double delivery, a fabricated
        # summary, or ANY fresh result message beside the bound one.

        # (0) The delivery step itself completed. R-11: this turns a
        # missing or broken edit seam into an AUTHORED failure here
        # rather than an ERROR cascade out of setUpClass.
        self.assertIsNone(
            self.delivery_error,
            "the delivery step must complete; it raised %r"
            % (self.delivery_error,),
        )

        # (1) ZERO fresh result sends. Asserted on the harness send log
        # AND on the client's raw TRANSPORT CALL LOG — a state-only
        # check would not have caught R-15, which is exactly why this
        # is asserted here.
        delivered = [
            s for s in self.harness.sends()
            if adapter_module.RESULT_MESSAGE_HEADER in s["text"]
        ]
        self.assertEqual(
            delivered, [],
            "a placeholder-bound workflow must NEVER receive a fresh"
            " result message; the result is delivered by editing the"
            " bound placeholder",
        )
        self.assertEqual(
            len(self.harness.sends()), self.sends_before_delivery,
            "delivery must not add ANY Telegram message",
        )
        sent_methods = [
            method for method, _ in
            self.placeholder_api.transport_calls
        ]
        self.assertNotIn(
            "sendMessage", sent_methods[self._edit_call_floor():],
            "the transport call log shows a fresh sendMessage on the"
            " result path: %r" % (sent_methods,),
        )

        # (2) EXACTLY ONE edit, targeting the BOUND object.
        self.assertEqual(
            len(self.placeholder_api.edit_calls), 1,
            "exactly one edit delivers the result: %r"
            % (self.placeholder_api.edit_calls,),
        )
        edit = self.placeholder_api.edit_calls[0]
        self.assertEqual(edit["message_id"], self.PLACEHOLDER_MESSAGE_ID)
        self.assertEqual(
            edit["chat_id"],
            self.offered_record["telegram"]["chat_id"],
        )

        # (3) The EXACT target identity and the Reviewer's own summary
        # — the original assertions, now against the edited text.
        self.assertIn(TARGET_REVIEWER_SUMMARY, edit["text"])
        self.assertIn(TARGET_URL, edit["text"])
        self.assertIn("issue #42", edit["text"])
        # R-1: no clock, no attempt counter reached the payload, and no
        # parse_mode/reply_markup rides with it.
        self.assertEqual(
            sorted(edit), ["chat_id", "message_id", "text"]
        )

        # (4) The durable marker, read from the STATE FILE.
        on_disk = wa_store.WorkflowStore(self.state_dir).load()
        marker = on_disk["workflows"]["wf-0001"]["result_delivery"]
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT
        )
        self.assertEqual(
            marker["edited_message_id"], self.PLACEHOLDER_MESSAGE_ID
        )
        # The LEGACY key keeps its own meaning and is NOT repurposed.
        self.assertIsNone(marker["telegram_message_id"])
        # R-4: the delivery is bound to THIS verified result.
        self.assertEqual(
            marker["verified_result_digest"],
            on_disk["workflows"]["wf-0001"]["verified_result"]["digest"],
        )
        # And the placeholder it edited is the one that was bound.
        placeholder = (
            on_disk["workflows"]["wf-0001"]["result_placeholder"]
        )
        self.assertEqual(
            placeholder["state"], wa_record.PLACEHOLDER_BOUND
        )
        self.assertEqual(
            placeholder["message_id"], self.PLACEHOLDER_MESSAGE_ID
        )

    def _edit_call_floor(self):
        """Index of the first transport call made by the DELIVERY
        step, so the assertion above covers the result path only and
        not the earlier placeholder bind."""
        for index, (method, payload) in enumerate(
            self.placeholder_api.transport_calls
        ):
            if "message_id" in payload:
                return index
        return len(self.placeholder_api.transport_calls)

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
        # R-33 added the release: reaching COMPLETED now also reclaims
        # the leased workspace in the SAME pass. Its absence was the
        # finding — no production caller invoked release_workspace, so
        # a finished workflow left its workspace and sessions behind,
        # which is why orphans accumulated over days.
        self.assertEqual(
            actions_pass2,
            [broker_module.ACTION_VERIFY,
             broker_module.ACTION_COMPLETE,
             broker_module.ACTION_RELEASE],
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


class _SendMessageTextExtractor(html.parser.HTMLParser):
    """Round 5 (supervisor ruling 06): the stdlib HTML parser is the ONLY
    markup handling in the detector — no hand-written tag or comment
    regex anywhere. Quoting is handled by the parser itself, so a `>`
    inside a quoted attribute cannot end a tag early, and comments,
    declarations, processing instructions and CDATA/marked sections are
    parser events that contribute no text. convert_charrefs=True decodes
    entities in text (html.unescape also runs first, so an ESCAPED tag
    such as `send&lt;b&gt;Message` collapses instead of keeping letters).

    Three views are built from the parser's events (a fourth, the
    MARKDOWN-VISIBLE view, is derived from the VISIBLE one by _views):
      * the CLEAN view — exactly the character data handed to
        handle_data (residue included verbatim); tags and their
        attributes contribute nothing;
      * the FAIL-CLOSED view — the same data PLUS, for every start tag,
        its raw text minus the leading tag-name word, and with the
        parser's incomplete-markup residue (a lone `<` data char followed
        by data beginning with a letter, `/`, `!` or `?`) minus that
        first word;
      * the VISIBLE view (round 6) — the CLEAN view MINUS the content of
        HIDDEN_CONTENT_ELEMENTS, tracked by element depth, so the visible
        halves around a hidden element JOIN (`send<style>x</style>Message`
        -> `sendMessage`). The set errs toward MORE exclusion: the two
        raw-text elements HTMLParser itself treats as CDATA (script,
        style), template content, document metadata (head, title), and
        elements whose text is not rendered as prose (textarea, noscript,
        iframe, noframes, noembed). Excluding more can only ADD recall,
        because a token found in ANY view counts. Nesting is a depth
        counter; a stray end tag never goes below zero. An UNCLOSED hidden
        element has an unknown extent, so its content (including what the
        parser left unconsumed in CDATA mode) is reported separately via
        unclosed_hidden() and the detector admits every cut point of it —
        `send<style>xMessage` is caught by joining `send` with the
        `Message` suffix.
    A token found in EITHER view counts. FAILURE DIRECTION for malformed
    markup, stated not assumed: Python's HTMLParser never drops text it
    could not parse, but it does two things that would HIDE a token —
    at end of input an unterminated tag token (`send<span Message`)
    comes back as `<` plus ordinary text, so the tag-name letters land
    between the halves; mid-document the same tag is tolerated up to the
    next `>` and everything inside becomes attributes (`send<span
    Message twice. later <b>`), as a browser would. The fail-closed view
    exists for exactly those two shapes: attribute text and residue are
    re-admitted with only the tag-name word removed, so the detector
    degrades toward OVER-approximation, never toward hiding. A bare `<`
    in prose (SECURITY's `| < 0.7 | No |` cell) is a lone `<` followed by
    a space, which is not residue; it strips harmlessly. On well-formed
    text without attribute junk or hidden elements the views are
    identical.

    The parser also records every START TAG's exact raw text
    (get_starttag_text(), self-closing included) in document order, for
    the closed start-tag allowlist (supervisor ruling 07) that is the
    OUTER gate on markup: see ALLOWED_START_TAGS."""

    HIDDEN_CONTENT_ELEMENTS = frozenset((
        "script", "style", "template", "head", "title", "textarea",
        "noscript", "iframe", "noframes", "noembed",
    ))

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.start_tags = []
        self._hidden_depth = 0
        self._unclosed_from = None

    @staticmethod
    def _minus_head(text):
        parts = text.split(None, 1)
        return parts[1] if len(parts) > 1 else ""

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text() or ""
        self.start_tags.append(raw)
        self.chunks.append(("attrs", self._minus_head(raw), False))
        if tag in self.HIDDEN_CONTENT_ELEMENTS:
            if self._hidden_depth == 0:
                self._unclosed_from = len(self.chunks)
            self._hidden_depth += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.HIDDEN_CONTENT_ELEMENTS and self._hidden_depth > 0:
            self._hidden_depth -= 1
            if self._hidden_depth == 0:
                self._unclosed_from = None

    def handle_data(self, data):
        after_lone_lt = (
            bool(self.chunks) and self.chunks[-1][:2] == ("data", "<")
        )
        hidden = self._hidden_depth > 0
        if after_lone_lt and data[:1] and (
                data[0].isalpha() or data[0] in "/!?"):
            self.chunks.append(("residue", data, hidden))
        else:
            self.chunks.append(("data", data, hidden))

    def views(self):
        clean = "".join(
            text for kind, text, _ in self.chunks
            if kind in ("data", "residue")
        )
        fail_closed = "".join(
            self._minus_head(text) if kind == "residue" else text
            for kind, text, _ in self.chunks
        )
        visible = "".join(
            text for kind, text, hidden in self.chunks
            if kind in ("data", "residue") and not hidden
        )
        return (clean, fail_closed, visible)

    def unclosed_hidden(self):
        """(visible text before the unclosed hidden element, its content
        to end of input) when a hidden element was never closed, else
        None. The CDATA leftover HTMLParser keeps in rawdata at close()
        is part of that content."""
        if self._unclosed_from is None:
            return None
        before = self.chunks[:self._unclosed_from]
        prefix = "".join(
            text for kind, text, hidden in before
            if kind in ("data", "residue") and not hidden
        )
        tail = "".join(
            text for kind, text, _ in self.chunks[self._unclosed_from:]
            if kind in ("data", "residue")
        ) + self.rawdata
        return (prefix, tail)


def _extract(text):
    parser = _SendMessageTextExtractor()
    parser.feed(html.unescape(text))
    parser.close()
    return parser


def _normalize(view):
    return re.sub(r"[^a-z0-9]", "", view.lower())


# Round 7 (supervisor ruling 09): the MARKDOWN-VISIBLE view. Derived from
# the VISIBLE text by dropping the Markdown link/reference machinery a
# renderer does not display —
#   `[label]: ...`    -> ``    reference-definition line (whole line)
#   `](destination)`  -> `]`   inline link / image destination
#   `][label]`        -> `]`   reference label
#   `[^label]`        -> ``    footnote reference
# — then the ruling-05 normalize core. `[send](#delivery)Message` renders
# as sendMessage but read `senddeliverymessage` in every other view; here
# it reads `sendmessage`. This is NOT a Markdown renderer and there is no
# Markdown allowlist: any construct not listed stays as it is.
#
# ONE PASS, NOT FOUR (round-7 lead finding): applying the substitutions
# as successive passes let the `]` produced by dropping one destination
# form a `][Message]` that the NEXT pass read as a reference label, so
# two ADJACENT links `[send](#a)[Message](#b)` lost the text between
# them (view truncated to `...send`). The three inline constructs are
# therefore matched in a SINGLE alternation pass — re.sub never re-scans
# replaced text — and every alternative is bounded by a character class
# that cannot cross its own closing delimiter (`[^()]`, `[^\]]`), so
# adjacent constructs match separately and the text between them
# survives. A destination may contain ONE level of balanced parentheses
# (`(#a(b))`); deeper nesting leaves the tail of the destination in the
# text (over-approximation only: it can add letters, never remove the
# visible halves). Definition lines are removed first, line-wise.
# FAILURE DIRECTION: a substitution that does not match leaves the text
# exactly as the VISIBLE view already has it, and "token in ANY view
# counts" means this view can only ADD recall — it degrades toward
# OVER-approximation (a dropped destination or definition can only join
# neighbouring text), never toward hiding.
_MARKDOWN_DEFINITION_LINE = re.compile(r"^\s*\[[^\]]+\]:\s.*$", re.M)
_MARKDOWN_INLINE_MACHINERY = re.compile(
    r"\]\((?:[^()]|\([^()]*\))*\)"     # ](destination), one nested level
    r"|\]\[[^\]]*\]"                     # ][label]
    r"|(?P<footnote>\[\^[^\]]*\])"       # [^label]
)


def _markdown_visible(text):
    text = _MARKDOWN_DEFINITION_LINE.sub("", text)
    return _MARKDOWN_INLINE_MACHINERY.sub(
        lambda match: "" if match.group("footnote") else "]", text
    )


def _views(parser):
    """CLEAN, FAIL-CLOSED, VISIBLE, and MARKDOWN-VISIBLE (the VISIBLE
    text with Markdown link/reference machinery dropped)."""
    clean, fail_closed, visible = parser.views()
    return (clean, fail_closed, visible, _markdown_visible(visible))


def sendmessage_normalized_views(text):
    """The DETECTOR's views of document text: markup removed by the
    stdlib parser above, then the ruling-05 core — lower-cased with EVERY
    non-[a-z0-9] character stripped. A unit mentions sendMessage iff any
    view contains the token "sendmessage". That is recall, not pattern
    matching: `sendMessage`, send**Message**, send_message, send-message,
    "send message", send.Message, SENDMESSAGE, send<b>Message</b>,
    send&#77;essage, send&nbsp;Message, send<style>x</style>Message, and
    any other Markdown/HTML/punctuation split, in any word order and
    casing, collapse to the same token. It deliberately OVER-approximates
    (stripping everything can join adjacent words, e.g. "...to send
    messages twice" also contains the token); that direction is
    fail-closed. These views are used ONLY to classify; the units that
    are digested are the real document units."""
    return tuple(_normalize(view) for view in _views(_extract(text)))


SENDMESSAGE_TOKEN = "sendmessage"


def _unclosed_hidden_hits(parser):
    """Token occurrences admitted by an UNCLOSED hidden element: its
    extent is unknown, so every cut point of its content is allowed —
    the token counts if it lies in the content, or straddles the join
    between the visible prefix and some suffix of the content."""
    unclosed = parser.unclosed_hidden()
    if unclosed is None:
        return 0
    prefix, tail = (_normalize(part) for part in unclosed)
    token = SENDMESSAGE_TOKEN
    straddles = any(
        prefix.endswith(token[:split]) and token[split:] in tail
        for split in range(1, len(token))
    )
    return prefix.count(token) + tail.count(token) + int(straddles)


def mentions_sendmessage(text):
    """True iff any detector view of ``text`` contains the token, or an
    unclosed hidden element admits it."""
    parser = _extract(text)
    return any(
        SENDMESSAGE_TOKEN in _normalize(view) for view in _views(parser)
    ) or _unclosed_hidden_hits(parser) > 0


def sendmessage_count(text):
    """Occurrences of the token, taken as the MAX over the detector views
    and the unclosed-hidden admission (over-approximation is the
    fail-closed direction for a count that is compared for equality
    against the enumerated cardinality)."""
    parser = _extract(text)
    return max(
        [_normalize(view).count(SENDMESSAGE_TOKEN) for view in _views(parser)]
        + [_unclosed_hidden_hits(parser)]
    )


def sendmessage_start_tags(text):
    """Every start tag the parser emits for ``text``, as exact raw text,
    in document order (self-closing tags included)."""
    return list(_extract(text).start_tags)


# Closed START-TAG allowlist (supervisor ruling 07) — the OUTER gate on
# markup, and an identity pin, not prose evidence: every start tag the
# stdlib parser emits for the document (exact raw text, self-closing
# included) must equal this ordered list — membership, order AND
# cardinality. Derived verbatim from the documents as they stand. Any
# other element — style, script, template, noscript, svg, iframe,
# details, div, span, a conditional-comment host, anything — fails BY
# CONSTRUCTION rather than by enumerating rendering rules; so does an
# allowed tag with an added attribute, a reordered tag, or a duplicate.
# End tags need no allowlist: an end tag never hides text (the parser
# drops it and the halves join). Re-derive ONLY when a document's markup
# is deliberately changed and re-reviewed.
ALLOWED_START_TAGS = {
    "README.md": (
        '<p align="center">',
        '<img src="assets/brand/banner.svg" alt="Dodging Infinity — Bounding the infinite to the finite." width="100%">',
        '<token from @BotFather>',
        '<intent>',
        '<task>',
    ),
    "SECURITY.md": (
        '<config>',
        '<config>',
        '<config>',
    ),
}


# Identity pin (NOT prose evidence) for test_exactly_once_discloses_the_
# not_retried_states: the complete, ordered set of document units that
# mention sendMessage AT ALL (per mentions_sendmessage), one digest per
# unit, computed by unit_digest over test_workspace_trust.document_units.
# Each pinned unit carries exactly one occurrence, and it is a negated
# "no second sendMessage" one; re-derive a digest ONLY when its sentence
# is deliberately changed and re-reviewed, the same discipline as the
# I1/I8 maps.
#
# DOMAIN BOUND — stated precisely, not overstated. This pin claims: every
# occurrence of the normalized "sendmessage" TOKEN in reader-visible text
# as recovered by the stdlib HTML parser — with the content of
# non-rendered elements (HIDDEN_CONTENT_ELEMENTS) excluded so the visible
# halves around it join, with attribute text and malformed-markup residue
# re-admitted fail-closed, with Markdown link/reference machinery dropped
# (_MARKDOWN_INLINE_MACHINERY), and with every start tag gated by the closed
# ALLOWED_START_TAGS list so that any element outside it fails by
# construction.
#
# NOT claimed — CLASS CLOSURE (supervisor ruling 09, superseding ruling
# 08 §3(ii)): after round 7 of task 20260901-225712, EVERY rendered-text
# evasion of ANY syntax family — HTML, Markdown (any construct not listed
# above), Unicode (confusables, homoglyphs, zero-width joiners, bidi
# controls, combining marks, font tricks), or any MIXTURE of them — is a
# RECORDED RESIDUAL, not a defect. So is a contradictory paraphrase that
# never uses the token at all (e.g. "a second message is sent"). THREAT
# MODEL: an honest editor documenting a second-send fallback in prose.
# LOAD-BEARING CONTROLS: the anchored positive pin, the identity-pinned
# closed mention set with cardinality, the closed start-tag allowlist,
# and human claim review.
#
# MAINTENANCE COST, recorded: the allowlists and digests make this pin
# sensitive to ANY future edit that adds a link, footnote, or tag to
# these two documents, or touches one of the four mention-bearing units —
# such an edit fails here until its entry is deliberately updated and
# re-reviewed, the same discipline as the I1/I8 maps.
SENDMESSAGE_MENTION_UNITS = {
    "README.md": (
        # "**Telegram result** — ... can never fall back to a second
        # result `sendMessage`." (principal-flow bullet)
        "12ff6a9e14f2b616bcd5542a4e934c91f680145222595d94f877cc7aca972d1e",
        # "Approval is **one-shot** ... a placeholder-bound workflow can
        # never fall back to a second result `sendMessage`, so the result
        # is **not re-sent automatically** ..." (exactly-once caveat)
        "ffbd6266ae2091f31b5dddeb6bf2a74a0a9a1b9922576d1a4f77a1a5e3144229",
    ),
    "SECURITY.md": (
        # "Final-result delivery uses one dedicated bot-owned Telegram
        # placeholder ... can never fall back to a second result
        # `sendMessage`; ..." (Completion bullet, delivery paragraph)
        "d8a9cf23001bf5eb0e181cfaf3259a03c5f6f9cda8ee2da09bdce8ef3e2a621b",
        # "Exactly once means never twice ... Because there is no
        # second `sendMessage`, the result is not re-sent automatically
        # ..." (Completion bullet, caveat paragraph)
        "8f6d2639731fe2334ce759dbcd33a12fa9693e26695984a58630c4488f7a8d7e",
    ),
}


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

    def test_di_remote_2_is_claimed_for_v070_not_v063(self):
        changelog = read_doc("CHANGELOG.md")
        self.assertIn("## Unreleased", changelog)
        before_v063, marker, v063_and_older = changelog.partition("## v0.6.3")
        self.assertTrue(marker, "v0.6.3 section missing")
        self.assertIn("## v0.7.0", before_v063)
        self.assertIn("DI-REMOTE-2", before_v063)
        # Historical accuracy: v0.6.3 and older release sections do not
        # retroactively claim the v0.7.0 capability.
        self.assertNotIn("DI-REMOTE-2", v063_and_older)

    def test_structure_only_limit_is_disclosed(self):
        self.assertIn(
            "validates STRUCTURE only", read_doc("SECURITY.md")
        )

    def test_operator_protocol_documents_the_v2_envelope(self):
        doc = read_doc("OPERATOR_PROTOCOL.md")
        self.assertIn("DI-REMOTE-2 RESPONSE", doc)
        self.assertIn("mission_authorization", doc)
        self.assertIn("role_outcome", doc)

    # The release gate is SEVEN steps, in one exact order. This pin is
    # POSITIONAL: each anchor must be found AFTER the previous one, so
    # restoring the old six-step ordering (or reordering two steps)
    # fails it. It uses find() plus authored messages, never index(),
    # so a missing anchor is a KILL and not a ValueError crash.
    RELEASE_GATE_SEQUENCE = (
        "Complete the README and documentation reconciliation",
        "Repair clean-clone CI hermeticity",
        "historical external-target mountain",
        "Integrate the reviewed and pushed Runtime stabilization commit",
        "Complete final DI-REMOTE-2 certification",
        "Prepare the v0.7.0 release tree",
        "Prove the exact v0.7.0 release tree green in CI",
    )

    def test_remote_fabric_roadmap_release_gate_is_ordered(self):
        roadmap = read_doc("docs/remote-mission-fabric-roadmap.md")
        gate = roadmap.find(
            "## Immediate release gate: DI-REMOTE-2 acceptance before Phase I"
        )
        phase = roadmap.find("# Phase I: Remote Mission Fabric")
        self.assertGreaterEqual(gate, 0, "release-gate heading missing")
        self.assertGreaterEqual(phase, 0, "Phase I heading missing")
        self.assertLess(
            gate, phase, "the release gate must precede Phase I"
        )
        section = roadmap[gate:phase]
        self.assertEqual(
            len(self.RELEASE_GATE_SEQUENCE), 7,
            "the release gate is a SEVEN-step ordering",
        )
        # Hard-wrapped prose: compare on the normalized section so a
        # re-wrap cannot silently break an anchor.
        flat_section = flat(section)
        position = 0
        for index, claim in enumerate(self.RELEASE_GATE_SEQUENCE, start=1):
            found = flat_section.find(flat(claim), position)
            self.assertGreaterEqual(
                found, 0,
                "release-gate step %d missing or out of order: %r"
                % (index, claim),
            )
            position = found + len(flat(claim))
        # All seven acceptance/CI steps are complete. Tag publication
        # remains a separate human-gated action rather than part of this
        # checklist's proof state.
        for done_step in ("1.  [x]", "2.  [x]", "3.  [x]", "4.  [x]",
                          "5.  [x]", "6.  [x]", "7.  [x]"):
            self.assertIn(done_step, section, done_step)
        # The gate carries the CI evidence and the stabilization SHA,
        # and the STALE run id is gone from the whole roadmap.
        self.assertIn(CI_RUN_ID, section)
        self.assertIn(STABILIZATION_SHA, section)
        self.assertNotIn(CI_STALE_RUN_ID, roadmap)
        flat_section = " ".join(section.replace("> ", "").split())
        self.assertIn(
            "DI-REMOTE-2 acceptance is complete when the public repository,"
            " exact release commit CI, canonical review evidence, and"
            " authoritative unchanged-tree test run all describe the same"
            " bounded system.",
            flat_section,
        )

    def test_remote_fabric_roadmap_pins_exact_delivery_capabilities(self):
        roadmap = read_doc("docs/remote-mission-fabric-roadmap.md")
        start = roadmap.index(
            "## Iteration 5: Telegram exact delivery authority and Git"
            " decision surfaces"
        )
        end = roadmap.index("## Iteration 6:", start)
        section = roadmap[start:end]
        for claim in (
            "Mission Authorization grants **ZERO delivery authority**",
            "Each delivery action is an independent one-shot capability",
            "exact staged bytes/digest",
            "exact resulting commit SHA, remote/ref, expected remote state",
            "exact source commit, destination, title/body digest",
            "exact PR, head SHA, base, merge method, required checks/reviews state",
            "Release binds the exact tag/commit and release body/artifact digests",
            "Deploy binds the exact immutable revision and environment",
        ):
            self.assertIn(claim, " ".join(section.split()), claim)

    def test_remote_fabric_console_and_release_milestone_are_explicit(self):
        roadmap = read_doc("docs/remote-mission-fabric-roadmap.md")
        console = roadmap[
            roadmap.index("## Iteration 13:"):
            roadmap.index("# Phase IV:")
        ]
        for control in (
            "mission selection", "status", "artifacts",
            "blocked-condition acknowledgement", "permitted recovery",
            "mission approval/rejection", "exact diff inspection",
            "`Prepare commit`", "`Approve commit`", "`Approve push`",
            "`Open PR` / PR update", "`Approve merge`",
            "tag/release/deploy approval when enabled",
            "authorization history", "expiry/replay state",
            "exact-result receipts",
        ):
            self.assertIn(control, console, control)
        milestone = roadmap.index("## Milestone B: DI-REMOTE-2 released")
        multi = roadmap.index("## Milestone C: Multi-mission operation")
        self.assertLess(milestone, multi)
        self.assertIn(
            "The v0.7.0 release tree is the baseline for Mission Router"
            " and concurrency work.",
            " ".join(roadmap[milestone:multi].split()),
        )

    def test_remote_fabric_roadmap_release_tree_state_is_current(self):
        roadmap = read_doc("docs/remote-mission-fabric-roadmap.md")
        flat_roadmap = " ".join(roadmap.split())
        self.assertIn(
            "DI-REMOTE-2 acceptance is complete for the v0.7.0 release tree",
            flat_roadmap,
        )
        self.assertIn(
            "cda06d8c502882672667d94821b8bd00e7060a52", roadmap
        )
        self.assertIn("break-glass Tailscale/SSH", flat_roadmap)
        self.assertIn("release tagging is governed separately by its human authorization gate", flat_roadmap)
        self.assertNotIn("not yet proven stable", flat_roadmap)
        self.assertNotIn("final external-target lifecycle acceptance", flat_roadmap)
        self.assertNotIn(
            "still-unreached DI-REMOTE-2 tagged release", flat_roadmap
        )

    def test_contributing_lists_the_new_components(self):
        doc = read_doc("CONTRIBUTING.md")
        for token in (
            "workflow_authority/", "target_runtime/", "dirun.py",
            "operator_session/",
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

    # RETARGETED (was test_live_proven_and_pending_items_are_disclosed,
    # which asserted "live-proven through active"). That framing is now
    # FALSE: the mountain is terminal. A green test asserting a stale
    # property is worse than no test, so the name moved with the claim.
    def test_terminal_outcome_and_release_evidence_boundary_are_disclosed(self):
        blob = flat_lower(
            read_doc("README.md") + read_doc("SECURITY.md")
            + read_doc("CHANGELOG.md")
        )
        self.assertIn(flat_lower(TERMINAL_ANCHOR), blob)
        self.assertIn("telegram", blob)
        self.assertIn("github", blob)
        self.assertIn("reviewer approve", blob)
        self.assertIn("verified_result", blob)
        self.assertIn("result_delivery", blob)
        self.assertIn("final-result release certification", blob)
        self.assertIn("verified", blob)
        self.assertIn("completed", blob)
        self.assertIn("exactly-once", blob)
        self.assertIn("fresh post-fix live mountain", blob)
        self.assertIn("not used as release evidence", blob)
        self.assertIn("artifact delivery", blob)
        self.assertIn("delivery_authority = none", blob)

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
        # Round-13 F-3, retargeted for the DI-REMOTE-3 placeholder lane
        # (task 20260901-225712): "exactly once" is misleading by
        # omission unless both docs disclose which durable shapes are
        # NOT re-sent. The principal path is the bound bot-owned
        # placeholder plus edit, so the disclosed states are the
        # go-forward ones, named FROM production
        # (workflow_authority.record) so this pin cannot drift from the
        # code: the ambiguous edit outcome (retried only as an idempotent
        # edit, never a send), the two degraded terminals, and the
        # terminal placeholder-creation state.
        #
        # ADDED CONTROL, doc side: both docs must state that a
        # placeholder-bound workflow can never fall back to a second
        # result `sendMessage`; documenting a second-send fallback would
        # fail here. Code side of the same control:
        # tests/test_di_remote_3_delivery.py DeterminismTests.
        # test_R2_edit_payload_has_no_parse_mode_or_reply_markup asserts
        # exactly ONE transport payload carries RESULT_MESSAGE_HEADER
        # together with a message_id — an edit, never a send.
        #
        # VACUITY GUARD: the bare word "partial" is deliberately NOT
        # asserted — it occurs incidentally inside the R-6 phrase
        # "agents-unprobed global PARTIAL". Legacy vocabulary is accepted
        # only inside an explicit legacy-lane framing that labels the
        # lane AT-MOST-ONCE (the adapter's own legacy-lane docstring is
        # the source of truth for that label).
        go_forward_states = (
            wa_record.DELIVERY_EDIT_INDEFINITE,
            wa_record.DELIVERY_DEGRADED_UNRENDERABLE,
            wa_record.DELIVERY_DEGRADED_UNBINDABLE,
            wa_record.PLACEHOLDER_INDEFINITE,
        )
        legacy_framing = "legacy lane (`reserved` / `partial`)"
        for name in ("README.md", "SECURITY.md"):
            # Normalize wrapping so line breaks inside a phrase don't
            # hide it.
            doc = " ".join(read_doc(name).lower().split())
            self.assertIn("re-sent automatically", doc, name)
            # "exactly once" must never stand alone as the whole story:
            # the not-arrives caveat appears in the same document.
            self.assertIn("not that it always eventually arrives", doc, name)
            for state in go_forward_states:
                # Backticked, so the placeholder state `indefinite`
                # cannot be satisfied by the tail of `edit_indefinite`.
                self.assertIn("`%s`" % state, doc, (name, state))
            self.assertIn(
                "can never fall back to a second result `sendmessage`",
                doc, name,
            )
            # CLOSED MENTION SET (round 3, reviewer D2 / supervisor
            # ruling 04). Presence of the negated sentence is not enough,
            # and neither is inferring meaning from the characters before
            # each mention: "can fall back ...", "It is false that ... can
            # never fall back ...", and "no guarantee that there will be
            # no second sendMessage" all defeated the earlier shapes. So
            # NO semantics are inferred. The document is split with the
            # ONE canonical splitter (test_workspace_trust.document_units,
            # the same one the I1/I8 fixtures use), every unit that
            # mentions sendMessage at all — any word order, casing, or
            # delimiting — is collected, and that list must EQUAL, in order and in
            # number, the enumerated allowed units for this document.
            # The allowed units are pinned by identity (unit_digest =
            # sha256 of the whitespace-flattened, lower-cased unit), so
            # the prose stays in the document and nothing is retyped
            # here. Closed by construction: an added sentence of ANY
            # wording is an extra mention-bearing unit (or changes an
            # allowed unit's identity), a verbatim benign duplicate
            # breaks the cardinality, and a removed required sentence
            # leaves the list short. The whole-document mention count is
            # asserted too, so a mention that lands inside an existing
            # unit cannot hide (it changes that unit's digest as well).
            # Round 4: the classifier is "does the unit contain the
            # normalized sendmessage token AT ALL" — no word-order
            # pattern (round 3's `second (result) sendMessage` regex was
            # an under-approximation: "may call sendMessage twice" and
            # send**Message** were invisible to it).
            raw = read_doc(name)
            mention_units = [
                unit for unit in document_units(raw)
                if mentions_sendmessage(unit)
            ]
            self.assertEqual(
                [unit_digest(unit) for unit in mention_units],
                list(SENDMESSAGE_MENTION_UNITS[name]),
                (name, [" ".join(unit.split())[:100]
                        for unit in mention_units]),
            )
            self.assertEqual(
                sendmessage_count(raw),
                len(SENDMESSAGE_MENTION_UNITS[name]),
                name,
            )
            # Ruling 07 outer gate: the document's start tags are exactly
            # the enumerated ones, in order (see ALLOWED_START_TAGS).
            self.assertEqual(
                sendmessage_start_tags(raw),
                list(ALLOWED_START_TAGS[name]),
                name,
            )
            self.assertIn(legacy_framing, doc, name)
            legacy_at = doc.index(legacy_framing)
            self.assertIn(
                "at-most-once", doc[legacy_at:legacy_at + 160], name
            )

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
                      "dirun.py", "operator_session/"):
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
        #
        # ANCHORED (task 20260901-225712 round 2, reviewer D1): a bare
        # "source-scoped" was vacuous for SECURITY.md, which also says
        # "observations source-scoped supported" in the unrelated R-3
        # recovery paragraph — deleting the R-6 framing left the pin
        # green. The phrase is now pinned in its R-6 shape, which every
        # document in ALL_DOCS carries verbatim: the completeness noun,
        # the term, and the ruling citation together. The R-3 wording
        # cannot satisfy it.
        r6_anchor = "observation completeness is source-scoped (ruling r-6)"
        for name in self.ALL_DOCS:
            flat = self.flat(name).lower()
            self.assertIn(r6_anchor, flat, name)
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


class ReadmePrincipalFlowPinTests(unittest.TestCase):
    """WS-B (task 20260826-211424): the README's release framing and
    principal DI-REMOTE-2 flow, pinned in the same edit that added
    them (the recorded normative-prose rule). doc<->code pins parse
    the document and assert against the code; framing pins assert
    exact load-bearing phrases whose code side is cited. The full
    claim->pin map lives in the WS-B evidence artifact."""

    @staticmethod
    def flat():
        return " ".join(read_doc("README.md").split())

    def test_v070_release_framing(self):
        readme = self.flat()
        self.assertIn("# Dodging Infinity v0.7.0", readme)
        self.assertIn(
            "v0.7.0 adds DI-REMOTE-2 Remote Target Repository Routing",
            readme,
        )
        self.assertIn(
            "fresh post-fix live mountain is not used as release evidence",
            readme,
        )
        self.assertNotIn(
            "v0.6.3 remains the latest tagged release", readme
        )
        self.assertNotIn("DI-REMOTE-2, unreleased", readme)

    def test_principal_flow_is_complete_and_ordered(self):
        # The principal architecture section shows the COMPLETE
        # remote-target flow, every component present and IN ORDER.
        # RETARGETED in I3: the section was promoted from a "###
        # Remote target routing" subsection to the leading "##
        # Current architecture on `main`" section; the marker moved
        # with it (retarget proven by re-running the falsifying
        # experiments — see the I3 evidence pin ledger).
        readme = self.flat()
        marker = ("## Current architecture on `main`: remote target"
                  " routing (DI-REMOTE-2")
        self.assertIn(marker, readme)
        section = readme[readme.index(marker):]
        tokens = (
            "control repository",
            "fresh Codex turns",
            "Runtime (`dirun`",
            "Broker (privileged",
            "managed target workspace",
            "target Herdr (Supervisor -> Lead -> Executor / Reviewer)",
            "evidence verification (a verified result is gated, not"
            " declared)",
            "Telegram result",
            "human delivery gate",
        )
        position = 0
        for token in tokens:
            found = section.find(token, position)
            self.assertGreaterEqual(
                found, 0,
                "principal-flow component missing or out of order:"
                " %r" % token,
            )
            position = found + len(token)

    def test_broker_contract_matches_code(self):
        # doc<->code: the fixed-action count is derived from
        # BROKER_ACTIONS, and the documented perform surface is the
        # COMPLETE parameter list — nothing filtered, so a parameter
        # added, removed, or renamed (capability included) fails this
        # pin (round-05 F-1: the earlier defaulted-parameter filter
        # excluded exactly the parameter that falsified the sentence).
        readme = self.flat()
        count_words = {8: "eight", 9: "nine", 10: "ten"}
        self.assertIn(
            "%s fixed lifecycle actions"
            % count_words[len(broker_module.BROKER_ACTIONS)],
            readme,
        )
        signature = inspect.signature(
            broker_module.TargetBroker.perform
        )
        self.assertEqual(
            list(signature.parameters),
            ["self", "workflow_id", "action", "revision",
             "capability"],
        )
        self.assertIn(
            "`(workflow_id, action, revision, capability)`", readme
        )
        # The capability's binding tuple, stated next to it.
        self.assertIn(
            "bound to exactly that `(workflow_id, action, revision)`"
            " tuple",
            readme,
        )

    def test_runtime_coupling_framing(self):
        # framing: ruling E-1 — the Runtime couples to the control
        # chain only through the durable workflow authority store,
        # AND the in-process half of the same bullet (round-05 F-2):
        # Telegram, the Gateway, and Codex never invoke it
        # in-process. Code side: the tests/test_static.py
        # authority-boundary checks (target_runtime import anywhere
        # in the control chain fails them).
        flat = self.flat().lower()
        self.assertIn(
            "coupled to the control chain only through the durable"
            " workflow authority store",
            flat,
        )
        self.assertIn("never invoke it in-process", flat)

    def test_broker_caller_cannot_supply_sensitive_values(self):
        # doc<->code-docstring (round-05 F-2): the security sentence
        # "never supplied by the caller" against perform's own stated
        # contract; the enforced behavior is tested in
        # tests/test_target_runtime.py action-handler tests.
        self.assertIn(
            "never supplied by the caller", self.flat().lower()
        )
        self.assertIn(
            "caller has no way to supply",
            inspect.getdoc(broker_module.TargetBroker.perform),
        )

    def test_workspace_verification_framing(self):
        # doc<->code-docstring (round-05 F-2): the three named
        # materialize-time verifications against
        # workspace.materialize's stated contract; the enforced
        # behavior is tested in tests/test_target_runtime.py
        # (containment / remote-mismatch / baseline-mismatch
        # refusals).
        self.assertIn(
            "containment, canonical-remote, and baseline"
            " verification",
            self.flat().lower(),
        )
        materialize_doc = inspect.getdoc(
            workspace_module.materialize
        )
        for token in ("containment", "canonical remote identity",
                      "approved baseline commit"):
            self.assertIn(token, materialize_doc)

    def test_control_repo_immutability_framing(self):
        # framing (round-05 F-2): the control repository cannot be
        # modified through the remote-target path. Code side: the
        # tests/test_static.py control-chain bans, the read-only
        # transport verbs (test_evidence transport tests), and
        # workspace.materialize's containment-outside-the-control-
        # repository refusal.
        self.assertIn(
            "target engineering can never modify it through this"
            " path",
            self.flat().lower(),
        )

    def test_di_remote_2_is_the_principal_architecture(self):
        # I3 anti-regression pin (independent Operator finding):
        # PRESENCE was never the failure — PRIMACY was. The pin is
        # POSITIONAL: the DI-REMOTE-2 material must be CENTERED as the
        # current principal architecture AHEAD of the v1 material, and
        # the v1 material must carry an explicit released/compatibility
        # label. Reinstating v1 as the centered flow fails this.
        readme = self.flat()
        # (1) Inside "# Architecture", the DI-REMOTE-2 section comes
        # FIRST and the v1 section is labeled released/local.  find()
        # + authored asserts, not index(): a missing anchor must be an
        # authored FAIL, never a ValueError crash.
        architecture_at = readme.find("# Architecture")
        v2_heading_at = readme.find(
            "## Current architecture on `main`: remote target routing"
            " (DI-REMOTE-2"
        )
        v1_heading_at = readme.find(
            "## Released architecture (v0.6.3): local missions"
        )
        self.assertGreaterEqual(
            architecture_at, 0, "Architecture heading missing"
        )
        self.assertGreaterEqual(
            v2_heading_at, 0,
            "the DI-REMOTE-2 current-architecture heading is missing"
            " or demoted — DI-REMOTE-2 must be the centered principal"
            " architecture",
        )
        self.assertGreaterEqual(
            v1_heading_at, 0,
            "the released-v1 architecture heading (with its explicit"
            " released label) is missing",
        )
        self.assertLess(architecture_at, v2_heading_at)
        self.assertLess(
            v2_heading_at, v1_heading_at,
            "the DI-REMOTE-2 architecture must precede the released"
            " v1 architecture inside the principal section",
        )
        # (2) The v1 section's opening paragraph says plainly what it
        # is: released tag behavior and the preserved local path, not
        # the principal `main` architecture.
        v1_opening = readme[v1_heading_at:v1_heading_at + 400].lower()
        self.assertIn("released v0.6.3 tag", v1_opening)
        self.assertIn("local-mission path", v1_opening)
        self.assertIn("not the principal", v1_opening)
        # (3) The bold operating-model presentation leads with the
        # DI-REMOTE-2 chain: the FIRST bold chain after "The operating
        # model is deliberate" carries the Runtime/target components,
        # and the v1 chain that follows is introduced as the released
        # v0.6.3 model.
        model_at = readme.find("The operating model is deliberate")
        self.assertGreaterEqual(model_at, 0)
        first_chain_at = readme.find("**Phone → Telegram →", model_at)
        self.assertGreaterEqual(first_chain_at, 0)
        first_chain = readme[first_chain_at:first_chain_at + 300]
        for token in ("Runtime", "Broker", "managed target",
                      "target Herdr", "evidence verification"):
            self.assertIn(
                token, first_chain,
                "the FIRST bold operating-model chain must be the"
                " DI-REMOTE-2 chain",
            )
        v1_chain_at = readme.find(
            "**Phone → Telegram → Telegram Adapter →"
        )
        self.assertGreaterEqual(
            v1_chain_at, 0, "the released v1 chain must stay present"
        )
        self.assertGreater(v1_chain_at, first_chain_at)
        v1_chain_intro = readme[
            max(0, v1_chain_at - 200):v1_chain_at
        ].lower()
        self.assertIn("released v0.6.3 operating model", v1_chain_intro)

    def test_initial_dispatch_framing(self):
        # framing (round-05 N-1): byte-exact is the FIRST dispatch;
        # a corrective follow-up is a bounded corrective brief. Code
        # side: broker._dispatch (initial byte-exact vs D6 follow-up
        # brief) and the dispatch tests in
        # tests/test_target_runtime.py.
        flat = self.flat().lower()
        self.assertIn(
            "first dispatch is the byte-exact stored handoff", flat
        )
        self.assertIn("corrective brief", flat)

    def test_managed_workspace_framing(self):
        # framing: workspaces live under the protected per-user root
        # and are materialized only after one-shot consumption. Code
        # side: workspace lease + consumption tests in
        # tests/test_target_runtime.py.
        flat = self.flat().lower()
        self.assertIn("protected per-user root", flat)
        self.assertIn("materialized only after one-shot approval", flat)

    def test_supervisor_first_framing(self):
        # framing: the target Herdr Supervisor is the first
        # strategy-bearing component. Code side: the Supervisor-first
        # narrative assertions in ReleaseNarrativeTests.
        self.assertIn(
            "first strategy-bearing component", self.flat().lower()
        )

    def test_runtime_minted_capability_framing(self):
        # framing: one-shot Broker capabilities are minted by the
        # Runtime, never by Codex. Code side: the capability
        # mint/consume tests in tests/test_target_runtime.py.
        flat = self.flat().lower()
        self.assertIn("runtime-minted one-shot", flat)
        self.assertIn("minted by the runtime, never by codex", flat)


class TerminalMountainDocsPinTests(unittest.TestCase):
    """The historical external-target mountain is TERMINAL — pinned as such.

    Positive pins carry the exact evidence identifiers. Negative pins
    forbid the overstatements the documents used to make. Every
    negative pin is ANCHORED: the same assertion first requires the
    terminal framing sentence to be PRESENT, so a document that simply
    stopped mentioning the mountain cannot satisfy it vacuously.
    """

    # ---- A. the terminal mountain ------------------------------------
    def test_every_reconciled_doc_carries_the_terminal_framing(self):
        for name in RECONCILED_DOCS:
            self.assertIn(
                flat_lower(TERMINAL_ANCHOR), flat_lower(read_doc(name)),
                name,
            )

    def test_every_reconciled_doc_carries_the_exact_evidence_identifiers(self):
        for name in RECONCILED_DOCS:
            doc = flat(read_doc(name))
            for identifier in (
                MOUNTAIN_WORKFLOW_ID,
                MOUNTAIN_TARGET_TASK,
                MOUNTAIN_BASELINE,
                MOUNTAIN_STOP_CODE,
            ):
                self.assertIn(
                    identifier, doc, "%s: %s" % (name, identifier)
                )

    def test_every_reconciled_doc_generalizes_the_natural_language_request(self):
        for name in RECONCILED_DOCS:
            doc = flat_lower(read_doc(name))
            self.assertIn("natural-language", doc, name)
            self.assertIn("external repository issue", doc, name)

    def test_the_stop_state_is_stated_with_what_stayed_null(self):
        # `verified_result` and `result_delivery` remained null and no
        # target Git delivery occurred: the three facts that separate a
        # BLOCKED mountain from a completed one. SECURITY.md states
        # the same boundary and is held to it too.
        for name in CLAIM_DISCIPLINED_DOCS:
            doc = flat_lower(read_doc(name))
            self.assertIn("`verified_result`", doc, name)
            self.assertIn("`result_delivery`", doc, name)
            self.assertIn("null", doc, name)
            self.assertIn("no target git delivery occurred", doc, name)

    def test_the_target_stayed_at_baseline_with_a_diff_only(self):
        for name in RECONCILED_DOCS:
            doc = flat_lower(read_doc(name))
            self.assertIn("implementation diff only", doc, name)
            self.assertIn(MOUNTAIN_BASELINE, doc, name)

    def test_the_engineering_that_did_run_is_stated_exactly(self):
        # The mountain reached a target task COMPLETE with a canonical
        # target Reviewer APPROVE, and target observation refreshed off
        # its stale ACTIVE reading. Understating this is as wrong as
        # overstating the stages that never ran. The anchors are exact
        # phrases: a bare "complete" occurs all over these documents.
        for name in RECONCILED_DOCS:
            doc = flat_lower(read_doc(name))
            self.assertIn("reached complete", doc, name)
            self.assertIn("canonical target reviewer approve", doc, name)
            self.assertIn(
                "refreshed from a stale `active` reading to `complete`",
                doc, name,
            )

    def test_readme_states_the_terminal_outcome_in_its_opening(self):
        # PRIMACY, not presence. The same framing sentence occurs
        # further down the README, so a whole-document assertIn stays
        # green through a header that still reads as a success story.
        # This pin is POSITIONAL: the terminal outcome must appear in
        # the opening material a skimmer actually reads.
        readme = read_doc("README.md").lower()
        anchor = find_flexible(readme, TERMINAL_ANCHOR.lower())
        boundary = readme.find("the goal is not merely multi-agent coding")
        self.assertGreaterEqual(
            anchor, 0, "README never states the terminal outcome"
        )
        self.assertGreaterEqual(
            boundary, 0, "README opening-section boundary line missing"
        )
        self.assertLess(
            anchor, boundary,
            "the terminal outcome must be stated in the README opening,"
            " not only deep in the DI-REMOTE-2 section",
        )

    # ---- B. historical stabilization lineage, now integrated ----------
    INTEGRATED_PHRASES = {
        "README.md": (
            "integrated into `main` for v0.7.0",
        ),
        "CHANGELOG.md": (
            "integrated into `main` for v0.7.0",
        ),
        "docs/remote-mission-fabric-roadmap.md": (
            "integrate the reviewed and pushed Runtime stabilization commit",
        ),
    }

    def test_stabilization_history_is_preserved_and_integration_is_current(self):
        for name in RECONCILED_DOCS:
            doc = flat(read_doc(name))
            self.assertIn(STABILIZATION_SHA, doc, name)
            self.assertIn(STABILIZATION_BRANCH, doc, name)
            lowered = flat_lower(read_doc(name))
            for phrase in self.INTEGRATED_PHRASES[name]:
                self.assertIn(
                    flat_lower(phrase), lowered,
                    "%s: %s" % (name, phrase),
                )
        for name in ("README.md", "CHANGELOG.md"):
            doc = flat_lower(read_doc(name))
            self.assertIn(STABILIZATION_TASK, doc, name)
            self.assertIn("round 6 approve", doc, name)
            self.assertIn("pushed", doc, name)

    def test_stabilization_validation_results_are_pinned(self):
        for name in ("README.md", "CHANGELOG.md"):
            doc = flat_lower(read_doc(name))
            for result in (
                "159/159",
                "250/250",
                "static checks pass",
                "python 3.9.6 compile pass",
                "`git diff --check` pass",
            ):
                self.assertIn(
                    result, doc, "%s: %s" % (name, result)
                )

    def test_the_35_of_37_live_state_caveat_is_disclosed(self):
        # NOT a regression introduced by that task: two pre-existing
        # live `.herd` specimen assertions predate it. Naming both
        # files is the point — a bare "35/37" is not the disclosure.
        for name in ("README.md", "CHANGELOG.md"):
            doc = flat_lower(read_doc(name))
            self.assertIn("35/37", doc, name)
            self.assertIn("tests/test_hermetic_git.py", doc, name)
            self.assertIn("tests/test_reconcile_audit.py", doc, name)
            self.assertIn("predate", doc, name)

    # ---- C. clean-clone CI evidence ----------------------------------
    def test_clean_clone_ci_evidence_is_exact_and_not_stale(self):
        for name in RECONCILED_DOCS:
            doc = flat(read_doc(name))
            self.assertIn(CI_RUN_ID, doc, name)
            for sha in CI_COMMITS:
                self.assertIn(sha, doc, "%s: %s" % (name, sha))
            self.assertNotIn(
                CI_STALE_RUN_ID, doc,
                "%s still cites the STALE CI run" % name,
            )

    def test_all_four_ci_jobs_are_described(self):
        for name, phrase in (
            ("README.md",
             "all four macOS/Ubuntu x Python 3.9/3.13 jobs green"),
            ("CHANGELOG.md",
             "green across all four macOS/Ubuntu x Python 3.9/3.13 jobs"),
            ("docs/remote-mission-fabric-roadmap.md",
             "all four PR matrix jobs (macOS and Ubuntu x Python 3.9 and"
             " 3.13) are green"),
        ):
            self.assertIn(flat(phrase), flat(read_doc(name)), name)

    # ---- Negative pins, each ANCHORED to the terminal framing ---------
    def test_no_reconciled_doc_reintroduces_a_forbidden_claim(self):
        for name in CLAIM_DISCIPLINED_DOCS:
            doc = flat_lower(read_doc(name))
            # ANCHOR first: absence only counts in a document that
            # actually discusses the mountain.
            self.assertIn(
                flat_lower(TERMINAL_ANCHOR), doc,
                "%s: anchor missing — the absence assertions below"
                " would pass vacuously" % name,
            )
            for claim in FORBIDDEN_LIVE_CLAIMS:
                self.assertNotIn(
                    claim, doc,
                    "%s reintroduces the forbidden claim %r"
                    % (name, claim),
                )

    def test_no_doc_claims_the_terminal_facts_are_unproven(self):
        # ROUND-1 B1. The target task reached COMPLETE and a canonical
        # target Reviewer APPROVE was recorded. A document that still
        # lists either among the not-yet-proved stages contradicts the
        # rest of the same document. This is a SHAPE pin, not a phrase
        # pin: rewording the sentence does not escape it.
        for name in CLAIM_DISCIPLINED_DOCS:
            doc = flat_lower(read_doc(name))
            self.assertIn(
                flat_lower(TERMINAL_ANCHOR), doc,
                "%s: anchor missing — the absence assertions below"
                " would pass vacuously" % name,
            )
            for label, pattern in FORBIDDEN_CLAIM_PATTERNS:
                match = re.search(pattern, doc)
                self.assertIsNone(
                    match,
                    "%s: %s — matched %r"
                    % (name, label, match.group(0) if match else None),
                )

    def test_no_doc_asserts_a_currently_active_mission(self):
        # ROUND-1 B2. The mountain is TERMINAL; no document may speak
        # of it in the present tense. A strikethrough is not a tense.
        for name in CLAIM_DISCIPLINED_DOCS:
            doc = flat_lower(read_doc(name))
            self.assertIn(
                flat_lower(TERMINAL_ANCHOR), doc,
                "%s: anchor missing — the absence assertions below"
                " would pass vacuously" % name,
            )
            for label, pattern in FORBIDDEN_PRESENT_TENSE_PATTERNS:
                match = re.search(pattern, doc)
                self.assertIsNone(
                    match,
                    "%s: %s — matched %r"
                    % (name, label, match.group(0) if match else None),
                )

    def test_di_remote_2_release_acceptance_is_current(self):
        readme = flat_lower(read_doc("README.md"))
        self.assertIn(flat_lower(TERMINAL_ANCHOR), readme)
        self.assertIn("v0.7.0 adds di-remote-2", readme)
        roadmap = flat_lower(read_doc("docs/remote-mission-fabric-roadmap.md"))
        self.assertIn(flat_lower(TERMINAL_ANCHOR), roadmap)
        self.assertIn("di-remote-2 acceptance is complete", roadmap)
        self.assertIn("v0.7.0 release tree", roadmap)

    def test_post_fix_live_mountain_is_not_release_evidence(self):
        for name in ("README.md",
                     "docs/remote-mission-fabric-roadmap.md"):
            doc = flat_lower(read_doc(name))
            self.assertIn(flat_lower(TERMINAL_ANCHOR), doc, name)
            self.assertIn("fresh post-fix live mountain", doc, name)
            self.assertIn("not used as release evidence", doc, name)


def setUpModule():
    """R-47/R-48: this module drives a full release through the
    Broker, which now reclaims the workflow's process-scope records
    (R-54 AR-4). It runs against a PRIVATE base."""
    global _ISOLATED_BASE
    _ISOLATED_BASE = scope_hygiene.isolate_module()


def tearDownModule():
    scope_hygiene.release_module(_ISOLATED_BASE)


if __name__ == "__main__":
    unittest.main()
