"""Pure route-decision derivation: "which Mission is this?" for one
inbound conversation turn, and the separate lane selection.

Deterministic evidence tiers, in order (architecture §4, roadmap
Iteration 1), each resolving only through durable bindings or the
observation the caller injects: an explicit Mission id; a reply-to
binding; an approval-presentation binding; a result-presentation
binding; an exact repository reference; an exact issue reference; a
known project; a durable alias; a unique conversation match. The
bounded natural-language routing turn is NOT here and is not claimed:
when no tier resolves, the outcome is a new proposal (only when the
turn carries one) or clarification.

Outcomes are closed (``EXISTING_MISSION`` / ``NEW_PROPOSAL`` /
``CLARIFICATION_REQUIRED``) and every clarification carries a closed
reason and a bounded detail. The rules that make this conservative:

- The first tier with context wins; a tier is consulted only when the
  turn names its selector. An explicit id that disagrees with an active
  binding, or arrives with ``NEW_MISSION`` intent, clarifies
  (``CONTEXT_DISAGREES``) rather than picking one.
- Every binding is revision-exact (AMD-1): if the observed current
  revision is not the binding's ``bound_revision`` the tier does not
  resolve and the outcome is ``BINDING_STALE``. There is no escape
  flag. A repository or issue binding whose observed repository differs
  is likewise stale. Routing never repairs, refreshes or advances a
  binding.
- An expired binding clarifies (``BINDING_EXPIRED``); a revoked binding
  is invisible. Neither is ever a resolution tier.
- Anything but a FRESH observation clarifies with its truthful reason
  (``MISSION_NOT_OBSERVED`` for an explicit id the source calls absent,
  ``BINDING_STALE`` for a bound Mission the source calls absent,
  ``OBSERVATION_UNAVAILABLE`` / ``_STALE`` / ``_INCONSISTENT``). A
  non-FRESH observation's content is never recorded as an observation
  point, so it can never raise the durable high-water mark.
- The conversation tier sees every ACTIVE conversation-scoped binding
  of the conversation: exactly one distinct Mission resolves, several
  clarify (``AMBIGUOUS_CANDIDATES``, enumerating at most
  ``MAX_ROUTE_CANDIDATES`` of them, none above that), none falls
  through.
- ``NEW_MISSION`` intent with a proposal outranks context; without a
  proposal it is ``NO_CONTEXT``. ``FOLLOW_UP`` intent never proposes.
  Intent is supplied by the caller: inferring it from free text IS the
  natural-language turn, which is out of scope.

Lane selection is a separate pure function whose result is a VALUE.
``ENGINEERING_LANE`` IS the bounded Herdr engineering route: engineering
selects it when that lane is among the caller-declared available lanes
(acceptance criterion 2); an unsupported domain or an unavailable lane
refuses with an explicit reason; an unresolved route can select no
lane. Selecting the lane is a decision value only — it confers no
dispatch permission, starts no Herdr work, and imports nothing of
Herdr. The identifier and the literal are deliberately neutral rather
than naming Herdr because this package sits in the static suite's
herdr-free roots, whose token scan forbids that name in identifiers and
string literals but expressly permits docstring prose, which is where
this meaning is stated. Nothing here reads authority as permission: a
DENIED or unauthorized Mission is still that Mission, the record holds
no permission field of any kind, and ``authority`` is ``"none"``.

Replay (A-R5). A route decision is keyed by the inbound turn's identity
``(transport, conversation_ref, message_ref)``. ``find_replay`` returns
the stored decision, marked replayed, only to the SAME authenticated
context that recorded it and only for the same inbound content; a
different context is refused with ``coordination_context_mismatch`` and
learns nothing, and different content under the same context is an
idempotency conflict. A replay never re-observes and never rewrites.
"""

from dataclasses import dataclass
from typing import List, Optional

from workflow_authority.digest import json_digest

from coordination import binding
from coordination import observation
from coordination import record

# Exact-value pinned in the bound-constant table: the most candidate
# Missions a clarification enumerates before refusing to enumerate.
MAX_ROUTE_CANDIDATES = 16

