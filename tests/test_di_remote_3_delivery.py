"""DI-REMOTE-3 increment I5: edit-based final delivery, the render
guard, and /status.

Scope: the adapter's delivery path and `/status`. The schema (I1), the
transport (I2), the placeholder lifecycle (I3) and the Broker gate
(I4) are accepted and unchanged.

R-9.2: every non-2xx transport fixture RAISES `HTTPError`, matching
`urlopen`. Every edit in this file runs through a REAL
`telegram_api.TelegramApi`, so the adapter is driven against the real
I2 classifier and the real description matcher rather than a hand-made
outcome.

Test ids map to plan §5: T-D1..T-D4, T-R1, T-R2, T-N1..T-N4 (at the
adapter level), T-U1, T-U2, T-V1, T-V2, T-G1..T-G4, T-X1, T-X5, T-X6.
"""

import json
import os
import unittest

from telegram_operator import adapter as adapter_module
from telegram_operator import telegram_api
from workflow_authority import digest as wa_digest
from workflow_authority import record as wa_record
from workflow_authority import store as wa_store

from test_mission import NOW, cb_update, msg_update
from test_di_remote_3_transport import (
    ScriptedTransport, api_ok, api_refusal, http_error,
)
from test_di_remote_3_lifecycle import LifecycleCase, PlaceholderApi

NOT_MODIFIED = "Bad Request: message is not modified"
NOT_FOUND = "Bad Request: message to edit not found"
PLACEHOLDER_ID = 4242


class EditApi(PlaceholderApi):
    """The lifecycle harness's api, plus a REAL `edit_message_text`.

    Both the placeholder send and the result edit run through one real
    `TelegramApi` over one scripted transport, so the recorded call log
    is the single source of truth for what actually left the process —
    which is what T-U1 asserts on.
    """

    def __init__(self, inner, script, edit_script=None):
        PlaceholderApi.__init__(self, inner, script)
        self.edit_calls = []
        self._edit_script = list(edit_script or [])
        self.chunking_sends = []

    def edit_message_text(self, chat_id, message_id, text):
        self.edit_calls.append({
            "chat_id": chat_id, "message_id": message_id, "text": text,
        })
        if self._edit_script:
            self.transport.script = [self._edit_script.pop(0)]
        return self._api.edit_message_text(chat_id, message_id, text)

    def send_message(self, chat_id, text, reply_markup=None):
        # Recorded so the result path can be proven never to touch the
        # CHUNKING sender (T-G3), on the call log rather than by
        # reading source.
        # Only RESULT-path traffic is the subject here; the adapter's
        # ordinary acknowledgements also go through send_message.
        if adapter_module.RESULT_MESSAGE_HEADER in (text or ""):
            self.chunking_sends.append(
                {"chat_id": chat_id, "text": text}
            )
        # PlaceholderApi delegates by __getattr__ and defines no
        # send_message of its own, so go to the inner fake directly.
        return self._inner.send_message(
            chat_id, text, reply_markup=reply_markup
        )


class DeliveryCase(LifecycleCase):

    def harness_with_edits(self, edit_script, send_script=None):
        harness = self.harness(None)
        harness.api = EditApi(
            harness.api,
            send_script or [api_ok({"message_id": PLACEHOLDER_ID})],
            edit_script=edit_script,
        )
        harness.adapter.api = harness.api
        return harness

    def bound_and_completed(self, harness, summary="mission verified"):
        """Approve, bind the placeholder through the ordinary adapter
        loop step, then record a verified result as the Runtime would."""
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        self.assertEqual(
            self.on_disk(harness)["result_placeholder"]["state"],
            wa_record.PLACEHOLDER_BOUND,
        )
        self.record_result(harness, summary)

    def record_result(self, harness, summary):
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            entry = workflows["workflows"]["wf-0001"]
            if entry["phase"] != wa_record.PHASE_COMPLETED:
                for phase in (wa_record.PHASE_WORKSPACE_READY,
                              wa_record.PHASE_PREPARED,
                              wa_record.PHASE_VALIDATED,
                              wa_record.PHASE_DISPATCHED,
                              wa_record.PHASE_VERIFIED,
                              wa_record.PHASE_COMPLETED):
                    wa_record.apply_transition(entry, phase)
            entry["verified_result"] = {
                "summary": summary,
                "digest": wa_digest.text_digest(summary),
                "recorded_at": NOW,
            }
            harness.workflow_store.save(workflows)

    def delivery(self, harness):
        return self.on_disk(harness)["result_delivery"]

    def visible_objects(self, harness):
        """Every Telegram object the RESULT path could have created or
        changed: fresh sends carrying the result header, plus edits."""
        # ONE authoritative source: the harness timeline records every
        # sendMessage that actually reached the fake, whichever entry
        # point produced it. `chunking_sends` is a SEPARATE probe of
        # "did the result path call the chunking sender at all", and
        # counting both would double-count the same message.
        header = adapter_module.RESULT_MESSAGE_HEADER
        sends = [
            entry[1] for entry in harness.timeline
            if entry[0] == "sendMessage"
            and header in (entry[1].get("text") or "")
        ]
        return sends, list(harness.api.edit_calls)

    def transport_edits(self, harness, since=0):
        """Every editMessageText payload the REAL transport sent, read
        from the raw transport call log. The result EDIT seam
        (`edit_message_text`) runs through a genuine `TelegramApi` over
        the `ScriptedTransport`, so this call log is the authoritative
        record of every edit that actually left the process. Matched on
        the request URL (`.../bot<token>/editMessageText`).
        """
        return [
            call["payload"]
            for call in harness.api.transport.calls[since:]
            if call["url"].endswith("/editMessageText")
        ]

    def result_sends(self, harness):
        """Every legacy result `sendMessage` that actually reached the
        fake, read from the harness TIMELINE — the authoritative call
        log for sends in this harness.

        The result-delivery `send_message` seam is served by the
        harness's own `MissionApi`/`TimelineApi` fake (only the
        placeholder-send and edit seams are wired through the
        `ScriptedTransport`), so `harness.timeline` — not
        `transport.calls` — is where a legacy send is recorded. Each
        entry is `("sendMessage", {"chat_id", "text", "reply_markup"})`.
        Filtering on `RESULT_MESSAGE_HEADER` isolates the legacy
        delivery send from ordinary acknowledgements; the header appears
        on NO message before delivery, so the count is the exact number
        of legacy result sends the run performed.
        """
        header = adapter_module.RESULT_MESSAGE_HEADER
        return [
            entry[1] for entry in harness.timeline
            if entry[0] == "sendMessage"
            and header in (entry[1].get("text") or "")
        ]

    def arm_legacy(self, harness):
        """Run the real approval / mission-arming transaction the
        PRE-I3 (DI-REMOTE-2) way: everything except the I3 placeholder
        request. The record is armed and saved with `result_placeholder`
        never written — genuinely pre-placeholder, not nulled on disk.
        Leaves the record at its post-approval phase; callers advance it.
        """
        real_request = harness.adapter._request_result_placeholder

        def _di_remote_2_arming(workflows, workflow_id, now):
            # The pre-I3 arming transaction: no placeholder request.
            return False

        harness.adapter._request_result_placeholder = _di_remote_2_arming
        try:
            self.approve(harness)
        finally:
            harness.adapter._request_result_placeholder = real_request
        # The arming really left NO placeholder: this is what makes the
        # record genuinely pre-placeholder rather than a nulled bound
        # record. Read through the real store, not the raw bytes.
        self.assertIsNone(
            self.on_disk(harness)["result_placeholder"],
            "arm_legacy must yield a record that NEVER entered the"
            " placeholder architecture",
        )

    def legacy_completed(self, harness, summary="the legacy outcome"):
        """Arm and complete a workflow the PRE-PLACEHOLDER
        (DI-REMOTE-2) way, so `result_placeholder` is GENUINELY None —
        never stamped, never bound, never patched on disk.

        A brand-new record starts with `result_placeholder is None`
        (record.new_record). I3 later added `_request_result_placeholder`
        INTO the same locked transaction that arms the mission, so
        every go-forward record now carries a `required` placeholder the
        instant it is approved. A record that predates I3 was armed by
        that identical transaction MINUS this one write, and so reached
        COMPLETED with the placeholder field never written.

        We reproduce exactly that writer: the real approval /
        mission-consumption / phase-transition path runs, but the I3
        placeholder request is suppressed for this one arming — so the
        record is saved, validated and reloaded through the real store
        with `result_placeholder` never having existed. That is the
        material difference from a bound record nulled on disk: no
        placeholder dict was ever written, no chat_id/requested_at was
        ever stamped and then discarded, and no out-of-band JSON rewrite
        or re-chmod took place. The record IS a legacy record, not a
        forgery of one.
        """
        self.arm_legacy(harness)
        self.record_result(harness, summary)
        entry = self.on_disk(harness)
        self.assertEqual(entry["phase"], wa_record.PHASE_COMPLETED)
        self.assertIsNotNone(entry["verified_result"])
        self.assertIsNone(entry["result_delivery"])
        self.assertIsNone(entry["result_placeholder"])
        return harness


