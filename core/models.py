"""Core value types. No channel- or vendor-specific code here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Outcome(str, Enum):
    VERIFIED = "VERIFIED"  # Verified trusted contact
    PAUSE = "PAUSE"  # Pause and verify
    LIKELY_SCAM = "LIKELY_SCAM"  # Likely scam


class Action(str, Enum):
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"
    OPEN_CIRCUIT_BREAKER = "OPEN_CIRCUIT_BREAKER"
    ALERT_GUARDIAN = "ALERT_GUARDIAN"


@dataclass(frozen=True)
class RiskSignals:
    """Signals extracted from redacted content. Advisory input to policy only."""

    urgency: bool = False
    secrecy: bool = False
    authority_impersonation: bool = False
    payment_pressure: bool = False
    claimed_family_name: Optional[str] = None

    def active(self) -> list[str]:
        names = ["urgency", "secrecy", "authority_impersonation", "payment_pressure"]
        return [n for n in names if getattr(self, n)]


@dataclass(frozen=True)
class PolicyDecision:
    outcome: Outcome
    reasons: tuple[str, ...]
    actions: frozenset[Action] = frozenset()


@dataclass
class Case:
    case_id: str
    channel: str
    reporter_id: str
    redacted_text: str
    signals: RiskSignals
    outcome: Outcome
    reasons: list[str]
    created_at: datetime
    claimed_contact_id: Optional[str] = None
    pending_request_id: Optional[str] = None
    authorized_contact_id: Optional[str] = None
    confirmation_deadline: Optional[datetime] = None
    circuit_breaker_opened: bool = False
    history: list[str] = field(default_factory=list)
    high_risk: bool = False
    rule_fired: str = ""
    latency_ms: dict[str, float] = field(default_factory=dict)
    cost_rupees: float = 0.0
    pending_nonce: Optional[str] = None
    identity_route_verified: bool = False
    evidence: list[dict[str, str]] = field(default_factory=list)
    explanation: str = ""
    model_traces: list[dict[str, object]] = field(default_factory=list)