INBOUND_KEYS = (
    "transport", "conversation_ref", "message_ref", "explicit_mission_id",
    "reply_to_message_ref", "repository_url", "issue_ref", "project_key",
    "alias", "intent", "domain", "proposal_digest_sha256",
)
ROUTE_KEYS = (
    "route_id", "decided_at", "inbound", "inbound_digest_sha256", "outcome",
    "mission_id", "resolved_tier", "candidates", "reason", "detail",
    "proposal_digest_sha256", "bound_revision", "observed_revision",
    "observation_point", "lane_outcome", "lane", "lane_reason", "provenance",
    "authority",
)
# Reasons that arise from a binding tier and therefore record the
# binding's revision.
_BINDING_REASONS = (record.REASON_BINDING_EXPIRED, record.REASON_BINDING_STALE)
_FRESHNESS_REASONS = {
    record.FRESHNESS_UNAVAILABLE: record.REASON_OBSERVATION_UNAVAILABLE,
    record.FRESHNESS_STALE: record.REASON_OBSERVATION_STALE,
    record.FRESHNESS_INCONSISTENT: record.REASON_OBSERVATION_INCONSISTENT,
}


# -- the inbound turn -------------------------------------------------


@dataclass(frozen=True)
class InboundTurn:
    """One inbound conversation turn as the transport adapter presents
    it: its identity, the deterministic selectors it names, the
    caller-supplied intent and domain, and the digest of any candidate
    proposal it carries."""

    transport: str
    conversation_ref: str
    message_ref: str
    explicit_mission_id: Optional[str]
    reply_to_message_ref: Optional[str]
    repository_url: Optional[str]
    issue_ref: Optional[str]
    project_key: Optional[str]
    alias: Optional[str]
    intent: str
    domain: Optional[str]
    proposal_digest_sha256: Optional[str]

    def validate(self, location="inbound"):
        record.require_transport(self.transport, location + ".transport")
        record.require_str(self.conversation_ref, location + ".conversation_ref",
                           record.MAX_CONVERSATION_REF_CHARS)
        record.require_str(self.message_ref, location + ".message_ref",
                           record.MAX_MESSAGE_REF_CHARS)
        record.require_optional_id(self.explicit_mission_id, record.MISSION_ID_PREFIX,
                                   location + ".explicit_mission_id")
        record.require_optional_str(self.reply_to_message_ref,
                                    location + ".reply_to_message_ref",
                                    record.MAX_MESSAGE_REF_CHARS)
        record.require_optional_repository_url(self.repository_url,
                                               location + ".repository_url")
        if self.issue_ref is not None:
            binding.split_issue_selector(self.issue_ref, location + ".issue_ref")
        if self.project_key is not None:
            record.require_key(self.project_key, location + ".project_key")
        if self.alias is not None:
            record.require_key(self.alias, location + ".alias")
        record.require_member(self.intent, record.INTENTS, location + ".intent")
        record.require_optional_str(self.domain, location + ".domain",
                                    record.MAX_KEY_CHARS)
        record.require_optional_hex(self.proposal_digest_sha256,
                                    location + ".proposal_digest_sha256", 64)
        return self

    def as_dict(self):
        return dict((key, getattr(self, key)) for key in INBOUND_KEYS)

    def identity(self):
        return (self.transport, self.conversation_ref, self.message_ref)

    def digest(self):
        """Content identity of the validated turn (identity included)."""
        return json_digest(self.validate().as_dict())


def inbound_from_dict(value, location="inbound"):
    record.require_dict(value, location)
    record.require_closed_keys(value, INBOUND_KEYS, location)
    return InboundTurn(**dict((key, value[key]) for key in INBOUND_KEYS)).validate(
        location)


# -- derivation -------------------------------------------------------


@dataclass(frozen=True)
class RouteDerivation:
    """Everything a route decision records, derived purely."""

    outcome: str
    mission_id: Optional[str]
    resolved_tier: Optional[str]
    candidates: List[str]
    reason: str
    detail: str
    proposal_digest_sha256: Optional[str]
    bound_revision: Optional[int]
    observed_revision: Optional[int]
    observation_point: Optional[dict]
    lane_outcome: str
    lane: Optional[str]
    lane_reason: Optional[str]


