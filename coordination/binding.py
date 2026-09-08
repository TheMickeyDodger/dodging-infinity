"""Bounded conversation bindings: the durable evidence the deterministic
routing tiers resolve through.

A binding says "in this conversation (or durably, for a repository,
issue, project or alias), THIS selector meant THAT Mission, as observed
at THIS revision". Kinds and selectors:

- ``REPLY_TO_MESSAGE`` / ``APPROVAL_PRESENTATION`` / ``RESULT_PRESENTATION``:
  a message reference inside one conversation;
- ``CONVERSATION``: the conversation itself (selector ``""``);
- ``REPOSITORY``: an already-canonical repository URL; ``ISSUE``: that URL
  plus ``#<issue number>``; ``PROJECT`` / ``ALIAS``: an opaque key. These
  four are durable lookups with no conversation.

Every kind records ``bound_revision``, the Mission's current revision
in the FRESH observation the bind was made under, together with the
whole ``observation_point`` of that read (AMD-1, A-R3). Resolution is
revision-exact for EVERY kind: a binding whose bound revision is not
the observed current revision does not resolve; the router clarifies
with ``BINDING_STALE``. There is no escape flag. Routing never repairs,
refreshes or advances a binding; rebinding is a separate act that
requires its own FRESH observation and writes its own record.

Identity and uniqueness. A binding's identity is ``(kind, transport,
conversation_ref, selector)``. At most one NON-REVOKED binding may hold
an identity (``coordination_binding_conflict``); revocation is explicit,
timestamped and final. An expired binding is not active and is never a
resolution tier, but it keeps its identity until revoked, so a stale
context cannot be silently re-pointed by binding over it. Caps are
module constants: bindings per Mission, bindings per conversation, and
the longest validity a caller may declare.

Nothing here reads a clock, observes a Mission, or writes durable
state; the service does, under the store lock. A binding carries no
authority: ``authority`` is ``"none"`` and the loader refuses anything
else.
"""

from coordination import observation
from coordination import record

# Exact-value pinned in the bound-constant table.
MAX_BINDINGS_PER_MISSION = 64
MAX_BINDINGS_PER_CONVERSATION = 256
# Ten years: the longest validity a caller may declare on a binding.
MAX_BINDING_VALIDITY_SECONDS = 315360000

BINDING_KEYS = (
    "binding_id", "kind", "transport", "conversation_ref", "selector",
    "mission_id", "bound_revision", "bound_at", "expires_at", "revoked",
    "revoked_at", "bound_by", "observation_point", "authority",
)
_MESSAGE_SELECTOR_KINDS = (
    record.BINDING_APPROVAL_PRESENTATION, record.BINDING_REPLY_TO_MESSAGE,
    record.BINDING_RESULT_PRESENTATION,
)
_REPOSITORY_SELECTOR_KINDS = (record.BINDING_ISSUE, record.BINDING_REPOSITORY)
_DIGITS = frozenset("0123456789")


def split_issue_selector(value, location):
    """``(canonical repository url, issue number)`` of an ISSUE selector."""
    record.require_str(value, location, record.MAX_SELECTOR_CHARS)
    url, separator, number = value.rpartition("#")
    if not separator or not number or any(ch not in _DIGITS for ch in number) or (
        number[0] == "0"
    ):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s must be <canonical repository url>#<issue number>; got %r"
                    % (location, value))
    record.require_repository_url(url, location + " repository")
    return url, int(number)


def repository_of_selector(binding):
    """The canonical repository URL a REPOSITORY or ISSUE binding names."""
    if binding["kind"] == record.BINDING_REPOSITORY:
        return binding["selector"]
    return split_issue_selector(binding["selector"], "selector")[0]


def _validate_selector(kind, selector, location):
    if kind in _MESSAGE_SELECTOR_KINDS:
        return record.require_str(selector, location, record.MAX_MESSAGE_REF_CHARS)
    if kind == record.BINDING_CONVERSATION:
        if selector != "":
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s must be \"\" for a CONVERSATION binding; the"
                        " conversation itself is the selector" % location)
        return selector
    if kind == record.BINDING_REPOSITORY:
        record.require_str(selector, location, record.MAX_SELECTOR_CHARS)
        return record.require_repository_url(selector, location)
    if kind == record.BINDING_ISSUE:
        split_issue_selector(selector, location)
        return selector
    return record.require_key(selector, location)


