"""Regression coverage for the operator session seam (P1-A1).

Every test names the property it proves. Provider effects are observed
at the REAL substitution points — the ``codex_gateway.gateway`` module
attributes and the codex adapter's ``run_codex_turn`` — never inferred
from source text, so a seam that bound the provider at import time or
called it from ``prepare`` fails these tests for the right reason.
"""

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from codex_gateway import contract
from codex_gateway import gateway as gateway_module
from operator_session import (
    CodexOperatorSession,
    FunctionOperatorSession,
    OperatorSession,
    PreparedTurn,
    PreparedTurnError,
)
from operator_session import codex as codex_module
from operator_session import session as session_module

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "operator_session"
FIXED_ID = "f" * 32


class FakeRequest(object):
    """A plain request object: the seam must not require a dataclass."""

    def __init__(self, request_id, text, repository, session_id, source):
        self.request_id = request_id
        self.text = text
        self.repository = repository
        self.session_id = session_id
        self.source = source


class BuilderSpy(object):
    """Records every (args, kwargs) it is called with; returns FakeRequest."""

    def __init__(self, request_id=FIXED_ID):
        self.calls = []
        self.request_id = request_id
        self.built = []

    def __call__(self, text, repository, session_id=None, source="terminal"):
        self.calls.append(
            ((text, repository), {"session_id": session_id, "source": source})
        )
        request = FakeRequest(
            self.request_id, text, repository, session_id, source
        )
        self.built.append(request)
        return request


class SubmitSpy(object):
    """Counts calls, records the exact object received, returns a result."""

    def __init__(self, result="RESULT", raises=None):
        self.calls = []
        self.result = result
        self.raises = raises

    def __call__(self, request):
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        return self.result


def gateway_result(request_id, session_id="sess-1"):
    return contract.GatewayResult(
        contract_version=contract.GATEWAY_CONTRACT_VERSION,
        request_id=request_id,
        session_id=session_id,
        status=contract.STATUS_COMPLETED,
        message="ok",
        error=None,
        unrecognized_event_lines=0,
    )


class PrepareHasNoProviderEffectTests(unittest.TestCase):
    """T1: prepare builds the request and touches no provider."""

    def test_function_session_prepare_never_calls_submit(self):
        builder, submit = BuilderSpy(), SubmitSpy()
        session = FunctionOperatorSession(builder, submit)
        prepared = session.prepare("hi", "/repo", session_id="s", source="x")
        # The build hook ran exactly once (prepare does build) ...
        self.assertEqual(len(builder.calls), 1)
        # ... and the submit hook did not run at all.
        self.assertEqual(submit.calls, [])
        self.assertIsInstance(prepared, PreparedTurn)
        self.assertIs(prepared.request, builder.built[0])

    def test_codex_session_prepare_calls_neither_submit_nor_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("codex_gateway.gateway.submit") as submit, patch(
                "codex_gateway.codex_adapter.run_codex_turn"
            ) as run_turn, patch("subprocess.run") as run:
                prepared = CodexOperatorSession().prepare("hi", tmp)
            self.assertEqual(submit.call_count, 0)
            self.assertEqual(run_turn.call_count, 0)
            self.assertEqual(run.call_count, 0)
            # The build DID happen: a real gateway request was produced.
            self.assertIsInstance(prepared.request, contract.GatewayRequest)
            self.assertEqual(prepared.request.text, "hi")
            self.assertEqual(
                prepared.request.repository,
                os.path.abspath(os.path.realpath(tmp)),
            )

    def test_prepare_refuses_a_request_without_a_request_id(self):
        # Fail-closed at the build step too: an empty id never becomes
        # a PreparedTurn, and nothing reaches submit.
        submit = SubmitSpy()
        for bad in ("", None, 7):
            session = FunctionOperatorSession(BuilderSpy(bad), submit)
            with self.assertRaises(PreparedTurnError):
                session.prepare("hi", "/repo")
        self.assertEqual(submit.calls, [])


