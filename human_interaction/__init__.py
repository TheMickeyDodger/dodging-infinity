"""Human-interaction seam: a provider-neutral transport boundary.

``HumanInteractionAdapter`` and its outcome dataclasses live in
``human_interaction.contract`` and are provider-free (stdlib only). The
only provider-backed implementation is
``telegram_operator.interaction.TelegramHumanInteractionAdapter``,
which lives with the provider, not here. The seam owns no authority
and no durable state — see the ``human_interaction.contract``
docstring.
"""

from human_interaction.contract import (
    EVENT_ACTION,
    EVENT_KINDS,
    EVENT_MESSAGE,
    SEND_APPLIED,
    SEND_CLASSIFICATIONS,
    SEND_DEFINITE_ZERO,
    SEND_INDEFINITE,
    Control,
    EditOutcome,
    HumanInteractionAdapter,
    InteractionEvent,
    ReceiveOutcome,
    SendOnceOutcome,
    SendOutcome,
)

__all__ = [
    "EVENT_ACTION",
    "EVENT_KINDS",
    "EVENT_MESSAGE",
    "SEND_APPLIED",
    "SEND_CLASSIFICATIONS",
    "SEND_DEFINITE_ZERO",
    "SEND_INDEFINITE",
    "Control",
    "EditOutcome",
    "HumanInteractionAdapter",
    "InteractionEvent",
    "ReceiveOutcome",
    "SendOnceOutcome",
    "SendOutcome",
]
