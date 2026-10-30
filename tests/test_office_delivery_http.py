"""The office delivery adapter's local interface (b2a, corrected for round 44): a bounded, authenticated, loopback-only JSON-over-HTTP adapter over office_delivery.adapter, STARTED BY
THE HUMAN like the request panel (didelivery_office.py), and never by the office server, which only READS its discovery file.

NO SOCKET IS OPENED HERE. Every decision lives in PURE functions, in two stages:
  * admit(token, port, method, path, headers): everything decidable from the request line and headers (HEAD, access, framing, the route and method, the content type, and a length
    converted without raising). It returns how many body bytes to read: ZERO on every refusal, with the refusal attached. So NO BODY BYTE IS READ BEFORE A REQUEST IS ADMITTED
    (round 44, finding 1).
  * respond(office_adapter, plan, body): the body's own checks and the one adapter call.
  * handle(...) is only the two composed, for a body already in hand; it decides nothing itself (pinned).
The request handler class only carries bytes: it ALWAYS calls admit, ALWAYS reads that many bytes, ALWAYS calls respond. Tests pin that it holds no decision, and that order.
THE STATED LIMITS, exactly: no test exercises the socket-to-handler path, so a defect in the wrapper's request reading or response writing would not be caught; and the number of
concurrent requests is not bounded.

THE RULE these tests carry (round 44): every input validator must be total over its input domain, and each test supplies input built to break THE CHECK (a non-ASCII digit, a length
too long to convert, nesting deep enough to exhaust the stack, a bearer that cannot be encoded), not merely input with a wrong value.

What this pins:
  * HEAD: refused first, 405 with NO body, whoever sends it; it reads nothing and calls nothing.
  * ACCESS, before any body byte: Host exactly 127.0.0.1:<port>; Origin absent or exactly http://127.0.0.1:<port>; exactly one "Authorization: Bearer <token>", compared in constant time.
  * FRAMING, on EVERY route, before any adapter call: a Transfer-Encoding is refused (round 44, finding 4). It is not read as a zero length.
  * LENGTHS: only ASCII digits are a length, converted without raising however long (round 44, finding 2).
  * NESTING: bounded without recursion; nesting past the stack refuses as malformed, never raises (round 44, finding 3). A size bound is not a depth bound.
  * THE CLOSED ROUTE SET: GET /healthz, GET /deliveries/<prd-id>/status, POST /deliveries/<prd-id>/advance-once. Anything else is 404 or 405. One adapter call per route.
  * ANSWERS: a domain refusal is 200 with ok false and its problem; a transport problem is 4xx; an adapter exception is ok false with its problem code (or a fixed word), never its message.
  * LOOPBACK ONLY, a token from `secrets` only, and the bound constants value-pinned in tests/test_workflow_authority.py's registry (read from its source, never imported).
"""

import ast
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from office_delivery import http_adapter as web  # noqa: E402

HTTP_FILE = REPO_ROOT / "office_delivery" / "http_adapter.py"
ENTRY_FILE = REPO_ROOT / "didelivery_office.py"
TOKEN = "t" * 64
PORT = 43210
DELIVERY = "prd-0123456789abcdef01234567"
CONFIRMATION = {"delivery_id": DELIVERY, "revision": 1, "candidate_identity_digest_sha256": "c" * 64, "step": "COMMIT"}
STATUS = "/deliveries/%s/status" % DELIVERY
ADVANCE = "/deliveries/%s/advance-once" % DELIVERY
CHUNKED = ("Transfer-Encoding", "chunked")
DECISIONS = tuple(kind for kind in (ast.If, ast.IfExp, ast.Try, ast.While, ast.BoolOp, ast.Compare, ast.Raise, ast.With,
                                    getattr(ast, "Match", None), getattr(ast, "TryStar", None)) if kind is not None)


class FakeAdapter(object):
    def __init__(self, status=None, advance=None, error=None):
        self.calls = []
        self.status_answer = {"delivery": {"delivery_id": DELIVERY}, "office": {}} if status is None else status
        self.advance_answer = {"advanced": True, "step": "COMMIT", "receipt": {"receipt_id": "r"}} if advance is None else advance
        self.error = error

    def status(self, delivery_id):
        self.calls.append(("status", delivery_id))
        if self.error is not None:
            raise self.error
        return self.status_answer

    def advance_once(self, delivery_id, confirmation):
        self.calls.append(("advance_once", delivery_id, confirmation))
        if self.error is not None:
            raise self.error
        return self.advance_answer