class PreparedRequestIdTests(unittest.TestCase):
    """T2: request_id is a stable, non-empty str from the injected factory."""

    def test_function_session_request_id_is_the_builders_value(self):
        session = FunctionOperatorSession(BuilderSpy("req-fixed"), SubmitSpy())
        prepared = session.prepare("hi", "/repo")
        first = prepared.request_id
        self.assertIsInstance(first, str)
        self.assertTrue(first)
        self.assertEqual(first, "req-fixed")
        self.assertEqual(prepared.request_id, first)
        self.assertEqual(prepared.request.request_id, first)

    def test_request_id_is_captured_not_projected_from_a_mutable_request(self):
        # A2 stability: the id is a frozen field captured at prepare.
        # Reassigning the (mutable) request's id afterwards changes
        # NOTHING the caller reads, and execute refuses the drifted
        # request with zero provider calls.
        submit = SubmitSpy()
        session = FunctionOperatorSession(BuilderSpy("first"), submit)
        prepared = session.prepare("hi", "/repo")
        self.assertEqual(prepared.request_id, "first")
        prepared.request.request_id = "second"
        self.assertEqual(prepared.request_id, "first")
        self.assertEqual(prepared.request_id, "first")
        with self.assertRaises(PreparedTurnError):
            session.execute(prepared)
        self.assertEqual(submit.calls, [])
        # The captured field itself is frozen.
        with self.assertRaises(AttributeError):
            prepared.request_id = "third"

    def test_request_id_is_read_once_from_a_dynamic_property(self):
        # A request whose request_id is a property yielding a NEW
        # value on every read: prepare captures exactly one value,
        # every read of the turn returns that value, and execute
        # refuses because the live identity no longer matches.
        class DriftingRequest(object):
            def __init__(self):
                self.reads = 0
                self.text, self.repository = "t", "/repo"
                self.session_id, self.source = None, "x"

            @property
            def request_id(self):
                self.reads += 1
                return "id-%d" % self.reads

        drifting = DriftingRequest()
        submit = SubmitSpy()
        session = FunctionOperatorSession(
            lambda *a, **k: drifting, submit
        )
        prepared = session.prepare("hi", "/repo")
        self.assertEqual(drifting.reads, 1)
        self.assertEqual(prepared.request_id, "id-1")
        self.assertEqual(prepared.request_id, "id-1")
        self.assertEqual(drifting.reads, 1)
        with self.assertRaises(PreparedTurnError):
            session.execute(prepared)
        self.assertEqual(submit.calls, [])

    def test_codex_session_request_id_is_the_gateway_factorys_value(self):
        # gateway.build_request reads default_request_id_factory from
        # its module globals at call time, so patching the module
        # attribute is the injected-factory seam for the Codex session.
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "codex_gateway.gateway.default_request_id_factory",
                lambda: FIXED_ID,
            ), patch("codex_gateway.gateway.submit") as submit:
                prepared = CodexOperatorSession().prepare("hi", tmp)
        self.assertEqual(submit.call_count, 0)
        self.assertIsInstance(prepared.request_id, str)
        self.assertEqual(prepared.request_id, FIXED_ID)
        self.assertEqual(prepared.request_id, prepared.request_id)


class ExecuteDelegatesExactlyOnceTests(unittest.TestCase):
    """T3: execute delegates exactly once; no hidden retry; no swallow."""

    def test_execute_calls_submit_exactly_once_and_returns_its_result(self):
        builder, submit = BuilderSpy(), SubmitSpy(result="R1")
        session = FunctionOperatorSession(builder, submit)
        prepared = session.prepare("hi", "/repo")
        result = session.execute(prepared)
        self.assertEqual(len(submit.calls), 1)
        self.assertEqual(result, "R1")

    def test_second_execute_is_a_second_call_not_a_replay_guard(self):
        builder, submit = BuilderSpy(), SubmitSpy()
        session = FunctionOperatorSession(builder, submit)
        prepared = session.prepare("hi", "/repo")
        session.execute(prepared)
        session.execute(prepared)
        self.assertEqual(len(submit.calls), 2)
        self.assertIs(submit.calls[0], submit.calls[1])

    def test_submit_exception_propagates_unchanged_with_no_retry(self):
        boom = RuntimeError("provider exploded")
        builder, submit = BuilderSpy(), SubmitSpy(raises=boom)
        session = FunctionOperatorSession(builder, submit)
        prepared = session.prepare("hi", "/repo")
        with self.assertRaises(RuntimeError) as caught:
            session.execute(prepared)
        self.assertIs(caught.exception, boom)
        self.assertEqual(len(submit.calls), 1)