def _clarify(reason, detail, candidates=(), bound_revision=None,
             observed_revision=None, observation_point=None):
    return dict(outcome=record.ROUTE_CLARIFICATION_REQUIRED, mission_id=None,
                resolved_tier=None, candidates=sorted(candidates), reason=reason,
                detail=detail, proposal_digest_sha256=None,
                bound_revision=bound_revision, observed_revision=observed_revision,
                observation_point=observation_point)


def _resolved(mission_id, tier, observed, bound_revision):
    return dict(outcome=record.ROUTE_EXISTING_MISSION, mission_id=mission_id,
                resolved_tier=tier, candidates=[mission_id],
                reason=record.REASON_RESOLVED,
                detail="resolved by %s" % tier, proposal_digest_sha256=None,
                bound_revision=bound_revision,
                observed_revision=observed.current_revision,
                observation_point=observation.observation_point(observed))


def _observe(observe, mission_id):
    result = observe(mission_id)
    if not isinstance(result, observation.FreshnessResult):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "the observation lookup must return a FreshnessResult; got %s"
                    % type(result).__name__)
    record.require_member(result.freshness, record.FRESHNESS_STATES, "freshness")
    return result


def _resolve(mission_id, tier, bindings, observe):
    """Resolve ``mission_id`` reached through ``tier`` (with the active
    bindings that named it, empty for the explicit tier) against a
    fresh observation, or clarify truthfully."""
    bound_revision = None if not bindings else bindings[0]["bound_revision"]
    result = _observe(observe, mission_id)
    if result.freshness == record.FRESHNESS_ABSENT:
        if bindings:
            return _clarify(record.REASON_BINDING_STALE,
                            "the binding names mission %s, which the observation"
                            " source states does not exist" % mission_id,
                            [mission_id], bound_revision)
        return _clarify(record.REASON_MISSION_NOT_OBSERVED,
                        "the observation source states mission %s does not"
                        " exist; ask the human rather than guess" % mission_id,
                        [mission_id])
    if result.freshness != record.FRESHNESS_FRESH:
        return _clarify(_FRESHNESS_REASONS[result.freshness],
                        "mission %s could not be observed %s: %s"
                        % (mission_id, result.freshness.lower(), result.problem),
                        [mission_id], bound_revision)
    observed = result.observation
    # The recorded bound_revision is the OFFENDING binding's (review
    # R2-2): that field exists to explain why routing clarified, so it
    # must name the binding that actually failed, not the first consulted.
    for bound in bindings:
        if bound["bound_revision"] != observed.current_revision:
            return _clarify(record.REASON_BINDING_STALE,
                            "binding %s was bound at revision %d but mission %s is"
                            " at revision %d; the context is stale and routing"
                            " never advances a binding"
                            % (bound["binding_id"], bound["bound_revision"],
                               mission_id, observed.current_revision),
                            [mission_id], bound["bound_revision"],
                            observed.current_revision,
                            observation.observation_point(observed))
        if bound["kind"] in (record.BINDING_REPOSITORY, record.BINDING_ISSUE) and (
            binding.repository_of_selector(bound) != observed.repository_url
        ):
            return _clarify(record.REASON_BINDING_STALE,
                            "binding %s names repository %s but mission %s is"
                            " observed at %r"
                            % (bound["binding_id"],
                               binding.repository_of_selector(bound), mission_id,
                               observed.repository_url),
                            [mission_id], bound["bound_revision"],
                            observed.current_revision,
                            observation.observation_point(observed))
    return _resolved(mission_id, tier, observed, bound_revision)


def _named_tiers(turn):
    """The binding tiers the turn names, in resolution order, as
    ``(tier, kind, selector)``."""
    reply = turn.reply_to_message_ref
    return [
        (tier, kind, selector) for tier, kind, selector in (
            (record.TIER_REPLY_TO_BINDING, record.BINDING_REPLY_TO_MESSAGE, reply),
            (record.TIER_APPROVAL_BINDING, record.BINDING_APPROVAL_PRESENTATION,
             reply),
            (record.TIER_RESULT_BINDING, record.BINDING_RESULT_PRESENTATION, reply),
            (record.TIER_REPOSITORY_REFERENCE, record.BINDING_REPOSITORY,
             turn.repository_url),
            (record.TIER_ISSUE_REFERENCE, record.BINDING_ISSUE, turn.issue_ref),
            (record.TIER_PROJECT_BINDING, record.BINDING_PROJECT, turn.project_key),
            (record.TIER_ALIAS_BINDING, record.BINDING_ALIAS, turn.alias),
        ) if selector is not None
    ]


