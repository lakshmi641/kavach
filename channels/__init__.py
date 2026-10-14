"""Messaging channel adapters.

Only ``channels.base`` may be imported by the core. Concrete adapters
(``channels.telegram``, ``channels.fake``) are wired in at the application edge.
"""

from channels.base import (
    Alert,
    ChannelPayloadError,
    CircuitBreakerEvent,
    ConfirmationRequest,
    ConfirmationResponse,
    InboundMessage,
    MessagingChannel,
    OutboundReply,
)

__all__ = [
    "Alert",
    "ChannelPayloadError",
    "CircuitBreakerEvent",
    "ConfirmationRequest",
    "ConfirmationResponse",
    "InboundMessage",
    "MessagingChannel",
    "OutboundReply",
]