class FunctionSessionForwardingTests(unittest.TestCase):
    """T4: exact keyword forwarding and identity of the submitted object."""

    def test_build_hook_forwards_session_id_and_source_as_keywords(self):
        builder, submit = BuilderSpy(), SubmitSpy()
        session = FunctionOperatorSession(builder, submit)
        session.prepare("text", "/repo", session_id="sess-9", source="telegram")
        self.assertEqual(
            builder.calls,
            [(("text", "/repo"),
              {"session_id": "sess-9", "source": "telegram"})],
        )

    def test_build_hook_defaults_match_the_gateway_signature(self):
        builder = BuilderSpy()
        FunctionOperatorSession(builder, SubmitSpy()).prepare("text", "/repo")
        self.assertEqual(
            builder.calls,
            [(("text", "/repo"), {"session_id": None, "source": "terminal"})],
        )

    def test_submit_receives_the_built_object_by_identity(self):
        builder, submit = BuilderSpy(), SubmitSpy()
        session = FunctionOperatorSession(builder, submit)
        prepared = session.prepare("text", "/repo")
        session.execute(prepared)
        self.assertEqual(len(submit.calls), 1)
        self.assertIs(submit.calls[0], builder.built[0])
        self.assertIs(submit.calls[0], prepared.request)


class CodexSessionDelegationTests(unittest.TestCase):
    """T5: the Codex session delegates to codex_gateway.gateway at call time."""

    def test_hooks_delegate_to_the_gateway_module_attributes(self):
        request = contract.GatewayRequest(
            contract_version=contract.GATEWAY_CONTRACT_VERSION,
            request_id=FIXED_ID,
            source="telegram",
            repository="/repo",
            text="text",
            session_id="sess-9",
        )
        expected = gateway_result(FIXED_ID)
        with patch(
            "codex_gateway.gateway.build_request", return_value=request
        ) as build, patch(
            "codex_gateway.gateway.submit", return_value=expected
        ) as submit:
            session = CodexOperatorSession()
            prepared = session.prepare(
                "text", "/repo", session_id="sess-9", source="telegram"
            )
            self.assertEqual(submit.call_count, 0)
            result = session.execute(prepared)
        self.assertEqual(
            build.call_args_list,
            [call("text", "/repo", session_id="sess-9", source="telegram")],
        )
        self.assertEqual(submit.call_args_list, [call(request)])
        self.assertIs(submit.call_args[0][0], request)
        self.assertIs(prepared.request, request)
        self.assertIs(result, expected)

    def test_codex_module_binds_no_gateway_function_at_import_time(self):
        # Structural half of T5: the only provider import is the
        # gateway MODULE; no `from codex_gateway.gateway import ...`
        # and no module-level alias of build_request/submit exists.
        tree = ast.parse((PACKAGE_DIR / "codex.py").read_text())
        froms = [
            node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        ]
        provider_froms = [
            (node.module, [alias.name for alias in node.names])
            for node in froms
            if (node.module or "").startswith("codex_gateway")
        ]
        self.assertEqual(provider_froms, [("codex_gateway", ["gateway"])])
        self.assertFalse(hasattr(codex_module, "submit"))
        self.assertFalse(hasattr(codex_module, "build_request"))
        self.assertIs(codex_module.gateway_module, gateway_module)


