"""Tests for the panel's controller (``mission_panel.service.PanelService``).

The REAL stack end to end: the restricted conversation (real posture check and
event parser, only the process spawn substituted) -> the journal -> the local
request surface -> Mission Core -> the operator-attested approval -> the real
``MissionBridge`` (driven by the bridge test suite's scripted observer, spawn
recorder and fake transport, the repo's established way of running the bridge
without a live Herdr). Nothing here is evidence of a live provider or Herdr.

What is pinned: nothing becomes a proposal of record without a human binding
it; the decision is the typed exact affirmative and nothing a provider wrote;
a stale display is refused and invalidated, never re-pointed; a duplicate click
or a second window cannot apply or start anything twice; an uncertain
submission or start is shown as uncertain and never retried; a window is offered
only what the records support; and no view leaks a capability, a path or an
internal id.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from _hermetic_git import run_git  # noqa: E402
from local_request import store as store_module  # noqa: E402
from local_request import surface as surface_module  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission_panel import conversation as conversation_module  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from mission_panel import service as service_module  # noqa: E402
from operator_session.codex import RestrictedCodexOperatorSession  # noqa: E402
from test_mission_bridge import (  # noqa: E402
    Fixture as BridgeFixture, REPO_URL, TASK_ID, raw_observation,
)
from test_mission_panel_conversation import (  # noqa: E402
    FakeSpawn, GOOD, PROPOSAL_REPLY,
)


class PanelFixture(BridgeFixture):

    def setUp(self):
        super(PanelFixture, self).setUp()
        self.control = os.path.join(self.tmp.name, "control")
        os.makedirs(self.control)
        run_git("init", "-q", self.control)
        for name in ("AGENTS.md", "OPERATOR_PROTOCOL.md"):
            with open(os.path.join(self.control, name), "w") as handle:
                handle.write("# fixture\n")
        self.provider = FakeSpawn()
        self.build()

    def build(self, bridge="default", provider=None, progression=None):
        """A fresh PanelService over the SAME state directory: a restart, or a
        second window's process, holds exactly what is durable."""
        self.journal = journal_module.PanelJournal(self.state)
        self.registry = conversation_module.InFlight()
        self.convo = conversation_module.PanelConversation(
            self.journal,
            RestrictedCodexOperatorSession(runner=provider or self.provider),
            self.control, REPO_URL, self.clock, registry=self.registry)
        self.addCleanup(self.convo.idle, 10)
        used = self.bridge if bridge == "default" else bridge
        self.panel_surface = surface_module.LocalRequestSurface(
            self.missions, store_module.LocalRequestStore(self.state), self.clock,
            bridge=used)
        self.service = service_module.PanelService(
            self.panel_surface, self.journal, self.convo, self.registry,
            self.clock, bridge=used, repository_url=REPO_URL,
            progression=progression)
        return self.service

    # -- driving the conversation -------------------------------------------

    def say(self, text, reply, conversation_id=None):
        self.provider.script.append(reply)
        turn = self.service.send_turn(conversation_id, text)["turn"]
        self.assertTrue(self.convo.idle(15))
        return self.service.conversation_view(turn["conversation_id"])["turns"][-1]

    def drafted(self, objective=None, conversation_id=None):
        reply = PROPOSAL_REPLY if objective is None else (
            "```mission-proposal\n%s\n```" % json.dumps(dict(GOOD, objective=objective)))
        turn = self.say("make the probe stable", reply, conversation_id)
        self.assertIsNotNone(turn["draft"], turn)
        return turn

    def of_record(self, supersedes=None, **kwargs):
        turn = self.drafted(**kwargs)
        out = self.service.submit_draft(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"], supersedes)
        return out["request"]

    def refusal(self, problem, callable_, *args, **kwargs):
        with self.assertRaises(service_module.PanelRefusal) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.problem, problem, caught.exception.reason)
        return caught.exception

    def core_refusal(self, problem, callable_, *args, **kwargs):
        with self.assertRaises(surface_module.LocalRequestRefusal) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.problem, problem, caught.exception.reason)
        return caught.exception

    def binding_of(self, view):
        return dict(mission_id=view["mission_id"], revision=view["revision"],
                    proposal_digest_sha256=view["proposal_digest_sha256"])

    def decide_args(self, view, offer=None, **changes):
        """What a window submits WITH a decision: the displayed Mission, revision and digest,
        and the expiry, scope, delivery targets and repository it was shown."""
        offer = offer or self.service.decision_offer(view["request_ref"])
        args = dict(
            mission_id=view["mission_id"], revision=view["revision"],
            proposal_digest_sha256=view["proposal_digest_sha256"],
            offer_digest_sha256=offer["offer_digest_sha256"],
            expires_at=offer["expires_at"],
            action_scope=list(offer["approved_action_scope"]),
            delivery_targets=list(offer["approved_delivery_targets"]),
            repository_url=view["proposal"]["repository_url"], reply="approve")
        args.update(changes)
        return args

    def approve(self, view, reply="approve", offer=None, **changes):
        return self.service.decide(
            view["request_ref"],
            **self.decide_args(view, offer=offer, reply=reply, **changes))

    def stage(self, request_ref):
        return self.service.request_view(request_ref)["lifecycle"]["stage"]

    def authorizations(self):
        if not os.path.exists(os.path.join(self.state, "missions.json")):
            return {}                  # no Mission was ever created
        return json.loads(self.mission_bytes()).get("authorizations", {})

    def finish_team(self, mission_id, status="COMPLETE", artifacts=True):
        if artifacts:
            self.write_artifacts(mission_id)
        self.observer.raw = raw_observation(status=status)


