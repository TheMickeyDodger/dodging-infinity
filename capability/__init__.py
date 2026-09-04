"""Capability seam: a substrate-neutral boundary for one-shot authority.

``CapabilityAuthority`` lives in ``capability.contract`` and is
substrate-free (standard library only). A substrate package supplies
the only implementation, and that implementation lives with its
substrate, not here; this package does not name it, so the dependency
runs one way, from the implementation to the contract. The refusal
codes and ``CapabilityError`` are re-exported alongside the contract
because they are the seam's return vocabulary. The seam owns no
authority, no policy, no lock, no clock, and no state — see the
``capability.contract`` docstring.
"""

from capability.contract import (
    PROBLEM_CAPABILITY_CONSUMED,
    PROBLEM_CAPABILITY_EXPIRED,
    PROBLEM_CAPABILITY_MISMATCH,
    PROBLEM_CAPABILITY_MISSING,
    PROBLEM_CAPABILITY_STORE,
    PROBLEM_CAPABILITY_STORE_FULL,
    PROBLEM_CAPABILITY_UNKNOWN,
    CapabilityAuthority,
    CapabilityError,
)

__all__ = [
    "CapabilityAuthority",
    "CapabilityError",
    "PROBLEM_CAPABILITY_MISSING",
    "PROBLEM_CAPABILITY_UNKNOWN",
    "PROBLEM_CAPABILITY_CONSUMED",
    "PROBLEM_CAPABILITY_EXPIRED",
    "PROBLEM_CAPABILITY_MISMATCH",
    "PROBLEM_CAPABILITY_STORE",
    "PROBLEM_CAPABILITY_STORE_FULL",
]