class ExecuteFailsClosedTests(unittest.TestCase):
    """T6: every refusal happens BEFORE any provider call."""

    def setUp(self):
        self.submit = SubmitSpy()
        self.session = FunctionOperatorSession(BuilderSpy(), self.submit)

    def assert_refused(self, prepared):
        with self.assertRaises(PreparedTurnError):
            self.session.execute(prepared)
        self.assertEqual(self.submit.calls, [])

    def test_foreign_object_is_refused(self):
        self.assert_refused(object())
        self.assert_refused(FakeRequest(FIXED_ID, "t", "/repo", None, "x"))
        self.assert_refused(None)

    def test_prepared_turn_from_a_different_session_is_refused(self):
        other_submit = SubmitSpy()
        other = FunctionOperatorSession(BuilderSpy(), other_submit)
        foreign = other.prepare("hi", "/repo")
        self.assert_refused(foreign)
        self.assertEqual(other_submit.calls, [])
        # The origin session itself executes it: binding is to the
        # producer, not a blanket refusal.
        other.execute(foreign)
        self.assertEqual(len(other_submit.calls), 1)

    def test_hand_constructed_turn_with_valid_request_is_refused(self):
        # Finding 1 (A2 provenance): the reviewer's probe. An ordinary
        # construction with THIS session as origin and a VALID request
        # must not execute — only a turn minted by prepare may.
        request = FakeRequest(FIXED_ID, "t", "/repo", None, "x")
        forged = PreparedTurn(
            origin=self.session, request=request, request_id=FIXED_ID
        )
        self.assert_refused(forged)
        # A value-equal forgery of a genuinely minted turn is still
        # refused: provenance is identity, not field equality.
        minted = self.session.prepare("hi", "/repo")
        look_alike = PreparedTurn(
            origin=minted.origin, request=minted.request,
            request_id=minted.request_id,
        )
        self.assert_refused(look_alike)
        self.session.execute(minted)
        self.assertEqual(self.submit.calls, [minted.request])

    def test_empty_none_and_non_str_request_ids_are_refused(self):
        for bad in ("", None, 7):
            prepared = PreparedTurn(
                origin=self.session,
                request=FakeRequest(bad, "t", "/repo", None, "x"),
                request_id=bad,
            )
            self.assert_refused(prepared)

    def test_captured_id_validation_is_independent_of_provenance(self):
        # Defense in depth: execute re-validates the captured id even
        # on a genuinely minted turn. Reaching past the frozen field
        # deliberately (object.__setattr__) is the only way to get
        # here; an ordinary caller cannot.
        for bad in ("", None, 7):
            prepared = self.session.prepare("hi", "/repo")
            object.__setattr__(prepared, "request_id", bad)
            self.assert_refused(prepared)

    def test_request_id_mutated_after_prepare_is_refused(self):
        for drifted in ("", None, "other-id"):
            prepared = self.session.prepare("hi", "/repo")
            prepared.request.request_id = drifted
            self.assert_refused(prepared)

    def test_equality_spoofing_live_request_id_is_refused(self):
        # Round-3 finding 1: the live id must be a non-empty str BEFORE
        # it is compared with the captured one. A non-string whose
        # __eq__ answers True to anything would otherwise pass the
        # drift comparison and reach the provider.
        class AlwaysEqual(object):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

            __hash__ = object.__hash__

        prepared = self.session.prepare("hi", "/repo")
        prepared.request.request_id = AlwaysEqual()
        # The spoof really does compare equal to the captured id.
        self.assertTrue(prepared.request.request_id == prepared.request_id)
        self.assertFalse(prepared.request.request_id != prepared.request_id)
        self.assert_refused(prepared)

    def test_equality_spoofing_str_subclass_live_request_id_is_refused(self):
        # A NONBLANK str subclass passes the type and blank checks, so
        # the drift comparison itself must use the base str operation
        # rather than the value's overridable __ne__/__eq__. This
        # spoof's content differs from the captured id yet answers
        # "equal" to anything; it must be refused with no provider call.
        class DriftSpoof(str):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

            __hash__ = str.__hash__

        prepared = self.session.prepare("hi", "/repo")
        spoof = DriftSpoof("different-id")
        self.assertTrue(str.__ne__(spoof, prepared.request_id))
        prepared.request.request_id = spoof
        # The spoof really does compare equal to the captured id.
        self.assertTrue(prepared.request.request_id == prepared.request_id)
        self.assertFalse(prepared.request.request_id != prepared.request_id)
        self.assert_refused(prepared)

    def test_empty_and_numeric_live_request_ids_are_refused(self):
        # Same gate, reached the same way: a live id of "" (a str, but
        # empty) and of 7 (non-str) each fail the type/emptiness clause
        # regardless of how they compare with the captured value.
        for live in ("", 7):
            prepared = self.session.prepare("hi", "/repo")
            prepared.request.request_id = live
            self.assert_refused(prepared)

    def test_request_without_request_id_attribute_is_refused(self):
        prepared = self.session.prepare("hi", "/repo")
        del prepared.request.request_id
        self.assert_refused(prepared)

    def test_base_session_is_abstract(self):
        with self.assertRaises(TypeError):
            OperatorSession()