class FullLifecycleTests(PanelFixture):

    def test_F1_request_to_verified_result_through_every_real_seam(self):
        # 1. ordinary language -> a clarifying question (a conversation turn only)
        clarify = self.say("make the probe stable", "Which probe do you mean?")
        self.assertIsNone(clarify["draft"])
        self.assertEqual(self.service.overview()["requests"], [])
        # 2. -> an exact proposal DRAFT: not yet of record
        turn = self.drafted(conversation_id=clarify["conversation_id"])
        self.assertEqual(self.panel_surface.list_requests()["requests"], [])
        # 3. a human binds it: now it is Mission Core's proposal, revision, digest
        view = self.service.submit_draft(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"])["request"]
        self.assertEqual(view["lifecycle"]["stage"], "of_record")
        self.assertEqual(view["revision"], 1)
        self.assertEqual(view["proposal_digest_sha256"],
                         turn["draft"]["proposal_digest_sha256"])
        self.assertEqual(view["proposal"], turn["draft"]["proposal"])
        self.assertEqual(self.authorizations(), {})
        # 4. the separate human decision: the typed exact affirmative
        approved = self.approve(view)["request"]
        self.assertEqual(approved["lifecycle"]["stage"], "approved")
        self.assertEqual(approved["lifecycle"]["provenance"]["proof"],
                         "operator_attested_not_independently_verified")
        self.assertEqual(len(self.authorizations()), 1)
        self.assertEqual(self.spawn.calls, [], "approval must not start work")
        # 5. validation and dispatch
        dispatched = self.service.run(view["request_ref"], "dispatch")["request"]
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(dispatched["lifecycle"]["stage"], "dispatched")
        self.assertIn("exactly once", dispatched["lifecycle"]["detail"])
        # 6. observation: the team is working
        self.observer.raw = raw_observation()
        running = self.service.run(view["request_ref"], "observe")["request"]
        self.assertEqual(running["lifecycle"]["stage"], "running")
        self.assertEqual(running["lifecycle"]["actions"], [],
                         "verification must not be offered while the team works")
        # 7. review + independent verification once the team has finished
        self.finish_team(view["mission_id"])
        ready = self.service.request_view(view["request_ref"])
        self.assertEqual(ready["lifecycle"]["actions"], ["verify"])
        done = self.service.run(view["request_ref"], "verify")["request"]
        self.assertEqual(done["lifecycle"]["stage"], "verified")
        # 8. the retrievable verified result
        result = self.service.result(view["request_ref"])
        self.assertTrue(result["recoverable"])
        self.assertIn("readiness probe fix landed", result["result_text"])
        self.assertFalse(result["delivered"])
        self.assertEqual(result["delivery_authority"], "none")


class ConversationIsNotAuthorityTests(PanelFixture):

    def test_A1_a_draft_in_chat_is_not_a_proposal_of_record(self):
        self.drafted()
        self.assertEqual(self.panel_surface.list_requests()["requests"], [])
        self.assertEqual(self.service.overview()["requests"], [])

    def test_A2_a_reply_that_claims_approval_changes_nothing(self):
        boast = ("Approved! I have started the run.\n```mission-proposal\n%s\n```"
                 % json.dumps(dict(GOOD, approved=True, decision="approve")))
        turn = self.say("go", boast)
        self.assertIsNone(turn["draft"])
        self.assertIn("approved", turn["draft_problem"])
        self.assertEqual(self.panel_surface.list_requests()["requests"], [])
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(self.spawn.calls, [])

    def test_A3_only_the_exact_draft_the_person_read_can_be_submitted(self):
        turn = self.drafted()
        with self.assertRaises(journal_module.JournalError) as caught:
            self.service.submit_draft(turn["turn_id"], "b" * 64)
        self.assertEqual(caught.exception.problem, journal_module.PROBLEM_DRAFT_MISMATCH)
        self.assertEqual(self.panel_surface.list_requests()["requests"], [])

    def test_A4_a_turn_with_no_draft_cannot_be_submitted(self):
        turn = self.say("hello", "What would you like done?")
        with self.assertRaises(journal_module.JournalError) as caught:
            self.service.submit_draft(turn["turn_id"], "a" * 64)
        self.assertEqual(caught.exception.problem, journal_module.PROBLEM_NO_DRAFT)

    def test_A5_a_provider_reply_cannot_reach_the_decision(self):
        # A reply's text is display content; nothing in a turn view is an
        # action, and the decision takes only the person's typed reply.
        view = self.of_record()
        self.say("approve", "approve", conversation_id=None)
        self.assertEqual(self.stage(view["request_ref"]), "of_record")
        self.assertEqual(self.authorizations(), {})


class DecisionTests(PanelFixture):

    def test_D1_only_the_exact_affirmative_decides(self):
        view = self.of_record()
        before = (self.mission_bytes(), self.surface_bytes_())
        for reply in ("yes", "approved!", "approve it", "I approve", "ok", "",
                      "   ", "disapprove", "approve\nand dispatch", 7, None,
                      "x" * (store_module.MAX_RELAYED_REPLY_CHARS + 1)):
            with self.subTest(reply=reply):
                self.core_refusal("local_request_reply_not_affirmative",
                                  self.approve, view, reply)
        self.assertEqual((self.mission_bytes(), self.surface_bytes_()), before,
                         "a refused reply must not read or write anything")
        self.assertEqual(self.authorizations(), {})

    def test_D2_case_and_surrounding_whitespace_are_ignored_nothing_else(self):
        for index, reply in enumerate((" Approve ", "APPROVED", "approved\n")):
            with self.subTest(reply=reply):
                view = self.of_record(objective="request %d" % index)
                self.assertEqual(self.approve(view, reply)["request"]["lifecycle"]["stage"],
                                 "approved")

    def test_D3_a_duplicate_click_applies_one_approval(self):
        view = self.of_record()
        shown = self.service.decision_offer(view["request_ref"])    # one display, clicked twice
        first = self.approve(view, offer=shown)
        second = self.approve(view, offer=shown)
        self.assertEqual(len(self.authorizations()), 1)
        self.assertEqual(second["decision"]["authorization_id"],
                         first["decision"]["authorization_id"])
        self.assertTrue(second["decision"]["idempotent"])

    def test_D4_two_windows_deciding_at_once_apply_one_approval(self):
        view = self.of_record()
        other = self.build()          # a second window's process over the same state
        results, errors = [], []

        def decide(service):
            try:
                results.append(service.decide(
                    view["request_ref"], **self.decide_args(view)))
            except Exception as exc:       # pragma: no cover - reported below
                errors.append(exc)
        threads = [threading.Thread(target=decide, args=(s,))
                   for s in (self.service, other)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(self.authorizations()), 1)
        self.assertEqual(len({r["decision"]["authorization_id"] for r in results}), 1)

    def test_D5_a_stale_display_is_refused_and_invalidated_never_repointed(self):
        view = self.of_record()
        shown = self.service.decision_offer(view["request_ref"])    # the window already holds it
        self.authenticated_edit_(view["mission_id"], 1)
        refused = self.refusal(service_module.PROBLEM_STALE_DISPLAY, self.approve, view, offer=shown)
        self.assertTrue(refused.details["display_invalid"])
        self.assertEqual(refused.details["binding"], "stale_revision")
        self.assertEqual(refused.details["displayed"]["revision"], 1)
        self.assertEqual(refused.details["current"]["revision"], 2)
        self.assertEqual(self.authorizations(), {})
        # The current proposal is shown for READING; approving it takes a fresh decision
        # bound to a fresh offer for what is now displayed.
        current = self.service.request_view(view["request_ref"])
        self.assertEqual(current["revision"], 2)
        fresh = self.service.decision_offer(view["request_ref"])
        self.assertEqual(fresh["revision"], 2)
        self.assertNotEqual(fresh["offer_digest_sha256"], shown["offer_digest_sha256"])

    def test_D5b_a_revision_that_differs_ONLY_in_the_proof_rules_is_stale_not_decidable(self):
        # Round 27 F1 at the record: the proof contract is INSIDE the proposal and its digest, so a
        # revision that changes nothing but the rules is a different proposal. A window that was shown
        # the old rules and holds the old offer must be refused, and approval must take a fresh offer
        # for the rules now of record. (A test that also changed the objective would prove nothing.)
        view = self.of_record()
        shown = self.service.decision_offer(view["request_ref"])
        edited = json.loads(json.dumps(view["proposal"]))
        edited["proof_contract"]["continuation_budget"]["max_attempts"] += 5
        edited["proof_contract"]["requirements"][0]["max_evidence_age_seconds"] = 60
        self.assertNotEqual(edited["proof_contract"], view["proposal"]["proof_contract"])
        self.assertEqual({k: v for k, v in edited.items() if k != "proof_contract"},
                         {k: v for k, v in view["proposal"].items() if k != "proof_contract"},
                         "this edit must change the proof rules and nothing else")
        from test_local_request import SYNTHETIC_AUTHENTICATED
        self.missions.edit(view["mission_id"], 1, edited,
                           self.missions.mint_decision_id(SYNTHETIC_AUTHENTICATED),
                           SYNTHETIC_AUTHENTICATED)
        refused = self.refusal(service_module.PROBLEM_STALE_DISPLAY, self.approve, view, offer=shown)
        self.assertTrue(refused.details["display_invalid"])
        self.assertEqual(refused.details["displayed"]["revision"], 1)
        self.assertEqual(refused.details["current"]["revision"], 2)
        self.assertNotEqual(refused.details["displayed"]["proposal_digest_sha256"],
                            refused.details["current"]["proposal_digest_sha256"])
        self.assertEqual(self.authorizations(), {}, "nothing was approved on rules never shown")
        # The window can READ the new rules (they are in the view it is sent) ...
        current = self.service.request_view(view["request_ref"])
        self.assertEqual(current["proposal"]["proof_contract"], edited["proof_contract"])
        # The words name the revision that is of record (found by the browser scenario for this: they named
        # the digest the entry was first submitted with).
        self.assertIn(current["proposal_digest_sha256"][:12], current["lifecycle"]["detail"])
        self.assertNotIn(view["proposal_digest_sha256"][:12], current["lifecycle"]["detail"])
        # ... and approving takes a fresh offer bound to the NEW digest.
        fresh = self.service.decision_offer(view["request_ref"])
        self.assertEqual(fresh["proposal_digest_sha256"], current["proposal_digest_sha256"])
        self.approve(current, offer=fresh)
        self.assertEqual(len(self.authorizations()), 1)
        self.assertEqual(self.stage(view["request_ref"]), "approved",
                         "the record must say the revision it approved is approved")
        # Approving moves the stage and NOTHING the person was shown to bind to: same Mission, revision,
        # digest and proposal before and after (a window compares these to know its display is current).
        after = self.service.request_view(view["request_ref"])
        for key in ("mission_id", "revision", "proposal_digest_sha256"):
            self.assertEqual(after[key], current[key], key)
        # What the panel holds of an out-of-panel revision ends with the approval: it has the first revision's
        # body and not the second's, so it sends NO body for it rather than the wrong one. A window therefore
        # keeps the body it was shown (the digest, unchanged, covers it) and never takes `null` for a change.
        self.assertIsNone(after["proposal"])

    def test_D6_a_digest_that_is_not_the_displayed_offers_is_tampering(self):
        view = self.of_record()
        shown = self.service.decision_offer(view["request_ref"])
        refused = self.refusal(
            service_module.PROBLEM_STALE_DISPLAY, self.service.decide,
            view["request_ref"], **self.decide_args(
                view, offer=shown, revision=1, proposal_digest_sha256="a" * 64))
        self.assertEqual(refused.details["binding"], "offer_tampered")
        self.assertEqual(self.authorizations(), {})

    def test_D7_a_display_naming_another_mission_is_tampering(self):
        a, b = self.of_record(objective="A"), self.of_record(objective="B")
        shown = self.service.decision_offer(a["request_ref"])
        refused = self.refusal(
            service_module.PROBLEM_STALE_DISPLAY, self.service.decide, a["request_ref"],
            **self.decide_args(a, offer=shown, mission_id=b["mission_id"]))
        self.assertEqual(refused.details["binding"], "offer_tampered")
        self.assertEqual(self.authorizations(), {})

    def test_D8_a_withdrawn_proposal_cannot_be_decided(self):
        view = self.of_record()
        shown = self.service.decision_offer(view["request_ref"])    # a window already showing it
        self.service.withdraw(view["request_ref"], **self.binding_of(view))
        refused = self.refusal(service_module.PROBLEM_STALE_DISPLAY, self.approve, view, offer=shown)
        self.assertEqual(refused.details["binding"], "withdrawn")
        self.assertEqual(refused.details["surface_problem"], "local_request_cancelled")
        self.assertTrue(refused.details["display_invalid"])
        self.assertEqual(self.authorizations(), {})

    def test_D9_the_approval_expires_and_is_then_neither_offered_nor_started(self):
        view = self.of_record()
        self.approve(view)
        self.clock.now += mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS + 1
        expired = self.service.request_view(view["request_ref"])
        self.assertEqual(expired["lifecycle"]["stage"], "expired")
        self.assertEqual(expired["lifecycle"]["actions"], [])
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, self.service.run,
                     view["request_ref"], "dispatch")
        self.assertEqual(self.spawn.calls, [])

    def test_D10_the_attested_provenance_and_risk_are_carried_to_the_view(self):
        view = self.of_record()
        approved = self.approve(view)["request"]
        attestation = approved["attestation"]
        self.assertEqual(attestation["state"], "APPLIED")
        self.assertEqual(attestation["relayed_reply"], "approve")
        self.assertEqual(attestation["provenance_label"]["proof"],
                         "operator_attested_not_independently_verified")
        self.assertIn("could fabricate it", attestation["residual_risk"])

    def test_D11_the_ordinary_unauthenticated_approve_stays_refused(self):
        view = self.of_record()
        refusal = self.panel_surface.approve(view["request_ref"], 1,
                                             view["proposal_digest_sha256"])
        self.assertEqual(refusal["problem"], "local_request_approval_unauthenticated")
        self.assertEqual(self.authorizations(), {})

    # fixtures local to this class ------------------------------------------

    def surface_bytes_(self):
        with open(os.path.join(self.state, "local_requests.json"), "rb") as handle:
            return handle.read()

    def authenticated_edit_(self, mission_id, revision):
        from test_local_request import SYNTHETIC_AUTHENTICATED, request
        return self.missions.edit(
            mission_id, revision, request(objective="edited elsewhere"),
            self.missions.mint_decision_id(SYNTHETIC_AUTHENTICATED),
            SYNTHETIC_AUTHENTICATED)


