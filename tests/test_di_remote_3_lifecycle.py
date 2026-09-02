"""DI-REMOTE-3 increment I3: adapter placeholder lifecycle.

Scope of THIS file: Layer 1 (the placeholder request written in the
SAME locked transaction that arms the mission), the
`required -> sending -> bound / failed_unsent / indefinite` loop step,
the deterministic id-carrying placeholder text, and the /status
branches. The Broker dispatch gate is I4 and the edit-based delivery
engine is I5; neither is claimed here.

Durable guarantees are re-read from the ON-DISK state file through a
FRESH store instance, never from `adapter._document` or from the
in-memory workflows document. That is not stylistic: the recorded
defect class is a fail-closed guarantee asserted only in memory, which
left 193 tests green while the on-disk record reloaded still ARMED.

R-9.2: every placeholder send in this file runs through a REAL
`telegram_api.TelegramApi` over a transport that RAISES `HTTPError`
for non-2xx, exactly as `urlopen` does. No adapter-level test here
fakes `send_message_once` with a hand-made return value, so the
adapter is driven against the real I2 classifier.

New file, new tests only: no pre-existing test is modified.

Test ids map to the lead plan §5 matrix: T-A1, T-A2, T-A3, and the
adapter-level T-C2, T-C3, T-C4.
"""

import json
import os
import socket
import unittest

from telegram_operator import telegram_api
from workflow_authority import digest as wa_digest
from workflow_authority import record as wa_record
from workflow_authority import store as wa_store

from test_mission import MissionCase, NOW, cb_update, msg_update
from test_di_remote_3_transport import (
    ScriptedTransport, api_ok, api_refusal, http_error,
)

TOKEN = "12345:SECRET-TOKEN-VALUE"


class SimulatedCrash(Exception):
    """The process dying between a send and its durable outcome."""


class PlaceholderApi(object):
    """The harness's Telegram fake, plus a REAL `send_message_once`.

    Everything the existing mission flow uses is delegated untouched to
    the harness's `MissionApi`. `send_message_once` is served by a
    genuine `telegram_api.TelegramApi` over a `ScriptedTransport`, so
    the adapter exercises the real I2 three-valued classifier and the
    real HTTPError body-parsing path rather than a hand-made outcome.
    """

    def __init__(self, inner, script, crash_after=None):
        self._inner = inner
        self.transport = ScriptedTransport(script)
        self._api = telegram_api.TelegramApi(
            TOKEN, transport=self.transport, sleeper=lambda s: None
        )
        self.once_calls = []
        # crash_after=N: the Nth send goes through the REAL transport
        # (so that object's request genuinely WAS issued) and the
        # process then dies before its outcome is recorded.
        self._crash_after = crash_after

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def timeline(self):
        return self._inner.timeline

    def send_message_once(self, chat_id, text):
        self.once_calls.append({"chat_id": chat_id, "text": text})
        outcome = self._api.send_message_once(chat_id, text)
        if (self._crash_after is not None
                and len(self.once_calls) >= self._crash_after):
            raise SimulatedCrash(
                "process died after the transport attempt for %r"
                % (text.splitlines()[1],)
            )
        return outcome


class LifecycleCase(MissionCase):
    """A mission harness whose placeholder sends run through the real
    transport seam."""

    def harness_with(self, script, gateway_script=None,
                     crash_after=None):
        harness = self.harness(gateway_script)
        harness.api = PlaceholderApi(
            harness.api, script, crash_after=crash_after
        )
        harness.adapter.api = harness.api
        return harness

    def approve_many(self, harness, count, start_update_id=100):
        """Offer and approve `count` missions, yielding wf-0001..N.

        Update ids increase MONOTONICALLY across the whole sequence:
        the adapter advances its offset on every processed update, so
        an offer numbered below an earlier approval would be dropped as
        already-seen and the fixture would silently build fewer
        workflows than the test believes.
        """
        ids = []
        update_id = start_update_id
        for index in range(count):
            harness.offer_mission(uid=update_id)
            update_id += 1
            bound = harness.bound_message_id()
            workflow_id = "wf-%04d" % (index + 1)
            harness.adapter.process_update(
                cb_update(
                    update_id, "A:%s" % workflow_id, message_id=bound,
                )
            )
            update_id += 1
            ids.append(workflow_id)
        # Anti-vacuity: every workflow really exists on disk and is
        # eligible, so a fixture that quietly built fewer fails HERE
        # rather than passing a weaker version of the test.
        on_disk = wa_store.WorkflowStore(harness.tmpdir).load()
        self.assertEqual(sorted(on_disk["workflows"]), sorted(ids))
        for workflow_id in ids:
            placeholder = (
                on_disk["workflows"][workflow_id]["result_placeholder"]
            )
            self.assertIsNotNone(placeholder, workflow_id)
            self.assertEqual(
                placeholder["state"],
                wa_record.PLACEHOLDER_REQUIRED, workflow_id,
            )
        return ids

    def approve_second_workflow(self, harness, update_id=900):
        """Offer and approve one more mission in an existing harness."""
        harness.offer_mission(uid=update_id)
        bound = harness.bound_message_id()
        existing = sorted(
            wa_store.WorkflowStore(harness.tmpdir).load()["workflows"]
        )
        workflow_id = existing[-1]
        harness.adapter.process_update(
            cb_update(update_id + 1, "A:%s" % workflow_id,
                      message_id=bound)
        )
        self.assertEqual(
            self.states_on_disk(harness)[workflow_id],
            wa_record.PLACEHOLDER_REQUIRED,
        )
        return workflow_id

    def states_on_disk(self, harness):
        """Every workflow's placeholder state, from the STATE FILE."""
        workflows = wa_store.WorkflowStore(harness.tmpdir).load()
        return {
            workflow_id: (
                entry["result_placeholder"] or {}
            ).get("state")
            for workflow_id, entry in workflows["workflows"].items()
        }

    def approve(self, harness, uid=1, update_id=10):
        harness.offer_mission(uid=uid)
        bound = harness.bound_message_id()
        harness.adapter.process_update(
            cb_update(update_id, "A:wf-0001", message_id=bound)
        )
        return bound

    def on_disk(self, harness, workflow_id="wf-0001"):
        """RESTART PROBE: a FRESH WorkflowStore, read from the file."""
        return wa_store.WorkflowStore(
            harness.tmpdir
        ).load()["workflows"][workflow_id]

    def raw_on_disk(self, harness, workflow_id="wf-0001"):
        """The literal bytes on disk, with no normalizer and no
        validation — so a test cannot be satisfied by a value the load
        path synthesized."""
        path = os.path.join(harness.tmpdir, wa_store.WORKFLOWS_FILE_NAME)
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)["workflows"][workflow_id]

    def placeholder_sends(self, harness):
        """EVERY Telegram message carrying the placeholder header,
        from BOTH surfaces.

        Reviewer round-04 B-1b: counting only `once_calls` made an
        ADDED `send_message(...)` invisible — that call is delegated
        through `PlaceholderApi.__getattr__` to the inner fake and
        lands in the harness TIMELINE, which the old helper never
        read. A mutant that left `send_message_once` in place and
        added a chunking send beside it therefore survived, while
        issuing two Telegram messages for one placeholder.

        The guarantee is "the placeholder path issues exactly ONE
        Telegram message", so it must be counted over every path that
        can issue one.
        """
        from telegram_operator.adapter import PLACEHOLDER_MESSAGE_HEADER
        sends = [
            dict(call, via="send_message_once")
            for call in harness.api.once_calls
            if PLACEHOLDER_MESSAGE_HEADER in call["text"]
        ]
        sends.extend(
            dict(entry[1], via="send_message")
            for entry in harness.timeline
            if entry[0] == "sendMessage"
            and PLACEHOLDER_MESSAGE_HEADER in (entry[1].get("text") or "")
        )
        return sends

    def chunking_placeholder_sends(self, harness):
        """Placeholder traffic that went through the CHUNKING,
        RETRYING `send_message` — must always be empty."""
        return [
            call for call in self.placeholder_sends(harness)
            if call["via"] == "send_message"
        ]

    def assert_exactly_one_placeholder_message(self, harness, note=""):
        sends = self.placeholder_sends(harness)
        self.assertEqual(
            len(sends), 1,
            "%sthe placeholder path must issue EXACTLY ONE Telegram"
            " message; got %r" % (note and note + ": ", sends),
        )
        self.assertEqual(
            self.chunking_placeholder_sends(harness), [],
            "%sthe placeholder path must never touch the chunking"
            " sender" % (note and note + ": "),
        )
        return sends