class ExactlyOnceTests(DeliveryCase):

    def test_D1_result_edit_is_visible_exactly_once(self):
        """T-D1: the result is delivered by EDITING the bound
        placeholder — exactly one visible object, and ZERO sendMessage
        on the result path."""
        harness = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.bound_and_completed(harness, "external target issue resolved")
        harness.adapter.deliver_result_edits()

        sends, edits = self.visible_objects(harness)
        self.assertEqual(
            sends, [],
            "the result path must NEVER create a fresh message; it"
            " edits the bound placeholder",
        )
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["message_id"], PLACEHOLDER_ID)
        self.assertIn("external target issue resolved", edits[0]["text"])
        marker = self.delivery(harness)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT
        )
        self.assertEqual(marker["edited_message_id"], PLACEHOLDER_ID)
        # The LEGACY key keeps its meaning and is untouched.
        self.assertIsNone(marker["telegram_message_id"])

    def test_D3_repeated_passes_send_and_edit_nothing_new(self):
        """T-D3: N further passes over a delivered record produce no
        further Telegram traffic at all."""
        harness = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.bound_and_completed(harness)
        harness.adapter.deliver_result_edits()
        sends, edits = self.visible_objects(harness)
        self.assertEqual((len(sends), len(edits)), (0, 1))
        for _ in range(5):
            harness.adapter.deliver_result_edits()
            harness.adapter.deliver_pending_results()
        sends, edits = self.visible_objects(harness)
        self.assertEqual(
            (len(sends), len(edits)), (0, 1),
            "a delivered result must never be re-sent or re-edited",
        )

    def test_D2_replayed_delivery_after_crash_edits_the_same_object(
        self
    ):
        """T-D2: a crash in `edit_pending` is RESUMABLE — the next pass
        edits the SAME (chat_id, message_id) and still leaves exactly
        one visible object, because the edit is idempotent."""
        harness = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.bound_and_completed(harness)
        # Crash: the intent landed, the outcome did not.
        original = harness.workflow_store.save
        saves = {"n": 0}

        def fail_the_outcome_save(document):
            saves["n"] += 1
            if saves["n"] == 2:
                raise wa_store.StoreError("transient store failure")
            return original(document)

        harness.workflow_store.save = fail_the_outcome_save
        try:
            harness.adapter.deliver_result_edits()
        finally:
            harness.workflow_store.save = original
        stuck = self.delivery(harness)
        self.assertEqual(
            stuck["state"], wa_record.DELIVERY_EDIT_PENDING,
            "the fixture must really leave an unresolved edit intent",
        )

        # F3: the invariant is "crash AFTER Telegram applied the final
        # edit, before durable success". Prove the PRE-CRASH edit
        # PHYSICALLY reached the transport (chat_id + message_id +
        # text), not merely that the marker is edit_pending — a
        # synthetic success that issued NO transport call would
        # otherwise satisfy this test while violating its premise.
        entry = self.on_disk(harness)
        expected_text = adapter_module.render_result_text(entry)
        bound_chat = entry["result_placeholder"]["chat_id"]
        pre_crash = self.transport_edits(harness)
        self.assertEqual(
            len(pre_crash), 1,
            "the pre-crash edit must have physically reached Telegram;"
            " the transport editMessageText log was %r" % (pre_crash,),
        )
        self.assertEqual(pre_crash[0]["chat_id"], bound_chat)
        self.assertEqual(pre_crash[0]["message_id"], PLACEHOLDER_ID)
        self.assertEqual(pre_crash[0]["text"], expected_text)

        # RESTART: a fresh adapter over the SAME store resumes.
        restarted = self.harness_with_edits(
            [api_ok({"message_id": PLACEHOLDER_ID})]
        )
        self.assertEqual(restarted.tmpdir, harness.tmpdir)
        restarted.adapter.deliver_result_edits()
        marker = self.delivery(restarted)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT
        )
        self.assertEqual(marker["edited_message_id"], PLACEHOLDER_ID)
        sends, edits = self.visible_objects(restarted)
        self.assertEqual(sends, [])
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["message_id"], PLACEHOLDER_ID)
        # The restart edits the IDENTICAL binding on the transport — the
        # SAME object, never a new one.
        post_crash = self.transport_edits(restarted)
        self.assertEqual(len(post_crash), 1)
        self.assertEqual(post_crash[0]["chat_id"], bound_chat)
        self.assertEqual(post_crash[0]["message_id"], PLACEHOLDER_ID)
        self.assertEqual(post_crash[0]["text"], expected_text)

    def test_D4_revised_result_reedits_the_same_bound_object(self):
        """T-D4 (R-4): a genuinely REVISED verified result re-edits the
        SAME bound object. Zero new messages, still one visible
        object."""
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID}),
            api_ok({"message_id": PLACEHOLDER_ID}),
        ])
        self.bound_and_completed(harness, "first finding")
        harness.adapter.deliver_result_edits()
        first = self.delivery(harness)
        self.assertEqual(
            first["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT
        )

        self.record_result(harness, "revised finding after review")
        harness.adapter.deliver_result_edits()
        second = self.delivery(harness)
        self.assertEqual(
            second["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT
        )
        self.assertNotEqual(
            second["verified_result_digest"],
            first["verified_result_digest"],
        )
        self.assertNotEqual(
            second["rendered_digest"], first["rendered_digest"]
        )
        sends, edits = self.visible_objects(harness)
        self.assertEqual(sends, [], "a revision must create NO message")
        self.assertEqual(len(edits), 2)
        self.assertEqual(
            {call["message_id"] for call in edits}, {PLACEHOLDER_ID}
        )
        self.assertIn("revised finding after review", edits[1]["text"])


class RenderedDigestProofTests(DeliveryCase):
    """G1 read-as-proof half: the rendered_digest relation the record
    validator cannot check (the result renderer lives in the adapter,
    above the store-only boundary) is enforced where the receipt is read
    as proof — `_edit_delivery_claimable`."""

    def test_a_stale_rendered_digest_receipt_is_re_delivered(self):
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID}),
            api_ok({"message_id": PLACEHOLDER_ID}),
        ])
        self.bound_and_completed(harness, "verified outcome")
        harness.adapter.deliver_result_edits()
        correct = self.delivery(harness)["rendered_digest"]
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DELIVERED_BY_EDIT,
        )
        # Corrupt ONLY the stored rendered_digest to another valid hex.
        # The record still VALIDATES — the boundary does NOT check this
        # relation — so nothing but the read-as-proof guard can catch it.
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            workflows["workflows"]["wf-0001"]["result_delivery"][
                "rendered_digest"] = "0" * 64
            harness.workflow_store.save(workflows)
        self.assertNotEqual(
            self.delivery(harness)["rendered_digest"], correct)
        before = len(self.transport_edits(harness))

        # The read-as-proof guard treats the mismatched receipt as
        # unproven and re-delivers, healing the digest.
        harness.adapter.deliver_result_edits()
        after = self.delivery(harness)
        self.assertEqual(
            after["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT)
        self.assertEqual(
            after["rendered_digest"], correct,
            "a receipt whose rendered_digest is not the record's true"
            " render must be re-delivered, not trusted as proof",
        )
        self.assertGreater(
            len(self.transport_edits(harness)), before,
            "a re-edit must have been issued against the bound object",
        )

    def test_a_true_rendered_digest_receipt_is_never_re_delivered(self):
        # ANTI-VACUITY: an honest receipt is NOT re-delivered, so the
        # guard above is not simply re-editing everything.
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID}),
            api_ok({"message_id": PLACEHOLDER_ID}),
        ])
        self.bound_and_completed(harness, "verified outcome")
        harness.adapter.deliver_result_edits()
        before = len(self.transport_edits(harness))
        for _ in range(3):
            harness.adapter.deliver_result_edits()
        self.assertEqual(
            len(self.transport_edits(harness)), before,
            "an honest delivered_by_edit receipt must never be re-edited",
        )


class UnrenderableProofTests(DeliveryCase):
    """Round-04 read-as-proof: degraded_unrenderable is trusted as
    TERMINAL only when its premise still holds — the receipt's
    rendered_digest matches the current render AND that render genuinely
    exceeds one Telegram message. A false terminal (a fitting result
    marked unrenderable) is reclaimed and healed."""

    def test_a_short_render_wrongly_marked_unrenderable_is_healed(self):
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID}),
            api_ok({"message_id": PLACEHOLDER_ID}),
        ])
        self.bound_and_completed(harness, "a short verified outcome")
        entry = self.on_disk(harness)
        text = adapter_module.render_result_text(entry)
        self.assertLessEqual(
            len(text), telegram_api.MAX_MESSAGE_CHARS,
            "the fixture render must genuinely FIT one message, so the"
            " degraded_unrenderable receipt below is provably FALSE")
        # Forge a degraded_unrenderable receipt whose rendered_digest
        # MATCHES the current (fitting) render — the exact false terminal
        # the Lead's probe found. It validates (shape + H1 prerequisites
        # hold), so only the read-as-proof premise check can catch it.
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            workflows["workflows"]["wf-0001"]["result_delivery"] = {
                "state": wa_record.DELIVERY_DEGRADED_UNRENDERABLE,
                "reserved_at": 1, "telegram_message_id": None,
                "verified_result_digest":
                    entry["verified_result"]["digest"],
                "rendered_digest": wa_digest.text_digest(text),
                "edited_message_id": None, "attempted_at": 5,
                "settled_at": 6,
                "problem": "the rendered result is too long (forged)",
            }
            harness.workflow_store.save(workflows)
        self.assertTrue(
            harness.adapter._edit_delivery_claimable(self.on_disk(harness)),
            "a fitting result falsely marked unrenderable must be"
            " CLAIMABLE, not permanently suppressed")
        before = len(self.transport_edits(harness))
        harness.adapter.deliver_result_edits()
        marker = self.delivery(harness)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT,
            "the false terminal must be reclaimed and DELIVERED")
        self.assertGreater(
            len(self.transport_edits(harness)), before,
            "a heal edit must have been issued against the bound object")

    def test_a_stale_digest_on_a_still_oversized_result_is_healed(self):
        # Pins the DIGEST conjunct of the two-part premise independently
        # of the SIZE conjunct: a genuinely OVERSIZED render whose stored
        # rendered_digest is STALE (does not match the current render).
        # The size conjunct holds, so a guard that checked size ALONE
        # would leave this terminal; the digest conjunct must fail, so it
        # is reclaimed and rewritten with the CURRENT digest — but NEVER
        # delivered, chunked, or sent, because it is still oversized. A
        # mutant deleting the digest-equality conjunct is killed here.
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        self.record_result(
            harness, "y" * wa_record.MAX_VERIFIED_SUMMARY_CHARS)
        entry = self.on_disk(harness)
        text = adapter_module.render_result_text(entry)
        self.assertGreater(
            len(text), telegram_api.MAX_MESSAGE_CHARS,
            "the fixture must be genuinely oversized (size conjunct"
            " holds), so ONLY the digest conjunct can fail")
        current = wa_digest.text_digest(text)
        stale = wa_digest.text_digest("a stale rendering, not current")
        self.assertNotEqual(stale, current)
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            workflows["workflows"]["wf-0001"]["result_delivery"] = {
                "state": wa_record.DELIVERY_DEGRADED_UNRENDERABLE,
                "reserved_at": 1, "telegram_message_id": None,
                "verified_result_digest":
                    entry["verified_result"]["digest"],
                "rendered_digest": stale, "edited_message_id": None,
                "attempted_at": 5, "settled_at": 6,
                "problem": "the rendered result is too long (stale)",
            }
            harness.workflow_store.save(workflows)
        self.assertTrue(
            harness.adapter._edit_delivery_claimable(self.on_disk(harness)),
            "a degraded_unrenderable whose rendered_digest is STALE must"
            " NOT be trusted as terminal — the digest conjunct fails")
        harness.adapter.deliver_result_edits()
        marker = self.delivery(harness)
        # Still oversized -> stays terminal, but the stale digest is
        # healed to the CURRENT render digest, with NO delivery.
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DEGRADED_UNRENDERABLE,
            "a still-oversized result stays terminal")
        self.assertEqual(
            marker["rendered_digest"], current,
            "the STALE digest must be healed to the CURRENT render digest")
        self.assertNotEqual(marker["rendered_digest"], stale)
        self.assertEqual(harness.api.edit_calls, [], "NO edit")
        self.assertEqual(harness.api.chunking_sends, [], "NO chunk")
        sends, _ = self.visible_objects(harness)
        self.assertEqual(sends, [], "NO send: it is still oversized")

    def test_a_genuinely_oversized_result_stays_terminal_never_chunks(
        self
    ):
        # ANTI-VACUITY: T-G2 must not regress. A real oversize result
        # stays degraded_unrenderable across passes and is NEVER edited,
        # chunked, or truncated.
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        self.record_result(
            harness, "y" * wa_record.MAX_VERIFIED_SUMMARY_CHARS)
        entry = self.on_disk(harness)
        self.assertGreater(
            len(adapter_module.render_result_text(entry)),
            telegram_api.MAX_MESSAGE_CHARS,
            "the fixture must really be over the cap")
        harness.adapter.deliver_result_edits()
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DEGRADED_UNRENDERABLE)
        self.assertFalse(
            harness.adapter._edit_delivery_claimable(self.on_disk(harness)),
            "a genuinely oversized result must NOT be claimable")
        for _ in range(3):
            harness.adapter.deliver_result_edits()
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DEGRADED_UNRENDERABLE,
            "a genuinely oversized result stays terminal")
        self.assertEqual(harness.api.edit_calls, [], "never edited")
        self.assertEqual(harness.api.chunking_sends, [], "never chunked")


class DeterminismTests(DeliveryCase):

    def test_R1_rendered_result_is_byte_identical_across_clocks(self):
        """T-R1: the rendered text is a PURE function of the record —
        rendering under distinct clocks and after distinct attempt
        counts yields byte-identical output."""
        harness = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.bound_and_completed(harness, "a verified outcome")
        entry = self.on_disk(harness)
        first = adapter_module.render_result_text(entry)
        for clock in (0, NOW, NOW + 999999):
            harness.clock[0] = clock
            for _ in range(3):
                self.assertEqual(
                    adapter_module.render_result_text(entry), first
                )
        # No clock value appears in the text, under any clock.
        for clock in (0, NOW, NOW + 999999):
            self.assertNotIn(str(clock), first)
        for forbidden in ("as of", "attempt", "retry", "t="):
            self.assertNotIn(forbidden, first.lower(), forbidden)
        # It is a function of exactly the three declared inputs.
        self.assertIn(adapter_module.RESULT_MESSAGE_HEADER, first)
        self.assertIn(entry["target"]["canonical_url"], first)
        self.assertIn(entry["verified_result"]["summary"], first)
        # The EXACT bytes, authored here independently of the module's
        # own format string. Both lanes share this renderer, so a drift
        # would also change what LEGACY records receive — comparing the
        # renderer to itself could never catch that.
        self.assertEqual(
            first,
            "%s\n%s\n\nVERIFIED RESULT:\n"
            "a verified outcome\n\nDelivery is separately human-gated;"
            " this message grants no commit, push, PR, tag, release, or"
            " deploy authority." % (
                adapter_module.RESULT_MESSAGE_HEADER,
                adapter_module._render_target_line(entry),
            ),
        )

    def test_R2_edit_payload_has_no_parse_mode_or_reply_markup(self):
        """T-R2: the EXACT payload dict is asserted, not a subset. R-1
        holds BY CONSTRUCTION: the transport omits both fields
        entirely, so replays are byte-identical without anyone
        remembering to keep them so."""
        harness = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.bound_and_completed(harness, "a verified outcome")
        harness.adapter.deliver_result_edits()
        payloads = [
            call["payload"] for call in harness.api.transport.calls
            if "message_id" in call["payload"]
            and adapter_module.RESULT_MESSAGE_HEADER
            in (call["payload"].get("text") or "")
        ]
        self.assertEqual(len(payloads), 1)
        entry = self.on_disk(harness)
        self.assertEqual(
            payloads[0],
            {
                "chat_id": entry["telegram"]["chat_id"],
                "message_id": PLACEHOLDER_ID,
                "text": adapter_module.render_result_text(entry),
            },
        )


