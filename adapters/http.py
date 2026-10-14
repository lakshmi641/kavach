"""Async HTTP client for Telegram Bot API operations."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx


class TelegramHttpClient:
    def __init__(self, *, timeout: float = 15.0, retries: int = 1) -> None:
        self.timeout = timeout
        self.retries = retries

    async def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.retries + 1):
                try:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise RuntimeError("Telegram returned a non-object response")
                    return body
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt >= self.retries:
                        raise
                    await asyncio.sleep(0.15 * (attempt + 1))
        raise RuntimeError("Telegram request failed")

    async def download_file(self, token: str, file_id: str) -> tuple[bytes, str]:
        metadata = await self.post_json(f"https://api.telegram.org/bot{token}/getFile", {"file_id": file_id})
        path = metadata.get("result", {}).get("file_path")
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/"):
            raise RuntimeError("Telegram returned an invalid file path")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"https://api.telegram.org/file/bot{token}/{quote(path, safe='/')}")
            response.raise_for_status()
            if len(response.content) > 20 * 1024 * 1024:
                raise ValueError("Telegram file exceeds the 20 MB download limit")
            return response.content, response.headers.get("content-type", "audio/ogg")