class Layer1AtomicityTests(LifecycleCase):

    def test_A1_approval_durably_requests_placeholder_in_same_transaction(
        self
    ):
        """T-A1: approval writes the `required` placeholder request in
        the SAME locked load-modify-save transaction that arms the
        mission — asserted from the ON-DISK STATE FILE.

        This is the property that makes `result_placeholder is None`
        provably mean "legacy record" rather than "go-forward workflow
        that lost its request". It is re-read from a FRESH store
        instance and from the RAW bytes, never from
        `adapter._document` or from the in-memory workflows dict: the
        recorded defect class is exactly a fail-closed guarantee that
        was true in memory and false on disk.
        """
        harness = self.harness_with([api_ok({"message_id": 1})])
        # Before approval there is no placeholder at all.
        harness.offer_mission()
        self.assertIsNone(
            self.on_disk(harness)["result_placeholder"],
            "a merely OFFERED mission must not have a placeholder",
        )
        bound = harness.bound_message_id()
        harness.adapter.process_update(
            cb_update(10, "A:wf-0001", message_id=bound)
        )

        # --- the probe: FRESH store instance, read from the file ---
        entry = self.on_disk(harness)
        placeholder = entry["result_placeholder"]
        self.assertIsNotNone(
            placeholder,
            "approval must durably request the placeholder; a record"
            " armed without one would be indistinguishable from a"
            " legacy record",
        )
        self.assertEqual(
            placeholder["state"], wa_record.PLACEHOLDER_REQUIRED
        )
        self.assertEqual(
            placeholder["chat_id"], entry["telegram"]["chat_id"]
        )
        self.assertEqual(placeholder["requested_at"], NOW)
        for key in ("message_id", "sent_at", "bound_at", "text_digest"):
            self.assertIsNone(placeholder[key], key)

        # --- and from the RAW bytes, with no load-path normalization ---
        raw = self.raw_on_disk(harness)
        self.assertEqual(
            raw["result_placeholder"]["state"],
            wa_record.PLACEHOLDER_REQUIRED,
        )
        # ARMED and REQUESTED landed together: the mission is consumed
        # in the same bytes that carry the request.
        self.assertIsNotNone(raw["approval"]["consumed_at"])
        self.assertEqual(
            raw["approval"]["decision"], wa_record.DECISION_APPROVE
        )

    def test_A1_one_save_carries_both_arming_and_request(self):
        """T-A1 (b): the atomicity is STRUCTURAL, not incidental.

        The harness records every workflow-store save. The save that
        first shows the mission consumed is the SAME save that first
        shows the placeholder requested — so no window exists in which
        a record is armed without a request.
        """
        harness = self.harness_with([api_ok({"message_id": 1})])
        self.approve(harness)
        first_armed = first_requested = None
        for index, document in enumerate(harness.wf_saves()):
            entry = document.get("workflows", {}).get("wf-0001")
            if entry is None:
                continue
            if (first_armed is None
                    and entry["approval"]["consumed_at"] is not None):
                first_armed = index
            if (first_requested is None
                    and entry.get("result_placeholder") is not None):
                first_requested = index
        self.assertIsNotNone(first_armed, "the mission was never armed")
        self.assertIsNotNone(
            first_requested, "the placeholder was never requested"
        )
        self.assertEqual(
            first_armed, first_requested,
            "arming and the placeholder request must land in the SAME"
            " save; they appeared first in saves %r and %r"
            % (first_armed, first_requested),
        )

    def test_A2_failed_approval_save_arms_nothing_and_requests_nothing(
        self
    ):
        """T-A2: if the arming save cannot land, NOTHING is armed and
        NOTHING is requested — no partial write.

        The store is made unwritable at save time. On disk the record
        must still be un-consumed AND placeholder-free; a record with a
        request but no arming, or arming but no request, would falsify
        Layer 1.
        """
        harness = self.harness_with([api_ok({"message_id": 1})])
        harness.offer_mission()
        before = self.raw_on_disk(harness)
        self.assertIsNone(before["approval"]["consumed_at"])
        self.assertIsNone(before["result_placeholder"])

        def refuse_save(document):
            raise wa_store.StoreError("disk is gone")

        original = harness.workflow_store.save
        harness.workflow_store.save = refuse_save
        try:
            bound = harness.bound_message_id()
            harness.adapter.process_update(
                cb_update(10, "A:wf-0001", message_id=bound)
            )
        finally:
            harness.workflow_store.save = original

        after = self.raw_on_disk(harness)
        self.assertIsNone(
            after["approval"]["consumed_at"],
            "a failed save must arm nothing",
        )
        self.assertIsNone(
            after["result_placeholder"],
            "a failed save must request nothing",
        )
        # Nothing was sent either.
        self.assertEqual(self.placeholder_sends(harness), [])
        # And the human is told the decision was refused.
        answers = [a for a in harness.answers() if a.get("text")]
        self.assertTrue(
            any("refused" in a["text"].lower() for a in answers),
            "a refused decision must be acknowledged honestly: %r"
            % (answers,),
        )

    def test_a_rejected_mission_requests_no_placeholder(self):
        """A REJECT arms nothing and will never run, so it must not
        create a placeholder object in the chat."""
        harness = self.harness_with([api_ok({"message_id": 1})])
        harness.offer_mission()
        bound = harness.bound_message_id()
        harness.adapter.process_update(
            cb_update(10, "R:wf-0001", message_id=bound)
        )
        self.assertIsNone(
            self.on_disk(harness)["result_placeholder"]
        )
        harness.adapter.ensure_result_placeholders()
        self.assertEqual(self.placeholder_sends(harness), [])


