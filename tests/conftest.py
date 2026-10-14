"""Shared synthetic fixtures. Every name, number and id here is invented."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audit import AuditLog  # noqa: E402
from core.models import RiskSignals  # noqa: E402
from core.pipeline import KavachPipeline  # noqa: E402
from core.vault import ChannelIdentity, FamilySettings, TrustedContact, TrustedContactVault  # noqa: E402

PARENT_IDS = {"fake": "100", "telegram": "1001"}
RAVI_IDS = {"fake": "200", "telegram": "2002"}
SITA_IDS = {"fake": "300", "telegram": "3003"}
STRANGER_ID = "999"

RAVI_PHONE = "+91 90000 11111"
SITA_PHONE = "+91 90000 22222"
UNKNOWN_PHONE = "+91 90000 99999"

DIGITAL_ARREST = (
    "This is CBI officer. Your parcel had drugs, you are under digital arrest. "
    "Do not tell anyone. Pay Rs 50,000 immediately to avoid arrest."
)
RAVI_NEW_NUMBER = "Mom, it's Ravi, I changed my number. Can you call me when you are free?"


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: int) -> None:
        self.now += timedelta(**kwargs)


@dataclass
class StubExtractor:
    """Deterministic stand-in for Nemotron. Records exactly what it was given."""

    seen: list[str] = field(default_factory=list)

    async def extract(self, redacted_text: str, kind: str) -> RiskSignals:
        self.seen.append(redacted_text)
        t = redacted_text.lower()
        name = re.search(r"\b(?:it's|it is|this is)\s+([A-Z][a-z]+)", redacted_text)
        return RiskSignals(
            urgency=any(w in t for w in ("urgent", "immediately", "right now")),
            secrecy=any(w in t for w in ("don't tell", "do not tell", "secret")),
            authority_impersonation=any(w in t for w in ("cbi", "police", "arrest", "customs", "court")),
            payment_pressure=any(w in t for w in ("pay", "send money", "transfer", "upi")),
            claimed_family_name=name.group(1) if name and name.group(1) not in ("CBI",) else None,
        )


def build_vault(language: str = "te") -> TrustedContactVault:
    vault = TrustedContactVault(
        FamilySettings(
            parent=tuple(ChannelIdentity(ch, uid) for ch, uid in PARENT_IDS.items()),
            parent_name="Amma",
            language=language,
            guardian_contact_id="ravi",
            recorded_message_ref="voice-ravi-approved",
        )
    )
    vault.add_contact(
        TrustedContact(
            contact_id="ravi",
            name="Ravi",
            relationship="son",
            phone=RAVI_PHONE,
            identities=tuple(ChannelIdentity(ch, uid) for ch, uid in RAVI_IDS.items()),
            approved_by_guardian=True,
            is_guardian=True,
        )
    )
    vault.add_contact(
        TrustedContact(
            contact_id="sita",
            name="Sita",
            relationship="daughter",
            phone=SITA_PHONE,
            identities=tuple(ChannelIdentity(ch, uid) for ch, uid in SITA_IDS.items()),
            approved_by_guardian=True,
        )
    )
    return vault


def build_pipeline(channel: Any, language: str = "te"):
    clock = FakeClock()
    extractor = StubExtractor()
    ids = iter(f"{i:016x}" for i in range(1, 10_000))
    pipeline = KavachPipeline(
        channel=channel,
        extractor=extractor,
        vault=build_vault(language),
        audit=AuditLog(clock),
        clock=clock,
        confirmation_timeout=timedelta(minutes=10),
        id_factory=lambda: next(ids),
    )
    return pipeline, clock, extractor


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
