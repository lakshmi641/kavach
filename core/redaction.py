"""Local redaction, applied before any model, Tavily or outbound call.

Deliberately conservative: over-masking a year or a short number is acceptable;
leaking an OTP or account number is not. Names are kept because the
verification flow needs the claimed name; see README for this boundary.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# (label, pattern, group to mask; 0 = whole match). Order matters.
_RULES: list[tuple[str, re.Pattern[str], int]] = [
    ("PASSWORD", re.compile(r"(?i)\b(?:password|passcode|pwd|m-?pin|pin)\b\s*(?:is|:|=|-)?\s*(\S+)"), 1),
    ("OTP", re.compile(r"(?i)\b(?:otp|one[\s-]?time[\s-]?password|verification code|code)\b[^\d\n]{0,15}(\d{4,8})\b"), 1),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), 0),
    ("UPI", re.compile(r"\b[\w.\-]{2,}@[A-Za-z]{2,}\b"), 0),
    ("CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), 0),
    ("ID_NUMBER", re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"), 0),
    ("PHONE", re.compile(r"(?:\+91[\s-]?)?\b[6-9]\d{4}[\s-]?\d{5}\b"), 0),
    ("ACCOUNT", re.compile(r"\b\d{9,18}\b"), 0),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), 0),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), 0),
    # Any remaining 4-8 digit run that is not a rupee amount.
    (
        "CODE",
        re.compile(
            r"(?<![\d,.])(?<!₹)(?<!₹ )(?<!Rs)(?<!Rs )(?<!Rs\.)(?<!Rs\. )(?<!INR)(?<!INR )\b\d{4,8}\b(?![\d,])"
        ),
        0,
    ),
]


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def redacted_anything(self) -> bool:
        return bool(self.counts)


def redact(text: str) -> RedactionResult:
    counts: Counter[str] = Counter()
    out = text
    for label, pattern, group in _RULES:

        def _mask(m: re.Match[str], label: str = label, group: int = group) -> str:
            counts[label] += 1
            if group == 0:
                return f"[{label}]"
            start, end = m.span(group)
            base = m.start()
            whole = m.group(0)
            return whole[: start - base] + f"[{label}]" + whole[end - base :]

        out = pattern.sub(_mask, out)
    return RedactionResult(text=out, counts=dict(counts))


def contains_sensitive(text: str) -> bool:
    """True if redaction would still change the text."""
    return redact(text).text != text