class NotModifiedProofTests(DeliveryCase):

    def test_N1_not_modified_on_the_bound_object_is_success(self):
        """T-N1: message-not-modified, on the bound object, with the
        current digest, from a genuine structured ok=false body, IS
        success — the object provably holds the intended text."""
        harness = self.harness_with_edits([
            http_error(400, api_refusal(NOT_MODIFIED))
        ])
        self.bound_and_completed(harness)
        harness.adapter.deliver_result_edits()
        marker = self.delivery(harness)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT
        )
        self.assertEqual(marker["edited_message_id"], PLACEHOLDER_ID)
        sends, _ = self.visible_objects(harness)
        self.assertEqual(sends, [])

    def test_N4_other_ok_false_descriptions_are_never_success(self):
        """T-N4: a table over real Telegram descriptions, near-misses
        and a SUPERSTRING of the real phrase. None may be recorded as
        delivered, and none may create a message."""
        rejected = (
            "Bad Request: message is not modified by another party",
            "Bad Request: message is not modified yet",
            "Bad Request: message was not modified",
            "Bad Request: chat not found",
            "Bad Request: message can't be edited",
            "Forbidden: bot was blocked by the user",
            "Too Many Requests: retry after 5",
        )
        for description in rejected:
            harness = self.harness_with_edits([
                http_error(400, api_refusal(description))
            ])
            self.bound_and_completed(harness)
            harness.adapter.deliver_result_edits()
            marker = self.delivery(harness)
            self.assertNotEqual(
                marker["state"],
                wa_record.DELIVERY_DELIVERED_BY_EDIT, description,
            )
            sends, _ = self.visible_objects(harness)
            self.assertEqual(sends, [], description)
            self.setUp()

    def test_N2_N3_not_modified_without_the_proof_is_not_success(self):
        """T-N2/T-N3: the phrase alone is never enough.

        Clause 4 (a genuine structured ok=false body) is driven here: a
        PROXY response carrying the exact phrase in a non-Telegram body
        must NOT be success. Clauses 1 and 2 (binding and digest) are
        driven by T-U2 and T-V1.
        """
        harness = self.harness_with_edits([
            http_error(400, b'{"description": "message is not modified"}')
        ])
        self.bound_and_completed(harness)
        harness.adapter.deliver_result_edits()
        marker = self.delivery(harness)
        self.assertNotEqual(
            marker["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT
        )
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_EDIT_INDEFINITE
        )
        sends, _ = self.visible_objects(harness)
        self.assertEqual(sends, [])


class UnbindableTests(DeliveryCase):

    def test_U1_message_to_edit_not_found_never_sends_a_replacement(
        self
    ):
        """T-U1 (R-3): the bound object is gone. Asserted on the
        recorded TRANSPORT CALL LOG that ZERO sendMessage calls
        occurred on the result path — not merely that the state is
        right."""
        harness = self.harness_with_edits([
            http_error(400, api_refusal(NOT_FOUND))
        ])
        self.bound_and_completed(harness)
        before = len(harness.api.transport.calls)
        harness.adapter.deliver_result_edits()
        for _ in range(3):
            harness.adapter.deliver_result_edits()
            harness.adapter.deliver_pending_results()

        marker = self.delivery(harness)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DEGRADED_UNBINDABLE
        )
        self.assertIsNone(marker["edited_message_id"])
        # THE TRANSPORT CALL LOG: not one sendMessage after the edit.
        after = harness.api.transport.calls[before:]
        methods = [
            "sendMessage" if "message_id" not in call["payload"]
            else "editMessageText" for call in after
        ]
        self.assertNotIn(
            "sendMessage", methods,
            "R-3: NO replacement message may EVER be sent; the"
            " transport log shows %r" % (methods,),
        )
        self.assertEqual(harness.api.chunking_sends, [])
        sends, _ = self.visible_objects(harness)
        self.assertEqual(sends, [])
        # TERMINAL: further passes attempt nothing at all.
        self.assertEqual(len(harness.api.edit_calls), 1)

    def test_U2_binding_mismatch_fails_closed_and_is_surfaced(self):
        """T-U2 (R-2 clause 1): if the binding moved under us between
        the claim and the outcome, the edit is NOT success — read from
        the STATE FILE."""
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(harness)
        real_edit = harness.api.edit_message_text

        def edit_then_move_the_binding(chat_id, message_id, text):
            outcome = real_edit(chat_id, message_id, text)
            with wa_store.exclusive_store_lock(harness.tmpdir):
                workflows = harness.workflow_store.load()
                placeholder = (
                    workflows["workflows"]["wf-0001"]
                    ["result_placeholder"]
                )
                placeholder["message_id"] = PLACEHOLDER_ID + 1
                harness.workflow_store.save(workflows)
            return outcome

        harness.api.edit_message_text = edit_then_move_the_binding
        harness.adapter.deliver_result_edits()

        marker = self.delivery(harness)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DEGRADED_UNBINDABLE,
            "an edit whose binding no longer matches must fail closed",
        )
        self.assertIsNone(marker["edited_message_id"])
        self.assertEqual(harness.api.chunking_sends, [])


class RevisionBindingTests(DeliveryCase):

    def test_V1_a_stale_digest_never_satisfies_delivery(self):
        """T-V1 (R-4): a delivery recorded against a STALE
        verified_result digest does not satisfy the CURRENT one."""
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID}),
            api_ok({"message_id": PLACEHOLDER_ID}),
        ])
        self.bound_and_completed(harness, "first")
        harness.adapter.deliver_result_edits()
        self.assertEqual(len(harness.api.edit_calls), 1)
        # A revised result: the recorded digest is now stale.
        self.record_result(harness, "second")
        entry = self.on_disk(harness)
        self.assertNotEqual(
            entry["result_delivery"]["verified_result_digest"],
            entry["verified_result"]["digest"],
        )
        self.assertTrue(
            harness.adapter._edit_delivery_claimable(entry),
            "a stale digest must NOT count as delivered",
        )
        harness.adapter.deliver_result_edits()
        self.assertEqual(len(harness.api.edit_calls), 2)
        self.assertEqual(
            self.delivery(harness)["verified_result_digest"],
            entry["verified_result"]["digest"],
        )

    def test_V2_delivery_records_both_digests(self):
        """T-V2: BOTH the verified-result digest and the rendered-text
        digest are recorded, and both are the real ones."""
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(harness, "a verified outcome")
        harness.adapter.deliver_result_edits()
        entry = self.on_disk(harness)
        marker = entry["result_delivery"]
        self.assertEqual(
            marker["verified_result_digest"],
            entry["verified_result"]["digest"],
        )
        self.assertEqual(
            marker["rendered_digest"],
            wa_digest.text_digest(
                adapter_module.render_result_text(entry)
            ),
        )
        self.assertNotEqual(
            marker["verified_result_digest"], marker["rendered_digest"]
        )
        self.assertIsNotNone(marker["attempted_at"])
        self.assertIsNotNone(marker["settled_at"])


