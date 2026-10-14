"""Register the configured Telegram webhook without printing its bot token."""

from __future__ import annotations

import asyncio
import os
import sys

import httpx


async def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if not token or not secret or not base.startswith("https://"):
        print("Set TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, and an HTTPS PUBLIC_BASE_URL.", file=sys.stderr)
        return 2
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": base + "/tg/webhook", "secret_token": secret,
                  "allowed_updates": ["message", "callback_query"], "drop_pending_updates": False},
        )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        print("Telegram rejected the webhook registration.", file=sys.stderr)
        return 1
    print("Telegram webhook registered at the configured Kavach endpoint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
