"""DI-REMOTE-3 increment I2: transport.

Scope of THIS file, stated so it is not read as more than it is: the
single-attempt placeholder send, the three-valued R-5/R-7 classifier,
the structured ok=false description exposure, the rigorous R-2/R-3
description matchers, and the bounded editMessageText. Nothing here
creates, binds, gates or delivers anything — the adapter lifecycle
(I3), the Broker gate (I4) and the edit-based delivery engine (I5) are
later increments.

Two clauses of plan §3.4 are NOT in this increment and are not
claimed: clause 1 (the edit targeted the bound chat_id AND message_id)
and clause 2 (the rendered digest matches the current verified
result). Both need the durable record, which the transport layer does
not and must not read. I2 delivers clauses 3 and 4 — rigorous exact
matching of the description, and only under genuine structured proof.

New file, new tests only: no pre-existing test is modified, weakened,
skipped or renamed (lead plan §6.1).

Test ids map to the lead plan §5 matrix and are named in each
docstring: T-C1..T-C8, T-N1..T-N4.
"""

import io
import json
import socket
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram_operator import telegram_api

TOKEN = "12345:SECRET-TOKEN-VALUE"


def api_ok(result):
    return 200, json.dumps({"ok": True, "result": result}).encode("utf-8")


def api_refusal(description, error_code=400):
    return json.dumps({
        "ok": False, "error_code": error_code, "description": description,
    }).encode("utf-8")


def http_error(status, body=None, url="https://api.telegram.org/botX/m"):
    """An HTTPError that CAN carry a readable body.

    The existing `FakeTransport` can already RAISE (it raises any
    BaseException in its script), but every existing test builds
    `HTTPError(..., fp=None)`, which carries no body at all — and
    `.read()` on it raises `KeyError` out of `tempfile`, not an
    OSError. The real `default_transport` raises HTTPError WITH a body
    on every non-2xx, so the fakes could not express the one shape the
    real dependency actually produces. This helper closes that gap;
    `fp=None` remains reachable by passing body=None, because that is
    the "body cannot be read at all" case R-7 must also handle.
    """
    fp = io.BytesIO(body) if body is not None else None
    return urllib.error.HTTPError(url, status, "err", {}, fp)


class WeirdBodyHTTPError(urllib.error.HTTPError):
    """A RAISING HTTPError whose body read misbehaves.

    R-9.4: failure to read or parse the body degrades to "no Telegram
    body" and therefore to INDEFINITE, never definite_zero — an
    unparsed body is exactly absence of evidence. These shapes drive
    that degradation; none of them may propagate.
    """

    def __init__(self, status, reader):
        urllib.error.HTTPError.__init__(
            self, "https://api.telegram.org/botX/m", status, "err", {},
            io.BytesIO(b""),
        )
        self._reader = reader

    def read(self, *args, **kwargs):
        return self._reader()


class DeadlineHTTPError(urllib.error.HTTPError):
    """An HTTPError whose ``reason`` is the client socket deadline.

    ``HTTPError`` subclasses ``URLError``, so a fired deadline can
    arrive on that branch too. ``reason`` is a read-only property on
    ``HTTPError``, hence the subclass rather than an attribute poke.
    """

    @property
    def reason(self):
        return socket.timeout("deadline")