def headers(token=TOKEN, host="127.0.0.1:%d" % PORT, origin=None, extra=()):
    out = [("Host", host)]
    if token is not None:
        out.append(("Authorization", "Bearer " + token))
    if origin is not None:
        out.append(("Origin", origin))
    return out + list(extra)


def post_headers(body, **kw):
    return headers(extra=[("Content-Type", "application/json"), ("Content-Length", str(len(body)))], **kw)


def advance_headers(length):
    return headers(extra=[("Content-Type", "application/json"), ("Content-Length", length)])


def call(method, path, hdrs, body=b"", adapter=None):
    adapter = FakeAdapter() if adapter is None else adapter
    status, payload = web.handle(adapter, TOKEN, PORT, method, path, hdrs, body)
    return status, payload, adapter


BODY = json.dumps(CONFIRMATION).encode("utf-8")
REFUSED_FRAMING = (400, {"ok": False, "problem": "unsupported_framing"}, [])
# Each is built to break a length CHECK: a digit that is not ASCII (first, since int() raises on it), whitespace, a sign, other notations, nothing, and lengths too long to convert.
REFUSED_LENGTHS = (("²", 400, "content_length"), ("１２", 400, "content_length"), ("12 ", 400, "content_length"), (" 12", 400, "content_length"),
                   ("+12", 400, "content_length"), ("-1", 400, "content_length"), ("1e3", 400, "content_length"), ("0x10", 400, "content_length"),
                   ("", 400, "content_length"), ("9" * 40, 413, "body_too_large"), ("9" * 5000, 413, "body_too_large"))


class AccessTests(unittest.TestCase):
    def test_a_missing_wrong_or_repeated_bearer_is_401_and_reaches_no_adapter(self):
        for hdrs in (headers(token=None), headers(token="x" * 64), headers(extra=[("Authorization", "Bearer " + TOKEN)]),
                     [("Host", "127.0.0.1:%d" % PORT), ("Authorization", "Basic " + TOKEN)]):
            status, payload, adapter = call("GET", "/healthz", hdrs)
            self.assertEqual((status, payload["ok"], payload["problem"]), (401, False, "unauthorized"), hdrs)
            self.assertEqual(adapter.calls, [])

    def test_a_bearer_that_cannot_be_encoded_is_401_and_never_raises(self):
        status, payload, adapter = call("GET", "/healthz", headers(token="\ud800" + "t" * 63))
        self.assertEqual((status, payload, adapter.calls), (401, {"ok": False, "problem": "unauthorized"}, []))

    def test_a_host_other_than_the_bound_loopback_address_is_403(self):
        for host in ("localhost:%d" % PORT, "127.0.0.1:1", "example.test", ""):
            status, payload, _ = call("GET", "/healthz", headers(host=host))
            self.assertEqual((status, payload["problem"]), (403, "host_refused"), host)

    def test_an_origin_other_than_the_bound_loopback_origin_is_403_and_an_absent_one_is_allowed(self):
        status, payload, _ = call("GET", "/healthz", headers(origin="http://evil.test"))
        self.assertEqual((status, payload["problem"]), (403, "origin_refused"))
        self.assertEqual(call("GET", "/healthz", headers(origin="http://127.0.0.1:%d" % PORT))[0], 200)
        self.assertEqual(call("GET", "/healthz", headers())[0], 200)


