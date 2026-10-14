"""Tavily search restricted to official domains and visible publication dates."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from core.explain import Evidence


OFFICIAL_DOMAINS = (
    "cybercrime.gov.in", "rbi.org.in", "pib.gov.in", "mha.gov.in",
    "dot.gov.in", "trai.gov.in", "cert-in.org.in",
)


class TavilyEvidence:
    def __init__(self, api_key: str, *, domains: tuple[str, ...] = OFFICIAL_DOMAINS) -> None:
        if not api_key:
            raise ValueError("TAVILY_API_KEY is required")
        self.api_key = api_key
        self.domains = domains

    async def search(self, sanitized_claim: str) -> list[Evidence]:
        query = sanitized_claim[:240]
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"query": query, "max_results": 5, "search_depth": "basic",
                      "include_domains": list(self.domains), "include_domains_mode": "restrict",
                      "include_published_date": True, "include_raw_content": False},
            )
            response.raise_for_status()
            data = response.json()
        candidates: list[tuple[str, str, str, str]] = []
        for item in data.get("results", []):
            url = str(item.get("url", ""))
            host = (urlparse(url).hostname or "").lower().removeprefix("www.")
            if not any(host == domain or host.endswith("." + domain) for domain in self.domains):
                continue
            published = item.get("published_date") or item.get("publishedDate")
            if not published:
                continue
            candidates.append((str(item.get("title", "Official advisory"))[:180], url, host,
                               str(published)[:40]))
        out: list[Evidence] = []
        # Extract just the best dated, allowlisted result; search snippets are not treated as quotes.
        for title, url, host, published in candidates[:1]:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.tavily.com/extract",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"urls": url, "query": query, "chunks_per_source": 3,
                          "extract_depth": "basic", "format": "text"},
                )
                response.raise_for_status()
                extracted = response.json().get("results", [])
            item = next((r for r in extracted if r.get("url") == url), None)
            content = str(item.get("raw_content", "")) if item else ""
            if content and _date_visible(published, content):
                out.append(Evidence(title, url, host, published, content[:500]))
        return out


def _date_visible(published: str, content: str) -> bool:
    """Require the publication year (and exact ISO date when present) in extracted page text."""
    iso = published[:10]
    if len(iso) == 10 and iso[4] == "-" and iso in content:
        return True
    year = next((part for part in published.split("-") if len(part) == 4 and part.isdigit()), "")
    if year and year in content:
        return True
    return False


def build_sanitized_claim(signals: object) -> str:
    """Build a generic search claim; never include message, contact or family data."""
    values = []
    for attr, phrase in (("authority_impersonation", "government impersonation"),
                         ("payment_pressure", "payment fraud"), ("urgency", "urgent payment scam"),
                         ("secrecy", "secrecy pressure scam")):
        if getattr(signals, attr, False):
            values.append(phrase)
    if not values:
        values.append("online fraud advisory")
    return "India official advisory " + " ".join(values[:3])