class DisplayedOfferIsWhatIsBoundTests(PanelFixture):
    """The decision OFFER a window displays is FROZEN when it is first shown and RETAINED. A
    decision submits the exact offer it displayed; the service compares it with the retained
    record (any difference is tampering) AND with the current canonical state (any difference is a
    changed offer), then binds the retained expiry and scope. A digest covers the proposal's
    content only and the expiry is recomputed from the clock on every read, so without a retained
    offer a changed one could be substituted under an unchanged digest. Think time within the
    expiry is NOT staleness; time beyond it is a distinct EXPIRY."""

    def authorization(self, view=None):
        """The Mission Core authorization (of ``view``'s Mission when given)."""
        found = [a for a in self.authorizations().values()
                 if view is None or a["mission_id"] == view["mission_id"]]
        return found[-1]

    def offer_for(self, view):
        return self.service.decision_offer(view["request_ref"])

    # -- the offer is frozen, retained and shared ----------------------------------------

    def test_O0_an_offer_states_its_absolute_expiry_and_digest_and_is_retained(self):
        view = self.of_record()
        offer = self.offer_for(view)
        self.assertEqual(offer["expires_at"], offer["server_now"]
                         + mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS)
        self.assertRegex(offer["offer_digest_sha256"], r"^[0-9a-f]{64}$")
        retained = self.journal.offer(offer["offer_digest_sha256"])
        self.assertEqual(retained["expires_at"], offer["expires_at"])
        self.assertEqual(retained["request_ref"], view["request_ref"])
        self.build()                                         # a restart: new objects, same state
        self.assertEqual(self.journal.offer(offer["offer_digest_sha256"]), retained,
                         "it survives a restart")

    def test_O0b_every_window_is_shown_the_same_offer_while_it_is_live(self):
        view = self.of_record()
        first = self.offer_for(view)
        self.clock.now += 200
        other = self.build()
        second = other.decision_offer(view["request_ref"])
        self.assertEqual(second["offer_digest_sha256"], first["offer_digest_sha256"])
        self.assertEqual(second["expires_at"], first["expires_at"],
                         "the expiry is frozen: not recomputed from the clock")

    def test_O0c_after_the_expiry_a_refresh_is_a_new_offer_with_a_new_window(self):
        view = self.of_record()
        first = self.offer_for(view)
        self.clock.now = first["expires_at"]
        refreshed = self.offer_for(view)
        self.assertNotEqual(refreshed["offer_digest_sha256"], first["offer_digest_sha256"])
        self.assertEqual(refreshed["expires_at"], self.clock.now
                         + mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS)

    # -- think time is not staleness; the approval binds what was shown ---------------------

    def test_O1_the_approval_binds_the_expiry_that_was_shown_never_a_recomputed_one(self):
        view = self.of_record()
        shown = self.offer_for(view)
        self.clock.now += 120                            # the person is reading
        recomputed = self.service._surface.present(view["request_ref"])["latest_expires_at"]
        self.assertNotEqual(recomputed, shown["expires_at"],
                            "a freshly computed expiry differs, with digest and revision unchanged")
        out = self.approve(view, offer=shown)
        self.assertEqual(self.authorization()["expires_at"], shown["expires_at"])
        self.assertNotEqual(self.authorization()["expires_at"], recomputed)
        self.assertEqual(out["request"]["lifecycle"]["approval_expires_at"], shown["expires_at"])

    def test_O1b_think_time_is_not_staleness_up_to_the_last_second_of_the_window(self):
        """THE positive case: the unchanged offer still succeeds, bound to the expiry the person
        saw, for as long as now < that expiry."""
        for think in (1, 300, 898, 899):
            with self.subTest(seconds_of_thinking=think):
                self.clock.now += 1
                view = self.of_record(objective="request after %d s" % think)
                shown = self.offer_for(view)
                self.clock.now += think
                self.assertLess(self.clock.now, shown["expires_at"])
                out = self.approve(view, offer=shown)
                self.assertEqual(out["request"]["lifecycle"]["stage"], "approved")
                self.assertEqual(self.authorization(view)["expires_at"], shown["expires_at"])

    def test_O1c_the_window_closes_exactly_at_the_expiry_that_was_shown(self):
        view = self.of_record()
        shown = self.offer_for(view)
        self.clock.now = shown["expires_at"] - 1
        self.approve(view, offer=shown)                     # the last second: succeeds
        self.assertEqual(self.authorization(view)["expires_at"], shown["expires_at"])
        other = self.of_record(objective="a second request")
        shown2 = self.offer_for(other)
        self.clock.now = shown2["expires_at"]               # the first second after: expired
        self.refusal(service_module.PROBLEM_OFFER_EXPIRED, self.approve, other, offer=shown2)

    def test_O2_a_window_that_has_passed_is_EXPIRED_not_stale_not_rebound(self):
        view = self.of_record()
        shown = self.offer_for(view)
        self.clock.now = shown["expires_at"]                # now >= the expiry that was shown
        refused = self.refusal(service_module.PROBLEM_OFFER_EXPIRED, self.approve, view, offer=shown)
        self.assertEqual(refused.details["expires_at"], shown["expires_at"])
        self.assertFalse(refused.details["display_invalid"],
                         "nothing about the offer changed: only time passed")
        self.assertEqual(self.authorizations(), {}, "nothing may be approved on a rebound expiry")
        fresh = self.offer_for(view)                        # reading the offer again is the way on
        self.assertEqual(fresh["proposal_digest_sha256"], shown["proposal_digest_sha256"])
        self.approve(view, offer=fresh)
        self.assertEqual(self.authorization()["expires_at"], fresh["expires_at"])

    # -- an offer that was changed, forged or never issued ----------------------------------

    def test_O3_a_submitted_expiry_that_differs_from_the_retained_offer_is_tampering(self):
        view = self.of_record()
        shown = self.offer_for(view)
        for other in (shown["expires_at"] - 1, shown["expires_at"] + 1, shown["expires_at"] + 10_000):
            with self.subTest(expires_at=other):
                refused = self.refusal(service_module.PROBLEM_STALE_DISPLAY, self.approve, view,
                                       offer=shown, expires_at=other)
                self.assertEqual(refused.details["binding"], "offer_tampered")
        self.assertEqual(self.authorizations(), {})

    def test_O4_a_malformed_offer_is_refused_before_anything_is_read(self):
        view = self.of_record()
        shown = self.offer_for(view)
        before = self.mission_bytes()
        for bad in (None, True, "1000900", 1.5, -1, [900]):
            with self.subTest(expires_at=bad):
                self.refusal(service_module.PROBLEM_BAD_REQUEST, self.approve, view,
                             offer=shown, expires_at=bad)
        for bad in (None, 7, "", "A" * 64, "a" * 63):
            with self.subTest(offer_digest_sha256=bad):
                self.refusal(service_module.PROBLEM_BAD_REQUEST, self.approve, view,
                             offer=shown, offer_digest_sha256=bad)
        self.assertEqual(self.mission_bytes(), before)

    def test_O5_every_changed_bound_field_is_tampering_before_any_authority_write(self):
        view = self.of_record()
        shown = self.offer_for(view)
        cases = (
            dict(action_scope=["repository_read"]), dict(action_scope=[]),
            dict(action_scope=shown["approved_action_scope"] + ["verification_run"]),
            dict(delivery_targets=["github_pr"]),
            dict(repository_url="https://github.com/Other/Repo"), dict(repository_url=None),
            dict(mission_id="mn-" + "0" * 32), dict(revision=2),
            dict(proposal_digest_sha256="0" * 64),
        )
        for change in cases:
            with self.subTest(change=change):
                with self.assertRaises((service_module.PanelRefusal,
                                        surface_module.LocalRequestRefusal)) as caught:
                    self.approve(view, offer=shown, **change)
                self.assertIn(caught.exception.problem, (
                    service_module.PROBLEM_STALE_DISPLAY, "local_request_misattributed"))
        self.assertEqual(self.authorizations(), {})

    def test_O5b_an_offer_that_was_never_issued_is_refused_and_a_never_issued_expired_one_is_expired(self):
        view = self.of_record()
        shown = self.offer_for(view)
        forged = dict(offer_digest_sha256="f" * 64)
        refused = self.refusal(service_module.PROBLEM_STALE_DISPLAY, self.approve, view,
                               offer=shown, **forged)
        self.assertEqual(refused.details["binding"], "offer_unknown")
        self.clock.now = shown["expires_at"] + 5
        self.refusal(service_module.PROBLEM_OFFER_EXPIRED, self.approve, view, offer=shown, **forged)
        self.assertEqual(self.authorizations(), {})

    def forge(self, view, real, **change):
        """Write a self-consistent offer record straight into the journal file (as corruption
        or a bug could): its digest matches its fields, so only the CURRENT state can refuse it."""
        fields = dict(
            request_ref=view["request_ref"], mission_id=view["mission_id"], revision=1,
            proposal_digest_sha256=view["proposal_digest_sha256"],
            repository_url=view["proposal"]["repository_url"],
            action_scope=list(real["approved_action_scope"]),
            delivery_targets=list(real["approved_delivery_targets"]),
            expires_at=real["expires_at"])
        fields.update(change)
        digest = journal_module.offer_digest(fields)
        document = self.journal.load()
        document["offers"] = {digest: dict(fields, offer_digest_sha256=digest,
                                           issued_at=real["expires_at"] - 900)}
        self.journal.save(document)
        return dict(real, offer_digest_sha256=digest, expires_at=fields["expires_at"],
                    approved_action_scope=fields["action_scope"],
                    approved_delivery_targets=fields["delivery_targets"])

    def test_O5c_a_retained_offer_that_disagrees_with_the_current_record_is_refused_too(self):
        """The submitted offer matches the retained one EXACTLY, and the retained one is wrong
        against the canonical state: the current state still decides, before any authority write."""
        view = self.of_record()
        real = self.offer_for(view)
        for binding, change in (
            ("scope_mismatch", dict(action_scope=["repository_read"])),
            ("delivery_targets_mismatch", dict(delivery_targets=["github_pr"])),
            ("repository_mismatch", dict(repository_url="https://github.com/Other/Repo")),
            ("expiry_beyond_window", dict(expires_at=real["expires_at"] + 10_000)),
        ):
            with self.subTest(binding=binding):
                offer = self.forge(view, real, **change)
                args = self.decide_args(view, offer=offer, repository_url=change.get(
                    "repository_url", view["proposal"]["repository_url"]))
                with self.assertRaises(service_module.PanelRefusal) as caught:
                    self.service.decide(view["request_ref"], **args)
                self.assertEqual(caught.exception.problem, service_module.PROBLEM_STALE_DISPLAY)
                self.assertEqual(caught.exception.details["binding"], binding)
        self.assertEqual(self.authorizations(), {})

    def test_O6_malformed_scope_targets_or_repository_are_refused_as_bad_requests(self):
        view = self.of_record()
        shown = self.offer_for(view)
        for change in (dict(action_scope="engineering_change"), dict(action_scope=[7]),
                       dict(delivery_targets="none"), dict(delivery_targets=[None]),
                       dict(repository_url=7), dict(repository_url=["x"])):
            with self.subTest(change=change):
                self.refusal(service_module.PROBLEM_BAD_REQUEST, self.approve, view,
                             offer=shown, **change)
        self.assertEqual(self.authorizations(), {})

    def test_O7_scope_order_does_not_matter_only_the_set(self):
        view = self.of_record()
        shown = self.offer_for(view)
        self.approve(view, offer=shown, action_scope=list(reversed(shown["approved_action_scope"])))
        self.assertEqual(self.authorization()["authorized_action_scope"],
                         sorted(shown["approved_action_scope"]))

    # -- replacement and revision invalidate; two windows never approve twice -----------------

    def test_O1d_a_replacement_makes_the_old_display_stale_and_the_new_one_decidable(self):
        old = self.of_record(objective="first wording")
        shown = self.offer_for(old)
        new = self.of_record(supersedes=old["request_ref"], objective="second wording")
        refused = self.refusal(service_module.PROBLEM_STALE_DISPLAY, self.approve, old, offer=shown)
        self.assertEqual(refused.details["binding"], "withdrawn")
        self.assertTrue(refused.details["display_invalid"])
        self.assertEqual(self.authorizations(), {})
        self.approve(new)                                   # the replacement is a fresh decision
        self.assertEqual(len(self.authorizations()), 1)

    def test_O1e_two_windows_showing_the_same_offer_make_one_approval_bound_to_it(self):
        view = self.of_record()
        shown_a = self.offer_for(view)
        other = self.build()                                # a second window, shown the same offer
        shown_b = other.decision_offer(view["request_ref"])
        self.assertEqual(shown_a["offer_digest_sha256"], shown_b["offer_digest_sha256"])
        self.clock.now += 45
        first = self.approve(view, offer=shown_a)
        second = other.decide(view["request_ref"], **self.decide_args(view, offer=shown_b))
        self.assertFalse(first["decision"]["idempotent"])
        self.assertTrue(second["decision"]["idempotent"], "the second window found the approval")
        self.assertEqual(len(self.authorizations()), 1, "no duplicate")
        self.assertEqual(self.authorization()["expires_at"], shown_a["expires_at"],
                         "and nothing was silently rebound")

    def test_O8_a_window_holding_an_older_offer_than_the_approval_finds_the_approval(self):
        view = self.of_record()
        first = self.offer_for(view)
        self.clock.now = first["expires_at"]                # the first window's offer lapses ...
        second = self.offer_for(view)                       # ... a second window is shown a new one
        self.assertNotEqual(first["expires_at"], second["expires_at"])
        self.approve(view, offer=second)
        again = self.approve(view, offer=first)             # the first window finally clicks
        self.assertTrue(again["decision"]["idempotent"])
        self.assertEqual(len(self.authorizations()), 1)
        self.assertEqual(self.authorization()["expires_at"], second["expires_at"])

    def test_O9_what_reaches_the_approval_is_the_retained_expiry_never_the_recomputed_one(self):
        view = self.of_record()
        shown = self.offer_for(view)
        self.clock.now += 200
        seen = []
        real = self.panel_surface.attest_approval

        def spy(*args, **kwargs):
            seen.append(args[6] if len(args) > 6 else kwargs.get("expires_at"))
            return real(*args, **kwargs)
        self.panel_surface.attest_approval = spy
        self.approve(view, offer=shown)
        self.assertEqual(seen, [shown["expires_at"]])
        self.assertNotEqual(seen[0], self.clock.now + 900,
                            "a freshly computed expiry must never be what is bound")


