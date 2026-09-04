"""Capability seam: a substrate-neutral boundary for one-shot authority.

This module is SUBSTRATE-FREE. It imports nothing from any substrate:
no token store, no store of units of work, no configuration loader,
no transport, no provider, and no orchestration machinery; it is
standard-library only (``abc``). A substrate package subclasses
``CapabilityAuthority`` and is the only place that knows how the
three calls below are actually carried out. This module does not name
that package: the dependency runs one way, from the implementation to
this contract, never back.

The seam carries exactly three calls. An implementation binds its own
inputs (for example the directory of a durable token store) when it
is constructed; every call takes the caller's clock value ``now`` as
an argument and returns the substrate's own result UNCHANGED.

- ``mint(workflow_id, action, revision, now)``: issue one token bound
  to exactly ``(workflow_id, action, revision)``, valid from ``now``
  for the substrate's own window; returns the token.
- ``validate_and_consume(token, workflow_id, action, revision,
  now)``: check one presented token against exactly that binding and
  spend it once; returns ``(ok, problem, detail)``, where ``problem``
  is one of the refusal codes below or ``None``. A refusal spends
  nothing and alters no other token.
- ``compact(now, non_actionable, oracle_errors)``: retire tokens that
  are spent, expired at ``now``, or proven non-actionable by the
  caller's ``non_actionable`` oracle (which must return exactly
  ``True`` to retire one; an oracle failure is appended to
  ``oracle_errors`` and keeps the token); returns the retired tokens.

The refusal codes and ``CapabilityError`` live here because the
return vocabulary is part of the seam: a caller that reads a
``problem`` value or catches the error reads them from this module,
whichever implementation produced them. ``CapabilityError`` means the
implementation's own store is unusable or full; a refusal is never an
exception.

What the seam does NOT own, in plain terms: it grants no authority of
its own (a token is minted only because the caller already decided
the action is authorized, and presenting one proves nothing beyond
that earlier decision); it decides no policy; it holds no lock (the
caller serializes the calls); it reads no clock (``now`` is always
passed in); it knows nothing about what a unit of work is, which
actions exist, or what a revision means beyond equality; it publishes
nothing and moves nothing; it never carries a token anywhere but its
own return value: not into a message, a record, a receipt, a log, or
a status line; it retries nothing, loops over nothing, loads no
configuration, wraps no error, and keeps no state of its own; and it
has no relationship with any other seam in the tree.

The vocabulary here is generic on purpose: a product, provider,
sibling-seam, or delivery name in this package would turn the neutral
contract into a statement about one particular substrate. The static
suite scans every token of this package, docstrings and comments
included, so such a name cannot return unnoticed. ``workflow_id`` is
the one product-shaped name kept, because it is the binding field
every implementation is keyed on.
"""

import abc

# The refusal vocabulary. Values are part of the seam: a caller
# compares ``problem`` against these names, never against a literal.
PROBLEM_CAPABILITY_MISSING = "capability_missing"
PROBLEM_CAPABILITY_UNKNOWN = "capability_unknown"
PROBLEM_CAPABILITY_CONSUMED = "capability_already_consumed"
PROBLEM_CAPABILITY_EXPIRED = "capability_expired"
PROBLEM_CAPABILITY_MISMATCH = "capability_binding_mismatch"
PROBLEM_CAPABILITY_STORE = "capability_store_unreadable"
PROBLEM_CAPABILITY_STORE_FULL = "capability_store_full"


class CapabilityError(Exception):
    """The implementation's token store is unusable or full; the
    message is actionable. Raised by ``mint`` and ``compact``; never
    raised for a refused presentation."""


class CapabilityAuthority(abc.ABC):
    """The substrate-neutral one-shot capability boundary.

    Every method makes exactly ONE substrate call with the inputs
    bound at construction plus the arguments given, returns the
    substrate's result unchanged, retries nothing, and swallows
    nothing.
    """

    @abc.abstractmethod
    def mint(self, workflow_id, action, revision, now):
        """Issue one token bound to exactly ``(workflow_id, action,
        revision)`` at ``now``; returns the substrate's own token
        unchanged, or raises ``CapabilityError``."""

    @abc.abstractmethod
    def validate_and_consume(self, token, workflow_id, action, revision,
                             now):
        """Check one presented token against exactly that binding at
        ``now`` and spend it once; returns the substrate's own
        ``(ok, problem, detail)`` unchanged."""

    @abc.abstractmethod
    def compact(self, now, non_actionable, oracle_errors):
        """Retire spent, expired, or provably non-actionable tokens at
        ``now``; returns the substrate's own list of retired tokens
        unchanged, or raises ``CapabilityError``."""
