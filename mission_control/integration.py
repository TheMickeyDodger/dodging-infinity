"""The hard missing-dependency gate (Task 8, slice S-IV).

Slice S-IV adds the engineering dispatch bootstrap and the effect-boundary
Mission gate; slice S-V adds the guards those two must never run
without: the canonical Mission CONTROL record (hold / cancel, read and
enforced at every gate) and workflow RETENTION enforcement (against
release and pruning). Until BOTH exist in the same candidate, every
production entry that could create or advance a Mission-origin workflow
refuses with ``mission_dependency_missing``, naming the absent guards,
whatever the configuration says, and performs zero effects.

The predicate is DERIVED from the actual presence of the guard
implementations AND, on a configured gate, from their being WIRED on the
instance the gate holds — never from a configuration flag or a constant
a caller could flip:

- control record: the state module defines the control record shape and
  the ``MissionStateOperations`` class defines the four control
  operations, AND the configured Mission service instance exposes the
  control READ (``mission_controls``) and those operations (a stub
  service that lacks the read, or a service class without them, is
  unwired and refuses);
- retention: the workflow record layer defines the retention shape and
  the workflow store layer defines the enforcement predicate.

A static test pins the derivation; only the S-V implementation that adds
those guards can make it true, inside the integrated S-IV + S-V frozen
candidate.
"""

from mission import state as mission_state
from mission import state_service as mission_state_service
from workflow_authority import record as workflow_record
from workflow_authority import store as workflow_store

PROBLEM_DEPENDENCY_MISSING = "mission_dependency_missing"

GUARD_CONTROL_RECORD = "mission_control_record"
GUARD_RETENTION = "workflow_retention_enforcement"
REQUIRED_GUARDS = (GUARD_CONTROL_RECORD, GUARD_RETENTION)

# What each guard's PRESENCE is derived from: the names the S-V
# implementation must define, on the modules that own them, and the
# read + operations the configured service INSTANCE must expose.
CONTROL_RECORD_STATE_NAMES = ("CONTROL_RECORD_KEYS", "CONTROL_KEY")
CONTROL_RECORD_OPERATIONS = ("request_hold", "lift_hold", "request_cancel",
                             "confirm_cancel")
CONTROL_RECORD_READ = "mission_controls"
RETENTION_RECORD_NAMES = ("RETENTION_KEY", "RETENTION_KEYS")
RETENTION_STORE_NAMES = ("retention_protects",)


def control_record_defined():
    """The control record is defined at the class/module level."""
    if not all(getattr(mission_state, name, None) is not None
               for name in CONTROL_RECORD_STATE_NAMES):
        return False
    operations = mission_state_service.MissionStateOperations
    return all(callable(getattr(operations, name, None))
               for name in CONTROL_RECORD_OPERATIONS)


def control_record_wired(service):
    """The control read and every control operation are callable on the
    CONFIGURED service instance (class symbols alone do not count)."""
    if service is None:
        return False
    if not callable(getattr(service, CONTROL_RECORD_READ, None)):
        return False
    return all(callable(getattr(service, name, None))
               for name in CONTROL_RECORD_OPERATIONS)


def retention_defined():
    if not all(getattr(workflow_record, name, None) is not None
               for name in RETENTION_RECORD_NAMES):
        return False
    return all(callable(getattr(workflow_store, name, None))
               for name in RETENTION_STORE_NAMES)


def missing_guards(service=None):
    """The absent S-V guards, in the fixed order of ``REQUIRED_GUARDS``;
    empty exactly when every guard implementation is present and — when
    ``service`` (the configured Mission service instance) is given —
    wired on it. Without a service the class-level derivation alone
    answers, which is the bootstrap-free static view; every configured
    gate passes its instance."""
    absent = []
    if not control_record_defined() or (
        service is not None and not control_record_wired(service)
    ):
        absent.append(GUARD_CONTROL_RECORD)
    if not retention_defined():
        absent.append(GUARD_RETENTION)
    return tuple(absent)


def required_guards_present(service=None):
    """True only when the canonical control record and retention
    enforcement both exist in this candidate (and, with ``service``,
    are wired on that instance)."""
    return not missing_guards(service)


def dependency_refusal(missing):
    """The uniform refusal every gated entry returns while the predicate
    is false: the problem code and a detail naming the absent guards."""
    return {
        "ok": False,
        "problem": PROBLEM_DEPENDENCY_MISSING,
        "detail": ("the integrated Mission engineering path is not enabled in"
                   " this build: missing %s" % ", ".join(missing)),
        "missing_guards": list(missing),
    }