class WithdrawAndReviseTests(PanelFixture):

    def test_W1_withdrawal_is_a_withdrawal_permanently_closed(self):
        view = self.of_record()
        out = self.service.withdraw(view["request_ref"], **self.binding_of(view))
        self.assertEqual(out["request"]["lifecycle"]["stage"], "withdrawn")
        self.assertEqual(out["request"]["lifecycle"]["actions"], [])
        # A second withdrawal is refused as already withdrawn, not silently ok.
        self.core_refusal("local_request_cancelled", self.service.withdraw,
                          view["request_ref"], **self.binding_of(view))

    def test_W2_a_revision_is_withdrawal_plus_a_separate_replacement(self):
        old = self.of_record(objective="first wording")
        new = self.of_record(supersedes=old["request_ref"], objective="second wording")
        self.assertNotEqual(old["request_ref"], new["request_ref"])
        self.assertNotEqual(old["mission_id"], new["mission_id"])
        self.assertEqual(new["revision"], 1, "a replacement is a NEW Mission at revision 1")
        self.assertEqual(new["lifecycle"]["stage"], "of_record")
        self.assertEqual(new["lineage"]["supersedes"], old["request_ref"])
        gone = self.service.request_view(old["request_ref"])
        self.assertEqual(gone["lifecycle"]["stage"], "revised")
        self.assertEqual(gone["lineage"]["superseded_by"], new["request_ref"])
        # History preserved: the withdrawn record keeps its own binding.
        self.assertEqual(gone["proposal"]["objective"], "first wording")
        self.assertEqual(gone["mission_id"], old["mission_id"])
        self.assertEqual(gone["cancellation"]["revision"], 1)
        self.assertEqual(gone["cancellation"]["proposal_digest_sha256"],
                         old["proposal_digest_sha256"])

    def test_W3_a_proposal_cannot_be_revised_once_it_is_approved(self):
        old = self.of_record()
        self.approve(old)
        turn = self.drafted(objective="too late")
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED,
                     self.service.submit_draft, turn["turn_id"],
                     turn["draft"]["proposal_digest_sha256"], old["request_ref"])
        self.assertEqual(self.stage(old["request_ref"]), "approved")

    def test_W4_without_the_key_nothing_is_withdrawn_and_nothing_is_replaced(self):
        old = self.of_record(objective="first wording")
        restarted = self.build()          # the key was held in memory only
        view = restarted.request_view(old["request_ref"])
        self.assertEqual(view["lifecycle"]["actions"], ["decide"])
        self.assertIn("no longer holds the key", view["lifecycle"]["notice"])
        self.refusal(service_module.PROBLEM_CAPABILITY_UNAVAILABLE, restarted.withdraw,
                     old["request_ref"], **self.binding_of(old))
        turn = self.drafted(objective="second wording")
        self.refusal(service_module.PROBLEM_CAPABILITY_UNAVAILABLE, restarted.submit_draft,
                     turn["turn_id"], turn["draft"]["proposal_digest_sha256"],
                     old["request_ref"])
        self.assertEqual(restarted.request_view(old["request_ref"])["lifecycle"]["stage"],
                         "of_record")
        self.assertIsNone(self.journal.turn(turn["turn_id"])["submission"])

    def test_W5_a_replacement_retried_after_the_old_one_is_withdrawn_does_not_need_the_key(self):
        old = self.of_record(objective="first wording")
        self.service.withdraw(old["request_ref"], **self.binding_of(old))
        restarted = self.build()
        turn = self.drafted(objective="second wording")
        new = restarted.submit_draft(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"],
            old["request_ref"])["request"]
        self.assertEqual(new["lineage"]["supersedes"], old["request_ref"])
        self.assertEqual(restarted.request_view(old["request_ref"])["lifecycle"]["stage"],
                         "revised")