class WhitespaceRequestIdTests(unittest.TestCase):
    """P1-A1 correction: a whitespace-only request id fails closed at
    every site the predicate guards (built, captured, live), while an
    id that merely CONTAINS whitespace is captured verbatim."""

    BLANK_IDS = (" ", "  ", "\t", "\n", " \t\r\n ")

    def setUp(self):
        self.submit = SubmitSpy()
        self.session = FunctionOperatorSession(BuilderSpy(), self.submit)

    def assert_refused(self, prepared):
        with self.assertRaises(PreparedTurnError):
            self.session.execute(prepared)
        self.assertEqual(self.submit.calls, [])

    def test_prepare_refuses_whitespace_only_built_ids(self):
        # Built-id site: no PreparedTurn, nothing reaches submit, and
        # the turn is never minted -- so a look-alike constructed for
        # that id is refused by execute too, not merely by prepare.
        for blank in self.BLANK_IDS:
            builder = BuilderSpy(blank)
            session = FunctionOperatorSession(builder, self.submit)
            with self.assertRaises(PreparedTurnError):
                session.prepare("hi", "/repo")
            self.assertEqual(len(builder.built), 1)
            self.assertEqual(len(session._minted()), 0)
            look_alike = PreparedTurn(
                origin=session, request=builder.built[0], request_id=blank
            )
            with self.assertRaises(PreparedTurnError):
                session.execute(look_alike)
        self.assertEqual(self.submit.calls, [])

    def test_live_drift_to_whitespace_only_is_refused(self):
        # Live-id site, reached the way "" and 7 are in
        # test_empty_and_numeric_live_request_ids_are_refused.
        for live in self.BLANK_IDS:
            prepared = self.session.prepare("hi", "/repo")
            prepared.request.request_id = live
            self.assert_refused(prepared)

    def test_captured_whitespace_only_id_is_refused(self):
        # Captured-id site, reached the way
        # test_captured_id_validation_is_independent_of_provenance
        # reaches it. The live id is set to the SAME blank value so the
        # drift comparison cannot stand in for the predicate: only the
        # blank check itself refuses this turn.
        for blank in self.BLANK_IDS:
            prepared = self.session.prepare("hi", "/repo")
            object.__setattr__(prepared, "request_id", blank)
            prepared.request.request_id = blank
            self.assert_refused(prepared)

    def test_ids_containing_whitespace_are_accepted_verbatim(self):
        # Acceptance guard: only BLANK ids are refused. Interior and
        # even surrounding whitespace is kept exactly as built -- the
        # seam does not strip or normalize -- and execute makes its one
        # provider call with the built request.
        for accepted in ("req 1", " req-1\t"):
            builder, submit = BuilderSpy(accepted), SubmitSpy(result="R")
            session = FunctionOperatorSession(builder, submit)
            prepared = session.prepare("hi", "/repo")
            self.assertEqual(prepared.request_id, accepted)
            self.assertEqual(prepared.request.request_id, accepted)
            self.assertEqual(session.execute(prepared), "R")
            self.assertEqual(submit.calls, [builder.built[0]])

    def test_str_subclass_overriding_strip_is_refused(self):
        # Same threat class as test_equality_spoofing_live_request_id_
        # is_refused: isinstance(value, str) is True for a SUBCLASS, so
        # the blank check must use the base str operation rather than
        # a bound method the value can override. This spoof is blank
        # by str.strip yet answers "not blank" to .strip() and "equal"
        # to anything; it must be refused at all three sites with zero
        # provider calls.
        class BlankSpoof(str):
            def strip(self, chars=None):
                return "not blank"

            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

            __hash__ = str.__hash__

        spoof = BlankSpoof(" \t\r\n ")
        self.assertEqual(str.strip(spoof), "")
        self.assertNotEqual(spoof.strip(), "")
        # Built-id site: never a PreparedTurn, never minted.
        builder = BuilderSpy(spoof)
        session = FunctionOperatorSession(builder, self.submit)
        with self.assertRaises(PreparedTurnError):
            session.prepare("hi", "/repo")
        self.assertEqual(len(session._minted()), 0)
        # Live-id site: the spoof compares equal to the captured id, so
        # the drift comparison cannot stand in for the blank check.
        prepared = self.session.prepare("hi", "/repo")
        prepared.request.request_id = spoof
        self.assertTrue(prepared.request.request_id == prepared.request_id)
        self.assert_refused(prepared)
        # Captured-id site: captured AND live are the spoof.
        prepared = self.session.prepare("hi", "/repo")
        object.__setattr__(prepared, "request_id", spoof)
        prepared.request.request_id = spoof
        self.assert_refused(prepared)
        self.assertEqual(self.submit.calls, [])