class PlaceholderLifecycleTests(LifecycleCase):

    def test_required_becomes_bound_through_the_real_classifier(self):
        """The happy path, end to end: `required` -> `sending`
        write-ahead -> ONE send -> `bound`, all durable."""
        harness = self.harness_with([api_ok({"message_id": 4242})])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()

        sends = self.assert_exactly_one_placeholder_message(harness)
        # Strategy §1.3: the text carries the workflow id, so a human
        # can identify the object in the chat.
        self.assertIn("wf-0001", sends[0]["text"])
        self.assertEqual(len(harness.api.transport.calls), 1)

        placeholder = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            placeholder["state"], wa_record.PLACEHOLDER_BOUND
        )
        self.assertEqual(placeholder["message_id"], 4242)
        self.assertIsNotNone(placeholder["bound_at"])
        # The digest binds the EXACT text that was sent.
        self.assertEqual(
            placeholder["text_digest"],
            wa_digest.text_digest(sends[0]["text"]),
        )
        # A bound placeholder is terminal for binding: further passes
        # send nothing.
        harness.adapter.ensure_result_placeholders()
        harness.adapter.ensure_result_placeholders()
        self.assert_exactly_one_placeholder_message(
            harness, "after further passes"
        )

    def test_L1_write_ahead_lands_before_the_transport_is_touched(self):
        """LEAD RULING L-1: `sent_at` AND `text_digest` are durable on
        disk BEFORE the send is attempted.

        Probed from inside the transport itself — the only place that
        can observe the on-disk state at the moment of the call — so a
        write-ahead that actually happened afterwards fails here.
        """
        observed = {}

        def probe(url, payload_bytes, deadline_seconds):
            observed["placeholder"] = self.on_disk(
                harness
            )["result_placeholder"]
            return api_ok({"message_id": 7})

        harness = self.harness_with([api_ok({"message_id": 7})])
        self.approve(harness)
        harness.api.transport = probe
        harness.api._api._transport = probe
        harness.adapter.ensure_result_placeholders()

        self.assertIn(
            "placeholder", observed,
            "the transport was never called; this test proves nothing",
        )
        written = observed["placeholder"]
        self.assertEqual(
            written["state"], wa_record.PLACEHOLDER_SENDING,
            "the send intent must be durable BEFORE the send",
        )
        self.assertIsNotNone(written["sent_at"], "L-1: sent_at")
        self.assertEqual(
            written["text_digest"],
            wa_digest.text_digest(
                harness.adapter_module.render_placeholder_text("wf-0001")
            ),
            "L-1: the digest of the EXACT text about to be sent must be"
            " written ahead, or an indefinite outcome leaves an"
            " unidentifiable object on screen",
        )
        self.assertIsNone(written["message_id"])
        self.assertIsNone(written["bound_at"])

    def test_C4_definite_zero_becomes_failed_unsent_and_is_retried(self):
        """T-C4 (adapter level): a PARSED Telegram refusal proves no
        message exists, so the record goes to `failed_unsent` and the
        NEXT pass retries — the safe direction is not over-frozen."""
        harness = self.harness_with([
            http_error(400, api_refusal("Bad Request: chat not found")),
            api_ok({"message_id": 55}),
        ])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        placeholder = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            placeholder["state"], wa_record.PLACEHOLDER_FAILED_UNSENT
        )
        self.assertIsNone(placeholder["message_id"])
        # L-1 fields are retained: the record still says exactly what
        # it tried to send.
        self.assertIsNotNone(placeholder["sent_at"])
        self.assertIsNotNone(placeholder["text_digest"])

        harness.adapter.ensure_result_placeholders()
        placeholder = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            placeholder["state"], wa_record.PLACEHOLDER_BOUND
        )
        self.assertEqual(placeholder["message_id"], 55)
        self.assertEqual(len(self.placeholder_sends(harness)), 2)

    def test_C2_deadline_is_indefinite_and_sends_exactly_once(self):
        """T-C2 (adapter level): a fired deadline is INDEFINITE and
        makes EXACTLY ONE transport call — the adapter must not inherit
        the blanket retry that would generate a duplicate
        placeholder."""
        harness = self.harness_with([socket.timeout("deadline")])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        self.assertEqual(len(harness.api.transport.calls), 1)
        placeholder = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            placeholder["state"], wa_record.PLACEHOLDER_INDEFINITE
        )
        self.assertIsNone(placeholder["message_id"])

    def test_C3_indefinite_is_terminal_across_restarts(self):
        """T-C3: `indefinite` is TERMINAL across restarts.

        The record is reloaded from the STATE FILE into a brand-new
        Adapter, and N further passes are run over it. The total
        transport call count must still be exactly one: a second send
        would be the duplicate-placeholder generator R-5 names.
        """
        harness = self.harness_with([socket.timeout("deadline")])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        self.assertEqual(len(harness.api.transport.calls), 1)
        self.assertEqual(
            self.on_disk(harness)["result_placeholder"]["state"],
            wa_record.PLACEHOLDER_INDEFINITE,
        )

        # RESTART: a fresh adapter over the SAME on-disk store, with a
        # transport that WOULD succeed if it were ever called.
        restarted = self.harness_with([api_ok({"message_id": 999})])
        self.assertEqual(restarted.tmpdir, harness.tmpdir)
        restarted.adapter.startup_recovery()
        for _ in range(5):
            restarted.adapter.ensure_result_placeholders()
            restarted.adapter.deliver_pending_results()

        self.assertEqual(
            restarted.api.transport.calls, [],
            "an INDEFINITE placeholder must never be re-sent, across"
            " any number of restarts or passes",
        )
        self.assertEqual(self.placeholder_sends(restarted), [])
        placeholder = self.on_disk(restarted)["result_placeholder"]
        self.assertEqual(
            placeholder["state"], wa_record.PLACEHOLDER_INDEFINITE
        )
        self.assertIsNone(placeholder["message_id"])

    def test_a_crash_interrupted_sending_fails_closed_at_startup(self):
        """Strategy §1.3's forced residual: a placeholder found in
        `sending` when the process starts cannot be reconciled — the
        Bot API offers no way to read back the bot's own outgoing
        messages — so it fails CLOSED to `indefinite` and is never
        re-sent."""
        harness = self.harness_with([api_ok({"message_id": 1})])
        self.approve(harness)
        # Simulate the crash: the write-ahead landed, the outcome did
        # not. Written through the real store, so it is a VALID record.
        text = harness.adapter_module.render_placeholder_text("wf-0001")
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            placeholder = (
                workflows["workflows"]["wf-0001"]["result_placeholder"]
            )
            placeholder["state"] = wa_record.PLACEHOLDER_SENDING
            placeholder["sent_at"] = NOW
            placeholder["text_digest"] = wa_digest.text_digest(text)
            harness.workflow_store.save(workflows)

        restarted = self.harness_with([api_ok({"message_id": 999})])
        restarted.adapter.startup_recovery()
        self.assertEqual(
            self.on_disk(restarted)["result_placeholder"]["state"],
            wa_record.PLACEHOLDER_INDEFINITE,
        )
        for _ in range(3):
            restarted.adapter.ensure_result_placeholders()
        self.assertEqual(
            restarted.api.transport.calls, [],
            "a crash-orphaned send must never be repeated",
        )

    def test_legacy_records_are_never_given_a_placeholder(self):
        """A record with `result_placeholder is None` is the legacy
        lane: the loop step must not fabricate one for it."""
        harness = self.harness_with([api_ok({"message_id": 1})])
        # R-17: a GENUINE pre-placeholder record — the real pre-I3
        # arming with the I3 placeholder request suppressed, so
        # result_placeholder is NEVER written. Not a bound record nulled
        # on disk (that forgery is what R-17 item 2 forbids).
        real_request = harness.adapter._request_result_placeholder
        harness.adapter._request_result_placeholder = (
            lambda workflows, workflow_id, now: False
        )
        try:
            self.approve(harness)
        finally:
            harness.adapter._request_result_placeholder = real_request
        self.assertIsNone(
            self.on_disk(harness)["result_placeholder"],
            "the arming must leave a genuinely null placeholder",
        )

        for index in range(3):
            try:
                harness.adapter.ensure_result_placeholders()
            except Exception as exc:
                self.fail(
                    "a legacy (null) placeholder must simply be SKIPPED"
                    " on pass %d; raised %s: %s"
                    % (index, type(exc).__name__, exc)
                )
        self.assertEqual(harness.api.transport.calls, [])
        self.assertEqual(self.placeholder_sends(harness), [])
        self.assertIsNone(
            self.on_disk(harness)["result_placeholder"],
            "the legacy lane must never be fabricated into a"
            " placeholder-bearing record",
        )

    def test_placeholder_text_is_deterministic(self):
        """R-1 discipline: the text is a pure function of the workflow
        id. `text_digest` binds it, so a text that varied between
        renders would make the recorded digest describe something not
        on screen."""
        render = None
        from telegram_operator import adapter as adapter_module
        render = adapter_module.render_placeholder_text
        first = render("wf-0001")
        for _ in range(5):
            self.assertEqual(render("wf-0001"), first)
        self.assertNotEqual(render("wf-0002"), first)
        self.assertIn("wf-0001", first)
        # The EXACT text, authored here independently of the module's
        # own constants: a test that filtered on
        # `adapter.PLACEHOLDER_MESSAGE_HEADER` would stay green through
        # any change to it, because both sides would move together.
        self.assertEqual(
            first,
            "Mission result placeholder\nworkflow: wf-0001\n\nThe"
            " verified mission result will appear in THIS message when"
            " the mission completes. This message grants no commit,"
            " push, PR, tag, release, or deploy authority.",
        )
        # No clock, no counter, no "as of".
        for forbidden in ("as of", "attempt", str(NOW)):
            self.assertNotIn(forbidden, first.lower(), forbidden)
        # It fits ONE Telegram message, so the placeholder is never
        # chunked into several objects.
        self.assertLessEqual(
            len(first), telegram_api.MAX_MESSAGE_CHARS
        )