class EngineFieldTablePinTests(DeliveryCase):
    """Round-02 G4: the CROSS-BOUNDARY pin the F1 comment claims.

    `record.py::_DELIVERY_FIELD_TABLE` must mirror exactly what the
    adapter WRITES for each delivery state. This test drives the REAL
    engine to produce ALL EIGHT delivery states — the five edit-lane
    states, plus the three legacy states (delivered, and reserved /
    partial via the legacy engine's own reserve and partial-send paths)
    — then asserts (a) the state was actually reached on disk — which means the engine's save PASSED
    validation, so a table that rejected the engine's shape would make
    the state UNreachable and fail here — and (b) the on-disk marker's
    null/non-null profile matches an INDEPENDENTLY AUTHORED expectation
    (not read from the table under test). Together these pin the table
    to engine output for every state the engine emits.
    """

    def _profile(self, marker):
        return {
            key: marker.get(key) is not None
            for key in (
                "telegram_message_id", "verified_result_digest",
                "rendered_digest", "edited_message_id", "attempted_at",
                "settled_at", "problem",
            )
        }

    def test_engine_output_satisfies_the_delivery_field_table(self):
        T, F = True, False

        # delivered_by_edit
        h = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.bound_and_completed(h)
        h.adapter.deliver_result_edits()
        m = self.delivery(h)
        self.assertIsNotNone(
            m, "the engine wrote NO delivery marker for this state")
        self.assertEqual(m["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT)
        self.assertEqual(self._profile(m), {
            "telegram_message_id": F, "verified_result_digest": T,
            "rendered_digest": T, "edited_message_id": T,
            "attempted_at": T, "settled_at": T, "problem": F,
        })
        wa_record.validate_record(self.on_disk(h))
        self.setUp()

        # edit_pending (crash: intent written, outcome save fails)
        h = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.bound_and_completed(h)
        original = h.workflow_store.save
        saves = {"n": 0}

        def fail_second(document):
            saves["n"] += 1
            if saves["n"] == 2:
                raise wa_store.StoreError("transient")
            return original(document)

        h.workflow_store.save = fail_second
        try:
            h.adapter.deliver_result_edits()
        finally:
            h.workflow_store.save = original
        m = self.delivery(h)
        self.assertIsNotNone(
            m, "the engine wrote NO delivery marker for this state")
        self.assertEqual(m["state"], wa_record.DELIVERY_EDIT_PENDING)
        self.assertEqual(self._profile(m), {
            "telegram_message_id": F, "verified_result_digest": T,
            "rendered_digest": T, "edited_message_id": F,
            "attempted_at": T, "settled_at": F, "problem": F,
        })
        wa_record.validate_record(self.on_disk(h))
        self.setUp()

        # degraded_unrenderable (oversize summary)
        h = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.approve(h)
        h.adapter.ensure_result_placeholders()
        self.record_result(h, "y" * wa_record.MAX_VERIFIED_SUMMARY_CHARS)
        h.adapter.deliver_result_edits()
        m = self.delivery(h)
        self.assertIsNotNone(
            m, "the engine wrote NO delivery marker for this state")
        self.assertEqual(
            m["state"], wa_record.DELIVERY_DEGRADED_UNRENDERABLE)
        degraded_profile = {
            "telegram_message_id": F, "verified_result_digest": T,
            "rendered_digest": T, "edited_message_id": F,
            "attempted_at": T, "settled_at": T, "problem": T,
        }
        self.assertEqual(self._profile(m), degraded_profile)
        wa_record.validate_record(self.on_disk(h))
        self.setUp()

        # degraded_unbindable (message to edit not found)
        h = self.harness_with_edits(
            [http_error(400, api_refusal(NOT_FOUND))])
        self.bound_and_completed(h)
        h.adapter.deliver_result_edits()
        m = self.delivery(h)
        self.assertIsNotNone(
            m, "the engine wrote NO delivery marker for this state")
        self.assertEqual(
            m["state"], wa_record.DELIVERY_DEGRADED_UNBINDABLE)
        self.assertEqual(self._profile(m), degraded_profile)
        wa_record.validate_record(self.on_disk(h))
        self.setUp()

        # edit_indefinite (ambiguous 500)
        h = self.harness_with_edits([http_error(500, None)])
        self.bound_and_completed(h)
        h.adapter.deliver_result_edits()
        m = self.delivery(h)
        self.assertIsNotNone(
            m, "the engine wrote NO delivery marker for this state")
        self.assertEqual(m["state"], wa_record.DELIVERY_EDIT_INDEFINITE)
        self.assertEqual(self._profile(m), degraded_profile)
        wa_record.validate_record(self.on_disk(h))
        self.setUp()

        legacy_null_profile = {
            "telegram_message_id": F, "verified_result_digest": F,
            "rendered_digest": F, "edited_message_id": F,
            "attempted_at": F, "settled_at": F, "problem": F,
        }

        # legacy delivered (at-most-once send)
        h = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.legacy_completed(h, "legacy result")
        h.adapter.deliver_pending_results()
        m = self.delivery(h)
        self.assertIsNotNone(
            m, "the engine wrote NO delivery marker for this state")
        self.assertEqual(m["state"], wa_record.DELIVERY_DELIVERED)
        self.assertEqual(self._profile(m), dict(
            legacy_null_profile, telegram_message_id=T))
        wa_record.validate_record(self.on_disk(h))
        self.setUp()

        # legacy RESERVED — deliver_pending_results writes it before the
        # send; suppressing _deliver_one_result leaves it on disk (the
        # crash-between-reserve-and-send window it exists for).
        h = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.legacy_completed(h, "legacy result")
        original = h.adapter._deliver_one_result
        h.adapter._deliver_one_result = lambda wid, entry: None
        try:
            h.adapter.deliver_pending_results()
        finally:
            h.adapter._deliver_one_result = original
        m = self.delivery(h)
        self.assertIsNotNone(
            m, "the engine wrote NO delivery marker for this state")
        self.assertEqual(m["state"], wa_record.DELIVERY_RESERVED)
        self.assertEqual(self._profile(m), legacy_null_profile)
        wa_record.validate_record(self.on_disk(h))
        self.setUp()

        # legacy PARTIAL — _deliver_one_result writes it when the send
        # displayed some chunks but did not complete. Inject that
        # outcome for the result-header send.
        h = self.harness_with_edits([api_ok({"message_id": PLACEHOLDER_ID})])
        self.legacy_completed(h, "legacy result")
        h.api.result_send_script.append(telegram_api.SendOutcome(
            ok=False, message_ids=(), chunks_sent=1, truncated_chars=0,
            problem="partial send"))
        h.adapter.deliver_pending_results()
        m = self.delivery(h)
        self.assertIsNotNone(
            m, "the engine wrote NO delivery marker for this state")
        self.assertEqual(m["state"], wa_record.DELIVERY_PARTIAL)
        self.assertEqual(self._profile(m), legacy_null_profile)
        wa_record.validate_record(self.on_disk(h))

        # TOTALITY: every row in the table was produced above.
        self.assertEqual(
            set(wa_record.DELIVERY_STATES),
            {
                wa_record.DELIVERY_DELIVERED_BY_EDIT,
                wa_record.DELIVERY_EDIT_PENDING,
                wa_record.DELIVERY_DEGRADED_UNRENDERABLE,
                wa_record.DELIVERY_DEGRADED_UNBINDABLE,
                wa_record.DELIVERY_EDIT_INDEFINITE,
                wa_record.DELIVERY_DELIVERED,
                wa_record.DELIVERY_RESERVED,
                wa_record.DELIVERY_PARTIAL,
            },
            "if a delivery state is added, this cross-boundary pin must"
            " drive it too — it must not silently cover fewer states"
            " than the table has rows",
        )


class RenderGuardTests(DeliveryCase):

    def test_G1_constant_drift_alarm_only_not_the_guarantee(self):
        """T-G1: the arithmetic, computed from the LIVE constants with
        zero hard-coded literals.

        LABELLING (Supervisor ACCEPTED CORRECTION to strategy §3):
        this test is ONLY a CONSTANT-DRIFT ALARM. The LOAD-BEARING
        single-message guarantee is the RENDER-TIME GUARD, pinned by
        T-G2/T-G3/T-G4. This arithmetic can never establish the
        guarantee: R-18 now bounds `issue_or_pr.number` at the record
        boundary (its canonical URL must fit MAX_TARGET_URL_CHARS), but
        the guard remains load-bearing for the verified-result SUMMARY,
        which the record boundary does NOT bound to one message
        (MAX_VERIFIED_SUMMARY_CHARS is wider than the enforced-
        presentable MAX_OUTCOME_DETAIL_CHARS), so a schema-cap summary
        can still overflow one message.
        """
        from telegram_operator import protocol
        from workflow_authority import canonical
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(harness, "x")
        entry = self.on_disk(harness)
        overhead = len(adapter_module.render_result_text(
            dict(entry, verified_result=dict(
                entry["verified_result"], summary=""))
        ))
        worst_case_url = canonical.MAX_TARGET_URL_CHARS - len(
            entry["target"]["canonical_url"]
        )
        total = (
            overhead + worst_case_url
            + protocol.MAX_OUTCOME_DETAIL_CHARS
        )
        self.assertLessEqual(
            total, telegram_api.MAX_MESSAGE_CHARS,
            "at the ENFORCED upstream summary bound the result fits one"
            " message; this is a drift alarm, NOT the guarantee",
        )
        # And the schema's own bound is deliberately WIDER, which is
        # exactly why the render guard is the real closure.
        self.assertGreater(
            wa_record.MAX_VERIFIED_SUMMARY_CHARS,
            protocol.MAX_OUTCOME_DETAIL_CHARS,
        )

    def test_G2_oversize_result_fails_closed_and_never_chunks(self):
        """T-G2: the LOAD-BEARING guard. An over-cap rendered result
        becomes `degraded_unrenderable` — no edit, no send, no
        chunking, no truncation notice anywhere."""
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        summary = "y" * wa_record.MAX_VERIFIED_SUMMARY_CHARS
        self.record_result(harness, summary)
        entry = self.on_disk(harness)
        self.assertGreater(
            len(adapter_module.render_result_text(entry)),
            telegram_api.MAX_MESSAGE_CHARS,
            "the fixture must really be over the cap",
        )
        harness.adapter.deliver_result_edits()

        marker = self.delivery(harness)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DEGRADED_UNRENDERABLE
        )
        self.assertEqual(
            harness.api.edit_calls, [], "no edit may be attempted"
        )
        self.assertEqual(harness.api.chunking_sends, [])
        sends, _ = self.visible_objects(harness)
        self.assertEqual(sends, [])
        # Truthful, and never a silent truncation.
        self.assertIn("never chunked", marker["problem"])
        self.assertIn(str(telegram_api.MAX_MESSAGE_CHARS),
                      marker["problem"])
        for forbidden in ("[message cut here", "characters omitted"):
            self.assertNotIn(forbidden, marker["problem"])

    def test_G3_the_result_path_never_touches_the_chunking_sender(self):
        """T-G3: BEHAVIOURAL, on the call log — `send_message` is
        recorded and must be untouched by the edit path across every
        outcome shape. A source scan would be early warning only."""
        for script in (
            [api_ok({"message_id": PLACEHOLDER_ID})],
            [http_error(400, api_refusal(NOT_MODIFIED))],
            [http_error(400, api_refusal(NOT_FOUND))],
            [http_error(500, None)],
        ):
            harness = self.harness_with_edits(script)
            self.bound_and_completed(harness)
            harness.adapter.deliver_result_edits()
            self.assertEqual(
                harness.api.chunking_sends, [],
                "the result path called the CHUNKING sender (%r)"
                % (script,),
            )
            self.setUp()

    def test_G4_an_over_long_issue_number_is_REFUSED_at_the_record(self):
        """T-G4, RETARGETED under RULING R-18 — not removed.

        Its old job was to show the render guard catching a huge issue
        number at delivery time. R-18 establishes that such a record
        should never have been ACCEPTED: `canonical.py` derives the
        number from the FULL issue URL, which is bounded by
        `MAX_TARGET_URL_CHARS`, but `record.py` bounded the number not
        at all. So G4's new job is to prove the VALIDATOR refuses it,
        with its own problem code.

        The bound is computed from LIVE canonical constants — no
        literal 512 and no literal 482 — so a constant change breaks
        this test rather than silently widening the contract.
        """
        from workflow_authority import canonical
        from test_mission import mission_document
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        repo_url = "https://github.com/octocat/target"
        room = (
            canonical.MAX_TARGET_URL_CHARS
            - len(repo_url) - len("/") - len(canonical.ISSUE_SEGMENT)
            - len("/")
        )
        # The LARGEST number the canonical contract can express for
        # this repository URL, and the first one it cannot.
        largest_legal = int("9" * room)
        first_illegal = int("9" * (room + 1))

        harness.offer_mission(document=mission_document(
            issue_or_pr={"kind": "issue", "number": largest_legal}
        ))
        bound = harness.bound_message_id()
        harness.adapter.process_update(
            cb_update(10, "A:wf-0001", message_id=bound)
        )
        entry = self.on_disk(harness)
        self.assertEqual(
            entry["target"]["issue_or_pr"]["number"], largest_legal,
            "the largest canonical-legal number must be ACCEPTED",
        )

        # One digit more is UNREPRESENTABLE — proven on a COHERENT
        # record. F4: `_validate_target` (R-18) runs BEFORE the total
        # render-binding check, so an INCOHERENT over-record (number
        # bumped, rendered_text stale) would, under a mutant deleting
        # the R-18 guard, be refused by PROBLEM_RENDER_BINDING — a kill
        # attributable to the wrong guard. Re-rendering the
        # authorization WITH the oversized number makes R-18 the SOLE
        # refuser: delete it and the record VALIDATES, firing the
        # authored `self.fail` below.
        from workflow_authority import rendering as wa_rendering

        def coherent_over(kind, number):
            over = dict(
                entry,
                target=dict(
                    entry["target"],
                    issue_or_pr={"kind": kind, "number": number},
                ),
                mission_authorization=dict(
                    entry["mission_authorization"]
                ),
            )
            text = wa_rendering.render_record_text(over)
            over["mission_authorization"]["rendered_text"] = text
            over["mission_authorization"]["digest_sha256"] = (
                wa_digest.text_digest(text)
            )
            return over

        over = coherent_over("issue", first_illegal)
        try:
            wa_record.validate_record(over)
        except wa_record.RecordError as exc:
            self.assertEqual(
                exc.problem, wa_record.PROBLEM_ISSUE_URL_TOO_LONG,
                "a coherent over-bound issue record must be refused by"
                " the R-18 bound, not another guard",
            )
            self.assertIn(str(canonical.MAX_TARGET_URL_CHARS),
                          str(exc))
        else:
            self.fail(
                "a coherent over-bound issue number must be REFUSED at"
                " the record boundary by the R-18 bound; it VALIDATED"
            )
        # A PR target is bounded by its own (shorter) segment.
        pr_room = (
            canonical.MAX_TARGET_URL_CHARS
            - len(repo_url) - len("/") - len(canonical.PULL_SEGMENT)
            - len("/")
        )
        self.assertGreater(pr_room, room)
        over_pr = coherent_over("pr", int("9" * (pr_room + 1)))
        try:
            wa_record.validate_record(over_pr)
        except wa_record.RecordError as exc:
            self.assertEqual(
                exc.problem, wa_record.PROBLEM_ISSUE_URL_TOO_LONG,
                "a coherent over-bound PR record must be refused by the"
                " R-18 bound, not another guard",
            )
        else:
            self.fail(
                "a coherent over-bound PR number must be REFUSED at the"
                " record boundary by the R-18 bound; it VALIDATED"
            )

    def test_G5_the_MAXIMUM_LEGAL_target_still_fits_one_message(self):
        """T-G5 (R-18 item 5) — the ITEM-13 PROOF, and the reason the
        boundary check is strictly stronger than the guard alone.

        Construct the MAXIMUM LEGAL canonical target, give it a
        summary at the enforced upstream bound, and assert the whole
        rendered message fits ONE Telegram message. Every number comes
        from LIVE constants, so a constant change breaks this.

        The render guard proves never-chunk and fail-closed; it does
        NOT prove that every legal verified result is PRESENTABLE.
        This does — and `degraded_unrenderable` therefore covers only
        what the record boundary has already made unrepresentable.
        """
        from telegram_operator import protocol
        from workflow_authority import canonical
        from test_mission import mission_document
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        repo_url = "https://github.com/octocat/target"
        room = (
            canonical.MAX_TARGET_URL_CHARS
            - len(repo_url) - len("/") - len(canonical.ISSUE_SEGMENT)
            - len("/")
        )
        harness.offer_mission(document=mission_document(
            issue_or_pr={"kind": "issue", "number": int("9" * room)}
        ))
        bound = harness.bound_message_id()
        harness.adapter.process_update(
            cb_update(10, "A:wf-0001", message_id=bound)
        )
        harness.adapter.ensure_result_placeholders()
        self.record_result(
            harness, "s" * protocol.MAX_OUTCOME_DETAIL_CHARS
        )
        entry = self.on_disk(harness)
        rendered = adapter_module.render_result_text(entry)
        self.assertLessEqual(
            len(rendered), telegram_api.MAX_MESSAGE_CHARS,
            "the MAXIMUM LEGAL canonical target with a summary at the"
            " enforced bound must fit ONE Telegram message; got %d of"
            " %d" % (len(rendered), telegram_api.MAX_MESSAGE_CHARS),
        )
        # ... and it is genuinely DELIVERED, not merely renderable.
        harness.adapter.deliver_result_edits()
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DELIVERED_BY_EDIT,
        )
        self.assertEqual(len(harness.api.edit_calls), 1)

    def test_G6_the_render_guard_is_RETAINED_as_defence_in_depth(self):
        """R-18 item 4: the runtime guard stays, unchanged and
        load-bearing in its own right.

        It is now unreachable through the record boundary for the
        target line — which is the point — so it is exercised here
        through the summary, the input the boundary does NOT bound
        (MAX_VERIFIED_SUMMARY_CHARS is deliberately wider than the
        enforced upstream MAX_OUTCOME_DETAIL_CHARS). `degraded_
        unrenderable` therefore stays REACHABLE in principle and must
        never be presented as satisfying item 13.
        """
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        self.record_result(
            harness, "y" * wa_record.MAX_VERIFIED_SUMMARY_CHARS
        )
        entry = self.on_disk(harness)
        self.assertGreater(
            len(adapter_module.render_result_text(entry)),
            telegram_api.MAX_MESSAGE_CHARS,
        )
        harness.adapter.deliver_result_edits()
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DEGRADED_UNRENDERABLE,
        )
        self.assertEqual(harness.api.edit_calls, [])
        self.assertEqual(harness.api.chunking_sends, [])

    def test_G7_migration_truth_for_an_over_long_number_on_disk(self):
        """R-18 item 6 — MIGRATION TRUTH, deterministic and tested.

        A record carrying an over-long number could only exist if it
        was created OUTSIDE the canonicalizer. Establish what happens
        to one: the store REFUSES the whole load, fail-closed, naming
        the record and the new problem — it is never silently
        repaired, never truncated, and never partially loaded.

        Historical BLOCKED workflows and the legacy lane are untouched
        by this: neither carries an out-of-contract number, and both
        keep loading, which the second half asserts.
        """
        from workflow_authority import canonical
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.approve(harness)
        path = os.path.join(harness.tmpdir, wa_store.WORKFLOWS_FILE_NAME)
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        entry = raw["workflows"]["wf-0001"]
        repo_url = entry["target"]["canonical_url"]
        room = (
            canonical.MAX_TARGET_URL_CHARS
            - len(repo_url) - len("/") - len(canonical.ISSUE_SEGMENT)
            - len("/")
        )
        # G3: build a COHERENT oversized record — re-render the
        # authorization WITH the oversized number and re-bind its digest.
        # The R-18 bound runs BEFORE the total render-binding check, so
        # an INCOHERENT record (number bumped, rendered_text stale)
        # would, with the R-18 bound deleted, fail on
        # PROBLEM_RENDER_BINDING — a kill attributable to the wrong
        # guard. Coherence makes R-18 the SOLE refuser at BOTH the
        # durable SAVE and the raw on-disk LOAD, so deleting ONLY the
        # R-18 bound makes these authored assertions fail.
        from workflow_authority import rendering as wa_rendering
        entry["target"]["issue_or_pr"] = {
            "kind": "issue", "number": int("9" * (room + 1)),
        }
        rendered = wa_rendering.render_record_text(entry)
        entry["mission_authorization"]["rendered_text"] = rendered
        entry["mission_authorization"]["digest_sha256"] = (
            wa_digest.text_digest(rendered)
        )

        # DURABLE SAVE: the coherent oversized record is refused BEFORE
        # it touches disk, by the R-18 bound.
        save_doc = {
            "workflow_store_schema_version": 2,
            "workflows": {"wf-0001": entry},
        }
        with self.assertRaises(wa_store.StoreError) as caught_save:
            wa_store.WorkflowStore(harness.tmpdir).save(save_doc)
        self.assertIn(
            str(canonical.MAX_TARGET_URL_CHARS),
            str(caught_save.exception),
            "the durable SAVE of a coherent oversized record must be"
            " refused by the R-18 bound",
        )

        # RAW ON-DISK LOAD: the same coherent record written straight to
        # disk fails the WHOLE load closed, by the R-18 bound.
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        os.chmod(path, 0o600)

        with self.assertRaises(wa_store.StoreError) as caught:
            wa_store.WorkflowStore(harness.tmpdir).load()
        message = str(caught.exception)
        self.assertIn("wf-0001", message)
        self.assertIn(str(canonical.MAX_TARGET_URL_CHARS), message)
        # Fail-closed, never repaired: the bytes on disk are untouched.
        with open(path, "r", encoding="utf-8") as handle:
            still = json.load(handle)
        self.assertEqual(
            still["workflows"]["wf-0001"]["target"]["issue_or_pr"],
            entry["target"]["issue_or_pr"],
        )

        # A historical BLOCKED record and a legacy null-placeholder
        # record are unaffected and still load.
        self.setUp()
        clean = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        # R-17: a GENUINE pre-placeholder record via the real pre-I3
        # arming (arm_legacy), then BLOCKED — no on-disk placeholder
        # nulling. The point stands: a null-placeholder BLOCKED record
        # loads unchanged.
        self.arm_legacy(clean)
        with wa_store.exclusive_store_lock(clean.tmpdir):
            workflows = clean.workflow_store.load()
            record = workflows["workflows"]["wf-0001"]
            wa_record.apply_transition(record, wa_record.PHASE_BLOCKED)
            clean.workflow_store.save(workflows)
        reloaded = wa_store.WorkflowStore(
            clean.tmpdir
        ).load()["workflows"]["wf-0001"]
        self.assertEqual(reloaded["phase"], wa_record.PHASE_BLOCKED)
        self.assertIsNone(reloaded["result_placeholder"])
        self.assertIsNone(reloaded["verified_result"])

    def test_R18_worst_case_target_leaves_conservative_headroom(self):
        """R-18 item 4: the WORST-CASE legal target, rendered with a
        summary at the enforced upstream bound, leaves at least 920
        characters of conservative Telegram headroom in one message.

        Every quantity is DERIVED from live constants — the maximum
        legal issue number for the repository URL, the render overhead,
        `MAX_OUTCOME_DETAIL_CHARS`, `MAX_TARGET_URL_CHARS`,
        `MAX_MESSAGE_CHARS`. Only the 920 acceptance threshold is a
        literal, so widening the canonical URL bound or the summary
        bound erodes the measured headroom and breaks this test rather
        than silently shrinking the margin.
        """
        from telegram_operator import protocol
        from workflow_authority import canonical
        from test_mission import mission_document
        HEADROOM_FLOOR = 920  # the acceptance threshold, the only literal
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        repo_url = "https://github.com/octocat/target"
        room = (
            canonical.MAX_TARGET_URL_CHARS
            - len(repo_url) - len("/") - len(canonical.ISSUE_SEGMENT)
            - len("/")
        )
        # The MAXIMUM LEGAL issue number: its canonical URL sits exactly
        # on MAX_TARGET_URL_CHARS.
        harness.offer_mission(document=mission_document(
            issue_or_pr={"kind": "issue", "number": int("9" * room)}
        ))
        bound = harness.bound_message_id()
        harness.adapter.process_update(
            cb_update(10, "A:wf-0001", message_id=bound)
        )
        harness.adapter.ensure_result_placeholders()
        # The largest PRESENTABLE summary is the ENFORCED upstream bound,
        # not the wider schema cap (which the render guard would refuse).
        self.record_result(
            harness, "s" * protocol.MAX_OUTCOME_DETAIL_CHARS
        )
        entry = self.on_disk(harness)
        rendered = adapter_module.render_result_text(entry)
        headroom = telegram_api.MAX_MESSAGE_CHARS - len(rendered)
        self.assertLessEqual(
            len(rendered), telegram_api.MAX_MESSAGE_CHARS,
            "the worst-case legal target must fit one message",
        )
        self.assertGreaterEqual(
            headroom, HEADROOM_FLOOR,
            "the worst-case legal target must leave >= %d chars of"
            " conservative headroom; measured %d (rendered %d of %d)"
            % (HEADROOM_FLOOR, headroom, len(rendered),
               telegram_api.MAX_MESSAGE_CHARS),
        )
        # And it is genuinely DELIVERED at that worst case, not merely
        # renderable.
        harness.adapter.deliver_result_edits()
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DELIVERED_BY_EDIT,
        )