class ProviderFreeBoundaryTests(unittest.TestCase):
    """T7: the seam never imports orchestration or authority machinery."""

    FORBIDDEN_ROOTS = {"herdr", "herdctl", "workflow_authority"}
    PROVIDER_ROOTS = {
        "codex_gateway", "telegram_operator", "target_runtime",
    } | FORBIDDEN_ROOTS

    @staticmethod
    def _import_roots(path):
        roots = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                roots.add((node.module or "").split(".")[0])
        return roots

    def test_no_module_in_the_package_imports_forbidden_roots(self):
        files = sorted(PACKAGE_DIR.glob("*.py"))
        self.assertEqual(
            [p.name for p in files],
            ["__init__.py", "codex.py", "pi.py", "session.py"],
        )
        for path in files:
            self.assertEqual(
                self._import_roots(path) & self.FORBIDDEN_ROOTS, set(), path
            )

    def test_only_the_codex_module_imports_a_provider(self):
        self.assertEqual(
            self._import_roots(PACKAGE_DIR / "session.py")
            & self.PROVIDER_ROOTS,
            set(),
        )
        self.assertEqual(
            self._import_roots(PACKAGE_DIR / "__init__.py")
            & self.PROVIDER_ROOTS,
            set(),
        )
        self.assertEqual(
            self._import_roots(PACKAGE_DIR / "codex.py") & self.PROVIDER_ROOTS,
            {"codex_gateway"},
        )
        # The Pi module is a substitute for the provider, so it may not
        # import the provider it substitutes for, nor any orchestration,
        # authority, runtime, capability, worker, delivery, durable
        # execution, or git transport root.
        pi_roots = self._import_roots(PACKAGE_DIR / "pi.py")
        self.assertEqual(pi_roots & self.PROVIDER_ROOTS, set())
        self.assertEqual(
            pi_roots & {
                "herdr", "herdctl", "workflow_authority", "target_runtime",
                "capability", "worker", "pr_delivery", "durable_execution",
                "git_transport", "codex_gateway", "telegram_operator",
            },
            set(),
        )

    def test_importing_the_package_loads_no_forbidden_module(self):
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "import operator_session\n"
                    "import operator_session.session\n"
                    "import operator_session.codex\n"
                    "import operator_session.pi\n"
                    "bad = sorted(\n"
                    "    name for name in sys.modules\n"
                    "    if name.split('.')[0] in\n"
                    "    ('herdr', 'herdctl', 'workflow_authority',\n"
                    "     'target_runtime', 'telegram_operator')\n"
                    ")\n"
                    "print('\\n'.join(bad))\n"
                    "sys.exit(1 if bad else 0)\n"
                ),
            ],
            cwd=str(REPO_ROOT),
            env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            probe.returncode, 0, (probe.returncode, probe.stdout, probe.stderr)
        )