class BatchContaminationTests(LifecycleCase):
    """RULING R-10: a crash must never contaminate workflows whose
    transport was never invoked.

    The defect this closes was real and I reproduced it first-hand
    before fixing it: `ensure_result_placeholders` claimed EVERY
    eligible record as `sending` in ONE save, so a crash after the
    FIRST object's send left the startup sweep marking the entire
    remaining batch terminal `indefinite` — 4 of 4 never-attempted
    workflows in a 5-workflow store. That is not fail-closed; it
    fabricates ambiguity for objects that were never touched, strands
    N-1 unrelated missions per crash, and makes /status report an
    unresolvable send for a message that was never sent.

    Strategy §1.2/§1.3 accept ONE object's irreducible ambiguity
    because the Bot API cannot reconcile it. That justification is PER
    OBJECT, for the object whose request was actually issued.
    """

    def test_R10_a_crash_never_contaminates_never_attempted_workflows(
        self
    ):
        """R-10 evidence 1: N=4 eligible workflows, the process dies
        after the FIRST object's transport attempt, then restarts.

        Every never-attempted workflow must still be `required` and
        still selectable, and AT MOST ONE record may be `indefinite`.
        Every assertion reads the STATE FILE through a fresh store
        instance — never in-memory adapter state (T-A1 discipline).
        """
        harness = self.harness_with(
            [api_ok({"message_id": 11})], crash_after=1
        )
        workflow_ids = self.approve_many(harness, 4)
        self.assertEqual(len(workflow_ids), 4)
        # All four are eligible before the pass.
        self.assertEqual(
            set(self.states_on_disk(harness).values()),
            {wa_record.PLACEHOLDER_REQUIRED},
        )

        with self.assertRaises(SimulatedCrash):
            harness.adapter.ensure_result_placeholders()

        # The transport was invoked for EXACTLY ONE object.
        attempted = [
            call["text"].splitlines()[1].split()[1]
            for call in harness.api.once_calls
        ]
        self.assertEqual(len(attempted), 1, attempted)
        never_attempted = [
            workflow_id for workflow_id in workflow_ids
            if workflow_id not in attempted
        ]
        self.assertEqual(len(never_attempted), 3)

        # AT THE CRASH: only the attempted object is claimed. This is
        # the assertion the batch claim fails — it would show all four
        # in `sending`.
        at_crash = self.states_on_disk(harness)
        self.assertEqual(
            at_crash[attempted[0]], wa_record.PLACEHOLDER_SENDING
        )
        for workflow_id in never_attempted:
            self.assertEqual(
                at_crash[workflow_id],
                wa_record.PLACEHOLDER_REQUIRED,
                "%s was claimed although its transport was never"
                " invoked; a crash must not claim ahead" % workflow_id,
            )

        # --- RESTART: a fresh adapter over the SAME on-disk store ---
        restarted = self.harness_with([api_ok({"message_id": 22})])
        self.assertEqual(restarted.tmpdir, harness.tmpdir)
        restarted.adapter.startup_recovery()

        after = self.states_on_disk(restarted)
        indefinite = [
            workflow_id for workflow_id, state in after.items()
            if state == wa_record.PLACEHOLDER_INDEFINITE
        ]
        self.assertLessEqual(
            len(indefinite), 1,
            "a crash may orphan AT MOST ONE placeholder — the"
            " irreducible one-object ambiguity; got %r" % (indefinite,),
        )
        self.assertEqual(indefinite, [attempted[0]])
        for workflow_id in never_attempted:
            self.assertEqual(
                after[workflow_id], wa_record.PLACEHOLDER_REQUIRED,
                "%s was never attempted (zero transport invocation ="
                " provably zero display) and must remain retryable"
                % workflow_id,
            )

        # ... and SELECTABLE: the next pass really does bind them.
        restarted.adapter.ensure_result_placeholders()
        final = self.states_on_disk(restarted)
        for workflow_id in never_attempted:
            self.assertEqual(
                final[workflow_id], wa_record.PLACEHOLDER_BOUND,
                "%s must be picked up and bound by the next pass"
                % workflow_id,
            )
        # The attempted object is NEVER re-sent (R-5): the restarted
        # transport only ever served the three never-attempted ones.
        self.assertEqual(len(restarted.api.transport.calls), 3)
        self.assertEqual(
            final[attempted[0]], wa_record.PLACEHOLDER_INDEFINITE
        )

    def test_R10_at_most_one_placeholder_is_sending_at_any_moment(self):
        """R-10's invariant, observed from INSIDE the transport.

        The store is inspected at the moment of every send, which is
        the only place the intermediate state is visible. Exactly one
        record may be `sending` — the one being sent.
        """
        observed = []

        harness = self.harness_with([api_ok({"message_id": 1})])
        workflow_ids = self.approve_many(harness, 4)
        real_transport = harness.api.transport

        def probe(url, payload_bytes, deadline_seconds):
            states = self.states_on_disk(harness)
            observed.append(sorted(
                workflow_id for workflow_id, state in states.items()
                if state == wa_record.PLACEHOLDER_SENDING
            ))
            return real_transport(url, payload_bytes, deadline_seconds)

        harness.api._api._transport = probe
        harness.adapter.ensure_result_placeholders()

        self.assertEqual(
            len(observed), len(workflow_ids),
            "every eligible workflow should have been sent once",
        )
        for sending in observed:
            self.assertEqual(
                len(sending), 1,
                "at most ONE placeholder may be in `sending` at a"
                " time; saw %r" % (sending,),
            )
        # All four end bound, so the per-object claim did not lose any.
        self.assertEqual(
            set(self.states_on_disk(harness).values()),
            {wa_record.PLACEHOLDER_BOUND},
        )

    def test_R10_the_one_object_ambiguity_is_INTACT(self):
        """R-10 evidence 3: the fix must NOT be a relaxation.

        The object whose request WAS issued still becomes `indefinite`,
        is still TERMINAL, and is still never retried across restarts.
        Making `indefinite` retryable would be the
        duplicate-placeholder generator R-5 forbids — a correctness
        regression, not a fix.
        """
        harness = self.harness_with(
            [socket.timeout("deadline")], crash_after=None
        )
        workflow_ids = self.approve_many(harness, 3)
        harness.adapter.ensure_result_placeholders()

        states = self.states_on_disk(harness)
        # Every object was attempted here, and every deadline is
        # INDEFINITE — the fix does not make ambiguity disappear.
        for workflow_id in workflow_ids:
            self.assertEqual(
                states[workflow_id], wa_record.PLACEHOLDER_INDEFINITE,
                workflow_id,
            )
        self.assertEqual(
            len(harness.api.transport.calls), len(workflow_ids)
        )

        # RESTART, with a transport that WOULD succeed if called.
        restarted = self.harness_with([api_ok({"message_id": 99})])
        restarted.adapter.startup_recovery()
        for _ in range(4):
            restarted.adapter.ensure_result_placeholders()
        self.assertEqual(
            restarted.api.transport.calls, [],
            "an INDEFINITE placeholder must never be retried, however"
            " many passes or restarts follow",
        )
        self.assertEqual(
            set(self.states_on_disk(restarted).values()),
            {wa_record.PLACEHOLDER_INDEFINITE},
        )

    def test_R10_a_failed_outcome_write_stops_the_pass(self):
        """R-10's invariant under a TRANSIENT outcome-save failure.

        If an outcome cannot be persisted the record is still
        `sending`. Claiming another object would put a SECOND record in
        `sending` and reopen the batch-contamination window, so the
        pass STOPS instead.

        FIXTURE NOTE (reviewer round-05 blocker, and the reason this
        test was rewritten). It previously refused EVERY save after the
        first. That made it green for the wrong reason: with the guard
        deleted, the next object's CLAIM save also failed, so nothing
        was sent and the guard's presence and absence were
        indistinguishable — the recorded class "the fixture must
        contain the condition the guard protects against". The failure
        is now TRANSIENT and hits ONLY the first outcome save, which is
        both the realistic shape and the one under which a later claim
        COULD succeed. Deleting the guard while keeping the call now
        produces four sends and TWO records in `sending` at once, and
        this test fails.
        """
        harness = self.harness_with([api_ok({"message_id": 5})])
        workflow_ids = self.approve_many(harness, 4)

        # Observe the store from INSIDE the transport: the only place
        # the intermediate state is visible.
        concurrent = []
        real_transport = harness.api.transport

        def probe(url, payload_bytes, deadline_seconds):
            states = self.states_on_disk(harness)
            concurrent.append(sorted(
                workflow_id for workflow_id, state in states.items()
                if state == wa_record.PLACEHOLDER_SENDING
            ))
            return real_transport(url, payload_bytes, deadline_seconds)

        harness.api._api._transport = probe

        original = harness.workflow_store.save
        saves = {"n": 0}

        def fail_only_the_first_outcome_save(document):
            # Save order is: claim#1, outcome#1, claim#2, outcome#2 ...
            # so save 2 is the FIRST outcome save. Every later save
            # succeeds, so a subsequent claim is genuinely possible —
            # which is exactly what makes the guard observable.
            saves["n"] += 1
            if saves["n"] == 2:
                raise wa_store.StoreError("transient store failure")
            return original(document)

        harness.workflow_store.save = fail_only_the_first_outcome_save
        try:
            harness.adapter.ensure_result_placeholders()
        finally:
            harness.workflow_store.save = original

        states = self.states_on_disk(harness)
        # ANTI-VACUITY: the fixture really did fail the OUTCOME save —
        # the first record is stuck `sending`, not `required`.
        self.assertEqual(
            states["wf-0001"], wa_record.PLACEHOLDER_SENDING,
            "the fixture must fail the first OUTCOME save; got %r"
            % (states,),
        )
        # The pass STOPPED: exactly one placeholder message was ever
        # issued, on either surface.
        self.assert_exactly_one_placeholder_message(
            harness, "after a transient outcome-save failure"
        )
        # ... and never two records in `sending` at once, observed
        # live rather than inferred from the end state.
        for sending in concurrent:
            self.assertLessEqual(
                len(sending), 1,
                "at most ONE placeholder may be in `sending` at a"
                " time; saw %r" % (sending,),
            )
        sending_now = [
            workflow_id for workflow_id, state in states.items()
            if state == wa_record.PLACEHOLDER_SENDING
        ]
        self.assertEqual(sending_now, ["wf-0001"])
        for workflow_id in workflow_ids[1:]:
            self.assertEqual(
                states[workflow_id], wa_record.PLACEHOLDER_REQUIRED,
                "%s must not have been claimed after the pass stopped"
                % workflow_id,
            )

    def test_R10_the_pass_resumes_normally_on_the_next_call(self):
        """Anti-vacuity for the stop: stopping is not abandoning.

        Once the store is healthy again the next pass claims the
        remaining objects normally, so the guard costs one pass, not
        the workflows.
        """
        harness = self.harness_with([api_ok({"message_id": 5})])
        workflow_ids = self.approve_many(harness, 3)
        original = harness.workflow_store.save
        saves = {"n": 0}

        def fail_only_the_first_outcome_save(document):
            saves["n"] += 1
            if saves["n"] == 2:
                raise wa_store.StoreError("transient store failure")
            return original(document)

        harness.workflow_store.save = fail_only_the_first_outcome_save
        try:
            harness.adapter.ensure_result_placeholders()
        finally:
            harness.workflow_store.save = original
        self.assertEqual(len(self.placeholder_sends(harness)), 1)

        harness.adapter.ensure_result_placeholders()
        states = self.states_on_disk(harness)
        for workflow_id in workflow_ids[1:]:
            self.assertEqual(
                states[workflow_id], wa_record.PLACEHOLDER_BOUND,
                workflow_id,
            )
        # The stuck one is STILL not re-sent (B-1): it waits for the
        # startup sweep.
        self.assertEqual(states["wf-0001"], wa_record.PLACEHOLDER_SENDING)
        self.assertEqual(len(self.placeholder_sends(harness)), 3)
        self.assertEqual(self.chunking_placeholder_sends(harness), [])


