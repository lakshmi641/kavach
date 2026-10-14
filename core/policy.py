"""Deterministic policy. The only place outcomes and actions are decided.

Models may supply ``RiskSignals``; they never choose outcomes, send alerts,
or mark anyone as verified.
"""

from __future__ import annotations

from core.models import Action, Outcome, PolicyDecision, RiskSignals


def _pressure(s: RiskSignals) -> bool:
    return s.payment_pressure and (s.urgency or s.secrecy)


def is_high_risk(s: RiskSignals) -> bool:
    # This rule outranks identity confirmation and a saved sender number.
    return (s.payment_pressure and s.urgency and s.secrecy) or (
        s.authority_impersonation and s.payment_pressure
    )


def decide(signals: RiskSignals, *, sender_is_bound: bool = False) -> PolicyDecision:
    if is_high_risk(signals):
        return PolicyDecision(
            Outcome.LIKELY_SCAM,
            ("Authority impersonation with payment pressure",),
            frozenset({Action.OPEN_CIRCUIT_BREAKER, Action.ALERT_GUARDIAN}),
        )

    if sender_is_bound:
        return PolicyDecision(
            Outcome.PAUSE,
            ("Forwarded from a bound family account; confirm the sender before trusting the request",),
            frozenset({Action.REQUEST_CONFIRMATION}),
        )

    if signals.claimed_family_name:
        actions = {Action.REQUEST_CONFIRMATION}
        if _pressure(signals):
            actions |= {Action.OPEN_CIRCUIT_BREAKER, Action.ALERT_GUARDIAN}
        return PolicyDecision(
            Outcome.PAUSE,
            ("Someone claims to be family from a number that is not saved",),
            frozenset(actions),
        )

    if len(signals.active()) >= 2:
        return PolicyDecision(
            Outcome.PAUSE,
            ("Several warning signs from an unknown contact",),
            frozenset({Action.ALERT_GUARDIAN}),
        )

    return PolicyDecision(Outcome.PAUSE, ("This contact is not verified",))


def apply_confirmation(confirmed: bool, *, high_risk: bool = False) -> PolicyDecision:
    if high_risk:
        return PolicyDecision(
            Outcome.LIKELY_SCAM,
            ("High-risk payment pressure still requires you to stop",),
            frozenset({Action.OPEN_CIRCUIT_BREAKER, Action.ALERT_GUARDIAN}),
        )
    if confirmed:
        return PolicyDecision(Outcome.VERIFIED, ("Your trusted contact confirmed it was them",))
    return PolicyDecision(
        Outcome.LIKELY_SCAM,
        ("Your trusted contact said it was not them",),
        frozenset({Action.OPEN_CIRCUIT_BREAKER, Action.ALERT_GUARDIAN}),
    )


def apply_timeout() -> PolicyDecision:
    return PolicyDecision(
        Outcome.PAUSE,
        ("No answer from your trusted contact; still not verified",),
        frozenset({Action.ALERT_GUARDIAN}),
    )
