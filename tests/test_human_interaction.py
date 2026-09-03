"""Regression coverage for the human-interaction seam (P1-A2).

Every test names the property it proves. Transport effects are
observed at the REAL substitution points — a recording client injected
into the Telegram implementation, the real ``TelegramApi`` over a
scripted transport, and the adapter's own ``_interaction`` attribute —
never inferred from source text, except where a static pin is the
stated proof (T1, T5) and is then paired with a behavioral probe.

Test ids map to the seam design section F: T1..T7.
"""

import ast
import inspect
import json
import os
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import human_interaction
from human_interaction import (
    EVENT_ACTION,
    EVENT_MESSAGE,
    SEND_APPLIED,
    SEND_CLASSIFICATIONS,
    SEND_DEFINITE_ZERO,
    SEND_INDEFINITE,
    Control,
    EditOutcome,
    HumanInteractionAdapter,
    InteractionEvent,
    ReceiveOutcome,
    SendOnceOutcome,
    SendOutcome,
)
from human_interaction import contract as contract_module
from operator_session import FunctionOperatorSession
from telegram_operator import adapter as adapter_module
from telegram_operator import authz, config, protocol, state
from telegram_operator import telegram_api
from telegram_operator.interaction import TelegramHumanInteractionAdapter
from test_di_remote_3_transport import (
    NOT_FOUND,
    NOT_MODIFIED,
    ScriptedTransport,
    TOKEN,
    api_ok,
    api_refusal,
    http_error,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "human_interaction"
FORBIDDEN_ROOTS = (
    "telegram_operator", "codex_gateway", "operator_session",
    "workflow_authority", "target_runtime", "herdr", "herdctl",
)
NOW = 1_000_000
ALLOWED = (42,)


def import_roots(path):
    """Top-level module names named by every import statement in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    return roots


def dynamic_import_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", None))
            if name in ("__import__", "import_module"):
                found.append(name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            module = getattr(node, "module", None) or ""
            if "importlib" in names or module.startswith("importlib"):
                found.append("importlib")
    return found


# --- Recording fakes ---------------------------------------------------


class RecordingClient(object):
    """A Telegram client fake that records every call and replays a
    per-method script of returns or exceptions to raise."""

    def __init__(self, **scripts):
        self.calls = []
        self.scripts = {name: list(steps) for name, steps in scripts.items()}

    def _step(self, name, args):
        self.calls.append((name, args))
        steps = self.scripts.get(name)
        if not steps:
            raise AssertionError("unscripted client call %s%r" % (name, args))
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, BaseException):
            raise step
        return step

    def poll_updates(self, offset):
        return self._step("poll_updates", (offset,))

    def send_message(self, chat_id, text, reply_markup=None):
        return self._step("send_message", (chat_id, text, reply_markup))

    def send_message_once(self, chat_id, text):
        return self._step("send_message_once", (chat_id, text))

    def edit_message_text(self, chat_id, message_id, text):
        return self._step("edit_message_text", (chat_id, message_id, text))

    def answer_callback_query(self, callback_id, text):
        return self._step("answer_callback_query", (callback_id, text))

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        return self._step(
            "edit_message_reply_markup", (chat_id, message_id, reply_markup)
        )


class Untouchable(object):
    """Any attribute access is a test failure."""

    def __getattribute__(self, name):
        raise AssertionError("client attribute %r was touched" % name)


class SpyMapping(dict):
    """Mapping that records every key looked up on it (recursively)."""

    def __init__(self, data, accessed):
        wrapped = {}
        for key, value in data.items():
            if isinstance(value, dict):
                value = SpyMapping(value, accessed)
            wrapped[key] = value
        super(SpyMapping, self).__init__(wrapped)
        self.accessed = accessed

    def get(self, key, default=None):
        self.accessed.append(key)
        return super(SpyMapping, self).get(key, default)

    def __getitem__(self, key):
        self.accessed.append(key)
        return super(SpyMapping, self).__getitem__(key)

    def __contains__(self, key):
        self.accessed.append(key)
        return super(SpyMapping, self).__contains__(key)


CONTENT_KEYS = ("text", "data", "caption", "entities")


def message_update(uid=5, user=42, chat=42, chat_type="private",
                   text="fix the bug", message_id=70):
    message = {
        "message_id": message_id,
        "from": {"id": user},
        "chat": {"id": chat, "type": chat_type},
    }
    if text is not None:
        message["text"] = text
    return {"update_id": uid, "message": message}


def callback_update(uid=6, user=42, chat=42, data="a:abc", message_id=9):
    return {
        "update_id": uid,
        "callback_query": {
            "id": "cb%d" % uid,
            "from": {"id": user},
            "data": data,
            "message": {
                "message_id": message_id,
                "chat": {"id": chat, "type": "private"},
            },
        },
    }


def make_adapter(directory, api, **kwargs):
    adapter_config = config.AdapterConfig(
        bot_token="T", allowed_user_ids=ALLOWED, repository="/resolved/repo"
    )
    store = state.StateStore(directory)
    return adapter_module.Adapter(adapter_config, store, api, **kwargs)


# --- T1 ----------------------------------------------------------------


class T1NeutralDependencyBoundaryTests(unittest.TestCase):
    """T1: the neutral package depends on nothing provider-shaped."""

    def package_files(self):
        files = sorted(PACKAGE_DIR.glob("*.py"))
        self.assertTrue(files, "human_interaction package is empty")
        self.assertIn(PACKAGE_DIR / "contract.py", files)
        self.assertIn(PACKAGE_DIR / "__init__.py", files)
        return files

    def test_static_import_roots_exclude_every_forbidden_root(self):
        """Property: no human_interaction/*.py imports telegram_operator,
        codex_gateway, operator_session, workflow_authority,
        target_runtime, herdr or herdctl."""
        for path in self.package_files():
            roots = import_roots(path)
            self.assertTrue(roots, ("no imports parsed", path))
            self.assertEqual(
                roots & set(FORBIDDEN_ROOTS), set(), (path, roots)
            )

    def test_package_uses_no_dynamic_import_machinery(self):
        """Property: no __import__ / importlib in the neutral package,
        so the static check above cannot be bypassed at runtime."""
        for path in self.package_files():
            self.assertEqual(dynamic_import_calls(path), [], path)

    def test_subprocess_import_probe_loads_no_forbidden_module(self):
        """Property (behavioral): importing human_interaction in a fresh
        interpreter loads none of the forbidden module roots."""
        code = (
            "import sys\n"
            "import human_interaction\n"
            "import human_interaction.contract\n"
            "roots = %r\n"
            "bad = sorted(\n"
            "    name for name in sys.modules\n"
            "    if any(name == r or name.startswith(r + '.') for r in roots)\n"
            ")\n"
            "print('\\n'.join(bad))\n"
            "print('LOADED', 'human_interaction' in sys.modules)\n"
            "sys.exit(1 if bad else 0)\n"
        ) % (FORBIDDEN_ROOTS,)
        probe = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        self.assertEqual(
            probe.returncode, 0, (probe.stdout, probe.stderr)
        )
        # Anti-vacuity: the probe really imported the package.
        self.assertIn("LOADED True", probe.stdout)

    def test_telegram_implementation_imports_only_seam_provider_and_stdlib(
        self
    ):
        """Property: telegram_operator/interaction.py import roots are a
        subset of {human_interaction, telegram_operator, stdlib} — in
        particular never operator_session, codex_gateway or
        workflow_authority."""
        path = REPO_ROOT / "telegram_operator" / "interaction.py"
        roots = import_roots(path)
        self.assertIn("human_interaction", roots)
        self.assertIn("telegram_operator", roots)
        stdlib_dir = os.path.realpath(sysconfig.get_paths()["stdlib"])
        for root in roots - {"human_interaction", "telegram_operator"}:
            self.assertNotIn(root, FORBIDDEN_ROOTS)
            module = __import__(root)
            location = getattr(module, "__file__", None)
            self.assertTrue(
                location is None
                or os.path.realpath(location).startswith(stdlib_dir),
                (root, location),
            )
        self.assertEqual(dynamic_import_calls(path), [])

    def test_contract_is_abstract_with_exactly_the_specified_members(self):
        """Property: HumanInteractionAdapter is abstract and cannot be
        instantiated; its abstract member set is exactly section B's."""
        self.assertTrue(inspect.isabstract(HumanInteractionAdapter))
        with self.assertRaises(TypeError):
            HumanInteractionAdapter()
        self.assertEqual(
            set(HumanInteractionAdapter.__abstractmethods__),
            {
                "receive", "send", "send_once", "edit", "acknowledge",
                "offer_controls", "chunk_count", "would_truncate",
                "max_message_chars", "max_deliverable_chars",
            },
        )
        for name in ("max_message_chars", "max_deliverable_chars"):
            self.assertIsInstance(
                inspect.getattr_static(HumanInteractionAdapter, name),
                property,
            )
        self.assertTrue(
            issubclass(TelegramHumanInteractionAdapter, HumanInteractionAdapter)
        )

    def test_contract_docstring_states_what_the_seam_does_not_own(self):
        """Property: the non-ownership statement is present in plain
        terms (authority, delivery/Git, evidence, Herdr, reasoning,
        durable state, retry, OperatorSession)."""
        doc = " ".join(contract_module.__doc__.split())
        self.assertIn("does NOT own", doc)
        for phrase in (
            "Mission Authorization", "Git", "evidence", "Herdr",
            "Operator reasoning", "durable state", "retry policy",
            "OperatorSession",
        ):
            self.assertIn(phrase, doc, phrase)


# --- T2 ----------------------------------------------------------------


class T2TelegramDelegationTests(unittest.TestCase):
    """T2: the Telegram implementation delegates exactly once per call
    with the exact arguments and preserves the client's outcome."""

    def adapter(self, **scripts):
        client = RecordingClient(**scripts)
        return TelegramHumanInteractionAdapter(client, ALLOWED), client

    def test_construction_touches_the_client_for_nothing(self):
        """Property: __init__ only stores api and allowed_user_ids."""
        client = Untouchable()
        seam = TelegramHumanInteractionAdapter(client, ALLOWED)
        self.assertIs(object.__getattribute__(seam, "api"), client)
        self.assertEqual(seam.allowed_user_ids, ALLOWED)

    def test_send_delegates_once_and_returns_the_client_object_by_identity(
        self
    ):
        """Property: send -> one send_message(chat_id, text) call, and the
        client's SendOutcome is returned unchanged (assertIs)."""
        outcome = SendOutcome(True, (11,), 1, 0, None)
        seam, client = self.adapter(send_message=[outcome])
        self.assertIs(seam.send(42, "hello"), outcome)
        self.assertEqual(client.calls, [("send_message", (42, "hello", None))])

    def test_send_once_delegates_once_and_returns_by_identity(self):
        """Property: send_once -> one send_message_once(chat_id, text)."""
        outcome = SendOnceOutcome(SEND_APPLIED, message_id=12)
        seam, client = self.adapter(send_message_once=[outcome])
        self.assertIs(seam.send_once(42, "placeholder"), outcome)
        self.assertEqual(
            client.calls, [("send_message_once", (42, "placeholder"))]
        )

    def test_acknowledge_delegates_once_and_returns_by_identity(self):
        """Property: acknowledge -> one answer_callback_query(id, text)."""
        pair = (True, None)
        seam, client = self.adapter(answer_callback_query=[pair])
        self.assertIs(seam.acknowledge("cb1", "Recorded"), pair)
        self.assertEqual(
            client.calls, [("answer_callback_query", ("cb1", "Recorded"))]
        )

    def test_receive_delegates_once_with_the_exact_cursor(self):
        """Property: receive(cursor) -> one poll_updates(cursor)."""
        seam, client = self.adapter(
            poll_updates=[telegram_api.PollOutcome((), True, None)]
        )
        outcome = seam.receive(17)
        self.assertEqual(client.calls, [("poll_updates", (17,))])
        self.assertEqual(outcome, ReceiveOutcome((), True, None))

    def test_offer_controls_renders_the_exact_ordered_keyboard_once(self):
        """Property: offer_controls renders one inline_keyboard row, in
        order, byte-identical to the adapter's former dict, in ONE
        edit_message_reply_markup call."""
        pair = (True, None)
        seam, client = self.adapter(edit_message_reply_markup=[pair])
        controls = (
            Control("Approve plan", "a:ID"), Control("Reject plan", "r:ID"),
        )
        self.assertIs(seam.offer_controls(42, 9, controls), pair)
        self.assertEqual(client.calls, [(
            "edit_message_reply_markup",
            (42, 9, {"inline_keyboard": [[
                {"text": "Approve plan", "callback_data": "a:ID"},
                {"text": "Reject plan", "callback_data": "r:ID"},
            ]]}),
        )])
        # Order is preserved exactly, not sorted.
        seam2, client2 = self.adapter(edit_message_reply_markup=[pair])
        seam2.offer_controls(42, 9, tuple(reversed(controls)))
        self.assertEqual(
            client2.calls[0][1][2]["inline_keyboard"][0][0]["text"],
            "Reject plan",
        )

    def test_offer_controls_none_clears_by_passing_none(self):
        """Property: controls=None -> reply_markup None (the clear)."""
        seam, client = self.adapter(edit_message_reply_markup=[(True, None)])
        seam.offer_controls(42, 9, None)
        self.assertEqual(
            client.calls, [("edit_message_reply_markup", (42, 9, None))]
        )

    def test_edit_delegates_once_and_preserves_ok_problem_detail(self):
        """Property: edit -> one edit_message_text(chat, msg, text); ok,
        problem and detail pass through; a plain failure sets neither
        neutral flag."""
        detail = object()
        seam, client = self.adapter(edit_message_text=[
            telegram_api.EditOutcome(ok=False, problem="boom", detail=detail)
        ])
        outcome = seam.edit(42, 9, "new text")
        self.assertEqual(
            client.calls, [("edit_message_text", (42, 9, "new text"))]
        )
        self.assertIsInstance(outcome, EditOutcome)
        self.assertEqual(
            (outcome.ok, outcome.problem, outcome.already_applied,
             outcome.target_missing),
            (False, "boom", False, False),
        )
        self.assertIs(outcome.detail, detail)
        ok_seam, _ = self.adapter(edit_message_text=[
            telegram_api.EditOutcome(ok=True)
        ])
        self.assertEqual(ok_seam.edit(42, 9, "t").ok, True)

    def real_api(self, script):
        transport = ScriptedTransport(script)
        sleeps = []
        api = telegram_api.TelegramApi(
            TOKEN, transport=transport, sleeper=sleeps.append
        )
        return TelegramHumanInteractionAdapter(api, ALLOWED), transport

    def test_already_applied_only_under_structured_not_modified_proof(self):
        """Property (real TelegramApi over ScriptedTransport): an HTTP-400
        ok:false message-not-modified body sets already_applied; a bare
        400 without a body does NOT."""
        seam, transport = self.real_api(
            [http_error(400, api_refusal(NOT_MODIFIED))]
        )
        outcome = seam.edit(42, 99, "same text")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.already_applied)
        self.assertFalse(outcome.target_missing)
        self.assertEqual(outcome.detail.description, NOT_MODIFIED)
        self.assertEqual(transport.calls[0]["payload"], {
            "chat_id": 42, "message_id": 99, "text": "same text",
        })
        bare_seam, _ = self.real_api([http_error(400, None)])
        bare = bare_seam.edit(42, 99, "same text")
        self.assertFalse(bare.ok)
        self.assertFalse(bare.already_applied)
        self.assertFalse(bare.target_missing)
        self.assertIsNotNone(bare.problem)

    def test_target_missing_under_message_to_edit_not_found(self):
        """Property (real TelegramApi): message-to-edit-not-found sets
        target_missing and never already_applied."""
        seam, _ = self.real_api([http_error(400, api_refusal(NOT_FOUND))])
        outcome = seam.edit(42, 99, "text")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.target_missing)
        self.assertFalse(outcome.already_applied)
        self.assertEqual(outcome.detail.description, NOT_FOUND)

    def test_presentation_limits_read_telegram_api_at_call_time(self):
        """Property: chunk_count / would_truncate / max_* delegate to the
        telegram_api module attributes as they are AT CALL TIME."""
        seam = TelegramHumanInteractionAdapter(Untouchable(), ALLOWED)
        self.assertEqual(seam.max_message_chars, telegram_api.MAX_MESSAGE_CHARS)
        self.assertEqual(
            seam.max_deliverable_chars, telegram_api.MAX_DELIVERABLE_CHARS
        )
        text = "x" * (telegram_api.MAX_MESSAGE_CHARS + 1)
        self.assertEqual(seam.chunk_count(text), telegram_api.chunk_count(text))
        self.assertEqual(
            seam.would_truncate(text), telegram_api.would_truncate(text)
        )
        with mock.patch.object(telegram_api, "MAX_MESSAGE_CHARS", 7):
            with mock.patch.object(telegram_api, "MAX_DELIVERABLE_CHARS", 21):
                self.assertEqual(seam.max_message_chars, 7)
                self.assertEqual(seam.max_deliverable_chars, 21)
                self.assertEqual(seam.chunk_count("x" * 15), 3)


# --- T3 ----------------------------------------------------------------


class T3IdentityAndAuthenticationOrderingTests(unittest.TestCase):
    """T3: events carry exact identity; content only after
    authentication; the transport shapes map exactly."""

    def receive(self, updates):
        client = RecordingClient(
            poll_updates=[telegram_api.PollOutcome(tuple(updates), False, None)]
        )
        return TelegramHumanInteractionAdapter(client, ALLOWED).receive(0)

    def test_allowed_message_maps_every_identity_field_and_content(self):
        """Property: update_id/user/chat/message ids and text map 1:1."""
        (event,) = self.receive([message_update()]).events
        self.assertEqual(event, InteractionEvent(
            sequence=5, allowed=True, reason=authz.REASON_ALLOWED,
            kind=EVENT_MESSAGE, principal_id=42, conversation_id=42,
            message_id=70, action_id=None, content="fix the bug",
        ))

    def test_allowed_callback_maps_action_id_bound_message_and_data(self):
        """Property: a callback maps callback id, bound message id and
        data, as an EVENT_ACTION."""
        (event,) = self.receive([callback_update()]).events
        self.assertEqual(event, InteractionEvent(
            sequence=6, allowed=True, reason=authz.REASON_ALLOWED,
            kind=EVENT_ACTION, principal_id=42, conversation_id=42,
            message_id=9, action_id="cb6", content="a:abc",
        ))

    def test_denied_updates_carry_no_content_and_read_no_content_key(self):
        """Property (behavioral): for unknown-user, non-private,
        chat/user mismatch and malformed updates, allowed=False,
        content=None, and a recording mapping proves no content key
        (text/data/caption/entities) was ever read."""
        cases = {
            authz.REASON_UNKNOWN_USER: message_update(user=666, chat=666),
            authz.REASON_NON_PRIVATE_CHAT: message_update(chat_type="group"),
            authz.REASON_CHAT_USER_MISMATCH: message_update(chat=43),
            authz.REASON_MALFORMED_ENVELOPE: {
                "update_id": 8, "message": {"from": {"id": 42},
                                            "text": "secret"},
            },
        }
        for reason, raw in cases.items():
            accessed = []
            raw["message"]["caption"] = "secret caption"
            raw["message"]["entities"] = [{"type": "bot_command"}]
            spy = SpyMapping(raw, accessed)
            (event,) = self.receive([spy]).events
            self.assertFalse(event.allowed, reason)
            self.assertEqual(event.reason, reason)
            self.assertIsNone(event.content, reason)
            self.assertTrue(accessed, reason)
            for key in CONTENT_KEYS:
                self.assertNotIn(key, accessed, (reason, accessed))
        callback_raw = callback_update(user=666, chat=666)
        callback_raw["callback_query"]["caption"] = "x"
        accessed = []
        (event,) = self.receive([SpyMapping(callback_raw, accessed)]).events
        self.assertFalse(event.allowed)
        self.assertIsNone(event.content)
        for key in CONTENT_KEYS:
            self.assertNotIn(key, accessed)

    def test_content_is_read_only_after_an_allowed_decision(self):
        """Property (behavioral): for an ALLOWED update the content key
        is read, and only after the envelope keys."""
        accessed = []
        (event,) = self.receive(
            [SpyMapping(message_update(), accessed)]
        ).events
        self.assertTrue(event.allowed)
        self.assertEqual(event.content, "fix the bug")
        self.assertIn("text", accessed)
        self.assertLess(accessed.index("update_id"), accessed.index("text"))
        self.assertLess(accessed.index("chat"), accessed.index("text"))

    def test_malformed_updates_are_denied_with_none_sequence_when_unreadable(
        self
    ):
        """Property: non-dict and bool update_id -> denied, sequence
        None; a readable id with a missing chat -> denied, sequence kept."""
        outcome = self.receive([
            "not a dict", None, 5,
            {"update_id": True, "message": message_update()["message"]},
            {"update_id": 11, "message": {"message_id": 1,
                                          "from": {"id": 42}}},
        ])
        self.assertEqual(len(outcome.events), 5)
        for event in outcome.events[:4]:
            self.assertFalse(event.allowed)
            self.assertIsNone(event.sequence)
            self.assertIsNone(event.content)
            self.assertEqual(event.reason, authz.REASON_MALFORMED_ENVELOPE)
        last = outcome.events[4]
        self.assertFalse(last.allowed)
        self.assertEqual(last.sequence, 11)
        self.assertIsNone(last.content)

    def test_allowed_message_without_text_has_none_content(self):
        """Property: an allowed non-text message (sticker) -> content None
        while identity is intact."""
        (event,) = self.receive([message_update(text=None)]).events
        self.assertTrue(event.allowed)
        self.assertEqual(event.kind, EVENT_MESSAGE)
        self.assertIsNone(event.content)
        self.assertEqual((event.sequence, event.principal_id), (5, 42))

    def test_idle_deadline_maps_to_idle_with_no_events(self):
        """Property: deadline_fired -> idle=True, events=(), no problem."""
        client = RecordingClient(
            poll_updates=[telegram_api.PollOutcome((), True, None)]
        )
        outcome = TelegramHumanInteractionAdapter(client, ALLOWED).receive(3)
        self.assertEqual(outcome, ReceiveOutcome((), True, None))

    def test_poll_problem_passes_through_verbatim_with_no_events(self):
        """Property: a transport problem string is returned unchanged,
        idle=False, events=()."""
        client = RecordingClient(poll_updates=[
            telegram_api.PollOutcome((), False, "telegram api down (redacted)")
        ])
        outcome = TelegramHumanInteractionAdapter(client, ALLOWED).receive(3)
        self.assertEqual(
            outcome, ReceiveOutcome((), False, "telegram api down (redacted)")
        )

    def test_malformed_client_outcome_fails_closed_with_a_problem(self):
        """Property: a client outcome missing problem/updates/
        deadline_fired is a problem, never an idle wait."""

        class Partial(object):
            problem = None
            updates = ()

        client = RecordingClient(poll_updates=[Partial()])
        outcome = TelegramHumanInteractionAdapter(client, ALLOWED).receive(0)
        self.assertEqual(outcome.events, ())
        self.assertFalse(outcome.idle)
        self.assertIsInstance(outcome.problem, str)
        self.assertIn("deadline_fired", outcome.problem)
        bare = RecordingClient(poll_updates=[object()])
        outcome = TelegramHumanInteractionAdapter(bare, ALLOWED).receive(0)
        self.assertFalse(outcome.idle)
        self.assertTrue(outcome.problem)


# --- T4 ----------------------------------------------------------------


class T4NoRetryNoSwallowTests(unittest.TestCase):
    """T4: one client call per seam call, whatever the outcome; client
    exceptions propagate unchanged."""

    METHODS = (
        ("send", (42, "t"), "send_message", SendOutcome(False, (), 0, 0, "p")),
        ("send_once", (42, "t"), "send_message_once",
         SendOnceOutcome(SEND_INDEFINITE, problem="p")),
        ("edit", (42, 9, "t"), "edit_message_text",
         telegram_api.EditOutcome(ok=False, problem="p")),
        ("acknowledge", ("cb", "t"), "answer_callback_query", (False, "p")),
        ("offer_controls", (42, 9, None), "edit_message_reply_markup",
         (False, "p")),
        ("receive", (0,), "poll_updates",
         telegram_api.PollOutcome((), False, "p")),
    )

    def test_failure_outcomes_make_exactly_one_call_and_are_not_retried(self):
        """Property: a failed / indefinite client outcome is returned
        after exactly one call; no second attempt, no sleep."""
        for name, args, client_method, outcome in self.METHODS:
            client = RecordingClient(**{client_method: [outcome]})
            seam = TelegramHumanInteractionAdapter(client, ALLOWED)
            result = getattr(seam, name)(*args)
            self.assertEqual(len(client.calls), 1, name)
            self.assertEqual(client.calls[0][0], client_method, name)
            if name in ("send", "send_once", "acknowledge", "offer_controls"):
                self.assertIs(result, outcome, name)
            elif name == "edit":
                self.assertEqual((result.ok, result.problem), (False, "p"))
            else:
                self.assertEqual(result.problem, "p")

    def test_client_exceptions_propagate_unchanged_after_one_call(self):
        """Property: an exception raised by the client escapes the seam
        by identity, after exactly one call."""
        for name, args, client_method, _ in self.METHODS:
            boom = RuntimeError("client failed: %s" % client_method)
            client = RecordingClient(**{client_method: [boom]})
            seam = TelegramHumanInteractionAdapter(client, ALLOWED)
            with self.assertRaises(RuntimeError) as caught:
                getattr(seam, name)(*args)
            self.assertIs(caught.exception, boom, name)
            self.assertEqual(len(client.calls), 1, name)

    def test_implementation_source_has_no_retry_sleep_or_swallow(self):
        """Property (static, paired with the behavioral tests above): the
        Telegram implementation contains no loop-retry, no sleep, and no
        try/except at all."""
        tree = ast.parse(
            (REPO_ROOT / "telegram_operator" / "interaction.py").read_text()
        )
        for node in ast.walk(tree):
            self.assertNotIsInstance(node, (ast.Try, ast.While))
            if isinstance(node, ast.Call):
                self.assertNotEqual(getattr(node.func, "attr", None), "sleep")


# --- T5 ----------------------------------------------------------------


class T5ProductionWiringTests(unittest.TestCase):
    """T5: the adapter defaults to the Telegram seam over the injected
    client, honours an explicit seam, and no longer reaches the
    Telegram client or authz directly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_default_interaction_is_telegram_over_the_injected_client(self):
        """Property: Adapter(config, store, api) -> _interaction is a
        TelegramHumanInteractionAdapter with api IS api and the config's
        allowlist; construction touches the client for nothing."""
        api = Untouchable()
        adapter = make_adapter(self.tmp.name, api)
        seam = adapter._interaction
        self.assertIsInstance(seam, TelegramHumanInteractionAdapter)
        self.assertIs(object.__getattribute__(seam, "api"), api)
        self.assertEqual(seam.allowed_user_ids, adapter.config.allowed_user_ids)
        self.assertIs(adapter.api, api)

    def test_api_setter_rebinds_only_the_default_interaction(self):
        """Property: adapter.api = other rebinds the DEFAULT seam's api;
        an explicitly injected seam wins and is untouched."""
        first, second = object(), object()
        adapter = make_adapter(self.tmp.name, first)
        adapter.api = second
        self.assertIs(adapter.api, second)
        self.assertIs(adapter._interaction.api, second)
        injected = FakeHumanInteractionAdapter([])
        explicit = make_adapter(self.tmp.name, first, interaction=injected)
        self.assertIs(explicit._interaction, injected)
        explicit.api = second
        self.assertIs(explicit.api, second)
        self.assertIs(explicit._interaction, injected)
        self.assertFalse(hasattr(injected, "api"))

    def test_session_and_interaction_are_independent_attributes(self):
        """Property: _session and _interaction are separate seams; neither
        module imports the other."""
        adapter = make_adapter(self.tmp.name, object())
        self.assertIsNot(adapter._session, adapter._interaction)
        self.assertNotIn(
            "operator_session",
            import_roots(REPO_ROOT / "telegram_operator" / "interaction.py"),
        )
        for path in (REPO_ROOT / "operator_session").glob("*.py"):
            self.assertNotIn("human_interaction", import_roots(path))

    def test_cli_builds_the_adapter_without_an_interaction_keyword(self):
        """Property (AST pin): telegram_operator/cli.py constructs
        Adapter(...) with no `interaction=` keyword, so production runs
        the Telegram default."""
        tree = ast.parse(
            (REPO_ROOT / "telegram_operator" / "cli.py").read_text()
        )
        constructions = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None))
            == "Adapter"
        ]
        self.assertTrue(constructions)
        for node in constructions:
            self.assertNotIn(
                "interaction", [keyword.arg for keyword in node.keywords]
            )

    def test_adapter_imports_neither_telegram_api_nor_authz_and_never_calls_api(
        self
    ):
        """Property (AST pin, D9): adapter.py imports neither
        telegram_operator.telegram_api nor telegram_operator.authz, and
        contains no `self.api.<call>`; it does import human_interaction
        and telegram_operator.interaction."""
        tree = ast.parse(
            (REPO_ROOT / "telegram_operator" / "adapter.py").read_text()
        )
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(
                    "%s.%s" % (node.module, alias.name) for alias in node.names
                )
        for forbidden in (
            "telegram_operator.telegram_api", "telegram_operator.authz",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertIn("human_interaction", imported)
        self.assertIn("telegram_operator.interaction", imported)
        self_api_uses = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "api"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        ]
        self.assertEqual(self_api_uses, [])
        self.assertFalse(hasattr(adapter_module, "telegram_api"))
        self.assertFalse(hasattr(adapter_module, "authz"))


# --- T6 ----------------------------------------------------------------


class FakeHumanInteractionAdapter(HumanInteractionAdapter):
    """A test-side, non-Telegram seam: scripts ReceiveOutcomes and records
    every neutral call into a timeline shared with the store."""

    def __init__(self, receive_script, timeline=None):
        self.receive_script = list(receive_script)
        self.timeline = timeline if timeline is not None else []
        self.next_message_id = 100

    def _record(self, name, *args):
        self.timeline.append((name, args))

    def receive(self, cursor):
        self._record("receive", cursor)
        if self.receive_script:
            return self.receive_script.pop(0)
        return ReceiveOutcome((), True, None)

    def send(self, conversation_id, text):
        self._record("send", conversation_id, text)
        message_id = self.next_message_id
        self.next_message_id += 1
        return SendOutcome(True, (message_id,), 1, 0, None)

    def send_once(self, conversation_id, text):
        self._record("send_once", conversation_id, text)
        message_id = self.next_message_id
        self.next_message_id += 1
        return SendOnceOutcome(SEND_APPLIED, message_id=message_id)

    def edit(self, conversation_id, message_id, text):
        self._record("edit", conversation_id, message_id, text)
        return EditOutcome(ok=True)

    def acknowledge(self, action_id, text):
        self._record("acknowledge", action_id, text)
        return True, None

    def offer_controls(self, conversation_id, message_id, controls):
        self._record("offer_controls", conversation_id, message_id, controls)
        return True, None

    def chunk_count(self, text):
        self._record("chunk_count", text)
        return 1

    def would_truncate(self, text):
        self._record("would_truncate", text)
        return False

    @property
    def max_message_chars(self):
        return 4096

    @property
    def max_deliverable_chars(self):
        return 20480


class RecordingStore(state.StateStore):
    """Records what every save left ON DISK into the shared timeline."""

    def __init__(self, directory, timeline):
        super(RecordingStore, self).__init__(directory)
        self.timeline = timeline

    def save(self, document):
        super(RecordingStore, self).save(document)
        snapshot = state.StateStore(self.directory).load()
        self.timeline.append(("save", (snapshot,)))


class RecordingSession(FunctionOperatorSession):
    """Counts prepare/execute so an observational path can prove it
    dispatched no Operator turn."""

    def __init__(self, build, submit):
        super(RecordingSession, self).__init__(build, submit)
        self.prepared = 0
        self.executed = 0

    def prepare(self, *args, **kwargs):
        self.prepared += 1
        return super(RecordingSession, self).prepare(*args, **kwargs)

    def execute(self, prepared):
        self.executed += 1
        return super(RecordingSession, self).execute(prepared)


class FakeGatewayRequest(object):
    def __init__(self, request_id, text, repository, session_id, source):
        self.request_id = request_id
        self.text = text
        self.repository = repository
        self.session_id = session_id
        self.source = source


class FakeGatewayResult(object):
    def __init__(self, request_id, message, session_id="sess-1"):
        self.contract_version = 1
        self.request_id = request_id
        self.session_id = session_id
        self.status = "completed"
        self.message = message
        self.error = None
        self.unrecognized_event_lines = 0


def envelope(kind, body):
    return protocol.RESPONSE_PREFIX + json.dumps({
        "remote_protocol_version": protocol.REMOTE_PROTOCOL_VERSION,
        "kind": kind,
        "body": body,
    })


def event(sequence, content, kind=EVENT_MESSAGE, allowed=True,
          reason=authz.REASON_ALLOWED, principal_id=42, conversation_id=42,
          message_id=None, action_id=None):
    return InteractionEvent(
        sequence=sequence, allowed=allowed, reason=reason, kind=kind,
        principal_id=principal_id, conversation_id=conversation_id,
        message_id=message_id, action_id=action_id, content=content,
    )


def flatten(value):
    if isinstance(value, (tuple, list)):
        for item in value:
            for leaf in flatten(item):
                yield leaf
    else:
        yield value


class T6FakeAdapterControllerPathTests(unittest.TestCase):
    """T6: the adapter's whole controller path runs over a non-Telegram
    seam, with no Telegram dict and no TelegramApi anywhere."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Anything Telegram-shaped constructed during the run is a failure.
        for target in (telegram_api.TelegramApi, TelegramHumanInteractionAdapter):
            patcher = mock.patch.object(
                target, "__init__",
                side_effect=AssertionError("%s constructed" % target.__name__),
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def harness(self, receive_script, gateway_script):
        timeline = []
        fake = FakeHumanInteractionAdapter(receive_script, timeline)
        store = RecordingStore(self.tmp.name, timeline)
        requests = []
        counter = [0]
        script = list(gateway_script)

        def build(text, repository, session_id=None, source="terminal"):
            counter[0] += 1
            return FakeGatewayRequest(
                "req-%d" % counter[0], text, repository, session_id, source
            )

        def submit(request):
            requests.append(request)
            timeline.append(("gateway.submit", request))
            result = script.pop(0)
            result.request_id = request.request_id
            return result

        session = RecordingSession(build, submit)
        adapter_config = config.AdapterConfig(
            bot_token="T", allowed_user_ids=ALLOWED,
            repository="/resolved/repo",
        )
        adapter = adapter_module.Adapter(
            adapter_config, store, Untouchable(),
            clock=lambda: NOW, operator_session=session, interaction=fake,
        )
        return adapter, fake, timeline, session, requests

    @staticmethod
    def drain(adapter):
        while True:
            try:
                item = adapter._work_signals.get_nowait()
            except Exception:
                return
            if item is adapter_module._WORKER_SENTINEL:
                return
            adapter.process_work_item(item)

    @staticmethod
    def index(timeline, name, predicate=lambda args: True):
        for position, (kind, args) in enumerate(timeline):
            if kind == name and predicate(args):
                return position
        return None

    def assert_no_telegram_shape_reached_the_fake(self, timeline):
        for kind, args in timeline:
            if kind in ("save", "gateway.submit"):
                continue
            for leaf in flatten(args):
                self.assertNotIsInstance(leaf, dict, (kind, args))

    def test_intent_to_approval_to_dispatch_runs_entirely_over_the_seam(self):
        """Property: intent event -> queued + ack -> worker -> plan sent ->
        offer_controls with EXACTLY the two plan Controls on the bound
        message -> action event a:<id> -> decision durably consumed
        BEFORE acknowledge -> dispatch -> reply; nothing Telegram-shaped
        is involved."""
        adapter, fake, timeline, session, requests = self.harness(
            receive_script=[], gateway_script=[
                FakeGatewayResult(None, envelope("plan", "Step 1. Do X.")),
                FakeGatewayResult(None, envelope("result", "done")),
            ],
        )
        fake.receive_script.append(
            ReceiveOutcome((event(5, "fix the bug"),), False, None)
        )
        self.assertTrue(adapter.poll_once())
        saves = [args[0] for kind, args in timeline if kind == "save"]
        self.assertEqual(saves[-1]["update_offset"], 6)
        self.assertEqual(saves[-1]["queue"][0]["text"], "fix the bug")
        self.assertEqual(saves[-1]["queue"][0]["chat_id"], 42)
        self.assertEqual(saves[-1]["queue"][0]["user_id"], 42)
        self.assertEqual(saves[-1]["queue"][0]["update_id"], 5)
        ack = self.index(
            timeline, "send",
            lambda args: args[1] == "Received. Routing to the Codex Operator…",
        )
        self.assertIsNotNone(ack)
        self.assertLess(self.index(timeline, "save"), ack)
        self.drain(adapter)
        self.assertEqual(session.prepared, 1)
        self.assertEqual(session.executed, 1)
        self.assertIn("fix the bug", requests[0].text)
        plan = self.index(
            timeline, "send",
            lambda args: adapter_module.PLAN_MESSAGE_HEADER in args[1],
        )
        self.assertIsNotNone(plan)
        offer = self.index(timeline, "offer_controls")
        self.assertIsNotNone(offer)
        self.assertLess(plan, offer)
        (approval_id,) = adapter._document["approvals"].keys()
        record = adapter._document["approvals"][approval_id]
        plan_message_id = record["plan_message_id"]
        self.assertIsNotNone(plan_message_id)
        self.assertEqual(timeline[offer][1], (42, plan_message_id, (
            Control("Approve plan", "a:" + approval_id),
            Control("Reject plan", "r:" + approval_id),
        )))
        # The binding was persisted BEFORE the controls were offered.
        armed = self.index(
            timeline, "save",
            lambda args: any(
                stored["plan_message_id"] == plan_message_id
                for stored in args[0]["approvals"].values()
            ),
        )
        self.assertLess(armed, offer)
        # The human presses Approve on the bound message.
        fake.receive_script.append(ReceiveOutcome((
            event(6, "a:" + approval_id, kind=EVENT_ACTION,
                  message_id=plan_message_id, action_id="act-6"),
        ), False, None))
        self.assertTrue(adapter.poll_once())
        consumed = self.index(
            timeline, "save",
            lambda args: any(
                stored["consumed_at"] is not None
                for stored in args[0]["approvals"].values()
            ),
        )
        acknowledged = self.index(timeline, "acknowledge")
        self.assertIsNotNone(consumed)
        self.assertIsNotNone(acknowledged)
        self.assertLess(consumed, acknowledged)
        self.assertEqual(timeline[acknowledged][1], (
            "act-6", "Decision recorded (approve). Dispatching…",
        ))
        cleared = self.index(
            timeline, "offer_controls", lambda args: args[2] is None
        )
        self.assertEqual(timeline[cleared][1], (42, plan_message_id, None))
        self.drain(adapter)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1].session_id, "sess-1")
        self.assertTrue(requests[1].text.startswith(protocol.DECISION_PREFIX))
        reply = self.index(
            timeline, "send", lambda args: "done" in args[1]
        )
        self.assertIsNotNone(reply)
        self.assertLess(self.index(timeline, "gateway.submit"), reply)
        self.assertEqual(
            state.StateStore(self.tmp.name).load()["update_offset"], 7
        )
        self.assert_no_telegram_shape_reached_the_fake(timeline)

    def test_denied_event_saves_only_the_offset_and_calls_nothing(self):
        """Property: a denied event -> offset-only durable save; no
        interaction call other than the receive itself."""
        adapter, fake, timeline, session, _ = self.harness(
            receive_script=[ReceiveOutcome((
                event(9, None, allowed=False, reason=authz.REASON_UNKNOWN_USER,
                      principal_id=666, conversation_id=666),
            ), False, None)],
            gateway_script=[],
        )
        before = state.StateStore(self.tmp.name).load()
        self.assertTrue(adapter.poll_once())
        after = state.StateStore(self.tmp.name).load()
        self.assertEqual(after["update_offset"], 10)
        for key in before:
            if key != "update_offset":
                self.assertEqual(after[key], before[key], key)
        self.assertEqual(
            [kind for kind, _ in timeline if kind != "save"], ["receive"]
        )
        self.assertEqual((session.prepared, session.executed), (0, 0))
        self.assert_no_telegram_shape_reached_the_fake(timeline)

    def test_status_is_answered_without_any_operator_session_turn(self):
        """Property: /status -> ack, then an answer from durable state
        only; prepare/execute are never called."""
        adapter, fake, timeline, session, requests = self.harness(
            receive_script=[
                ReceiveOutcome((event(5, "/status"),), False, None),
            ],
            gateway_script=[],
        )
        self.assertTrue(adapter.poll_once())
        self.assertEqual(
            timeline[self.index(timeline, "send")][1],
            (42, "Gathering status…"),
        )
        self.drain(adapter)
        answer = self.index(
            timeline, "send", lambda args: args[1].startswith("Adapter state")
        )
        self.assertIsNotNone(answer)
        self.assertEqual((session.prepared, session.executed), (0, 0))
        self.assertEqual(requests, [])
        self.assertIsNone(self.index(timeline, "gateway.submit"))
        self.assert_no_telegram_shape_reached_the_fake(timeline)

    def test_poll_problem_and_idle_leave_the_cursor_untouched(self):
        """Property: a receive problem returns False and saves nothing; an
        idle receive returns True and saves nothing."""
        adapter, fake, timeline, _, _ = self.harness(
            receive_script=[
                ReceiveOutcome((), False, "transport down"),
                ReceiveOutcome((), True, None),
            ],
            gateway_script=[],
        )
        self.assertFalse(adapter.poll_once())
        self.assertEqual(adapter._last_poll_problem, "transport down")
        self.assertTrue(adapter.poll_once())
        self.assertEqual([kind for kind, _ in timeline], ["receive", "receive"])
        self.assertIsNone(state.StateStore(self.tmp.name).load()["update_offset"])


# --- T7 ----------------------------------------------------------------


class T7RetainedTelegramApiCompatibilityTests(unittest.TestCase):
    """T7: telegram_api's public names ARE the neutral ones, the client
    still returns them, and the raw-update entry point still works."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_telegram_api_names_are_the_neutral_objects_by_identity(self):
        """Property: telegram_api.SendOutcome IS human_interaction.SendOutcome
        (and SendOnceOutcome, EditOutcome); SEND_* values are equal."""
        self.assertIs(telegram_api.SendOutcome, human_interaction.SendOutcome)
        self.assertIs(
            telegram_api.SendOnceOutcome, human_interaction.SendOnceOutcome
        )
        self.assertIs(telegram_api.EditOutcome, human_interaction.EditOutcome)
        self.assertEqual(telegram_api.SEND_APPLIED, SEND_APPLIED)
        self.assertEqual(telegram_api.SEND_DEFINITE_ZERO, SEND_DEFINITE_ZERO)
        self.assertEqual(telegram_api.SEND_INDEFINITE, SEND_INDEFINITE)
        self.assertEqual(telegram_api.SEND_CLASSIFICATIONS, SEND_CLASSIFICATIONS)
        self.assertEqual(
            SEND_CLASSIFICATIONS, ("applied", "definite_zero", "indefinite")
        )
        self.assertEqual(
            [f.name for f in SendOutcome.__dataclass_fields__.values()],
            ["ok", "message_ids", "chunks_sent", "truncated_chars", "problem"],
        )
        self.assertEqual(
            [f.name for f in SendOnceOutcome.__dataclass_fields__.values()],
            ["classification", "message_id", "problem", "detail"],
        )
        self.assertEqual(
            [f.name for f in EditOutcome.__dataclass_fields__.values()],
            ["ok", "problem", "detail", "already_applied", "target_missing"],
        )

    def test_real_client_still_returns_the_shared_types(self):
        """Property: TelegramApi.send_message / send_message_once /
        edit_message_text return instances of the neutral dataclasses."""
        transport = ScriptedTransport([api_ok({"message_id": 5})])
        api = telegram_api.TelegramApi(
            TOKEN, transport=transport, sleeper=lambda seconds: None
        )
        self.assertIsInstance(api.send_message(42, "t"), SendOutcome)
        self.assertIsInstance(api.send_message_once(42, "t"), SendOnceOutcome)
        self.assertIsInstance(api.edit_message_text(42, 5, "t"), EditOutcome)

    def test_process_update_still_routes_a_raw_update_by_default(self):
        """Property: Adapter.process_update(raw) with the default seam
        authenticates and routes exactly as before (queued + acked)."""
        client = RecordingClient(
            send_message=[SendOutcome(True, (1,), 1, 0, None)]
        )
        adapter = make_adapter(self.tmp.name, client, clock=lambda: NOW)
        adapter.process_update(message_update(uid=5, text="fix the bug"))
        document = state.StateStore(self.tmp.name).load()
        self.assertEqual(document["update_offset"], 6)
        self.assertEqual(document["queue"][0]["text"], "fix the bug")
        self.assertEqual(client.calls, [(
            "send_message",
            (42, "Received. Routing to the Codex Operator…", None),
        )])
        adapter.process_update(message_update(uid=7, user=666, chat=666))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            state.StateStore(self.tmp.name).load()["update_offset"], 8
        )

    def test_process_update_refuses_a_non_telegram_interaction(self):
        """Property: with a seam lacking event_from_update,
        process_update raises TypeError naming process_event, before
        touching anything."""
        fake = FakeHumanInteractionAdapter([])
        adapter = make_adapter(self.tmp.name, object(), interaction=fake)
        with self.assertRaises(TypeError) as caught:
            adapter.process_update(message_update())
        self.assertIn("process_event", str(caught.exception))
        self.assertEqual(fake.timeline, [])
        self.assertIsNone(state.StateStore(self.tmp.name).load()["update_offset"])


if __name__ == "__main__":
    unittest.main()