def _hits(turn, document):
    """``(tier, binding)`` for every non-revoked binding a named tier
    finds, in tier order."""
    hits = []
    for tier, kind, selector in _named_tiers(turn):
        scoped = kind in record.CONVERSATION_SCOPED_BINDING_KINDS
        for bound in binding.find(document, kind, turn.transport,
                                  turn.conversation_ref if scoped else None,
                                  selector):
            hits.append((tier, bound))
    return hits


def conversation_missions(document, transport, conversation_ref, now):
    """The distinct Missions every ACTIVE conversation-scoped binding of
    the conversation names, sorted."""
    return sorted(set(
        bound["mission_id"] for bound in binding.conversation_bindings(
            document, transport, conversation_ref, now)))


def _identity_outcome(turn, document, observe, now):
    explicit = turn.explicit_mission_id
    hits = _hits(turn, document)
    active = [(tier, b) for tier, b in hits if binding.is_active(b, now)]
    if explicit is not None:
        if turn.intent == record.INTENT_NEW_MISSION:
            return _clarify(record.REASON_CONTEXT_DISAGREES,
                            "the turn names mission %s and also asks for a new"
                            " Mission; ask which was meant" % explicit, [explicit])
        others = sorted(set(b["mission_id"] for _, b in active
                            if b["mission_id"] != explicit))
        if others:
            return _clarify(record.REASON_CONTEXT_DISAGREES,
                            "the turn names mission %s but its context is bound to"
                            " %s; ask which was meant" % (explicit, ", ".join(others)),
                            [explicit] + others)
        # APPLICABLE matching context is consulted FIRST (review F3 as
        # narrowed by round 2, AMD-1 through the explicit tier): the
        # bindings this turn's selectors actually name for the Mission,
        # plus genuine CONVERSATION-kind bindings of this conversation —
        # never per-message or other selector-scoped bindings the turn did
        # not name, so unrelated history cannot block a valid reply. An
        # expired applicable binding clarifies, and every active one must
        # stand at the observed revision: naming the Mission never turns
        # stale context into a resolution.
        matching = [b for _, b in hits if b["mission_id"] == explicit]
        named = set(b["binding_id"] for b in matching)
        matching += [b for b in binding.conversation_bindings(
            document, turn.transport, turn.conversation_ref, now)
            if b["mission_id"] == explicit and b["binding_id"] not in named
            and b["kind"] == record.BINDING_CONVERSATION]
        for bound in matching:
            if binding.is_expired(bound, now):
                return _clarify(record.REASON_BINDING_EXPIRED,
                                "the turn names mission %s through context whose"
                                " binding %s expired at %d; ask rather than treat"
                                " expired context as current"
                                % (explicit, bound["binding_id"], bound["expires_at"]),
                                [explicit], bound["bound_revision"])
        return _resolve(explicit, record.TIER_EXPLICIT_MISSION_ID, matching, observe)
    if turn.intent == record.INTENT_NEW_MISSION:
        if turn.proposal_digest_sha256 is not None:
            return _proposal(turn)
        return _clarify(record.REASON_NO_CONTEXT,
                        "the turn asks for a new Mission but carries no proposal")
    for tier, bound in hits:
        if binding.is_expired(bound, now):
            return _clarify(record.REASON_BINDING_EXPIRED,
                            "binding %s for this context expired at %d; it is not"
                            " a resolution tier and routing never renews it"
                            % (bound["binding_id"], bound["expires_at"]),
                            [bound["mission_id"]], bound["bound_revision"])
        return _resolve(bound["mission_id"], tier, [bound], observe)
    members = conversation_missions(document, turn.transport,
                                    turn.conversation_ref, now)
    if len(members) == 1:
        mission_id = members[0]
        bindings = [b for b in binding.conversation_bindings(
            document, turn.transport, turn.conversation_ref, now)
            if b["mission_id"] == mission_id]
        return _resolve(mission_id, record.TIER_UNIQUE_CONVERSATION_MATCH,
                        bindings, observe)
    if len(members) > MAX_ROUTE_CANDIDATES:
        return _clarify(record.REASON_AMBIGUOUS_CANDIDATES,
                        "this conversation is bound to %d Missions, more than the"
                        " %d a clarification enumerates; ask which was meant"
                        % (len(members), MAX_ROUTE_CANDIDATES))
    if len(members) > 1:
        return _clarify(record.REASON_AMBIGUOUS_CANDIDATES,
                        "this conversation is bound to %d Missions; ask which was"
                        " meant rather than guess" % len(members), members)
    if turn.proposal_digest_sha256 is not None and (
        turn.intent != record.INTENT_FOLLOW_UP
    ):
        return _proposal(turn)
    return _clarify(record.REASON_NO_CONTEXT,
                    "no explicit id, binding or proposal identifies a Mission; the"
                    " natural-language routing turn is not implemented")


