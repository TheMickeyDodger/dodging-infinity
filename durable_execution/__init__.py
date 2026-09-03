"""Durable-execution seam: a substrate-neutral boundary for one pass of work.

``DurableExecution`` lives in ``durable_execution.contract`` and is
substrate-free (standard library only). A substrate package supplies
the only implementation, and that implementation lives with its
substrate, not here; this package does not name it, so the dependency
runs one way, from the implementation to the contract. The seam owns
no authority, no lock, no loop, and no state — see the
``durable_execution.contract`` docstring.
"""

from durable_execution.contract import DurableExecution

__all__ = ["DurableExecution"]
