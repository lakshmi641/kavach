"""Channel-agnostic messaging contract for Kavach.

The core (policy, trusted-contact vault, pipeline, audit trail) depends only on
the types in this module. Channel adapters (Telegram today, WhatsApp later)
translate between their own payloads and these types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional, Protocol, runtime_checkable

MessageKind = Literal["text", "image", "audio", "video", "other"]


class ChannelPayloadError(ValueError):
    """Raised when an adapter receives a payload it cannot safely parse."""


@dataclass(frozen=True)
class InboundMessage:
    """Something the protected parent forwarded to Kavach."""

    channel: str
    sender_id: str
    kind: MessageKind
    text: str = ""
    media_ref: Optional[str] = None
    # Telegram's authenticated forwarding metadata, when the sender did not hide it.
    forwarded_sender_id: Optional[str] = None
    received_at: Optional[str] = None


@dataclass(frozen=True)
class ConfirmationRequest:
    """Out-of-band question to a saved trusted contact: "Was this you?"."""

    request_id: str
    case_id: str
    recipient_id: str
    prompt: str
    nonce: str = ""


@dataclass(frozen=True)
class ConfirmationResponse:
    """A trusted contact's answer, as parsed by the channel adapter."""

    channel: str
    request_id: str
    responder_id: str
    confirmed: bool
    nonce: str = ""


@dataclass(frozen=True)
class OutboundReply:
    recipient_id: str
    case_id: str
    text: str
    language: str


@dataclass(frozen=True)
class Alert:
    recipient_id: str
    case_id: str
    text: str


@dataclass(frozen=True)
class CircuitBreakerEvent:
    """Instruction to show the full-screen "Pause. Do not pay." experience."""

    recipient_id: str
    case_id: str
    headline: str
    # Saved trusted number for the one-tap call (rendered by the Mini App).
    call_number: Optional[str] = None
    # Reference to the parent-approved human recording. Never AI-generated.
    recorded_message_ref: Optional[str] = None


@runtime_checkable
class MessagingChannel(Protocol):
    """Everything the core needs from a messaging channel.

    Parsing is synchronous and pure; sending is async so adapters can do I/O.
    """

    name: str

    def parse_inbound(self, raw: Mapping[str, Any]) -> InboundMessage: ...

    def parse_confirmation(self, raw: Mapping[str, Any]) -> ConfirmationResponse: ...

    async def send_reply(self, reply: OutboundReply) -> None: ...

    async def send_confirmation_request(self, request: ConfirmationRequest) -> None: ...

    async def send_alert(self, alert: Alert) -> None: ...

    async def open_circuit_breaker(self, event: CircuitBreakerEvent) -> None: ...