class SendingExclusionTests(LifecycleCase):
    """Reviewer round-04 B-1: `sending` must be claimed by NOBODY, and
    that exclusion must be pinned in its own right.

    The asymmetry that caused the gap is worth naming: the round-04
    battery pinned the `indefinite` exclusion but not the `sending`
    exclusion, though both live in the same selection tuple and the
    same docstring sentence. Adding `sending` to that tuple SURVIVED a
    771-test suite while issuing a SECOND placeholder send — the
    duplicate-placeholder generator R-5 names.

    The state is REACHABLE, not an unreachable equivalent: a transient
    StoreError on the OUTCOME save leaves the record durably `sending`
    inside a LIVE process, with the startup reconciliation already
    behind it. Under R-10's per-object claim this exclusion becomes
    MORE load-bearing, not less — at most one object may be `sending`
    at a time, so a stuck one must still be skipped by every later
    pass and must still wait for the startup sweep.
    """

    def stick_in_sending(self, harness):
        """Get a record durably into `sending` the HONEST way: the
        claim lands, the send happens, the outcome save fails."""
        original = harness.workflow_store.save
        calls = {"n": 0}

        def save_then_refuse(document):
            calls["n"] += 1
            if calls["n"] > 1:
                raise wa_store.StoreError("transient store failure")
            return original(document)

        harness.workflow_store.save = save_then_refuse
        try:
            harness.adapter.ensure_result_placeholders()
        finally:
            harness.workflow_store.save = original
        state = self.states_on_disk(harness)["wf-0001"]
        self.assertEqual(
            state, wa_record.PLACEHOLDER_SENDING,
            "the fixture must really leave the record in `sending`, or"
            " this test proves nothing",
        )

    def test_B1_a_stuck_sending_record_is_never_re_claimed_in_process(
        self
    ):
        """B-1: a record stuck in `sending` inside a LIVE process is
        claimed by no later pass — ZERO additional placeholder
        messages, with no intervening startup reconcile."""
        harness = self.harness_with([api_ok({"message_id": 12})])
        self.approve(harness)
        self.stick_in_sending(harness)
        self.assert_exactly_one_placeholder_message(
            harness, "after the outcome save failed"
        )

        # NO startup reconcile here on purpose: this is the live
        # process, and `sending` must be excluded by SELECTION.
        for _ in range(5):
            harness.adapter.ensure_result_placeholders()

        self.assert_exactly_one_placeholder_message(
            harness, "after five further live passes"
        )
        self.assertEqual(
            len(harness.api.transport.calls), 1,
            "a stuck `sending` record must never be re-sent while the"
            " process is live; it waits for the startup sweep",
        )
        self.assertEqual(
            self.states_on_disk(harness)["wf-0001"],
            wa_record.PLACEHOLDER_SENDING,
        )
        # It is still resolvable the ONLY correct way: the startup
        # sweep fails it closed, and it is then never retried.
        restarted = self.harness_with([api_ok({"message_id": 99})])
        restarted.adapter.startup_recovery()
        self.assertEqual(
            self.states_on_disk(restarted)["wf-0001"],
            wa_record.PLACEHOLDER_INDEFINITE,
        )
        for _ in range(3):
            restarted.adapter.ensure_result_placeholders()
        self.assertEqual(restarted.api.transport.calls, [])

    def test_B1_anti_vacuity_failed_unsent_IS_claimed_from_the_same_shape(
        self
    ):
        """B-1's anti-vacuity half: the exclusion above is SELECTIVE,
        not a blanket refusal to ever claim again.

        Same fixture shape — a completed send whose outcome was
        recorded — but landing in `failed_unsent`, which IS claimable.
        A later pass must pick it up and send again.
        """
        harness = self.harness_with([
            http_error(400, api_refusal("Bad Request: chat not found")),
            api_ok({"message_id": 31}),
        ])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        self.assertEqual(
            self.states_on_disk(harness)["wf-0001"],
            wa_record.PLACEHOLDER_FAILED_UNSENT,
        )
        self.assertEqual(len(self.placeholder_sends(harness)), 1)

        harness.adapter.ensure_result_placeholders()
        self.assertEqual(
            self.states_on_disk(harness)["wf-0001"],
            wa_record.PLACEHOLDER_BOUND,
        )
        self.assertEqual(
            len(self.placeholder_sends(harness)), 2,
            "`failed_unsent` IS claimable — proved no message exists,"
            " so retry is safe",
        )
        self.assertEqual(self.chunking_placeholder_sends(harness), [])

    def test_the_eligibility_snapshot_lists_only_claimable_records(self):
        """The snapshot layer, pinned in its own right.

        The snapshot and the under-lock re-check are defence in depth,
        and each masks the other's absence, so each needs its own pin.
        This one asserts the snapshot never NAMES a record that is not
        claimable.
        """
        harness = self.harness_with([api_ok({"message_id": 1})])
        workflow_ids = self.approve_many(harness, 4)
        self.assertEqual(
            harness.adapter._eligible_placeholder_workflow_ids(),
            workflow_ids,
        )
        # Drive each record into a distinct non-claimable state.
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            entries = workflows["workflows"]
            entries["wf-0001"]["result_placeholder"] = None
            for workflow_id, state, extra in (
                ("wf-0002", wa_record.PLACEHOLDER_BOUND,
                 {"message_id": 5, "sent_at": 1, "bound_at": 2,
                  "text_digest": "a" * 64}),
                ("wf-0003", wa_record.PLACEHOLDER_INDEFINITE,
                 {"sent_at": 1, "text_digest": "a" * 64}),
                ("wf-0004", wa_record.PLACEHOLDER_SENDING,
                 {"sent_at": 1, "text_digest": "a" * 64}),
            ):
                placeholder = entries[workflow_id]["result_placeholder"]
                placeholder["state"] = state
                placeholder.update(extra)
            harness.workflow_store.save(workflows)

        self.assertEqual(
            harness.adapter._eligible_placeholder_workflow_ids(), [],
            "no non-claimable record may be named by the snapshot",
        )
        harness.adapter.ensure_result_placeholders()
        self.assertEqual(self.placeholder_sends(harness), [])

    def test_the_claim_re_checks_claimability_under_the_lock(self):
        """The re-check layer, pinned in its own right.

        The store is SHARED across processes, so the snapshot is a
        candidate set and never authority: a record may have moved on
        between the listing and the claim. Simulated by feeding the
        loop an id the snapshot would never have produced.
        """
        harness = self.harness_with([api_ok({"message_id": 1})])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        self.assertEqual(
            self.states_on_disk(harness)["wf-0001"],
            wa_record.PLACEHOLDER_BOUND,
        )
        before = self.on_disk(harness)["result_placeholder"]

        # The snapshot (wrongly) names an already-BOUND record.
        harness.adapter._eligible_placeholder_workflow_ids = (
            lambda: ["wf-0001"]
        )
        for index in range(3):
            # Wrapped: without the `claim is None -> continue` guard
            # the loop unpacks None and dies with a TypeError, and a
            # crash is not a kill (R-8.3 / R-11).
            try:
                harness.adapter.ensure_result_placeholders()
            except Exception as exc:
                self.fail(
                    "a record the re-check refuses must be SKIPPED on"
                    " pass %d, not raise %s: %s"
                    % (index, type(exc).__name__, exc)
                )

        self.assert_exactly_one_placeholder_message(
            harness, "a re-checked non-claimable record"
        )
        self.assertEqual(
            self.on_disk(harness)["result_placeholder"], before,
            "the under-lock re-check must refuse a record that is no"
            " longer claimable, whatever the snapshot said",
        )
        # A vanished workflow id is refused too, and does not crash.
        # A VANISHED workflow id is refused too, and does not crash.
        # The store is shared across processes, so a record named by
        # the snapshot can be pruned before the claim runs.
        harness.adapter._eligible_placeholder_workflow_ids = (
            lambda: ["wf-does-not-exist"]
        )
        try:
            harness.adapter.ensure_result_placeholders()
        except Exception as exc:
            self.fail(
                "an unknown workflow id must be skipped, not raise"
                " %s: %s" % (type(exc).__name__, exc)
            )
        self.assert_exactly_one_placeholder_message(harness)
        # ... and a MIXTURE: the vanished id must not stop the pass
        # reaching a genuinely claimable one behind it.
        second = self.approve_second_workflow(harness)
        harness.adapter._eligible_placeholder_workflow_ids = (
            lambda: ["wf-does-not-exist", second]
        )
        try:
            harness.adapter.ensure_result_placeholders()
        except Exception as exc:
            self.fail(
                "a vanished id must be skipped, not raise %s: %s"
                % (type(exc).__name__, exc)
            )
        self.assertEqual(
            self.states_on_disk(harness)[second],
            wa_record.PLACEHOLDER_BOUND,
            "a claimable record behind a vanished one must still be"
            " claimed",
        )

    def test_a_claim_that_did_not_land_authorizes_no_send(self):
        """If the write-ahead claim cannot be persisted, nothing may be
        sent for that record — L-1 in its contrapositive form."""
        harness = self.harness_with([api_ok({"message_id": 1})])
        self.approve(harness)

        def refuse(document):
            raise wa_store.StoreError("disk is gone")

        original = harness.workflow_store.save
        harness.workflow_store.save = refuse
        try:
            harness.adapter.ensure_result_placeholders()
        finally:
            harness.workflow_store.save = original

        self.assertEqual(
            self.placeholder_sends(harness), [],
            "a claim that did not land must authorize NO send",
        )
        self.assertEqual(harness.api.transport.calls, [])
        self.assertEqual(
            self.states_on_disk(harness)["wf-0001"],
            wa_record.PLACEHOLDER_REQUIRED,
            "the record stays claimable for a later pass",
        )