class ClaimabilityBoundaryTests(DeliveryCase):
    """Which records the edit engine may claim — every boundary of
    `_edit_delivery_claimable`, driven through the real engine."""

    def prepared(self, summary="a verified outcome"):
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(harness, summary)
        return harness

    def set_placeholder_state(self, harness, state):
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            placeholder = (
                workflows["workflows"]["wf-0001"]["result_placeholder"]
            )
            placeholder["state"] = state
            if state in (wa_record.PLACEHOLDER_REQUIRED,):
                placeholder["message_id"] = None
                placeholder["bound_at"] = None
                placeholder["sent_at"] = None
                placeholder["text_digest"] = None
            elif state in (wa_record.PLACEHOLDER_SENDING,
                           wa_record.PLACEHOLDER_FAILED_UNSENT,
                           wa_record.PLACEHOLDER_INDEFINITE):
                placeholder["message_id"] = None
                placeholder["bound_at"] = None
            harness.workflow_store.save(workflows)

    def test_only_a_BOUND_placeholder_may_be_claimed(self):
        """The edit engine drives ONLY workflows carrying a BINDING.
        Every other placeholder state is skipped — there is no object
        to edit, and inventing one would create a second message."""
        for state in wa_record.PLACEHOLDER_STATES:
            if state == wa_record.PLACEHOLDER_BOUND:
                continue
            harness = self.prepared()
            self.set_placeholder_state(harness, state)
            harness.adapter.deliver_result_edits()
            self.assertEqual(
                harness.api.edit_calls, [],
                "placeholder state %r must not be claimed" % state,
            )
            self.assertEqual(harness.api.chunking_sends, [], state)
            self.assertIsNone(self.delivery(harness), state)
            self.setUp()
        # ANTI-VACUITY: `bound` IS claimed from the same fixture.
        harness = self.prepared()
        harness.adapter.deliver_result_edits()
        self.assertEqual(len(harness.api.edit_calls), 1)

    def test_a_non_completed_workflow_is_never_delivered(self):
        """A verified result on a workflow that has not reached
        COMPLETED is not deliverable: delivery is the COMPLETED
        lifecycle step, and delivering earlier would present an outcome
        the Runtime has not finished."""
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.approve(harness)
        harness.adapter.ensure_result_placeholders()
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            entry = workflows["workflows"]["wf-0001"]
            for phase in (wa_record.PHASE_WORKSPACE_READY,
                          wa_record.PHASE_PREPARED,
                          wa_record.PHASE_VALIDATED,
                          wa_record.PHASE_DISPATCHED,
                          wa_record.PHASE_VERIFIED):
                wa_record.apply_transition(entry, phase)
            entry["verified_result"] = {
                "summary": "early", "digest": wa_digest.text_digest("early"),
                "recorded_at": NOW,
            }
            harness.workflow_store.save(workflows)
        self.assertEqual(
            self.on_disk(harness)["phase"], wa_record.PHASE_VERIFIED
        )
        harness.adapter.deliver_result_edits()
        self.assertEqual(harness.api.edit_calls, [])
        self.assertIsNone(self.delivery(harness))
        # ANTI-VACUITY: once COMPLETED it IS delivered.
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            wa_record.apply_transition(
                workflows["workflows"]["wf-0001"],
                wa_record.PHASE_COMPLETED,
            )
            harness.workflow_store.save(workflows)
        harness.adapter.deliver_result_edits()
        self.assertEqual(len(harness.api.edit_calls), 1)

    def test_degraded_unbindable_is_TERMINAL_for_every_revision(self):
        """R-3: once the bound object is gone it is gone. Even a
        REVISED verified result must not re-attempt — there is nothing
        to edit, and a replacement message is never sent."""
        harness = self.harness_with_edits([
            http_error(400, api_refusal(NOT_FOUND))
        ])
        self.bound_and_completed(harness, "first")
        harness.adapter.deliver_result_edits()
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DEGRADED_UNBINDABLE,
        )
        self.assertEqual(len(harness.api.edit_calls), 1)
        # A genuinely revised result must NOT resurrect it.
        self.record_result(harness, "revised after the object was lost")
        for _ in range(3):
            harness.adapter.deliver_result_edits()
            harness.adapter.deliver_pending_results()
        self.assertEqual(
            len(harness.api.edit_calls), 1,
            "degraded_unbindable is TERMINAL for every revision",
        )
        self.assertEqual(harness.api.chunking_sends, [])
        sends, _ = self.visible_objects(harness)
        self.assertEqual(sends, [])
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DEGRADED_UNBINDABLE,
        )


