"""A dated, locally cached official advisory for repeatable synthetic demos."""

from core.explain import Evidence


DIGITAL_ARREST_ADVISORY = Evidence(
    title="Advisory on ‘Digital Arrest’ Scam",
    url="https://cybercrime.gov.in/pdf/Advisories/ADVISORYTAU-ADV-003DigitalArrest06.03.2025.pdf",
    domain="cybercrime.gov.in",
    published_date="2025-03-06",
    quote="Real law enforcement won't arrest you digitally.",
)


def cached_for(signals: object) -> list[Evidence]:
    if getattr(signals, "authority_impersonation", False):
        return [DIGITAL_ARREST_ADVISORY]
    return []