class OutcomeRecordingGuardTests(LifecycleCase):
    """Direct coverage of the two helpers' defensive guards.

    These are driven at unit level ON PURPOSE. The guards protect
    against a store changed underneath a pass and against a transport
    that violates its own contract; neither is reachable through the
    normal loop, so a test that only drove the loop would leave them
    unpinned — which is exactly what the first battery pass found.
    """

    def stored(self, harness):
        with wa_store.exclusive_store_lock(harness.tmpdir):
            return harness.workflow_store.load()

    def test_an_existing_placeholder_request_is_never_overwritten(self):
        """Y-17's guarantee: the request is written once. Overwriting
        would reset a bound or terminal placeholder back to
        `required` and re-send it."""
        harness = self.harness_with([api_ok({"message_id": 5})])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        before = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(before["state"], wa_record.PLACEHOLDER_BOUND)

        workflows = self.stored(harness)
        created = harness.adapter._request_result_placeholder(
            workflows, "wf-0001", NOW + 500
        )
        self.assertFalse(
            created, "an existing placeholder must not be re-requested"
        )
        self.assertEqual(
            workflows["workflows"]["wf-0001"]["result_placeholder"],
            before,
        )
        # A missing workflow id is refused, not created.
        self.assertFalse(
            harness.adapter._request_result_placeholder(
                workflows, "wf-nope", NOW
            )
        )

    def test_outcome_is_ignored_unless_the_record_is_still_sending(self):
        """Y-34: only the intent THIS pass wrote may be resolved. A
        record no longer in `sending` was changed underneath us and is
        left alone, never overwritten."""
        harness = self.harness_with([api_ok({"message_id": 5})])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        bound = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(bound["state"], wa_record.PLACEHOLDER_BOUND)

        harness.adapter._record_placeholder_outcome(
            "wf-0001", bound["text_digest"],
            telegram_api.SendOnceOutcome(
                classification=telegram_api.SEND_INDEFINITE
            ),
        )
        self.assertEqual(
            self.on_disk(harness)["result_placeholder"], bound,
            "a BOUND placeholder must not be rewritten by a late"
            " outcome",
        )

        # NOTE on the strength of this case: it is ALSO protected
        # downstream by the record schema (an `indefinite` state may
        # not carry a message_id, so the save would fail closed
        # anyway), which makes it a weak probe of THIS guard on its
        # own — it stayed green with the guard deleted. The isolating
        # probe is the next test.

    def test_a_required_placeholder_is_never_bound_by_a_stray_outcome(
        self
    ):
        """Y-34, isolated: nothing has been sent, the stored digest is
        null, and a stray `applied` outcome must NOT bind the
        placeholder to a message this pass never created.

        Unlike the BOUND case above, nothing downstream masks this:
        `required` -> `bound` with a message id is a perfectly VALID
        record, so only the `sending` guard prevents it.
        """
        harness = self.harness_with([api_ok({"message_id": 1})])
        self.approve(harness)
        before = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            before["state"], wa_record.PLACEHOLDER_REQUIRED
        )
        self.assertIsNone(before["text_digest"])
        harness.adapter._record_placeholder_outcome(
            "wf-0001", None,
            telegram_api.SendOnceOutcome(
                classification=telegram_api.SEND_APPLIED, message_id=77
            ),
        )
        after = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            after["state"], wa_record.PLACEHOLDER_REQUIRED,
            "a REQUIRED placeholder must never be bound by an outcome"
            " for a send that was never written ahead",
        )
        self.assertIsNone(after["message_id"])
        self.assertEqual(after, before)

    def test_a_failed_unsent_placeholder_is_never_bound_by_a_late_outcome(
        self
    ):
        """Y-34, the case that ISOLATES the `sending` guard.

        Two earlier probes (`bound` and `required`) are masked by the
        record schema: the state they would be rewritten into is
        INVALID, so the save fails closed and the guard's absence is
        unobservable. `failed_unsent` is different — it already carries
        `sent_at` and `text_digest`, so rewriting it to `bound` with a
        message id produces a perfectly VALID record. Only the
        `sending` guard stops it, which makes this the probe that
        actually tests the guard rather than the schema behind it.

        Concretely: a definite-zero left this record `failed_unsent`
        (proved: no message exists). A late or duplicated outcome
        claiming `applied` must NOT bind it to a message id, or the
        record would assert a binding for an object that was never
        created.
        """
        harness = self.harness_with([
            http_error(400, api_refusal("Bad Request: chat not found")),
        ])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        before = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            before["state"], wa_record.PLACEHOLDER_FAILED_UNSENT
        )
        self.assertIsNotNone(before["text_digest"])
        self.assertIsNotNone(before["sent_at"])

        harness.adapter._record_placeholder_outcome(
            "wf-0001", before["text_digest"],
            telegram_api.SendOnceOutcome(
                classification=telegram_api.SEND_APPLIED, message_id=77
            ),
        )
        after = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            after["state"], wa_record.PLACEHOLDER_FAILED_UNSENT,
            "only the intent THIS pass wrote may be resolved; a"
            " failed_unsent record must not be bound by a late outcome",
        )
        self.assertIsNone(after["message_id"])
        self.assertEqual(after, before)
        # Anti-vacuity: from `sending`, the SAME outcome DOES bind —
        # so the refusal above is the guard, not a blanket no-op.
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            workflows["workflows"]["wf-0001"]["result_placeholder"][
                "state"
            ] = wa_record.PLACEHOLDER_SENDING
            harness.workflow_store.save(workflows)
        harness.adapter._record_placeholder_outcome(
            "wf-0001", before["text_digest"],
            telegram_api.SendOnceOutcome(
                classification=telegram_api.SEND_APPLIED, message_id=77
            ),
        )
        resolved = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            resolved["state"], wa_record.PLACEHOLDER_BOUND
        )
        self.assertEqual(resolved["message_id"], 77)

    def test_outcome_is_ignored_when_the_digest_does_not_match(self):
        """Y-35: the digest identifies WHICH send this outcome belongs
        to. A mismatched digest means the record was re-armed for a
        different text; resolving it would bind the wrong object."""
        harness = self.harness_with([socket.timeout("d")])
        self.approve(harness)
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            placeholder = (
                workflows["workflows"]["wf-0001"]["result_placeholder"]
            )
            placeholder["state"] = wa_record.PLACEHOLDER_SENDING
            placeholder["sent_at"] = NOW
            placeholder["text_digest"] = wa_digest.text_digest("a text")
            harness.workflow_store.save(workflows)
        before = self.on_disk(harness)["result_placeholder"]

        harness.adapter._record_placeholder_outcome(
            "wf-0001", wa_digest.text_digest("a DIFFERENT text"),
            telegram_api.SendOnceOutcome(
                classification=telegram_api.SEND_APPLIED, message_id=9
            ),
        )
        self.assertEqual(
            self.on_disk(harness)["result_placeholder"], before,
            "an outcome for a different text must not bind this"
            " placeholder",
        )
        # Anti-vacuity: the MATCHING digest does resolve it.
        harness.adapter._record_placeholder_outcome(
            "wf-0001", before["text_digest"],
            telegram_api.SendOnceOutcome(
                classification=telegram_api.SEND_APPLIED, message_id=9
            ),
        )
        after = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(after["state"], wa_record.PLACEHOLDER_BOUND)
        self.assertEqual(after["message_id"], 9)

    def test_applied_without_a_usable_message_id_fails_closed(self):
        """Y-36/Y-37, belt coverage: if a transport ever reported
        `applied` without a usable int message id — violating its own
        contract — the adapter must NOT record a bound placeholder with
        a missing or bool id. `True` must never masquerade as message
        id 1."""
        for message_id in (None, True, False, 0, -1, "9", 1.0):
            harness = self.harness_with([socket.timeout("d")])
            self.approve(harness)
            with wa_store.exclusive_store_lock(harness.tmpdir):
                workflows = harness.workflow_store.load()
                placeholder = (
                    workflows["workflows"]["wf-0001"]
                    ["result_placeholder"]
                )
                placeholder["state"] = wa_record.PLACEHOLDER_SENDING
                placeholder["sent_at"] = NOW
                placeholder["text_digest"] = wa_digest.text_digest("t")
                harness.workflow_store.save(workflows)
            harness.adapter._record_placeholder_outcome(
                "wf-0001", wa_digest.text_digest("t"),
                telegram_api.SendOnceOutcome(
                    classification=telegram_api.SEND_APPLIED,
                    message_id=message_id,
                ),
            )
            placeholder = self.on_disk(harness)["result_placeholder"]
            self.assertEqual(
                placeholder["state"],
                wa_record.PLACEHOLDER_INDEFINITE, repr(message_id),
            )
            self.assertIsNone(
                placeholder["message_id"], repr(message_id)
            )