class DuplicatesAndTwoWindowsTests(PanelFixture):

    def test_X1_a_duplicate_submit_click_makes_one_proposal(self):
        turn = self.drafted()
        digest = turn["draft"]["proposal_digest_sha256"]
        first = self.service.submit_draft(turn["turn_id"], digest)
        again = self.service.submit_draft(turn["turn_id"], digest)
        self.assertTrue(first["created"])
        self.assertFalse(again["created"])
        self.assertEqual(again["request"]["request_ref"], first["request"]["request_ref"])
        self.assertEqual(len(self.panel_surface.list_requests()["requests"]), 1)

    def test_X2_two_windows_submitting_at_once_make_one_proposal(self):
        turn = self.drafted()
        digest = turn["draft"]["proposal_digest_sha256"]
        other = self.build()
        results, errors = [], []

        def submit(service):
            try:
                results.append(service.submit_draft(turn["turn_id"], digest))
            except Exception as exc:       # pragma: no cover - reported below
                errors.append(exc)
        threads = [threading.Thread(target=submit, args=(s,))
                   for s in (self.service, other)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(self.panel_surface.list_requests()["requests"]), 1)
        self.assertEqual(sorted(r["created"] for r in results), [False, True])

    def test_X3_a_duplicate_dispatch_click_starts_one_run(self):
        view = self.of_record()
        self.approve(view)
        self.service.run(view["request_ref"], "dispatch")
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, self.service.run,
                     view["request_ref"], "dispatch")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_X4_two_windows_dispatching_at_once_start_one_run(self):
        # Two windows are two connections to ONE adapter: one service object.
        view = self.of_record()
        self.approve(view)
        outcomes = []

        def dispatch(service):
            try:
                outcomes.append(("ok", service.run(view["request_ref"], "dispatch")))
            except Exception as exc:
                outcomes.append(("refused", exc))
        threads = [threading.Thread(target=dispatch, args=(s,))
                   for s in (self.service, self.service)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(len(self.spawn.calls), 1)
        self.assertEqual(sorted(kind for kind, _ in outcomes), ["ok", "refused"])

    def test_X5_a_window_that_reconnects_re_reads_the_records_not_its_last_screen(self):
        view = self.of_record()
        self.approve(view)
        reconnected = self.build()
        again = reconnected.request_view(view["request_ref"])
        self.assertEqual(again["lifecycle"]["stage"], "approved")
        listed = reconnected.overview()["requests"]
        self.assertEqual([r["request_ref"] for r in listed], [view["request_ref"]])
        self.assertEqual(listed[0]["lifecycle"]["stage"], "approved")

    def test_X6_a_reload_shows_the_conversation_history_as_history_not_state(self):
        turn = self.drafted()
        reloaded = self.build()
        shown = reloaded.conversation_view(turn["conversation_id"])["turns"]
        self.assertEqual(shown[-1]["user_text"], "make the probe stable")
        self.assertEqual(shown[-1]["provider_text"], "untrusted")
        # The draft is still a draft: nothing of record came back with it.
        self.assertEqual(reloaded.overview()["requests"], [])


class UncertaintyTests(PanelFixture):

    def test_U1_an_uncertain_submission_is_shown_as_uncertain_and_never_retried(self):
        turn = self.drafted()
        digest = turn["draft"]["proposal_digest_sha256"]
        real = self.panel_surface.submit
        calls = []

        def lose_the_response(proposal):
            calls.append(proposal)
            real(proposal)                       # the proposal DID land ...
            raise RuntimeError("the response was lost")   # ... the answer did not
        self.panel_surface.submit = lose_the_response
        out = self.service.submit_draft(turn["turn_id"], digest)
        self.assertEqual(out["submission"]["state"], "UNCERTAIN")
        self.assertIsNone(out["request"])
        again = self.service.submit_draft(turn["turn_id"], digest)
        self.assertFalse(again["created"])
        self.assertEqual(again["submission"]["state"], "UNCERTAIN")
        self.assertEqual(len(calls), 1, "an uncertain submission is not retried")
        # Reconciled from the existing records: the matching proposal is NAMED,
        # never silently linked.
        shown = self.service.conversation_view(turn["conversation_id"])["turns"][-1]
        listed = self.panel_surface.list_requests()["requests"]
        self.assertEqual(shown["submission"]["possible_matches"],
                         [listed[0]["request_ref"]])

    def test_U2_a_start_whose_outcome_is_unknown_is_a_HOLD_never_started_again(self):
        view = self.of_record()
        self.approve(view)
        self.spawn.raises = "after_child"
        try:
            self.service.run(view["request_ref"], "dispatch")
        except Exception:
            pass
        held = self.service.request_view(view["request_ref"])
        self.assertEqual(held["lifecycle"]["stage"], "dispatch_uncertain")
        self.assertEqual(held["lifecycle"]["actions"], ["reconcile"])
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, self.service.run,
                     view["request_ref"], "dispatch")
        self.assertEqual(len(self.spawn.calls), 1)

    def test_U3_a_process_that_died_mid_turn_leaves_an_UNCERTAIN_turn_not_a_rerun(self):
        dead = self.journal.reserve_turn(None, "was in flight", "gw-0", self.clock(), set())
        rerun = FakeSpawn()
        restarted = self.build(provider=rerun)
        restarted.start()
        shown = restarted.conversation_view(dead["conversation_id"])["turns"][0]
        self.assertEqual(shown["state"], "UNCERTAIN")
        self.assertEqual(rerun.calls, [])

    def test_U4_an_unknown_approval_outcome_is_checked_against_Mission_Core_not_reapplied(self):
        view = self.of_record()
        real = self.panel_surface._missions.apply_operator_attested_approval

        def lose_the_response(*args, **kwargs):
            real(*args, **kwargs)                 # Mission Core DID record it ...
            raise RuntimeError("the response was lost")   # ... the answer was lost
        self.panel_surface._missions.apply_operator_attested_approval = lose_the_response
        self.core_refusal("local_request_attestation_hold", self.approve, view)
        self.assertEqual(len(self.authorizations()), 1)
        held = self.service.request_view(view["request_ref"])
        self.assertEqual(held["lifecycle"]["stage"], "approval_uncertain")
        self.assertEqual(held["lifecycle"]["actions"], ["check_approval"])
        self.panel_surface._missions.apply_operator_attested_approval = real
        checked = self.service.check_approval(view["request_ref"])["request"]
        self.assertEqual(checked["lifecycle"]["stage"], "approved")
        self.assertEqual(len(self.authorizations()), 1, "reconciled, not applied again")


