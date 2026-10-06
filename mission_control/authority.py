"""The consequential-decision provenance predicate.

A Mission Authorization is issued by the Mission Core from any
authenticated decision (Task 4); the core does not distinguish, for its
own purposes, who or what stood behind the transport credential. A
CONSEQUENTIAL effect — engineering dispatch, delivery — needs more than
"the configured credential was presented": it needs a decision that a
person actually took. This module states, in one closed table, which
recorded provenance kinds count:

- ``configured_connector_client_confirmation``: the configured client
  showed the exact proposal to its user and returned the answer through
  its own interface (an elicitation round trip the server originated);
- ``local_process_user``: a decision taken at the trusted node's own
  interactive terminal.

``configured_connector_credential_ordinal`` — what a plain tool call
can record — is NOT sufficient, because the tool-calling model presents
that credential, and a model can request authority but never grant it.
None of the three kinds proves a human identity, and this predicate
does not claim otherwise.

The predicate reads the ``MissionService.get`` projection and returns a
plain dictionary; it validates nothing, mints nothing and writes
nothing. It is consumed by later slices (dispatch and delivery gates)
and unit-tested here.
"""

from mission import record as mission_record

CONSEQUENTIAL_PRINCIPAL_KINDS = (
    mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION,
    mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER,
)

PROBLEM_PROVENANCE_INSUFFICIENT = "mission_control_provenance_insufficient"
PROBLEM_NO_AUTHORIZATION = "mission_control_no_authorization"
PROBLEM_DECISION_NOT_FOUND = "mission_control_decision_not_found"

# The exact prerequisite surfaced when a decision was recorded from the
# connector credential alone. This text goes into the live envelope.
PREREQUISITE_CLIENT_CONFIRMATION = (
    "the decision was recorded from the configured connector credential"
    " alone, which the tool-calling model presents; a consequential effect"
    " needs a client-confirmed decision (the connector client must"
    " negotiate MCP form elicitation and accept event-stream tool"
    " responses so the server can ask its user directly) or a decision"
    " taken at the trusted node's terminal"
)

RESULT_KEYS = (
    "sufficient", "problem", "detail", "prerequisite", "authorization_id",
    "decision_id", "principal_kind", "transport",
)


def _result(sufficient, problem, detail, prerequisite=None,
            authorization_id=None, decision_id=None, principal_kind=None,
            transport=None):
    result = {
        "sufficient": sufficient, "problem": problem, "detail": detail,
        "prerequisite": prerequisite, "authorization_id": authorization_id,
        "decision_id": decision_id, "principal_kind": principal_kind,
        "transport": transport,
    }
    assert tuple(sorted(result)) == tuple(sorted(RESULT_KEYS))
    return result


def consequential_decision_provenance(stored, authorization_id):
    """Whether the decision that issued ``authorization_id`` on the
    Mission projection ``stored`` (the ``MissionService.get`` result)
    carries provenance sufficient for a consequential effect.

    Read-only over the projection. Absent authorization or decision
    refuses with its own code; a present decision answers from its
    recorded ``provenance.principal_kind`` against the closed table.
    """
    if not isinstance(authorization_id, str) or not authorization_id:
        return _result(False, PROBLEM_NO_AUTHORIZATION,
                       "no live authorization was presented")
    record = stored.get("record") if isinstance(stored, dict) else None
    decisions = record.get("decisions") if isinstance(record, dict) else None
    if not isinstance(decisions, list):
        return _result(False, PROBLEM_DECISION_NOT_FOUND,
                       "the projection carries no decision records",
                       authorization_id=authorization_id)
    for decision in decisions:
        outcome = decision.get("outcome") or {}
        if outcome.get("authorization_id") != authorization_id:
            continue
        provenance = decision.get("provenance") or {}
        kind = provenance.get("principal_kind")
        transport = provenance.get("transport")
        if kind in CONSEQUENTIAL_PRINCIPAL_KINDS:
            return _result(
                True, None,
                "decision %s was recorded with provenance kind %s"
                % (decision.get("decision_id"), kind),
                authorization_id=authorization_id,
                decision_id=decision.get("decision_id"),
                principal_kind=kind, transport=transport,
            )
        return _result(
            False, PROBLEM_PROVENANCE_INSUFFICIENT,
            "decision %s was recorded with provenance kind %s, which is not"
            " sufficient for a consequential effect"
            % (decision.get("decision_id"), kind),
            prerequisite=PREREQUISITE_CLIENT_CONFIRMATION,
            authorization_id=authorization_id,
            decision_id=decision.get("decision_id"),
            principal_kind=kind, transport=transport,
        )
    return _result(False, PROBLEM_DECISION_NOT_FOUND,
                   "no recorded decision issued authorization %s"
                   % authorization_id, authorization_id=authorization_id)