class RunLoopWiringTests(LifecycleCase):

    def test_run_loop_drives_the_placeholder_step_each_iteration(self):
        """Y-43: `ensure_result_placeholders` is actually wired into
        `run()`. Behavioural, not a source scan: the real loop is run
        for exactly one iteration and the calls are recorded."""
        harness = self.harness_with([api_ok({"message_id": 1})])
        calls = []
        original = harness.adapter.ensure_result_placeholders
        delivered = []

        def record_placeholders():
            calls.append("placeholders")
            return original()

        def record_delivery():
            delivered.append("delivery")

        def one_poll():
            harness.adapter._stopping = True
            return True

        harness.adapter.ensure_result_placeholders = record_placeholders
        harness.adapter.deliver_pending_results = record_delivery
        harness.adapter.poll_once = one_poll
        harness.adapter.startup_recovery = lambda: None
        harness.adapter.run()

        self.assertEqual(
            calls, ["placeholders"],
            "run() must drive the placeholder lifecycle each iteration",
        )
        # Anti-vacuity: the loop really ran one iteration.
        self.assertEqual(delivered, ["delivery"])


class PlaceholderStatusTests(LifecycleCase):

    def render(self, state, **overrides):
        from telegram_operator import adapter as adapter_module
        if state is None:
            return adapter_module._render_placeholder_status(None)
        placeholder = {
            "state": state, "chat_id": 1001, "message_id": None,
            "requested_at": 10, "sent_at": None, "bound_at": None,
            "text_digest": None,
        }
        placeholder.update(overrides)
        return adapter_module._render_placeholder_status(placeholder)

    def test_every_placeholder_state_renders_distinctly_and_truthfully(
        self
    ):
        """/status: every state gets its own words; no state renders as
        'pending' or 'delivered' unless it is."""
        rendered = {}
        for state in wa_record.PLACEHOLDER_STATES:
            rendered[state] = self.render(
                state, message_id=(
                    77 if state in ("bound", "unbindable") else None
                )
            )
        # DISTINCT: no two states share a rendering.
        self.assertEqual(
            len(set(rendered.values())), len(rendered), rendered
        )
        # None of them is silently reported as bound or delivered.
        for state, text in rendered.items():
            if state != wa_record.PLACEHOLDER_BOUND:
                self.assertNotIn("bound to message", text, state)
            self.assertNotIn("delivered", text.lower(), state)
            self.assertNotIn("unrecognized", text, state)
        # The TERMINAL states say so, and say a human must act.
        for state in (wa_record.PLACEHOLDER_INDEFINITE,
                      wa_record.PLACEHOLDER_UNBINDABLE):
            self.assertIn("TERMINAL", rendered[state], state)
        self.assertIn(
            "never retried automatically",
            rendered[wa_record.PLACEHOLDER_INDEFINITE],
        )
        self.assertIn(
            "Human recovery required",
            rendered[wa_record.PLACEHOLDER_INDEFINITE],
        )
        # No placeholder state may render as "pending": none of the
        # six IS a pending delivery, and reporting one that way is the
        # dishonest-status defect this branch exists to prevent.
        for state, text in rendered.items():
            self.assertNotIn("pending", text.lower(), state)
        # The legacy lane is disclosed as AT-MOST-ONCE and ungated.
        # Wrapped: removing the `placeholder is None` branch makes this
        # raise, and a crash is not a kill (R-8.3).
        try:
            legacy = self.render(None)
        except Exception as exc:
            self.fail(
                "a null placeholder must render the legacy-lane line,"
                " not raise %s: %s" % (type(exc).__name__, exc)
            )
        self.assertNotIn("pending", legacy.lower())
        self.assertIn("legacy", legacy.lower())
        self.assertIn("AT-MOST-ONCE", legacy)
        self.assertIn("ungated", legacy)

    def test_unknown_placeholder_state_reaches_the_fail_loud_fallback(
        self
    ):
        """The unknown/unmapped fallback stays REACHABLE: a state
        added without a branch must fail loud, never be reported as
        bound or pending."""
        text = self.render("teleported")
        self.assertIn("unrecognized", text)
        self.assertIn("teleported", text)

    def test_status_reports_the_placeholder_state_to_the_human(self):
        """The branch is wired into the real /status output, not just
        available as a function."""
        harness = self.harness_with([socket.timeout("deadline")])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        harness.adapter.process_update(msg_update(30, "/status"))
        harness.drain_worker()
        status = [
            send["text"] for send in harness.sends()
            if "Adapter state" in send["text"]
        ][-1]
        self.assertIn("wf-0001", status)
        self.assertIn("INDEFINITE", status)
        self.assertIn("Human recovery required", status)