class ScriptedTransport(object):
    """Records every call; replays a script of returns and raises.

    Deliberately a superset of the existing FakeTransport's
    expressiveness: a script entry may be a (status, body) pair to
    RETURN, or an exception to RAISE — including an HTTPError carrying
    a real body, which is what the production transport does.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, payload_bytes, deadline_seconds):
        self.calls.append({
            "url": url,
            "payload": json.loads(payload_bytes.decode("utf-8")),
            "deadline": deadline_seconds,
        })
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, BaseException):
            raise step
        return step


class RecordingSleeper(object):
    def __init__(self):
        self.sleeps = []

    def __call__(self, seconds):
        self.sleeps.append(seconds)


def make_api(script):
    transport = ScriptedTransport(script)
    sleeper = RecordingSleeper()
    api = telegram_api.TelegramApi(
        TOKEN, transport=transport, sleeper=sleeper
    )
    return api, transport, sleeper


NOT_MODIFIED = "Bad Request: message is not modified"
NOT_MODIFIED_LONG = (
    "Bad Request: message is not modified: specified new message"
    " content and reply markup are exactly the same as a current"
    " content and reply markup of the message"
)
NOT_FOUND = "Bad Request: message to edit not found"


# --- The §3.2 table, transcribed INDEPENDENTLY from the plan ----------
#
# Authored from the plan text, deliberately NOT derived from
# telegram_api's own constants or from classify_send_once itself: a
# table generated from the code under test is self-consistent under a
# mutant of that code and would stay green through a flipped row.
APPLIED = "applied"
DEFINITE_ZERO = "definite_zero"
INDEFINITE = "indefinite"


# Independently authored from the plan and this increment's brief —
# deliberately NOT derived from telegram_api's own tuples, so a value
# or membership change cannot stay self-consistent.
EXPECTED_CALL_OUTCOMES = (
    "ok", "telegram_refused", "no_telegram_body", "deadline",
    "os_error", "undecodable_body", "response_over_bound",
    "request_not_sent",
)
EXPECTED_SEND_CLASSIFICATIONS = ("applied", "definite_zero", "indefinite")


class VocabularyPinTests(unittest.TestCase):

    def test_call_outcome_vocabulary_is_exactly_as_specified(self):
        self.assertEqual(
            tuple(telegram_api.CALL_OUTCOMES), EXPECTED_CALL_OUTCOMES
        )
        self.assertEqual(
            tuple(telegram_api.SEND_CLASSIFICATIONS),
            EXPECTED_SEND_CLASSIFICATIONS,
        )
        # The named constants carry exactly those values, so a renamed
        # value cannot hide behind tests that compare constant to
        # constant.
        for name, value in (
            ("CALL_OK", "ok"),
            ("CALL_TELEGRAM_REFUSED", "telegram_refused"),
            ("CALL_NO_TELEGRAM_BODY", "no_telegram_body"),
            ("CALL_DEADLINE", "deadline"),
            ("CALL_OS_ERROR", "os_error"),
            ("CALL_UNDECODABLE_BODY", "undecodable_body"),
            ("CALL_RESPONSE_OVER_BOUND", "response_over_bound"),
            ("CALL_REQUEST_NOT_SENT", "request_not_sent"),
            ("SEND_APPLIED", "applied"),
            ("SEND_DEFINITE_ZERO", "definite_zero"),
            ("SEND_INDEFINITE", "indefinite"),
        ):
            self.assertEqual(
                getattr(telegram_api, name), value, name
            )
        # Every outcome the classifier can be handed maps to one of the
        # three classes — no outcome falls through to None.
        for outcome in EXPECTED_CALL_OUTCOMES:
            self.assertIn(
                telegram_api.classify_send_once(
                    telegram_api.CallDetail(outcome=outcome), None
                ),
                EXPECTED_SEND_CLASSIFICATIONS, outcome,
            )

    def test_R9_every_new_non_2xx_fixture_RAISES_http_error(self):
        """R-9.2, enforced mechanically on THIS module.

        A fake that RETURNS (status, body) for a non-2xx does not
        exercise the production path — `urlopen` raises `HTTPError`
        there — and so is not evidence for R-2, R-3 or R-7. This scans
        every script literal handed to `make_api` in this file and
        fails if any non-2xx status is expressed as a returned tuple.
        It is a self-audit pin, so the bar stays true as this file
        grows rather than only at the moment it was written.
        """
        import ast
        with io.open(__file__.replace(".pyc", ".py"),
                     encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        offenders = []
        scanned = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None)
            if name != "make_api" or not node.args:
                continue
            argument = node.args[0]
            if not isinstance(argument, (ast.List, ast.Tuple)):
                continue
            for element in argument.elts:
                scanned += 1
                if not isinstance(element, ast.Tuple) or not element.elts:
                    continue
                first = element.elts[0]
                if (isinstance(first, ast.Constant)
                        and isinstance(first.value, int)
                        and not isinstance(first.value, bool)
                        and not 200 <= first.value < 300):
                    offenders.append((first.value, element.lineno))
        # Anti-vacuity: the scan actually found script entries. A scan
        # that matched nothing would pass this vacuously.
        self.assertGreater(
            scanned, 10,
            "the R-9.2 scan found almost no make_api script entries;"
            " it is not looking where it thinks it is",
        )
        self.assertEqual(
            offenders, [],
            "R-9.2: non-2xx fixtures must RAISE HTTPError (use"
            " http_error(...)), not return (status, body); offenders"
            " are (status, line): %r" % (offenders,),
        )


class ClassificationTableTests(unittest.TestCase):

    def send_once(self, script, text="placeholder wf-0001"):
        api, transport, sleeper = make_api(script)
        outcome = api.send_message_once(4242, text)
        return outcome, transport, sleeper

    def test_C1_placeholder_send_classification_table(self):
        """T-C1: EVERY row of plan §3.2, driven through the real
        transport seam, asserting the class AND that exactly ONE
        sendMessage left this process."""
        OK_ = telegram_api.CALL_OK
        REFUSED = telegram_api.CALL_TELEGRAM_REFUSED
        NO_BODY = telegram_api.CALL_NO_TELEGRAM_BODY
        DEADLINE = telegram_api.CALL_DEADLINE
        OS_ERR = telegram_api.CALL_OS_ERROR
        UNDECODABLE = telegram_api.CALL_UNDECODABLE_BODY
        OVER_BOUND = telegram_api.CALL_RESPONSE_OVER_BOUND
        NOT_SENT = telegram_api.CALL_REQUEST_NOT_SENT
        rows = (
            # (label, script step, expected class, expected outcome)
            ("ok=true with a usable int message_id",
             api_ok({"message_id": 77}), APPLIED, OK_),
            ("ok=true with no message_id",
             api_ok({}), INDEFINITE, OK_),
            ("ok=true with a bool message_id (bool is not an int here)",
             api_ok({"message_id": True}), INDEFINITE, OK_),
            ("ok=true with a non-dict result",
             api_ok("nope"), INDEFINITE, OK_),

            # R-7: 4xx WITHOUT a parseable Telegram body proves nothing.
            ("HTTP 400, body unreadable (fp=None)",
             http_error(400, None), INDEFINITE, NO_BODY),
            ("HTTP 400, body is not JSON",
             http_error(400, b"<html>502 from a proxy</html>"),
             INDEFINITE, NO_BODY),
            ("HTTP 400, JSON but not a Telegram document",
             http_error(400, b'{"error": "gateway refused"}'),
             INDEFINITE, NO_BODY),
            ("HTTP 400, JSON object without an 'ok' key",
             http_error(400, b'{"description": "looks telegram-ish"}'),
             INDEFINITE, NO_BODY),
            ("HTTP 400, JSON array",
             http_error(400, b'[{"ok": false}]'), INDEFINITE, NO_BODY),
            ("HTTP 403, body unreadable",
             http_error(403, None), INDEFINITE, NO_BODY),

            # Genuine parsed Telegram refusal: positive proof of no
            # effect, and the ONLY route to definite_zero from a
            # response.
            ("HTTP 400 with a genuine Telegram ok=false body",
             http_error(400, api_refusal("Bad Request: chat not found")),
             DEFINITE_ZERO, REFUSED),
            ("HTTP 200 with a genuine Telegram ok=false body",
             (200, api_refusal("Bad Request: chat not found")),
             DEFINITE_ZERO, REFUSED),

            # 429 / 5xx stay INDEFINITE even with a parsed body: a
            # rate-limit or a server-side error cannot prove the
            # message was not created first.
            ("HTTP 429 with a genuine Telegram ok=false body",
             http_error(429, api_refusal("Too Many Requests", 429)),
             INDEFINITE, REFUSED),
            ("HTTP 500 with a genuine Telegram ok=false body",
             http_error(500, api_refusal("Internal Server Error", 500)),
             INDEFINITE, REFUSED),
            ("HTTP 502, body unreadable",
             http_error(502, None), INDEFINITE, NO_BODY),

            # Deliberate INVERSIONS of _call's existing retryable flag.
            ("client deadline fired",
             socket.timeout("deadline"), INDEFINITE, DEADLINE),
            ("connection reset after the request was written",
             ConnectionResetError("reset by peer"), INDEFINITE, OS_ERR),
            ("generic OSError",
             OSError("broken pipe"), INDEFINITE, OS_ERR),
            ("undecodable JSON body on a 200",
             (200, b"\xff\xfe not json"), INDEFINITE, UNDECODABLE),
            ("valid JSON that is not an object, on a 200",
             (200, b'"a string"'), INDEFINITE, NO_BODY),
            ("response over MAX_RESPONSE_BYTES",
             (200, b"x" * (telegram_api.MAX_RESPONSE_BYTES + 1)),
             INDEFINITE, OVER_BOUND),

            # Malformed request: raised before any byte was written.
            ("unexpected non-OSError exception (malformed request)",
             ValueError("http.client.InvalidURL-alike"),
             DEFINITE_ZERO, NOT_SENT),
        )
        seen = set()
        seen_outcomes = set()
        for label, step, expected, expected_outcome in rows:
            outcome, transport, sleeper = self.send_once([step])
            self.assertEqual(
                outcome.classification, expected, label
            )
            # The STRUCTURAL outcome is asserted too, not only the
            # headline class: several distinct structural conditions
            # share a class, so a mislabelled outcome would otherwise
            # be invisible here.
            self.assertEqual(
                outcome.detail.outcome, expected_outcome, label
            )
            # EXACTLY ONE sendMessage per call, on every row.
            self.assertEqual(len(transport.calls), 1, label)
            # The EXACT payload, not a subset: nothing may be added to
            # a placeholder send.
            self.assertEqual(
                transport.calls[0]["payload"],
                {"chat_id": 4242, "text": "placeholder wf-0001"}, label,
            )
            # This entry never sleeps: it never retries.
            self.assertEqual(sleeper.sleeps, [], label)
            seen.add(expected)
            seen_outcomes.add(expected_outcome)
        # Every structural outcome in the closed vocabulary is exercised
        # by this table — a new outcome added without a row fails here.
        self.assertEqual(
            seen_outcomes, set(telegram_api.CALL_OUTCOMES)
        )
        # ANTI-VACUITY: the table is not frozen to one class. A
        # classifier that returned 'indefinite' unconditionally would
        # satisfy most rows above; it cannot satisfy this.
        self.assertEqual(seen, {APPLIED, DEFINITE_ZERO, INDEFINITE})
        # ... and the classification vocabulary is exactly three-valued.
        self.assertEqual(
            set(telegram_api.SEND_CLASSIFICATIONS),
            {APPLIED, DEFINITE_ZERO, INDEFINITE},
        )

    def test_C1_applied_row_returns_the_bound_message_id(self):
        """T-C1 (b): 'applied' is the only class that yields a
        message_id, and it is the real one."""
        outcome, _, _ = self.send_once([api_ok({"message_id": 77})])
        self.assertEqual(outcome.classification, APPLIED)
        self.assertEqual(outcome.message_id, 77)
        self.assertIsNone(outcome.problem)
        for step in (api_ok({}), http_error(400, None),
                     socket.timeout("d")):
            outcome, _, _ = self.send_once([step])
            self.assertIsNone(outcome.message_id, repr(step))
            self.assertIsNotNone(
                outcome.problem,
                "a non-applied outcome must carry a truthful problem",
            )

    def test_C2_deadline_on_placeholder_send_is_indefinite_not_retried(
        self
    ):
        """T-C2: a fired deadline is INDEFINITE and makes EXACTLY ONE
        transport call.

        The contrast is the point: the same script through the
        retrying seam sends more than once. A placeholder send routed
        through `_send_with_retry` would be a duplicate-placeholder
        generator, so this asserts the count, not just the class.
        """
        script = [socket.timeout("deadline"), api_ok({"message_id": 5})]
        api, transport, sleeper = make_api(script)
        outcome = api.send_message_once(42, "placeholder")
        self.assertEqual(outcome.classification, INDEFINITE)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(sleeper.sleeps, [])
        self.assertIsNotNone(outcome.problem)
        self.assertIn("INDEFINITE", outcome.problem)

        # The SAME script through the existing retrying seam recovers
        # on attempt 2 — proving the difference is the seam, not the
        # script, and that send_message's behaviour is unchanged.
        api2, transport2, _ = make_api(
            [socket.timeout("deadline"), api_ok({"message_id": 5})]
        )
        self.assertTrue(api2.send_message(42, "hello").ok)
        self.assertGreater(len(transport2.calls), 1)

    def test_C3_indefinite_send_never_accumulates_transport_calls(self):
        """T-C3: repeated invocations each make exactly one call — the
        entry itself never loops, so an indefinite outcome can never
        become two placeholders through this seam.

        The DURABLE terminality of `indefinite` (never auto-retried
        across restarts) is I3's, and is not claimed here.
        """
        for step in (socket.timeout("d"), ConnectionResetError("r"),
                     http_error(400, None), http_error(502, None)):
            api, transport, sleeper = make_api([step])
            for _ in range(4):
                outcome = api.send_message_once(42, "placeholder")
                self.assertEqual(
                    outcome.classification, INDEFINITE, repr(step)
                )
            # Four invocations, four calls: one each, never a retry.
            self.assertEqual(len(transport.calls), 4, repr(step))
            self.assertEqual(sleeper.sleeps, [], repr(step))

    def test_C4_definite_zero_is_reachable_and_is_safe_to_retry(self):
        """T-C4: the SAFE direction is not over-frozen.

        `definite_zero` must remain reachable — a classifier that
        answered `indefinite` to everything would be trivially safe and
        useless — and a caller may retry it without duplicating,
        because nothing was created.
        """
        api, transport, _ = make_api([
            http_error(400, api_refusal("Bad Request: chat not found")),
            api_ok({"message_id": 31}),
        ])
        first = api.send_message_once(42, "placeholder")
        self.assertEqual(first.classification, DEFINITE_ZERO)
        self.assertIsNone(first.message_id)
        second = api.send_message_once(42, "placeholder")
        self.assertEqual(second.classification, APPLIED)
        self.assertEqual(second.message_id, 31)
        self.assertEqual(len(transport.calls), 2)

    def test_C6_4xx_without_a_parseable_telegram_body_is_indefinite(self):
        """T-C6 (R-7, required evidence): a 4xx WITHOUT a parseable
        Telegram body is INDEFINITE and is NOT retried.

        An intermediary proxy, gateway or load balancer can emit one,
        and it cannot prove Telegram never created the message. A
        status-only classifier is the exact defect R-7 forbids, so
        every body shape a proxy realistically emits is driven here.
        """
        proxy_bodies = (
            None,
            b"",
            b"<html><body>400 Bad Request</body></html>",
            b"Bad Request",
            b'{"error": "gateway refused", "code": 400}',
            b'{"ok": true}',
            b'{"description": "message is not modified"}',
            b'[{"ok": false, "description": "message is not modified"}]',
            b"\xff\xfe\x00 binary",
        )
        for status in (400, 401, 403, 404, 409):
            for body in proxy_bodies:
                api, transport, sleeper = make_api([
                    http_error(status, body)
                ])
                outcome = api.send_message_once(42, "placeholder")
                label = "HTTP %d body=%r" % (status, body)
                self.assertEqual(
                    outcome.classification, INDEFINITE, label
                )
                # MUST NOT retry: exactly one transport call.
                self.assertEqual(len(transport.calls), 1, label)
                self.assertEqual(sleeper.sleeps, [], label)
                # The detail records the absence of proof explicitly.
                self.assertEqual(
                    outcome.detail.outcome,
                    telegram_api.CALL_NO_TELEGRAM_BODY, label,
                )
                self.assertFalse(outcome.detail.body_parsed, label)

    def test_C7_a_parsed_telegram_body_still_reaches_definite_zero(self):
        """T-C7 (anti-vacuity twin of T-C6): the SAME 4xx statuses,
        with a genuine parsed Telegram ok=false body, DO reach
        `definite_zero`.

        Without this, T-C6 would be satisfied by a classifier frozen to
        `indefinite`, and R-7 would be 'proven' by a function that
        answers one value.
        """
        for status in (400, 401, 403, 404, 409):
            api, transport, _ = make_api([
                http_error(
                    status,
                    api_refusal("Bad Request: chat not found", status),
                )
            ])
            outcome = api.send_message_once(42, "placeholder")
            label = "HTTP %d with a Telegram body" % status
            self.assertEqual(
                outcome.classification, DEFINITE_ZERO, label
            )
            self.assertEqual(len(transport.calls), 1, label)
            self.assertEqual(
                outcome.detail.outcome,
                telegram_api.CALL_TELEGRAM_REFUSED, label,
            )
            self.assertTrue(outcome.detail.body_parsed, label)
            self.assertIs(outcome.detail.telegram_ok, False, label)
            self.assertEqual(
                outcome.detail.description,
                "Bad Request: chat not found", label,
            )
            self.assertEqual(outcome.detail.error_code, status, label)

    def test_send_once_never_chunks(self):
        """The placeholder path never creates more than one object: an
        over-long text is REFUSED before the transport is touched."""
        long_text = "x" * (telegram_api.MAX_MESSAGE_CHARS + 1)
        api, transport, _ = make_api([api_ok({"message_id": 1})])
        outcome = api.send_message_once(42, long_text)
        self.assertEqual(outcome.classification, DEFINITE_ZERO)
        self.assertEqual(
            transport.calls, [],
            "an over-long placeholder must not reach the transport",
        )
        self.assertIn("never chunks", outcome.problem)
        # Exactly at the limit it DOES send, in ONE call — the refusal
        # is a real boundary, not a blanket block.
        api2, transport2, _ = make_api([api_ok({"message_id": 1})])
        exact = "y" * telegram_api.MAX_MESSAGE_CHARS
        self.assertEqual(
            api2.send_message_once(42, exact).classification, APPLIED
        )
        self.assertEqual(len(transport2.calls), 1)
        self.assertEqual(transport2.calls[0]["payload"]["text"], exact)


class BodyDegradationTests(unittest.TestCase):
    """R-9.4: a body that cannot be read or parsed degrades to "no
    Telegram body" and therefore to INDEFINITE — never definite_zero,
    and never by propagating an exception.

    Every assertion here is AUTHORED: the mutants these guard against
    (removing a try/except, dropping a type guard, letting `None` leak
    out of the parser) all fail by raising, and a mutant that dies by
    ERROR cascade is not a kill (R-8.3).
    """

    def degrade(self, error, label):
        api, transport, _ = make_api([error])
        try:
            outcome = api.send_message_once(42, "placeholder")
        except Exception as exc:
            self.fail(
                "%s must DEGRADE to 'no Telegram body', not propagate;"
                " raised %s: %s" % (label, type(exc).__name__, exc)
            )
        self.assertIsNotNone(
            outcome.detail,
            "%s must still carry a structured detail" % label,
        )
        self.assertEqual(
            outcome.detail.outcome,
            telegram_api.CALL_NO_TELEGRAM_BODY, label,
        )
        self.assertFalse(outcome.detail.body_parsed, label)
        self.assertIsNone(outcome.detail.description, label)
        # R-7 with full force: absence of evidence is INDEFINITE.
        self.assertEqual(outcome.classification, INDEFINITE, label)
        self.assertEqual(len(transport.calls), 1, label)
        return outcome

    def test_unreadable_body_degrades_and_never_propagates(self):
        # The shape the EXISTING tests use: HTTPError(fp=None), whose
        # .read() raises KeyError out of tempfile — not an OSError,
        # which is why the guard is a bare `except Exception`.
        self.degrade(http_error(400, None), "HTTPError(fp=None)")
        for reader, label in (
            (lambda: (_ for _ in ()).throw(RuntimeError("boom")),
             "read() raises RuntimeError"),
            (lambda: (_ for _ in ()).throw(OSError("socket gone")),
             "read() raises OSError"),
            (lambda: "a str, not bytes", "read() returns str"),
            (lambda: None, "read() returns None"),
            (lambda: bytearray(b'{"ok": false}'),
             "read() returns bytearray"),
        ):
            self.degrade(WeirdBodyHTTPError(400, reader), label)

    def test_oversize_error_body_is_not_parsed(self):
        # The SAME MAX_RESPONSE_BYTES bound as the success path: a
        # partial body is never parsed, so a huge body cannot become
        # 'proof' of a refusal.
        oversize = b'{"ok": false, "description": "x' + (
            b"y" * telegram_api.MAX_RESPONSE_BYTES
        ) + b'"}'
        self.assertGreater(
            len(oversize), telegram_api.MAX_RESPONSE_BYTES
        )
        self.degrade(
            WeirdBodyHTTPError(400, lambda: oversize),
            "body over MAX_RESPONSE_BYTES",
        )
        # Anti-vacuity: a body just under the bound DOES parse, so the
        # row above is the bound biting, not a blanket refusal.
        padding = telegram_api.MAX_RESPONSE_BYTES - 64
        big = json.dumps({
            "ok": False, "error_code": 400,
            "description": "z" * padding,
        }).encode("utf-8")
        self.assertLessEqual(len(big), telegram_api.MAX_RESPONSE_BYTES)
        api, _, _ = make_api([WeirdBodyHTTPError(400, lambda: big)])
        detail = api.send_message_once(42, "placeholder").detail
        self.assertEqual(
            detail.outcome, telegram_api.CALL_TELEGRAM_REFUSED
        )
        self.assertTrue(detail.body_parsed)

    def test_undecodable_and_non_telegram_error_bodies_degrade(self):
        for body, label in (
            (b"\xff\xfe\x00", "undecodable bytes"),
            (b"", "empty body"),
            (b"not json at all", "not JSON"),
            (b"[1, 2, 3]", "JSON array"),
            (b'"a string"', "JSON string"),
            (b"null", "JSON null"),
            (b"123", "JSON number"),
            (b'{"ok": true}', "ok=true on an error response"),
            (b'{"description": "no ok key"}', "no ok key"),
            (b'{"ok": "false"}', "ok is the STRING 'false'"),
            (b'{"ok": 0}', "ok is 0"),
        ):
            self.degrade(http_error(400, body), label)

    def test_a_refusal_without_a_description_reports_none(self):
        # Not the string "None": a fabricated description could be
        # matched against the R-2/R-3 forms.
        api, _, _ = make_api([
            http_error(400, b'{"ok": false, "error_code": 400}')
        ])
        detail = api.send_message_once(42, "placeholder").detail
        self.assertEqual(
            detail.outcome, telegram_api.CALL_TELEGRAM_REFUSED
        )
        self.assertIsNone(detail.description)
        self.assertFalse(telegram_api.is_message_not_modified(detail))
        # A non-string description is likewise not fabricated into one.
        for raw in (b'{"ok": false, "description": 5}',
                    b'{"ok": false, "description": null}',
                    b'{"ok": false, "description": ["a"]}'):
            api2, _, _ = make_api([http_error(400, raw)])
            detail2 = api2.send_message_once(42, "p").detail
            self.assertIsNone(detail2.description, raw)
            self.assertFalse(
                telegram_api.is_message_not_modified(detail2), raw
            )

    def test_error_code_excludes_bool_and_non_int(self):
        for raw, expected in (
            (b'{"ok": false, "error_code": 400}', 400),
            (b'{"ok": false, "error_code": true}', None),
            (b'{"ok": false, "error_code": false}', None),
            (b'{"ok": false, "error_code": "400"}', None),
            (b'{"ok": false, "error_code": 4.5}', None),
            (b'{"ok": false}', None),
        ):
            api, _, _ = make_api([http_error(400, raw)])
            detail = api.send_message_once(42, "p").detail
            self.assertEqual(detail.error_code, expected, raw)

    def test_normalize_description_is_total(self):
        # Called on a detail whose description is None or non-string;
        # must answer None rather than raising.
        for value in (None, 5, [], {}, b"bytes", 1.5, True):
            try:
                self.assertIsNone(
                    telegram_api.normalize_description(value), repr(value)
                )
            except Exception as exc:
                self.fail(
                    "normalize_description(%r) must be TOTAL; raised"
                    " %s: %s" % (value, type(exc).__name__, exc)
                )

    def test_http_error_that_is_a_deadline_is_recorded_as_deadline(self):
        # HTTPError subclasses URLError, so a deadline can arrive on
        # that branch too; it must be the deadline outcome, not a
        # transport error.
        error = DeadlineHTTPError(
            "https://api.telegram.org/botX/m", 504, "err", {}, None
        )
        self.assertTrue(
            telegram_api._is_deadline_error(error),
            "the fixture must really look like a deadline, or this"
            " test proves nothing",
        )
        api, transport, _ = make_api([error])
        outcome = api.send_message_once(42, "placeholder")
        self.assertEqual(
            outcome.detail.outcome, telegram_api.CALL_DEADLINE
        )
        self.assertEqual(outcome.classification, INDEFINITE)
        self.assertEqual(len(transport.calls), 1)


class EditTransportTests(unittest.TestCase):

    def test_C5_edit_retry_is_bounded_and_payload_is_byte_identical(self):
        """T-C5: editMessageText retries through the EXISTING bounded
        seam — safe because an edit against a pre-bound object with a
        byte-identical payload is idempotent (R-5) — and every attempt
        sends the SAME bytes."""
        api, transport, sleeper = make_api([http_error(502, None)])
        outcome = api.edit_message_text(42, 99, "the result")
        self.assertFalse(outcome.ok)
        # BOUNDED: exactly MAX_SEND_ATTEMPTS, with capped backoff.
        self.assertEqual(
            len(transport.calls), telegram_api.MAX_SEND_ATTEMPTS
        )
        self.assertEqual(
            len(sleeper.sleeps), telegram_api.MAX_SEND_ATTEMPTS - 1
        )
        for pause in sleeper.sleeps:
            self.assertLessEqual(
                pause, telegram_api.RETRY_BACKOFF_CEILING_SECONDS
            )
        # IDEMPOTENT: every attempt is byte-identical, asserted on the
        # exact payload dicts rather than on a subset.
        payloads = [call["payload"] for call in transport.calls]
        for payload in payloads:
            self.assertEqual(payload, payloads[0])
        # R-1 by construction: no parse_mode, no reply_markup, ever.
        self.assertEqual(
            payloads[0],
            {"chat_id": 42, "message_id": 99, "text": "the result"},
        )

    def test_C5_edit_recovers_within_the_bound(self):
        """T-C5 (b): anti-vacuity — the retry is real, not a no-op."""
        api, transport, _ = make_api([
            http_error(502, None), api_ok({"message_id": 99}),
        ])
        outcome = api.edit_message_text(42, 99, "the result")
        self.assertTrue(outcome.ok)
        self.assertIsNone(outcome.problem)
        self.assertEqual(len(transport.calls), 2)
        # The structured detail survives a SUCCESSFUL call too — I5
        # reads it to apply R-2, so losing it on success would make the
        # not-modified proof unavailable exactly when it is needed.
        self.assertIsNotNone(outcome.detail)
        self.assertEqual(outcome.detail.outcome, telegram_api.CALL_OK)
        self.assertTrue(outcome.detail.body_parsed)
        self.assertIs(outcome.detail.telegram_ok, True)

    def test_edit_never_chunks_and_refuses_over_long_text(self):
        api, transport, _ = make_api([api_ok({})])
        long_text = "x" * (telegram_api.MAX_MESSAGE_CHARS + 1)
        outcome = api.edit_message_text(42, 99, long_text)
        self.assertFalse(outcome.ok)
        self.assertEqual(transport.calls, [])
        self.assertIn("never chunks and never truncates", outcome.problem)

    def test_edit_surfaces_the_structured_description(self):
        """The edit path carries the structured detail R-2/R-3 need —
        the folded problem string alone cannot satisfy either."""
        api, _, _ = make_api([http_error(400, api_refusal(NOT_MODIFIED))])
        outcome = api.edit_message_text(42, 99, "same text")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.detail.description, NOT_MODIFIED)
        self.assertTrue(telegram_api.is_message_not_modified(outcome.detail))


class DescriptionMatchingTests(unittest.TestCase):
    """T-N1..T-N4. I2 delivers plan §3.4 clauses 3 and 4 only —
    rigorous matching, under structured proof. Clauses 1 and 2 (bound
    chat/message identity and the rendered-digest binding) need the
    durable record and belong to I5."""

    def refusal_detail(self, description, status=400):
        api, _, _ = make_api([http_error(status, api_refusal(description))])
        return api.send_message_once(42, "x").detail

    def test_N1_not_modified_under_structured_proof_is_recognized(self):
        """T-N1: both documented Telegram forms are recognized, and
        only from a genuine parsed ok=false body."""
        for description in (NOT_MODIFIED, NOT_MODIFIED_LONG):
            detail = self.refusal_detail(description)
            self.assertTrue(
                telegram_api.is_message_not_modified(detail), description
            )
        # R-3's twin, matched with the same discipline.
        self.assertTrue(
            telegram_api.is_message_to_edit_not_found(
                self.refusal_detail(NOT_FOUND)
            )
        )
        # ... and the two conditions never answer for each other.
        self.assertFalse(
            telegram_api.is_message_to_edit_not_found(
                self.refusal_detail(NOT_MODIFIED)
            )
        )
        self.assertFalse(
            telegram_api.is_message_not_modified(
                self.refusal_detail(NOT_FOUND)
            )
        )

    def test_N2_the_same_phrase_without_structured_proof_is_not_success(
        self
    ):
        """T-N2 (plan §3.4 clause 4): the phrase is honoured ONLY from
        a genuine parsed Telegram ok=false document. A body that merely
        CONTAINS the words, from a source that never proved Telegram
        refused, is not success."""
        # A proxy body carrying the exact phrase, unparsed as Telegram.
        api, _, _ = make_api([
            http_error(400, b'{"description": "message is not modified"}')
        ])
        detail = api.send_message_once(42, "x").detail
        self.assertEqual(
            detail.outcome, telegram_api.CALL_NO_TELEGRAM_BODY
        )
        self.assertFalse(telegram_api.is_message_not_modified(detail))
        # A hand-built detail that asserts the phrase without proof.
        forged = telegram_api.CallDetail(
            outcome=telegram_api.CALL_NO_TELEGRAM_BODY,
            http_status=400, body_parsed=False,
            description=NOT_MODIFIED,
        )
        self.assertFalse(telegram_api.is_message_not_modified(forged))
        # ok=true can never be a refusal, whatever it says.
        self.assertFalse(telegram_api.is_message_not_modified(
            telegram_api.CallDetail(
                outcome=telegram_api.CALL_TELEGRAM_REFUSED,
                body_parsed=True, telegram_ok=True,
                description=NOT_MODIFIED,
            )
        ))

    def test_N3_non_refusal_outcomes_are_never_success(self):
        """T-N3: every other structural outcome answers False, even
        carrying the exact description."""
        for outcome in telegram_api.CALL_OUTCOMES:
            if outcome == telegram_api.CALL_TELEGRAM_REFUSED:
                continue
            detail = telegram_api.CallDetail(
                outcome=outcome, body_parsed=True, telegram_ok=False,
                description=NOT_MODIFIED,
            )
            self.assertFalse(
                telegram_api.is_message_not_modified(detail), outcome
            )
        # A missing/None detail must not crash and must not be success.
        self.assertFalse(telegram_api.is_message_not_modified(None))
        self.assertFalse(telegram_api.is_message_to_edit_not_found(None))

    def test_N4_other_descriptions_including_a_superstring_are_never_success(
        self
    ):
        """T-N4: a table over real Telegram descriptions, near-misses,
        and a SUPERSTRING of the real phrase.

        The superstring is the row that matters: a loose `in` match
        would accept it, and accepting it would record a result as
        delivered on the strength of a different message entirely.
        """
        rejected = (
            # A SUPERSTRING of the real phrase — the loose-match killer.
            "Bad Request: message is not modified by another party",
            "Bad Request: message is not modified yet",
            "message is not modified extra words here",
            "prefix Bad Request: message is not modified",
            # Near-misses.
            "Bad Request: message is not modifie",
            "Bad Request: message was not modified",
            "Bad Request: messages are not modified",
            "Bad Request: message is modified",
            # Real, different Telegram refusals.
            "Bad Request: chat not found",
            "Bad Request: message can't be edited",
            "Bad Request: MESSAGE_ID_INVALID",
            "Forbidden: bot was blocked by the user",
            "Too Many Requests: retry after 5",
            "Bad Request: message text is empty",
            "Bad Request: message to delete not found",
            "",
        )
        for description in rejected:
            detail = self.refusal_detail(description)
            self.assertFalse(
                telegram_api.is_message_not_modified(detail),
                "must NOT match: %r" % description,
            )
            self.assertFalse(
                telegram_api.is_message_to_edit_not_found(detail),
                "must NOT match: %r" % description,
            )
        # ANTI-VACUITY: the matcher is not simply always False. Real
        # whitespace/case variants of the true phrase DO match, so the
        # rejections above are discrimination, not a constant.
        for accepted in (
            NOT_MODIFIED,
            "bad request: message is not modified",
            "BAD REQUEST: MESSAGE IS NOT MODIFIED",
            "  Bad Request:   message is not   modified  ",
            "message is not modified",
        ):
            self.assertTrue(
                telegram_api.is_message_not_modified(
                    self.refusal_detail(accepted)
                ),
                "must match: %r" % accepted,
            )


class _StubHandler(BaseHTTPRequestHandler):
    """Returns the status and body the test class configured."""

    def do_POST(self):
        status, body = self.server.reply
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RealTransportContractTests(unittest.TestCase):
    """T-C8. The recorded 'an injected seam that does not match its
    real dependency' class, closed by running the REAL dependency.

    `default_transport` uses `urlopen`, which RAISES HTTPError on every
    non-2xx, and Telegram returns BOTH message-not-modified AND
    message-to-edit-not-found as HTTP 400 with a JSON ok=false body.
    Every fake in the suite reaches the ok=false branch by RETURNING
    (400, body) — a shape the real transport never produces. A
    classifier proven only against that fake is not evidence, so this
    test drives the real transport end to end over a real socket.
    """

    def serve(self, status, body):
        server = HTTPServer(("127.0.0.1", 0), _StubHandler)
        server.reply = (status, body)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def real_api(self, status, body):
        """A TelegramApi on the REAL default_transport, pointed at the
        stub. Only the URL is redirected; the transport is production."""
        port = self.serve(status, body)
        api = telegram_api.TelegramApi(TOKEN)
        self.assertIs(
            api._transport, telegram_api.default_transport,
            "this contract test is void unless the REAL transport runs",
        )
        api._url = lambda method: "http://127.0.0.1:%d/%s" % (port, method)
        return api

    def test_C8_real_transport_400_with_telegram_body_reaches_classifier(
        self
    ):
        """T-C8: HTTP 400 + a genuine Telegram ok=false body, over a
        real socket, through the RAISING HTTPError path — the parsed
        description reaches the classifier and the matcher."""
        api = self.real_api(400, api_refusal(NOT_MODIFIED))
        outcome = api.send_message_once(42, "placeholder wf-0001")
        self.assertEqual(
            outcome.detail.outcome, telegram_api.CALL_TELEGRAM_REFUSED
        )
        self.assertTrue(outcome.detail.body_parsed)
        self.assertIs(outcome.detail.telegram_ok, False)
        # The exact description survives the real transport. Before
        # this increment it did not exist in the process at all.
        self.assertEqual(outcome.detail.description, NOT_MODIFIED)
        self.assertEqual(outcome.detail.http_status, 400)
        self.assertEqual(outcome.classification, DEFINITE_ZERO)
        self.assertTrue(
            telegram_api.is_message_not_modified(outcome.detail)
        )

    def test_C8_real_transport_400_not_found_reaches_the_r3_matcher(self):
        """T-C8 (b): R-3's condition over the same real path."""
        api = self.real_api(400, api_refusal(NOT_FOUND))
        outcome = api.edit_message_text(42, 99, "the result")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.detail.description, NOT_FOUND)
        self.assertTrue(
            telegram_api.is_message_to_edit_not_found(outcome.detail)
        )
        self.assertFalse(
            telegram_api.is_message_not_modified(outcome.detail)
        )

    def test_C8_real_transport_400_from_a_proxy_is_indefinite(self):
        """T-C8 (c): R-7 over the real path — a 400 whose body is not a
        Telegram document classifies INDEFINITE, so the proof of R-7
        does not rest on the fake either."""
        api = self.real_api(400, b"<html>400 Bad Request</html>")
        outcome = api.send_message_once(42, "placeholder")
        self.assertEqual(
            outcome.detail.outcome, telegram_api.CALL_NO_TELEGRAM_BODY
        )
        self.assertFalse(outcome.detail.body_parsed)
        self.assertEqual(outcome.classification, INDEFINITE)

    def test_C8_real_transport_success_still_works(self):
        """T-C8 (d): anti-vacuity — the real transport's happy path is
        unaffected, so the rows above are not green because the stub
        broke everything."""
        api = self.real_api(
            200, json.dumps({"ok": True, "result": {"message_id": 12}})
            .encode("utf-8")
        )
        outcome = api.send_message_once(42, "placeholder")
        self.assertEqual(outcome.classification, APPLIED)
        self.assertEqual(outcome.message_id, 12)


