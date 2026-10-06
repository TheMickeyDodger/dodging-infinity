"""Dodging Infinity Mission control: the integration layer between the
neutral Mission Core and the conversational surface (Task 8).

This package is transport-side and Herdr-free: it imports the standard
library and the neutral ``mission`` package, and never the
orchestration engine, the Runtime, the delivery package, or any
process-spawning or network module. It writes no durable state of its
own in this slice. Nothing here mints, broadens or transfers authority;
the one function it holds today, the consequential-decision provenance
predicate, only reads what the Mission Core recorded and says whether
that record is sufficient for a consequential effect.
"""

from mission_control.authority import (
    CONSEQUENTIAL_PRINCIPAL_KINDS,
    PROBLEM_PROVENANCE_INSUFFICIENT,
    consequential_decision_provenance,
)

__all__ = [
    "CONSEQUENTIAL_PRINCIPAL_KINDS",
    "PROBLEM_PROVENANCE_INSUFFICIENT",
    "consequential_decision_provenance",
]
