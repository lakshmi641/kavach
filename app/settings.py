"""Environment-backed settings; provider secrets are optional in local demo mode."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "./data/kavach.sqlite3"))
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_webhook_secret: str = field(default_factory=lambda: os.getenv("TELEGRAM_WEBHOOK_SECRET", ""))
    parent_telegram_id: str = field(default_factory=lambda: os.getenv("PARENT_TELEGRAM_ID", ""))
    telegram_bot_username: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_USERNAME", ""))
    mini_app_url: str = field(default_factory=lambda: os.getenv("MINI_APP_URL", ""))
    nebius_api_key: str = field(default_factory=lambda: os.getenv("NEBIUS_API_KEY", ""))
    nebius_base_url: str = field(default_factory=lambda: os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1"))
    model_nano: str = field(default_factory=lambda: os.getenv("MODEL_NANO", ""))
    model_super: str = field(default_factory=lambda: os.getenv("MODEL_SUPER", ""))
    model_ultra: str = field(default_factory=lambda: os.getenv("MODEL_ULTRA", ""))
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    asr_base_url: str = field(default_factory=lambda: os.getenv("ASR_BASE_URL", ""))
    asr_api_key: str = field(default_factory=lambda: os.getenv("ASR_API_KEY", ""))
    asr_model: str = field(default_factory=lambda: os.getenv("ASR_MODEL", "whisper-1"))
    audit_hmac_key: str = field(default_factory=lambda: os.getenv("AUDIT_HMAC_KEY", ""))
    environment: str = field(default_factory=lambda: os.getenv("KAVACH_ENV", "demo"))
    session_secret: str = field(default_factory=lambda: os.getenv("SESSION_SECRET", "local-demo-session-secret-change-me"))
    model_input_cost_per_million_inr: float = field(default_factory=lambda: float(os.getenv("MODEL_INPUT_COST_PER_MILLION_INR", "0")))
    model_output_cost_per_million_inr: float = field(default_factory=lambda: float(os.getenv("MODEL_OUTPUT_COST_PER_MILLION_INR", "0")))

    @property
    def live_models_enabled(self) -> bool:
        return bool(self.nebius_api_key and self.model_nano)

    @property
    def live_tavily_enabled(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_webhook_secret)

    def validate_live(self) -> None:
        missing = [key for key, value in {
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "TELEGRAM_WEBHOOK_SECRET": self.telegram_webhook_secret,
            "PARENT_TELEGRAM_ID": self.parent_telegram_id,
            "AUDIT_HMAC_KEY": self.audit_hmac_key,
            "MINI_APP_URL": self.mini_app_url,
            "NEBIUS_API_KEY": self.nebius_api_key,
            "MODEL_NANO": self.model_nano,
            "MODEL_ULTRA": self.model_ultra,
            "MODEL_SUPER": self.model_super,
            "TAVILY_API_KEY": self.tavily_api_key,
            "ASR_BASE_URL": self.asr_base_url,
            "ASR_API_KEY": self.asr_api_key,
        }.items() if not value]
        if self.environment == "production" and missing:
            raise ValueError("Missing production settings: " + ", ".join(missing))
        if self.environment == "production" and len(self.audit_hmac_key.encode()) < 16:
            raise ValueError("AUDIT_HMAC_KEY must be at least 16 bytes in production")
        if self.environment == "production" and len(self.session_secret.encode()) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 bytes in production")
        if self.environment == "production" and not self.mini_app_url.startswith("https://"):
            raise ValueError("MINI_APP_URL must use HTTPS in production")
        if self.environment == "production" and not self.nebius_base_url.startswith("https://"):
            raise ValueError("NEBIUS_BASE_URL must use HTTPS in production")
        if self.environment == "production" and not self.asr_base_url.startswith("https://"):
            raise ValueError("ASR_BASE_URL must use HTTPS in production")
        if self.environment == "production" and (not self.parent_telegram_id.isdigit() or int(self.parent_telegram_id) <= 0):
            raise ValueError("PARENT_TELEGRAM_ID must be the positive numeric Telegram account ID")
        if self.environment == "production" and not all(
            char.isalnum() or char in "_-" for char in self.telegram_webhook_secret
        ):
            raise ValueError("TELEGRAM_WEBHOOK_SECRET may contain only letters, numbers, _ and -")