class StaticProbePinTests(unittest.TestCase):
    """Finding 3: the behavioral probe in tests/test_static.py is an
    ENUMERATED import list. This pin defends against silent deletion
    of the three operator_session imports from that enumeration —
    without it, removing them would leave every suite green while the
    probe stopped proving the seam loads no forbidden module."""

    def test_static_probe_imports_every_operator_session_module(self):
        source = (REPO_ROOT / "tests" / "test_static.py").read_text()
        tree = ast.parse(source)
        probes = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "probe"
                for t in node.targets
            )
        ]
        self.assertEqual(len(probes), 1)
        argv = probes[0].value.args[0]
        self.assertIsInstance(argv, ast.List)
        # The -c program is the implicitly concatenated string that
        # follows the "-c" literal: one Constant after folding.
        elements = argv.elts
        self.assertEqual(elements[1].value, "-c")
        program = elements[2]
        self.assertIsInstance(program, ast.Constant)
        lines = program.value.splitlines()
        for required in (
            "import operator_session",
            "import operator_session.session",
            "import operator_session.codex",
            "import operator_session.pi",
        ):
            self.assertIn(required, lines)
        # And the probe still treats these roots as forbidden.
        for root in ("herdr", "herdctl", "target_runtime"):
            self.assertIn(root, program.value)


class ContributingBulletAnchorTests(unittest.TestCase):
    """Ruling F3(b) anchor. The narrative tuple pin checks the token
    `operator_session/` ANYWHERE in CONTRIBUTING.md, and the py_compile
    echo line satisfies it on its own — so deleting the Key-components
    bullet stayed green (round-2 mutant m9). This pin anchors the bullet
    itself: a component line must START with the backtick token."""

    def test_contributing_has_the_component_bullet_line(self):
        lines = (REPO_ROOT / "CONTRIBUTING.md").read_text().splitlines()
        bullets = [l for l in lines if l.startswith("- `operator_session/`")]
        self.assertEqual(len(bullets), 1, lines)
        self.assertIn("one provider call per execute invocation", bullets[0])
        self.assertNotIn("exactly once", bullets[0])

    def test_contributing_compile_line_names_the_package(self):
        # Round-3 finding 2 / ruling F3(b) second half: the compile
        # echo line is derived the way test_ci_compiles_the_new_packages
        # derives the ci.yml line — the LAST line containing the compile
        # module name — so neither the token-anywhere tuple nor the
        # bullet anchor can stand in for it.
        needle = "py_" + "compile"
        lines = (REPO_ROOT / "CONTRIBUTING.md").read_text().splitlines()
        compile_line = [l for l in lines if needle in l][-1]
        self.assertTrue(compile_line.startswith("python3 -m " + needle))
        self.assertIn("operator_session/*.py", compile_line.split())