def _proposal(turn):
    return dict(outcome=record.ROUTE_NEW_PROPOSAL, mission_id=None,
                resolved_tier=None, candidates=[],
                reason=record.REASON_NEW_PROPOSAL_INTENT,
                detail="a new proposal with digest %s; creating the Mission is"
                       " Mission Core's act, not this decision's"
                       % turn.proposal_digest_sha256,
                proposal_digest_sha256=turn.proposal_digest_sha256,
                bound_revision=None, observed_revision=None,
                observation_point=None)


def require_available_lanes(value):
    if not isinstance(value, frozenset):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "available lanes must be a frozenset; got %s"
                    % type(value).__name__)
    for lane in value:
        record.require_member(lane, record.LANES, "available lane")
    return value


def select_lane(outcome, domain, available_lanes):
    """``(lane_outcome, lane, lane_reason)`` for a resolved-or-proposed
    route in ``domain``; a value, never a permission."""
    require_available_lanes(available_lanes)
    if domain is None:
        return (record.LANE_OUTCOME_NOT_REQUESTED, None, None)
    if domain not in record.SUPPORTED_DOMAINS:
        return (record.LANE_OUTCOME_REFUSED, None,
                record.LANE_REASON_DOMAIN_UNSUPPORTED)
    if outcome == record.ROUTE_CLARIFICATION_REQUIRED:
        return (record.LANE_OUTCOME_REFUSED, None,
                record.LANE_REASON_ROUTE_NOT_RESOLVED)
    lane = record.LANE_BY_DOMAIN[domain]
    if lane not in available_lanes:
        return (record.LANE_OUTCOME_REFUSED, None,
                record.LANE_REASON_CAPABILITY_UNAVAILABLE)
    return (record.LANE_OUTCOME_SELECTED, lane, None)


def derive_route(turn, document, observe, now, available_lanes):
    """The pure route decision for ``turn`` over the document's bindings
    and the injected observation lookup (``mission_id -> FreshnessResult``)."""
    if not isinstance(turn, InboundTurn):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "derive_route takes an InboundTurn; got %s" % type(turn).__name__)
    turn.validate()
    record.require_timestamp(now, "now")
    fields = _identity_outcome(turn, document, observe, now)
    lane_outcome, lane, lane_reason = select_lane(fields["outcome"], turn.domain,
                                                  available_lanes)
    return RouteDerivation(lane_outcome=lane_outcome, lane=lane,
                           lane_reason=lane_reason, **fields)


# -- the route record -------------------------------------------------


