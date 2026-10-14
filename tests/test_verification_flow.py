"""Safety-critical family-verification flow, exercised through FakeChannel."""

from __future__ import annotations

import asyncio

import pytest

from channels.fake import FakeChannel
from core.models import Outcome, RiskSignals
from core.pipeline import UnknownReporter, UnknownRequest
from core.redaction import contains_sensitive
from tests.conftest import (
    DIGITAL_ARREST,
    PARENT_IDS,
    RAVI_IDS,
    RAVI_NEW_NUMBER,
    RAVI_PHONE,
    SITA_IDS,
    STRANGER_ID,
    UNKNOWN_PHONE,
    build_pipeline,
)

PARENT = PARENT_IDS["fake"]


def run(coro):
    return asyncio.run(coro)


def inbound(text: str, phone: str | None = None, sender: str = PARENT,
            forwarded_sender_id: str | None = None) -> dict:
    return {"sender_id": sender, "kind": "text", "text": text,
            "contact_card_phone": phone, "forwarded_sender_id": forwarded_sender_id}


async def _open_ravi_claim(pipeline):
    return await pipeline.handle_inbound(inbound(RAVI_NEW_NUMBER, UNKNOWN_PHONE))


# 1. The same pipeline works with FakeChannel ----------------------------------
def test_pipeline_runs_on_fake_channel():
    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel)

    case = run(pipeline.handle_inbound(inbound("Reached home safely.", RAVI_PHONE,
                                               forwarded_sender_id=RAVI_IDS["fake"])))

    assert case.outcome is Outcome.PAUSE
    assert case.identity_route_verified is False
    [request] = channel.confirmation_requests
    assert request.recipient_id == RAVI_IDS["fake"]
    run(pipeline.handle_confirmation({"request_id": request.request_id,
                                      "responder_id": RAVI_IDS["fake"],
                                      "confirmed": True, "nonce": request.nonce}))
    assert case.outcome is Outcome.VERIFIED
    assert case.identity_route_verified
    assert [r.recipient_id for r in channel.replies] == [PARENT, PARENT]
    assert channel.alerts == [] and channel.circuit_breakers == []
    assert "case_opened" in pipeline.audit.events(case.case_id)


def test_unknown_contact_without_claim_is_paused_not_called_safe():
    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel)
    case = run(pipeline.handle_inbound(inbound("Hello aunty, how are you?", UNKNOWN_PHONE)))
    assert case.outcome is Outcome.PAUSE
    assert channel.confirmation_requests == []


def test_contact_card_phone_never_verifies_identity():
    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel)
    case = run(pipeline.handle_inbound(inbound("Reached home safely.", RAVI_PHONE)))
    assert case.outcome is Outcome.PAUSE
    assert not case.identity_route_verified


def test_invalid_signal_extraction_fails_to_pause_and_alert():
    class BrokenExtractor:
        last_failed = True

        async def extract(self, redacted_text, kind):
            return RiskSignals()

    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel)
    pipeline.extractor = BrokenExtractor()
    case = run(pipeline.handle_inbound(inbound("A message that could not be parsed")))
    assert case.outcome is Outcome.PAUSE
    assert channel.alerts
    assert "could not read" in case.reasons[0]


def test_only_the_protected_parent_can_submit_checks():
    pipeline, _, _ = build_pipeline(FakeChannel())
    with pytest.raises(UnknownReporter):
        run(pipeline.handle_inbound(inbound("anything", sender=STRANGER_ID)))


# 2. An untrusted person cannot confirm a family identity ----------------------
@pytest.mark.parametrize(
    "responder",
    [STRANGER_ID, PARENT, SITA_IDS["fake"]],
    ids=["stranger", "parent-herself", "other-trusted-contact"],
)
def test_untrusted_responder_cannot_confirm_identity(responder):
    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel)
    case = run(_open_ravi_claim(pipeline))

    assert case.outcome is Outcome.PAUSE
    [request] = channel.confirmation_requests
    assert request.recipient_id == RAVI_IDS["fake"]  # asked the REAL Ravi

    after = run(
        pipeline.handle_confirmation(
            {"request_id": request.request_id, "responder_id": responder, "confirmed": True,
             "nonce": request.nonce}
        )
    )

    assert after.outcome is Outcome.PAUSE
    assert after.pending_request_id == request.request_id  # still waiting for Ravi
    assert "unauthorized_confirmation_ignored" in pipeline.audit.events(case.case_id)
    assert Outcome.VERIFIED.value not in after.history


def test_forged_request_id_is_rejected():
    pipeline, _, _ = build_pipeline(FakeChannel())
    run(_open_ravi_claim(pipeline))
    with pytest.raises(UnknownRequest):
        run(
            pipeline.handle_confirmation(
                {"request_id": "deadbeefdeadbeef", "responder_id": RAVI_IDS["fake"], "confirmed": True}
            )
        )


# 3. No guardian response leaves the case in PAUSE -----------------------------
def test_no_response_keeps_case_paused_and_escalates():
    channel = FakeChannel()
    pipeline, clock, _ = build_pipeline(channel)
    case = run(_open_ravi_claim(pipeline))
    [request] = channel.confirmation_requests

    clock.advance(minutes=11)
    expired = run(pipeline.expire_pending())

    assert expired == [case]
    assert case.outcome is Outcome.PAUSE
    assert case.pending_request_id is None
    assert "confirmation_timed_out" in pipeline.audit.events(case.case_id)
    assert channel.alerts, "guardian should be escalated on timeout"

    # A late "yes" after the deadline must not flip the case to VERIFIED.
    with pytest.raises(UnknownRequest):
        run(
            pipeline.handle_confirmation(
                {"request_id": request.request_id, "responder_id": RAVI_IDS["fake"], "confirmed": True,
                 "nonce": request.nonce}
            )
        )
    assert case.outcome is Outcome.PAUSE


