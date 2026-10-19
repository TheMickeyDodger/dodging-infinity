"""The passive loopback HTTP adapter (``target_runtime.catalog_http``).

Real HTTP over real loopback sockets, against the REAL catalog owner over
isolated stores and the FENCED fake runtime of ``test_runtime_snapshot``
(the engine listing is never reached), or against a stub owner returning a
real owner answer where the serve time itself is the subject. The bearer
token is generated per test and never printed: no assertion compares it in
a way whose failure message would show it.
"""

import copy
import http.client
import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission_sources import contract  # noqa: E402
from mission_sources import query as query_module  # noqa: E402
from mission_sources import roster  # noqa: E402
from target_runtime import catalog  # noqa: E402
from target_runtime import catalog_http  # noqa: E402
from target_runtime import runtime_snapshot as snapshot_module  # noqa: E402
import test_catalog as catalog_tests  # noqa: E402
import test_runtime_snapshot as snapshot_tests  # noqa: E402
from test_mission_sources import NOW  # noqa: E402

FRESHNESS_HEADERS = (catalog_http.HEADER_FRESHNESS_HEALTH,
                     catalog_http.HEADER_FRESHNESS_PROBLEM)

#: The REAL adapter process (``catalog_http.main``: discovery file, signals,
#: the one owner, the real query), with a fixed clock and the FAKE listing.
ADAPTER_CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from target_runtime import catalog_http
sys.exit(catalog_http.main(json.loads(sys.argv[2]),
                           clock=lambda: int(sys.argv[3]),
                           listing_argv=json.loads(sys.argv[4])))
