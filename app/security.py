"""Telegram webhook and Mini App authentication helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(init_data: str, bot_token: str, *, max_age_seconds: int = 86_400) -> dict:
    if not init_data or not bot_token:
        raise ValueError("missing initData or bot token")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate initData field")
        values[key] = value
    received_hash = values.pop("hash", "")
    values.pop("signature", None)
    if len(received_hash) != 64:
        raise ValueError("missing initData hash")
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    # Telegram Mini Apps: derive the secret with WebAppData as the HMAC key.
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, expected):
        raise ValueError("invalid initData signature")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        user_id = str(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid initData payload") from exc
    now = int(time.time())
    if auth_date > now + 30 or now - auth_date > max_age_seconds:
        raise ValueError("expired initData")
    return {"user": user, "user_id": user_id, "auth_date": auth_date}


def session_cookie(actor_id: str, secret: str, *, issued_at: int | None = None) -> str:
    issued = issued_at or int(time.time())
    payload = f"{actor_id}:{issued}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def validate_session(value: str, secret: str, *, max_age_seconds: int = 3600) -> str | None:
    try:
        actor, issued_raw, signature = value.rsplit(":", 2)
        issued = int(issued_raw)
    except (ValueError, AttributeError):
        return None
    payload = f"{actor}:{issued}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    now = int(time.time())
    if issued > now + 30 or now - issued > max_age_seconds:
        return None
    return actor
