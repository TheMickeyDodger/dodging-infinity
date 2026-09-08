"""The coordination service: the only module that reads the clock or
asks the observation source, and the owner of every load-modify-save
cycle over the one coordination document.

Every mutating operation holds the store's cross-process lock around
its whole cycle: load (validated), observe if the operation needs a
Mission fact, apply ONE pure operation from the family module, save
(validated, at the sequence the document was loaded at). The store's
``store_sequence`` guard therefore refuses a conflicting write even if
the lock were misused, and a refusal anywhere inside the cycle writes
nothing. Reads load and validate but never write.

Observation. ``_observe`` asks the injected read-only source once per
Mission per operation through ``observation.observe_safely`` (a raising
or ill-typed source becomes UNAVAILABLE, class name only) and
classifies the answer against the durable high-water mark for that
Mission. The mark (A-R3) is derived here from ALL families that record
an observation point — bindings, route decisions (including
clarifications that were made under a FRESH read), attention records,
handoffs with every transition, and rosters — as the maximum
``(revision, cursor)`` point and its recorded content, so a source
answering behind that point is STALE and a source answering at that
point with different content is INCONSISTENT. Only a FRESH observation
is ever recorded, so a non-FRESH answer can never raise the floor.

Route replay never observes: the stored decision is looked up first,
and only a turn with no decision is derived. Routing never touches a
binding; ``rebind`` is the separate bounded act (AMD-1) that revokes
the old binding and makes a new one for the same identity under its
own FRESH observation.

Clock and id minting are injected for determinism; nothing here
performs any effect beyond the store file. No Mission or Capability is
launched, no message is sent (the attention presenter is an injected
abstract seam), no Herdr work is started, and no delivery action
exists. Every record the service writes carries ``authority: "none"``.
"""

from coordination import attention
from coordination import binding
from coordination import handoff
from coordination import observation
from coordination import record
from coordination import routing
from coordination import store


def _points(document, mission_id):
    """Every observation point Task 6 durably holds for ``mission_id``."""
    found = []
    for value in document["bindings"].values():
        if value["mission_id"] == mission_id:
            found.append(value["observation_point"])
    for value in document["route_decisions"].values():
        point = value["observation_point"]
        if point is None:
            continue
        subject = value["mission_id"]
        if subject is None and len(value["candidates"]) == 1:
            subject = value["candidates"][0]
        if subject == mission_id:
            found.append(point)
    for value in document["attention"].values():
        if value["mission_id"] == mission_id:
            # Every read the record retains: creation, surfacing,
            # acknowledgment (when FRESH) and closure (review F2).
            for key in attention.OBSERVATION_POINT_KEYS:
                if value[key] is not None:
                    found.append(value[key])
    for value in document["handoffs"].values():
        if value["mission_id"] == mission_id:
            found.append(value["observation_point"])
            for entry in value["transitions"]:
                found.append(entry["observation_point"])
    roster = document["participants"].get(mission_id)
    if roster is not None:
        found.append(roster["observation_point"])
    return found


def high_water_mark(document, mission_id):
    """The durable floor for ``mission_id``: the maximum recorded
    ``(revision, cursor)`` point with its content, or None when nothing
    is recorded. Two recorded points at the same maximum with different
    content are a durable inconsistency and refuse."""
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    best = None
    for point in _points(document, mission_id):
        pair = (point["revision"], record.cursor_value(point["cursor"]))
        if best is None or pair > best[0]:
            best = (pair, point)
        elif pair == best[0] and point != best[1]:
            record.fail(record.PROBLEM_OBSERVATION_INCONSISTENT,
                        "mission %s holds two recorded points at revision %d cursor"
                        " %s with different content" % (mission_id, point["revision"],
                                                         point["cursor"]))
    if best is None:
        return None
    point = best[1]
    return observation.HighWaterMark(revision=point["revision"],
                                     cursor=point["cursor"], point=dict(point))


