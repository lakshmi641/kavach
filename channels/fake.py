"""In-memory channel for tests: records everything the core sends."""

from __future__ import annotations

from typing import Any, Mapping

from channels.base import (
    Alert,
    ChannelPayloadError,
    CircuitBreakerEvent,
    ConfirmationRequest,
    ConfirmationResponse,
    InboundMessage,
    OutboundReply,
)

_KINDS = {"text", "image", "audio", "video", "other"}


class FakeChannel:
    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.replies: list[OutboundReply] = []
        self.alerts: list[Alert] = []
        self.confirmation_requests: list[ConfirmationRequest] = []
        self.circuit_breakers: list[CircuitBreakerEvent] = []

    # -- parsing -----------------------------------------------------------
    def parse_inbound(self, raw: Mapping[str, Any]) -> InboundMessage:
        try:
            sender_id = str(raw["sender_id"])
        except KeyError as exc:
            raise ChannelPayloadError("fake inbound payload needs sender_id") from exc
        kind = raw.get("kind", "text")
        if kind not in _KINDS:
            raise ChannelPayloadError(f"unsupported kind: {kind!r}")
        return InboundMessage(
            channel=self.name,
            sender_id=sender_id,
            kind=kind,
            text=str(raw.get("text", "")),
            media_ref=raw.get("media_ref"),
            forwarded_sender_id=raw.get("forwarded_sender_id"),
            received_at=raw.get("received_at"),
        )

    def parse_confirmation(self, raw: Mapping[str, Any]) -> ConfirmationResponse:
        try:
            return ConfirmationResponse(
                channel=self.name,
                request_id=str(raw["request_id"]),
                responder_id=str(raw["responder_id"]),
                confirmed=bool(raw["confirmed"]),
                nonce=str(raw.get("nonce", "")),
            )
        except KeyError as exc:
            raise ChannelPayloadError("fake confirmation payload is incomplete") from exc

    # -- sending -----------------------------------------------------------
    async def send_reply(self, reply: OutboundReply) -> None:
        self.replies.append(reply)

    async def send_confirmation_request(self, request: ConfirmationRequest) -> None:
        self.confirmation_requests.append(request)

    async def send_alert(self, alert: Alert) -> None:
        self.alerts.append(alert)

    async def open_circuit_breaker(self, event: CircuitBreakerEvent) -> None:
        self.circuit_breakers.append(event)

    # -- test helpers ------------------------------------------------------
    def all_outbound_text(self) -> list[str]:
        texts = [r.text for r in self.replies]
        texts += [a.text for a in self.alerts]
        texts += [c.prompt for c in self.confirmation_requests]
        texts += [e.headline for e in self.circuit_breakers]
        return texts