class AuthorityBoundaryTests(LifecycleCase):

    def test_A3_adapter_gains_no_runtime_broker_herdr_shell_or_git(self):
        """T-A3: the new code paths keep the adapter STORE-ONLY.

        Behavioural, not a source scan: `subprocess.run`/`Popen`,
        `os.system` and `os.popen` are replaced with recorders that
        FAIL the test if called, and the Runtime/Broker/Herdr entry
        points are replaced with recorders too. Then the full new
        surface is driven — approval (Layer 1), the placeholder loop
        step, the startup reconciliation, delivery and /status.
        """
        import subprocess
        import os as os_module
        from unittest.mock import patch
        from telegram_operator import adapter as adapter_module

        executed = []

        def forbidden(*args, **kwargs):
            executed.append(args[0] if args else kwargs)
            raise AssertionError(
                "the adapter executed a subprocess: %r" % (args,)
            )

        harness = self.harness_with([
            http_error(400, api_refusal("Bad Request: chat not found")),
            api_ok({"message_id": 8}),
        ])
        with patch.object(subprocess, "run", forbidden), \
                patch.object(subprocess, "Popen", forbidden), \
                patch.object(os_module, "system", forbidden), \
                patch.object(os_module, "popen", forbidden):
            self.approve(harness)
            harness.adapter.startup_recovery()
            harness.adapter.ensure_result_placeholders()
            harness.adapter.ensure_result_placeholders()
            harness.adapter.deliver_pending_results()
            harness.adapter.process_update(msg_update(31, "/status"))
            harness.drain_worker()
        self.assertEqual(executed, [])
        # The placeholder path really ran (anti-vacuity): without this
        # the assertions above would hold over a no-op.
        self.assertEqual(
            self.on_disk(harness)["result_placeholder"]["state"],
            wa_record.PLACEHOLDER_BOUND,
        )

    def test_A3_new_paths_import_no_runtime_broker_or_herdr(self):
        """T-A3 (b): the adapter module does not reach the Runtime,
        the Broker or Herdr at all — asserted on the module's own
        imported namespace and on its source's import statements,
        which together cover both `import x` and `from x import y`."""
        import ast
        import io as io_module
        from telegram_operator import adapter as adapter_module

        forbidden_roots = (
            "target_runtime", "herdr", "herdctl", "mission_control",
        )
        for name, value in vars(adapter_module).items():
            module_name = getattr(value, "__name__", "")
            for root in forbidden_roots:
                self.assertFalse(
                    module_name == root
                    or module_name.startswith(root + "."),
                    "adapter binds %r -> %r" % (name, module_name),
                )
        source_path = adapter_module.__file__
        with io_module.open(source_path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                # BOTH forms: the package itself and every dotted name
                # it pulls out, so `from x import y` cannot hide a
                # forbidden `x.y` behind a permitted `x`.
                imported.add(node.module)
                imported.update(
                    "%s.%s" % (node.module, alias.name)
                    for alias in node.names
                )
        # Anti-vacuity: the scan really found this module's imports, in
        # both syntactic forms.
        self.assertIn("workflow_authority.store", imported)
        self.assertIn("telegram_operator.telegram_api", imported)
        self.assertIn("threading", imported)
        for module_name in imported:
            for root in forbidden_roots:
                self.assertFalse(
                    module_name == root
                    or module_name.startswith(root + "."),
                    "adapter imports %r" % module_name,
                )

    def test_delivery_authority_stays_none_through_the_lifecycle(self):
        """No placeholder field is an authority field, in any state."""
        harness = self.harness_with([
            http_error(400, api_refusal("Bad Request: chat not found")),
            api_ok({"message_id": 3}),
        ])
        self.approve(harness)
        for _ in range(3):
            harness.adapter.ensure_result_placeholders()
            entry = self.on_disk(harness)
            self.assertEqual(
                entry["delivery_authority"],
                wa_record.DELIVERY_AUTHORITY_NONE,
                entry["result_placeholder"]["state"],
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