class AdmitBeforeBodyTests(unittest.TestCase):
    """Round 44, finding 1: no body byte is read before the request is admitted."""

    def test_every_refusal_admit_makes_reads_ZERO_body_bytes_even_when_a_body_is_declared(self):
        declared = [("Content-Type", "application/json"), ("Content-Length", "4000")]
        for method, path, hdrs in (
            ("POST", ADVANCE, headers(token=None, extra=declared)),
            ("POST", ADVANCE, headers(token="x" * 64, extra=declared)),
            ("POST", ADVANCE, headers(host="evil.test", extra=declared)),
            ("POST", ADVANCE, headers(origin="http://evil.test", extra=declared)),
            ("POST", ADVANCE, headers(extra=declared + [CHUNKED])),
            ("POST", "/deliveries/%s/revoke" % DELIVERY, headers(extra=declared)),
            ("PUT", ADVANCE, headers(extra=declared)),
        ):
            plan = web.admit(TOKEN, PORT, method, path, hdrs)
            self.assertEqual(plan["read"], 0, (method, path, hdrs))
            self.assertIsNotNone(plan["refusal"], (method, path, hdrs))

    def test_an_admitted_advance_reads_exactly_its_declared_length_and_nothing_else_reads_any(self):
        self.assertEqual(web.admit(TOKEN, PORT, "POST", ADVANCE, post_headers(BODY))["read"], len(BODY))
        self.assertEqual(web.admit(TOKEN, PORT, "GET", STATUS, headers())["read"], 0)
        self.assertEqual(web.admit(TOKEN, PORT, "GET", "/healthz", headers())["read"], 0)


class FramingTests(unittest.TestCase):
    """Round 44, finding 4: unsupported framing refuses on EVERY route, before any adapter call. It is not a zero length."""

    def test_healthz_with_chunked_framing_is_refused(self):
        status, payload, adapter = call("GET", "/healthz", headers(extra=[CHUNKED]))
        self.assertEqual((status, payload, adapter.calls), REFUSED_FRAMING)

    def test_status_with_chunked_framing_is_refused_before_the_adapter_is_called(self):
        status, payload, adapter = call("GET", STATUS, headers(extra=[CHUNKED]))
        self.assertEqual((status, payload, adapter.calls), REFUSED_FRAMING)

    def test_advance_once_with_chunked_framing_is_refused_as_framing_and_not_as_a_length(self):
        status, payload, adapter = call("POST", ADVANCE, headers(extra=[("Content-Type", "application/json"), CHUNKED]))
        self.assertEqual((status, payload, adapter.calls), REFUSED_FRAMING)


class LengthTotalityTests(unittest.TestCase):
    """Round 44, finding 2: a length is ASCII digits only, converted without raising however long; anything else REFUSES."""

    def test_a_length_that_is_not_plain_ascii_digits_within_the_bound_refuses_and_never_raises(self):
        for length, expected_status, problem in REFUSED_LENGTHS:
            status, payload, adapter = call("POST", ADVANCE, advance_headers(length), b"{}")
            self.assertEqual((status, payload, adapter.calls), (expected_status, {"ok": False, "problem": problem}, []), repr(length[:12]))

    def test_admit_reads_nothing_for_any_such_length_and_reads_a_long_zero_padded_one_by_its_value(self):
        for length, expected_status, problem in REFUSED_LENGTHS:
            plan = web.admit(TOKEN, PORT, "POST", ADVANCE, advance_headers(length))
            self.assertEqual((plan["read"], plan["refusal"]), (0, (expected_status, {"ok": False, "problem": problem})), repr(length[:12]))
        self.assertEqual(web.admit(TOKEN, PORT, "POST", ADVANCE, advance_headers("0" * 5000 + "2"))["read"], 2)

    def test_status_with_a_non_ascii_length_refuses_without_raising_in_either_stage(self):
        hdrs = headers(extra=[("Content-Length", "²")])
        plan = web.admit(TOKEN, PORT, "GET", STATUS, hdrs)
        self.assertEqual((plan["read"], plan["refusal"]), (0, (400, {"ok": False, "problem": "body_not_allowed"})))
        status, payload, adapter = call("GET", STATUS, hdrs)
        self.assertEqual((status, payload, adapter.calls), (400, {"ok": False, "problem": "body_not_allowed"}, []))


class NestingTotalityTests(unittest.TestCase):
    """Round 44, finding 3: nesting is bounded WITHOUT recursion; a body nested past the stack refuses as malformed, never raises."""

    def test_nesting_built_to_exhaust_the_stack_is_refused_as_malformed_and_never_raises(self):
        for depth in (600, 2000):
            body = b'{"a":' + b"[" * depth + b"]" * depth + b"}"
            self.assertLessEqual(len(body), web.MAX_BODY_BYTES, depth)
            status, payload, adapter = call("POST", ADVANCE, post_headers(body), body)
            self.assertEqual((status, payload, adapter.calls), (400, {"ok": False, "problem": "malformed_body"}, []), depth)

    def test_nesting_at_the_bound_is_accepted_and_one_past_it_is_refused(self):
        at_bound = json.dumps({"a": {"b": {"c": {}}}}).encode()
        past = json.dumps({"a": {"b": {"c": {"d": {}}}}}).encode()
        self.assertEqual(call("POST", ADVANCE, post_headers(at_bound), at_bound)[0], 200)
        self.assertEqual(call("POST", ADVANCE, post_headers(past), past)[1]["problem"], "malformed_body")


