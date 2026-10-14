"""Frozen, synthetic benchmark cases; identifiers are fabricated and never real."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaseRow:
    case_id: str
    category: str
    text: str
    language: str
    format: str
    signals: tuple[bool, bool, bool, bool]
    claimed_family: bool = False
    verifier_confirms: bool = False
    forwarded_bound_origin: bool = False
    planted_value: str = ""
    applicable_dated_advisory: bool = False


_FRAUD_CLAIMS = (
    "It's Ravi from a new number. Send money now.",
    "This is your son from a replacement phone. Please transfer today.",
    "Ravi here, my old SIM stopped working. Can you pay the bill?",
    "Mom, it's me. I need cash before the shop closes.",
    "Your nephew is messaging from a borrowed phone. Please help quickly.",
    "Ravi: I changed phones. Can you send the school fee?",
    "It's your daughter from a new number. I need help with a ticket.",
    "Brother here, this is my temporary line. Please pay the mechanic.",
    "Ravi from a new phone. Call me when you can.",
    "It's your son. Please reply when you see this.",
)
_REAL_CLAIMS = (
    "It's Ravi from my work phone. Please call me after lunch.",
    "Mom, new number for this week. I'm safe at the station.",
    "It's your daughter. I changed SIM cards; no rush, just saying hello.",
    "Ravi here. This is my second phone. Can we talk tonight?",
    "It's your son from the office line. Please call when free.",
    "Hi Amma, my number changed. I am on my way home.",
    "It's Sita. I have a temporary number; nothing urgent.",
    "Ravi here from a new device. Please save this number after we talk.",
    "It's your daughter. Please call me about Sunday's visit.",
    "Mom, new phone. I'm fine and will call you later.",
)
_AUTHORITY = (
    "Police parcel case: pay immediately or you will be arrested.",
    "CBI digital arrest notice. Do not tell anyone; transfer now.",
    "Customs officer says your parcel is held. Pay the fine today.",
    "Court warrant alert: send money immediately to avoid arrest.",
    "Police verification fee is due now. Keep this secret.",
)
_BENIGN = (
    "Reached home safely. See you tomorrow.",
    "Lunch is ready, come by when you are free.",
    "The doctor appointment is at four. No need to reply now.",
    "I left the book on the table. Talk later.",
    "Good morning, hope you slept well.",
)
_LANGUAGES = ("en", "hi", "te")
_FORMATS = ("text", "image", "audio", "video")


def heldout_cases() -> list[CaseRow]:
    cases: list[CaseRow] = []
    for i, text in enumerate(_FRAUD_CLAIMS):
        signals = (i < 5, i < 5, False, i < 8)
        cases.append(CaseRow(f"ho-family-fake-{i+1:02}", "family_impersonation", text,
                             _LANGUAGES[i % 3], _FORMATS[i % 4], signals,
                             claimed_family=True, verifier_confirms=False,
                             planted_value="482913" if i % 2 == 0 else ""))
    for i, text in enumerate(_REAL_CLAIMS):
        signals = (i in (0, 5), False, False, False)
        cases.append(CaseRow(f"ho-family-real-{i+1:02}", "genuine_new_number", text,
                             _LANGUAGES[(i+1) % 3], _FORMATS[(i+1) % 4], signals,
                             claimed_family=True, verifier_confirms=True))
    for i in range(20):
        text = _AUTHORITY[i % len(_AUTHORITY)]
        cases.append(CaseRow(f"ho-authority-{i+1:02}", "authority_scam", text,
                             _LANGUAGES[(i+2) % 3], _FORMATS[(i+2) % 4],
                             (True, True, True, True), planted_value="123456789012" if i % 3 == 0 else "",
                             applicable_dated_advisory=i % 2 == 0))
    for i in range(20):
        text = _BENIGN[i % len(_BENIGN)]
        cases.append(CaseRow(f"ho-benign-{i+1:02}", "benign", text,
                             _LANGUAGES[i % 3], _FORMATS[(i+3) % 4],
                             (False, False, False, False), forwarded_bound_origin=True,
                             verifier_confirms=True))
    return cases


def development_cases() -> list[CaseRow]:
    cases = []
    for i in range(10):
        cases.append(CaseRow(f"dev-family-{i+1:02}", "family_impersonation",
                             _FRAUD_CLAIMS[i], _LANGUAGES[i % 3], _FORMATS[i % 4],
                             (i % 2 == 0, i % 3 == 0, False, i < 6), True, False,
                             planted_value="123456" if i % 2 == 0 else ""))
    for i in range(10):
        cases.append(CaseRow(f"dev-authority-{i+1:02}", "authority_scam",
                             _AUTHORITY[i % len(_AUTHORITY)], _LANGUAGES[i % 3], _FORMATS[i % 4],
                             (True, True, True, True), planted_value="4111 1111 1111 1111" if i % 2 else ""))
    for i in range(10):
        cases.append(CaseRow(f"dev-benign-{i+1:02}", "benign", _BENIGN[i % len(_BENIGN)],
                             _LANGUAGES[i % 3], _FORMATS[i % 4], (False, False, False, False),
                             forwarded_bound_origin=True, verifier_confirms=True))
    return cases