class TelegramAdapterWiringTests(unittest.TestCase):
    """Wiring: the Telegram adapter selects the session as specified."""

    def setUp(self):
        from telegram_operator import adapter as adapter_module
        from telegram_operator import config, state
        self.adapter_module = adapter_module
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = state.StateStore(self.tmp.name)
        self.api = object()
        self.config = config.AdapterConfig(
            bot_token="T", allowed_user_ids=(42,), repository="/resolved/repo"
        )

    def build(self, **kwargs):
        with patch("codex_gateway.gateway.build_request") as build, patch(
            "codex_gateway.gateway.submit"
        ) as submit, patch(
            "codex_gateway.codex_adapter.run_codex_turn"
        ) as run_turn, patch("subprocess.run") as run:
            adapter = self.adapter_module.Adapter(
                self.config, self.store, self.api, **kwargs
            )
        for spy in (build, submit, run_turn, run):
            self.assertEqual(spy.call_count, 0)
        self.assertFalse(hasattr(adapter, "_submit"))
        self.assertFalse(hasattr(adapter, "_build_request"))
        return adapter

    def test_no_injection_selects_the_codex_session(self):
        adapter = self.build()
        self.assertIsInstance(adapter._session, CodexOperatorSession)

    def test_injected_pair_selects_a_function_session_over_the_pair(self):
        builder, submit = BuilderSpy(), SubmitSpy(result="R")
        adapter = self.build(submit_fn=submit, build_request_fn=builder)
        self.assertIsInstance(adapter._session, FunctionOperatorSession)
        prepared = adapter._session.prepare("hi", "/resolved/repo")
        self.assertIs(prepared.request, builder.built[0])
        self.assertEqual(adapter._session.execute(prepared), "R")
        self.assertIs(submit.calls[0], builder.built[0])

    def test_each_half_of_a_partial_injection_defaults_independently(self):
        submit = SubmitSpy(result="R")
        adapter = self.build(submit_fn=submit)
        self.assertIsInstance(adapter._session, FunctionOperatorSession)
        self.assertIs(
            adapter._session._build_request_fn,
            self.adapter_module.gateway_build_request,
        )
        self.assertIs(adapter._session._submit_fn, submit)
        # Behavioral: the gateway builder really runs for this half.
        prepared = adapter._session.prepare("hi", self.tmp.name)
        self.assertIsInstance(prepared.request, contract.GatewayRequest)
        self.assertEqual(adapter._session.execute(prepared), "R")
        self.assertIs(submit.calls[0], prepared.request)

        builder = BuilderSpy()
        adapter = self.build(build_request_fn=builder)
        self.assertIsInstance(adapter._session, FunctionOperatorSession)
        self.assertIs(adapter._session._build_request_fn, builder)
        self.assertIs(
            adapter._session._submit_fn, self.adapter_module.gateway_submit
        )

    def test_explicit_operator_session_wins_over_injected_callables(self):
        explicit = FunctionOperatorSession(BuilderSpy(), SubmitSpy())
        adapter = self.build(
            operator_session=explicit,
            submit_fn=SubmitSpy(), build_request_fn=BuilderSpy(),
        )
        self.assertIs(adapter._session, explicit)

    def test_no_selection_selects_the_codex_session_not_pi(self):
        from operator_session.pi import PiOperatorSession
        adapter = self.build()
        self.assertIsInstance(adapter._session, CodexOperatorSession)
        self.assertNotIsInstance(adapter._session, PiOperatorSession)

    def test_explicit_pi_selection_selects_the_pi_session(self):
        from operator_session import pi as pi_module
        explicit = pi_module.PiOperatorSession()
        with patch.object(pi_module, "run_process") as run_process:
            adapter = self.build(operator_session=explicit)
        self.assertIs(adapter._session, explicit)
        self.assertIsInstance(adapter._session, pi_module.PiOperatorSession)
        self.assertEqual(run_process.call_count, 0)

    def test_codex_stays_explicitly_selectable(self):
        explicit = CodexOperatorSession()
        adapter = self.build(operator_session=explicit)
        self.assertIs(adapter._session, explicit)


if __name__ == "__main__":
    unittest.main()