class RouteTests(unittest.TestCase):
    def test_healthz_answers_ok_and_calls_no_adapter(self):
        status, payload, adapter = call("GET", "/healthz", headers())
        self.assertEqual((status, payload, adapter.calls), (200, {"ok": True}, []))

    def test_status_calls_the_adapter_exactly_once_and_returns_its_answer_unchanged(self):
        status, payload, adapter = call("GET", STATUS, headers())
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True, "status": adapter.status_answer})
        self.assertEqual(adapter.calls, [("status", DELIVERY)])

    def test_advance_once_hands_the_parsed_confirmation_to_the_adapter_exactly_once(self):
        status, payload, adapter = call("POST", ADVANCE, post_headers(BODY), BODY)
        self.assertEqual((status, payload["ok"], payload["result"]), (200, True, adapter.advance_answer))
        self.assertEqual(adapter.calls, [("advance_once", DELIVERY, CONFIRMATION)])

    def test_no_route_outside_the_closed_set_exists(self):
        for method, path in (("GET", "/"), ("GET", "/deliveries"), ("POST", "/deliveries/%s/advance" % DELIVERY), ("POST", "/deliveries/%s/revoke" % DELIVERY),
                             ("GET", STATUS + "?x=1"), ("GET", "/deliveries/PRD-0123456789abcdef01234567/status"),
                             ("GET", "/deliveries/prd-0123/status"), ("GET", "/deliveries/../status"), ("GET", "/healthz/")):
            status, payload, adapter = call(method, path, headers())
            self.assertEqual((status, payload["problem"]), (404, "not_found"), path)
            self.assertEqual(adapter.calls, [])

    def test_a_known_route_with_the_wrong_method_is_405(self):
        for method, path in (("POST", "/healthz"), ("POST", STATUS), ("GET", ADVANCE), ("DELETE", STATUS), ("PUT", ADVANCE)):
            status, payload, adapter = call(method, path, headers())
            self.assertEqual((status, payload["problem"]), (405, "method_not_allowed"), (method, path))
            self.assertEqual(adapter.calls, [])

    def test_HEAD_is_405_with_NO_body_decided_by_admit_itself_whoever_sends_it(self):
        for path, hdrs in (("/healthz", headers()), (STATUS, headers()), (ADVANCE, headers()), ("/nowhere", headers()), (STATUS, headers(token=None))):
            plan = web.admit(TOKEN, PORT, "HEAD", path, hdrs)
            self.assertEqual((plan["read"], plan["refusal"]), (0, (405, None)), path)
            status, payload, adapter = call("HEAD", path, hdrs)
            self.assertEqual((status, payload, adapter.calls, web.encode(payload)), (405, None, [], b""), path)


class BodyTests(unittest.TestCase):
    def assert_refused(self, hdrs, body, expected_status, problem):
        status, payload, adapter = call("POST", ADVANCE, hdrs, body)
        self.assertEqual((status, payload["ok"], payload["problem"]), (expected_status, False, problem), (hdrs, body))
        self.assertEqual(adapter.calls, [])

    def test_the_body_must_be_declared_json(self):
        self.assert_refused(headers(extra=[("Content-Length", str(len(BODY)))]), BODY, 415, "content_type")
        self.assert_refused(headers(extra=[("Content-Type", "text/plain"), ("Content-Length", str(len(BODY)))]), BODY, 415, "content_type")

    def test_the_length_must_be_exact_single_and_within_the_bound(self):
        for length in (None, "abc", "-1", str(len(BODY) + 1), str(len(BODY) - 1)):
            extra = [("Content-Type", "application/json")] + ([] if length is None else [("Content-Length", length)])
            self.assert_refused(headers(extra=extra), BODY, 400, "content_length")
        self.assert_refused(headers(extra=[("Content-Type", "application/json"), ("Content-Length", str(len(BODY))), ("Content-Length", str(len(BODY)))]), BODY, 400, "content_length")
        big = b"{" + b" " * web.MAX_BODY_BYTES + b"}"
        self.assert_refused(post_headers(big), big, 413, "body_too_large")

    def test_the_body_must_be_one_json_object_with_no_duplicate_key_no_nan_and_bounded_nesting(self):
        deep = json.dumps({"a": {"b": {"c": {"d": {"e": {}}}}}}).encode()
        for body in (b"not json", b"[1, 2]", b'{"step": "COMMIT", "step": "PUSH"}', b'{"revision": NaN}', b"\xff\xfe", b"\xef\xbb\xbf{}", deep):
            self.assert_refused(post_headers(body), body, 400, "malformed_body")

    def test_status_takes_no_body(self):
        status, payload, adapter = call("GET", STATUS, headers(extra=[("Content-Length", "2")]), b"{}")
        self.assertEqual((status, payload["problem"], adapter.calls), (400, "body_not_allowed", []))