def new_route_record(route_id, turn, derivation, decided_at, context):
    """A validated route decision record."""
    if not isinstance(derivation, RouteDerivation):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "a route record takes a RouteDerivation; got %s"
                    % type(derivation).__name__)
    point = derivation.observation_point
    return validate_route({
        "route_id": route_id,
        "decided_at": decided_at,
        "inbound": turn.as_dict(),
        "inbound_digest_sha256": turn.digest(),
        "outcome": derivation.outcome,
        "mission_id": derivation.mission_id,
        "resolved_tier": derivation.resolved_tier,
        "candidates": list(derivation.candidates),
        "reason": derivation.reason,
        "detail": derivation.detail,
        "proposal_digest_sha256": derivation.proposal_digest_sha256,
        "bound_revision": derivation.bound_revision,
        "observed_revision": derivation.observed_revision,
        "observation_point": None if point is None else dict(point),
        "lane_outcome": derivation.lane_outcome,
        "lane": derivation.lane,
        "lane_reason": derivation.lane_reason,
        "provenance": record.provenance_record(
            context, decided_at, derivation.observed_revision,
            None if point is None else point["cursor"]),
        "authority": record.AUTHORITY_NONE,
    })


def _incoherent(location, message):
    record.fail(record.PROBLEM_BAD_VALUE, "%s is incoherent: %s" % (location, message))


def validate_route(value, location="route"):
    """One closed route decision record and its coherence rules."""
    record.require_dict(value, location)
    record.require_closed_keys(value, ROUTE_KEYS, location)
    record.require_id(value["route_id"], record.ROUTE_ID_PREFIX, location + ".route_id")
    record.require_timestamp(value["decided_at"], location + ".decided_at")
    turn = inbound_from_dict(value["inbound"], location + ".inbound")
    record.require_hex(value["inbound_digest_sha256"],
                       location + ".inbound_digest_sha256", 64)
    if value["inbound_digest_sha256"] != turn.digest():
        _incoherent(location, "inbound_digest_sha256 does not recompute from inbound")
    outcome = record.require_member(value["outcome"], record.ROUTE_OUTCOMES,
                                    location + ".outcome")
    record.require_optional_id(value["mission_id"], record.MISSION_ID_PREFIX,
                               location + ".mission_id")
    if value["resolved_tier"] is not None:
        record.require_member(value["resolved_tier"], record.ROUTE_TIERS,
                              location + ".resolved_tier")
    record.require_sorted_ids(value["candidates"], record.MISSION_ID_PREFIX,
                              location + ".candidates", MAX_ROUTE_CANDIDATES)
    reason = record.require_member(value["reason"], record.ROUTE_REASONS,
                                   location + ".reason")
    record.require_str(value["detail"], location + ".detail", record.MAX_DETAIL_CHARS)
    record.require_optional_hex(value["proposal_digest_sha256"],
                                location + ".proposal_digest_sha256", 64)
    record.require_optional_int(value["bound_revision"], location + ".bound_revision",
                                minimum=1)
    record.require_optional_int(value["observed_revision"],
                                location + ".observed_revision", minimum=1)
    point = value["observation_point"]
    if point is not None:
        observation.validate_observation_point(point, location + ".observation_point")
    if (point is None) != (value["observed_revision"] is None):
        _incoherent(location, "observed_revision and observation_point are recorded"
                              " together or not at all")
    if point is not None and point["revision"] != value["observed_revision"]:
        _incoherent(location, "observed_revision differs from the observation point")
    existing = outcome == record.ROUTE_EXISTING_MISSION
    if (value["mission_id"] is not None) != existing or (
        (value["resolved_tier"] is not None) != existing
    ):
        _incoherent(location, "mission_id and resolved_tier are set exactly for"
                              " EXISTING_MISSION")
    if existing and (reason != record.REASON_RESOLVED or point is None
                     or value["mission_id"] not in value["candidates"]):
        _incoherent(location, "EXISTING_MISSION requires reason RESOLVED, a fresh"
                              " observation point and the mission among candidates")
    if not existing and reason == record.REASON_RESOLVED:
        _incoherent(location, "only EXISTING_MISSION is RESOLVED")
    proposal = outcome == record.ROUTE_NEW_PROPOSAL
    if (value["proposal_digest_sha256"] is not None) != proposal or (
        proposal != (reason == record.REASON_NEW_PROPOSAL_INTENT)
    ):
        _incoherent(location, "proposal_digest_sha256 and NEW_PROPOSAL_INTENT are"
                              " set exactly for NEW_PROPOSAL")
    if proposal and (value["candidates"] or point is not None):
        _incoherent(location, "NEW_PROPOSAL names no candidate and no observation")
    binding_tier = existing and value["resolved_tier"] != (
        record.TIER_EXPLICIT_MISSION_ID)
    if binding_tier or reason in _BINDING_REASONS:
        if value["bound_revision"] is None:
            _incoherent(location, "a binding tier records bound_revision")
    elif value["bound_revision"] is not None and not value["candidates"]:
        # The explicit tier may record the matching context it consulted
        # (review F3); a decision that consulted no Mission records none.
        _incoherent(location, "bound_revision without a consulted Mission")
    lane_outcome = record.require_member(value["lane_outcome"], record.LANE_OUTCOMES,
                                         location + ".lane_outcome")
    if value["lane"] is not None:
        record.require_member(value["lane"], record.LANES, location + ".lane")
    if value["lane_reason"] is not None:
        record.require_member(value["lane_reason"], record.LANE_REASONS,
                              location + ".lane_reason")
    if (value["lane"] is not None) != (lane_outcome == record.LANE_OUTCOME_SELECTED):
        _incoherent(location, "lane is set exactly when SELECTED")
    if (value["lane_reason"] is not None) != (lane_outcome == record.LANE_OUTCOME_REFUSED):
        _incoherent(location, "lane_reason is set exactly when REFUSED")
    if lane_outcome != record.LANE_OUTCOME_NOT_REQUESTED and turn.domain is None:
        _incoherent(location, "a lane outcome without a requested domain")
    if lane_outcome == record.LANE_OUTCOME_NOT_REQUESTED and turn.domain is not None:
        _incoherent(location, "a requested domain without a lane outcome")
    if lane_outcome == record.LANE_OUTCOME_SELECTED and (
        outcome == record.ROUTE_CLARIFICATION_REQUIRED
    ):
        _incoherent(location, "a clarification selects no lane")
    provenance = record.validate_provenance(value["provenance"],
                                            location + ".provenance")
    if provenance["received_at"] != value["decided_at"] or (
        provenance["observed_revision"] != value["observed_revision"]
    ) or provenance["observation_cursor"] != (
        None if point is None else point["cursor"]
    ):
        _incoherent(location, "provenance does not restate decided_at and the"
                              " observation point")
    record.require_authority(value["authority"], location)
    return value