def test_confirmation_arriving_after_deadline_without_sweep_is_ignored():
    channel = FakeChannel()
    pipeline, clock, _ = build_pipeline(channel)
    case = run(_open_ravi_claim(pipeline))
    [request] = channel.confirmation_requests

    clock.advance(minutes=30)  # no expire_pending() sweep has run yet
    run(
        pipeline.handle_confirmation(
            {"request_id": request.request_id, "responder_id": RAVI_IDS["fake"], "confirmed": True,
             "nonce": request.nonce}
        )
    )
    assert case.outcome is Outcome.PAUSE
    assert "late_confirmation_ignored" in pipeline.audit.events(case.case_id)


# 4. A trusted guardian confirmation changes the result to VERIFIED ------------
def test_trusted_confirmation_verifies():
    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel)
    case = run(_open_ravi_claim(pipeline))
    [request] = channel.confirmation_requests

    run(
        pipeline.handle_confirmation(
            {"request_id": request.request_id, "responder_id": RAVI_IDS["fake"], "confirmed": True,
             "nonce": request.nonce}
        )
    )

    assert case.outcome is Outcome.VERIFIED
    assert case.history == [Outcome.PAUSE.value, Outcome.VERIFIED.value]
    assert channel.replies[-1].recipient_id == PARENT


def test_unknown_claimed_name_asks_the_guardian():
    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel)
    run(pipeline.handle_inbound(inbound("Hi aunty, it's Kiran from the building.", UNKNOWN_PHONE)))
    [request] = channel.confirmation_requests
    assert request.recipient_id == RAVI_IDS["fake"]  # Ravi is the guardian


def test_rejection_becomes_likely_scam():
    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel)
    case = run(_open_ravi_claim(pipeline))
    [request] = channel.confirmation_requests
    run(
        pipeline.handle_confirmation(
            {"request_id": request.request_id, "responder_id": RAVI_IDS["fake"], "confirmed": False,
             "nonce": request.nonce}
        )
    )
    assert case.outcome is Outcome.LIKELY_SCAM
    assert case.circuit_breaker_opened


def test_high_risk_stays_likely_scam_even_after_verifier_confirms():
    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel)
    message = "Mom, it's Ravi. Pay immediately, don't tell anyone."
    case = run(pipeline.handle_inbound(inbound(message, UNKNOWN_PHONE)))
    assert case.high_risk
    assert case.outcome is Outcome.LIKELY_SCAM
    [request] = channel.confirmation_requests
    run(pipeline.handle_confirmation({"request_id": request.request_id,
                                      "responder_id": RAVI_IDS["fake"],
                                      "confirmed": True, "nonce": request.nonce}))
    assert case.outcome is Outcome.LIKELY_SCAM
    assert not case.identity_route_verified
    assert case.circuit_breaker_opened


# 5. A high-risk policy result opens the Circuit-Breaker -----------------------
def test_high_risk_opens_circuit_breaker():
    channel = FakeChannel()
    pipeline, _, _ = build_pipeline(channel, language="te")
    case = run(pipeline.handle_inbound(inbound(DIGITAL_ARREST, UNKNOWN_PHONE)))

    assert case.outcome is Outcome.LIKELY_SCAM
    [event] = channel.circuit_breakers
    assert event.recipient_id == PARENT
    assert event.headline == "ఆగండి. డబ్బు చెల్లించకండి."
    assert event.recorded_message_ref == "voice-ravi-approved"  # human recording, not AI
    assert event.call_number == RAVI_PHONE  # saved trusted number
    assert [a.recipient_id for a in channel.alerts] == [RAVI_IDS["fake"]]
    assert "circuit_breaker_opened" in pipeline.audit.events(case.case_id)


# 6. Alerts never contain raw OTPs or account-like values ----------------------
def test_sensitive_values_never_leave_the_core():
    raw_values = ["482913", "123456789012", "4111 1111 1111 1111", "fraud.help@okaxis", "pass@1234"]
    text = (
        "This is CBI. You are under arrest, do not tell anyone. Pay immediately. "
        "Your OTP is 482913. Account 123456789012, card 4111 1111 1111 1111, "
        "UPI fraud.help@okaxis, password: pass@1234"
    )
    channel = FakeChannel()
    pipeline, _, extractor = build_pipeline(channel)
    run(pipeline.handle_inbound(inbound(text, UNKNOWN_PHONE)))

    outbound = channel.all_outbound_text()
    audit_text = [v for e in pipeline.audit.entries for v in e.detail.values()]
    assert channel.alerts, "high-risk case should alert the guardian"
    for value in raw_values:
        for blob in outbound + audit_text + extractor.seen:
            assert value not in blob, f"{value!r} leaked into: {blob!r}"
    for alert in channel.alerts:
        assert not contains_sensitive(alert.text)
    assert "[OTP]" in extractor.seen[0] and "[ACCOUNT]" in extractor.seen[0]
