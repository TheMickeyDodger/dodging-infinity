"""Mission Authorization closed key schema (structure only).

A Mission Authorization is target/objective/constraints/rules/
desired-outcome/acceptance AUTHORITY, never an engineering plan.
Engineering decomposition belongs exclusively to the target
engineering Supervisor; a Mission Authorization that carries any
implementation-strategy content would move planning into the control
layer, which the mission forbids.

Two independent guards, BOTH required:

(a) closed-key-set — the key set must be exactly
    ``ALLOWED_AUTHORIZATION_KEYS``; anything outside it is refused,
    and every allowed key must be present.
(b) forbidden-strategy-keys — an explicit list of implementation-
    strategy key names is refused BY NAME (after NFKC + casefold +
    strip normalization), and the check runs FIRST so it is
    load-bearing on its own: even if guard (a) were removed, a
    document carrying ``plan``/``steps``/``files``/... would still be
    refused, with its own problem code.

Value rendering and semantic validation land with the rendering
increment; this module deliberately validates STRUCTURE only, with
one exception: ``delivery_authority`` must already be exactly
``"none"``, because that bound is structural throughout the system.
"""

import unicodedata

from workflow_authority.record import DELIVERY_AUTHORITY_NONE

# The authority content plus the visible bindings the mission's
# acceptance criterion names. Nothing else is a Mission Authorization
# key.
ALLOWED_AUTHORIZATION_KEYS = frozenset(
    (
        "objective",
        "constraints",
        "rules",
        "desired_outcome",
        "acceptance",
        "unresolved_questions",
        "execution_scope",
        "control",
        "target",
        "issue_or_pr",
        "baseline",
        "handoff",
        "telegram_approval",
        "workflow_id",
        "human_intent",
        "revision",
        "delivery_authority",
    )
)

# Implementation-strategy keys refused BY NAME, independently of the
# closed set above. Matching normalizes the key first (NFKC +
# casefold + strip) so a cosmetic variant — casing ("Plan"), a
# compatibility form ("ﬁles" for "files", full-width
# characters), or padding whitespace (" plan") — cannot smuggle
# strategy content past THIS guard even if the closed-set guard were
# absent.
FORBIDDEN_STRATEGY_KEYS = frozenset(
    (
        "plan",
        "steps",
        "files",
        "file_changes",
        "implementation",
        "strategy",
        "decomposition",
        "roles",
        "role_assignment",
        "sequencing",
        "tasks",
        "subtasks",
        "approach",
        "design",
        "patch",
        "diff",
    )
)

PROBLEM_NOT_AN_OBJECT = "authorization_not_an_object"
PROBLEM_BAD_KEY_TYPE = "authorization_bad_key_type"
PROBLEM_FORBIDDEN_KEY = "authorization_forbidden_strategy_key"
PROBLEM_UNKNOWN_KEY = "authorization_unknown_key"
PROBLEM_MISSING_KEY = "authorization_missing_key"
PROBLEM_DELIVERY_AUTHORITY = "authorization_delivery_authority"
PROBLEM_NESTED_FORBIDDEN_KEY = "authorization_nested_strategy_key"
PROBLEM_TOO_DEEP = "authorization_too_deep"
PROBLEM_TOO_MANY_NODES = "authorization_too_many_nodes"

# Hard bounds for the deep value walk, never derived from input. A
# document beyond either bound is REFUSED with exact numbers, never
# partially walked.
MAX_AUTHORIZATION_DEPTH = 8
MAX_AUTHORIZATION_NODES = 512


class AuthorizationError(Exception):
    """A Mission Authorization failed validation; message actionable."""

    def __init__(self, message, problem):
        super(AuthorizationError, self).__init__(message)
        self.problem = problem


def _strategy_key_form(key):
    """The normalized form guard (b) matches forbidden names against.

    NFKC folds compatibility characters (ligatures, full-width
    forms), casefold handles case beyond ASCII, strip removes
    padding. Guard (b) must be strong standing alone, not only in
    combination with the closed-set guard.
    """
    return unicodedata.normalize("NFKC", key).casefold().strip()