class ExistingContractPreservedTests(unittest.TestCase):
    """The hard constraint on (d): `_call`'s arity and problem-string
    texts are unchanged, and `retryable` keeps its meaning."""

    def test_call_keeps_its_four_tuple_arity(self):
        api, _, _ = make_api([api_ok({"message_id": 1})])
        returned = api._call("sendMessage", {"chat_id": 1}, 5)
        self.assertEqual(len(returned), 4)
        # The structured sibling is additive: same four, plus detail.
        structured = api._call_structured("sendMessage", {"chat_id": 1}, 5)
        self.assertEqual(len(structured), 5)
        self.assertEqual(structured[:4], returned)
        self.assertIsInstance(structured[4], telegram_api.CallDetail)

    def test_send_with_retry_keeps_its_two_tuple_arity(self):
        # Authored assertion: breaking this arity makes every existing
        # caller crash, and an ERROR cascade is not a kill (R-8.3).
        api, _, _ = make_api([api_ok({"message_id": 1})])
        returned = api._send_with_retry("sendMessage", {"chat_id": 1})
        self.assertEqual(len(returned), 2)
        detailed = api._send_with_retry_detail(
            "sendMessage", {"chat_id": 1}
        )
        self.assertEqual(len(detailed), 3)
        self.assertEqual(detailed[:2], returned)
        self.assertIsInstance(detailed[2], telegram_api.CallDetail)

    def test_existing_problem_strings_are_byte_identical(self):
        cases = (
            ([http_error(400, api_refusal(NOT_MODIFIED))],
             "telegram api sendMessage failed: HTTP 400"),
            ([http_error(500, None)],
             "telegram api sendMessage failed: HTTP 500"),
            ([(200, b'{"ok": false, "description": "nope"}')],
             "telegram api sendMessage returned ok=false (HTTP 200): nope"),
            ([(200, b"not json")],
             None),  # prefix-checked below
        )
        for script, expected in cases:
            api, _, _ = make_api(script)
            _, problem, _, _ = api._call("sendMessage", {"a": 1}, 5)
            if expected is None:
                self.assertTrue(
                    problem.startswith(
                        "telegram api sendMessage returned undecodable"
                        " JSON ("
                    ), problem,
                )
            else:
                self.assertEqual(problem, expected)

    def test_retryable_flag_keeps_its_existing_meaning(self):
        # A 400 WITH a parsed Telegram body is now definite_zero on the
        # NEW path, but the OLD flag must still say non-retryable, and
        # 429/5xx must still say retryable — existing callers depend on
        # exactly this.
        for script, expected in (
            ([http_error(400, api_refusal("Bad Request: chat not found"))],
             False),
            ([http_error(429, api_refusal("Too Many Requests", 429))], True),
            ([http_error(500, None)], True),
            ([(200, b"not json")], True),
            ([OSError("boom")], True),
            ([ValueError("bad url")], False),
        ):
            api, _, _ = make_api(script)
            _, _, _, retryable = api._call("sendMessage", {"a": 1}, 5)
            self.assertEqual(retryable, expected, repr(script))

    def test_description_is_redacted_and_bounded(self):
        # The description leaves this module like every other text.
        api, _, _ = make_api([
            http_error(400, api_refusal("leak %s tail" % TOKEN))
        ])
        detail = api.send_message_once(42, "x").detail
        self.assertNotIn(TOKEN, detail.description)
        self.assertIn(telegram_api.REDACTED_TOKEN, detail.description)
        long_description = "z" * (telegram_api.MAX_PROBLEM_CHARS * 2)
        api2, _, _ = make_api([
            http_error(400, api_refusal(long_description))
        ])
        detail2 = api2.send_message_once(42, "x").detail
        self.assertIn("[problem text capped]", detail2.description)
        # A capped description can never equal a known short form, so
        # it fails CLOSED — the safe direction.
        self.assertFalse(telegram_api.is_message_not_modified(detail2))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
