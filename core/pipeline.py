"""Core verification pipeline.

Accepts any ``MessagingChannel`` by dependency injection and imports only
``channels.base`` - never a concrete adapter. Model calls are behind the
``SignalExtractor`` protocol and are not implemented in this slice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol
from uuid import uuid4

from channels.base import (
    Alert,
    CircuitBreakerEvent,
    ConfirmationRequest,
    MessageKind,
    MessagingChannel,
    OutboundReply,
)
from core import policy, templates
from core.audit import AuditLog
from core.models import Action, Case, Outcome, PolicyDecision, RiskSignals
from core.redaction import redact
from core.store import Store
from core.vault import TrustedContact, TrustedContactVault


class SignalExtractor(Protocol):
    """Receives redacted text only. Real implementation: Nemotron Nano / Omni."""

    async def extract(self, redacted_text: str, kind: MessageKind) -> RiskSignals: ...


class UnknownReporter(PermissionError):
    """Inbound message from someone who is not the protected parent."""


class UnknownRequest(LookupError):
    """Confirmation for a request id Kavach never issued or already closed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex[:16]


class KavachPipeline:
    def __init__(
        self,
        channel: MessagingChannel,
        extractor: SignalExtractor,
        vault: TrustedContactVault,
        audit: AuditLog,
        *,
        clock: Callable[[], datetime] = _utc_now,
        confirmation_timeout: timedelta = timedelta(minutes=10),
        id_factory: Callable[[], str] = _new_id,
        store: Store | None = None,
        reply_builder: Callable[[Case, str, str], Awaitable[str]] | None = None,
    ) -> None:
        self.channel = channel
        self.extractor = extractor
        self.vault = vault
        self.audit = audit
        self.clock = clock
        self.confirmation_timeout = confirmation_timeout
        self.new_id = id_factory
        self.store = store
        self.reply_builder = reply_builder
        self.cases: dict[str, Case] = {}
        self._by_request: dict[str, str] = {}
        if self.store:
            for restored in self.store.load_cases():
                self.cases[restored.case_id] = restored
                if restored.pending_request_id:
                    self._by_request[restored.pending_request_id] = restored.case_id

    # -- entry points --------------------------------------------------------
    async def handle_inbound(self, raw: Mapping[str, Any]) -> Case:
        msg = self.channel.parse_inbound(raw)
        if not self.vault.is_parent(msg.channel, msg.sender_id):
            self.audit.record("-", "rejected_reporter", channel=msg.channel)
            raise UnknownReporter("only the protected parent can submit checks")

        redacted = redact(msg.text)
        signals = await self.extractor.extract(redacted.text, msg.kind)
        origin_contact = self.vault.find_by_identity(msg.channel, msg.forwarded_sender_id)
        claimed = self.vault.find_by_name(signals.claimed_family_name) or origin_contact
        if origin_contact and not signals.claimed_family_name:
            signals = RiskSignals(
                urgency=signals.urgency, secrecy=signals.secrecy,
                authority_impersonation=signals.authority_impersonation,
                payment_pressure=signals.payment_pressure,
                claimed_family_name=origin_contact.name,
            )
        decision = policy.decide(signals, sender_is_bound=origin_contact is not None)
        extraction_failed = bool(getattr(self.extractor, "last_failed", False))
        if extraction_failed:
            actions = {Action.ALERT_GUARDIAN}
            if origin_contact:
                actions.add(Action.REQUEST_CONFIRMATION)
            decision = PolicyDecision(
                Outcome.PAUSE,
                ("Kavach could not read the message reliably; verify with your family before acting",),
                frozenset(actions),
            )
        if policy.is_high_risk(signals) and claimed is not None:
            # Ask the claimed relative as an additional identity check, while keeping the
            # likely-scam outcome and Circuit-Breaker in force regardless of their answer.
            decision = PolicyDecision(
                decision.outcome, decision.reasons,
                decision.actions | frozenset({Action.REQUEST_CONFIRMATION}),
            )

        case = Case(
            case_id=self.new_id(),
            channel=msg.channel,
            reporter_id=msg.sender_id,
            redacted_text=redacted.text,
            signals=signals,
            outcome=decision.outcome,
            reasons=list(decision.reasons),
            created_at=self.clock(),
            claimed_contact_id=claimed.contact_id if claimed else None,
            high_risk=policy.is_high_risk(signals),
            rule_fired=("high_risk_payment" if policy.is_high_risk(signals) else decision.reasons[0]),
        )
        self.cases[case.case_id] = case
        self.audit.record(
            case.case_id,
            "case_opened",
            kind=msg.kind,
            outcome=decision.outcome.value,
            signals=",".join(signals.active()) or "none",
            extraction_failed=str(extraction_failed).lower(),
            redactions=",".join(f"{k}={v}" for k, v in sorted(redacted.counts.items())) or "none",
            content=redacted.text,
        )
        await self._apply(case, decision)
        self._persist(case)
        return case

    async def handle_confirmation(self, raw: Mapping[str, Any]) -> Case:
        resp = self.channel.parse_confirmation(raw)
        case_id = self._by_request.get(resp.request_id)
        case = self.cases.get(case_id) if case_id else None
        if case is None or case.pending_request_id != resp.request_id:
            self.audit.record("-", "unknown_confirmation_request")
            raise UnknownRequest("no open confirmation request with that id")

        if (resp.channel != case.channel or resp.nonce != case.pending_nonce or not self.vault.is_identity(
            case.authorized_contact_id, resp.channel, resp.responder_id
        )):
            # Anyone other than the saved contact we asked is ignored. Case stays as is.
            self.audit.record(case.case_id, "unauthorized_confirmation_ignored")
            return case

        if case.confirmation_deadline and self.clock() >= case.confirmation_deadline:
            await self._expire(case)
            self.audit.record(case.case_id, "late_confirmation_ignored")
            return case

        case.pending_request_id = None
        case.pending_nonce = None
        self.audit.record(
            case.case_id, "confirmation_received", confirmed=str(resp.confirmed).lower()
        )
        case.identity_route_verified = resp.confirmed and not case.high_risk
        await self._apply(case, policy.apply_confirmation(resp.confirmed, high_risk=case.high_risk))
        self._persist(case)
        return case

    async def expire_pending(self) -> list[Case]:
        now = self.clock()
        expired = [
            c
            for c in self.cases.values()
            if c.pending_request_id and c.confirmation_deadline and now >= c.confirmation_deadline
        ]
        for case in expired:
            await self._expire(case)
            self._persist(case)
        return expired

    # -- internals -----------------------------------------------------------
    async def _expire(self, case: Case) -> None:
        case.pending_request_id = None
        case.pending_nonce = None
        self.audit.record(case.case_id, "confirmation_timed_out")
        await self._apply(case, policy.apply_timeout(), parent_key="timeout")

    async def _apply(
        self, case: Case, decision: PolicyDecision, *, parent_key: Optional[str] = None
    ) -> None:
        case.outcome = decision.outcome
        case.reasons = list(decision.reasons)
        case.history.append(decision.outcome.value)

        if Action.REQUEST_CONFIRMATION in decision.actions:
            await self._request_confirmation(case)
        if Action.OPEN_CIRCUIT_BREAKER in decision.actions and not case.circuit_breaker_opened:
            await self._open_circuit_breaker(case)
        escalation = self.vault.settings.escalation_rule
        rule_alert = escalation == "all_checks" or (
            escalation == "all_pause" and decision.outcome in (Outcome.PAUSE, Outcome.LIKELY_SCAM)
        )
        if Action.ALERT_GUARDIAN in decision.actions or rule_alert:
            await self._alert_guardian(case)

        language = self.vault.settings.language
        text = templates.parent_text(language, parent_key or decision.outcome.value)
        if self.reply_builder:
            try:
                text = await self.reply_builder(case, text, language)
            except Exception:
                # Advisory integrations can enrich a fixed reply but cannot block safety actions.
                pass
        await self.channel.send_reply(
            OutboundReply(
                recipient_id=case.reporter_id,
                case_id=case.case_id,
                text=_safe(f"{text} {' '.join(decision.reasons)}"),
                language=language,
            )
        )
        self.audit.record(case.case_id, "parent_replied", outcome=decision.outcome.value)

    async def _request_confirmation(self, case: Case) -> None:
        # Ask the real claimed relative on their saved identity; else the guardian.
        target: Optional[TrustedContact] = None
        if case.claimed_contact_id and self.vault.identity_on(case.claimed_contact_id, case.channel):
            target = self.vault.get(case.claimed_contact_id)
        elif self.vault.guardian and self.vault.identity_on(self.vault.guardian.contact_id, case.channel):
            target = self.vault.guardian
        if target is None:
            self.audit.record(case.case_id, "no_reachable_trusted_contact")
            return

        request_id = self.new_id()
        nonce = secrets.token_hex(16)
        case.pending_request_id = request_id
        case.pending_nonce = nonce
        case.authorized_contact_id = target.contact_id
        case.confirmation_deadline = self.clock() + self.confirmation_timeout
        self._by_request[request_id] = case.case_id

        prompt = templates.CONFIRMATION_PROMPT.format(
            claimed_name=case.signals.claimed_family_name or "a family member",
            parent_name=self.vault.settings.parent_name,
        )
        await self.channel.send_confirmation_request(
            ConfirmationRequest(
                request_id=request_id,
                case_id=case.case_id,
                recipient_id=self.vault.identity_on(target.contact_id, case.channel) or "",
                prompt=_safe(prompt),
                nonce=nonce,
            )
        )
        self.audit.record(case.case_id, "confirmation_requested", contact=target.contact_id)
        self._persist(case)

    async def _open_circuit_breaker(self, case: Case) -> None:
        call_contact = (
            self.vault.get(case.claimed_contact_id) if case.claimed_contact_id else None
        ) or self.vault.guardian
        await self.channel.open_circuit_breaker(
            CircuitBreakerEvent(
                recipient_id=case.reporter_id,
                case_id=case.case_id,
                headline=templates.parent_text(self.vault.settings.language, "circuit_breaker"),
                call_number=call_contact.phone if call_contact else None,
                recorded_message_ref=self.vault.settings.recorded_message_ref,
            )
        )
        case.circuit_breaker_opened = True
        self.audit.record(case.case_id, "circuit_breaker_opened")

    async def _alert_guardian(self, case: Case) -> None:
        guardian = self.vault.guardian
        recipient = self.vault.identity_on(guardian.contact_id, case.channel) if guardian else None
        if recipient is None:
            self.audit.record(case.case_id, "guardian_unreachable")
            return
        text = templates.GUARDIAN_ALERT.format(
            parent_name=self.vault.settings.parent_name,
            outcome=case.outcome.value,
            reason="; ".join(case.reasons),
            signals=", ".join(case.signals.active()) or "none",
            excerpt=case.redacted_text[:160],
        )
        await self.channel.send_alert(Alert(recipient, case.case_id, _safe(text)))
        self.audit.record(case.case_id, "guardian_alerted")

    def _persist(self, case: Case) -> None:
        if self.store:
            self.store.save_case(case)


def _safe(text: str) -> str:
    """Defence in depth: re-redact anything leaving the core."""
    return redact(text).text
