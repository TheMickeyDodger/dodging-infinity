"""Worker seam: a substrate-neutral boundary for host-bound work.

``Worker`` lives in ``worker.contract`` and is substrate-free
(standard library only). A substrate package supplies the only
implementation, and that implementation lives with its substrate, not
here; this package does not name it, so the dependency runs one way,
from the implementation to the contract. The seam owns no authority,
no proof, no identity, no clock, and no state; and it defines no
refusal vocabulary of its own, because every call returns the
substrate's own result unchanged, see the ``worker.contract``
docstring.
"""

from worker.contract import Worker

__all__ = ["Worker"]