def route_identity(route):
    return (route["inbound"]["transport"], route["inbound"]["conversation_ref"],
            route["inbound"]["message_ref"])


def validate_route_decisions(document, path):
    """Every route record, key equals id, and unique inbound identity."""
    seen = {}
    for key in sorted(document["route_decisions"]):
        route = document["route_decisions"][key]
        where = "route %r" % key
        validate_route(route, where)
        if route["route_id"] != key:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s carries route_id %r" % (where, route["route_id"]))
        who = route_identity(route)
        if who in seen:
            record.fail(record.PROBLEM_ROUTE_CONFLICT,
                        "%s and route %r decide the same inbound turn %r; a turn"
                        " is decided once" % (where, seen[who], who))
        seen[who] = key
    return document


@dataclass(frozen=True)
class Replay:
    """A stored decision returned as history: ``replayed`` is always
    True and ``route`` is the record exactly as decided."""

    replayed: bool
    route: dict


def find_replay(document, turn, context):
    """The stored decision for ``turn``'s identity, or None. Refuses a
    different authenticated context with no decision content, and the
    same context with different content as an idempotency conflict."""
    turn.validate()
    record.require_context(context)
    for key in sorted(document["route_decisions"]):
        route = document["route_decisions"][key]
        if route_identity(route) != turn.identity():
            continue
        recorded = dict((k, route["provenance"][k]) for k in record.CONTEXT_KEYS)
        if recorded != context.as_dict():
            record.fail(record.PROBLEM_CONTEXT_MISMATCH,
                        "a decision for this inbound turn exists but was recorded"
                        " under a different authenticated context; nothing about"
                        " it is disclosed")
        if route["inbound_digest_sha256"] != turn.digest():
            record.fail(record.PROBLEM_IDEMPOTENCY_CONFLICT,
                        "this inbound turn was already decided with different"
                        " content; a turn is decided once")
        return Replay(replayed=True, route=route)
    return None