class RunGatingTests(PanelFixture):

    def started(self):
        view = self.of_record()
        self.approve(view)
        self.service.run(view["request_ref"], "dispatch")
        self.observer.raw = raw_observation()
        self.service.run(view["request_ref"], "observe")
        return view

    def test_G1_verify_is_refused_while_the_team_works_and_the_run_is_untouched(self):
        view = self.started()
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, self.service.run,
                     view["request_ref"], "verify")
        self.assertEqual(self.mission(view["mission_id"])["state"], "RUNNING")

    def test_G2_a_team_that_stopped_in_error_is_offered_the_decision_with_a_warning(self):
        view = self.started()
        self.finish_team(view["mission_id"], status="ERROR")
        shown = self.service.request_view(view["request_ref"])
        self.assertEqual(shown["lifecycle"]["actions"], ["verify"])
        self.assertIn("would not verify", shown["lifecycle"]["notice"])
        stopped = self.service.run(view["request_ref"], "verify")["request"]
        self.assertEqual(stopped["lifecycle"]["stage"], "blocked")

    def test_G3_a_proposal_that_cannot_run_is_never_offered_a_start(self):
        turn = self.say("just look", "```mission-proposal\n%s\n```" % json.dumps(
            dict(GOOD, requested_action_scope=["repository_read"])))
        view = self.service.submit_draft(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"])["request"]
        approved = self.approve(view)["request"]
        self.assertEqual(approved["lifecycle"]["actions"], [])
        self.assertIn("engineering_change", approved["lifecycle"]["notice"])
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, self.service.run,
                     view["request_ref"], "dispatch")

    def test_G4_a_panel_without_a_run_decides_but_never_starts(self):
        service = self.build(bridge=None)
        view = self.of_record()
        approved = service.decide(view["request_ref"],
                                  **self.decide_args(view))["request"]
        self.assertEqual(approved["lifecycle"]["actions"], [])
        self.refusal(service_module.PROBLEM_RUN_UNAVAILABLE, service.run,
                     view["request_ref"], "dispatch")
        self.assertEqual(self.spawn.calls, [])

    def test_G5_an_unknown_command_is_refused_before_anything_is_read(self):
        view = self.of_record()
        for command in ("pause", "cancel", "prove", "dispatch; rm", "", None, 7):
            with self.subTest(command=command):
                self.refusal(service_module.PROBLEM_BAD_REQUEST, self.service.run,
                             view["request_ref"], command)

    def test_G6_the_result_is_only_retrievable_for_a_verified_run(self):
        view = self.started()
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, self.service.result,
                     view["request_ref"])

    def test_G7_a_result_that_is_gone_is_unrecoverable_not_verified(self):
        view = self.started()
        self.finish_team(view["mission_id"])
        self.service.run(view["request_ref"], "verify")
        os.unlink(os.path.join(self.ws(view["mission_id"]), ".herd", "state",
                               "task-checkpoint.md"))
        result = self.service.result(view["request_ref"])
        self.assertFalse(result["recoverable"])
        self.assertNotIn("result_text", result)

    def test_G8_a_huge_result_is_cut_for_display_with_the_cut_stated(self):
        view = self.started()
        # Bigger than the display bound, within the 64 KiB the hardened reader
        # admits (a larger file is refused at verification, never partly read).
        big = "# Task checkpoint\n\n" + "x" * (service_module.MAX_RESULT_TEXT_CHARS + 2000)
        self.finish_team(view["mission_id"], artifacts=False)
        self.write_artifacts(view["mission_id"], result=big)
        self.service.run(view["request_ref"], "verify")
        result = self.service.result(view["request_ref"])
        self.assertEqual(len(result["result_text"]), service_module.MAX_RESULT_TEXT_CHARS)
        self.assertTrue(result["result_truncated"])
        self.assertFalse(result["review_truncated"])