def validate_binding(value, location="binding"):
    """One closed binding record, or refuse."""
    record.require_dict(value, location)
    record.require_closed_keys(value, BINDING_KEYS, location)
    record.require_id(value["binding_id"], record.BINDING_ID_PREFIX,
                      location + ".binding_id")
    kind = record.require_member(value["kind"], record.BINDING_KINDS,
                                 location + ".kind")
    record.require_transport(value["transport"], location + ".transport")
    scoped = kind in record.CONVERSATION_SCOPED_BINDING_KINDS
    if scoped:
        record.require_str(value["conversation_ref"], location + ".conversation_ref",
                           record.MAX_CONVERSATION_REF_CHARS)
    elif value["conversation_ref"] is not None:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.conversation_ref must be null for a %s binding: it is a"
                    " durable lookup, not a conversation" % (location, kind))
    _validate_selector(kind, value["selector"], location + ".selector")
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_int(value["bound_revision"], location + ".bound_revision",
                       minimum=1)
    record.require_timestamp(value["bound_at"], location + ".bound_at")
    expires = record.require_optional_timestamp(value["expires_at"],
                                                location + ".expires_at")
    if expires is not None:
        if expires <= value["bound_at"]:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.expires_at must be later than bound_at" % location)
        if expires - value["bound_at"] > MAX_BINDING_VALIDITY_SECONDS:
            record.fail(record.PROBLEM_TOO_LARGE,
                        "%s declares %d seconds of validity; the hard bound is %d"
                        % (location, expires - value["bound_at"],
                           MAX_BINDING_VALIDITY_SECONDS))
    record.require_bool(value["revoked"], location + ".revoked")
    record.require_optional_timestamp(value["revoked_at"], location + ".revoked_at")
    if value["revoked"] != (value["revoked_at"] is not None):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s records revocation as (revoked, revoked_at) together or"
                    " not at all" % location)
    if value["revoked"] and value["revoked_at"] < value["bound_at"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.revoked_at precedes bound_at" % location)
    record.validate_context_dict(value["bound_by"], location + ".bound_by")
    observation.validate_observation_point(value["observation_point"],
                                           location + ".observation_point")
    if value["observation_point"]["revision"] != value["bound_revision"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.bound_revision %d is not the revision of its own"
                    " observation point (%d)"
                    % (location, value["bound_revision"],
                       value["observation_point"]["revision"]))
    record.require_authority(value["authority"], location)
    return value


def new_binding(binding_id, kind, transport, conversation_ref, selector,
                mission_id, observed, bound_at, expires_at, context):
    """A validated binding record made under the FRESH observation
    ``observed`` of ``mission_id``; the observation supplies
    ``bound_revision`` and the stored ``observation_point``."""
    record.require_context(context)
    if not isinstance(observed, observation.MissionObservation):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "a binding requires a MissionObservation; got %s"
                    % type(observed).__name__)
    observed.validate()
    record.require_id(mission_id, record.MISSION_ID_PREFIX, "mission_id")
    if observed.mission_id != mission_id:
        record.fail(record.PROBLEM_MISSION_MISMATCH,
                    "binding names mission %s but the observation is of %s"
                    % (mission_id, observed.mission_id))
    return validate_binding({
        "binding_id": binding_id,
        "kind": kind,
        "transport": transport,
        "conversation_ref": conversation_ref,
        "selector": selector,
        "mission_id": mission_id,
        "bound_revision": observed.current_revision,
        "bound_at": bound_at,
        "expires_at": expires_at,
        "revoked": False,
        "revoked_at": None,
        "bound_by": context.as_dict(),
        "observation_point": observation.observation_point(observed),
        "authority": record.AUTHORITY_NONE,
    })


def identity(binding):
    return (binding["kind"], binding["transport"], binding["conversation_ref"],
            binding["selector"])


def is_expired(binding, now):
    return binding["expires_at"] is not None and now >= binding["expires_at"]


def is_active(binding, now):
    """Not revoked and not expired at ``now``."""
    return not binding["revoked"] and not is_expired(binding, now)


def revoke(binding, now):
    """A revoked copy of ``binding``; revocation is explicit and final."""
    validate_binding(binding)
    record.require_timestamp(now, "now")
    if binding["revoked"]:
        record.fail(record.PROBLEM_INVALID_TRANSITION,
                    "binding %s is already revoked" % binding["binding_id"])
    if now < binding["bound_at"]:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "binding %s cannot be revoked before it was bound"
                    % binding["binding_id"])
    return validate_binding(dict(binding, revoked=True, revoked_at=now))


def find(document, kind, transport, conversation_ref, selector):
    """Every NON-REVOKED binding holding this identity (the loader keeps
    that at most one), in binding-id order. Expired bindings are
    returned so the caller can clarify truthfully rather than fall
    through as if no context existed."""
    wanted = (kind, transport, conversation_ref, selector)
    return [document["bindings"][key] for key in sorted(document["bindings"])
            if not document["bindings"][key]["revoked"]
            and identity(document["bindings"][key]) == wanted]


def conversation_bindings(document, transport, conversation_ref, now):
    """Every ACTIVE conversation-scoped binding of one conversation."""
    return [document["bindings"][key] for key in sorted(document["bindings"])
            if document["bindings"][key]["kind"] in (
                record.CONVERSATION_SCOPED_BINDING_KINDS)
            and document["bindings"][key]["transport"] == transport
            and document["bindings"][key]["conversation_ref"] == conversation_ref
            and is_active(document["bindings"][key], now)]


def validate_bindings(document, path):
    """Every binding record and the cross-record rules: key equals id,
    unique non-revoked identity, per-Mission and per-conversation caps."""
    held = set()
    per_mission = {}
    per_conversation = {}
    for key in sorted(document["bindings"]):
        binding = document["bindings"][key]
        where = "binding %r" % key
        validate_binding(binding, where)
        if binding["binding_id"] != key:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s carries binding_id %r" % (where, binding["binding_id"]))
        if not binding["revoked"]:
            who = identity(binding)
            if who in held:
                record.fail(record.PROBLEM_BINDING_CONFLICT,
                            "%s duplicates the non-revoked identity %r; rebinding"
                            " revokes first" % (where, who))
            held.add(who)
        per_mission[binding["mission_id"]] = per_mission.get(
            binding["mission_id"], 0) + 1
        if binding["conversation_ref"] is not None:
            conversation = (binding["transport"], binding["conversation_ref"])
            per_conversation[conversation] = per_conversation.get(
                conversation, 0) + 1
    for mission_id, count in per_mission.items():
        if count > MAX_BINDINGS_PER_MISSION:
            record.fail(record.PROBLEM_TOO_LARGE,
                        "mission %s holds %d bindings; the hard bound is %d"
                        % (mission_id, count, MAX_BINDINGS_PER_MISSION))
    for conversation, count in per_conversation.items():
        if count > MAX_BINDINGS_PER_CONVERSATION:
            record.fail(record.PROBLEM_TOO_LARGE,
                        "conversation %r holds %d bindings; the hard bound is %d"
                        % (conversation, count, MAX_BINDINGS_PER_CONVERSATION))
    return document