class OutcomeProofTests(DeliveryCase):
    """The R-2 four-part proof at the OUTCOME boundary, and the
    intent-identity guards around it."""

    def claim_then(self, edit_script, mutate):
        """Drive one edit, running `mutate(harness)` between the
        transport call and the outcome save."""
        harness = self.harness_with_edits(edit_script)
        self.bound_and_completed(harness)
        real_edit = harness.api.edit_message_text

        def edit_then_mutate(chat_id, message_id, text):
            outcome = real_edit(chat_id, message_id, text)
            mutate(harness)
            return outcome

        harness.api.edit_message_text = edit_then_mutate
        harness.adapter.deliver_result_edits()
        return harness

    def revise_result(self, harness):
        self.record_result(harness, "a DIFFERENT verified result")

    def change_delivery(self, state):
        def mutate(harness):
            with wa_store.exclusive_store_lock(harness.tmpdir):
                workflows = harness.workflow_store.load()
                delivery = (
                    workflows["workflows"]["wf-0001"]["result_delivery"]
                )
                delivery["state"] = state
                # I5/F1: the marker must stay STATE-COHERENT — the
                # edit_pending write-ahead lacks settled_at/problem,
                # which the settled edit states require, so add them
                # when moving off edit_pending. (The test only moves to
                # DEGRADED_UNBINDABLE, which requires both.)
                if state != wa_record.DELIVERY_EDIT_PENDING:
                    if delivery.get("settled_at") is None:
                        delivery["settled_at"] = NOW
                    if state in (
                        wa_record.DELIVERY_DEGRADED_UNBINDABLE,
                        wa_record.DELIVERY_DEGRADED_UNRENDERABLE,
                        wa_record.DELIVERY_EDIT_INDEFINITE,
                    ) and delivery.get("problem") is None:
                        delivery["problem"] = (
                            "left edit_pending: binding changed"
                        )
                harness.workflow_store.save(workflows)
        return mutate

    def change_rendered_digest(self, harness):
        with wa_store.exclusive_store_lock(harness.tmpdir):
            workflows = harness.workflow_store.load()
            delivery = (
                workflows["workflows"]["wf-0001"]["result_delivery"]
            )
            delivery["rendered_digest"] = wa_digest.text_digest("other")
            harness.workflow_store.save(workflows)

    def test_the_binding_proof_covers_chat_id_as_well_as_message_id(
        self
    ):
        """R-2 clause 1 covers BOTH halves of the binding.

        Driven at UNIT level on purpose, and labelled: a chat_id that
        differs between the record and the claim is UNREPRESENTABLE in
        a valid store — `telegram.chat_id` is part of the digest-bound
        rendered authorization, and the I1 schema additionally requires
        `result_placeholder.chat_id == telegram.chat_id`. Writing one
        is refused by the store, as it should be. So this is BELT
        coverage of the guard itself: if either schema rule were ever
        relaxed, the delivery path must still refuse rather than record
        a delivery against the wrong chat.
        """
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(harness)
        entry = self.on_disk(harness)
        text = adapter_module.render_result_text(entry)
        base_claim = {
            "chat_id": entry["result_placeholder"]["chat_id"],
            "message_id": entry["result_placeholder"]["message_id"],
            "text": text,
            "rendered_digest": wa_digest.text_digest(text),
            "verified_result_digest": entry["verified_result"]["digest"],
        }
        ok_outcome = telegram_api.EditOutcome(ok=True)

        # ANTI-VACUITY: the matching claim IS success.
        state, _, edited = harness.adapter._classify_edit_outcome(
            entry, base_claim, ok_outcome, None
        )
        self.assertEqual(state, wa_record.DELIVERY_DELIVERED_BY_EDIT)
        self.assertEqual(edited, base_claim["message_id"])

        # A different CHAT, and a different MESSAGE, each fail closed.
        for key in ("chat_id", "message_id"):
            claim = dict(base_claim)
            claim[key] = base_claim[key] + 1
            state, problem, edited = (
                harness.adapter._classify_edit_outcome(
                    entry, claim, ok_outcome, None
                )
            )
            self.assertEqual(
                state, wa_record.DELIVERY_DEGRADED_UNBINDABLE, key
            )
            self.assertIsNone(edited, key)
            self.assertIn("binding", problem, key)
        # ... and the same holds for a message-not-modified response.
        detail = telegram_api.CallDetail(
            outcome=telegram_api.CALL_TELEGRAM_REFUSED,
            http_status=400, body_parsed=True, telegram_ok=False,
            description=NOT_MODIFIED,
        )
        for key in ("chat_id", "message_id"):
            claim = dict(base_claim)
            claim[key] = base_claim[key] + 1
            state, _, _ = harness.adapter._classify_edit_outcome(
                entry, claim,
                telegram_api.EditOutcome(ok=False, detail=detail),
                detail,
            )
            self.assertEqual(
                state, wa_record.DELIVERY_DEGRADED_UNBINDABLE, key
            )

    def test_not_modified_with_a_stale_result_is_not_success(self):
        """R-2 clause 2 / R-4: message-not-modified proves the object
        holds the text THIS pass rendered. If the verified result was
        revised while the edit was in flight, that text is no longer
        the current one, so it is NOT recorded as delivered."""
        harness = self.claim_then(
            [http_error(400, api_refusal(NOT_MODIFIED))],
            self.revise_result,
        )
        marker = self.delivery(harness)
        self.assertNotEqual(
            marker["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT,
            "not-modified without the CURRENT-revision proof must not"
            " be recorded as delivered",
        )
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_EDIT_INDEFINITE
        )
        self.assertIn("four-part proof", marker["problem"])
        # ANTI-VACUITY: unrevised, the SAME response IS success.
        self.setUp()
        clean = self.harness_with_edits([
            http_error(400, api_refusal(NOT_MODIFIED))
        ])
        self.bound_and_completed(clean)
        clean.adapter.deliver_result_edits()
        self.assertEqual(
            self.delivery(clean)["state"],
            wa_record.DELIVERY_DELIVERED_BY_EDIT,
        )

    def test_an_outcome_never_overwrites_a_changed_intent(self):
        """Only the intent THIS pass wrote may be resolved: neither a
        record that left `edit_pending` nor one re-armed for a
        different rendered text."""
        harness = self.claim_then(
            [api_ok({"message_id": PLACEHOLDER_ID})],
            self.change_delivery(wa_record.DELIVERY_DEGRADED_UNBINDABLE),
        )
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DEGRADED_UNBINDABLE,
            "an outcome must not overwrite a record that left"
            " edit_pending",
        )
        self.setUp()
        harness = self.claim_then(
            [api_ok({"message_id": PLACEHOLDER_ID})],
            self.change_rendered_digest,
        )
        marker = self.delivery(harness)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_EDIT_PENDING,
            "an outcome for a DIFFERENT rendered text must not resolve"
            " this intent",
        )


class LegacyLaneGateTests(DeliveryCase):
    """RULING R-15: the legacy at-most-once lane is reachable ONLY by
    records that never entered the placeholder architecture.

    The defect this closes was real and I reproduced it first-hand
    before fixing it: `deliver_pending_results` selected on
    `phase == COMPLETED and verified_result is not None and
    result_delivery is None` and NEVER read `result_placeholder`, so a
    placeholder-BOUND record and a legacy record were
    indistinguishable to it. `_deliver_one_result` claimed in prose
    that it was reached only by legacy records; nothing enforced it.
    """

    def test_R15_a_bound_record_never_takes_the_legacy_lane(self):
        """R-15 regression, driving the EXACT transient-store schedule.

        `run()` calls `deliver_result_edits()` and then
        `deliver_pending_results()` in the SAME pass.
        `_edit_delivery_workflow_ids` returns [] on a TRANSIENT
        StoreError and writes NO marker, so the legacy path then sees a
        BOUND record still carrying `result_delivery is None`.

        Run-loop ordering is NOT an invariant — transient store
        recovery breaks it, and that window is exactly what this task
        exists to close. Asserted on the TRANSPORT CALL LOG that ZERO
        `sendMessage` occurred, not merely on state.
        """
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(harness, "the verified outcome")
        placeholder = self.on_disk(harness)["result_placeholder"]
        self.assertEqual(
            placeholder["state"], wa_record.PLACEHOLDER_BOUND
        )
        self.assertIsNone(
            self.delivery(harness),
            "the fixture must start with NO delivery marker, or the"
            " legacy selector would never look at it",
        )

        # The edit pass fails transiently and writes NOTHING.
        real_load = harness.workflow_store.load
        loads = {"n": 0}

        def transiently_failing_load():
            loads["n"] += 1
            if loads["n"] == 1:
                raise wa_store.StoreError("transient store failure")
            return real_load()

        harness.workflow_store.load = transiently_failing_load
        try:
            harness.adapter.deliver_result_edits()
        finally:
            harness.workflow_store.load = real_load
        self.assertIsNone(
            self.delivery(harness),
            "the fixture must really leave NO marker after the failed"
            " edit pass, or this test proves nothing",
        )

        # ... and the legacy path runs on the SAME pass.
        before = len(harness.api.transport.calls)
        harness.adapter.deliver_pending_results()

        # THE TRANSPORT CALL LOG: not one sendMessage.
        after = harness.api.transport.calls[before:]
        methods = [
            "editMessageText" if "message_id" in call["payload"]
            else "sendMessage" for call in after
        ]
        self.assertNotIn(
            "sendMessage", methods,
            "R-15: a placeholder-BOUND record must NEVER receive a"
            " fresh result message; the transport log shows %r"
            % (methods,),
        )
        self.assertEqual(harness.api.chunking_sends, [])
        sends, edits = self.visible_objects(harness)
        self.assertEqual(
            sends, [],
            "no second visible result object may be created",
        )
        self.assertEqual(edits, [])
        # No legacy marker was written onto a bound record either.
        self.assertIsNone(self.delivery(harness))

        # And the result is still delivered the CORRECT way once the
        # store recovers: by EDITING the bound object.
        harness.adapter.deliver_result_edits()
        marker = self.delivery(harness)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT
        )
        self.assertEqual(marker["edited_message_id"], PLACEHOLDER_ID)
        sends, edits = self.visible_objects(harness)
        self.assertEqual(sends, [])
        self.assertEqual(len(edits), 1)

    def test_R15_every_non_null_placeholder_state_is_excluded(self):
        """The gate is `is None`, not `!= bound`, and every other state
        is genuinely unsafe on this lane:

          * `bound`/`unbindable` — an object exists (or existed), so a
            fresh send is a SECOND visible object;
          * `sending`/`indefinite` — an object MAY exist; a fresh send
            may be a second object;
          * `failed_unsent`/`required` — no object yet, but the
            placeholder loop will still bind one later, leaving a
            stale empty placeholder beside the result.
        """
        for state in wa_record.PLACEHOLDER_STATES:
            harness = self.harness_with_edits([
                api_ok({"message_id": PLACEHOLDER_ID})
            ])
            self.bound_and_completed(harness)
            with wa_store.exclusive_store_lock(harness.tmpdir):
                workflows = harness.workflow_store.load()
                placeholder = (
                    workflows["workflows"]["wf-0001"]
                    ["result_placeholder"]
                )
                placeholder["state"] = state
                if state == wa_record.PLACEHOLDER_REQUIRED:
                    placeholder["message_id"] = None
                    placeholder["bound_at"] = None
                    placeholder["sent_at"] = None
                    placeholder["text_digest"] = None
                elif state in (wa_record.PLACEHOLDER_SENDING,
                               wa_record.PLACEHOLDER_FAILED_UNSENT,
                               wa_record.PLACEHOLDER_INDEFINITE):
                    placeholder["message_id"] = None
                    placeholder["bound_at"] = None
                workflows["workflows"]["wf-0001"]["result_delivery"] = None
                harness.workflow_store.save(workflows)

            harness.adapter.deliver_pending_results()
            sends, _ = self.visible_objects(harness)
            self.assertEqual(
                sends, [],
                "placeholder state %r must not reach the legacy lane"
                % state,
            )
            self.assertIsNone(self.delivery(harness), state)
            self.setUp()

        # ANTI-VACUITY: a genuinely LEGACY record still IS delivered by
        # this lane, with byte-identical semantics. R-17: built with the
        # REAL pre-I3 writer (legacy_completed), never bound-then-nulled.
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.legacy_completed(harness, "legacy result")
        harness.adapter.deliver_pending_results()
        sends, edits = self.visible_objects(harness)
        self.assertEqual(len(sends), 1)
        self.assertEqual(edits, [])
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DELIVERED,
        )

    def test_P11_the_bound_state_conjunct_of_binding_matches_is_pinned(
        self
    ):
        """Reviewer P-11: the `state == PLACEHOLDER_BOUND` conjunct
        inside `binding_matches` had no battery row.

        It is NOT equivalent, and this pins it. A placeholder that went
        `bound -> unbindable` RETAINS its `chat_id` and `message_id`
        (plan §3.1 keeps the binding so /status can name what was
        lost), so without the state conjunct the chat/message halves
        would still match and an edit would be recorded as DELIVERED
        against an object the record itself says is gone.
        """
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(harness)
        entry = self.on_disk(harness)
        text = adapter_module.render_result_text(entry)
        claim = {
            "chat_id": entry["result_placeholder"]["chat_id"],
            "message_id": entry["result_placeholder"]["message_id"],
            "text": text,
            "rendered_digest": wa_digest.text_digest(text),
            "verified_result_digest": entry["verified_result"]["digest"],
        }
        # ANTI-VACUITY: while BOUND, the same claim is success.
        state, _, _ = harness.adapter._classify_edit_outcome(
            entry, claim, telegram_api.EditOutcome(ok=True), None
        )
        self.assertEqual(state, wa_record.DELIVERY_DELIVERED_BY_EDIT)

        # UNBINDABLE keeps chat_id and message_id, so ONLY the state
        # conjunct distinguishes it.
        unbindable = dict(
            entry,
            result_placeholder=dict(
                entry["result_placeholder"],
                state=wa_record.PLACEHOLDER_UNBINDABLE,
            ),
        )
        self.assertEqual(
            unbindable["result_placeholder"]["chat_id"],
            claim["chat_id"],
        )
        self.assertEqual(
            unbindable["result_placeholder"]["message_id"],
            claim["message_id"],
        )
        state, problem, edited = (
            harness.adapter._classify_edit_outcome(
                unbindable, claim,
                telegram_api.EditOutcome(ok=True), None,
            )
        )
        self.assertEqual(
            state, wa_record.DELIVERY_DEGRADED_UNBINDABLE,
            "an edit must not be recorded as delivered against a"
            " placeholder the record says is UNBINDABLE",
        )
        self.assertIsNone(edited)
        self.assertIn("binding", problem)


