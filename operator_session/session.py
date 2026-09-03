"""Operator session seam: prepare one turn, then execute it.

This module is PROVIDER-FREE. It imports no provider, no orchestration
machinery, no authority store, and no transport adapter; the only
provider-aware module in the package is ``operator_session.codex``.

The seam is a two-step boundary between a caller (a transport adapter,
a CLI) and whatever operator provider carries the turn:

- ``prepare`` builds the provider request, validates its identity, and
  returns a ``PreparedTurn`` that binds the request to the session that
  built it. The validated request id is CAPTURED into the turn as a
  frozen field at that moment, so every later read returns the same
  value regardless of what the request object does afterwards.
  ``prepare`` performs NO provider call, writes NO state, and has NO
  side effect beyond remembering which turns it minted, so a caller can
  persist its own bookkeeping (dispatch markers, request ids) between
  prepare and execute.
- ``execute`` validates the prepared turn FIRST — it must be a
  ``PreparedTurn``, produced by THIS session's ``prepare`` (provenance,
  not just origin), carrying a non-empty string request id that the
  underlying request STILL reports — and only then makes ONE provider
  call per execute invocation, with the ORIGINAL request object,
  returning the provider's result unchanged. It retries nothing,
  swallows nothing, invents no status, and does not consume the turn:
  executing the same issued turn again is simply another provider call.

Provenance: ``prepare`` records each turn it mints in a per-session
weak set, and ``execute`` refuses any ``PreparedTurn`` not in it. An
ordinary construction ``PreparedTurn(origin=session, request=...,
request_id=...)`` therefore fails closed; the remaining bypass requires
deliberately writing to the session's private minted-turn set.

What the seam does NOT own, in plain terms: it holds no authority of
any kind — no Mission Authorization, no Git operation, no delivery,
push, tag, release, or deploy, no orchestration, no retry policy, and
no workflow lifecycle. The provider ``session_id`` that flows through
``prepare`` is opaque continuation state only: the seam passes it to
the provider and never interprets, stores, or derives anything from it.
"""

import abc
import weakref
from dataclasses import dataclass


class PreparedTurnError(Exception):
    """The prepared turn cannot be executed; NO provider call was made."""


@dataclass(frozen=True, eq=False)
class PreparedTurn:
    """A built request bound to the session that produced it.

    ``request`` is the existing provider request object held BY
    REFERENCE — the seam neither copies nor re-declares it, so the
    object handed to the provider on execute is the very object the
    build step returned. ``request_id`` is the identity CAPTURED by
    ``prepare`` after validation: a frozen field, not a projection of
    the request, so it is stable across reads by construction.
    Equality is identity (``eq=False``): a turn is the turn ``prepare``
    minted, never a value-equal look-alike.
    """

    origin: object
    request: object
    request_id: str


def _is_request_id(value):
    """A request id is a non-blank ``str``; anything else fails closed."""
    return isinstance(value, str) and str.strip(value) != ""


class OperatorSession(abc.ABC):
    """The prepare/execute boundary; subclasses supply the two hooks."""

    def _minted(self):
        """The weak set of turns this session's ``prepare`` produced.

        Created lazily so subclasses need not chain ``__init__``; weak
        so a turn the caller has dropped costs nothing afterwards.
        """
        minted = self.__dict__.get("_minted_turns")
        if minted is None:
            minted = weakref.WeakSet()
            self.__dict__["_minted_turns"] = minted
        return minted

    def prepare(self, text, repository, session_id=None, source="terminal"):
        """Build the request and bind it to this session. No provider call."""
        request = self._build_request(
            text, repository, session_id=session_id, source=source
        )
        request_id = getattr(request, "request_id", None)
        if not _is_request_id(request_id):
            raise PreparedTurnError(
                "built request carries no non-blank string request id"
                " (%r); the turn was not prepared" % (request_id,)
            )
        prepared = PreparedTurn(
            origin=self, request=request, request_id=request_id
        )
        self._minted().add(prepared)
        return prepared

    def execute(self, prepared):
        """Validate, then make one provider call with the request."""
        if not isinstance(prepared, PreparedTurn):
            raise PreparedTurnError(
                "execute requires a PreparedTurn, got %s; nothing was sent"
                % type(prepared).__name__
            )
        if prepared.origin is not self:
            raise PreparedTurnError(
                "prepared turn was produced by a different session;"
                " nothing was sent"
            )
        if prepared not in self._minted():
            raise PreparedTurnError(
                "prepared turn was not produced by this session's prepare;"
                " nothing was sent"
            )
        if not _is_request_id(prepared.request_id):
            raise PreparedTurnError(
                "prepared turn carries no non-blank string request id"
                " (%r); nothing was sent" % (prepared.request_id,)
            )
        live_id = getattr(prepared.request, "request_id", None)
        if not _is_request_id(live_id):
            raise PreparedTurnError(
                "request no longer carries a non-blank string request id"
                " (%r); nothing was sent" % (live_id,)
            )
        if str.__ne__(live_id, prepared.request_id):
            raise PreparedTurnError(
                "request identity changed after prepare (captured %r, now"
                " %r); nothing was sent" % (prepared.request_id, live_id)
            )
        return self._submit(prepared.request)

    @abc.abstractmethod
    def _build_request(self, text, repository, session_id=None,
                       source="terminal"):
        """Return the provider request object for this turn."""

    @abc.abstractmethod
    def _submit(self, request):
        """Hand the request to the provider and return its result."""


class FunctionOperatorSession(OperatorSession):
    """A session whose hooks delegate to two injected callables.

    The build hook forwards exactly
    ``build_request_fn(text, repository, session_id=..., source=...)``
    and the submit hook forwards exactly ``submit_fn(request)``.
    """

    def __init__(self, build_request_fn, submit_fn):
        self._build_request_fn = build_request_fn
        self._submit_fn = submit_fn

    def _build_request(self, text, repository, session_id=None,
                       source="terminal"):
        return self._build_request_fn(
            text, repository, session_id=session_id, source=source
        )

    def _submit(self, request):
        return self._submit_fn(request)
