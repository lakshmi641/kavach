"""Vendor-independent interfaces for advisory models and official evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.models import RiskSignals


@dataclass(frozen=True)
class Evidence:
    title: str
    url: str
    domain: str
    published_date: str
    quote: str = ""


@dataclass(frozen=True)
class Explanation:
    summary: str
    reasons: tuple[str, ...] = ()
    citation_relevance: bool = False


@dataclass(frozen=True)
class ModelTrace:
    model: str
    step: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_rupees: float = 0.0
    succeeded: bool = True


@dataclass(frozen=True)
class CheckResult:
    signals: RiskSignals
    explanation: Explanation
    reply: str
    evidence: tuple[Evidence, ...] = ()
    traces: tuple[ModelTrace, ...] = ()


class Explainer(Protocol):
    async def explain(
        self, redacted_text: str, signals: RiskSignals, evidence: list[Evidence]
    ) -> Explanation: ...


class Replier(Protocol):
    async def reply(self, template: str, explanation: Explanation, language: str) -> str: ...


class EvidenceSource(Protocol):
    async def search(self, sanitized_claim: str) -> list[Evidence]: ...


@dataclass
class TraceCollector:
    traces: list[ModelTrace] = field(default_factory=list)

    def add(self, trace: ModelTrace) -> None:
        self.traces.append(trace)
