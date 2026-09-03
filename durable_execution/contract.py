"""Durable-execution seam: a substrate-neutral boundary for one pass of work.

This module is SUBSTRATE-FREE. It imports nothing from any substrate:
no work store, no configuration loader, no transport, no provider, and
no orchestration machinery; it is standard-library only (``abc``). A
substrate package subclasses ``DurableExecution`` and is the only place
that knows how the three calls below are actually carried out. This
module does not name that package: the dependency runs one way, from
the implementation to this contract, never back.

The seam carries exactly three calls, in the order the caller makes
them after construction. None takes an argument: an implementation
binds its own inputs (for example a work-store handle and a state
directory) when it is constructed, and each call returns the
substrate's own result UNCHANGED.

- ``recover_inherited_processes()``: recover what a previous instance
  of the substrate left behind, before any work is advanced.
- ``readiness_attention()``: enumerate the work whose recorded
  readiness needs attention, together with the substrate's denominator.
- ``process_once()``: one pass over every claimable unit of work.

What the seam does NOT own, in plain terms: it holds no authority of
any kind. It does not create, validate, or consume an authorization or
an approval of any sort; it has no delivery authority, so it publishes
nothing and moves nothing out of the working tree; it decides no
policy, produces no evidence, and performs no review; it drives no
orchestration engine and reasons about nothing on anyone's behalf; it
routes nothing and schedules nothing; it has no lifecycle vocabulary
beyond the three calls (no start, enqueue, cancel, resume, checkpoint,
or inspect); it retries nothing, loops over nothing, holds no lock,
loads no configuration, wraps no error, and keeps no state of its own;
and it has no relationship with any other seam in the tree. Everything
the caller does around these calls (argument parsing, the
single-instance lock, pacing, exit codes, printing) stays with the
caller.

The vocabulary here is generic on purpose: a product, provider,
sibling-seam, or delivery name in this package would turn the neutral
contract into a statement about one particular substrate. The static
suite scans every token of this package, docstrings and comments
included, so such a name cannot return unnoticed.
"""

import abc


class DurableExecution(abc.ABC):
    """The substrate-neutral durable-execution boundary.

    Every method makes exactly ONE substrate call with the inputs bound
    at construction, returns the substrate's result unchanged, retries
    nothing, and swallows nothing.
    """

    @abc.abstractmethod
    def recover_inherited_processes(self):
        """Recover what a previous instance left behind; returns the
        substrate's own report unchanged."""

    @abc.abstractmethod
    def readiness_attention(self):
        """Enumerate work needing readiness attention; returns the
        substrate's own (rows, denominator) unchanged."""

    @abc.abstractmethod
    def process_once(self):
        """One pass over every claimable unit of work; returns the
        substrate's own per-unit outcomes unchanged."""
