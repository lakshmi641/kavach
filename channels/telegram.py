"""Telegram adapter.

All Telegram-specific payload parsing and outgoing message formatting lives
here and nowhere else. Network I/O goes through an injected ``HttpClient`` so
the adapter can be tested with fixture payloads and no real network calls.
No tokens, webhook setup or deployment code live in this module.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Protocol

from channels.base import (
    Alert,
    ChannelPayloadError,
    CircuitBreakerEvent,
    ConfirmationRequest,
    ConfirmationResponse,
    InboundMessage,
    MessageKind,
    OutboundReply,
)

CALLBACK_PREFIX = "kv"
_CONFIRM = "c"
_REJECT = "r"
_REQUEST_ID_RE = re.compile(r"^[a-f0-9]{8,32}$")
_NONCE_RE = re.compile(r"^[a-f0-9]{16,64}$")
_MAX_CALLBACK_BYTES = 64  # Telegram limit for callback_data


class HttpClient(Protocol):
    async def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class TelegramApiError(RuntimeError):
    """Telegram returned ok=false. Never includes the bot token."""


class TelegramChannel:
    name = "telegram"

    def __init__(
        self,
        http: HttpClient,
        bot_token: str,
        *,
        mini_app_url: str,
        api_base: str = "https://api.telegram.org",
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token must be provided by the caller (never hard-coded)")
        if not mini_app_url.startswith("https://"):
            raise ValueError("mini_app_url must be an https URL")
        self._http = http
        self._token = bot_token
        self._mini_app_url = mini_app_url.rstrip("/")
        self._api_base = api_base.rstrip("/")

    def __repr__(self) -> str:  # keep the token out of logs and tracebacks
        return f"TelegramChannel(api_base={self._api_base!r}, bot_token='***')"

    # -- parsing -----------------------------------------------------------
    def parse_inbound(self, raw: Mapping[str, Any]) -> InboundMessage:
        msg = raw.get("message")
        if not isinstance(msg, Mapping):
            raise ChannelPayloadError("update has no message")
        sender = msg.get("from")
        if not isinstance(sender, Mapping) or "id" not in sender:
            raise ChannelPayloadError("message has no sender")

        text = msg.get("text") or msg.get("caption") or ""
        kind: MessageKind
        media_ref: Optional[str] = None
        if msg.get("photo"):
            kind, media_ref = "image", msg["photo"][-1].get("file_id")
        elif msg.get("voice") or msg.get("audio"):
            kind = "audio"
            media_ref = (msg.get("voice") or msg.get("audio")).get("file_id")
        elif msg.get("video") or msg.get("video_note"):
            kind = "video"
            media_ref = (msg.get("video") or msg.get("video_note")).get("file_id")
        elif text:
            kind = "text"
        else:
            kind = "other"

        forwarded_id = None
        origin = msg.get("forward_origin")
        if isinstance(origin, Mapping):
            sender_user = origin.get("sender_user")
            if origin.get("type") == "user" and isinstance(sender_user, Mapping) and "id" in sender_user:
                forwarded_id = str(sender_user["id"])
        elif isinstance(msg.get("forward_from"), Mapping) and "id" in msg["forward_from"]:
            forwarded_id = str(msg["forward_from"]["id"])

        received_at = None
        if isinstance(msg.get("date"), int):
            received_at = datetime.fromtimestamp(msg["date"], tz=timezone.utc).isoformat()

        return InboundMessage(
            channel=self.name,
            sender_id=str(sender["id"]),
            kind=kind,
            text=str(text),
            media_ref=media_ref,
            forwarded_sender_id=forwarded_id,
            received_at=received_at,
        )

    def parse_confirmation(self, raw: Mapping[str, Any]) -> ConfirmationResponse:
        cq = raw.get("callback_query")
        if not isinstance(cq, Mapping):
            raise ChannelPayloadError("update has no callback_query")
        sender = cq.get("from")
        if not isinstance(sender, Mapping) or "id" not in sender:
            raise ChannelPayloadError("callback_query has no sender")
        data = cq.get("data")
        if not isinstance(data, str):
            raise ChannelPayloadError("callback_query has no data")
        parts = data.split(":")
        if (len(parts) != 4 or parts[0] != CALLBACK_PREFIX
            or parts[1] not in (_CONFIRM, _REJECT)
            or not _REQUEST_ID_RE.fullmatch(parts[2])
            or not _NONCE_RE.fullmatch(parts[3])):
            raise ChannelPayloadError("unrecognised callback data")
        return ConfirmationResponse(
            channel=self.name,
            request_id=parts[2],
            responder_id=str(sender["id"]),
            confirmed=parts[1] == _CONFIRM,
            nonce=parts[3],
        )

    # -- sending -----------------------------------------------------------
    async def send_reply(self, reply: OutboundReply) -> None:
        await self._call("sendMessage", {"chat_id": reply.recipient_id, "text": reply.text})

    async def send_confirmation_request(self, request: ConfirmationRequest) -> None:
        yes = f"{CALLBACK_PREFIX}:{_CONFIRM}:{request.request_id}:{request.nonce}"
        no = f"{CALLBACK_PREFIX}:{_REJECT}:{request.request_id}:{request.nonce}"
        for data in (yes, no):
            if len(data.encode()) > _MAX_CALLBACK_BYTES:
                raise ValueError("callback data exceeds Telegram's 64-byte limit")
        await self._call(
            "sendMessage",
            {
                "chat_id": request.recipient_id,
                "text": request.prompt,
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "Yes, it was me", "callback_data": yes},
                            {"text": "No, not me", "callback_data": no},
                        ]
                    ]
                },
            },
        )

    async def send_alert(self, alert: Alert) -> None:
        await self._call("sendMessage", {"chat_id": alert.recipient_id, "text": alert.text})

    async def open_circuit_breaker(self, event: CircuitBreakerEvent) -> None:
        # Telegram buttons cannot dial numbers, so the Mini App page renders the
        # one-tap call. The URL carries only the case id, never a phone number.
        await self._call(
            "sendMessage",
            {
                "chat_id": event.recipient_id,
                "text": event.headline,
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Open Kavach",
                                "web_app": {"url": f"{self._mini_app_url}/pause?case={event.case_id}"},
                            }
                        ]
                    ]
                },
            },
        )
        if event.recorded_message_ref:
            await self._call(
                "sendVoice",
                {"chat_id": event.recipient_id, "voice": event.recorded_message_ref},
            )

    # -- internals ---------------------------------------------------------
    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._api_base}/bot{self._token}/{method}"
        response = await self._http.post_json(url, payload)
        if not response.get("ok", False):
            description = str(response.get("description", "unknown error")).replace(self._token, "***")
            raise TelegramApiError(f"Telegram {method} failed: {description}")
        return response