'''


class StubOwner(object):
    """Returns ONE fixed answer object (a real owner answer) and records
    every call; the adapter must hand it over exactly."""

    def __init__(self, answer):
        self.answer = answer
        self.waits = []
        self.diagnosed = 0

    def request(self, wait_seconds):
        self.waits.append(wait_seconds)
        return self.answer

    def diagnostics(self):
        self.diagnosed += 1
        return {"stub": True}


class ForeignPeerServer(catalog_http.LoopbackCatalogServer):
    """Hands every connection to the handler as if from a non-loopback
    peer (a real bound socket cannot produce one)."""

    def finish_request(self, request, client_address):
        self.RequestHandlerClass(request, ("192.0.2.7", 40000), self)


class HttpFixture(catalog_tests.CatalogFixture):

    def setUp(self):
        super(HttpFixture, self).setUp()
        self.token = secrets.token_urlsafe(32)

    def serve(self, owner, clock=None, server_class=None):
        server = (server_class or catalog_http.LoopbackCatalogServer)(
            owner, self.token, clock or self.clock, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(30)
        self.addCleanup(stop)
        return server

    def spy(self, owner):
        """Record every answer object the owner returns to the adapter."""
        answers = []
        real = owner.request

        def request(wait_seconds):
            answers.append(real(wait_seconds))
            return answers[-1]
        owner.request = request
        return answers

    def request(self, server, path, method="GET", headers=(), auth=True,
                host=True):
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        try:
            conn.putrequest(method, path, skip_host=True,
                            skip_accept_encoding=True)
            if host:
                conn.putheader("Host", "127.0.0.1:%d" % port)
            if auth:
                conn.putheader("Authorization", "Bearer " + self.token)
            for name, value in headers:
                conn.putheader(name, value)
            conn.endheaders()
            response = conn.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            conn.close()

    def raw(self, server, data):
        with socket.create_connection(
                ("127.0.0.1", server.server_address[1]), timeout=60) as sock:
            sock.sendall(data)
            chunks = []
            while True:
                try:
                    chunk = sock.recv(65536)
                except ConnectionResetError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)

    def header(self, headers, name):
        values = [v for k, v in headers if k.lower() == name.lower()]
        self.assertLessEqual(len(values), 1, name)
        return values[0] if values else None

    def assert_no_token(self, *texts):
        for text in texts:
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            self.assertFalse(self.token in text, "the bearer token leaked")

    def catalog_get(self, server, query="?wait_seconds=5"):
        status, headers, body = self.request(server, "/catalog" + query)
        self.assert_no_token(body, repr(headers))
        self.assertEqual(self.header(headers, "Content-Type"),
                         "application/json; charset=utf-8")
        for name, _value in headers:
            self.assertFalse(name.lower().startswith("access-control-"),
                             name)
            self.assertNotEqual(name.lower(), "set-cookie")
        return status, headers, body, json.loads(body)

    def assert_envelope(self, answer):
        self.assertEqual(set(answer), set(catalog.ANSWER_KEYS))
        self.assertEqual((answer["endpoint"], answer["endpoint_version"]),
                         (catalog.ENDPOINT, catalog.ENDPOINT_VERSION))

    def assert_unavailable_headers(self, headers, problem):
        self.assertEqual(self.header(headers,
                                     catalog_http.HEADER_FRESHNESS_HEALTH),
                         contract.HEALTH_UNAVAILABLE)
        self.assertEqual(self.header(headers,
                                     catalog_http.HEADER_FRESHNESS_PROBLEM),
                         problem)

    def assert_fresh_headers(self, headers):
        self.assertEqual(self.header(headers,
                                     catalog_http.HEADER_FRESHNESS_HEALTH),
                         contract.HEALTH_FRESH)
        self.assertIsNone(self.header(headers,
                                      catalog_http.HEADER_FRESHNESS_PROBLEM))

    def bound_store(self):
        """A running Mission whose herd root the fake listing observes."""
        state_dir = self.mission_state()
        mission_id, workspace = self.running_mission(
            state_dir, task_id=snapshot_tests.TASK)
        self.herd(root=workspace)
        self.register(source_id="mc", store=state_dir)
        return mission_id, workspace

    def serve_encoding(self, answer):
        """The bytes ``catalog.serve`` writes for ``answer``."""
        reader, writer = os.pipe()
        try:
            catalog._write_line(writer, answer)
            os.close(writer)
            writer = None
            return os.read(reader, 1 << 20)
        finally:
            os.close(reader)
            if writer is not None:
                os.close(writer)

    def real_answer(self):
        """One REAL refreshed owner answer over a bound fixture store."""
        _mission_id, workspace = self.bound_store()
        owner = self.endpoint([workspace], self.plan())
        answer = owner.request(5)
        self.assertEqual(answer["served"], catalog.SERVED_REFRESHED)
        self.assertIsNotNone(answer["response"])
        return answer


class CatalogStatusTests(HttpFixture):
    """EVERY catalog envelope is 200, each case on its own; the body is the
    owner's answer object, byte for byte as ``serve`` writes it."""

    def test_200_refreshed_with_a_fresh_response_unmodified(self):
        mission_id, workspace = self.bound_store()
        owner = self.endpoint([workspace], self.plan())
        answers = self.spy(owner)
        status, headers, body, answer = self.catalog_get(self.serve(owner))
        self.assertEqual(status, 200)
        self.assert_envelope(answer)
        self.assertEqual(answer["served"], catalog.SERVED_REFRESHED)
        self.assertEqual(body, self.serve_encoding(answers[0]))
        self.assertEqual(body, catalog_http.encode_answer(answers[0]))
        self.assert_fresh_headers(headers)
        [mission] = answer["response"]["missions"]
        self.assertEqual(mission["mission_ref"], "mc:" + mission_id)
        [member] = mission["members"]
        self.assertEqual(member["classification"], contract.MEMBER_PRESENT)
        # runtime_capture when a response IS given: refresh_budget present.
        self.assertTrue(answer["runtime_capture"]["available"])
        self.assertIn("refresh_budget", answer["runtime_capture"])

    def test_200_cached_is_returned_unchanged_never_restamped(self):
        _mission_id, workspace = self.bound_store()
        owner = self.endpoint([workspace], self.plan())
        answers = self.spy(owner)
        server = self.serve(owner)
        first = self.catalog_get(server)[3]
        self.clock.now = NOW + 3                 # wall time moves on
        self.ticks.t += 1                         # within the 5 s interval
        status, headers, body, cached = self.catalog_get(server,
                                                         "?wait_seconds=1")
        self.assertEqual(status, 200)
        self.assertEqual(cached["served"], catalog.SERVED_CACHED)
        self.assertEqual(body, self.serve_encoding(answers[1]))
        for key in catalog.ANSWER_KEYS:
            if key not in ("served", "retry_after_seconds"):
                self.assertEqual(cached[key], first[key], key)
        self.assertEqual(
            (cached["requested_at"], cached["response"]["generated_at"],
             cached["response"]["valid_until"]),
            (first["requested_at"], first["response"]["generated_at"],
             first["response"]["valid_until"]))
        self.assertEqual(cached["retry_after_seconds"], 4.0)
        self.assert_fresh_headers(headers)

    def test_cadence_boundary_is_exact_through_http(self):
        """HERMETIC, on the owner's injectable monotonic clock (no sleeping,
        no real clock): ``since`` just under the 5 s interval serves the
        held answer UNCHANGED (or throttles when none is held) and starts
        NOTHING; ``since`` exactly 5 s starts a genuine refresh.
        ``retry_after_seconds`` is the owner's rounded remainder."""
        interval = catalog.MIN_REFRESH_INTERVAL_SECONDS
        _mission_id, workspace = self.bound_store()
        queries = catalog_tests.CountingQuery()
        patcher = mock.patch.object(query_module, "query", queries)
        patcher.start()
        self.addCleanup(patcher.stop)
        owner = self.endpoint([workspace], self.plan())
        server = self.serve(owner)
        start = self.ticks.t
        first = self.catalog_get(server, "?wait_seconds=1")[3]
        self.assertEqual((first["served"], queries.starts),
                         (catalog.SERVED_REFRESHED, 1))
        for since in (0.001, 4.99, 4.999, 4.9999):
            self.ticks.t = start + since
            self.clock.now = NOW + 4               # wall time moves on
            status, headers, _body, cached = self.catalog_get(
                server, "?wait_seconds=1")
            self.assertEqual((status, cached["served"], queries.starts),
                             (200, catalog.SERVED_CACHED, 1), since)
            for key in catalog.ANSWER_KEYS:
                if key not in ("served", "retry_after_seconds"):
                    self.assertEqual(cached[key], first[key], key)
            self.assertEqual(
                (cached["requested_at"], cached["response"]["generated_at"],
                 cached["response"]["valid_until"]),
                (first["requested_at"], first["response"]["generated_at"],
                 first["response"]["valid_until"]))
            self.assertEqual(cached["retry_after_seconds"], round(
                interval - (self.ticks.t - start), 3))
            self.assertTrue(0 <= cached["retry_after_seconds"] <= interval)
            self.assert_fresh_headers(headers)
        self.ticks.t = start + interval              # exactly 5 s
        refreshed = self.catalog_get(server, "?wait_seconds=1")[3]
        self.assertEqual((refreshed["served"], queries.starts,
                          refreshed["requested_at"],
                          refreshed["retry_after_seconds"]),
                         (catalog.SERVED_REFRESHED, 2, NOW + 4, None))
        # A failed refresh never replaces the held answer: within its
        # interval the earlier, still-fresh answer is served, unchanged.
        queries.fail = True
        self.ticks.t = start + 2 * interval
        self.assertEqual(self.catalog_get(server)[3]["problem"],
                         catalog.PROBLEM_QUERY_FAILED)
        self.ticks.t = start + 3 * interval - 0.001
        held = self.catalog_get(server)[3]
        self.assertEqual((held["served"], held["requested_at"],
                          queries.starts),
                         (catalog.SERVED_CACHED, refreshed["requested_at"],
                          3))
        # With NO held answer (an owner whose only refresh failed), just
        # under the interval is throttled and starts nothing; exactly the
        # interval starts a refresh.
        failing = self.serve(self.endpoint())
        anchor = self.ticks.t
        self.assertEqual(self.catalog_get(failing)[3]["problem"],
                         catalog.PROBLEM_QUERY_FAILED)
        self.assertEqual(queries.starts, 4)
        self.ticks.t = anchor + interval - 0.001
        status, headers, _body, throttled = self.catalog_get(failing)
        self.assertEqual((status, throttled["problem"], throttled["response"],
                          throttled["served"], queries.starts),
                         (200, catalog.PROBLEM_REFRESH_THROTTLED, None,
                          catalog.SERVED_NONE, 4))
        self.assertEqual(throttled["retry_after_seconds"], round(
            interval - (self.ticks.t - anchor), 3))
        self.assert_unavailable_headers(headers,
                                        catalog.PROBLEM_REFRESH_THROTTLED)
        self.ticks.t = anchor + interval
        self.assertEqual(self.catalog_get(failing)[3]["problem"],
                         catalog.PROBLEM_QUERY_FAILED)
        self.assertEqual(queries.starts, 5)

    def test_200_refresh_throttled_with_no_response(self):
        self.register(source_id="mc", store=self.mission_state())
        owner = self.endpoint()
        server = self.serve(owner)
        with mock.patch.object(query_module, "query",
                               side_effect=ValueError("defect")):
            self.catalog_get(server)
        self.ticks.t += 1
        status, headers, _body, answer = self.catalog_get(server)
        self.assertEqual(status, 200)
        self.assert_envelope(answer)
        self.assertEqual((answer["problem"], answer["response"],
                          answer["served"]),
                         (catalog.PROBLEM_REFRESH_THROTTLED, None,
                          catalog.SERVED_NONE))
        self.assertEqual(answer["retry_after_seconds"], 4.0)
        self.assert_unavailable_headers(headers,
                                        catalog.PROBLEM_REFRESH_THROTTLED)

    def test_200_query_not_finished_within_wait(self):
        self.register(source_id="mc", store=self.mission_state())
        self.blocked_query()
        owner = self.endpoint()
        status, headers, _body, answer = self.catalog_get(
            self.serve(owner), "?wait_seconds=1")
        self.assertEqual(status, 200)
        self.assert_envelope(answer)
        self.assertEqual((answer["problem"], answer["response"]),
                         (catalog.PROBLEM_QUERY_PENDING, None))
        self.assert_unavailable_headers(headers, catalog.PROBLEM_QUERY_PENDING)

    def test_200_slot_occupied(self):
        self.register(source_id="mc", store=self.mission_state())
        self.blocked_query()
        owner = self.endpoint()
        server = self.serve(owner)
        self.catalog_get(server, "?wait_seconds=1")
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        status, headers, _body, answer = self.catalog_get(server,
                                                          "?wait_seconds=1")
        self.assertEqual(status, 200)
        self.assert_envelope(answer)
        self.assertEqual((answer["problem"], answer["response"],
                          answer["runtime_capture"]),
                         (catalog.PROBLEM_SLOT_OCCUPIED, None, None))
        self.assert_unavailable_headers(headers, catalog.PROBLEM_SLOT_OCCUPIED)

    def test_200_owner_shut_down(self):
        owner = self.endpoint()
        server = self.serve(owner)
        owner.shutdown()
        status, headers, _body, answer = self.catalog_get(server)
        self.assertEqual(status, 200)
        self.assert_envelope(answer)
        self.assertEqual((answer["problem"], answer["response"]),
                         (catalog.PROBLEM_OWNER_SHUT_DOWN, None))
        self.assert_unavailable_headers(headers,
                                        catalog.PROBLEM_OWNER_SHUT_DOWN)

    def test_200_query_failed_without_refresh_budget(self):
        _mission_id, workspace = self.bound_store()
        owner = self.endpoint([workspace], self.plan())
        with mock.patch.object(query_module, "query",
                               side_effect=ValueError("defect")):
            status, headers, _body, answer = self.catalog_get(
                self.serve(owner))
        self.assertEqual(status, 200)
        self.assert_envelope(answer)
        self.assertEqual((answer["problem"], answer["response"]),
                         (catalog.PROBLEM_QUERY_FAILED, None))
        # runtime_capture when NO response is given: configured, captured,
        # and refresh_budget ABSENT.
        self.assertTrue(answer["runtime_capture"]["configured"])
        self.assertNotIn("refresh_budget", answer["runtime_capture"])
        self.assert_unavailable_headers(headers, catalog.PROBLEM_QUERY_FAILED)

    def test_200_no_runtime_root_is_a_runtime_capture_problem(self):
        """``catalog_no_runtime_root`` is NOT an envelope problem: the
        response is given, served refreshed, and the problem is the
        runtime capture's."""
        self.register(source_id="mc", store=self.mission_state())
        owner = self.endpoint(listing_argv=self.plan(), discover=True)
        status, headers, _body, answer = self.catalog_get(self.serve(owner))
        self.assertEqual(status, 200)
        self.assert_envelope(answer)
        self.assertEqual((answer["problem"], answer["served"]),
                         (None, catalog.SERVED_REFRESHED))
        self.assertIsNotNone(answer["response"])
        capture = answer["runtime_capture"]
        self.assertEqual(set(capture), {
            "configured", "discovery", "registered_roots", "available",
            "problem", "detail", "observed_at", "roots", "refresh_budget"})
        self.assertEqual((capture["available"], capture["problem"],
                          capture["observed_at"], capture["roots"]),
                         (False, catalog.PROBLEM_NO_RUNTIME_ROOT, None, None))
        self.assert_fresh_headers(headers)

    def test_200_serve_time_non_fresh_is_withheld_as_nine_keys(self):
        """An absent registry is answered with a response the canonical
        rule reads ``unavailable``: withheld, response null, the freshness
        problem, served none; the other keys as the owner returned them."""
        owner = self.endpoint()
        answers = self.spy(owner)
        status, headers, body, answer = self.catalog_get(self.serve(owner))
        self.assertEqual(status, 200)
        [original] = answers
        self.assertIsNotNone(original["response"])
        self.assertEqual(original["served"], catalog.SERVED_REFRESHED)
        self.assertEqual(tuple(sorted(answer)), tuple(sorted(
            catalog.ANSWER_KEYS)))
        self.assertEqual((answer["response"], answer["problem"],
                          answer["served"]),
                         (None, contract.PROBLEM_REGISTRY_DEGRADED,
                          catalog.SERVED_NONE))
        for key in ("endpoint", "endpoint_version", "requested_at",
                    "runtime_capture", "retry_after_seconds"):
            self.assertEqual(answer[key], original[key], key)
        self.assertIn("withheld", answer["detail"])
        self.assertEqual(self.header(headers,
                                     catalog_http.HEADER_FRESHNESS_HEALTH),
                         contract.HEALTH_UNAVAILABLE)
        self.assertEqual(self.header(headers,
                                     catalog_http.HEADER_FRESHNESS_PROBLEM),
                         contract.PROBLEM_REGISTRY_DEGRADED)
        self.assertNotIn(b'"missions"', body)


class FreshnessBoundaryTests(HttpFixture):
    """Serve-time freshness on the canonical rule: fresh iff ``now <=
    valid_until``; the owner's answer is never modified."""

    def at(self, answer, now):
        owner = StubOwner(answer)
        server = self.serve(owner, clock=lambda: now)
        return self.catalog_get(server)

    def test_exactly_valid_until_is_fresh(self):
        answer = self.real_answer()
        before = copy.deepcopy(answer)
        valid_until = answer["response"]["valid_until"]
        status, headers, body, served = self.at(answer, valid_until)
        self.assertEqual(status, 200)
        self.assertEqual(served, before)
        self.assertEqual(body, self.serve_encoding(before))
        self.assert_fresh_headers(headers)
        self.assertEqual(answer, before)

    def test_one_second_past_valid_until_is_withheld_stale(self):
        answer = self.real_answer()
        before = copy.deepcopy(answer)
        valid_until = answer["response"]["valid_until"]
        status, headers, _body, served = self.at(answer, valid_until + 1)
        self.assertEqual(status, 200)
        self.assertEqual(set(served), set(catalog.ANSWER_KEYS))
        self.assertEqual((served["response"], served["problem"],
                          served["served"]),
                         (None, contract.PROBLEM_RESPONSE_EXPIRED,
                          catalog.SERVED_NONE))
        self.assertEqual(served["requested_at"], before["requested_at"])
        self.assertEqual(self.header(headers,
                                     catalog_http.HEADER_FRESHNESS_HEALTH),
                         contract.HEALTH_STALE)
        self.assertEqual(self.header(headers,
                                     catalog_http.HEADER_FRESHNESS_PROBLEM),
                         contract.PROBLEM_RESPONSE_EXPIRED)
        self.assertEqual(answer, before)          # the owner's object intact

    def test_a_future_generated_at_is_withheld_malformed(self):
        answer = self.real_answer()
        generated_at = answer["response"]["generated_at"]
        _status, headers, _body, served = self.at(answer, generated_at - 1)
        self.assertEqual((served["response"], served["problem"]),
                         (None, contract.PROBLEM_OBSERVED_IN_FUTURE))
        self.assertEqual(self.header(headers,
                                     catalog_http.HEADER_FRESHNESS_HEALTH),
                         contract.HEALTH_MALFORMED)

    def test_serve_time_answer_matches_the_canonical_rule(self):
        answer = self.real_answer()
        valid_until = answer["response"]["valid_until"]
        for now in (valid_until - 1, valid_until, valid_until + 1,
                    valid_until + 100):
            served, health, problem = catalog_http.serve_time_answer(answer,
                                                                     now)
            self.assertEqual((health, problem),
                             contract.response_freshness(answer["response"],
                                                         now))
            self.assertIs(served is answer, health == contract.HEALTH_FRESH)


class NullRepresentationTests(HttpFixture):

    def test_null_values_are_present_keys_and_the_problem_header_is_omitted(
            self):
        _mission_id, workspace = self.bound_store()
        owner = self.endpoint([workspace], self.plan())
        status, headers, body, answer = self.catalog_get(self.serve(owner))
        self.assertEqual(status, 200)
        self.assertEqual(sorted(answer), sorted(catalog.ANSWER_KEYS))
        for key in ("problem", "detail", "retry_after_seconds"):
            self.assertIn(key, answer)
            self.assertIsNone(answer[key])
            self.assertIn(('"%s": null' % key).encode(), body)
        self.assertNotIn(catalog_http.HEADER_FRESHNESS_PROBLEM.lower(),
                         [name.lower() for name, _value in headers])


class RequestValidationTests(HttpFixture):

    def test_wait_seconds_out_of_range_or_malformed_is_400(self):
        owner = StubOwner(None)
        server = self.serve(owner)
        for query in ("?wait_seconds=0", "?wait_seconds=0.0",
                      "?wait_seconds=-1", "?wait_seconds=31",
                      "?wait_seconds=30.001", "?wait_seconds=abc",
                      "?wait_seconds=1e1", "?wait_seconds=", "?wait_seconds",
                      "?wait_seconds=1&wait_seconds=2",
                      "?wait_seconds=1&x=1", "?x=1", "?wait_seconds=+1",
                      "?wait_seconds=0x10", "?wait_seconds=NaN"):
            status, headers, _body, answer = self.catalog_get(server, query)
            self.assertEqual(status, 400, query)
            self.assert_envelope(answer)
            self.assertEqual((answer["problem"], answer["response"]),
                             (catalog.PROBLEM_BAD_REQUEST, None), query)
            self.assert_unavailable_headers(headers,
                                            catalog.PROBLEM_BAD_REQUEST)
        self.assertEqual(owner.waits, [])          # nothing was started

    def test_valid_waits_reach_the_owner_as_numbers(self):
        answer = self.real_answer()
        owner = StubOwner(answer)
        server = self.serve(owner, clock=lambda: answer["response"][
            "generated_at"])
        for query in ("", "?wait_seconds=1", "?wait_seconds=30",
                      "?wait_seconds=0.5", "?wait_seconds=10"):
            self.assertEqual(self.catalog_get(server, query)[0], 200, query)
        self.assertEqual(owner.waits, [catalog.DEFAULT_WAIT_SECONDS, 1, 30,
                                       0.5, 10])
        self.assertEqual([type(w) for w in owner.waits],
                         [int, int, int, float, int])

    def test_a_request_body_is_refused_400_unread(self):
        owner = StubOwner(None)
        server = self.serve(owner)
        for name, value in (("Content-Length", "0"),
                            ("Transfer-Encoding", "chunked")):
            status, _headers, body = self.request(server, "/catalog",
                                                  headers=((name, value),))
            self.assertEqual(status, 400, name)
            self.assertEqual(json.loads(body), {"error": "bad_request"})
        self.assertEqual(owner.waits, [])

    def test_queries_on_other_routes_are_400(self):
        owner = StubOwner(None)
        server = self.serve(owner)
        for path in ("/healthz?x=1", "/diagnostics?wait_seconds=1"):
            self.assertEqual(self.request(server, path)[0], 400, path)
        self.assertEqual(owner.diagnosed, 0)

    def test_an_unsupported_http_version_is_400_never_5xx(self):
        server = self.serve(StubOwner(None))
        reply = self.raw(server, b"GET /catalog HTTP/2.0\r\nHost: x\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.0 400 "), reply[:40])

    def test_http_09_is_refused_with_a_status_line_even_when_admissible(self):
        """A two-word request line is still read with headers by the
        standard library; with a valid Host and bearer it must NOT receive a
        bare catalog body (no status line, no freshness headers)."""
        owner = StubOwner(None)
        server = self.serve(owner)
        port = server.server_address[1]
        reply = self.raw(server, (
            "GET /catalog\r\nHost: 127.0.0.1:%d\r\nAuthorization: Bearer %s"
            "\r\n\r\n" % (port, self.token)).encode())
        self.assertTrue(reply.startswith(b"HTTP/1.0 400 "), reply[:40])
        self.assertTrue(reply.endswith(b'{"error": "bad_request"}\n'))
        self.assertEqual(owner.waits, [])
        self.assert_no_token(reply)


class AccessBoundaryTests(HttpFixture):

    def assert_refused(self, owner, status, headers, body, code, error):
        self.assertEqual(status, code)
        self.assertEqual(json.loads(body), {"error": error})
        self.assertEqual(owner.waits, [])
        for name in FRESHNESS_HEADERS:
            self.assertIsNone(self.header(headers, name))

    def test_a_missing_or_wrong_bearer_is_401(self):
        owner = StubOwner(None)
        server = self.serve(owner)
        status, headers, body = self.request(server, "/catalog", auth=False)
        self.assert_refused(owner, status, headers, body, 401,
                            "bearer_token_required")
        self.assertEqual(self.header(headers, "WWW-Authenticate"), "Bearer")
        wrong = "Bearer " + secrets.token_urlsafe(32)
        for headers_sent in (
                (("Authorization", wrong),),
                (("Authorization", "Bearer " + self.token + "x"),),
                (("Authorization", "bearer " + self.token),),
                (("Authorization", "Basic " + self.token),),
                (("Authorization", self.token),),
                (("Authorization", "Bearer"),),
                (("Authorization", "Bearer " + self.token),
                 ("Authorization", "Bearer " + self.token))):
            status, headers, body = self.request(
                server, "/catalog", auth=False, headers=headers_sent)
            self.assert_refused(owner, status, headers, body, 401,
                                "bearer_token_required")
        for path in ("/healthz", "/diagnostics"):
            self.assertEqual(self.request(server, path, auth=False)[0], 401)
        self.assertEqual(owner.diagnosed, 0)

    def test_host_mismatch_is_403(self):
        owner = StubOwner(None)
        server = self.serve(owner)
        port = server.server_address[1]
        for hosts in ((), ("localhost:%d" % port,), ("127.0.0.1",),
                      ("127.0.0.1:%d" % (port + 1),), ("evil.example",),
                      ("127.0.0.1:%d" % port, "127.0.0.1:%d" % port)):
            status, headers, body = self.request(
                server, "/catalog", host=False,
                headers=[("Host", h) for h in hosts])
            self.assert_refused(owner, status, headers, body, 403,
                                "host_origin_or_peer_refused")

    def test_origin_mismatch_is_403_and_the_bound_origin_is_admitted(self):
        answer = self.real_answer()
        owner = StubOwner(answer)
        server = self.serve(owner, clock=lambda: answer["response"][
            "generated_at"])
        port = server.server_address[1]
        for origins in (("http://evil.example",), ("null",),
                        ("http://localhost:%d" % port,),
                        ("https://127.0.0.1:%d" % port,),
                        ("http://127.0.0.1:%d" % (port + 1),),
                        ("http://127.0.0.1:%d" % port,
                         "http://127.0.0.1:%d" % port)):
            status, headers, body = self.request(
                server, "/catalog", headers=[("Origin", o) for o in origins])
            self.assert_refused(owner, status, headers, body, 403,
                                "host_origin_or_peer_refused")
            for name, _value in headers:
                self.assertFalse(name.lower().startswith("access-control-"))
        status, headers, _body = self.request(
            server, "/catalog",
            headers=(("Origin", "http://127.0.0.1:%d" % port),))
        self.assertEqual(status, 200)
        for name, _value in headers:
            self.assertFalse(name.lower().startswith("access-control-"))

    def test_a_non_loopback_peer_is_403(self):
        owner = StubOwner(None)
        server = self.serve(owner, server_class=ForeignPeerServer)
        status, headers, body = self.request(server, "/catalog")
        self.assert_refused(owner, status, headers, body, 403,
                            "host_origin_or_peer_refused")

    def test_unknown_paths_are_404(self):
        owner = StubOwner(None)
        server = self.serve(owner)
        port = server.server_address[1]
        for path in ("/", "/catalog/", "/Catalog", "/catalog.json",
                     "//catalog", "/healthz/", "/register", "/unregister",
                     "/sources", "/query", "/serve", "/control", "/mcp",
                     "/tools/call", "/jarvis", "/dispatch", "/spawn",
                     "/prompt", "/deliver", "/mission", "/%63atalog",
                     "http://127.0.0.1:%d/catalog" % port, "*"):
            status, headers, body = self.request(server, path)
            self.assert_refused(owner, status, headers, body, 404,
                                "no_such_route")
        self.assertEqual(owner.diagnosed, 0)

    def test_a_double_slash_is_not_a_spelling_of_a_route(self):
        """Routing is on the request target EXACTLY as sent: the standard
        library (3.12+) rewrites a leading "//" in ``self.path``, which must
        never make "//catalog" (or "///healthz") reach a route."""
        owner = StubOwner(None)
        server = self.serve(owner)
        for path in ("//catalog", "///catalog", "//catalog?wait_seconds=1",
                     "//healthz", "//diagnostics"):
            status, headers, body = self.request(server, path)
            self.assert_refused(owner, status, headers, body, 404,
                                "no_such_route")
        self.assertEqual(owner.diagnosed, 0)

    def test_every_other_method_is_405_and_unread(self):
        owner = StubOwner(None)
        server = self.serve(owner)
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE",
                       "CONNECT", "FOO"):
            for path in ("/catalog", "/register", "/healthz"):
                status, headers, body = self.request(
                    server, path, method=method,
                    headers=(("Content-Length", "0"),))
                self.assert_refused(owner, status, headers, body, 405,
                                    "method_not_allowed")
                self.assertEqual(self.header(headers, "Allow"), "GET")
        status, headers, _body = self.request(server, "/catalog",
                                              method="HEAD")
        self.assertEqual(status, 405)
        self.assertEqual(owner.waits, [])

    def test_the_only_handled_method_is_get(self):
        handler = catalog_http.CatalogRequestHandler
        methods = {name for name in dir(handler) if name.startswith("do_")}
        self.assertEqual(methods, {"do_" + m for m in (
            ("GET",) + catalog_http._REFUSED_METHODS)})
        for name in methods - {"do_GET"}:
            self.assertIs(getattr(handler, name), handler._method_refused)
        # Item 2 (evidence library) adds ONE read route, /artifact
        # (tests/test_artifact_content.py); item 3 (live code review) adds
        # ONE more, /code_review (tests/test_code_review_owner.py); still
        # GET only.
        self.assertEqual(catalog_http.ROUTES,
                         ("/catalog", "/diagnostics", "/healthz",
                          "/artifact", "/code_review"))

    def test_no_control_route_is_reachable_and_nothing_is_written(self):
        """Every plausible control/registration request, by every method,
        is refused before the owner, and the registry bytes never change."""
        state_dir = self.mission_state()
        self.register(source_id="mc", store=state_dir)
        registry_file = os.path.join(self.directory, "mission_sources.json")
        with open(registry_file, "rb") as handle:
            before = handle.read()
        owner = StubOwner(None)
        server = self.serve(owner)
        for method in ("GET", "POST", "PUT", "DELETE"):
            for path in ("/register?source_id=x", "/unregister?source_id=mc",
                         "/sources", "/control", "/dispatch", "/prompt",
                         "/spawn", "/tools/call", "/mcp", "/shutdown",
                         "/catalog/register"):
                status = self.request(server, path, method=method,
                                      headers=(("Content-Length", "0"),)
                                      if method != "GET" else ())[0]
                self.assertIn(status, (404, 405), (method, path))
        self.assertEqual(owner.waits, [])
        self.assertEqual(owner.diagnosed, 0)
        with open(registry_file, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_over_bound_request_line_and_headers_are_413(self):
        owner = StubOwner(None)
        server = self.serve(owner)
        port = server.server_address[1]
        host = ("Host: 127.0.0.1:%d\r\n" % port).encode()
        auth = ("Authorization: Bearer %s\r\n" % self.token).encode()
        pad = "a" * catalog.MAX_REQUEST_LINE_BYTES
        line = ("GET /catalog?wait_seconds=1&pad=%s HTTP/1.1\r\n"
                % pad).encode()
        reply = self.raw(server, line + host + auth + b"\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.0 413 "), reply[:40])
        # The standard library's own bounds, mapped: a request line past
        # 65536 bytes, a header line past 65536 bytes, and over 100 headers.
        reply = self.raw(server, b"GET /" + b"a" * 65532)
        self.assertTrue(reply.startswith(b"HTTP/1.0 413 "), reply[:40])
        reply = self.raw(server, b"GET /catalog HTTP/1.1\r\nX-Pad: "
                         + b"a" * (65537 - 7))
        self.assertTrue(reply.startswith(b"HTTP/1.0 413 "), reply[:40])
        reply = self.raw(server, b"GET /catalog HTTP/1.1\r\n"
                         + b"X-A: b\r\n" * 101)
        self.assertTrue(reply.startswith(b"HTTP/1.0 413 "), reply[:40])
        self.assertEqual(owner.waits, [])
        self.assert_no_token(reply)

    def test_connections_in_progress_are_bounded(self):
        answer = self.real_answer()
        owner = StubOwner(answer)
        with mock.patch.object(catalog_http.CatalogRequestHandler, "timeout",
                               3):
            server = self.serve(owner, clock=lambda: answer["response"][
                "generated_at"])
            port = server.server_address[1]
            idle = [socket.create_connection(("127.0.0.1", port), timeout=30)
                    for _ in range(catalog_http.MAX_CONCURRENT_REQUESTS)]
            try:
                deadline = time.monotonic() + 10
                while server._slots._value and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(server._slots._value, 0)
                request = ("GET /catalog HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                           "Authorization: Bearer %s\r\n\r\n"
                           % (port, self.token)).encode()
                self.assertEqual(self.raw(server, request), b"")
                self.assertEqual(owner.waits, [])
            finally:
                for sock in idle:
                    sock.close()
            deadline = time.monotonic() + 10
            while server._slots._value < catalog_http.MAX_CONCURRENT_REQUESTS \
                    and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(self.catalog_get(server)[0], 200)

    def test_only_loopback_can_be_bound(self):
        for host in ("0.0.0.0", "127.0.0.2", "::1", "localhost", "", "10.0.0.1"):
            with self.assertRaises(catalog_http.NotLoopbackError):
                catalog_http.require_loopback(host)
        catalog_http.require_loopback("127.0.0.1")
        with mock.patch.object(catalog_http, "LOOPBACK_HOST", "0.0.0.0"):
            with self.assertRaises(catalog_http.NotLoopbackError):
                catalog_http.LoopbackCatalogServer(StubOwner(None),
                                                   self.token, self.clock)
        with self.assertRaises(ValueError):
            catalog_http.LoopbackCatalogServer(StubOwner(None), "",
                                               self.clock)

    def test_diagnostics_and_healthz_are_read_only_answers(self):
        owner = StubOwner(None)
        server = self.serve(owner)
        status, headers, body = self.request(server, "/diagnostics")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {
            "endpoint": catalog.ENDPOINT,
            "endpoint_version": catalog.ENDPOINT_VERSION,
            "diagnostics": {"stub": True}})
        status, headers, body = self.request(server, "/healthz")
        self.assertEqual((status, json.loads(body)), (200, {
            "endpoint": catalog.ENDPOINT,
            "endpoint_version": catalog.ENDPOINT_VERSION, "healthz": "ok"}))
        self.assertEqual((owner.waits, owner.diagnosed), ([], 1))
        for name in FRESHNESS_HEADERS:
            self.assertIsNone(self.header(headers, name))


class DiscoveryFileTests(HttpFixture):

    def path(self, *parts):
        return os.path.join(self.root, "runtime", *parts)

    def test_published_atomically_owner_only_with_exactly_three_keys(self):
        target = self.path("backend-discovery.json")
        links = []
        real_link = os.link

        def spy(src, dst, *args, **kwargs):
            links.append((os.path.dirname(src), os.path.basename(dst)))
            return real_link(src, dst, *args, **kwargs)
        # Published by a NO-CLOBBER link from the same directory; a
        # replacing rename is never used.
        with mock.patch.object(catalog_http.os, "link", spy), \
                mock.patch.object(catalog_http.os, "rename",
                                  side_effect=AssertionError("rename")), \
                mock.patch.object(catalog_http.os, "replace",
                                  side_effect=AssertionError("replace")):
            identity = catalog_http.publish_discovery_file(target, 43210,
                                                           self.token)
        self.assertEqual(links, [(self.path(), "backend-discovery.json")])
        self.assertEqual(stat.S_IMODE(os.lstat(self.path()).st_mode), 0o700)
        info = os.lstat(target)
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual((info.st_dev, info.st_ino), identity)
        self.assertEqual(os.listdir(self.path()), ["backend-discovery.json"])
        value, problem = catalog_http.read_discovery_file(target)
        self.assertIsNone(problem)
        self.assertEqual(sorted(value), ["port", "token", "url"])
        self.assertEqual((value["port"], value["url"]),
                         (43210, "http://127.0.0.1:43210"))
        self.assertTrue(value["token"] == self.token)

    def test_concurrent_publications_have_exactly_one_winner(self):
        """Round-1 blocker 1: two publications reach the publishing step at
        the same instant (a barrier inside ``os.link``; independently
        bounded). Exactly one succeeds; the loser is refused WITHOUT
        modifying the winner's file, whose identity is unchanged."""
        target = self.path("backend-discovery.json")
        os.makedirs(self.path(), mode=0o700)
        barrier = threading.Barrier(2, timeout=10)
        real_link = os.link

        def link(src, dst, *args, **kwargs):
            barrier.wait()
            return real_link(src, dst, *args, **kwargs)
        tokens = [secrets.token_urlsafe(32) for _ in range(2)]
        results = [None, None]

        def publish(index):
            try:
                results[index] = ("won", catalog_http.publish_discovery_file(
                    target, 40000 + index, tokens[index]))
            except FileExistsError:
                results[index] = ("refused", None)
        with mock.patch.object(catalog_http.os, "link", link):
            threads = [threading.Thread(target=publish, args=(index,),
                                        daemon=True) for index in (0, 1)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(r[0] for r in results if r),
                         ["refused", "won"])
        winner = [i for i in (0, 1) if results[i][0] == "won"][0]
        info = os.lstat(target)
        self.assertEqual((info.st_dev, info.st_ino), results[winner][1])
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        value, problem = catalog_http.read_discovery_file(target)
        self.assertIsNone(problem)
        self.assertEqual(value["port"], 40000 + winner)
        self.assertTrue(value["token"] == tokens[winner])
        self.assertEqual(os.listdir(self.path()), ["backend-discovery.json"])

    def test_a_path_replaced_between_check_and_open_is_refused(self):
        """Round-1 blocker 2: the path is replaced AFTER the reader's check
        and BEFORE its open, by a different valid owner-only file, by an
        open-permission file, and by a FIFO. Each is refused, because the
        OPENED descriptor is re-checked; no read blocks (independent
        bound: each read runs in a thread joined with a timeout)."""
        base = self.path()
        os.makedirs(base, mode=0o700)
        target = os.path.join(base, "d.json")
        real_open = os.open
        forged = json.dumps({"port": 6, "token": "x" * 43,
                             "url": "http://127.0.0.1:6"})

        def swapping(kind):
            def opener(path, flags, *args, **kwargs):
                if path == target:
                    other = os.path.join(base, "replacement")
                    if kind == "fifo":
                        os.mkfifo(other, 0o600)
                    else:
                        with open(other, "w") as handle:
                            handle.write(forged)
                        os.chmod(other, 0o644 if kind == "open-mode"
                                 else 0o600)
                    os.replace(other, target)
                return real_open(path, flags, *args, **kwargs)
            return opener

        def bounded_read():
            outcome = []
            reader = threading.Thread(target=lambda: outcome.append(
                catalog_http.read_discovery_file(target)), daemon=True)
            reader.start()
            reader.join(10)
            self.assertFalse(reader.is_alive(), "the read blocked")
            return outcome[0]
        for kind in ("other-owner-only-file", "open-mode", "fifo"):
            if os.path.lexists(target):
                os.unlink(target)
            catalog_http.publish_discovery_file(target, 5, self.token)
            self.assertIsNone(catalog_http.read_discovery_file(target)[1])
            with mock.patch.object(catalog_http.os, "open", swapping(kind)):
                self.assertEqual(bounded_read(),
                                 (None, catalog_http.DISCOVERY_UNREADABLE),
                                 kind)

    def test_an_existing_file_is_never_overwritten(self):
        target = self.path("backend-discovery.json")
        os.makedirs(self.path(), mode=0o700)
        with open(target, "w") as handle:
            handle.write("someone else's\n")
        with self.assertRaises(FileExistsError):
            catalog_http.publish_discovery_file(target, 1, self.token)
        with open(target) as handle:
            self.assertEqual(handle.read(), "someone else's\n")
        self.assertEqual(os.listdir(self.path()), ["backend-discovery.json"])

    def test_an_unsafe_directory_is_refused(self):
        os.makedirs(self.path("open"), mode=0o700)
        os.chmod(self.path("open"), 0o750)
        with self.assertRaises(PermissionError):
            catalog_http.publish_discovery_file(
                self.path("open", "d.json"), 1, self.token)
        os.makedirs(self.path("real"), mode=0o700)
        os.symlink(self.path("real"), self.path("link"))
        with self.assertRaises(PermissionError):
            catalog_http.publish_discovery_file(
                self.path("link", "d.json"), 1, self.token)
        with self.assertRaises(FileNotFoundError):
            catalog_http.publish_discovery_file(
                self.path("missing", "leaf", "d.json"), 1, self.token)
        with self.assertRaises(ValueError):
            catalog_http.publish_discovery_file("relative.json", 1,
                                                self.token)
        self.assertEqual(os.listdir(self.path("real")), [])

    def test_a_failed_write_leaves_no_file_and_no_temp(self):
        target = self.path("backend-discovery.json")
        with mock.patch.object(catalog_http.os, "fsync",
                               side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                catalog_http.publish_discovery_file(target, 1, self.token)
        self.assertEqual(os.listdir(self.path()), [])

    def test_removal_only_of_the_file_this_launch_wrote(self):
        target = self.path("backend-discovery.json")
        identity = catalog_http.publish_discovery_file(target, 1, self.token)
        other = self.path("other")
        with open(other, "w") as handle:         # a distinct inode, made
            handle.write("replaced\n")          # while ours still exists
        os.replace(other, target)
        catalog_http.remove_discovery_file(target, identity)
        self.assertTrue(os.path.exists(target))     # not ours: kept
        os.unlink(target)
        catalog_http.remove_discovery_file(target, identity)  # absent: fine
        identity = catalog_http.publish_discovery_file(target, 1, self.token)
        catalog_http.remove_discovery_file(target, identity)
        self.assertFalse(os.path.lexists(target))

    def test_consumer_missing_or_unreadable_is_honestly_unavailable(self):
        """The reference consumer reader: every failure is a named
        unavailable problem; it never creates, repairs or removes."""
        base = self.path()
        os.makedirs(base, mode=0o700)
        read = catalog_http.read_discovery_file
        self.assertEqual(read(os.path.join(base, "absent.json")),
                         (None, catalog_http.DISCOVERY_ABSENT))
        self.assertEqual(read(os.path.join(self.root, "nowhere", "d.json")),
                         (None, catalog_http.DISCOVERY_ABSENT))
        good = {"port": 5, "token": "t" * 43, "url": "http://127.0.0.1:5"}

        def put(name, text, mode=0o600):
            path = os.path.join(base, name)
            with open(path, "w") as handle:
                handle.write(text)
            os.chmod(path, mode)
            return path
        self.assertEqual(read(put("ok.json", json.dumps(good)))[1], None)
        self.assertEqual(read(put("open.json", json.dumps(good), 0o644)),
                         (None, catalog_http.DISCOVERY_UNREADABLE))
        os.symlink(os.path.join(base, "ok.json"),
                   os.path.join(base, "link.json"))
        self.assertEqual(read(os.path.join(base, "link.json")),
                         (None, catalog_http.DISCOVERY_UNREADABLE))
        os.mkdir(os.path.join(base, "dir.json"))
        self.assertEqual(read(os.path.join(base, "dir.json")),
                         (None, catalog_http.DISCOVERY_UNREADABLE))
        self.assertEqual(read(put("big.json", " " * (
            catalog_http.MAX_DISCOVERY_FILE_BYTES + 1))),
            (None, catalog_http.DISCOVERY_UNREADABLE))
        for name, text in (
                ("partial.json", json.dumps(good)[:-3]),
                ("list.json", "[]"),
                ("extra.json", json.dumps(dict(good, extra=1))),
                ("missing.json", json.dumps({"port": 5, "token": "t"})),
                ("mismatch.json", json.dumps(dict(
                    good, url="http://127.0.0.1:6"))),
                ("remote.json", json.dumps(dict(
                    good, url="http://10.0.0.1:5"))),
                ("port.json", json.dumps(dict(good, port="5"))),
                ("empty-token.json", json.dumps(dict(good, token="")))):
            self.assertEqual(read(put(name, text)),
                             (None, catalog_http.DISCOVERY_MALFORMED), name)
        listing = sorted(os.listdir(base))
        read(os.path.join(base, "absent.json"))
        self.assertEqual(sorted(os.listdir(base)), listing)


class ShutdownTests(HttpFixture):

    def test_close_joins_an_in_flight_handler_and_reports_it(self):
        """Round-1 blocker 3: a handler is held inside the owner's request
        (independently bounded at 30 s). Closing the server must NOT return
        while it runs, it is reported in progress, and once released it
        completes its answer (200), then close returns."""
        answer = self.real_answer()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        class Blocking(StubOwner):
            def request(inner, wait_seconds):
                entered.set()
                release.wait(30)
                return StubOwner.request(inner, wait_seconds)
        server = catalog_http.LoopbackCatalogServer(
            Blocking(answer), self.token,
            lambda: answer["response"]["generated_at"])
        threading.Thread(target=server.serve_forever, daemon=True).start()
        replies = {}
        caller = threading.Thread(target=lambda: replies.update(
            reply=self.request(server, "/catalog?wait_seconds=1")),
            daemon=True)
        caller.start()
        self.assertTrue(entered.wait(10))
        self.assertEqual(server.handlers_in_progress(), 1)
        server.shutdown()
        closer = threading.Thread(target=server.server_close, daemon=True)
        closer.start()
        closer.join(1.0)
        self.assertTrue(closer.is_alive(),
                        "server_close returned with a handler still running")
        self.assertEqual(server.handlers_in_progress(), 1)
        release.set()
        closer.join(10)
        caller.join(10)
        self.assertFalse(closer.is_alive())
        self.assertFalse(caller.is_alive())
        self.assertEqual(server.handlers_in_progress(), 0)
        status, _headers, body = replies["reply"]
        self.assertEqual((status, json.loads(body)["served"]),
                         (200, answer["served"]))


class AdapterProcessTests(HttpFixture):
    """The REAL adapter process (``main``), with a fixed clock and the FAKE
    listing; stdout/stderr are checked for the token."""

    def launch(self, argv):
        listing = self.plan()
        self.engine_calls.clear()
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", ADAPTER_CHILD, str(REPO_ROOT),
             json.dumps(argv), str(NOW), json.dumps(listing)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.reap, child)
        return child

    def reap(self, child):
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=30)

    def argv(self, discovery, *extra):
        return ["--registry", self.directory, "--discovery-file",
                discovery] + list(extra)

    def get(self, discovery, path):
        value, problem = catalog_http.read_discovery_file(discovery)
        self.assertIsNone(problem)
        conn = http.client.HTTPConnection("127.0.0.1", value["port"],
                                          timeout=60)
        try:
            conn.request("GET", path, headers={
                "Authorization": "Bearer " + value["token"]})
            response = conn.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            conn.close()

    def test_lifecycle_one_owner_discovery_file_and_no_token_output(self):
        _mission_id, workspace = self.bound_store()
        discovery = os.path.join(self.root, "rt", "backend-discovery.json")
        child = self.launch(self.argv(discovery, "--herd-root", workspace))
        startup = json.loads(child.stdout.readline())
        self.assertEqual(set(startup), {"discovery_file", "endpoint",
                                        "endpoint_version", "pid", "routes",
                                        "url"})
        value, problem = catalog_http.read_discovery_file(discovery)
        self.assertIsNone(problem)
        self.token = value["token"]
        self.assertEqual(startup["url"], value["url"])
        self.assertEqual(stat.S_IMODE(os.lstat(discovery).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.lstat(os.path.dirname(
            discovery)).st_mode), 0o700)
        status, _headers, body = self.get(discovery, "/catalog?wait_seconds=10")
        first = json.loads(body)
        self.assertEqual((status, first["served"]),
                         (200, catalog.SERVED_REFRESHED))
        [mission] = first["response"]["missions"]
        self.assertEqual(len(mission["members"]), 1)
        status, _headers, body = self.get(discovery, "/catalog?wait_seconds=1")
        self.assertEqual(json.loads(body)["served"], catalog.SERVED_CACHED)
        child.send_signal(signal.SIGTERM)
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, catalog_http.EXIT_OK, err)
        self.assertFalse(os.path.lexists(discovery))
        self.assertEqual(os.listdir(os.path.dirname(discovery)), [])
        self.assert_no_token(json.dumps(startup), out, err)
        self.assertIn(b"stopped", err)
        self.assertIn(b'"handlers_in_progress": 0', err)

    def test_sigint_also_stops_cleanly(self):
        self.register(source_id="mc", store=self.mission_state())
        discovery = os.path.join(self.root, "rt", "d.json")
        child = self.launch(self.argv(discovery, "--herd-root", self.herd()))
        child.stdout.readline()
        child.send_signal(signal.SIGINT)
        _out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, catalog_http.EXIT_OK, err)
        self.assertFalse(os.path.lexists(discovery))

    def test_a_vanished_discovery_file_is_never_recreated(self):
        self.register(source_id="mc", store=self.mission_state())
        discovery = os.path.join(self.root, "rt", "d.json")
        child = self.launch(self.argv(discovery, "--herd-root", self.herd()))
        child.stdout.readline()
        value, _problem = catalog_http.read_discovery_file(discovery)
        self.token = value["token"]
        other = os.path.join(self.root, "not-ours")
        with open(other, "w") as handle:         # a distinct inode, made
            handle.write("not ours\n")          # while ours still exists
        os.unlink(discovery)
        time.sleep(1.5)
        self.assertEqual(catalog_http.read_discovery_file(discovery),
                         (None, catalog_http.DISCOVERY_ABSENT))
        os.replace(other, discovery)
        child.send_signal(signal.SIGTERM)
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, catalog_http.EXIT_OK, err)
        with open(discovery) as handle:
            self.assertEqual(handle.read(), "not ours\n")   # not removed
        self.assert_no_token(out, err)

    def test_a_leftover_discovery_file_refuses_the_launch(self):
        self.register(source_id="mc", store=self.mission_state())
        discovery = os.path.join(self.root, "rt", "d.json")
        os.makedirs(os.path.dirname(discovery), mode=0o700)
        with open(discovery, "w") as handle:
            handle.write("left over\n")
        child = self.launch(self.argv(discovery, "--herd-root", self.herd()))
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, catalog_http.EXIT_USAGE)
        self.assertEqual(out, b"")
        self.assertIn(b"FileExistsError", err)
        with open(discovery) as handle:
            self.assertEqual(handle.read(), "left over\n")

    def test_without_a_runtime_source_it_refuses_to_start(self):
        discovery = os.path.join(self.root, "rt", "d.json")
        child = self.launch(self.argv(discovery))
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, catalog_http.EXIT_USAGE)
        self.assertEqual(out, b"")
        self.assertIn(b"a runtime source is required", err)
        self.assertFalse(os.path.lexists(os.path.dirname(discovery)))
        for bad in (["--registry", "relative", "--discovery-file", discovery,
                     "--discover-roots"],
                    ["--registry", self.directory, "--discovery-file",
                     "relative.json", "--discover-roots"],
                    self.argv(discovery, "--discover-roots", "--port",
                              "70000"),
                    self.argv(discovery, "--host", "0.0.0.0",
                              "--discover-roots")):
            child = self.launch(bad)
            out, _err = child.communicate(timeout=60)
            self.assertEqual(child.returncode, catalog_http.EXIT_USAGE, bad)
            self.assertEqual(out, b"")
        self.assertFalse(os.path.lexists(os.path.dirname(discovery)))


class StarvationTests(HttpFixture):
    """FIXTURE-driven, through the REAL owner and real HTTP: a capture that
    did not observe (starved of its wait share, slot held, listing or
    boundary failed) can NEVER read as an observed-empty roster. The
    envelope stays fresh (allowed and honest); the runtime is reported
    unavailable with its own problem code, every mission's binding is
    unverified/runtime_snapshot_not_fresh, and no member is presented."""

    def diagnostics(self, server):
        return json.loads(self.request(server, "/diagnostics")[2])[
            "diagnostics"]

    def fifo(self):
        """A FIFO never written: a boundary child reading it blocks."""
        path = os.path.join(self.root, "never-written-%d.fifo" % len(
            os.listdir(self.root)))
        os.mkfifo(path)
        return path

    def starved(self, server, wait, problem):
        status, headers, _body, answer = self.catalog_get(
            server, "?wait_seconds=%g" % wait)
        self.assertEqual(status, 200)
        capture = answer["runtime_capture"]
        self.assertEqual((capture["configured"], capture["available"],
                          capture["problem"]), (True, False, problem))
        response = answer["response"]
        self.assertIsNotNone(response)            # the envelope is honest
        self.assertEqual(answer["problem"], None)
        self.assert_fresh_headers(headers)        # fresh envelope, and ...
        snapshot = response["runtime_snapshot"]   # ... unavailable runtime
        self.assertNotEqual(snapshot["health"], contract.HEALTH_FRESH)
        [mission] = response["missions"]
        self.assertEqual((mission["binding"]["state"],
                          mission["binding"]["reason"]),
                         (contract.BINDING_UNVERIFIED,
                          roster.REASON_SNAPSHOT_NOT_FRESH))
        self.assertEqual(mission["members"], [])  # nothing observed live
        return answer

    def test_capture_not_finished_within_wait_then_slot_occupied(self):
        _mission_id, workspace = self.bound_store()
        stall = snapshot_tests.StalledStart()
        self._stalls.append(stall)
        patcher = mock.patch.object(
            snapshot_tests.engine_runtime.subprocess, "Popen", stall)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(stall.release.set)
        owner = self.endpoint([workspace], self.plan())
        server = self.serve(owner)
        self.starved(server, 1, snapshot_module.PROBLEM_CAPTURE_PENDING)
        self.assertEqual(self.diagnostics(server)["runtime"]["work"],
                         snapshot_module.WORK_PENDING)     # never "done"
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        self.starved(server, 1, snapshot_module.PROBLEM_SLOT_OCCUPIED)
        self.assertEqual(self.diagnostics(server)["runtime"]["work"],
                         snapshot_module.WORK_PENDING)
        stall.release.set()
        self.assertEqual(owner.settle_runtime(15)["runtime"]["work"],
                         snapshot_module.WORK_SETTLED)

    def test_agent_listing_timed_out(self):
        _mission_id, workspace = self.bound_store()
        owner = self.endpoint([workspace], self.plan(sleep_before=30))
        server = self.serve(owner)
        self.starved(server, 30, snapshot_module.PROBLEM_LISTING_TIMED_OUT)
        self.assertEqual(owner.settle_runtime(15)["runtime"]["work"],
                         snapshot_module.WORK_SETTLED)

    def test_agent_listing_unavailable(self):
        _mission_id, workspace = self.bound_store()
        owner = self.endpoint([workspace], self.plan(stdout="", exit=1))
        self.starved(self.serve(owner), 10,
                     snapshot_module.PROBLEM_LISTING_UNAVAILABLE)

    def test_agent_listing_malformed(self):
        _mission_id, workspace = self.bound_store()
        owner = self.endpoint([workspace],
                              self.plan(stdout="warning: noise\n"))
        self.starved(self.serve(owner), 10,
                     snapshot_module.PROBLEM_LISTING_MALFORMED)

    def test_capture_boundary_timed_out(self):
        _mission_id, workspace = self.bound_store()
        self.substitute(**{snapshot_module.MODE_PASS: {"fifo": self.fifo()}})
        owner = self.endpoint([workspace], self.plan())
        server = self.serve(owner)
        self.starved(server, 30, snapshot_module.PROBLEM_BOUNDARY_TIMED_OUT)
        self.assertEqual(owner.settle_runtime(15)["runtime"]["work"],
                         snapshot_module.WORK_SETTLED)

    def test_a_healthy_empty_roster_is_distinguishable_from_starvation(self):
        """The inverse: the herd is OBSERVED and genuinely has no member.
        Every field that marks a starved capture reads the other way."""
        state_dir = self.mission_state()
        _mission_id, workspace = self.running_mission(
            state_dir, task_id=snapshot_tests.TASK)
        self.herd(root=workspace, agents={})
        self.register(source_id="mc", store=state_dir)
        owner = self.endpoint([workspace], self.plan(
            stdout=snapshot_tests.listing_stdout([])))
        status, headers, _body, answer = self.catalog_get(self.serve(owner))
        self.assertEqual(status, 200)
        self.assert_fresh_headers(headers)
        capture = answer["runtime_capture"]
        self.assertEqual((capture["available"], capture["problem"]),
                         (True, None))
        self.assertEqual(answer["response"]["runtime_snapshot"]["health"],
                         contract.HEALTH_FRESH)
        [mission] = answer["response"]["missions"]
        self.assertEqual(mission["members"], [])
        self.assertNotEqual(mission["binding"]["reason"],
                            roster.REASON_SNAPSHOT_NOT_FRESH)
        self.assertNotEqual(mission["binding"]["state"],
                            contract.BINDING_UNVERIFIED)


class ImportBoundaryTests(unittest.TestCase):

    def test_the_adapter_adds_no_project_module_beyond_the_catalog(self):
        probe = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import target_runtime.catalog\n"
            "before = set(sys.modules)\n"
            "import target_runtime.catalog_http\n"
            "added = sorted(set(sys.modules) - before)\n"
            "print('\\n'.join(added))\n")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", probe,
                                 str(REPO_ROOT)], capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        added = result.stdout.split()
        project = {"target_runtime", "mission_sources", "mission",
                   "workflow_authority", "herdr", "local_request",
                   "jarvis_di", "jarvisdi", "grok_bot", "pr_delivery",
                   "codex_gateway", "telegram_operator", "tunnel_control",
                   "capability", "worker", "operator_session",
                   "coordination", "durable_execution", "human_interaction"}
        self.assertEqual([m for m in added if m.split(".")[0] in project],
                         ["target_runtime.catalog_http"])
        self.assertNotIn("subprocess", added)

    def test_the_adapter_process_loads_no_control_or_delivery_module(self):
        probe = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import target_runtime.catalog_http\n"
            "bad = sorted(n for n in sys.modules if n.split('.')[0] in (\n"
            "    'local_request', 'jarvis_di', 'jarvisdi', 'grok_bot',\n"
            "    'pr_delivery', 'codex_gateway', 'tunnel_control',\n"
            "    'capability', 'worker', 'operator_session', 'coordination',\n"
            "    'durable_execution', 'human_interaction')\n"
            "    or n in ('mission_sources.readiness',\n"
            "             'mission.state_service', 'target_runtime.cli'))\n"
            "print('\\n'.join(bad))\n"
            "sys.exit(1 if bad else 0)\n")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", probe,
                                 str(REPO_ROOT)], capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
