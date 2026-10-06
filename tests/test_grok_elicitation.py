"""Focused tests for Task 8 slice S-I: the client-mediated decision
transport (``grok_mcp.elicitation``, the server's event-stream decision
call), the ``di_mission_decide`` relay, the client-confirmation
provenance, and the Mission-control provenance predicate.

Every test runs a REAL ``GrokMcpServer`` (``ThreadingHTTPServer``) over
a real socket, a real ``MissionService`` over a temporary store, and a
real HTTP client that reads the event stream and posts the JSON-RPC
response exactly as a Streamable HTTP client does. Effect counts are
read back from the store on disk (decisions, authorizations,
reservations), never from in-memory objects.

Sections: A negotiation and refusals, B the decision round trip,
C transport lifecycle (R2-13), D the authority card (R-S1-a),
E accepted-decision durability (R-S1-b), F static pins, G the
provenance predicate.
"""

import ast
import fcntl
import gc
import http.client
import io
import json
import os
import queue
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mission import decision as mission_decision  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import service as mission_service  # noqa: E402
from mission import store as mission_store  # noqa: E402
from mission_control import authority  # noqa: E402
from workflow_authority import atomic as atomic_module  # noqa: E402

from grok_mcp import controller as controller_module  # noqa: E402
from grok_mcp import decision_tools  # noqa: E402
from grok_mcp import elicitation  # noqa: E402
from grok_mcp import protocol  # noqa: E402
from grok_mcp import server as server_module  # noqa: E402

from test_grok_mcp import (  # noqa: E402
    McpClient,
    RecordingOperator,
    conforms,
    deterministic_refs,
    mission_proposal_arguments,
    non_docstring_strings,
)

TOKEN = "test-bearer-token"
REPOSITORY = "/repo/example"
DECIDE = protocol.TOOL_MISSION_DECIDE
SSE_ACCEPT = "application/json, text/event-stream"
JSON_ONLY_ACCEPT = "application/json"
FORM_CAPABILITIES = {"elicitation": {"form": {}}}
URL_ONLY_CAPABILITIES = {"elicitation": {"url": {}}}


# --------------------------------------------------------------------
# A raw Streamable HTTP client that can hold an event stream open.
# --------------------------------------------------------------------


class StreamClient(object):
    """One MCP session over raw ``http.client`` connections: JSON calls,
    an event-stream decision call, and JSON-RPC responses."""

    def __init__(self, port, token=TOKEN):
        self.port = port
        self.token = token
        self.session_id = None
        self.protocol_version = None
        self.next_id = 1

    def _headers(self, accept=SSE_ACCEPT, session=True):
        headers = {"Content-Type": "application/json", "Accept": accept}
        if self.token is not None:
            headers["Authorization"] = "Bearer " + self.token
        if session and self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def post(self, payload, accept=SSE_ACCEPT, session=True, session_id=None):
        headers = self._headers(accept, session)
        if session_id is not None:
            headers["Mcp-Session-Id"] = session_id
        connection = http.client.HTTPConnection("127.0.0.1", self.port,
                                                timeout=10)
        connection.request("POST", "/mcp", json.dumps(payload).encode("utf-8"),
                           headers)
        response = connection.getresponse()
        body = response.read()
        status = response.status
        connection.close()
        return status, body

    def rpc(self, method, params=None, **kwargs):
        payload = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        self.next_id += 1
        if params is not None:
            payload["params"] = params
        status, body = self.post(payload, **kwargs)
        return status, (json.loads(body.decode("utf-8")) if body else None)

    def initialize(self, version="2025-11-25", capabilities=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port,
                                                timeout=10)
        payload = {"jsonrpc": "2.0", "id": self.next_id, "method": "initialize",
                   "params": {"protocolVersion": version,
                              "capabilities": (
                                  FORM_CAPABILITIES if capabilities is None
                                  else capabilities),
                              "clientInfo": {"name": "stream-client",
                                             "version": "0"}}}
        self.next_id += 1
        connection.request("POST", "/mcp", json.dumps(payload).encode("utf-8"),
                           self._headers(session=False))
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        self.session_id = response.getheader("Mcp-Session-Id")
        self.protocol_version = body["result"]["protocolVersion"]
        connection.close()
        return response.status, body

    def open_decision(self, mission_id, revision, accept=SSE_ACCEPT):
        """POST a decide call and return the open connection plus the
        response object (headers read, body still streaming)."""
        payload = {"jsonrpc": "2.0", "id": self.next_id, "method": "tools/call",
                   "params": {"name": DECIDE,
                              "arguments": {"mission_id": mission_id,
                                            "revision": revision}}}
        self.next_id += 1
        connection = http.client.HTTPConnection("127.0.0.1", self.port,
                                                timeout=30)
        connection.request("POST", "/mcp", json.dumps(payload).encode("utf-8"),
                           self._headers(accept))
        response = connection.getresponse()
        return connection, response, payload["id"]

    @staticmethod
    def close(connection, response):
        """Close BOTH the response file object and the connection: the
        socket is released only when neither holds it."""
        try:
            response.close()
        finally:
            connection.close()

    @staticmethod
    def read_event(response):
        """One SSE event's ``data`` payload as JSON, or None at EOF."""
        data_lines = []
        while True:
            line = response.fp.readline()
            if not line:
                return None
            line = line.decode("utf-8").rstrip("\n")
            if line == "":
                if data_lines:
                    return json.loads("\n".join(data_lines))
                continue
            if line.startswith("data: "):
                data_lines.append(line[len("data: "):])

    def respond(self, request_id, action, content=None, **kwargs):
        result = {"action": action}
        if content is not None:
            result["content"] = content
        return self.post({"jsonrpc": "2.0", "id": request_id,
                          "result": result}, **kwargs)

    def run_decision(self, mission_id, revision, answer, tolerant=False):
        """Like ``decide`` but tolerant of every shape a faulted call can
        produce: a JSON refusal, a stream whose first event is already
        the final result, or a stream that ends without one. Returns
        (elicitation request or None, final result or None). With
        ``tolerant`` the response POST may fail or be refused (the
        server side of it may have been faulted) and the final event is
        still read, bounded by the connection timeout."""
        self.respond_failure = None
        try:
            connection, response, _ = self.open_decision(mission_id, revision)
        except Exception as exc:  # noqa: BLE001 - tolerant mode
            if not tolerant:
                raise
            self.respond_failure = "open %s" % type(exc).__name__
            return None, None
        try:
            if response.getheader("Content-Type") != "text/event-stream":
                body = response.read()
                try:
                    return None, json.loads(body.decode("utf-8"))
                except ValueError:
                    if not tolerant:
                        raise
                    return None, {"error": {"code": response.status,
                                            "message": "non-JSON body"}}
            first = self.read_event(response)
            if first is None or first.get("method") != "elicitation/create":
                return None, first
            reply = answer(first)
            if reply is not None:
                action, content = reply
                try:
                    status, _ = self.respond(first["id"], action, content)
                except Exception as exc:  # noqa: BLE001 - tolerant mode
                    if not tolerant:
                        raise
                    self.respond_failure = type(exc).__name__
                    status = None
                if not tolerant:
                    assert status == 202, status
                elif status != 202:
                    self.respond_failure = "status %r" % (status,)
            try:
                return first, self.read_event(response)
            except Exception as exc:  # noqa: BLE001 - tolerant mode
                if not tolerant:
                    raise
                self.respond_failure = (self.respond_failure or "") + (
                    " final read %s" % type(exc).__name__)
                return first, None
        finally:
            self.close(connection, response)

    def decide(self, mission_id, revision, answer, accept=SSE_ACCEPT):
        """Full round trip: open, read the elicitation, answer with
        ``answer(request)`` -> (action, content) or None to skip, read
        the final result. Returns (elicitation request, result event)."""
        connection, response, call_id = self.open_decision(
            mission_id, revision, accept)
        try:
            if response.getheader("Content-Type") != "text/event-stream":
                body = json.loads(response.read().decode("utf-8"))
                return None, body
            request = self.read_event(response)
            reply = answer(request)
            if reply is not None:
                action, content = reply
                status, _ = self.respond(request["id"], action, content)
                assert status == 202, status
            final = self.read_event(response)
            return request, final
        finally:
            self.close(connection, response)


def accept_with_prefix(request):
    """The honest client answer: confirm the enum value the card asks for."""
    schema = request["params"]["requestedSchema"]
    value = schema["properties"]["confirm"]["enum"][0]
    return "accept", {"confirm": value}


def structured_of(final):
    return final["result"]["structuredContent"]


# --------------------------------------------------------------------
# Fixture: real server + real Mission service over a temp store.
# --------------------------------------------------------------------


class Fixture(unittest.TestCase):

    def setUp(self):
        self.threads_before = threading.active_count()
        self.logs = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = os.path.join(self.tmp.name, "protected")
        self.store = mission_store.MissionStore(self.directory)
        self.now = [1_000_000]
        self.service = mission_service.MissionService(
            self.store, lambda: self.now[0])
        self.ingress = mission_record.AuthenticatedContext(
            transport="grok_mcp",
            principal_kind=mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
            principal_ref="1",
        )
        self.operator = RecordingOperator()
        self.controller = controller_module.GrokMcpController(
            self.operator.session(), REPOSITORY, mint_ref=deterministic_refs(),
            mission_service=self.service,
        )

    def serve(self, **server_kwargs):
        server = server_module.GrokMcpServer(
            ("127.0.0.1", 0), self.controller, bearer_token=TOKEN,
            log_writer=self.logs.append, **server_kwargs
        )
        self.server = server
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.assert_no_stray_threads)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return StreamClient(server.server_address[1])

    def assert_no_stray_threads(self):
        self.assertEqual(threading.active_count(), self.threads_before)

    def propose(self, **overrides):
        request_id = self.service.mint_request_id(self.ingress)
        outcome = self.service.propose(
            request_id, mission_proposal_arguments(**overrides), self.ingress)
        return outcome["mission_id"], outcome["revision"]

    # -- store readers: effect counts from disk ------------------------

    def document(self):
        return self.store.load()

    def store_bytes(self):
        if not os.path.exists(self.store.path):
            return None
        with open(self.store.path, "rb") as handle:
            return handle.read()

    def decisions(self, mission_id):
        return self.document()["missions"][mission_id]["decisions"]

    def reservation_ids(self, kind="decision"):
        return sorted(key for key, r in self.document()["reservations"].items()
                      if r["kind"] == kind)

    def authorizations(self):
        return self.document()["authorizations"]

    def reservations(self, kind="decision"):
        return [r for r in self.document()["reservations"].values()
                if r["kind"] == kind]

    def unconsumed_reservations(self, kind="decision"):
        return [r for r in self.reservations(kind) if r["consumed_by"] is None]

    def assert_zero_effects(self, mission_id, bytes_before):
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(self.reservations(), [])
        self.assertEqual(self.store_bytes(), bytes_before)


# ====================================================================
# A. Negotiation and refusals before anything is reserved
# ====================================================================


