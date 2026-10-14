"""Fixed, reviewable wording for safety-critical messages.

Telugu and Hindi strings are drafts pending native-speaker review before any
real use. Guardian-facing text is English in this slice.
"""

from __future__ import annotations

from core.models import Outcome

PARENT_TEXT: dict[str, dict[str, str]] = {
    "en": {
        Outcome.VERIFIED.value: "Verified trusted contact.",
        Outcome.PAUSE.value: "Pause and verify. Do not pay or share any code yet.",
        Outcome.LIKELY_SCAM.value: "Likely scam. Do not pay. Do not share any code.",
        "circuit_breaker": "Pause. Do not pay.",
        "timeout": "Still not verified. Do not pay. Call your family on the saved number.",
    },
    "te": {
        Outcome.VERIFIED.value: "నమ్మకమైన కాంటాక్ట్ నిర్ధారించబడింది.",
        Outcome.PAUSE.value: "ఆగండి, నిర్ధారించుకోండి. ఇప్పుడే డబ్బు చెల్లించకండి, ఏ కోడ్ కూడా చెప్పకండి.",
        Outcome.LIKELY_SCAM.value: "ఇది మోసం అయి ఉండవచ్చు. డబ్బు చెల్లించకండి. ఏ కోడ్ కూడా చెప్పకండి.",
        "circuit_breaker": "ఆగండి. డబ్బు చెల్లించకండి.",
        "timeout": "ఇంకా నిర్ధారించబడలేదు. డబ్బు చెల్లించకండి.",
    },
    "hi": {
        Outcome.VERIFIED.value: "भरोसेमंद संपर्क की पुष्टि हो गई।",
        Outcome.PAUSE.value: "रुकिए और पुष्टि कीजिए। अभी पैसे न भेजें, कोई कोड न बताएं।",
        Outcome.LIKELY_SCAM.value: "यह धोखाधड़ी हो सकती है। पैसे न भेजें। कोई कोड न बताएं।",
        "circuit_breaker": "रुकिए। पैसे न भेजें।",
        "timeout": "अभी तक पुष्टि नहीं हुई। पैसे न भेजें।",
    },
}

CONFIRMATION_PROMPT = (
    "Kavach check: someone claiming to be {claimed_name} contacted {parent_name} "
    "from a number that is not saved. Was this you?"
)

GUARDIAN_ALERT = (
    "Kavach alert for {parent_name}: {outcome}. Reason: {reason}. "
    "Signals: {signals}. Redacted excerpt: \"{excerpt}\""
)

# These translations are drafts pending native-speaker review.
RECOVERY_CHECKLIST: dict[str, tuple[str, ...]] = {
    "en": (
        "Call your bank now using the number on your card or the bank's official website.",
        "In India, call the national cybercrime helpline at 1930.",
        "Keep screenshots, messages and transaction IDs.",
        "Do not send more money. A person must contact the bank and file any report.",
    ),
    "hi": (
        "अपने कार्ड या बैंक की आधिकारिक वेबसाइट पर दिए नंबर से अभी बैंक को फोन करें।",
        "भारत में साइबर अपराध हेल्पलाइन 1930 पर फोन करें।",
        "स्क्रीनशॉट, संदेश और लेन-देन की आईडी संभालकर रखें।",
        "और पैसे न भेजें। बैंक से संपर्क और शिकायत कोई व्यक्ति स्वयं करे।",
    ),
    "te": (
        "మీ కార్డు లేదా బ్యాంకు అధికారిక వెబ్‌సైట్‌లో ఉన్న నంబర్‌కు వెంటనే ఫోన్ చేయండి.",
        "భారతదేశంలో సైబర్ నేరాల హెల్ప్‌లైన్ 1930కు కాల్ చేయండి.",
        "స్క్రీన్‌షాట్‌లు, సందేశాలు, లావాదేవీ ఐడీలను భద్రపరచండి.",
        "మరింత డబ్బు పంపకండి. బ్యాంకును సంప్రదించి ఫిర్యాదు చేయడానికి మనిషే చర్య తీసుకోవాలి.",
    ),
}


def parent_text(language: str, key: str) -> str:
    table = PARENT_TEXT.get(language, PARENT_TEXT["en"])
    return table.get(key, PARENT_TEXT["en"][key])


def recovery_checklist(language: str) -> tuple[str, ...]:
    return RECOVERY_CHECKLIST.get(language, RECOVERY_CHECKLIST["en"])