class AnswerTests(unittest.TestCase):
    def test_a_domain_refusal_is_200_with_ok_false_and_its_problem(self):
        refusal = {"advanced": False, "refusal": "guard_refused", "detail": "x", "step": None, "receipt": None}
        status, payload, _ = call("POST", ADVANCE, post_headers(BODY), BODY, FakeAdapter(advance=refusal))
        self.assertEqual((status, payload["ok"], payload["problem"], payload["result"]), (200, False, "guard_refused", refusal))

    def test_an_adapter_exception_answers_ok_false_with_its_problem_code_and_never_its_message(self):
        class Coded(Exception):
            problem = "pr_delivery_missing_record"
        status, payload, _ = call("GET", STATUS, headers(), adapter=FakeAdapter(error=Coded("secret detail /Users/someone")))
        self.assertEqual((status, payload), (200, {"ok": False, "problem": "pr_delivery_missing_record"}))
        status, payload, _ = call("GET", STATUS, headers(), adapter=FakeAdapter(error=RuntimeError("secret detail")))
        self.assertEqual((status, payload), (200, {"ok": False, "problem": "adapter_error"}))


class PureHelperTests(unittest.TestCase):
    def test_encode_is_deterministic_json_none_is_no_body_and_response_headers_state_the_length(self):
        data = web.encode({"ok": True, "b": 1, "a": 2})
        self.assertEqual(data, b'{"a":2,"b":1,"ok":true}')
        self.assertEqual(dict(web.response_headers(data)),
                         {"Content-Type": "application/json", "Content-Length": str(len(data)), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        self.assertEqual(web.encode(None), b"")
        self.assertEqual(dict(web.response_headers(b""))["Content-Length"], "0")

    def test_only_the_loopback_constant_may_be_bound(self):
        self.assertEqual(web.LOOPBACK_HOST, "127.0.0.1")
        web.require_loopback("127.0.0.1")
        for host in ("0.0.0.0", "::1", "localhost", "192.168.1.2", ""):
            with self.assertRaises(web.NotLoopbackError):
                web.require_loopback(host)


def _tree(path):
    return ast.parse(path.read_text())


def _calls(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            yield f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else type(f).__name__


def _function(name):
    return [f for f in _tree(HTTP_FILE).body if isinstance(f, ast.FunctionDef) and f.name == name][0]


def _wrapper():
    return [c for c in _tree(HTTP_FILE).body if isinstance(c, ast.ClassDef) and c.name == "_RequestHandler"][0]


class PositivePinTests(unittest.TestCase):
    def test_the_route_set_is_closed_and_named(self):
        self.assertEqual(web.ROUTES, (("GET", "/healthz"), ("GET", "/deliveries/<delivery-id>/status"), ("POST", "/deliveries/<delivery-id>/advance-once")))

    def test_the_server_binds_only_the_loopback_constant(self):
        binds = [n for n in ast.walk(_tree(HTTP_FILE)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_Server"]
        self.assertEqual(len(binds), 1)
        address = binds[0].args[0]
        self.assertTrue(isinstance(address, ast.Tuple) and isinstance(address.elts[0], ast.Name) and address.elts[0].id == "LOOPBACK_HOST")

    def test_the_token_comes_only_from_secrets_and_never_from_the_environment_or_an_argument(self):
        tree = _tree(HTTP_FILE)
        token_sources = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "token_hex"]
        self.assertEqual(len(token_sources), 1)
        self.assertEqual(token_sources[0].func.value.id, "secrets")
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse(names & {"environ", "getenv", "putenv", "argv"})
        options = [n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument"]
        self.assertEqual(sorted(options), ["--discovery-file", "--port"])

    def test_the_module_imports_no_process_raw_socket_terminal_network_client_or_delivery_module(self):
        roots = set()
        for node in ast.walk(_tree(HTTP_FILE)):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                roots.add((node.module or "").split(".")[0])
        self.assertFalse(roots & {"subprocess", "socket", "pty", "urllib", "requests", "pr_delivery", "multiprocessing", "os"}, roots)

    def test_exactly_one_adapter_call_per_route_in_respond(self):
        adapter_calls = [n.func.attr for n in ast.walk(_function("respond")) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                         and isinstance(n.func.value, ast.Name) and n.func.value.id == "office_adapter"]
        self.assertEqual(sorted(adapter_calls), ["advance_once", "status"])

    def test_handle_only_composes_admit_and_respond_and_decides_nothing_itself(self):
        handle = _function("handle")
        self.assertEqual([type(n).__name__ for n in ast.walk(handle) if isinstance(n, DECISIONS)], [])
        self.assertEqual(set(_calls(handle)), {"admit", "respond"})

    def test_THE_WRAPPER_HOLDS_NO_DECISION_it_only_carries_bytes_between_the_socket_and_the_pure_functions(self):
        wrapper = _wrapper()
        self.assertEqual([type(n).__name__ for n in ast.walk(wrapper) if isinstance(n, DECISIONS)], [])
        allowed = {"list", "items", "admit", "read", "respond", "encode", "send_response", "response_headers", "send_header", "end_headers", "write"}
        self.assertEqual(set(_calls(wrapper)) - allowed, set())

    def test_THE_WRAPPER_ADMITS_BEFORE_IT_READS_and_reads_exactly_what_admit_returns(self):
        serve = [f for f in _wrapper().body if isinstance(f, ast.FunctionDef) and f.name == "_serve"][0]
        order = [name for statement in serve.body for name in _calls(statement) if name in ("admit", "read", "respond")]
        self.assertEqual(order, ["admit", "read", "respond"])
        reads = [n for n in ast.walk(serve) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "read"]
        argument = reads[0].args[0]
        self.assertTrue(isinstance(argument, ast.Subscript) and isinstance(argument.value, ast.Name) and argument.value.id == "plan"
                        and isinstance(argument.slice, ast.Constant) and argument.slice.value == "read", ast.dump(argument))

    def test_every_mapped_method_including_HEAD_goes_to_the_one_serving_path(self):
        mapped = set()
        for node in _wrapper().body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id == "_serve":
                mapped.update(t.id for t in node.targets if isinstance(t, ast.Name))
        self.assertEqual(mapped, {"do_GET", "do_POST", "do_PUT", "do_DELETE", "do_PATCH", "do_OPTIONS", "do_HEAD"})

    def test_the_entry_script_only_runs_the_adapters_main(self):
        tree = _tree(ENTRY_FILE)
        imports = [(n.module, [a.name for a in n.names]) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertEqual(imports, [("office_delivery.http_adapter", ["main"])])


class BoundConstantRegistryTests(unittest.TestCase):
    def test_every_bound_constant_here_is_value_pinned_in_the_registry(self):
        def convention(name):
            return not name.startswith("_") and (name.startswith(("MAX_", "CANONICAL_")) or name.endswith(("_SECONDS", "_CHARS", "_CHUNKS")))
        derived = {}
        for node in ast.walk(_tree(HTTP_FILE)):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and convention(target.id):
                        derived[target.id] = ast.literal_eval(node.value)
        self.assertTrue(derived, "the module declares its bounds by the registry's rule")
        registry = None
        for node in ast.walk(_tree(REPO_ROOT / "tests" / "test_workflow_authority.py")):
            if isinstance(node, ast.ClassDef) and node.name == "BoundConstantPinTests":
                for item in node.body:
                    if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PINNED" for t in item.targets):
                        registry = ast.literal_eval(item.value)
        self.assertIsNotNone(registry)
        self.assertEqual(registry.get("office_delivery/http_adapter.py"), derived)


if __name__ == "__main__":
    unittest.main()