class RunLoopWiringTests(DeliveryCase):

    def test_the_run_loop_drives_the_edit_engine_each_iteration(self):
        """Behavioural, not a source scan: the real `run()` loop is run
        for exactly one iteration and the calls are recorded."""
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        calls = []
        harness.adapter.ensure_result_placeholders = (
            lambda: calls.append("placeholders")
        )
        harness.adapter.deliver_result_edits = (
            lambda: calls.append("edits")
        )
        harness.adapter.deliver_pending_results = (
            lambda: calls.append("legacy")
        )
        harness.adapter.startup_recovery = lambda: None

        def one_poll():
            harness.adapter._stopping = True
            return True

        harness.adapter.poll_once = one_poll
        harness.adapter.run()
        self.assertIn(
            "edits", calls,
            "run() must drive the edit engine every iteration",
        )
        self.assertEqual(calls, ["placeholders", "edits", "legacy"])


class StatusHonestyTests(DeliveryCase):

    def test_X1_every_state_renders_distinctly_and_never_as_pending(
        self
    ):
        """T-X1: a table over ALL placeholder x delivery states. No
        state renders as "pending" or "delivered" unless it is."""
        render = adapter_module._render_delivery_status
        rendered = {}
        for state in wa_record.DELIVERY_STATES:
            rendered[state] = render({
                "state": state, "reserved_at": 5,
                "telegram_message_id": (
                    42 if state == wa_record.DELIVERY_DELIVERED else None
                ),
            })
        self.assertEqual(
            len(set(rendered.values())), len(rendered), rendered
        )
        delivered_states = (wa_record.DELIVERY_DELIVERED,
                            wa_record.DELIVERY_DELIVERED_BY_EDIT)
        for state, text in rendered.items():
            self.assertNotIn("unrecognized", text, state)
            if state in delivered_states:
                continue
            self.assertNotIn("delivered by editing", text, state)
            # A state that is NOT delivered may not OPEN with a
            # delivery claim either: an operator reads the start of the
            # line, and "delivered. the bound message is gone" would be
            # a lie told in the first word.
            self.assertFalse(
                text.lower().startswith("delivered"),
                "state %r opens with a delivery claim: %r"
                % (state, text),
            )
        # Placeholder states, likewise (I3's renderer, re-pinned here
        # as part of the whole /status surface).
        placeholder_rendered = {
            state: adapter_module._render_placeholder_status({
                "state": state, "chat_id": 1, "message_id": 7,
                "requested_at": 1, "sent_at": 1, "bound_at": 1,
                "text_digest": "a" * 64,
            })
            for state in wa_record.PLACEHOLDER_STATES
        }
        self.assertEqual(
            len(set(placeholder_rendered.values())),
            len(placeholder_rendered),
        )
        for state, text in placeholder_rendered.items():
            self.assertNotIn("pending", text.lower(), state)

    def test_X5_status_discloses_the_legacy_lane_as_at_most_once(self):
        """T-X5 (Supervisor §6, non-negotiable): /status states plainly
        that a legacy, placeholder-free workflow dispatches UNGATED and
        is delivered AT-MOST-ONCE.

        ANCHORED to a line that MUST exist: the assertion first finds
        the workflow's own /status line by its id, then requires the
        disclosure ON THAT LINE. A report that never mentioned the
        workflow could otherwise satisfy a bare `assertIn`
        vacuously.
        """
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        # Build a GENUINE pre-placeholder legacy record (R-17): the real
        # pre-I3 arming (arm_legacy), never a bound record nulled on
        # disk. A placeholder-free record is exactly the legacy lane.
        self.arm_legacy(harness)

        harness.adapter.process_update(msg_update(40, "/status"))
        harness.drain_worker()
        status = [
            send["text"] for send in harness.sends()
            if "Adapter state" in send["text"]
        ][-1]
        anchor = [
            line for line in status.splitlines() if "wf-0001" in line
        ]
        self.assertEqual(
            len(anchor), 1,
            "the anchor line must exist exactly once, or this pin is"
            " vacuous: %r" % (status,),
        )
        line = anchor[0]
        self.assertIn("legacy", line.lower())
        self.assertIn("AT-MOST-ONCE", line)
        self.assertIn("ungated", line)
        # Falsification of the anchor: a report without the workflow
        # must NOT satisfy this test's shape.
        self.assertNotIn("AT-MOST-ONCE", "Adapter state\nRuntime: ok")


class InProcessMarkerTests(DeliveryCase):

    def test_X6_in_process_three_key_marker_is_read_safely(self):
        """T-X6 (Supervisor-directed, a NAMED test not a comment): a
        `result_delivery` marker built IN-PROCESS carries only the
        three legacy keys and has NOT passed the load-boundary
        normalizer. Every read of an additive key must use `.get`,
        never `[...]`.

        The marker is handed straight to the engine and to /status
        without a reload, which is the only way to reproduce the
        un-normalized shape.
        """
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(harness)
        entry = self.on_disk(harness)
        three_key = {
            "state": wa_record.DELIVERY_EDIT_PENDING,
            "reserved_at": NOW,
            "telegram_message_id": None,
        }
        in_process = dict(entry, result_delivery=three_key)

        # The claimability predicate reads additive keys.
        try:
            claimable = harness.adapter._edit_delivery_claimable(
                in_process
            )
        except KeyError as exc:
            self.fail(
                "an in-process three-key marker must be read with"
                " .get; subscripting raised KeyError(%s)" % exc
            )
        self.assertTrue(claimable)

        # /status reads additive keys too.
        for state in wa_record.DELIVERY_STATES:
            marker = dict(three_key, state=state)
            try:
                text = adapter_module._render_delivery_status(marker)
            except KeyError as exc:
                self.fail(
                    "/status must read additive keys with .get; state"
                    " %r raised KeyError(%s)" % (state, exc)
                )
            self.assertTrue(text)

        # The key ABSENT ENTIRELY is the true un-normalized shape: a
        # record built in-process may not carry `result_delivery` at
        # all.
        without_key = dict(entry)
        without_key.pop("result_delivery", None)
        try:
            harness.adapter._edit_delivery_claimable(without_key)
        except KeyError as exc:
            self.fail(
                "a record with NO result_delivery key must be read with"
                " .get; subscripting raised KeyError(%s)" % exc
            )
        try:
            harness.adapter._delivery_marker(
                without_key,
                state=wa_record.DELIVERY_EDIT_PENDING,
                verified_result_digest="a" * 64,
                rendered_digest="b" * 64,
                attempted_at=NOW,
            )
        except KeyError as exc:
            self.fail(
                "the marker builder must read the existing marker with"
                " .get; subscripting raised KeyError(%s)" % exc
            )
        # And with a three-key marker present, the builder must not
        # subscript an ADDITIVE key either.
        try:
            partial = harness.adapter._delivery_marker(
                in_process,
                state=wa_record.DELIVERY_EDIT_PENDING,
                verified_result_digest="a" * 64,
                rendered_digest="b" * 64,
                attempted_at=NOW,
            )
        except KeyError as exc:
            self.fail(
                "the marker builder must read additive keys with .get;"
                " subscripting raised KeyError(%s)" % exc
            )
        self.assertEqual(partial["reserved_at"], NOW)

        # And the marker builder preserves reserved_at from it.
        built = harness.adapter._delivery_marker(
            in_process,
            state=wa_record.DELIVERY_DELIVERED_BY_EDIT,
            verified_result_digest="a" * 64,
            rendered_digest="b" * 64,
            attempted_at=NOW + 5,
        )
        self.assertEqual(built["reserved_at"], NOW)
        self.assertEqual(sorted(built), sorted(
            list(wa_record.RESULT_DELIVERY_LEGACY_KEYS)
            + list(wa_record.RESULT_DELIVERY_ADDITIVE_KEYS)
        ))


