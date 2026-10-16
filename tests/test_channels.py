"""Channel adapters: contract conformance, Telegram parsing/formatting, isolation."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

from channels.base import ChannelPayloadError, ConfirmationRequest, MessagingChannel
from channels.fake import FakeChannel
from channels.telegram import TelegramApiError, TelegramChannel
from core.models import Outcome
from tests.conftest import (
    DIGITAL_ARREST,
    PARENT_IDS,
    RAVI_IDS,
    RAVI_NEW_NUMBER,
    STRANGER_ID,
    build_pipeline,
)

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "TEST-TOKEN-not-real"
MINI_APP = "https://kavach.example/app"


def run(coro):
    return asyncio.run(coro)


class RecordingHttp:
    """Injected HTTP client: records calls, never touches the network."""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.response = response or {"ok": True, "result": {}}

    async def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((url, payload))
        return self.response

    def methods(self) -> list[str]:
        return [url.rsplit("/", 1)[-1] for url, _ in self.calls]


def telegram(http: RecordingHttp | None = None) -> TelegramChannel:
    return TelegramChannel(http or RecordingHttp(), TOKEN, mini_app_url=MINI_APP)


# ---- fixture payloads (shape of Telegram Bot API updates; synthetic values) ----
def tg_text_update(text: str, user_id: str = PARENT_IDS["telegram"], contact_phone: str | None = None) -> dict:
    message: dict[str, Any] = {
        "message_id": 11,
        "date": 1792483200,
        "chat": {"id": int(user_id), "type": "private"},
        "from": {"id": int(user_id), "is_bot": False, "first_name": "Test"},
        "text": text,
    }
    if contact_phone:
        message["contact"] = {"phone_number": contact_phone, "first_name": "Unknown"}
    return {"update_id": 1, "message": message}


def tg_photo_update() -> dict:
    return {
        "update_id": 2,
        "message": {
            "message_id": 12,
            "date": 1792483200,
            "from": {"id": int(PARENT_IDS["telegram"])},
            "chat": {"id": int(PARENT_IDS["telegram"]), "type": "private"},
            "photo": [{"file_id": "small"}, {"file_id": "large"}],
            "caption": "Is this real?",
        },
    }


def tg_voice_update() -> dict:
    return {
        "update_id": 3,
        "message": {
            "message_id": 13,
            "from": {"id": int(PARENT_IDS["telegram"])},
            "chat": {"id": int(PARENT_IDS["telegram"]), "type": "private"},
            "voice": {"file_id": "voice-1", "duration": 30},
        },
    }


def tg_callback(data: str, from_id: str) -> dict:
    return {"update_id": 4, "callback_query": {"id": "cb1", "from": {"id": int(from_id)}, "data": data}}


# ---- contract ----------------------------------------------------------------
def test_both_adapters_satisfy_the_protocol():
    assert isinstance(FakeChannel(), MessagingChannel)
    assert isinstance(telegram(), MessagingChannel)


# ---- Telegram parsing ----------------------------------------------------------
def test_telegram_parses_text_with_shared_contact():
    msg = telegram().parse_inbound(tg_text_update("hello", contact_phone="+91 90000 99999"))
    assert (msg.channel, msg.sender_id, msg.kind, msg.text) == ("telegram", PARENT_IDS["telegram"], "text", "hello")
    assert msg.forwarded_sender_id is None  # contact-card numbers are not identity evidence
    assert msg.received_at and msg.received_at.startswith("2026-")


def test_telegram_parses_photo_and_voice():
    photo = telegram().parse_inbound(tg_photo_update())
    assert (photo.kind, photo.media_ref, photo.text) == ("image", "large", "Is this real?")
    voice = telegram().parse_inbound(tg_voice_update())
    assert (voice.kind, voice.media_ref) == ("audio", "voice-1")


def test_telegram_parses_confirmation_callback():
    nonce = "0123456789abcdef0123456789abcdef"
    resp = telegram().parse_confirmation(tg_callback(f"kv:c:00000000000000aa:{nonce}", RAVI_IDS["telegram"]))
    assert (resp.request_id, resp.responder_id, resp.confirmed, resp.nonce) == (
        "00000000000000aa", RAVI_IDS["telegram"], True, nonce)
    assert telegram().parse_confirmation(tg_callback(f"kv:r:00000000000000aa:{nonce}", "1")).confirmed is False


@pytest.mark.parametrize(
    "payload",
    [
        {"update_id": 9},
        {"message": {"text": "no sender"}},
        tg_callback("kv:x:00000000000000aa", "1"),
        tg_callback("kv:c:NOT-HEX", "1"),
        tg_callback("other:c:00000000000000aa", "1"),
        {"callback_query": {"from": {"id": 1}}},
    ],
)
def test_telegram_rejects_malformed_payloads(payload):
    channel = telegram()
    with pytest.raises(ChannelPayloadError):
        if "callback_query" in payload:
            channel.parse_confirmation(payload)
        else:
            channel.parse_inbound(payload)


# ---- Telegram formatting --------------------------------------------------------
def test_confirmation_request_has_yes_no_buttons_within_limits():
    http = RecordingHttp()
    nonce = "0123456789abcdef0123456789abcdef"
    run(telegram(http).send_confirmation_request(ConfirmationRequest(
        "00000000000000ab", "case1", "2002", "Was this you?", nonce)))
    [(url, payload)] = http.calls
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == [
        f"kv:c:00000000000000ab:{nonce}", f"kv:r:00000000000000ab:{nonce}"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)


def test_contact_request_uses_telegram_self_contact_button():
    http = RecordingHttp()
    run(telegram(http).request_contact("2002"))
    [(url, payload)] = http.calls
    assert url.endswith("/sendMessage")
    button = payload["reply_markup"]["keyboard"][0][0]
    assert button == {"text": "Share my number", "request_contact": True}


def test_token_is_never_exposed():
    channel = telegram(RecordingHttp({"ok": False, "description": f"bad token {TOKEN}"}))
    assert TOKEN not in repr(channel)
    with pytest.raises(TelegramApiError) as err:
        run(channel.send_alert(__import__("channels.base", fromlist=["Alert"]).Alert("1", "c", "x")))
    assert TOKEN not in str(err.value)


def test_constructor_requires_injected_token_and_https_mini_app():
    with pytest.raises(ValueError):
        TelegramChannel(RecordingHttp(), "", mini_app_url=MINI_APP)
    with pytest.raises(ValueError):
        TelegramChannel(RecordingHttp(), TOKEN, mini_app_url="http://insecure.example")


# ---- the SAME pipeline on the Telegram adapter ---------------------------------
def test_pipeline_runs_unchanged_on_telegram_adapter():
    http = RecordingHttp()
    pipeline, _, _ = build_pipeline(telegram(http))

    case = run(pipeline.handle_inbound(tg_text_update(DIGITAL_ARREST, contact_phone="+91 90000 99999")))

    assert case.outcome is Outcome.LIKELY_SCAM
    assert http.methods() == ["sendMessage", "sendVoice", "sendMessage", "sendMessage"]
    cb_url, cb_payload = http.calls[0]
    button = cb_payload["reply_markup"]["inline_keyboard"][0][0]
    assert button["web_app"]["url"] == f"{MINI_APP}/pause?case={case.case_id}"
    assert "+91" not in button["web_app"]["url"]  # no phone number in URLs


def test_telegram_stranger_cannot_confirm_but_ravi_can():
    http = RecordingHttp()
    pipeline, _, _ = build_pipeline(telegram(http))
    case = run(pipeline.handle_inbound(tg_text_update(RAVI_NEW_NUMBER, contact_phone="+91 90000 99999")))
    request_payload = next(p for _, p in http.calls if "inline_keyboard" in p.get("reply_markup", {}) and
                           "callback_data" in p["reply_markup"]["inline_keyboard"][0][0])
    assert request_payload["chat_id"] == RAVI_IDS["telegram"]
    yes = request_payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    run(pipeline.handle_confirmation(tg_callback(yes, STRANGER_ID)))
    assert case.outcome is Outcome.PAUSE
    run(pipeline.handle_confirmation(tg_callback(yes, RAVI_IDS["telegram"])))
    assert case.outcome is Outcome.VERIFIED


# ---- isolation: core never imports a concrete adapter --------------------------
CORE_MODULES = sorted((ROOT / "core").glob("*.py"))


@pytest.mark.parametrize("path", CORE_MODULES, ids=lambda p: p.name)
def test_core_does_not_import_concrete_channels(path):
    tree = ast.parse(path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {m for m in imported if m.startswith("channels.") and m != "channels.base"}
    forbidden |= {m for m in imported if "telegram" in m.lower() or "whatsapp" in m.lower()}
    assert not forbidden, f"{path.name} imports {forbidden}"