class CoordinationService(object):
    """Routing, bindings, attention and handoffs over one durable
    document, one lock, one clock, one read-only observation source."""

    def __init__(self, coordination_store, clock, observation_source,
                 available_lanes, mint_id=None):
        if not isinstance(coordination_store, store.CoordinationStore):
            record.fail(record.PROBLEM_BAD_TYPE,
                        "service requires a CoordinationStore; got %s"
                        % type(coordination_store).__name__)
        if not isinstance(observation_source, observation.MissionObservationSource):
            record.fail(record.PROBLEM_BAD_TYPE,
                        "service requires a MissionObservationSource; got %s"
                        % type(observation_source).__name__)
        routing.require_available_lanes(available_lanes)
        self.store = coordination_store
        self._clock = clock
        self._source = observation_source
        self._lanes = available_lanes
        self._mint = record.mint_id if mint_id is None else mint_id

    # -- plumbing -----------------------------------------------------

    def _now(self):
        return record.require_timestamp(self._clock(), "clock")

    def _fresh_id(self, prefix, taken):
        for _ in range(8):
            candidate = record.require_id(self._mint(prefix), prefix, "minted id")
            if candidate not in taken:
                return candidate
        record.fail(record.PROBLEM_BAD_VALUE,
                    "the id minter kept returning ids already in use")

    def _observe(self, document, mission_id, now):
        outcome = observation.observe_safely(self._source, mission_id)
        return observation.classify(outcome, now, mission_id,
                                    high_water_mark(document, mission_id))

    def _fresh(self, document, mission_id, now, what):
        return handoff.require_fresh(self._observe(document, mission_id, now),
                                     mission_id, what)

    def inspect(self):
        """The validated document, for inspection; never written."""
        return self.store.load()

    # -- routing ------------------------------------------------------

    def route(self, turn, context):
        """The stored decision for a replayed turn (context-bound), or a
        new decision derived, recorded and returned."""
        record.require_context(context)
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            replay = routing.find_replay(document, turn, context)
            if replay is not None:
                return replay
            now = self._now()
            derivation = routing.derive_route(
                turn, document, lambda mission_id: self._observe(
                    document, mission_id, now), now, self._lanes)
            route_id = self._fresh_id(record.ROUTE_ID_PREFIX,
                                      document["route_decisions"])
            route = routing.new_route_record(route_id, turn, derivation, now, context)
            document["route_decisions"][route_id] = route
            self.store.save(document, expected_sequence=sequence)
        return routing.Replay(replayed=False, route=route)

    # -- bindings -----------------------------------------------------

    def bind(self, kind, transport, conversation_ref, selector, mission_id, context,
             expires_at=None):
        """A new binding under a FRESH observation of ``mission_id``."""
        record.require_context(context)
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            now = self._now()
            observed = self._fresh(document, mission_id, now, "binding")
            bound = self._bind(document, kind, transport, conversation_ref, selector,
                               mission_id, observed, now, expires_at, context)
            self.store.save(document, expected_sequence=sequence)
        return bound

    def _bind(self, document, kind, transport, conversation_ref, selector,
              mission_id, observed, now, expires_at, context):
        for existing in binding.find(document, kind, transport, conversation_ref,
                                     selector):
            record.fail(record.PROBLEM_BINDING_CONFLICT,
                        "binding %s already holds this identity; rebind it instead"
                        % existing["binding_id"])
        binding_id = self._fresh_id(record.BINDING_ID_PREFIX, document["bindings"])
        bound = binding.new_binding(binding_id, kind, transport, conversation_ref,
                                    selector, mission_id, observed, now, expires_at,
                                    context)
        document["bindings"][binding_id] = bound
        binding.validate_bindings(document, self.store.path)
        return bound

    def revoke_binding(self, binding_id, context):
        record.require_context(context)
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            bound = self._binding(document, binding_id)
            revoked = binding.revoke(bound, self._now())
            document["bindings"][binding_id] = revoked
            self.store.save(document, expected_sequence=sequence)
        return revoked

    def rebind(self, binding_id, context, expires_at=None):
        """The separate bounded act AMD-1 requires: revoke ``binding_id``
        and bind the same identity to the same Mission again under a
        FRESH observation. Routing never does this."""
        record.require_context(context)
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            old = self._binding(document, binding_id)
            now = self._now()
            observed = self._fresh(document, old["mission_id"], now, "rebinding")
            document["bindings"][binding_id] = binding.revoke(old, now)
            fresh = self._bind(document, old["kind"], old["transport"],
                               old["conversation_ref"], old["selector"],
                               old["mission_id"], observed, now, expires_at, context)
            self.store.save(document, expected_sequence=sequence)
        return fresh

    @staticmethod
    def _binding(document, binding_id):
        record.require_id(binding_id, record.BINDING_ID_PREFIX, "binding_id")
        bound = document["bindings"].get(binding_id)
        if bound is None:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "binding %s is not in the document" % binding_id)
        return bound

    # -- attention ----------------------------------------------------

    def project_attention(self, mission_id, destination):
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            now = self._now()
            result = self._observe(document, mission_id, now)
            outcome = attention.project(
                document, mission_id, destination, result, now,
                lambda: self._fresh_id(record.ATTENTION_ID_PREFIX,
                                       document["attention"]))
            if outcome.created or outcome.obsoleted or outcome.resolved:
                self.store.save(document, expected_sequence=sequence)
        return outcome

    def surface_attention(self, attention_id, presenter):
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            value = self._attention(document, attention_id)
            now = self._now()
            result = self._observe(document, value["mission_id"], now)
            outcome = attention.surface(
                document, attention_id, result, presenter, now,
                lambda: self._fresh_id(record.ATTENTION_ID_PREFIX,
                                       document["attention"]))
            if outcome.surfaced or outcome.contradicted:
                self.store.save(document, expected_sequence=sequence)
        return outcome

    def acknowledge_attention(self, attention_id, context):
        record.require_context(context)
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            value = self._attention(document, attention_id)
            now = self._now()
            result = self._observe(document, value["mission_id"], now)
            acked = attention.acknowledge(document, attention_id, context, result, now)
            self.store.save(document, expected_sequence=sequence)
        return acked

    def pending_attention(self, destination):
        return attention.pending(self.store.load(), destination)

    def aggregate_attention(self, destination):
        return attention.aggregate(self.store.load(), destination)

    @staticmethod
    def _attention(document, attention_id):
        record.require_id(attention_id, record.ATTENTION_ID_PREFIX, "attention_id")
        value = document["attention"].get(attention_id)
        if value is None:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "attention %s is not in the document" % attention_id)
        return value

    # -- handoffs -----------------------------------------------------

    def set_roster(self, mission_id, participants, context):
        record.require_context(context)
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            now = self._now()
            roster = handoff.set_roster(document, mission_id, participants,
                                        self._observe(document, mission_id, now),
                                        now, context)
            self.store.save(document, expected_sequence=sequence)
        return roster

    def create_handoff(self, mission_id, source, destination, purpose, request_text,
                       evidence_refs, artifact_refs, idempotency_key,
                       parent_handoff_id, context):
        record.require_context(context)
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            now = self._now()
            before = set(document["handoffs"])
            created = handoff.create_handoff(
                document, self._fresh_id(record.HANDOFF_ID_PREFIX,
                                         document["handoffs"]),
                mission_id, source, destination, purpose, request_text,
                evidence_refs, artifact_refs, idempotency_key, parent_handoff_id,
                self._observe(document, mission_id, now), now, context)
            if created["handoff_id"] not in before:
                self.store.save(document, expected_sequence=sequence)
        return created

    def transition_handoff(self, handoff_id, status, actor, context):
        record.require_context(context)
        with self.store.lock():
            document = self.store.load()
            sequence = document["store_sequence"]
            record.require_id(handoff_id, record.HANDOFF_ID_PREFIX, "handoff_id")
            value = document["handoffs"].get(handoff_id)
            if value is None:
                record.fail(record.PROBLEM_BAD_VALUE,
                            "handoff %s is not in the document" % handoff_id)
            now = self._now()
            updated = handoff.transition(
                document, handoff_id, status, actor,
                self._observe(document, value["mission_id"], now), now, context)
            self.store.save(document, expected_sequence=sequence)
        return updated