class ANegotiationTests(Fixture):

    def test_A1_form_negotiation_rules_per_revision(self):
        negotiated = protocol.form_elicitation_negotiated
        self.assertTrue(negotiated("2025-11-25", {"elicitation": {"form": {}}}))
        self.assertTrue(negotiated("2025-11-25", {"elicitation": {}}))
        self.assertTrue(negotiated(
            "2025-11-25", {"elicitation": {"form": {}, "url": {}}}))
        self.assertFalse(negotiated("2025-11-25", {"elicitation": {"url": {}}}))
        self.assertFalse(negotiated("2025-11-25", {"elicitation": {"form": True}}))
        self.assertFalse(negotiated("2025-11-25", {}))
        self.assertFalse(negotiated("2025-11-25", None))
        self.assertFalse(negotiated("2025-11-25", {"elicitation": "yes"}))
        self.assertTrue(negotiated("2025-06-18", {"elicitation": {}}))
        self.assertTrue(negotiated("2025-06-18", {"elicitation": {"form": {}}}))
        self.assertFalse(negotiated("2025-06-18", {}))
        self.assertFalse(negotiated("2025-03-26", {"elicitation": {}}))
        self.assertFalse(negotiated("2024-11-05", {"elicitation": {}}))

    def test_A2_session_records_form_capability_and_existing_paths_unchanged(self):
        client = self.serve()
        client.initialize(capabilities=FORM_CAPABILITIES)
        self.assertEqual(self.server.session_state(client.session_id),
                         ("2025-11-25", True))
        other = StreamClient(client.port)
        other.initialize(capabilities={})
        self.assertEqual(self.server.session_state(other.session_id),
                         ("2025-11-25", False))
        legacy = McpClient(client.port)
        self.assertEqual(legacy.initialize()[0], 200)
        self.assertEqual(legacy.rpc("ping")[0], 200)
        status, body = legacy.call("di_ping", {"echo": "x"})
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["structuredContent"]["echo"], "x")
        self.assertEqual(self.server.elicitations.count, 0)

    def _refused(self, client, mission_id, revision, accept, reason):
        before = self.store_bytes()
        request, final = client.decide(mission_id, revision, accept_with_prefix,
                                       accept=accept)
        self.assertIsNone(request)
        structured = final["result"]["structuredContent"]
        self.assertTrue(final["result"]["isError"])
        self.assertEqual(structured["status"], "refused")
        self.assertEqual(structured["reason"], reason)
        self.assertTrue(conforms(protocol.tool_by_name(DECIDE)["outputSchema"],
                                 structured))
        self.assert_zero_effects(mission_id, before)
        self.assertEqual(self.server.elicitations.count, 0)

    def test_A3_non_negotiated_client_refuses_with_zero_reservations(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize(capabilities={})
        self._refused(client, mission_id, revision, SSE_ACCEPT,
                      elicitation.REFUSAL_NOT_NEGOTIATED)

    def test_A4_url_only_client_refuses(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize(capabilities=URL_ONLY_CAPABILITIES)
        self._refused(client, mission_id, revision, SSE_ACCEPT,
                      elicitation.REFUSAL_NOT_NEGOTIATED)

    def test_A5_accept_without_event_stream_refuses(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        self._refused(client, mission_id, revision, JSON_ONLY_ACCEPT,
                      elicitation.REFUSAL_SSE_NOT_ACCEPTED)

    def test_A6_direct_controller_call_without_channel_refuses(self):
        mission_id, revision = self.propose()
        client_ingress = mission_record.AuthenticatedContext(
            transport="grok_mcp",
            principal_kind=mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION,
            principal_ref="1")
        before = self.store_bytes()
        result = self.controller.call_tool(
            DECIDE, {"mission_id": mission_id, "revision": revision},
            ingress=self.ingress, client_ingress=client_ingress)
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured["reason"],
                         elicitation.REFUSAL_SSE_NOT_ACCEPTED)
        result = self.controller.call_tool(
            DECIDE, {"mission_id": mission_id, "revision": revision},
            ingress=self.ingress)
        self.assertEqual(result.structured["reason"],
                         "no authenticated ingress for this call; Mission tools"
                         " accept only requests the server authenticated")
        # A plain-ordinal context presented as the client context refuses.
        result = self.controller.call_tool(
            DECIDE, {"mission_id": mission_id, "revision": revision},
            ingress=self.ingress, client_ingress=self.ingress,
            elicitation=elicitation.ElicitationChannel(
                elicit_fn=lambda *a: None, claim_fn=lambda: True))
        self.assertTrue(result.is_error)
        self.assert_zero_effects(mission_id, before)

    def test_A7_unwired_service_refuses(self):
        controller = controller_module.GrokMcpController(
            self.operator.session(), REPOSITORY, mint_ref=deterministic_refs())
        result = controller.call_tool(
            DECIDE, {"mission_id": "mn-" + "0" * 32, "revision": 1},
            ingress=self.ingress)
        self.assertEqual(result.structured["reason"],
                         "mission service not wired on this endpoint")

    def test_A8_stale_requested_revision_refuses_before_reserving(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        before = self.store_bytes()
        request, final = client.decide(mission_id, revision + 1,
                                       accept_with_prefix)
        self.assertIsNone(request)
        structured = structured_of(final)
        self.assertEqual(structured["problem"], "mission_stale_revision")
        self.assert_zero_effects(mission_id, before)


# ====================================================================
# B. The decision round trip
# ====================================================================


class BDecisionRoundTripTests(Fixture):

    def test_B1_accept_records_one_decision_one_authorization_bounded_expiry(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        request, final = client.decide(mission_id, revision, accept_with_prefix)
        self.assertEqual(request["method"], "elicitation/create")
        self.assertEqual(request["params"]["mode"], "form")
        self.assertRegex(request["id"], r"^md-[0-9a-f]{32}$")
        structured = structured_of(final)
        self.assertNotIn("isError", final["result"])
        self.assertTrue(conforms(protocol.tool_by_name(DECIDE)["outputSchema"],
                                 structured))
        self.assertEqual(structured["status"], "applied")
        self.assertEqual(structured["decision"], "APPROVE")
        self.assertEqual(structured["elicitation_outcome"], "accept")
        self.assertEqual(structured["decision_id"], request["id"])
        self.assertEqual(structured["current_state"], "AUTHORIZED")
        self.assertTrue(structured["authorization_live"])
        self.assertEqual(
            structured["expires_at"],
            self.now[0] + decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS)
        decisions = self.decisions(mission_id)
        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertEqual(decision["decision_id"], request["id"])
        self.assertEqual(decision["provenance"]["principal_kind"],
                         mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION)
        self.assertEqual(decision["provenance"]["transport"], "grok_mcp")
        self.assertIsNone(decision["provenance"]["human_identity_proof"])
        self.assertEqual(decision["provenance"]["proof"],
                         "transport_credential_only")
        self.assertEqual(decision["expires_at"], structured["expires_at"])
        self.assertEqual(len(self.authorizations()), 1)
        authorization = list(self.authorizations().values())[0]
        self.assertEqual(authorization["expires_at"], structured["expires_at"])
        self.assertEqual(authorization["authorized_action_scope"],
                         ["engineering_change", "repository_read"])
        self.assertEqual(authorization["authorized_delivery_targets"],
                         ["github_pr"])
        reservations = self.reservations()
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0]["consumed_by"], request["id"])
        self.assertEqual(reservations[0]["context"]["principal_kind"],
                         mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION)
        stored = self.service.get(mission_id)
        self.assertEqual(stored["live_authorization_id"],
                         structured["authorization_id"])
        self.assertEqual(self.server.elicitations.count, 0)

    def test_B2_decline_records_one_denial_and_no_authorization(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        request, final = client.decide(mission_id, revision,
                                       lambda r: ("decline", None))
        structured = structured_of(final)
        self.assertEqual(structured["status"], "applied")
        self.assertEqual(structured["decision"], "DENY")
        self.assertEqual(structured["current_state"], "DENIED")
        self.assertEqual(structured["elicitation_outcome"], "decline")
        decisions = self.decisions(mission_id)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision"], "DENY")
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(self.unconsumed_reservations(), [])

    def test_B3_cancel_records_nothing_and_leaves_one_unconsumed_reservation(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        request, final = client.decide(mission_id, revision,
                                       lambda r: ("cancel", None))
        structured = structured_of(final)
        self.assertTrue(final["result"]["isError"])
        self.assertEqual(structured["status"], "not_recorded")
        self.assertEqual(structured["elicitation_outcome"], "cancel")
        self.assertEqual(structured["decision_id"], request["id"])
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(len(self.unconsumed_reservations()), 1)
        self.assertEqual(self.service.get(mission_id)["record"]["state"],
                         "AWAITING_DECISION")

    def test_B4_wrong_confirm_value_is_a_binding_mismatch_with_zero_decisions(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        request, final = client.decide(
            mission_id, revision, lambda r: ("accept", {"confirm": "0" * 12}))
        structured = structured_of(final)
        self.assertEqual(structured["problem"], "elicitation_binding_mismatch")
        self.assertEqual(structured["elicitation_outcome"], "binding_mismatch")
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(len(self.unconsumed_reservations()), 1)
        # Extra content, missing content, and an error response likewise.
        for answer in (
            lambda r: ("accept", {"confirm": accept_with_prefix(r)[1]["confirm"],
                                  "extra": 1}),
            lambda r: ("accept", None),
        ):
            request, final = client.decide(mission_id, revision, answer)
            self.assertEqual(structured_of(final)["problem"],
                             "elicitation_binding_mismatch")
        self.assertEqual(self.decisions(mission_id), [])

    def test_B5_client_error_response_records_nothing(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        try:
            request = client.read_event(response)
            status, _ = client.post({"jsonrpc": "2.0", "id": request["id"],
                                     "error": {"code": -1, "message": "no"}})
            self.assertEqual(status, 202)
            final = client.read_event(response)
        finally:
            client.close(connection, response)
        self.assertEqual(structured_of(final)["elicitation_outcome"],
                         "client_error")
        self.assertEqual(self.decisions(mission_id), [])

    def test_B6_stale_revision_after_the_card_is_refused_by_the_core(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        try:
            request = client.read_event(response)
            # An edit lands between the card and the answer.
            decision_id = self.service.mint_decision_id(self.ingress)
            self.service.edit(mission_id, revision,
                              mission_proposal_arguments(objective="changed"),
                              decision_id, self.ingress)
            action, content = accept_with_prefix(request)
            self.assertEqual(client.respond(request["id"], action, content)[0],
                             202)
            final = client.read_event(response)
        finally:
            client.close(connection, response)
        structured = structured_of(final)
        self.assertEqual(structured["problem"], "mission_stale_revision")
        self.assertEqual(structured["elicitation_outcome"], "accept")
        decisions = self.decisions(mission_id)
        self.assertEqual([d["decision"] for d in decisions], ["EDIT"])
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(len(self.unconsumed_reservations()), 1)

    def test_B7_accept_over_2025_06_18_omits_mode_and_records_one_decision(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize(version="2025-06-18", capabilities={"elicitation": {}})
        request, final = client.decide(mission_id, revision, accept_with_prefix)
        self.assertNotIn("mode", request["params"])
        self.assertEqual(structured_of(final)["decision"], "APPROVE")
        self.assertEqual(len(self.decisions(mission_id)), 1)

    def test_B8_model_cannot_supply_the_accept_through_arguments(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        before = self.store_bytes()
        for extra in ({"action": "accept"}, {"confirm": "x"},
                      {"decision": "APPROVE"}):
            arguments = dict(mission_id=mission_id, revision=revision, **extra)
            status, body = client.rpc("tools/call",
                                      {"name": DECIDE, "arguments": arguments})
            self.assertEqual(status, 200)
            structured = body["result"]["structuredContent"]
            self.assertTrue(body["result"]["isError"])
            self.assertIn("unknown property", structured["reason"])
        self.assert_zero_effects(mission_id, before)


# ====================================================================
# C. Transport lifecycle (R2-13)
# ====================================================================


class CTransportLifecycleTests(Fixture):

    def test_C1_replayed_response_is_reported_and_has_no_effect(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        try:
            request = client.read_event(response)
            action, content = accept_with_prefix(request)
            self.assertEqual(client.respond(request["id"], action, content)[0],
                             202)
            final = client.read_event(response)
        finally:
            client.close(connection, response)
        self.assertEqual(structured_of(final)["decision"], "APPROVE")
        # The same id again: replayed, 202, nothing changes.
        before = self.store_bytes()
        self.assertEqual(client.respond(request["id"], "decline")[0], 202)
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(len(self.decisions(mission_id)), 1)
        self.assertIn("client response replayed\n", self.logs)
        self.assertIn("client response delivered\n", self.logs)

    def test_C2_unknown_id_and_cross_session_responses_have_no_effect(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        other = StreamClient(client.port)
        other.initialize()
        # Unknown id on a valid session.
        self.assertEqual(client.respond("md-" + "1" * 32, "accept",
                                        {"confirm": "x"})[0], 202)
        self.assertIn("client response unsolicited\n", self.logs)
        connection, response, _ = client.open_decision(mission_id, revision)
        try:
            request = client.read_event(response)
            action, content = accept_with_prefix(request)
            # The right id from the WRONG session: unsolicited.
            status, _ = other.respond(request["id"], action, content)
            self.assertEqual(status, 202)
            self.assertEqual(self.server.elicitations.count, 1)
            self.assertEqual(self.decisions(mission_id), [])
            # A non-string id is unsolicited too.
            self.assertEqual(client.post({"jsonrpc": "2.0", "id": 7,
                                          "result": {"action": "accept"}})[0],
                             202)
            # Then the right session: exactly one decision.
            self.assertEqual(client.respond(request["id"], action, content)[0],
                             202)
            final = client.read_event(response)
        finally:
            client.close(connection, response)
        self.assertEqual(structured_of(final)["decision"], "APPROVE")
        self.assertEqual(len(self.decisions(mission_id)), 1)
        self.assertEqual(self.logs.count("client response unsolicited\n"), 3)

    def test_C3_response_without_or_with_unknown_session_is_gated(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        try:
            request = client.read_event(response)
            action, content = accept_with_prefix(request)
            status, body = client.respond(request["id"], action, content,
                                          session=False)
            self.assertEqual((status, body), (400, b"missing session"))
            status, body = client.respond(request["id"], action, content,
                                          session_id="0" * 32)
            self.assertEqual((status, body), (404, b"unknown session"))
            self.assertEqual(self.server.elicitations.count, 1)
            self.assertEqual(self.decisions(mission_id), [])
            self.assertEqual(client.respond(request["id"], action, content)[0],
                             202)
            final = client.read_event(response)
        finally:
            client.close(connection, response)
        self.assertEqual(structured_of(final)["decision"], "APPROVE")
        self.assertEqual(len(self.decisions(mission_id)), 1)

    def test_C4_response_after_expiry_is_refused_with_zero_decisions(self):
        mission_id, revision = self.propose()
        client = self.serve(elicitation_validity_seconds=0.3,
                            elicitation_poll_seconds=0.02)
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        try:
            request = client.read_event(response)
            final = client.read_event(response)  # expiry ends the wait
            action, content = accept_with_prefix(request)
            self.assertEqual(client.respond(request["id"], action, content)[0],
                             202)
        finally:
            client.close(connection, response)
        structured = structured_of(final)
        self.assertEqual(structured["elicitation_outcome"], "expired")
        self.assertEqual(structured["status"], "not_recorded")
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(len(self.unconsumed_reservations()), 1)
        self.assertEqual(self.server.elicitations.count, 0)
        self.assertIn("client response unsolicited\n", self.logs)

    def test_C5_stream_closure_observed_mid_wait_cleans_up_and_server_still_serves(self):
        mission_id, revision = self.propose()
        client = self.serve(elicitation_poll_seconds=0.02)
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        request = client.read_event(response)
        self.assertEqual(self.server.elicitations.count, 1)
        client.close(connection, response)
        deadline = time.monotonic() + 5
        while self.server.elicitations.count and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.server.elicitations.count, 0)
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(len(self.unconsumed_reservations()), 1)
        # The late answer is unsolicited and the server keeps serving.
        action, content = accept_with_prefix(request)
        self.assertEqual(client.respond(request["id"], action, content)[0], 202)
        self.assertIn("client response unsolicited\n", self.logs)
        self.assertEqual(client.rpc("ping")[0], 200)
        self.assertEqual(self.decisions(mission_id), [])
        # A fresh round trip still works after the observed closure.
        request, final = client.decide(mission_id, revision, accept_with_prefix)
        self.assertEqual(structured_of(final)["decision"], "APPROVE")
        self.assertEqual(len(self.decisions(mission_id)), 1)

    def test_C6_server_close_wakes_a_waiting_handler_within_bound(self):
        mission_id, revision = self.propose()
        client = self.serve(elicitation_poll_seconds=0.02)
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        client.read_event(response)
        self.assertEqual(self.server.elicitations.count, 1)
        started = time.monotonic()
        self.server.shutdown()
        self.server.server_close()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5)
        final = client.read_event(response)
        client.close(connection, response)
        self.assertEqual(structured_of(final)["elicitation_outcome"],
                         "server_closed")
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.server.elicitations.count, 0)

    def test_C7_pending_table_is_bounded_and_refuses_before_reserving(self):
        mission_id, revision = self.propose()
        client = self.serve(elicitation_poll_seconds=0.02)
        client.initialize()
        opened = []
        for _ in range(elicitation.MAX_PENDING_ELICITATIONS):
            connection, response, _ = client.open_decision(mission_id, revision)
            client.read_event(response)
            opened.append((connection, response))
        self.assertEqual(self.server.elicitations.count,
                         elicitation.MAX_PENDING_ELICITATIONS)
        reservations_before = len(self.reservations())
        request, final = client.decide(mission_id, revision, accept_with_prefix)
        self.assertIsNone(request)
        self.assertEqual(structured_of(final)["reason"],
                         elicitation.REFUSAL_TABLE_FULL)
        self.assertEqual(len(self.reservations()), reservations_before)
        for connection, response in opened:
            client.close(connection, response)
        deadline = time.monotonic() + 5
        while self.server.elicitations.count and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.server.elicitations.count, 0)
        self.assertEqual(self.decisions(mission_id), [])

    def test_C8_concurrent_status_and_ping_served_while_a_decision_waits(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        try:
            request = client.read_event(response)
            other = McpClient(client.port)
            self.assertEqual(other.initialize()[0], 200)
            status, body = other.call("di_status")
            self.assertEqual(status, 200)
            self.assertIn(DECIDE, body["result"]["structuredContent"]["tools"])
            self.assertEqual(client.rpc("ping")[0], 200)
            status, body = client.rpc("tools/call", {
                "name": "di_mission_get",
                "arguments": {"mission_id": mission_id}})
            self.assertEqual(body["result"]["structuredContent"]["state"],
                             "AWAITING_DECISION")
            action, content = accept_with_prefix(request)
            self.assertEqual(client.respond(request["id"], action, content)[0],
                             202)
            final = client.read_event(response)
        finally:
            client.close(connection, response)
        self.assertEqual(structured_of(final)["decision"], "APPROVE")

    def test_C9_pending_table_unit_semantics(self):
        clock = [100]
        table = elicitation.PendingTable(lambda: clock[0], validity_seconds=10,
                                         poll_seconds=0.01, max_pending=2)
        open_probe = lambda: True  # noqa: E731
        first = table.register("s1", "md-a", open_probe)
        self.assertIsNotNone(first)
        self.assertIsNone(table.register("s1", "md-a", open_probe))
        second = table.register("s1", "md-b", open_probe)
        self.assertIsNone(table.register("s2", "md-c", open_probe))
        self.assertFalse(table.available())
        self.assertEqual(table.deliver("s2", {"id": "md-a"}), "unsolicited")
        self.assertEqual(table.deliver("s1", {"id": "md-a"}), "delivered")
        self.assertEqual(table.deliver("s1", {"id": "md-a"}), "replayed")
        self.assertEqual(table.wait(first), ("delivered", {"id": "md-a"}))
        clock[0] = 111
        self.assertEqual(table.deliver("s1", {"id": "md-b"}), "unsolicited")
        self.assertEqual(table.wait(second), ("expired", None))
        self.assertEqual(table.count, 0)
        third = table.register("s1", "md-d", lambda: False)
        self.assertEqual(table.wait(third), ("stream_closed", None))
        fourth = table.register("s1", "md-e", open_probe)
        table.close_all()
        self.assertEqual(table.wait(fourth), ("server_closed", None))
        self.assertIsNone(table.register("s1", "md-f", open_probe))
        # A pending entry cannot exist without its probe.
        with self.assertRaises(TypeError):
            table.register("s1", "md-g", None)

    def test_C10_response_evaluation_is_closed(self):
        evaluate = elicitation.evaluate_response
        self.assertEqual(evaluate({"result": {"action": "accept",
                                              "content": {"confirm": "abc"}}},
                                  "abc"), ("accept", None))
        self.assertEqual(evaluate({"result": {"action": "decline"}}, "abc"),
                         ("decline", None))
        self.assertEqual(evaluate({"result": {"action": "cancel"}}, "abc"),
                         ("cancel", None))
        for message in (None, [], {"result": None}, {"result": {}},
                        {"result": {"action": "yes"}}):
            self.assertEqual(evaluate(message, "abc")[0], "malformed_response")
        self.assertEqual(evaluate({"error": {}}, "abc")[0], "client_error")
        for content in (None, {}, {"confirm": "abd"}, {"confirm": 1},
                        {"confirm": "abc", "x": 1}, "abc"):
            self.assertEqual(
                evaluate({"result": {"action": "accept", "content": content}},
                         "abc")[0], "binding_mismatch")


    def test_C11_admission_is_one_locked_transition_both_orderings_unit(self):
        # S1-R1 at the table: with the probe reporting a closure observed,
        # a response is refused at admission (before any poll); with the
        # probe open, the response is admitted and a later closure changes
        # nothing about the admitted answer.
        clock = [100]
        table = elicitation.PendingTable(lambda: clock[0], validity_seconds=10,
                                         poll_seconds=0.01, max_pending=4)
        probes = []
        # (a) closure observed before admission; the probe is bound AT
        # registration, so a response before any wait() is refused too.
        closed = table.register("s1", "md-a",
                                lambda: probes.append("a") or False)
        self.assertEqual(table.deliver("s1", {"id": "md-a"}), "unsolicited")
        self.assertEqual(closed.state, "abandoned")
        self.assertEqual(closed.outcome, "stream_closed")
        self.assertEqual(probes, ["a"])
        self.assertEqual(table.wait(closed), ("stream_closed", None))
        # (b) admitted, then the probe would report a closure: the admitted
        # answer stands and the waiter receives it.
        state = {"open": True}
        open_entry = table.register("s1", "md-b", lambda: state["open"])
        self.assertEqual(table.deliver("s1", {"id": "md-b"}), "delivered")
        state["open"] = False
        self.assertEqual(table.wait(open_entry), ("delivered", {"id": "md-b"}))
        self.assertEqual(open_entry.state, "answered")
        # A raising probe reads as a closure observed.

        def boom():
            raise OSError("probe failed")

        raising = table.register("s1", "md-c", boom)
        self.assertEqual(table.deliver("s1", {"id": "md-c"}), "unsolicited")
        self.assertEqual(raising.outcome, "stream_closed")
        self.assertEqual(table.count, 0)

    def test_C12_closure_observed_before_admission_over_the_wire_records_nothing(self):
        # S1-R1 ordering (a), synchronized: the response is posted only
        # after the SERVER'S OWN probe has observed the closure (the
        # delivering thread waits for that observation before admission is
        # attempted), so admission sees the closure regardless of polling.
        mission_id, revision = self.propose()
        observed = threading.Event()

        class ClosureSyncTable(elicitation.PendingTable):
            def deliver(inner, session_id, message):
                key = (session_id, message.get("id"))
                with inner._lock:
                    entry = inner._entries.get(key)
                    probe = entry.alive if entry is not None else None
                if probe is not None:
                    deadline = time.monotonic() + 5
                    while probe() and time.monotonic() < deadline:
                        time.sleep(0.005)
                    observed.set()
                return elicitation.PendingTable.deliver(inner, session_id,
                                                        message)

        client = self.serve(elicitation_poll_seconds=5)  # no poll in time
        self.server.elicitations = ClosureSyncTable(time.time, None, 5)
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        request = client.read_event(response)
        client.close(connection, response)
        action, content = accept_with_prefix(request)
        status, _ = client.respond(request["id"], action, content)
        self.assertEqual(status, 202)
        self.assertTrue(observed.is_set())
        self.assertIn("client response unsolicited\n", self.logs)
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(len(self.unconsumed_reservations()), 1)
        self.assertEqual(self.server.elicitations.count, 0)
        self.server.elicitations.close_all()

    def test_C16_closure_observed_after_request_write_before_wait_records_nothing(self):
        # S1-R1 (round 04): the gap between the request write and the
        # start of wait(). The waiter is held at the entry of wait(); the
        # stream is closed and the response is posted while it is held;
        # the probe bound at registration refuses the response at
        # admission, so no decision exists when wait() finally runs.
        mission_id, revision = self.propose()
        at_wait = threading.Event()
        proceed = threading.Event()
        holder = {}

        class HeldBeforeWait(elicitation.PendingTable):
            def wait(inner, entry):
                holder["entry"] = entry
                at_wait.set()
                proceed.wait(10)
                return elicitation.PendingTable.wait(inner, entry)

        client = self.serve(elicitation_poll_seconds=0.02)
        self.server.elicitations = HeldBeforeWait(time.time, None, 0.02)
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        request = client.read_event(response)
        self.assertTrue(at_wait.wait(10))
        entry = holder["entry"]
        self.assertIs(entry.alive.__func__ if hasattr(entry.alive, "__func__")
                      else None,
                      server_module.GrokMcpRequestHandler._stream_open)
        client.close(connection, response)
        deadline = time.monotonic() + 5
        while entry.alive() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(entry.alive())
        action, content = accept_with_prefix(request)
        self.assertEqual(client.respond(request["id"], action, content)[0],
                         202)
        self.assertIn("client response unsolicited\n", self.logs)
        self.assertEqual(entry.state, "abandoned")
        self.assertEqual(entry.outcome, "stream_closed")
        self.assertEqual(self.decisions(mission_id), [])
        proceed.set()
        deadline = time.monotonic() + 5
        while self.server.elicitations.count and time.monotonic() < deadline:
            time.sleep(0.01)
        deadline = time.monotonic() + 5
        while not any("with no response admitted" in line
                      for line in self.logs) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(len(self.unconsumed_reservations()), 1)
        self.assertEqual(self.server.elicitations.count, 0)
        self.assertEqual(self.server.elicitations.claimed, 0)
        # S1-C1: the wording is conditioned on actual admission.
        self.assertFalse(any("after admission" in line for line in self.logs),
                         self.logs)
        self.assertTrue(any(
            line.startswith("stream closure observed with no response"
                            " admitted before result write")
            or line.startswith("presentation write failed with no response"
                               " admitted")
            for line in self.logs), self.logs)
        self.server.elicitations.close_all()

    def test_C13_admitted_then_closure_observed_keeps_the_decision(self):
        # S1-R1 ordering (b), synchronized: the admission hook closes the
        # client's stream INSIDE the answered transition, before the waiter
        # wakes, and waits until the server-side probe observes it. The
        # decision is applied and stays; the result write is logged as
        # uncertain from what the server observed.
        mission_id, revision = self.propose()
        holder = {}

        class CloseAfterAdmission(elicitation.PendingTable):
            def _answered(inner, entry):
                connection, response = holder["stream"]
                probe = entry.alive
                StreamClient.close(connection, response)
                deadline = time.monotonic() + 5
                while probe() and time.monotonic() < deadline:
                    time.sleep(0.005)

        client = self.serve(elicitation_poll_seconds=0.02)
        self.server.elicitations = CloseAfterAdmission(time.time, None, 0.02)
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        holder["stream"] = (connection, response)
        request = client.read_event(response)
        action, content = accept_with_prefix(request)
        self.assertEqual(client.respond(request["id"], action, content)[0],
                         202)
        deadline = time.monotonic() + 5
        while not any("after admission" in line for line in self.logs) and (
            time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertTrue(any(
            line.startswith("stream closure observed after admission")
            or line.startswith("presentation write failed after admission")
            for line in self.logs), self.logs)
        decisions = self.decisions(mission_id)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision"], "APPROVE")
        self.assertEqual(len(self.authorizations()), 1)
        stored = self.service.get(mission_id)
        self.assertEqual(stored["record"]["state"], "AUTHORIZED")
        self.assertEqual(self.server.elicitations.count, 0)
        self.server.elicitations.close_all()

    def test_C14_concurrent_last_slot_one_winner_loser_reserves_nothing(self):
        # S1-R2: MAX-1 entries pending, then two decide calls race for the
        # last slot through a barrier placed inside the claim. Exactly one
        # claims; the loser refuses table_full with NO reservation.
        mission_id, revision = self.propose()
        barrier = threading.Barrier(2, timeout=10)

        class RacingTable(elicitation.PendingTable):
            def claim(inner):
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
                return elicitation.PendingTable.claim(inner)

        client = self.serve(elicitation_poll_seconds=0.02)
        self.server.elicitations = RacingTable(time.time, None, 0.02)
        table = self.server.elicitations
        client.initialize()
        opened = []
        # Fill all but the last slot without racing (the barrier needs two
        # parties, so open these through a plain claim).
        table.claim = lambda: elicitation.PendingTable.claim(table)
        for _ in range(elicitation.MAX_PENDING_ELICITATIONS - 1):
            connection, response, _ = client.open_decision(mission_id, revision)
            client.read_event(response)
            opened.append((connection, response))
        del table.claim  # restore the racing claim
        self.assertEqual(table.count, elicitation.MAX_PENDING_ELICITATIONS - 1)
        reservations_before = len(self.reservations())
        results = {}

        def race(label):
            other = StreamClient(client.port)
            other.initialize()
            connection, response, _ = other.open_decision(mission_id, revision)
            try:
                if response.getheader("Content-Type") != "text/event-stream":
                    body = json.loads(response.read().decode("utf-8"))
                    results[label] = ("lost", body, None, None)
                    return
                event = client.read_event(response)
                if event.get("method") == "elicitation/create":
                    results[label] = ("won", event, connection, response)
                    return
                results[label] = ("lost", event, None, None)
            finally:
                if results.get(label, ("won",))[0] == "lost":
                    other.close(connection, response)

        threads = [threading.Thread(target=race, args=(name,))
                   for name in ("x", "y")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        outcomes = sorted(value[0] for value in results.values())
        self.assertEqual(outcomes, ["lost", "won"])
        lost = [v for v in results.values() if v[0] == "lost"][0][1]
        self.assertEqual(structured_of(lost)["reason"],
                         elicitation.REFUSAL_TABLE_FULL)
        self.assertIsNone(structured_of(lost)["decision_id"])
        # Exactly one new reservation: the winner's.
        self.assertEqual(len(self.reservations()), reservations_before + 1)
        self.assertEqual(table.count, elicitation.MAX_PENDING_ELICITATIONS)
        self.assertEqual(table.claimed, 0)
        won = [v for v in results.values() if v[0] == "won"][0]
        for connection, response in opened + [(won[2], won[3])]:
            client.close(connection, response)
        deadline = time.monotonic() + 5
        while table.count and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(table.count, 0)
        self.assertEqual(self.decisions(mission_id), [])

    def test_C15_claim_release_accounting_unit(self):
        table = elicitation.PendingTable(lambda: 1, validity_seconds=10,
                                         poll_seconds=0.01, max_pending=2)
        self.assertTrue(table.claim())
        self.assertTrue(table.claim())
        self.assertFalse(table.claim())
        self.assertFalse(table.available())
        self.assertEqual(table.claimed, 2)
        table.release_claim()
        self.assertEqual(table.claimed, 1)
        probe = lambda: True  # noqa: E731
        # S1-R3a: a held claim is consumed ONLY by a successful
        # registration. A raising clock leaves it with the holder...
        table._clock = mock.Mock(side_effect=RuntimeError("clock"))
        with self.assertRaises(RuntimeError):
            table.register("s", "md-a", probe, claimed=True)
        self.assertEqual(table.claimed, 1)
        self.assertEqual(table.count, 0)
        table._clock = lambda: 1
        # ...and so does a refused registration (duplicate key).
        entry = table.register("s", "md-a", probe, claimed=True)
        self.assertIsNotNone(entry)
        self.assertEqual(table.claimed, 0)
        self.assertEqual(table.count, 1)
        self.assertTrue(table.claim())
        self.assertIsNone(table.register("s", "md-a", probe, claimed=True))
        self.assertEqual(table.claimed, 1)
        table.release_claim()
        self.assertEqual(table.claimed, 0)
        # Without a claim the room check still applies.
        self.assertIsNotNone(table.register("s", "md-b", probe))
        self.assertIsNone(table.register("s", "md-c", probe))
        table.release_claim()  # never negative
        self.assertEqual(table.claimed, 0)
        table.close_all()
        self.assertFalse(table.claim())
        # The channel keeps the claim until the callee consumes it.
        released = []
        seen = {}

        def callee(request_id, message, confirm_value, channel):
            seen["holding_on_entry"] = channel.holding
            channel.consume()
            return "cancel", None

        channel = elicitation.ElicitationChannel(
            elicit_fn=callee, claim_fn=lambda: True,
            release_fn=lambda: released.append(1))
        self.assertTrue(channel.claim())
        self.assertTrue(channel.holding)
        self.assertEqual(channel.elicit("md-x", "card", "abc"), ("cancel", None))
        self.assertTrue(seen["holding_on_entry"])
        self.assertFalse(channel.holding)
        channel.release()
        self.assertEqual(released, [])


# ====================================================================
# D. The authority card (R-S1-a)
# ====================================================================


def full_contract():
    return {
        "requirements": [
            {"key": "tests_green", "description": "the suite passes",
             "evidence_kinds": ["VERIFICATION_RECORD"],
             "required_artifact_keys": ["diff"],
             "max_evidence_age_seconds": 3600},
            {"key": "review_ok", "description": "reviewer approved",
             "evidence_kinds": ["EXTERNAL_ATTESTATION", "VERIFICATION_RECORD"],
             "required_artifact_keys": [],
             "max_evidence_age_seconds": 7200},
        ],
        "required_artifacts": [
            {"key": "diff", "role": "PRODUCED",
             "expected_content_digest_sha256": "a" * 64},
        ],
        "required_dependencies": [
            {"key": "upstream", "kind": "RESOURCE",
             "target": {"form": "EXACT_RESOURCE", "resource_key": "ci"}},
        ],
        "required_resource_readiness": [
            {"resource_key": "runtime", "max_age_seconds": 900},
        ],
        "degradation_policy": {"permitted_blocker_keys": ["flaky_ci"]},
        "continuation_budget": {"max_attempts": 3, "max_checkpoints": 9},
    }


class DAuthorityCardTests(Fixture):

    def test_D1_card_renders_every_obligation_of_the_exact_revision(self):
        mission_id, revision = self.propose(proof_contract=full_contract())
        client = self.serve()
        client.initialize()
        request, final = client.decide(mission_id, revision, accept_with_prefix)
        message = request["params"]["message"]
        entry = self.service.get(mission_id)["record"]["revisions"][-1]
        digest = entry["proposal_digest_sha256"]
        base = mission_proposal_arguments()
        for expected in (
            "mission id: " + mission_id, "revision: %d" % revision,
            "proposal digest sha256: " + digest, base["objective"],
            base["target_context"], "repository: " + base["repository_url"],
            base["requested_scope"],
            "requested action scope: engineering_change, repository_read",
            "requested delivery target: github_pr",
            "requirement tests_green: evidence kinds VERIFICATION_RECORD;"
            " required artifacts diff; max evidence age 3600 seconds",
            "the suite passes",
            "requirement review_ok: evidence kinds EXTERNAL_ATTESTATION,"
            " VERIFICATION_RECORD; required artifacts (none); max evidence"
            " age 7200 seconds",
            "reviewer approved",
            "diff role PRODUCED digest " + "a" * 64,
            "upstream kind RESOURCE target form=EXACT_RESOURCE resource_key=ci",
            "runtime within 900 seconds",
            "degradation policy: permitted blocker keys flaky_ci",
            "continuation budget: 3 attempts, 9 checkpoints",
            "baseline: none declared in this revision",
            "valid until the decision time plus %d seconds"
            % decision_tools.CLIENT_CONFIRMED_AUTHORITY_SECONDS,
            "dispatches nothing, runs nothing and performs no repository"
            " action",
            "first 12 characters of the proposal digest: " + digest[:12],
        ):
            self.assertIn(expected, message, expected)
        schema = request["params"]["requestedSchema"]
        self.assertEqual(schema["required"], ["confirm"])
        self.assertEqual(schema["properties"]["confirm"]["enum"],
                         [digest[:12]])
        self.assertLessEqual(len(message),
                             protocol.MAX_ELICITATION_MESSAGE_CHARS)
        self.assertEqual(structured_of(final)["decision"], "APPROVE")

    def test_D2_card_without_contract_states_it_and_renders_the_baseline(self):
        # Task 8 S-IV: the ONE canonical ``baseline`` field, through the
        # REAL propose -> get -> elicited card -> approval payload — never
        # an unsupported key injected by hand, never rendered as "none"
        # while a baseline is being approved.
        baseline = {"ref": "refs/heads/main", "commit_sha": "b" * 40}
        without_id, _ = self.propose()
        mission_id, revision = self.propose(baseline=baseline)
        stored = self.service.get(mission_id)["record"]
        entry = stored["revisions"][-1]
        self.assertEqual(entry["proposal"]["baseline"], baseline)
        self.assertNotIn("baseline", self.service.get(without_id)["record"]
                         ["revisions"][-1]["proposal"])
        # Digest-bound: the same proposal without the baseline digests
        # differently.
        self.assertNotEqual(
            entry["proposal_digest_sha256"],
            self.service.get(without_id)["record"]["revisions"][-1]
            ["proposal_digest_sha256"])
        card = decision_tools.render_card(stored, entry, "c" * 12)
        self.assertIn("proof contract: none in this revision", card)
        self.assertIn("baseline: ref=refs/heads/main commit_sha=%s" % ("b" * 40),
                      card)
        self.assertNotIn("baseline: none", card)
        # The elicited card and the approval payload carry the exact field.
        client = self.serve()
        client.initialize()
        request, final = client.decide(mission_id, revision, accept_with_prefix)
        message = request["params"]["message"]
        self.assertIn("baseline: ref=refs/heads/main commit_sha=%s" % ("b" * 40),
                      message)
        structured = structured_of(final)
        self.assertEqual(structured["decision"], "APPROVE")
        self.assertEqual(structured["baseline"], baseline)
        approved_id = structured["authorization_id"]
        # An EDIT of the baseline invalidates the prior approval.
        decision_id = self.service.mint_decision_id(self.ingress)
        edited = self.service.edit(
            mission_id, revision,
            mission_proposal_arguments(baseline=dict(baseline, commit_sha="c" * 40)),
            decision_id, self.ingress)
        self.assertIn(approved_id, edited["invalidated_authorization_ids"])
        self.assertEqual(self.service.get(mission_id)["record"]["revisions"][-1]
                         ["proposal"]["baseline"]["commit_sha"], "c" * 40)
        self.assertIsNone(self.service.get(mission_id)["live_authorization_id"])

    def test_D3_oversized_card_refuses_with_zero_reservations_and_decisions(self):
        mission_id, revision = self.propose(
            objective="o" * mission_record.MAX_OBJECTIVE_CHARS,
            requested_scope="s" * mission_record.MAX_SCOPE_TEXT_CHARS,
            target_context="t" * mission_record.MAX_TARGET_CONTEXT_CHARS,
        )
        client = self.serve()
        client.initialize()
        before = self.store_bytes()
        request, final = client.decide(mission_id, revision, accept_with_prefix)
        self.assertIsNone(request)
        structured = structured_of(final)
        self.assertEqual(structured["problem"],
                         "elicitation_presentation_oversized")
        self.assertIsNone(structured["decision_id"])
        self.assert_zero_effects(mission_id, before)
        self.assertEqual(self.server.elicitations.count, 0)
        record = self.service.get(mission_id)["record"]
        self.assertIsNone(decision_tools.render_card(
            record, record["revisions"][-1], "0" * 12))


# ====================================================================
# E. Accepted decision durability (R-S1-b)
# ====================================================================


class EAcceptedDecisionDurabilityTests(Fixture):

    def test_E1_stream_closure_observed_before_response_records_nothing(self):
        mission_id, revision = self.propose()
        client = self.serve(elicitation_poll_seconds=0.02)
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        client.read_event(response)
        client.close(connection, response)
        deadline = time.monotonic() + 5
        while self.server.elicitations.count and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.server.elicitations.count, 0)
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(len(self.unconsumed_reservations()), 1)
        self.assertEqual(self.service.get(mission_id)["record"]["state"],
                         "AWAITING_DECISION")

    def test_E2_accepted_decision_survives_a_failed_final_write(self):
        mission_id, revision = self.propose()
        faults = []

        def fault(request_id):
            faults.append(request_id)
            raise BrokenPipeError("injected")

        client = self.serve(presentation_fault=fault)
        client.initialize()
        connection, response, _ = client.open_decision(mission_id, revision)
        try:
            request = client.read_event(response)
            action, content = accept_with_prefix(request)
            self.assertEqual(client.respond(request["id"], action, content)[0],
                             202)
            final = client.read_event(response)
        finally:
            client.close(connection, response)
        self.assertIsNone(final)  # the stream ended without a result event
        self.assertEqual(len(faults), 1)
        self.assertIn("presentation write failed after admission"
                      " BrokenPipeError; result presentation uncertain\n",
                      self.logs)
        # The decision REMAINS applied: one decision, one authorization.
        decisions = self.decisions(mission_id)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision"], "APPROVE")
        self.assertEqual(len(self.authorizations()), 1)
        stored = self.service.get(mission_id)
        self.assertEqual(stored["record"]["state"], "AUTHORIZED")
        self.assertIsNotNone(stored["live_authorization_id"])
        # And the next read over the wire shows it.
        status, body = client.rpc("tools/call", {
            "name": "di_mission_get",
            "arguments": {"mission_id": mission_id}})
        structured = body["result"]["structuredContent"]
        self.assertEqual(structured["state"], "AUTHORIZED")
        self.assertEqual(structured["active_authorization_id"],
                         stored["live_authorization_id"])
        self.assertEqual(self.server.elicitations.count, 0)

    def _decide_with_injected_initial_write(self, exception):
        """Open a decide call whose FIRST event write raises ``exception``
        (a real exception on the stream path, after the reservation)."""
        mission_id, revision = self.propose()
        client = self.serve(elicitation_poll_seconds=0.02)
        client.initialize()
        original = server_module.GrokMcpRequestHandler._write_event

        def failing_write(handler, payload):
            if payload.get("method") == "elicitation/create":
                raise exception
            return original(handler, payload)

        patcher = mock.patch.object(server_module.GrokMcpRequestHandler,
                                    "_write_event", failing_write)
        patcher.start()
        try:
            connection, response, _ = client.open_decision(mission_id, revision)
            try:
                final = client.read_event(response)
            finally:
                client.close(connection, response)
        finally:
            patcher.stop()
        return mission_id, final

    def test_E3_injected_exception_on_the_initial_write_cleans_up_and_accounts(self):
        # S1-R3: an unexpected exception (not an OSError) while writing the
        # card. The stream is already open, so the refusal arrives as the
        # final event; the pending entry is gone immediately; nothing is
        # applied; the refusal NAMES the reserved decision id.
        mission_id, final = self._decide_with_injected_initial_write(
            RuntimeError("injected"))
        structured = structured_of(final)
        self.assertTrue(final["result"]["isError"])
        self.assertEqual(structured["elicitation_outcome"], "stream_failed")
        self.assertEqual(structured["status"], "not_recorded")
        self.assertRegex(structured["decision_id"], r"^md-[0-9a-f]{32}$")
        self.assertEqual(self.server.elicitations.count, 0)
        self.assertEqual(self.server.elicitations.claimed, 0)
        self.assertIn("stream failure RuntimeError\n", self.logs)
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(self.authorizations(), {})
        unconsumed = self.unconsumed_reservations()
        self.assertEqual(len(unconsumed), 1)
        self.assertEqual(len(self.reservations()), 1)
        # A late answer to the reserved id is unsolicited, and the server
        # still serves a fresh, successful round trip.
        client = StreamClient(self.server.server_address[1])
        client.initialize()
        self.assertEqual(client.respond(structured["decision_id"], "accept",
                                        {"confirm": "0" * 12})[0], 202)
        self.assertEqual(self.decisions(mission_id), [])
        request, final = client.decide(mission_id, 1, accept_with_prefix)
        self.assertEqual(structured_of(final)["decision"], "APPROVE")
        self.assertEqual(len(self.decisions(mission_id)), 1)
        self.assertEqual(len(self.reservations()), 2)

    def test_E4_os_error_on_the_initial_write_is_write_failed_and_accounts(self):
        mission_id, final = self._decide_with_injected_initial_write(
            BrokenPipeError("injected"))
        structured = structured_of(final)
        self.assertEqual(structured["elicitation_outcome"], "write_failed")
        self.assertEqual(structured["status"], "not_recorded")
        self.assertRegex(structured["decision_id"], r"^md-[0-9a-f]{32}$")
        self.assertEqual(self.server.elicitations.count, 0)
        self.assertEqual(self.server.elicitations.claimed, 0)
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(len(self.unconsumed_reservations()), 1)

    def test_E5_channel_outcome_write_failed_records_nothing(self):
        # The channel-level contract (kept from the original E3): a
        # substituted write-failure outcome records nothing either.
        mission_id, revision = self.propose()
        client_ingress = mission_record.AuthenticatedContext(
            transport="grok_mcp",
            principal_kind=mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION,
            principal_ref="1")
        channel = elicitation.ElicitationChannel(
            elicit_fn=lambda *a: (elicitation.OUTCOME_WRITE_FAILED, None),
            claim_fn=lambda: True, release_fn=lambda: None)
        result = self.controller.call_tool(
            DECIDE, {"mission_id": mission_id, "revision": revision},
            ingress=self.ingress, client_ingress=client_ingress,
            elicitation=channel)
        self.assertEqual(result.structured["elicitation_outcome"],
                         "write_failed")
        self.assertEqual(result.structured["status"], "not_recorded")
        self.assertRegex(result.structured["decision_id"], r"^md-[0-9a-f]{32}$")
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(len(self.unconsumed_reservations()), 1)

    def test_E6_post_mint_exception_from_the_channel_names_the_reservation(self):
        # An unexpected exception raised by the channel itself after the
        # mint: refused, nothing applied, decision id reported, claim
        # released.
        mission_id, revision = self.propose()
        client_ingress = mission_record.AuthenticatedContext(
            transport="grok_mcp",
            principal_kind=mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION,
            principal_ref="1")
        released = []

        def explode(*a):
            raise RuntimeError("injected")

        channel = elicitation.ElicitationChannel(
            elicit_fn=explode, claim_fn=lambda: True,
            release_fn=lambda: released.append(1))
        result = self.controller.call_tool(
            DECIDE, {"mission_id": mission_id, "revision": revision},
            ingress=self.ingress, client_ingress=client_ingress,
            elicitation=channel)
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured["elicitation_outcome"],
                         "stream_failed")
        self.assertRegex(result.structured["decision_id"], r"^md-[0-9a-f]{32}$")
        self.assertIn("RuntimeError", result.structured["reason"])
        self.assertEqual(self.decisions(mission_id), [])
        self.assertEqual(len(self.unconsumed_reservations()), 1)
        # S1-R3a: the callee raised before it consumed the claim, so the
        # claim was still the channel's and was released exactly once.
        self.assertEqual(released, [1])
        self.assertFalse(channel.holding)
        channel.release()
        self.assertEqual(released, [1])


# ====================================================================
# H. Systematic lifecycle proof: one real exception per boundary
# ====================================================================


def _nth_call(nth, exception, when=None):
    """A side effect that raises ``exception`` on the ``nth`` matching
    call after arming (``when`` filters the calls that count)."""
    state = {"armed": False, "seen": 0}

    def side_effect(original):
        def wrapper(*args, **kwargs):
            if state["armed"] and (when is None or when(*args, **kwargs)):
                state["seen"] += 1
                if state["seen"] == nth:
                    raise exception
            return original(*args, **kwargs)
        return wrapper

    return state, side_effect


class Boundary(object):
    """One row of the lifecycle matrix: where a real exception is
    injected and what the readback-consistent outcome must be."""

    def __init__(self, name, patch, answer, *, final_json=False,
                 status, outcome, reserved, decisions, authorizations,
                 reservations, unconsumed, reason_has=None, problem=None,
                 log_has=None, ok=False, decision=None, recorded=None,
                 projection=None, decisions_are=None, live=True,
                 primitives=False, validity=None, tolerant=False):
        self.primitives = primitives  # a carried-state (primitives) result
        self.validity = validity      # server validity bound for this row
        self.tolerant = tolerant      # the response POST may fail
        self.name = name
        self.patch = patch
        self.answer = answer
        self.final_json = final_json
        self.status = status
        self.outcome = outcome
        self.reserved = reserved
        self.decisions = decisions
        self.authorizations = authorizations
        self.reservations = reservations
        self.unconsumed = unconsumed
        self.reason_has = reason_has
        self.problem = problem
        self.log_has = log_has
        self.ok = ok
        self.decision = decision
        self.recorded = recorded          # expected decision_recorded
        self.projection = projection      # expected authorization_projection
        self.decisions_are = decisions_are  # kinds on disk, when not ours
        self.live = live                  # expected authorization_live


def _accept(fixture, request):
    return accept_with_prefix(request)


def _no_answer(fixture, request):
    return None


def _decline(fixture, request):
    return "decline", None


def _edit_then_accept(fixture, request):
    # A core MissionError BEFORE the save: an edit lands between the card
    # and the answer, so the apply refuses stale (mission/service.py,
    # apply_human_decision, before ``_store.save``).
    decision_id = fixture.service.mint_decision_id(fixture.ingress)
    fixture.service.edit(fixture.current_mission, fixture.current_revision,
                         mission_proposal_arguments(objective="changed"),
                         decision_id, fixture.ingress)
    return accept_with_prefix(request)


def _patch_attr(target, attribute, exception, nth=1, when=None):
    """Wrap ``target.attribute`` so its ``nth`` matching call (after
    arming) raises ``exception``; returns (state, patcher)."""
    original = getattr(target, attribute)
    state, side_effect = _nth_call(nth, exception, when)
    return state, mock.patch.object(target, attribute, side_effect(original))


def _replace_fault(nth):
    # PRE-replace store failure: the temp file is written and fsynced,
    # ``os.replace`` itself raises -> the previous document stays.
    return lambda fixture: _patch_attr(
        os, "replace", OSError("injected before replace"), nth,
        when=lambda src, dst, *a, **k: dst.endswith("missions.json"))


def _dir_open_fault(nth):
    # POST-replace store failure: ``os.replace`` succeeded, then the
    # directory open for its fsync raises -> the NEW document is visible
    # although ``save`` raised. Only the two-argument read-only open of
    # the store directory counts (mkstemp and the lock open pass 3 args).
    return lambda fixture: _patch_attr(
        os, "open", OSError("injected after replace"), nth,
        when=lambda *a, **k: len(a) == 2 and a[1] == os.O_RDONLY)


def _typed_store_error(nth):
    return lambda fixture: _patch_attr(
        mission_store.MissionStore, "save",
        mission_store.MissionStoreError("injected typed store error",
                                        mission_store.PROBLEM_STORE_FULL),
        nth)


def _handler_write_fault(exception):
    return lambda fixture: _patch_attr(
        server_module.GrokMcpRequestHandler, "_write_event", exception,
        when=lambda handler, payload: payload.get("method")
        == "elicitation/create")


def _table_fault(attribute, exception):
    return lambda fixture: _patch_attr(elicitation.PendingTable, attribute,
                                       exception)


def _clock_fault(fixture):
    # The table's clock raises on its first call after arming: that call
    # is ``register`` (the clock is read before the entry is created).
    table = fixture.server.elicitations
    state, side_effect = _nth_call(1, RuntimeError("injected clock"))
    return state, mock.patch.object(table, "_clock", side_effect(table._clock))


def _register_none(fixture):
    state = {"armed": False}
    original = elicitation.PendingTable.register

    def wrapper(table, *args, **kwargs):
        if state["armed"]:
            return None
        return original(table, *args, **kwargs)

    return state, mock.patch.object(elicitation.PendingTable, "register",
                                    wrapper)


def _evaluate_fault(fixture):
    return _patch_attr(elicitation, "evaluate_response",
                       RuntimeError("injected evaluate"))


def _combine(*factories):
    """Several injections armed and started together."""
    def factory(fixture):
        pairs = [inner(fixture) for inner in factories]

        class All(object):
            def start(self):
                for _, patcher in pairs:
                    patcher.start()

            def stop(self):
                for _, patcher in reversed(pairs):
                    patcher.stop()

        class State(object):
            def __setitem__(self, key, value):
                for state, _ in pairs:
                    state[key] = value

        return State(), All()
    return factory


def _readback_get_fault(fixture):
    # The relay reads the mission once BEFORE the mint (call 1); the
    # readback after the failed apply is its second ``get`` (call 2).
    return _patch_attr(mission_service.MissionService, "get",
                       mission_store.MissionStoreError(
                           "injected readback failure"), 2)


_apply_pre_replace_then_readback_fault = _combine(_replace_fault(2),
                                                  _readback_get_fault)


def _validation_fault(fixture):
    # reviewer1 round-05 reproduction (a): the public authorization
    # validator raises while the relay reconciles.
    return _patch_attr(mission_service.MissionService, "validate_authorization",
                       RuntimeError("injected validation"))


def _projection_fault(fixture):
    # reviewer1 round-05 reproduction (b): the relay's own readback
    # projection/formatting raises.
    return _patch_attr(decision_tools, "_readback_outcome",
                       RuntimeError("injected projection"))


def _recorded_only_fault(fixture):
    return _patch_attr(decision_tools, "_recorded_only",
                       RuntimeError("injected recorded-only"))


def _applied_once_fault(fixture):
    # The relay's formatting of a SUCCESSFUL apply raises (first call
    # only): the reconciliation's own formatting then succeeds.
    return _patch_attr(decision_tools, "_applied", RuntimeError("format"), 1)


def _invalid_clock_in_decision_outcome(fixture):
    # reviewer1 round-05 reproduction (c): the core raises its own
    # MissionError AFTER its save — ``apply_human_decision`` saves, then
    # ``_decision_outcome`` reads the clock (mission/service.py:343 ->
    # :373 -> _now -> record.require_timestamp). The clock is made
    # invalid only for the duration of that call, so the readback after
    # it sees a valid clock again.
    original = mission_service.MissionService._decision_outcome
    state = {"armed": False}

    def wrapper(service, document, decision_record, idempotent):
        if not state["armed"]:
            return original(service, document, decision_record, idempotent)
        clock = service._clock
        service._clock = lambda: -1
        try:
            return original(service, document, decision_record, idempotent)
        finally:
            service._clock = clock

    return state, mock.patch.object(mission_service.MissionService,
                                    "_decision_outcome", wrapper)


def _no_fault(fixture):
    state = {"armed": False}

    class NoPatch(object):
        def start(self):
            pass

        def stop(self):
            pass

    return state, NoPatch()


def _release_fault(nth):
    # Round 06: ``channel.release()`` raises. Call 1 after arming is the
    # relay's own release in ``decision_tools._decide``; call 2 is the
    # server's release in ``_decision_call``.
    return lambda fixture: _patch_attr(
        elicitation.ElicitationChannel, "release", RuntimeError("release"),
        nth)


def _applied_always_fault(fixture):
    return _patch_every(decision_tools, "_applied", RuntimeError("format"))


def _patch_every(target, attribute, exception):
    """Every call after arming raises."""
    state = {"armed": False}
    original = getattr(target, attribute)

    def wrapper(*args, **kwargs):
        if state["armed"]:
            raise exception
        return original(*args, **kwargs)

    return state, mock.patch.object(target, attribute, wrapper)


def _refusal_fault(fixture):
    return _patch_every(decision_tools, "refusal", RuntimeError("refusal"))


def _server_cleanup_boundary_fault(fixture):
    # Round 07: the server's cleanup call boundary itself raises (the
    # ``best_effort`` call for the closure's discard), after admission.
    state = {"armed": False}
    original = elicitation.best_effort

    def wrapper(label, *args, **kwargs):
        if state["armed"] and label == "cleanup discard":
            raise RuntimeError("cleanup boundary")
        return original(label, *args, **kwargs)

    return state, mock.patch.object(elicitation, "best_effort", wrapper)


def _log_writer_fault(fixture):
    # Round 07: the configured log writer raises OSError on every line.
    state = {"armed": False}
    original = fixture.server.log_writer

    def writer(text):
        if state["armed"]:
            raise OSError("log writer down")
        return original(text)

    fixture.server.log_writer = writer

    class NoPatch(object):
        def start(self):
            pass

        def stop(self):
            fixture.server.log_writer = original

    return state, NoPatch()


def _unlock_fault(nth):
    # S1-C3: the lock-context exit after a save — ``fcntl.flock(...,
    # LOCK_UN)`` at workflow_authority/atomic.py:88 raises. Unlock 1 after
    # arming is the mint's, unlock 2 the apply's.
    return lambda fixture: _patch_attr(
        fcntl, "flock", OSError("injected unlock"), nth,
        when=lambda descriptor, operation: operation == fcntl.LOCK_UN)


def _presentation_fault(fixture):
    state = {"armed": False}

    def fault(request_id):
        if state["armed"]:
            raise BrokenPipeError("injected presentation")

    fixture.server.presentation_fault = fault

    class NoPatch(object):
        def start(self):
            pass

        def stop(self):
            pass

    return state, NoPatch()


STORE_FULL = mission_store.PROBLEM_STORE_FULL

# The lifecycle, in the order the code runs it (see the evidence table):
#   1 claim -> 2 mint reservation -> 3 register entry + bind probe ->
#   4 write request event -> 5 wait -> 6 deliver/admit (other thread) ->
#   7 evaluate -> 8 apply decision -> 9 write result -> 10 cleanup.
# (The card is rendered before the claim; it holds nothing.)
LIFECYCLE_ROWS = [
    Boundary("1 claim raises", _table_fault("claim", RuntimeError("claim")),
             _no_answer, final_json=True, status="refused", outcome=None,
             reserved=False, decisions=0, authorizations=0, reservations=0,
             unconsumed=0, reason_has="decision relay raised RuntimeError"),
    Boundary("2a mint typed store error (pre-write)", _typed_store_error(1),
             _no_answer, final_json=True, status="refused", outcome=None,
             reserved=False, decisions=0, authorizations=0, reservations=0,
             unconsumed=0, problem=STORE_FULL),
    Boundary("2b mint pre-replace", _replace_fault(1), _no_answer,
             final_json=True, status="store_outcome_uncertain", outcome=None,
             reserved=False, decisions=0, authorizations=0, reservations=0,
             unconsumed=0, reason_has="not known to this relay"),
    Boundary("2c mint post-replace", _dir_open_fault(1), _no_answer,
             final_json=True, status="store_outcome_uncertain", outcome=None,
             reserved=False, decisions=0, authorizations=0, reservations=1,
             unconsumed=1, reason_has="not known to this relay"),
    Boundary("3a register: clock raises", _clock_fault, _no_answer,
             final_json=True, status="refused", outcome="stream_failed",
             reserved=True, decisions=0, authorizations=0, reservations=1,
             unconsumed=1, reason_has="after reserving the decision id",
             recorded=False),
    Boundary("3b register refuses (None)", _register_none, _no_answer,
             final_json=True, status="not_recorded", outcome="table_full",
             reserved=True, decisions=0, authorizations=0, reservations=1,
             unconsumed=1, recorded=False),
    Boundary("4a request write raises RuntimeError",
             _handler_write_fault(RuntimeError("write")), _no_answer,
             status="not_recorded", outcome="stream_failed", reserved=True,
             decisions=0, authorizations=0, reservations=1, unconsumed=1,
             log_has="stream failure RuntimeError", recorded=False),
    Boundary("4b request write raises OSError",
             _handler_write_fault(BrokenPipeError("write")), _no_answer,
             status="not_recorded", outcome="write_failed", reserved=True,
             decisions=0, authorizations=0, reservations=1, unconsumed=1,
             recorded=False),
    Boundary("5 wait raises", _table_fault("wait", RuntimeError("wait")),
             _no_answer, status="refused", outcome="stream_failed",
             reserved=True, decisions=0, authorizations=0, reservations=1,
             unconsumed=1, reason_has="after reserving the decision id",
             recorded=False),
    # Evaluation happens AT ADMISSION (under the table lock, in the
    # response POST's handler): a raise there admits nothing, that POST
    # fails, and the waiter expires within this row's short validity.
    Boundary("7 evaluate raises at admission", _evaluate_fault, _accept,
             status="not_recorded", outcome="expired", reserved=True,
             decisions=0, authorizations=0, reservations=1, unconsumed=1,
             recorded=False, log_has="handler failure RuntimeError",
             validity=1.0, tolerant=True),
    Boundary("8a apply typed store error (pre-write)", _typed_store_error(2),
             _accept, status="refused", outcome="accept", reserved=True,
             decisions=0, authorizations=0, reservations=1, unconsumed=1,
             problem=STORE_FULL, reason_has="readback shows no decision",
             recorded=False),
    Boundary("8b apply pre-replace", _replace_fault(2), _accept,
             status="refused", outcome="accept", reserved=True, decisions=0,
             authorizations=0, reservations=1, unconsumed=1,
             reason_has="readback shows no decision", recorded=False),
    Boundary("8c apply post-replace", _dir_open_fault(2), _accept,
             status="applied", outcome="accept", reserved=True, decisions=1,
             authorizations=1, reservations=1, unconsumed=0, ok=True,
             decision="APPROVE", reason_has="reported from readback",
             recorded=True, projection="available"),
    Boundary("8d apply pre-replace, readback raises",
             _apply_pre_replace_then_readback_fault, _accept,
             status="store_outcome_uncertain", outcome="accept",
             reserved=True, decisions=0, authorizations=0, reservations=1,
             unconsumed=1, reason_has="NOT re-applied", recorded=None),
    # -- round 05: the apply is durable on disk, the reconciliation itself
    #    fails part-way (reviewer1's two reproductions), or the core raises
    #    after its save, or before it.
    Boundary("8e apply post-replace, authorization validation raises in"
             " reconciliation", _combine(_dir_open_fault(2), _validation_fault),
             _accept, status="applied", outcome="accept", reserved=True,
             decisions=1, authorizations=1, reservations=1, unconsumed=0,
             ok=True, decision="APPROVE", recorded=True,
             projection="unavailable (RuntimeError)", live=None,
             reason_has="projection could not be computed (RuntimeError)"),
    Boundary("8f apply post-replace, readback projection/formatting raises",
             _combine(_dir_open_fault(2), _projection_fault), _accept,
             status="applied", outcome="accept", reserved=True, decisions=1,
             authorizations=1, reservations=1, unconsumed=0, ok=True,
             decision="APPROVE", recorded=True,
             projection="unavailable (RuntimeError)", live=None,
             reason_has="projection could not be computed (RuntimeError)"),
    Boundary("8g core MissionError AFTER save (invalid clock in"
             " _decision_outcome)", _invalid_clock_in_decision_outcome,
             _accept, status="applied", outcome="accept", reserved=True,
             decisions=1, authorizations=1, reservations=1, unconsumed=0,
             ok=True, decision="APPROVE", recorded=True,
             projection="available", reason_has="raised MissionError after"
             " the decision was durably recorded"),
    Boundary("8h core MissionError BEFORE save (stale revision)", _no_fault,
             _edit_then_accept, status="refused", outcome="accept",
             reserved=True, decisions=1, authorizations=0, reservations=2,
             unconsumed=1, problem="mission_stale_revision",
             reason_has="readback shows no decision", recorded=False,
             decisions_are=["EDIT"]),
    Boundary("8i relay formatting raises after a successful apply",
             _applied_once_fault, _accept, status="applied", outcome="accept",
             reserved=True, decisions=1, authorizations=1, reservations=1,
             unconsumed=0, ok=True, decision="APPROVE", recorded=True,
             projection="available",
             reason_has="reported from the apply's retained result"),
    Boundary("8j apply post-replace, both readback projections raise"
             " (last-resort guard)",
             _combine(_dir_open_fault(2), _projection_fault,
                      _recorded_only_fault), _accept,
             status="applied", outcome="accept", ok=True, decision="APPROVE",
             reserved=True, decisions=1, authorizations=1, reservations=1,
             unconsumed=0, recorded=True, primitives=True,
             projection="unavailable (RuntimeError)",
             reason_has="IS recorded (proven by readback)"),
    # -- round 06: cleanup can never replace a result; the commit point is
    #    recorded before any formatting; refusal formatting is guarded;
    #    lock-context exit after a save (S1-C3).
    Boundary("10a relay release raises after APPROVE", _release_fault(1),
             _accept, status="applied", outcome="accept", ok=True,
             decision="APPROVE", reserved=True, decisions=1, authorizations=1,
             reservations=1, unconsumed=0, recorded=True,
             projection="available",
             reason_has="cleanup release raised RuntimeError; suppressed"),
    Boundary("10b relay release raises after DENY", _release_fault(1),
             _decline, status="applied", outcome="decline", ok=True,
             decision="DENY", reserved=True, decisions=1, authorizations=0,
             reservations=1, unconsumed=0, recorded=True,
             projection="available",
             reason_has="cleanup release raised RuntimeError; suppressed"),
    # -- round 07: the answer is preserved from admission; logging is
    #    best-effort everywhere; a decided call is always answered.
    Boundary("11a wait-discard raises after admitted APPROVE",
             lambda fixture: _patch_attr(elicitation.PendingTable, "discard",
                                         RuntimeError("wait discard"), 1),
             _accept, status="applied", outcome="accept", ok=True,
             decision="APPROVE", reserved=True, decisions=1, authorizations=1,
             reservations=1, unconsumed=0, recorded=True,
             projection="available"),
    Boundary("11b wait-discard raises after admitted DENY",
             lambda fixture: _patch_attr(elicitation.PendingTable, "discard",
                                         RuntimeError("wait discard"), 1),
             _decline, status="applied", outcome="decline", ok=True,
             decision="DENY", reserved=True, decisions=1, authorizations=0,
             reservations=1, unconsumed=0, recorded=True,
             projection="available"),
    Boundary("11c server cleanup boundary raises after admitted APPROVE",
             _server_cleanup_boundary_fault, _accept, status="applied",
             outcome="accept", ok=True, decision="APPROVE", reserved=True,
             decisions=1, authorizations=1, reservations=1, unconsumed=0,
             recorded=True, projection="available"),
    Boundary("11d server cleanup boundary raises after admitted DENY",
             _server_cleanup_boundary_fault, _decline, status="applied",
             outcome="decline", ok=True, decision="DENY", reserved=True,
             decisions=1, authorizations=0, reservations=1, unconsumed=0,
             recorded=True, projection="available"),
    Boundary("11e second release raises AND log writer raises OSError,"
             " APPROVE", _combine(_release_fault(2), _log_writer_fault),
             _accept, status="applied", outcome="accept", ok=True,
             decision="APPROVE", reserved=True, decisions=1, authorizations=1,
             reservations=1, unconsumed=0, recorded=True,
             projection="available"),
    Boundary("11f second release raises AND log writer raises OSError,"
             " DENY", _combine(_release_fault(2), _log_writer_fault),
             _decline, status="applied", outcome="decline", ok=True,
             decision="DENY", reserved=True, decisions=1, authorizations=0,
             reservations=1, unconsumed=0, recorded=True,
             projection="available"),
    Boundary("10c server release raises after APPROVE", _release_fault(2),
             _accept, status="applied", outcome="accept", ok=True,
             decision="APPROVE", reserved=True, decisions=1, authorizations=1,
             reservations=1, unconsumed=0, recorded=True,
             projection="available",
             log_has="cleanup release raised RuntimeError; suppressed"),
    Boundary("10d server release raises after DENY", _release_fault(2),
             _decline, status="applied", outcome="decline", ok=True,
             decision="DENY", reserved=True, decisions=1, authorizations=0,
             reservations=1, unconsumed=0, recorded=True,
             projection="available",
             log_has="cleanup release raised RuntimeError; suppressed"),
    Boundary("10e formatting AND readback fail after a successful APPROVE",
             _combine(_applied_always_fault, _readback_get_fault), _accept,
             status="applied", outcome="accept", ok=True, decision="APPROVE",
             reserved=True, decisions=1, authorizations=1, reservations=1,
             unconsumed=0, recorded=True, primitives=True,
             projection="unavailable (RuntimeError)",
             reason_has="IS recorded (proven by the apply's return)"),
    Boundary("10f formatting AND readback fail after a successful DENY",
             _combine(_applied_always_fault, _readback_get_fault), _decline,
             status="applied", outcome="decline", ok=True, decision="DENY",
             reserved=True, decisions=1, authorizations=0, reservations=1,
             unconsumed=0, recorded=True, primitives=True,
             projection="unavailable (RuntimeError)",
             reason_has="IS recorded (proven by the apply's return)"),
    Boundary("10g refusal formatting raises post-mint (stream path raised)",
             _combine(_table_fault("wait", RuntimeError("wait")),
                      _refusal_fault), _no_answer,
             status="refused", outcome=None, reserved=True, decisions=0,
             authorizations=0, reservations=1, unconsumed=1, recorded=False,
             reason_has="is not recorded (nothing was applied)"),
    Boundary("2d mint: lock-context exit raises after save", _unlock_fault(1),
             _no_answer, final_json=True, status="store_outcome_uncertain",
             outcome=None, reserved=False, decisions=0, authorizations=0,
             reservations=1, unconsumed=1, reason_has="not known to this relay"),
    Boundary("8k apply: lock-context exit raises after save",
             _unlock_fault(2), _accept, status="applied", outcome="accept",
             ok=True, decision="APPROVE", reserved=True, decisions=1,
             authorizations=1, reservations=1, unconsumed=0, recorded=True,
             projection="available", reason_has="raised OSError after the"
             " decision was durably recorded"),
    Boundary("9 result write raises after apply", _presentation_fault,
             _accept, status=None, outcome=None, reserved=True, decisions=1,
             authorizations=1, reservations=1, unconsumed=0,
             log_has="presentation write failed after admission"
                     " BrokenPipeError"),
]


class _FreshServerMixin(Fixture):
    """A server per row/run, started and stopped explicitly (no
    cleanups), over a core that can be replaced between runs."""

    def _fresh_core(self):
        self.logs[:] = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = os.path.join(self.tmp.name, "protected")
        self.store = mission_store.MissionStore(self.directory)
        self.service = mission_service.MissionService(
            self.store, lambda: self.now[0])
        self.controller = controller_module.GrokMcpController(
            self.operator.session(), REPOSITORY, mint_ref=deterministic_refs(),
            mission_service=self.service,
        )

    def serve(self, **server_kwargs):
        server = server_module.GrokMcpServer(
            ("127.0.0.1", 0), self.controller, bearer_token=TOKEN,
            log_writer=self.logs.append, **server_kwargs
        )
        self.server = server
        self.server_thread = threading.Thread(target=server.serve_forever,
                                              daemon=True)
        self.server_thread.start()
        return StreamClient(server.server_address[1])

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(5)
        # Handler threads finish on their own after the client closed;
        # give them a bounded moment before the strict count.
        deadline = time.monotonic() + 5
        while threading.active_count() != self.threads_before and (
            time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assert_no_stray_threads()


class HLifecycleMatrixTests(_FreshServerMixin):

    def _run_row(self, row):
        mission_id, revision = self.propose()
        self.current_mission, self.current_revision = mission_id, revision
        server_kwargs = {"elicitation_poll_seconds": 0.02}
        if row.validity is not None:
            server_kwargs["elicitation_validity_seconds"] = row.validity
        client = self.serve(**server_kwargs)
        client.initialize()
        state, patcher = row.patch(self)
        patcher.start()
        try:
            state["armed"] = True
            request, final = client.run_decision(
                mission_id, revision,
                lambda request: row.answer(self, request),
                tolerant=row.tolerant)
        finally:
            state["armed"] = False
            patcher.stop()
        # Every boundary: nothing held, nothing pending, claims balanced.
        self.assertEqual(self.server.elicitations.count, 0)
        self.assertEqual(self.server.elicitations.claimed, 0)
        # Readback-consistent effect counts, from disk.
        self.assertEqual(len(self.decisions(mission_id)), row.decisions)
        self.assertEqual(len(self.authorizations()), row.authorizations)
        self.assertEqual(len(self.reservations()), row.reservations)
        self.assertEqual(len(self.unconsumed_reservations()), row.unconsumed)
        if row.decisions_are is not None:
            self.assertEqual([d["decision"] for d in self.decisions(mission_id)],
                             row.decisions_are)
        if row.log_has is not None:
            self.assertTrue(any(row.log_has in line for line in self.logs),
                            self.logs)
        if row.status is None:
            # The result could not be presented; there is nothing to read.
            self.assertIsNone(final)
            return
        self.assertIsNotNone(final, row.name)
        if row.final_json:
            self.assertIsNone(request)
        structured = structured_of(final)
        self.assertEqual(structured["status"], row.status)
        self.assertEqual(structured["ok"], row.ok)
        self.assertEqual(final["result"].get("isError", False), not row.ok)
        self.assertEqual(structured["elicitation_outcome"], row.outcome)
        self.assertEqual(structured["decision_recorded"], row.recorded,
                         row.name)
        self.assertEqual(structured["authorization_projection"],
                         row.projection, row.name)
        if row.reserved:
            # The reported id IS a reserved id on disk, always — and the
            # unconsumed one when exactly one stays unconsumed.
            self.assertIn(structured["decision_id"], self.reservation_ids())
            if row.unconsumed == 1:
                self.assertEqual(
                    [k for k, r in self.document()["reservations"].items()
                     if r["kind"] == "decision" and r["consumed_by"] is None],
                    [structured["decision_id"]])
            if row.decisions and row.decisions_are is None:
                self.assertEqual(
                    [d["decision_id"] for d in self.decisions(mission_id)],
                    [structured["decision_id"]])
        else:
            self.assertIsNone(structured["decision_id"])
        if row.problem is not None:
            self.assertEqual(structured["problem"], row.problem)
        if row.reason_has is not None:
            self.assertIn(row.reason_has, structured["reason"])
        if row.decision is not None:
            self.assertEqual(structured["decision"], row.decision)
            self.assertEqual(structured["mission_id"], mission_id)
            self.assertEqual(structured["revision"], revision)
            if row.primitives:
                # Carried state only: nothing read from a record.
                self.assertIsNone(structured["authorization_id"])
                self.assertIsNone(structured["state"])
            else:
                self.assertEqual(
                    structured["state"],
                    self.document()["missions"][mission_id]["state"])
                if row.decision == "APPROVE":
                    self.assertEqual(structured["authorization_live"], row.live)
                    self.assertRegex(structured["authorization_id"],
                                     r"^ma-[0-9a-f]{32}$")
                else:
                    self.assertIsNone(structured["authorization_id"])
                    self.assertIsNone(structured["authorization_live"])
        # The structured result conforms to the served output schema.
        schema = [tool for tool in protocol.TOOLS
                  if tool["name"] == DECIDE][0]["outputSchema"]
        self.assertTrue(conforms(schema, structured), (row.name, structured))
        # Truthful wording: no claim about the peer, no "no effect" claim
        # where the relay could not know.
        text = json.dumps(structured)
        self.assertNotIn("disconnect", text)
        if row.status == "store_outcome_uncertain":
            self.assertNotIn("nothing was reserved", text)
            self.assertNotIn("no decision was recorded", text)

    def test_H1_lifecycle_matrix_every_boundary_cleans_up_and_reports_truthfully(self):
        # ONE table-driven fault-injection test: each row injects a real
        # exception at one lifecycle boundary through the production path
        # (real server, real store on disk, real client) in its own
        # fresh server + store, then asserts the readback-consistent
        # counts and the reported id/outcome/status.
        names = [row.name for row in LIFECYCLE_ROWS]
        self.assertEqual(len(names), len(set(names)))
        for index, row in enumerate(LIFECYCLE_ROWS):
            with self.subTest(row=row.name):
                if index:
                    self._fresh_core()
                try:
                    self._run_row(row)
                finally:
                    self._stop_server()

    def test_H2_matrix_covers_every_store_write_pre_and_post_replace(self):
        # The matrix itself is pinned: both store writes (mint, apply) are
        # injected on both sides of ``os.replace``, plus the registration
        # clock and the apply-time readback failure the brief names.
        names = {row.name for row in LIFECYCLE_ROWS}
        for required in ("2b mint pre-replace", "2c mint post-replace",
                         "8b apply pre-replace", "8c apply post-replace",
                         "3a register: clock raises",
                         "8d apply pre-replace, readback raises",
                         "8a apply typed store error (pre-write)"):
            self.assertIn(required, names)
        # Round 05: the reconciliation path itself, and the core raising on
        # either side of its save.
        for prefix in ("8e apply post-replace, authorization validation",
                       "8f apply post-replace, readback projection",
                       "8g core MissionError AFTER save",
                       "8h core MissionError BEFORE save",
                       "8i relay formatting raises",
                       "8j apply post-replace, both readback projections",
                       "10a relay release raises after APPROVE",
                       "10b relay release raises after DENY",
                       "10c server release raises after APPROVE",
                       "10d server release raises after DENY",
                       "10e formatting AND readback fail after a successful"
                       " APPROVE",
                       "10f formatting AND readback fail after a successful"
                       " DENY",
                       "10g refusal formatting raises post-mint",
                       "2d mint: lock-context exit raises after save",
                       "8k apply: lock-context exit raises after save",
                       "11a wait-discard raises after admitted APPROVE",
                       "11b wait-discard raises after admitted DENY",
                       "11c server cleanup boundary raises after admitted"
                       " APPROVE",
                       "11d server cleanup boundary raises after admitted"
                       " DENY",
                       "11e second release raises AND log writer raises",
                       "11f second release raises AND log writer raises"):
            self.assertTrue(any(name.startswith(prefix) for name in names),
                            prefix)
        # The core's post-save raise site the matrix targets is real: the
        # save precedes the projection in apply_human_decision.
        source = (REPO_ROOT / "mission" / "service.py").read_text()
        apply_at = source.index("def apply_human_decision")
        save_at = source.index("self._store.save(document)", apply_at)
        self.assertLess(save_at, source.index("self._decision_outcome(",
                                              save_at))
        # The injections sit on the primitive's own two sides of replace:
        # the primitive is used, never modified (tree identity in the
        # evidence shows workflow_authority/atomic.py unchanged).
        source = (REPO_ROOT / "workflow_authority" / "atomic.py").read_text()
        self.assertLess(source.index("os.replace(temp_path, path)"),
                        source.index("os.open(directory, os.O_RDONLY)"))


# ====================================================================
# H3. Automated fault sweep over deterministic semantic fault points
# ====================================================================
#
# Mechanism (per the correction-4 sweep amendment):
# - ENUMERATE, from one baseline APPROVE and one baseline DENY, the
#   Python-level functions of the six in-scope modules that the decide
#   handler thread executes, as (module, owner, name, occurrence). The
#   profile hook is used for recording ONLY, filtered to that thread;
#   C-level calls, lock/Event primitives and the lock context managers
#   are not points; closures/generators that cannot be reached as an
#   attribute are counted as unpatchable and skipped (reported).
# - The enumeration must be identical across two recordings, or the
#   test FAILS with a clear message (never a silent degradation).
# - INJECT by PATCHING the target so that its N-th invocation in the
#   decide thread raises a real RuntimeError; never from a hook.
# - ISOLATE: every case runs on its own server, store and client inside
#   a child process, with a hard per-case timeout; a stranded case is a
#   FAILURE within the bound, never a hang.
# - SCOPE: by default the CI subset (every point in decision_tools,
#   elicitation, server and atomic, plus the first occurrence of each
#   distinct function in service and store); DI_ELICITATION_SWEEP_FULL=1
#   sweeps every enumerated point (focused-only; runtime reported).
# This is a sweep of ENUMERATED SEMANTIC POINTS. It is not a claim of
# complete call-site coverage.

_SWEEP_FILES = {
    str(REPO_ROOT / "grok_mcp" / "decision_tools.py"): "decision_tools",
    str(REPO_ROOT / "grok_mcp" / "elicitation.py"): "elicitation",
    str(REPO_ROOT / "grok_mcp" / "server.py"): "server",
    str(REPO_ROOT / "mission" / "service.py"): "service",
    str(REPO_ROOT / "mission" / "store.py"): "store",
    str(REPO_ROOT / "workflow_authority" / "atomic.py"): "atomic",
}
_SWEEP_MODULES = {
    "decision_tools": decision_tools, "elicitation": elicitation,
    "server": server_module, "service": mission_service,
    "store": mission_store, "atomic": atomic_module,
}
# server.py: only the decision path (the response POST's own handler
# functions are not the path under test; ``deliver`` is elicitation.py).
_SWEEP_SERVER_FUNCTIONS = {"_decision_call", "elicit", "_start_stream",
                           "_write_event", "_stream_open", "_cleanup"}
# Lock context managers are not semantic fault points (amendment 1).
_SWEEP_EXCLUDED = {("atomic", None, "exclusive_store_lock"),
                   ("store", "MissionStore", "lock")}
_SWEEP_CASE_TIMEOUT_SECONDS = 20
_SWEEP_EXIT_TIMEOUT_SECONDS = 10
_SWEEP_FULL_ENV = "DI_ELICITATION_SWEEP_FULL"
_SWEEP_LOCAL = threading.local()
_SWEEP_ADMISSION = {}
_UNPATCHABLE = object()


def _terminate_worker(process, timeout):
    """Kill a still-running worker and wait for it, BOUNDED; True when
    the worker is gone within the bound."""
    try:
        if process.poll() is None:
            process.kill()
    except Exception:  # noqa: BLE001 - a dead process is fine
        pass
    try:
        process.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


_PUMP_POLL_SECONDS = 0.05
_PUMP_CHUNK_BYTES = 65536


def _default_open_stderr(path):
    return open(path, "w")


class _WorkerSession(object):
    """One child worker, owned from the instant ``popen`` returns.

    Containment proof:
    - stderr is opened BEFORE ``popen``; the moment ``popen`` returns the
      child is recorded and every later constructor step (closing the
      stderr handle included) runs under a guard whose failure path is
      ``close`` (kill + bounded reap + pipe close) — so no exception
      between creation and ownership can leave a live child or an open
      pipe;
    - the pump never touches the ``TextIOWrapper``/``BufferedReader`` of
      ``process.stdout``: it reads the RAW descriptor with ``os.read``
      after a ``select`` bounded by ``_PUMP_POLL_SECONDS`` and re-checks a
      stop flag between polls, decoding lines itself. No Python-level
      lock is shared with shutdown, and no read can block unboundedly (a
      readable pipe returns data or EOF at once);
    - ``close`` sets the stop flag, kills and reaps within
      ``exit_timeout``, joins the pump within a bound, then closes the
      wrapper (its buffer lock is never held by anyone, so the close is
      bounded) and the stderr handle. A pump still alive after its bound
      is recorded as ``pump_leaked`` and, with a worker that outlived a
      kill, as ``hung``: a runner failure, never a hang."""

    def __init__(self, popen, thread_factory, argv, cwd, env, stderr_path,
                 case_timeout, exit_timeout, open_stderr=None):
        self.thread_factory = thread_factory
        self.case_timeout = case_timeout
        self.exit_timeout = exit_timeout
        self.lines = queue.Queue()
        self.pump_thread = None
        self.stopping = False
        self.hung = False
        self.pump_leaked = False
        self.closed = False
        self.process = None
        self.stderr_handle = (open_stderr or _default_open_stderr)(stderr_path)
        try:
            self.process = popen(argv, cwd=cwd, env=env,
                                 stdout=subprocess.PIPE,
                                 stderr=self.stderr_handle, text=True)
        except BaseException:
            self._close_stderr()
            raise
        # OWNED from here: any failure below kills, reaps and closes.
        try:
            self.fd = self.process.stdout.fileno()
            self._close_stderr()
        except BaseException:
            self.close()
            raise

    def _close_stderr(self):
        # The reference is dropped only after a SUCCESSFUL close, so a
        # close that raised is retried by the guard, never forgotten.
        handle = self.stderr_handle
        if handle is not None:
            handle.close()
            self.stderr_handle = None

    def start(self):
        # The thread is recorded only once it has started: a thread that
        # never started must not be joined (a real Thread raises there).
        try:
            thread = self.thread_factory(target=self._pump, daemon=True)
            thread.start()
            self.pump_thread = thread
        except BaseException:
            self.close()
            raise

    def _pump(self):
        buffer = b""
        try:
            while not self.stopping:
                readable, _, _ = select.select([self.fd], [], [],
                                               _PUMP_POLL_SECONDS)
                if not readable:
                    continue
                chunk = os.read(self.fd, _PUMP_CHUNK_BYTES)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self.lines.put(line.decode("utf-8", "replace") + "\n")
        except (OSError, ValueError):
            pass
        finally:
            if buffer:
                self.lines.put(buffer.decode("utf-8", "replace"))
            self.lines.put(None)

    def next_line(self):
        return self.lines.get(timeout=self.case_timeout)

    def terminate(self):
        return _terminate_worker(self.process, self.exit_timeout)

    def close(self):
        """Idempotent and BOUNDED: stop flag, kill + bounded reap, bounded
        pump join, close the pipe exactly once (no lock held by anyone),
        close stderr; record a leaked pump / hung worker as failures."""
        if self.closed:
            return
        self.closed = True
        self.stopping = True
        gone = True
        if self.process is not None:
            gone = _terminate_worker(self.process, self.exit_timeout)
        if self.pump_thread is not None:
            self.pump_thread.join(max(self.exit_timeout, 4 * _PUMP_POLL_SECONDS))
            self.pump_leaked = self.pump_thread.is_alive()
        if self.process is not None and not self.pump_leaked:
            # The one close of the raw descriptor, performed only when no
            # reader can still be inside ``os.read`` on it: closing a
            # descriptor under a live reader would let the next pipe reuse
            # its number and be read by the wrong thread. A leaked pump is
            # a recorded failure below; its descriptor is left to it.
            try:
                self.process.stdout.close()
            except Exception:  # noqa: BLE001 - closing is best-effort
                pass
        try:
            self._close_stderr()
        except Exception:  # noqa: BLE001 - closing is best-effort
            handle, self.stderr_handle = self.stderr_handle, None
            if handle is not None:
                try:
                    handle.close()
                except Exception:  # noqa: BLE001
                    pass
        self.hung = (not gone) or self.pump_leaked


def _sweep_owner(frame, module):
    """The patchable owner of the function running in ``frame``: None for
    a module-level function, a class name for a method (plain or
    static), or _UNPATCHABLE for anything not reachable as an attribute
    (closures such as the server's ``elicit``, generators)."""
    code = frame.f_code
    module_object = _SWEEP_MODULES[module]
    candidate = module_object.__dict__.get(code.co_name)
    if getattr(candidate, "__code__", None) is code:
        return None
    for attribute, obj in module_object.__dict__.items():
        if isinstance(obj, type):
            member = obj.__dict__.get(code.co_name)
            function = getattr(member, "__func__", member)
            if getattr(function, "__code__", None) is code:
                return attribute
    return _UNPATCHABLE


class _PointRecorder(object):
    """Recording-only profile hook: the ordered semantic points executed
    by the decide handler thread (the thread that enters
    ``_decision_call``) while armed."""

    def __init__(self):
        self.points = []
        self.unpatchable = []
        self.thread = None
        self.armed = False

    def hook(self, frame, event, arg):
        if event != "call" or not self.armed:
            return
        module = _SWEEP_FILES.get(frame.f_code.co_filename)
        if module is None:
            return
        name = frame.f_code.co_name
        if name.startswith("<"):
            return  # comprehension / lambda frames are not functions
        if module == "server" and name not in _SWEEP_SERVER_FUNCTIONS:
            return
        ident = threading.get_ident()
        if self.thread is None and name == "_decision_call":
            self.thread = ident
        if ident != self.thread:
            return
        owner = _sweep_owner(frame, module)
        if owner is _UNPATCHABLE:
            self.unpatchable.append((module, name))
            return
        key = (module, owner, name)
        if key in _SWEEP_EXCLUDED:
            return
        occurrence = 1 + sum(1 for point in self.points if point[:3] == key)
        self.points.append((module, owner, name, occurrence))


def _sweep_holder(point):
    module, owner, name, _ = point
    module_object = _SWEEP_MODULES[module]
    return module_object if owner is None else getattr(module_object, owner)


def _install_fault(point, fired):
    """Patch the point's target so that its N-th invocation in the decide
    thread raises; ``fired`` receives where that happened."""
    holder = _sweep_holder(point)
    name, occurrence = point[2], point[3]
    member = holder.__dict__[name] if isinstance(holder, type) else getattr(
        holder, name)
    is_static = isinstance(member, staticmethod)
    original = member.__func__ if is_static else member
    counter = {"n": 0}

    def wrapper(*args, **kwargs):
        if getattr(_SWEEP_LOCAL, "active", False):
            counter["n"] += 1
            if counter["n"] == occurrence and not fired:
                stack = []
                walker = sys._getframe(1)
                while walker is not None:
                    stack.append(walker.f_code.co_name)
                    walker = walker.f_back
                fired.update({
                    "point": list(point),
                    "during_mint": "mint_decision_id" in stack,
                    "after_record": getattr(_SWEEP_LOCAL, "entered_record",
                                            False),
                })
                raise RuntimeError("sweep fault at %r" % (point,))
        return original(*args, **kwargs)

    patchers = [mock.patch.object(
        holder, name, staticmethod(wrapper) if is_static else wrapper)]
    if not isinstance(holder, type):
        # A module-level function may be bound by name in another
        # in-scope module (``from workflow_authority.atomic import
        # atomic_write_json`` in mission/store.py): patch every binding.
        for other in _SWEEP_MODULES.values():
            for attribute, value in list(other.__dict__.items()):
                if value is original and (other, attribute) != (holder, name):
                    patchers.append(mock.patch.object(other, attribute, wrapper))
    return patchers


def _install_markers():
    """Mark the decide handler thread (and whether ``_record`` was
    entered) so injections count invocations in that thread only."""
    original_call = server_module.GrokMcpRequestHandler._decision_call

    def decision_call(self, *args, **kwargs):
        _SWEEP_LOCAL.active = True
        _SWEEP_LOCAL.entered_record = False
        try:
            return original_call(self, *args, **kwargs)
        finally:
            _SWEEP_LOCAL.active = False

    original_record = decision_tools._record

    def record(*args, **kwargs):
        _SWEEP_LOCAL.entered_record = True
        return original_record(*args, **kwargs)

    # ADMISSION is recorded through the table's own admission hook, so
    # the oracle can start at the moment an answer was admitted.
    original_answered = elicitation.PendingTable._answered

    def answered(table, entry):
        _SWEEP_ADMISSION["answer"] = entry.answer
        return original_answered(table, entry)

    return [
        mock.patch.object(server_module.GrokMcpRequestHandler,
                          "_decision_call", decision_call),
        mock.patch.object(decision_tools, "_record", record),
        mock.patch.object(elicitation.PendingTable, "_answered", answered),
    ]


def _sweep_select(points):
    """The CI subset: the first occurrence of every distinct function,
    plus every further occurrence at or after the mint (the post-mint
    path is the class under proof) in the three grok_mcp modules and
    atomic.py. Repeated pre-mint occurrences (card rendering) and the
    store validators' recursion are left to the full mode."""
    if os.environ.get(_SWEEP_FULL_ENV) == "1":
        return list(points), "full"
    mint_at = next((index for index, point in enumerate(points)
                    if point[2] == "mint_decision_id"), len(points))
    chosen = []
    seen = set()
    for index, point in enumerate(points):
        key = tuple(point[:3])
        post_mint = index >= mint_at and point[0] in (
            "decision_tools", "elicitation", "server", "atomic")
        if key not in seen or post_mint:
            chosen.append(point)
        seen.add(key)
    return chosen, "ci-subset"


class _SweepRunnerMixin(_FreshServerMixin):
    """The sweep's call, case, recorder, oracle and isolated runner,
    shared by the sweep itself (H3), the runner-failure tests (H4) and
    the oracle self-tests (H5)."""

    def _sweep_call(self, kind, recorder=None):
        """One decide call on a fresh server + store; returns JSON-safe
        facts about the result, the table and the disk."""
        self._fresh_core()
        client = self.serve(elicitation_poll_seconds=5.0,
                            elicitation_validity_seconds=1.5)
        client.initialize()
        mission_id, revision = self.propose()
        answer = _accept if kind == "APPROVE" else _decline
        if recorder is not None:
            recorder.armed = True
        try:
            request, final = client.run_decision(
                mission_id, revision, lambda req: answer(self, req),
                tolerant=True)
        finally:
            if recorder is not None:
                recorder.armed = False
        count = self.server.elicitations.count
        claimed = self.server.elicitations.claimed
        self._stop_server()
        reserved = self.reservation_ids()
        ours = [d for d in self.decisions(mission_id)
                if d["decision_id"] in reserved]
        return {
            "final": final, "count": count, "claimed": claimed,
            "reserved": reserved, "recorded": ours,
            "decision_count": len(self.decisions(mission_id)),
            "logs": list(self.logs),
            "respond_failure": client.respond_failure,
        }

    def _sweep_case(self, kind, point):
        """One injected case: fault patch, thread markers, one call."""
        fired = {}
        # The fault patch FIRST, then the markers, which must wrap the
        # already-patched functions (a fault on ``_decision_call`` or
        # ``_record`` must sit inside the marker that flags the thread).
        faults = _install_fault(point, fired)
        for patcher in faults:
            patcher.start()
        markers = _install_markers()
        for patcher in markers:
            patcher.start()
        _SWEEP_ADMISSION.clear()
        try:
            run = self._sweep_call(kind)
        finally:
            for patcher in reversed(markers + faults):
                patcher.stop()
        run["fired"] = fired or None
        answer = _SWEEP_ADMISSION.get("answer")
        run["admitted"] = list(answer) if answer is not None else None
        return run

    def _record_points(self, kind):
        recorder = _PointRecorder()
        threading.setprofile(recorder.hook)
        try:
            baseline = self._sweep_call(kind, recorder)
        finally:
            threading.setprofile(None)
        self.assertEqual(len(baseline["recorded"]), 1, kind)
        self.assertIsNotNone(recorder.thread, kind)
        return recorder.points, recorder.unpatchable

    def _check_invariants(self, kind, run, label):
        if run.get("timeout"):
            self.fail("%s: case exceeded %ds (stranded lock or blocked read)"
                      % (label, _SWEEP_CASE_TIMEOUT_SECONDS))
        if run.get("crashed"):
            self.fail("%s: worker ended before reporting (rc=%r, exited=%r):"
                      "\n%s" % (label, run.get("returncode"),
                                run.get("worker_exited"), run.get("stderr")))
        if run.get("worker_hung"):
            self.fail("%s: worker did not exit within the bound after a kill"
                      % label)
        self.assertIsNotNone(
            run["fired"],
            "%s: the enumerated point was never invoked in the decide"
            " thread — the enumeration is not deterministic" % label)
        final = run["final"]
        presented = final is not None
        if not presented:
            # Only the presentation itself may fail, and then the server
            # says so.
            self.assertTrue(any("presentation" in line for line in run["logs"]),
                            "%s: no response and no presentation failure"
                            " logged: %r" % (label, run["logs"]))
        self.assertEqual(run["count"], 0, label)
        self.assertEqual(run["claimed"], 0, label)
        self.assertLessEqual(run["decision_count"], 1, label)
        self.assertLessEqual(len(run["reserved"]), 1, label)
        recorded = run["recorded"]
        expected_outcome = "accept" if kind == "APPROVE" else "decline"
        if not presented:
            return
        structured = structured_of(final) if "result" in final else None
        fired = run["fired"]
        if structured is None:
            # A bare JSON-RPC error is only acceptable when no id was
            # ever held by the relay.
            self.assertFalse(run["reserved"] and not fired["during_mint"],
                             (label, final))
            return
        if recorded:
            self.assertNotIn(structured["status"],
                             ("refused", "not_recorded"), (label, structured))
            self.assertIsNot(structured["decision_recorded"], False,
                             (label, structured))
            if structured["status"] == "applied":
                self.assertIs(structured["decision_recorded"], True, label)
            self.assertEqual(structured["decision_id"],
                             recorded[0]["decision_id"], label)
            self.assertEqual(structured["elicitation_outcome"],
                             expected_outcome, label)
        else:
            self.assertNotEqual(structured["status"], "applied", label)
            self.assertIsNot(structured["decision_recorded"], True, label)
        if run["reserved"] and not fired["during_mint"]:
            # The relay held the id: the result carries it.
            self.assertEqual(structured["decision_id"], run["reserved"][0],
                             (label, structured, fired))
        if fired["after_record"]:
            self.assertEqual(structured["elicitation_outcome"],
                             expected_outcome, (label, structured, fired))
        admitted = run.get("admitted")
        if admitted is not None:
            # FROM ADMISSION: the result carries the admitted outcome and
            # is either a recorded decision, a refusal proven by readback,
            # or truthful uncertainty — never stream_failed/not_recorded
            # because something after admission failed.
            self.assertEqual(structured["elicitation_outcome"], admitted[0],
                             (label, structured, admitted))
            self.assertNotIn(structured["status"], ("not_recorded",),
                             (label, structured, admitted))
            if structured["status"] == "refused":
                self.assertIs(structured["decision_recorded"], False,
                              (label, structured))
                self.assertIn("readback", structured["reason"],
                              (label, structured))
                self.assertEqual(recorded, [], label)
            elif structured["status"] == "applied":
                self.assertEqual(len(recorded), 1, (label, structured))
            else:
                self.assertEqual(structured["status"],
                                 "store_outcome_uncertain", (label, structured))

    def _run_cases_isolated(self, kind, points, workdir,
                            popen=subprocess.Popen,
                            thread_factory=threading.Thread,
                            case_timeout=_SWEEP_CASE_TIMEOUT_SECONDS,
                            exit_timeout=_SWEEP_EXIT_TIMEOUT_SECONDS,
                            open_stderr=None):
        """Every case in a child process, one JSON line per case, with a
        hard per-case timeout and a bounded worker exit; a timed-out,
        crashed or non-exiting worker is recorded as a failure and the
        next child starts after it. ``popen``/``thread_factory`` are
        seams for the runner-failure tests."""
        points_path = os.path.join(workdir, "points-%s.json" % kind)
        with open(points_path, "w") as handle:
            json.dump(points, handle)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), str(REPO_ROOT / "tests")])
        results = {}
        index = 0
        while index < len(points):
            stderr_path = os.path.join(workdir, "worker-%s-%d.err"
                                       % (kind, index))
            first = index
            session = _WorkerSession(
                popen, thread_factory,
                [sys.executable, __file__, "--sweep-worker", kind,
                 points_path, str(index), str(len(points))],
                str(REPO_ROOT), env, stderr_path, case_timeout, exit_timeout,
                open_stderr=open_stderr)
            # OWNED from here: ``session.close`` (kill + bounded wait +
            # pipe close) runs on every exit, including a raise before or
            # during the pump thread's start.
            try:
                session.start()
                while index < len(points):
                    try:
                        line = session.next_line()
                    except queue.Empty:
                        results[index] = {"timeout": True}
                        index += 1
                        break
                    if line is None:
                        # EOF does not prove termination: bounded.
                        exited = session.terminate()
                        with open(stderr_path) as handle:
                            stderr_text = handle.read()[-4000:]
                        results[index] = {
                            "crashed": True, "worker_exited": exited,
                            "returncode": session.process.returncode,
                            "stderr": stderr_text,
                        }
                        index += 1
                        break
                    payload = json.loads(line)
                    if payload["index"] != index:
                        raise RuntimeError(
                            "worker reported case %r, expected %r"
                            % (payload["index"], index))
                    results[index] = payload["run"]
                    index += 1
                else:
                    # The last payload does not prove termination either.
                    session.terminate()
            finally:
                session.close()
                if session.hung:
                    results["worker-hung-%d-%d" % (first, index)] = {
                        "worker_hung": True, "pump_leaked": session.pump_leaked,
                        "cases": [first, index]}
        return results

class H3FaultSweepTests(_SweepRunnerMixin):

    def test_H3_semantic_fault_sweep_keeps_the_invariants(self):
        # The sweep runs under a ResourceWarning recorder: a worker pipe
        # or process left open by the runner would surface here as a
        # failure, not as a warning in a log.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            self._sweep_body()
            gc.collect()
        leaks = [
            "%s:%s: %s" % (entry.filename, entry.lineno, entry.message)
            for entry in caught
            if issubclass(entry.category, ResourceWarning)
            and os.path.basename(str(entry.filename)) == os.path.basename(__file__)
        ]
        self.assertEqual(leaks, [])

    def _sweep_body(self):
        started = time.monotonic()
        workdir = tempfile.mkdtemp(prefix="sweep-")
        self.addCleanup(lambda: __import__("shutil").rmtree(workdir, True))
        report = []
        plan = {}
        for kind in ("APPROVE", "DENY"):
            points, unpatchable = self._record_points(kind)
            again, _ = self._record_points(kind)
            self.assertEqual(
                points, again,
                "%s: two recordings enumerated different semantic points;"
                " the decide path is not deterministic under this fixture"
                % kind)
            self.assertGreater(len(points), 30, kind)
            keys = {point[:3] for point in points}
            # The points reach the sites the reviews named.
            for required in (("service", "MissionService", "apply_human_decision"),
                             ("service", "MissionService", "_decision_outcome"),
                             ("atomic", None, "atomic_write_json"),
                             ("store", "MissionStore", "save"),
                             ("decision_tools", None, "_applied"),
                             ("decision_tools", None, "_record"),
                             ("elicitation", "ElicitationChannel", "release"),
                             ("elicitation", "PendingTable", "discard"),
                             ("server", "GrokMcpRequestHandler", "_write_event"),
                             ("elicitation", None, "best_effort")):
                self.assertIn(required, keys, kind)
            selected, mode = _sweep_select(points)
            plan[kind] = (points, unpatchable, selected, mode)
        # The two kinds' isolated cases run concurrently (two worker
        # processes); each case still has its own server, store and
        # client and its own hard timeout.
        outcomes = {}

        def run_kind(kind):
            try:
                outcomes[kind] = self._run_cases_isolated(
                    kind, plan[kind][2], workdir)
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                outcomes[kind] = exc

        runners = [threading.Thread(target=run_kind, args=(kind,))
                   for kind in plan]
        for runner in runners:
            runner.start()
        for runner in runners:
            runner.join(_SWEEP_CASE_TIMEOUT_SECONDS * (len(plan["APPROVE"][2])
                                                       + 2))
        for kind, (points, unpatchable, selected, mode) in plan.items():
            results = outcomes.get(kind)
            if isinstance(results, BaseException):
                raise results
            self.assertIsNotNone(results, "%s sweep did not finish" % kind)
            for index, point in enumerate(selected):
                label = "%s point %s" % (kind, point)
                with self.subTest(kind=kind, point=tuple(point)):
                    self._check_invariants(kind, results[index], label)
            for key, value in results.items():
                if isinstance(key, str):
                    self.fail("%s: %s: %r" % (kind, key, value))
            report.append(
                "%s enumerated=%d unpatchable_skipped=%d %s injected=%d"
                " mode=%s" % (kind, len(points), len(unpatchable),
                              sorted(set("%s.%s" % pair
                                         for pair in unpatchable)),
                              len(selected), mode))
        sys.stderr.write("\nH3 sweep: %s; runtime %.1fs\n"
                         % ("; ".join(report), time.monotonic() - started))


class _FakeProcess(object):
    """A worker double over a REAL buffered pipe, wrapped exactly as
    ``Popen(..., stdout=PIPE, text=True)`` wraps it. The given lines are
    written up front; the write end is closed at once (EOF) unless
    ``hold_writer`` — then it stays open until a kill (``close_on_kill``)
    or, simulating a surviving descendant that inherited the write end,
    until the test itself releases it. The process exits only when
    killed (or never, ``exits_on_kill=False``)."""

    def __init__(self, lines=(), hold_writer=False, close_on_kill=True,
                 exits_on_kill=True):
        read_end, self.writer = os.pipe()
        self.stdout = io.TextIOWrapper(io.open(read_end, "rb", -1),
                                       encoding="utf-8")
        for line in lines:
            os.write(self.writer, line.encode("utf-8"))
        self.close_on_kill = close_on_kill
        self.exits_on_kill = exits_on_kill
        self.killed = False
        self.returncode = None
        if not hold_writer:
            self.release_writer()

    def release_writer(self):
        writer, self.writer = self.writer, None
        if writer is not None:
            os.close(writer)

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        if self.exits_on_kill:
            self.returncode = -9
        if self.close_on_kill:
            self.release_writer()

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        raise subprocess.TimeoutExpired("fake worker", timeout)


class _FailingThread(object):
    def __init__(self, **kwargs):
        pass

    def start(self):
        raise RuntimeError("pump thread could not start")


class _StderrCloseRaises(object):
    """A stderr handle double whose FIRST close raises (an exception while
    leaving the stderr context after ``popen``); the second closes."""

    def __init__(self, path):
        self._handle = open(path, "w")
        self.attempts = 0

    def fileno(self):
        return self._handle.fileno()

    @property
    def closed(self):
        return self._handle.closed

    def close(self):
        self.attempts += 1
        if self.attempts == 1:
            raise OSError("stderr close failed")
        self._handle.close()


class H4RunnerFailureTests(_SweepRunnerMixin):
    """The isolated runner against worker doubles: no hang, the child
    killed, the pipe closed, bounded runtime, failures recorded."""

    POINTS = [["elicitation", "PendingTable", "discard", 1],
              ["decision_tools", None, "_record", 1]]

    def _runner(self, make_process, thread_factory=threading.Thread,
                open_stderr=None, case_timeout=0.5, exit_timeout=0.2):
        """Run the isolated runner over the two points with worker
        doubles: ``make_process`` builds one double per spawned worker.
        Returns (results, processes)."""
        workdir = tempfile.mkdtemp(prefix="runner-")
        self.addCleanup(lambda: __import__("shutil").rmtree(workdir, True))
        processes = []

        def popen(argv, **kwargs):
            process = make_process()
            processes.append(process)
            return process

        self.processes = processes
        self.addCleanup(lambda: [p.release_writer() for p in processes])
        started = time.monotonic()
        try:
            results = self._run_cases_isolated(
                "APPROVE", self.POINTS, workdir, popen=popen,
                thread_factory=thread_factory, case_timeout=case_timeout,
                exit_timeout=exit_timeout, open_stderr=open_stderr)
        finally:
            self.elapsed = time.monotonic() - started
        return results, processes

    def _assert_all_owned(self, processes, bound=5):
        for process in processes:
            self.assertTrue(process.killed)
            self.assertTrue(process.stdout.closed)
        self.assertLess(self.elapsed, bound)

    def test_H4a_worker_not_exiting_after_eof_is_killed_and_recorded(self):
        # EOF on the pipe, worker still running: the bounded terminate
        # kills it; the case is recorded as crashed with the worker gone.
        results, processes = self._runner(lambda: _FakeProcess())
        self._assert_all_owned(processes)
        self.assertEqual(len(processes), 2)  # one worker per remaining case
        for index in (0, 1):
            self.assertTrue(results[index]["crashed"])
            self.assertTrue(results[index]["worker_exited"])
        self.assertFalse(any(isinstance(key, str) for key in results))

    def test_H4b_worker_not_exiting_after_last_payload_is_killed(self):
        lines = [json.dumps({"index": i, "run": {"fake": i}}) + "\n"
                 for i in range(len(self.POINTS))]
        results, processes = self._runner(
            lambda: _FakeProcess(lines, hold_writer=True))
        self._assert_all_owned(processes)
        self.assertEqual(len(processes), 1)
        self.assertEqual(results[0], {"fake": 0})
        self.assertEqual(results[1], {"fake": 1})
        self.assertFalse(any(isinstance(key, str) for key in results))

    def test_H4c_pump_start_failure_kills_child_and_closes_pipe(self):
        with self.assertRaises(RuntimeError):
            self._runner(lambda: _FakeProcess(), thread_factory=_FailingThread)
        self.assertEqual(len(self.processes), 1)
        self._assert_all_owned(self.processes)

    def test_H4d_worker_ignoring_kill_is_a_recorded_failure_not_a_hang(self):
        results, processes = self._runner(
            lambda: _FakeProcess(exits_on_kill=False))
        self._assert_all_owned(processes)
        for index in (0, 1):
            self.assertTrue(results[index]["crashed"])
            self.assertFalse(results[index]["worker_exited"])
        hung = [value for key, value in results.items() if isinstance(key, str)]
        self.assertEqual(len(hung), len(processes))
        for entry in hung:
            self.assertTrue(entry["worker_hung"])
            with self.assertRaises(AssertionError):
                self._check_invariants("APPROVE", entry, "hung")

    def test_H4e_real_pipe_writer_surviving_the_kill_shutdown_is_bounded(self):
        # R08-1: the worker is killed but a descendant keeps the write end
        # open, so EOF never arrives and the buffered reader would block
        # forever in ``readline``. With a 0.02 s exit bound, shutdown must
        # still return within a small fixed bound, close the pipe and
        # record the failure (each case times out; no line ever arrives).
        results, processes = self._runner(
            lambda: _FakeProcess(hold_writer=True, close_on_kill=False),
            case_timeout=0.1, exit_timeout=0.02)
        self._assert_all_owned(processes, bound=2)
        for process in processes:
            self.assertIsNotNone(process.writer)  # still held by "someone"
        for index in (0, 1):
            self.assertTrue(results[index]["timeout"])
        self.assertFalse(any(value.get("pump_leaked")
                             for key, value in results.items()
                             if isinstance(key, str)))
        # Releasing the writers afterwards changes nothing already recorded.
        for process in processes:
            process.release_writer()

    def test_H4f_stderr_context_failure_after_popen_is_contained(self):
        # R08-2: the child exists when leaving the stderr context raises;
        # the guard kills it, closes stdout and stderr, and surfaces the
        # exception, without a hang.
        handles = []

        def open_stderr(path):
            handle = _StderrCloseRaises(path)
            handles.append(handle)
            return handle

        with self.assertRaises(OSError):
            self._runner(lambda: _FakeProcess(), open_stderr=open_stderr)
        self.assertEqual(len(self.processes), 1)
        self._assert_all_owned(self.processes)
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)
        self.assertEqual(handles[0].attempts, 2)


class H5OracleSelfTests(_SweepRunnerMixin):
    """The invariant checker must REJECT the payloads reviewer1's
    round-07 reproductions produced; a checker that accepts them is not
    evidence."""

    @staticmethod
    def _payload(status, outcome, recorded_flag, decisions, reason=""):
        structured = {
            "status": status, "ok": status == "applied",
            "elicitation_outcome": outcome, "decision_recorded": recorded_flag,
            "decision_id": "md-" + "0" * 32, "reason": reason,
        }
        return {
            "final": {"result": {"structuredContent": structured}},
            "count": 0, "claimed": 0, "reserved": ["md-" + "0" * 32],
            "recorded": decisions, "decision_count": len(decisions),
            "logs": [], "respond_failure": None,
            "fired": {"point": ["x"], "during_mint": False,
                      "after_record": False},
        }

    def test_H5a_admitted_approve_reported_stream_failed_is_rejected(self):
        run = self._payload("refused", "stream_failed", False, [])
        run["admitted"] = ["accept", None]
        with self.assertRaises(AssertionError):
            self._check_invariants("APPROVE", run, "round-07 (a)")

    def test_H5b_admitted_deny_reported_not_recorded_is_rejected(self):
        run = self._payload("not_recorded", "decline", False, [])
        run["admitted"] = ["decline", None]
        with self.assertRaises(AssertionError):
            self._check_invariants("DENY", run, "round-07 (b)")

    def test_H5c_recorded_decision_with_no_response_is_rejected(self):
        run = self._payload("applied", "accept", True,
                            [{"decision_id": "md-" + "0" * 32}])
        run["final"] = None
        run["admitted"] = ["accept", None]
        with self.assertRaises(AssertionError):
            self._check_invariants("APPROVE", run, "round-07 (c)")

    def test_H5d_admitted_but_refused_without_readback_proof_is_rejected(self):
        run = self._payload("refused", "accept", False, [],
                            reason="decision relay raised RuntimeError")
        run["admitted"] = ["accept", None]
        with self.assertRaises(AssertionError):
            self._check_invariants("APPROVE", run, "round-07 (d)")

    def test_H5e_consistent_payloads_pass(self):
        good = self._payload("applied", "accept", True,
                             [{"decision_id": "md-" + "0" * 32}])
        good["admitted"] = ["accept", None]
        self._check_invariants("APPROVE", good, "good applied")
        refused = self._payload("refused", "decline", False, [],
                                reason="the readback shows no decision")
        refused["admitted"] = ["decline", None]
        self._check_invariants("DENY", refused, "good refused")


def _sweep_worker(argv):
    """Child-process entry: run cases [start, end) of one kind and print
    one JSON line per case."""
    kind, points_path, start, end = argv[0], argv[1], int(argv[2]), int(argv[3])
    with open(points_path) as handle:
        points = json.load(handle)
    for index in range(start, end):
        case = H3FaultSweepTests("test_H3_semantic_fault_sweep_keeps_the_invariants")
        case.setUp()
        try:
            run = case._sweep_case(kind, tuple(points[index]))
        finally:
            case.doCleanups()
        sys.stdout.write(json.dumps({"index": index, "run": run}) + "\n")
        sys.stdout.flush()


# ====================================================================
# F. Static pins on the new files
# ====================================================================


_SIDE_CALL_FILES = ("grok_mcp/decision_tools.py", "grok_mcp/elicitation.py",
                    "grok_mcp/server.py",
                    # Task 8 S-VII: the elicited control and acknowledgment
                    # relays are on the same client-mediated path.
                    "grok_mcp/control_tools.py")
# Segments of a receiver path that name a cleanup or raw-log callable.
_SIDE_CALL_SEGMENTS = frozenset({"log_writer", "discard", "discard_key",
                                 "release", "release_claim", "stderr",
                                 "stdout"})
_SIDE_CALL_ALLOWED = frozenset({("grok_mcp/elicitation.py", "best_effort")})


def _receiver_path(node):
    """The dotted chain of an expression (``sys.stderr.write`` ->
    ('sys', 'stderr', 'write')), or () when it is not a plain chain."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return ()


def _names_side_callable(node, aliases):
    """Whether an expression denotes a banned callable: a receiver path
    with a banned segment, a known alias (name or attribute chain), a
    lambda whose body does, or a ``partial`` of one."""
    path = _receiver_path(node)
    if path:
        if set(path) & _SIDE_CALL_SEGMENTS:
            return True
        if path in aliases or (len(path) == 1 and path[0] in aliases):
            return True
        return False
    if isinstance(node, ast.Lambda):
        return any(isinstance(inner, ast.Call)
                   and _names_side_callable(inner.func, aliases)
                   for inner in ast.walk(node.body))
    if isinstance(node, ast.Call):
        callee = _receiver_path(node.func)
        if callee and callee[-1] == "partial":
            return any(_names_side_callable(arg, aliases) for arg in node.args)
    return False


def _direct_side_calls(tree, relative):
    """Every direct call of a cleanup/raw-log callable on the decide-path
    files, by receiver path or alias, outside the allowed primitive:
    ``[(file, function, description)]``.

    SCOPE: a bounded SYNTACTIC check over these sources — receiver
    paths, assignment/default-argument aliases, lambdas and ``partial``
    of a banned callable. It is not proof against arbitrary Python:
    ``getattr`` with computed strings and dictionary-carried callables
    are outside the bounded syntactic scan (F3 bans exec/eval/import/
    process/file operations, not getattr or dict calls)."""
    violations = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (relative, function.name) in _SIDE_CALL_ALLOWED:
            continue
        aliases = set()
        # Aliases: assignments and default arguments binding a banned
        # callable (including lambdas and partials of one), with the
        # bound name or attribute chain as the alias.
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                if _names_side_callable(node.value, aliases):
                    for target in node.targets:
                        path = _receiver_path(target)
                        if path:
                            aliases.add(path if len(path) > 1 else path[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.Lambda)) and node is not function:
                arguments = node.args
                positional = arguments.posonlyargs + arguments.args
                for argument, default in zip(
                    positional[len(positional) - len(arguments.defaults):],
                    arguments.defaults,
                ):
                    if _names_side_callable(default, aliases):
                        aliases.add(argument.arg)
                for argument, default in zip(arguments.kwonlyargs,
                                             arguments.kw_defaults):
                    if default is not None and _names_side_callable(default,
                                                                    aliases):
                        aliases.add(argument.arg)
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if _names_side_callable(node.func, aliases):
                violations.append((relative, function.name,
                                   ast.dump(node.func)[:80]))
    # A violation inside a nested function is reported under the nested
    # function as well as its parent walk; keep one entry per call site.
    return sorted(set(violations))


class FStaticPinTests(unittest.TestCase):

    def test_F1_accept_never_comes_from_tool_arguments(self):
        # decision_tools reads arguments for exactly two keys; the
        # elicitation module never names ``arguments`` at all.
        source = (REPO_ROOT / "grok_mcp" / "decision_tools.py").read_text()
        keys = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Subscript) and isinstance(
                node.value, ast.Name
            ) and node.value.id == "arguments":
                self.assertIsInstance(node.slice, ast.Constant)
                keys.add(node.slice.value)
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", None))
                if name == "get" and isinstance(node.func, ast.Attribute):
                    self.assertFalse(
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "arguments",
                        "arguments.get() is not allowed")
        self.assertEqual(keys, {"mission_id", "revision"})
        elicitation_tree = ast.parse(
            (REPO_ROOT / "grok_mcp" / "elicitation.py").read_text())
        for node in ast.walk(elicitation_tree):
            if isinstance(node, ast.Name):
                self.assertNotEqual(node.id, "arguments")
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(node.attr, "arguments")
        schema = protocol.tool_by_name(DECIDE)["inputSchema"]
        self.assertEqual(sorted(schema["properties"]), ["mission_id", "revision"])
        self.assertEqual(sorted(schema["required"]), ["mission_id", "revision"])
        self.assertIs(schema["additionalProperties"], False)

    def test_F2_decision_tool_schema_carries_no_principal_or_authority_input(self):
        tool = protocol.tool_by_name(DECIDE)
        for banned in ("principal", "actor", "subject", "provenance",
                       "authorized_by", "on_behalf_of", "issued_by",
                       "authorization", "decision_id", "authorization_id",
                       "expires_at", "action", "confirm", "content"):
            self.assertNotIn(banned, tool["inputSchema"]["properties"])
        output = tool["outputSchema"]
        for key in ("decision", "elicitation_outcome", "expires_at",
                    "authorization_id", "authorization_live"):
            self.assertIn(key, output["required"])
        self.assertIs(output["additionalProperties"], False)

    def test_F3_new_grok_modules_keep_the_package_bans(self):
        for name in ("elicitation.py", "decision_tools.py", "server.py",
                     "control_tools.py"):
            path = REPO_ROOT / "grok_mcp" / name
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    callee = getattr(node.func, "id",
                                     getattr(node.func, "attr", None))
                    self.assertNotIn(callee, {
                        "sleep", "Thread", "system", "popen", "Popen", "run",
                        "spawn", "fork", "exec", "eval", "open",
                        "__import__", "import_module",
                    }, (name, callee))
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    roots = (
                        [a.name.split(".")[0] for a in node.names]
                        if isinstance(node, ast.Import)
                        else [(node.module or "").split(".")[0]]
                    )
                    for root in roots:
                        self.assertNotIn(root, {
                            "subprocess", "shutil", "tempfile", "herdr",
                            "herdctl", "target_runtime", "pr_delivery",
                            "telegram_operator", "workflow_authority",
                            "operator_session",
                        }, (name, root))
            for token in non_docstring_strings(path):
                self.assertNotIn(".herd", token.string)

    def test_F4_server_builds_the_context_pair_once_after_the_bearer_check(self):
        source = (REPO_ROOT / "grok_mcp" / "server.py").read_text()
        self.assertEqual(source.count("AuthenticatedContext("), 1)
        self.assertLess(source.index("bearer_matches(supplied)"),
                        source.index("AuthenticatedContext("))
        self.assertIn("PRINCIPAL_KIND_CLIENT_CONFIRMATION", source)
        # The client response branch sits AFTER the session gate.
        self.assertLess(source.index('self._reply(404, b"unknown session")'),
                        source.index("elicitations.deliver("))

    def test_F5_mission_control_is_herdr_free_and_subprocess_free(self):
        package = REPO_ROOT / "mission_control"
        files = sorted(package.glob("*.py"))
        self.assertEqual([p.name for p in files],
                         ["__init__.py", "attention.py", "authority.py",
                          "controls.py", "delivery.py",
                          "delivery_artifacts.py", "engineering.py",
                          "gate.py", "integration.py", "observation_adapter.py",
                          "observation_receipts.py", "readiness.py",
                          "reconciliation_bridge.py", "status.py"])
        # Import roots per file: the S-I files read the neutral core only;
        # the S-II readers (Task 8) additionally take coordination's
        # contracts and, for the status read, the other stores' lock-free
        # loaders plus the standard library it needs for path evidence.
        # The S-IV files (bootstrap, effect gate, dependency predicate)
        # take the neutral core and the workflow record/store layers —
        # never target_runtime, herdr, subprocess or the network.
        allowed_roots = {
            "__init__.py": {"mission", "mission_control"},
            "authority.py": {"mission", "mission_control"},
            "engineering.py": {"mission", "mission_control",
                               "workflow_authority", "hashlib", "collections"},
            "gate.py": {"mission", "mission_control", "workflow_authority",
                        "collections", "time"},
            "integration.py": {"mission", "workflow_authority"},
            "observation_adapter.py": {"mission", "mission_control",
                                       "coordination", "hashlib"},
            "status.py": {"mission", "mission_control", "coordination",
                          "workflow_authority", "pr_delivery"},
            # The S-V files: the Runtime's pure observation-receipt
            # vocabulary (shared by its writer and its reader) and the
            # reconciliation bridge (the neutral core, the workflow
            # record, and the delivery layer's READ-only store and
            # receipt validators — pinned narrow in tests/test_static.py).
            "observation_receipts.py": {"secrets", "workflow_authority"},
            "reconciliation_bridge.py": {"mission", "mission_control",
                                         "pr_delivery", "workflow_authority"},
            # The S-VI file: the one pure content-addressed format of the
            # delivery documents (verification record, proposal,
            # decision), shared by the Runtime writer and the Mission-side
            # readers — the standard library and the canonical digest only.
            "delivery_artifacts.py": {"hashlib", "json", "os", "secrets",
                                      "workflow_authority"},
            # The S-VI Mission-bound delivery driver and desk: the neutral
            # core, the Mission-control layer, the workflow record/store and
            # the delivery package's pure modules (the CLI lazily, inside
            # the production factory only — pinned in tests/test_static.py);
            # ``time`` for that factory's production clock; ``os`` for the
            # preparation's lease-confinement path checks.
            "delivery.py": {"hashlib", "mission", "mission_control", "os",
                            "pr_delivery", "time", "workflow_authority"},
            # The S-VII files. The control desk: the neutral core and the
            # canonical digest (its confirm value). The attention desk:
            # coordination's own contracts over the Mission-control
            # observation bridge, ``secrets`` for its elicitation request
            # ids and the canonical digest (its confirm value). The
            # readiness producer: the neutral core and ``fcntl``/``os`` for
            # its non-destructive probe of the Runtime's lock — no process,
            # engine or network.
            "controls.py": {"mission", "workflow_authority"},
            "attention.py": {"coordination", "mission_control", "secrets",
                             "workflow_authority"},
            "readiness.py": {"fcntl", "mission", "os"},
        }
        for path in files:
            source = path.read_text()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    roots = (
                        [a.name.split(".")[0] for a in node.names]
                        if isinstance(node, ast.Import)
                        else [(node.module or "").split(".")[0]]
                    )
                    for root in roots:
                        self.assertIn(root, allowed_roots[path.name],
                                      (path.name, root))
            self.assertNotIn("herd", source.lower().replace("herdr-free", "")
                             .replace("the orchestration engine", ""))

    def test_F7_cleanup_and_logging_only_through_the_best_effort_primitive(self):
        # On the decide path, every cleanup call (discard, discard_key,
        # release, release_claim) and every raw log call (log_writer,
        # stderr/stdout writes) appears ONLY inside the one primitive —
        # by full receiver path AND through any alias bound to such a
        # callable; the handler's ``log_message`` body is exactly that
        # primitive call. This is a bounded syntactic check (see
        # ``_direct_side_calls``), not proof against arbitrary Python.
        violations = []
        for relative in _SIDE_CALL_FILES:
            violations.extend(_direct_side_calls(
                ast.parse((REPO_ROOT / relative).read_text()), relative))
        self.assertEqual(violations, [])
        server_tree = ast.parse((REPO_ROOT / "grok_mcp" / "server.py").read_text())
        bodies = [node.body for node in ast.walk(server_tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "log_message"]
        self.assertEqual(len(bodies), 1)
        statements = [s for s in bodies[0]
                      if not (isinstance(s, ast.Expr)
                              and isinstance(s.value, ast.Constant)
                              and isinstance(s.value.value, str))]
        self.assertEqual(len(statements), 1)
        call = statements[0].value
        self.assertIsInstance(call, ast.Call)
        self.assertEqual(call.func.attr, "best_effort")

    def test_F8_side_call_pin_rejects_receiver_paths_and_aliases(self):
        # NEGATIVE self-tests: the pin must FAIL on in-memory mutations of
        # the real server source that add each escape, and pass on the
        # real source (F7). Each mutation is a new method on the handler.
        real = (REPO_ROOT / "grok_mcp" / "server.py").read_text()
        self.assertEqual(_direct_side_calls(ast.parse(real), "grok_mcp/server.py"),
                         [])
        mutations = {
            "stderr receiver path": "        sys.stderr.write('x')\n",
            "alias of channel.release": (
                "        cleanup = channel.release\n        cleanup()\n"),
            "alias of self.server.log_writer": (
                "        log = self.server.log_writer\n        log(x)\n"),
            "attribute alias": (
                "        self._later = table.discard\n"
                "        self._later(entry)\n"),
            "default-argument alias": (
                "        def inner(done=channel.release):\n"
                "            done()\n"),
            "lambda alias": (
                "        done = lambda: channel.release()\n        done()\n"),
            "partial alias": (
                "        done = functools.partial(table.discard_key, a, b)\n"
                "        done()\n"),
            "direct partial call": (
                "        functools.partial(channel.release)()\n"),
            "stdout receiver path": "        sys.stdout.write('x')\n",
        }
        for label, body in mutations.items():
            mutated = real + (
                "\n\ndef _mutant(self, channel, table, entry, a, b, x):\n" + body)
            violations = _direct_side_calls(ast.parse(mutated), "grok_mcp/server.py")
            self.assertTrue(violations, label)
            self.assertTrue(all(v[1] == "_mutant" or v[1] == "inner"
                                for v in violations), (label, violations))
        # Inside the primitive itself the calls are allowed.
        allowed = (real + "\n\ndef best_effort(label, action):\n"
                   "    sys.stderr.write(label)\n    action()\n")
        self.assertEqual(
            [v for v in _direct_side_calls(ast.parse(allowed),
                                           "grok_mcp/elicitation.py")
             if v[1] == "best_effort"], [])

    def test_F6_principal_kind_is_additive_and_documented(self):
        self.assertEqual(mission_record.PRINCIPAL_KINDS, (
            "configured_connector_credential_ordinal", "local_process_user",
            "configured_connector_client_confirmation"))
        self.assertEqual(mission_record.PROOF_TRANSPORT_CREDENTIAL_ONLY,
                         "transport_credential_only")
        context = mission_record.AuthenticatedContext(
            "grok_mcp", mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION, "1")
        provenance = mission_record.provenance_record(
            context, 1, "decision", "md-" + "0" * 32, "mn-" + "0" * 32, 1)
        self.assertIsNone(provenance["human_identity_proof"])
        self.assertEqual(provenance["proof"], "transport_credential_only")
        mission_record.validate_provenance(provenance)
        source = (REPO_ROOT / "mission" / "record.py").read_text()
        self.assertIn("DOES NOT claim: who the human was", source)


# ====================================================================
# G. The consequential-decision provenance predicate
# ====================================================================


class GAuthorityPredicateTests(Fixture):

    def _approve_with(self, mission_id, revision, kind):
        context = mission_record.AuthenticatedContext(
            transport="grok_mcp" if kind != (
                mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER
            ) else "local_terminal",
            principal_kind=kind, principal_ref="1")
        decision_id = self.service.mint_decision_id(context)
        envelope = mission_decision.HumanDecisionEnvelope(
            context=context, decision_id=decision_id, mission_id=mission_id,
            revision=revision, decision=mission_decision.DECISION_APPROVE,
            received_at=self.now[0],
            approved_action_scope=["engineering_change", "repository_read"],
            approved_delivery_targets=["github_pr"])
        return self.service.apply_human_decision(envelope)

    def test_G1_connector_credential_is_insufficient_with_prerequisite(self):
        mission_id, revision = self.propose()
        outcome = self._approve_with(
            mission_id, revision,
            mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL)
        stored = self.service.get(mission_id)
        self.assertEqual(stored["live_authorization_id"],
                         outcome["authorization_id"])
        result = authority.consequential_decision_provenance(
            stored, outcome["authorization_id"])
        self.assertFalse(result["sufficient"])
        self.assertEqual(result["problem"],
                         "mission_control_provenance_insufficient")
        self.assertEqual(result["principal_kind"],
                         mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL)
        self.assertEqual(result["decision_id"], outcome["decision_id"])
        self.assertIn("negotiate MCP form elicitation", result["prerequisite"])
        self.assertEqual(sorted(result), sorted(authority.RESULT_KEYS))

    def test_G2_client_confirmation_and_local_process_user_are_sufficient(self):
        for kind in (mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION,
                     mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER):
            mission_id, revision = self.propose()
            outcome = self._approve_with(mission_id, revision, kind)
            result = authority.consequential_decision_provenance(
                self.service.get(mission_id), outcome["authorization_id"])
            self.assertTrue(result["sufficient"], kind)
            self.assertIsNone(result["problem"])
            self.assertIsNone(result["prerequisite"])
            self.assertEqual(result["principal_kind"], kind)

    def test_G3_absent_or_unknown_authorization_refuses(self):
        mission_id, revision = self.propose()
        stored = self.service.get(mission_id)
        self.assertEqual(
            authority.consequential_decision_provenance(stored, None)["problem"],
            "mission_control_no_authorization")
        self.assertEqual(
            authority.consequential_decision_provenance(
                stored, "ma-" + "0" * 32)["problem"],
            "mission_control_decision_not_found")
        self.assertEqual(
            authority.consequential_decision_provenance(
                {}, "ma-" + "0" * 32)["problem"],
            "mission_control_decision_not_found")

    def test_G4_over_the_wire_decide_yields_a_sufficient_authorization(self):
        mission_id, revision = self.propose()
        client = self.serve()
        client.initialize()
        request, final = client.decide(mission_id, revision, accept_with_prefix)
        structured = structured_of(final)
        result = authority.consequential_decision_provenance(
            self.service.get(mission_id), structured["authorization_id"])
        self.assertTrue(result["sufficient"])
        self.assertEqual(result["principal_kind"],
                         mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION)
        # The plain approve tool on the same store stays insufficient.
        other_id, other_revision = self.propose()
        status, body = client.rpc("tools/call", {
            "name": "di_mission_approve",
            "arguments": {"mission_id": other_id, "revision": other_revision}})
        approved = body["result"]["structuredContent"]
        result = authority.consequential_decision_provenance(
            self.service.get(other_id), approved["authorization_id"])
        self.assertFalse(result["sufficient"])
        self.assertEqual(result["problem"],
                         "mission_control_provenance_insufficient")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--sweep-worker"]:
        _sweep_worker(sys.argv[2:])
    else:
        unittest.main()
