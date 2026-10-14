"""Local media preprocessing plus an optional OpenAI-compatible ASR endpoint.

Only extracted, redacted text is passed to Nano and Tavily. Temporary video audio
is created under the OS temp directory and removed when processing finishes.
"""

from __future__ import annotations

import asyncio
import io
import subprocess
import tempfile
from pathlib import Path

import httpx

from app.settings import Settings
from core.redaction import redact


class MediaUnavailable(RuntimeError):
    pass


class MediaProcessor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze(self, content: bytes, kind: str, content_type: str = "") -> str:
        if kind == "image":
            return await asyncio.to_thread(self._ocr, content)
        if kind == "audio":
            transcript = await self._transcribe(content, content_type or "audio/ogg", "recording")
            return redact(transcript).text
        if kind == "video":
            audio = await asyncio.to_thread(self._extract_audio, content)
            transcript = await self._transcribe(audio, "audio/wav", "video-audio")
            return redact(transcript).text
        raise MediaUnavailable("Unsupported media type")

    @staticmethod
    def _ocr(content: bytes) -> str:
        try:
            from PIL import Image
            import pytesseract
            image = Image.open(io.BytesIO(content)).convert("RGB")
            text = pytesseract.image_to_string(image, timeout=12)
        except ImportError as exc:
            raise MediaUnavailable("Install Pillow, pytesseract, and the Tesseract OCR binary to inspect images.") from exc
        except Exception as exc:
            raise MediaUnavailable("Image OCR did not complete safely.") from exc
        return redact(text).text

    @staticmethod
    def _extract_audio(content: bytes) -> bytes:
        try:
            with tempfile.TemporaryDirectory(prefix="kavach-media-") as temp_dir:
                source = Path(temp_dir) / "input-video"
                source.write_bytes(content)
                result = subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-vn", "-ac", "1",
                     "-ar", "16000", "-f", "wav", "pipe:1"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
                )
                if result.returncode != 0 or not result.stdout:
                    raise MediaUnavailable("Could not extract audio from the video.")
                return result.stdout
        except FileNotFoundError as exc:
            raise MediaUnavailable("Install ffmpeg to inspect video audio.") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaUnavailable("Video audio extraction timed out.") from exc

    async def _transcribe(self, content: bytes, content_type: str, filename: str) -> str:
        base_url = self.settings.asr_base_url
        if not self.settings.asr_api_key or not base_url:
            raise MediaUnavailable("Configure ASR_BASE_URL and ASR_API_KEY to transcribe voice or video.")
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                base_url.rstrip("/") + "/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.settings.asr_api_key}"},
                files={"file": (filename, content, content_type)},
                data={"model": self.settings.asr_model, "response_format": "json"},
            )
            response.raise_for_status()
            text = response.json().get("text")
        if not isinstance(text, str) or not text.strip():
            raise MediaUnavailable("The transcription service returned no transcript.")
        return text[:12_000]