def validate_authorization_structure(document):
    """Validate the Mission Authorization key structure, failing closed.

    Raises AuthorizationError with a distinct ``problem`` code on the
    first failure. Guard (b) — forbidden implementation-strategy keys
    — runs BEFORE guard (a) so its refusal (and its problem code) does
    not depend on the closed-set check existing.
    """
    if not isinstance(document, dict):
        raise AuthorizationError(
            "Mission Authorization must be an object, not %s"
            % type(document).__name__,
            PROBLEM_NOT_AN_OBJECT,
        )
    for key in document:
        if not isinstance(key, str):
            raise AuthorizationError(
                "Mission Authorization has a non-string key %r" % (key,),
                PROBLEM_BAD_KEY_TYPE,
            )
    # Guard (b): forbidden implementation-strategy keys, by name.
    forbidden = sorted(
        key for key in document
        if _strategy_key_form(key) in FORBIDDEN_STRATEGY_KEYS
    )
    if forbidden:
        raise AuthorizationError(
            "Mission Authorization carries implementation-strategy"
            " keys: %s. A Mission Authorization is authority, never an"
            " engineering plan; strategy belongs to the target"
            " engineering Supervisor"
            % ", ".join(repr(key) for key in forbidden),
            PROBLEM_FORBIDDEN_KEY,
        )
    # Guard (a): closed key set, exactly.
    unknown = sorted(set(document) - ALLOWED_AUTHORIZATION_KEYS)
    if unknown:
        raise AuthorizationError(
            "Mission Authorization has unknown keys: %s (the key set"
            " is closed)" % ", ".join(repr(key) for key in unknown),
            PROBLEM_UNKNOWN_KEY,
        )
    missing = sorted(ALLOWED_AUTHORIZATION_KEYS - set(document))
    if missing:
        raise AuthorizationError(
            "Mission Authorization is missing required keys: %s"
            % ", ".join(repr(key) for key in missing),
            PROBLEM_MISSING_KEY,
        )
    authority = document["delivery_authority"]
    if not isinstance(authority, str) or authority != (
        DELIVERY_AUTHORITY_NONE
    ):
        raise AuthorizationError(
            "Mission Authorization delivery_authority must be exactly"
            " the string %r; got %r. No Mission Authorization may"
            " carry delivery authority"
            % (DELIVERY_AUTHORITY_NONE, authority),
            PROBLEM_DELIVERY_AUTHORITY,
        )


def _walk_values(value, depth, counter, location):
    counter[0] += 1
    if counter[0] > MAX_AUTHORIZATION_NODES:
        raise AuthorizationError(
            "Mission Authorization has more than %d nested nodes"
            " (refused at node %d); the deep strategy check will not"
            " partially walk an unbounded document"
            % (MAX_AUTHORIZATION_NODES, counter[0]),
            PROBLEM_TOO_MANY_NODES,
        )
    if depth > MAX_AUTHORIZATION_DEPTH:
        raise AuthorizationError(
            "Mission Authorization nests deeper than %d at %s; refused"
            % (MAX_AUTHORIZATION_DEPTH, location),
            PROBLEM_TOO_DEEP,
        )
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and _strategy_key_form(key) in (
                FORBIDDEN_STRATEGY_KEYS
            ):
                raise AuthorizationError(
                    "Mission Authorization carries the implementation-"
                    "strategy key %r nested at %s; strategy belongs to"
                    " the target engineering Supervisor at ANY depth"
                    % (key, location),
                    PROBLEM_NESTED_FORBIDDEN_KEY,
                )
            _walk_values(
                item, depth + 1, counter,
                "%s.%s" % (location, key),
            )
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk_values(
                item, depth + 1, counter,
                "%s[%d]" % (location, index),
            )


def validate_authorization_values_deep(document):
    """Refuse forbidden strategy KEYS at any nesting depth.

    The top-level structure check cannot see a plan smuggled as the
    VALUE of a permitted key when that value is itself an object (for
    example ``objective: {"plan": [...]}``). This walk refuses the
    forbidden key names at every depth, with hard depth and node
    bounds (refusal, never a partial walk).

    STATED RESIDUAL LIMIT: implementation strategy expressed as PROSE
    inside a string value is NOT detectable by any structural check —
    this walk narrows the gap, it does not close it. Callers must not
    claim structural prevention of prose strategy.
    """
    if not isinstance(document, dict):
        raise AuthorizationError(
            "Mission Authorization must be an object, not %s"
            % type(document).__name__,
            PROBLEM_NOT_AN_OBJECT,
        )
    counter = [0]
    for key, value in document.items():
        _walk_values(value, 1, counter, "$.%s" % (key,))