class UnsafeInputAndPrivacyTests(PanelFixture):

    def test_P1_malformed_identifiers_are_refused_before_any_read_or_write(self):
        before = os.listdir(self.state) if os.path.exists(self.state) else []
        hostile = ("../../etc/passwd", "lr-" + "0" * 31, "LR-" + "0" * 32, "", None, 7,
                   "lr-" + "g" * 32, "x" * 10000, {"a": 1}, ["lr-" + "0" * 32])
        for value in hostile:
            for call in (self.service.request_view, self.service.decision_offer,
                         self.service.check_approval, self.service.result):
                with self.subTest(value=repr(value)[:30], call=call.__name__):
                    self.refusal(service_module.PROBLEM_BAD_REQUEST, call, value)
        for value in hostile:
            with self.subTest(conversation=repr(value)[:30]):
                self.refusal(service_module.PROBLEM_BAD_REQUEST,
                             self.service.conversation_view, value)
        self.assertEqual(os.listdir(self.state) if os.path.exists(self.state) else [], before)

    def test_P2_malformed_decision_and_submission_fields_are_refused(self):
        view = self.of_record()
        for field, bad in (("revision", 0), ("revision", True), ("revision", "1"),
                           ("revision", 1.0), ("mission_id", 7), ("mission_id", ""),
                           ("proposal_digest_sha256", "A" * 64),
                           ("proposal_digest_sha256", "a" * 63)):
            with self.subTest(field=field, bad=bad):
                args = self.decide_args(view)
                args[field] = bad
                with self.assertRaises((service_module.PanelRefusal,
                                        surface_module.LocalRequestRefusal)):
                    self.service.decide(view["request_ref"], **args)
        self.assertEqual(self.authorizations(), {})

    def test_P3_unsafe_words_are_refused_before_the_provider_or_the_journal(self):
        for text in ("", "   ", 7, None, "lone \ud800 surrogate",
                     "x" * (journal_module.MAX_TURN_TEXT_CHARS + 1)):
            with self.subTest(text=repr(text)[:20]):
                with self.assertRaises(journal_module.JournalError):
                    self.service.send_turn(None, text)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.journal.load()["turns"], {})

    def test_P4_no_view_leaks_a_capability_a_path_an_internal_id_or_a_secret(self):
        turn = self.drafted()
        view = self.service.submit_draft(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"])
        token = self.service._capabilities[view["request"]["request_ref"]]
        self.approve(view["request"])
        self.service.run(view["request"]["request_ref"], "dispatch")
        self.observer.raw = raw_observation()
        self.service.run(view["request"]["request_ref"], "observe")
        everything = json.dumps([
            self.service.overview(), view,
            self.service.request_view(view["request"]["request_ref"]),
            self.service.conversation_view(turn["conversation_id"])], sort_keys=True)
        for needle, why in (
                (token, "the withdrawal key"),
                (store_module.token_digest(token), "its digest"),
                (store_module.TOKEN_PREFIX, "a capability prefix"),
                ("control_capabilities", "the capability table"),
                (self.tmp.name, "a temp path"),
                (self.control, "the control repository path"),
                (self.root, "the workspaces root"),
                (self.repository, "the workspace repository"),
                ("mission_request_id", "an internal request id"),
                ("gateway_request_id", "a gateway request id"),
                ("Bearer", "an authorization header")):
            self.assertNotIn(needle, everything, why)

    def test_P5_the_same_proposal_always_carries_its_source_identity(self):
        view = self.of_record()
        for shown in (self.service.request_view(view["request_ref"]),
                      self.service.overview()["requests"][0]):
            self.assertEqual(shown["request_ref"], view["request_ref"])
            self.assertEqual(shown["mission_id"], view["mission_id"])
        full = self.service.request_view(view["request_ref"])
        self.assertEqual(full["revision"], 1)
        self.assertEqual(full["proposal_digest_sha256"],
                         mission_record.proposal_digest(full["proposal"]))

    def test_P6_the_overview_is_bounded(self):
        for index in range(service_module.MAX_OVERVIEW_REQUESTS + 3):
            self.clock.now += 1
            self.of_record(objective="request %d" % index)
        listed = self.service.overview()
        self.assertEqual(len(listed["requests"]), service_module.MAX_OVERVIEW_REQUESTS)
        self.assertTrue(listed["requests_truncated"])
        self.assertEqual(listed["requests"][0]["created_at"], self.clock.now)


class CapabilitiesTests(PanelFixture):

    def test_K1_the_overview_states_what_the_panel_is_and_is_not(self):
        shown = self.service.overview()["capabilities"]
        self.assertTrue(shown["run_available"])
        self.assertEqual(shown["repository_url"], REPO_URL)
        self.assertEqual(shown["conversation"], "restricted_read_only_fresh_turns")
        self.assertEqual(shown["delivery_authority"], "none")
        self.assertEqual(shown["decision"]["transport"], "operator_attested_relay")
        self.assertEqual(shown["decision"]["proof"],
                         "operator_attested_not_independently_verified")
        self.assertEqual(shown["decision"]["reply"], "approve")
        self.assertEqual(shown["decision"]["approval_valid_seconds"],
                         mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS)
        self.assertEqual(shown["scope_meanings"]["engineering_change"],
                         "change files in an isolated copy of the repository")
        self.assertFalse(self.build(bridge=None).overview()["capabilities"]["run_available"])

    def test_K2_the_decision_offer_is_exactly_the_bound_proposal_and_changes_nothing(self):
        view = self.of_record()
        before = (self.mission_bytes(),)
        offer = self.service.decision_offer(view["request_ref"])
        self.assertEqual(offer["mission_id"], view["mission_id"])
        self.assertEqual(offer["revision"], 1)
        self.assertEqual(offer["proposal_digest_sha256"], view["proposal_digest_sha256"])
        self.assertEqual(offer["approved_action_scope"],
                         sorted(view["proposal"]["requested_action_scope"]))
        self.assertEqual(offer["approved_delivery_targets"], [])
        self.assertEqual(offer["approval_valid_seconds"],
                         mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS)
        self.assertEqual(offer["expires_at"], offer["server_now"]
                         + mission_record.MAX_ATTESTED_APPROVAL_VALIDITY_SECONDS)
        self.assertEqual(offer["reply"], "approve")
        self.assertEqual((self.mission_bytes(),), before)
        self.assertEqual(self.authorizations(), {})


class ProgressionTests(PanelFixture):
    """The panel reports whether a progression runtime is passing, and what it
    offers follows that. The probe is a plain injected callable here; the real
    probe and the real runtime are exercised in the status and entry-point
    suites."""

    RUNNING = {"state": "running", "detail": "the progression runtime is passing",
               "passes": 4, "interval_seconds": 5, "last_pass_age_seconds": 2}
    STOPPED = {"state": "stopped", "detail": "no progression runtime has reported here",
               "passes": None, "interval_seconds": None, "last_pass_age_seconds": None}

    def started(self, service):
        view = self.of_record()
        self.approve(view)
        service.run(view["request_ref"], "dispatch")
        return view

    def test_PS1_the_capability_reports_the_probe_and_is_absent_without_a_run(self):
        service = self.build(progression=lambda: dict(self.RUNNING))
        self.assertEqual(service.overview()["capabilities"]["progression"]["state"], "running")
        self.assertEqual(service.overview()["capabilities"]["progression"]["passes"], 4)
        self.assertIsNone(
            self.build(bridge=None, progression=lambda: dict(self.RUNNING)
                       ).overview()["capabilities"]["progression"])
        self.assertIsNone(self.build().overview()["capabilities"]["progression"])

    def test_PS2_a_probe_that_fails_is_unreadable_never_running(self):
        def broken():
            raise OSError("synthetic: /private/path/that/must/not/leak")
        shown = self.build(progression=broken).overview()["capabilities"]["progression"]
        self.assertEqual(shown["state"], "unreadable")
        self.assertIn("OSError", shown["detail"])
        self.assertNotIn("private", shown["detail"])

    def test_PS3_only_the_documented_probe_fields_reach_a_window(self):
        leaky = dict(self.RUNNING, path="/private/state", token="secret")
        shown = self.build(progression=lambda: leaky).overview()["capabilities"]["progression"]
        self.assertEqual(sorted(shown), ["detail", "interval_seconds",
                                         "last_pass_age_seconds", "passes", "state"])

    def test_PS4_while_the_runtime_passes_observing_is_not_a_button(self):
        service = self.build(progression=lambda: dict(self.RUNNING))
        view = self.started(service)
        shown = service.request_view(view["request_ref"])
        self.assertEqual(shown["lifecycle"]["stage"], "dispatched")
        self.assertEqual(shown["lifecycle"]["actions"], [])
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, service.run,
                     view["request_ref"], "observe")
        self.assertEqual(self.mission(view["mission_id"])["state"], "AUTHORIZED")

    def test_PS5_with_the_runtime_stopped_the_fallback_is_offered_and_the_screen_says_why(self):
        service = self.build(progression=lambda: dict(self.STOPPED))
        view = self.started(service)
        shown = service.request_view(view["request_ref"])
        self.assertEqual(shown["lifecycle"]["actions"], ["observe"])
        self.assertIn("Nothing is advancing this Mission automatically",
                      shown["lifecycle"]["notice"])
        self.assertIn("no progression runtime has reported here",
                      shown["lifecycle"]["notice"])
        self.observer.raw = raw_observation()
        self.assertEqual(
            service.run(view["request_ref"], "observe")["request"]["lifecycle"]["stage"],
            "running")

    def test_PS6_a_ready_run_is_left_to_the_runtime_and_then_shows_its_result(self):
        service = self.build(progression=lambda: dict(self.RUNNING))
        view = self.started(service)
        self.observer.raw = raw_observation()
        self.bridge.advance(view["mission_id"])                  # the runtime's pass: observe
        self.finish_team(view["mission_id"])          # the panel's contract needs only run_result
        ready = service.request_view(view["request_ref"])
        self.assertEqual(ready["lifecycle"]["actions"], [])
        self.assertIn("next pass", ready["lifecycle"]["notice"])
        self.refusal(service_module.PROBLEM_ACTION_NOT_OFFERED, service.run,
                     view["request_ref"], "verify")
        self.assertEqual(self.mission(view["mission_id"])["state"], "RUNNING")
        self.bridge.advance(view["mission_id"])                  # the runtime's next pass
        done = service.request_view(view["request_ref"])
        self.assertEqual(done["lifecycle"]["stage"], "verified")
        self.assertEqual(done["lifecycle"]["actions"], ["result"])
        self.assertTrue(service.result(view["request_ref"])["recoverable"])
        self.assertEqual(len(self.spawn.calls), 1)


if __name__ == "__main__":
    unittest.main()
