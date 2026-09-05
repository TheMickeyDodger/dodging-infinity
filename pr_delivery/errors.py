"""The transport error type, kept in a process-free module.

``machine.py`` must be able to name the transport's failure class
without importing the transport module itself: the static suite proves
that importing the machine, the receipts, or the boundary alone never
loads ``pr_delivery.transport`` (and therefore never loads
``subprocess``), which is what makes a test fake structurally unable to
reach GitHub. ``transport.py`` re-exports this class.
"""


class DeliveryTransportError(Exception):
    """A transport call failed or returned unusable output."""