class LegacyLaneRegressionTests(DeliveryCase):

    def test_M1_M2_M3_legacy_markers_are_never_edited(self):
        """T-M1..T-M3 regression: legacy DELIVERED/RESERVED/PARTIAL
        markers stay terminal and truthful, and the EDIT engine never
        touches them — even on a placeholder-bound record."""
        for state, message_id in (
            (wa_record.DELIVERY_DELIVERED, 99),
            (wa_record.DELIVERY_RESERVED, None),
            (wa_record.DELIVERY_PARTIAL, None),
        ):
            harness = self.harness_with_edits([
                api_ok({"message_id": PLACEHOLDER_ID})
            ])
            self.bound_and_completed(harness)
            with wa_store.exclusive_store_lock(harness.tmpdir):
                workflows = harness.workflow_store.load()
                workflows["workflows"]["wf-0001"]["result_delivery"] = {
                    "state": state, "reserved_at": NOW,
                    "telegram_message_id": message_id,
                }
                harness.workflow_store.save(workflows)
            before = self.delivery(harness)
            for _ in range(3):
                harness.adapter.deliver_result_edits()
                harness.adapter.deliver_pending_results()
            self.assertEqual(
                self.delivery(harness), before,
                "a legacy %r marker must never be rewritten" % state,
            )
            self.assertEqual(harness.api.edit_calls, [], state)
            self.assertEqual(harness.api.chunking_sends, [], state)
            self.setUp()

    def test_a_legacy_null_placeholder_record_never_enters_the_edit_lane(
        self
    ):
        """Plan §4: the edit engine drives ONLY placeholder-bound
        workflows. A GENUINELY pre-placeholder record — armed the
        DI-REMOTE-2 way, its `result_placeholder` never written — stays
        on the at-most-once path with byte-identical semantics.

        The fixture is built by `legacy_completed`, which runs the real
        arming transaction without the I3 placeholder request, NOT by
        rewriting a bound record's placeholder to null on disk. So this
        certifies the actual historical writer, not a forgery of it.
        """
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.legacy_completed(harness, "legacy result")
        entry = self.on_disk(harness)
        expected_text = adapter_module.render_result_text(entry)
        expected_chat_id = entry["telegram"]["chat_id"]

        try:
            harness.adapter.deliver_result_edits()
        except Exception as exc:
            self.fail(
                "a LEGACY (null-placeholder) record must simply be"
                " SKIPPED by the edit engine; raised %s: %s"
                % (type(exc).__name__, exc)
            )
        self.assertEqual(
            harness.api.edit_calls, [],
            "the edit engine must never claim a legacy record",
        )
        self.assertIsNone(self.delivery(harness))

        # The LEGACY path still delivers it, by sendMessage.
        edits_before = len(harness.api.transport.calls)
        harness.adapter.deliver_pending_results()
        self.assertEqual(
            self.delivery(harness)["state"],
            wa_record.DELIVERY_DELIVERED,
        )

        # ANTI-VACUITY: prove the historical legacy sendMessage path
        # GENUINELY FIRED. Assert positively on the SEND CALL LOG that
        # exactly one sendMessage left the process, carrying the bound
        # chat_id and the rendered result text — so this test can never
        # pass merely because nothing ran.
        sent = self.result_sends(harness)
        self.assertEqual(
            len(sent), 1,
            "the legacy lane must have fired exactly one sendMessage;"
            " send call log was %r" % (sent,),
        )
        self.assertEqual(sent[0]["chat_id"], expected_chat_id)
        self.assertEqual(sent[0]["text"], expected_text)
        self.assertIn(
            "legacy result", sent[0]["text"],
            "the sendMessage must carry the rendered verified result,"
            " proving the legacy render path actually executed",
        )
        # And NO edit was ever issued for the legacy record — asserted
        # on the real transport (edit) call log.
        self.assertEqual(
            self.transport_edits(harness, edits_before), [],
            "a legacy record must NEVER produce an editMessageText",
        )
        sends, edits = self.visible_objects(harness)
        self.assertEqual(len(sends), 1)
        self.assertEqual(edits, [])


class LegacyLaneCertificationTests(DeliveryCase):
    """R-17: certify BOTH delivery lanes independently, each on the
    TRANSPORT CALL LOG, and pin the at-most-once property of the legacy
    lane across repeated passes. The production R-15 selector guard in
    `deliver_pending_results` is exercised, never modified.
    """

    def test_bound_record_delivers_by_edit_only_and_never_sends(self):
        """Lane A (deliverable 4a + 5): a placeholder-BOUND workflow is
        delivered by editMessageText ONLY, emits ZERO sendMessage, and
        is NEVER eligible for the legacy lane — asserted on the raw
        transport call log, not merely on record state.
        """
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(harness, "the bound outcome")
        self.assertEqual(
            self.on_disk(harness)["result_placeholder"]["state"],
            wa_record.PLACEHOLDER_BOUND,
        )
        self.assertIsNone(self.delivery(harness))

        # Drive the legacy path DIRECTLY first: a bound record must not
        # be eligible, so it must send nothing and write no marker.
        harness.adapter.deliver_pending_results()
        self.assertEqual(
            self.result_sends(harness), [],
            "a placeholder-BOUND record must NEVER take the legacy"
            " sendMessage lane",
        )
        self.assertIsNone(
            self.delivery(harness),
            "the legacy lane must not write a marker onto a bound"
            " record",
        )

        # Now the correct lane delivers it: by EDIT.
        before = len(harness.api.transport.calls)
        harness.adapter.deliver_result_edits()
        edits = self.transport_edits(harness, before)
        self.assertEqual(len(edits), 1, "exactly one edit delivers it")
        self.assertEqual(edits[0]["message_id"], PLACEHOLDER_ID)
        self.assertEqual(
            self.result_sends(harness), [],
            "the edit lane must emit ZERO legacy sendMessage; send log"
            " was %r" % (self.result_sends(harness),),
        )
        marker = self.delivery(harness)
        self.assertEqual(
            marker["state"], wa_record.DELIVERY_DELIVERED_BY_EDIT
        )
        self.assertEqual(marker["edited_message_id"], PLACEHOLDER_ID)

    def test_historical_legacy_is_at_most_once_across_repeated_passes(
        self
    ):
        """Lane B (deliverable 4b): a genuinely pre-placeholder record
        is delivered by the legacy sendMessage lane AT MOST ONCE.

        Prove the property, not just one send: run repeated
        deliver_result_edits()/deliver_pending_results() passes and
        assert the sendMessage count stays EXACTLY 1 and the DELIVERED
        marker is never rewritten.
        """
        harness = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.legacy_completed(harness, "the historical outcome")
        entry = self.on_disk(harness)
        expected_text = adapter_module.render_result_text(entry)
        expected_chat_id = entry["telegram"]["chat_id"]

        edits_before = len(harness.api.transport.calls)
        # Five full passes in the run-loop order.
        for _ in range(5):
            harness.adapter.deliver_result_edits()
            harness.adapter.deliver_pending_results()

        sends = self.result_sends(harness)
        self.assertEqual(
            len(sends), 1,
            "the legacy lane must send AT MOST ONCE across repeated"
            " passes; send call log showed %d sends" % len(sends),
        )
        self.assertEqual(sends[0]["chat_id"], expected_chat_id)
        self.assertEqual(sends[0]["text"], expected_text)
        self.assertEqual(
            self.transport_edits(harness, edits_before), [],
            "a legacy record must NEVER be edited",
        )
        marker = self.delivery(harness)
        self.assertEqual(marker["state"], wa_record.DELIVERY_DELIVERED)
        first_marker = dict(marker)
        # One more pair of passes: still no new send, marker unchanged.
        harness.adapter.deliver_result_edits()
        harness.adapter.deliver_pending_results()
        self.assertEqual(
            len(self.result_sends(harness)), 1,
            "a settled legacy delivery is never re-sent",
        )
        self.assertEqual(
            dict(self.delivery(harness)), first_marker,
            "the DELIVERED marker must not be rewritten",
        )


    def test_each_legacy_selector_conjunct_is_load_bearing(self):
        """R-17 deliverable 6 anchor: EACH of the four conjuncts of the
        legacy selector in `deliver_pending_results`
        (`phase == COMPLETED`, `verified_result is not None`,
        `result_delivery is None`, `result_placeholder is None`) is
        independently NECESSARY.

        Method: start from a record that satisfies the OTHER three
        conjuncts and violates exactly ONE, and assert the legacy lane
        does NOT fire — no legacy send, no marker. A source mutant that
        drops or weakens any single conjunct makes one of these sub-cases
        fire a send and is killed HERE. The baseline proves the lane
        genuinely fires when all four hold, so no sub-case is vacuous.
        """
        # BASELINE (anti-vacuity): all four conjuncts hold -> it sends.
        base = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.legacy_completed(base, "eligible")
        base.adapter.deliver_pending_results()
        self.assertEqual(
            len(self.result_sends(base)), 1,
            "an eligible legacy record MUST send, or every sub-case"
            " below is vacuous",
        )
        self.assertEqual(
            self.delivery(base)["state"], wa_record.DELIVERY_DELIVERED
        )

        # CONJUNCT 1 — phase == COMPLETED. A legacy record stopped at
        # VERIFIED (verified_result set, placeholder None, delivery
        # None) must NOT be delivered.
        self.setUp()
        h1 = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.arm_legacy(h1)
        with wa_store.exclusive_store_lock(h1.tmpdir):
            workflows = h1.workflow_store.load()
            entry = workflows["workflows"]["wf-0001"]
            for phase in (wa_record.PHASE_WORKSPACE_READY,
                          wa_record.PHASE_PREPARED,
                          wa_record.PHASE_VALIDATED,
                          wa_record.PHASE_DISPATCHED,
                          wa_record.PHASE_VERIFIED):
                wa_record.apply_transition(entry, phase)
            entry["verified_result"] = {
                "summary": "early",
                "digest": wa_digest.text_digest("early"),
                "recorded_at": NOW,
            }
            h1.workflow_store.save(workflows)
        self.assertEqual(
            self.on_disk(h1)["phase"], wa_record.PHASE_VERIFIED
        )
        self.assertIsNone(self.on_disk(h1)["result_placeholder"])
        h1.adapter.deliver_pending_results()
        self.assertEqual(
            self.result_sends(h1), [],
            "a non-COMPLETED legacy record must NOT be delivered:"
            " the `phase == COMPLETED` conjunct is load-bearing",
        )
        self.assertIsNone(self.delivery(h1))

        # CONJUNCT 2 — verified_result is not None. A COMPLETED legacy
        # record with NO verified result must NOT be delivered (and the
        # render path, which dereferences verified_result.summary, must
        # never be reached for it).
        self.setUp()
        h2 = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.arm_legacy(h2)
        with wa_store.exclusive_store_lock(h2.tmpdir):
            workflows = h2.workflow_store.load()
            entry = workflows["workflows"]["wf-0001"]
            for phase in (wa_record.PHASE_WORKSPACE_READY,
                          wa_record.PHASE_PREPARED,
                          wa_record.PHASE_VALIDATED,
                          wa_record.PHASE_DISPATCHED,
                          wa_record.PHASE_VERIFIED,
                          wa_record.PHASE_COMPLETED):
                wa_record.apply_transition(entry, phase)
            entry["verified_result"] = None
            h2.workflow_store.save(workflows)
        self.assertEqual(
            self.on_disk(h2)["phase"], wa_record.PHASE_COMPLETED
        )
        self.assertIsNone(self.on_disk(h2)["verified_result"])
        # AUTHORED kill (repo rule: a crash is not a kill). Deleting the
        # `verified_result is not None` conjunct selects this record and
        # the renderer dereferences verified_result["summary"], raising
        # TypeError. Catch it and FAIL by an authored assertion naming
        # the invariant, rather than letting the mutant die by an
        # uncaught crash.
        try:
            h2.adapter.deliver_pending_results()
        except Exception as exc:
            self.fail(
                "a COMPLETED legacy record with NO verified_result must"
                " NOT be selected for delivery; deleting the"
                " `verified_result is not None` conjunct let it reach"
                " the renderer and raised %r" % (exc,)
            )
        self.assertEqual(
            self.result_sends(h2), [],
            "a COMPLETED legacy record with no verified_result must NOT"
            " be delivered: the `verified_result is not None` conjunct"
            " is load-bearing",
        )
        self.assertIsNone(self.delivery(h2))

        # CONJUNCT 3 — result_delivery is None. A legacy record that is
        # ALREADY delivered must NOT be re-sent.
        self.setUp()
        h3 = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.legacy_completed(h3, "already")
        with wa_store.exclusive_store_lock(h3.tmpdir):
            workflows = h3.workflow_store.load()
            workflows["workflows"]["wf-0001"]["result_delivery"] = {
                "state": wa_record.DELIVERY_DELIVERED,
                "reserved_at": NOW,
                "telegram_message_id": 7,
            }
            h3.workflow_store.save(workflows)
        h3.adapter.deliver_pending_results()
        self.assertEqual(
            self.result_sends(h3), [],
            "an already-delivered legacy record must NOT be re-sent:"
            " the `result_delivery is None` conjunct is load-bearing",
        )
        self.assertEqual(
            self.delivery(h3)["telegram_message_id"], 7,
            "the existing marker must be left intact",
        )

        # CONJUNCT 4 — result_placeholder is None. A placeholder-BOUND
        # record must NOT take the legacy lane (this is the R-15 guard).
        self.setUp()
        h4 = self.harness_with_edits([
            api_ok({"message_id": PLACEHOLDER_ID})
        ])
        self.bound_and_completed(h4, "bound")
        self.assertEqual(
            self.on_disk(h4)["result_placeholder"]["state"],
            wa_record.PLACEHOLDER_BOUND,
        )
        h4.adapter.deliver_pending_results()
        self.assertEqual(
            self.result_sends(h4), [],
            "a placeholder-BOUND record must NOT take the legacy lane:"
            " the `result_placeholder is None` conjunct is the R-15"
            " guard and is load-bearing",
        )
        self.assertIsNone(self.delivery(h4))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
