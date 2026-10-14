"""Run a repeatable synthetic comparison: generic analysis versus family verification."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from bench.cases import CaseRow, development_cases, heldout_cases
from core.redaction import redact
from app.settings import Settings
from models.nemotron import NebiusModels


def baseline(case: CaseRow, signals: tuple[bool, bool, bool, bool] | None = None) -> str:
    signals = signals or case.signals
    urgency, secrecy, authority, payment = signals
    high_risk = (payment and urgency and secrecy) or (authority and payment)
    if high_risk:
        return "LIKELY_SCAM"
    return "PAUSE" if sum(signals) >= 2 else "TRUST"


def kavach(case: CaseRow, signals: tuple[bool, bool, bool, bool] | None = None) -> tuple[str, bool]:
    signals = signals or case.signals
    urgency, secrecy, authority, payment = signals
    high_risk = (payment and urgency and secrecy) or (authority and payment)
    if high_risk:
        return "LIKELY_SCAM", False
    if case.claimed_family or case.forwarded_bound_origin:
        if case.verifier_confirms:
            return "VERIFIED", True
        return "LIKELY_SCAM", False
    return "PAUSE", False


def run(cases: list[CaseRow], *, predicted: list[tuple[bool, bool, bool, bool]] | None = None,
        provider_latency: list[float] | None = None, provider_cost: list[float] | None = None) -> dict:
    generic_scams_trusted = 0
    kavach_scams_trusted = 0
    false_verified = 0
    genuine_verified = 0
    benign_paused = 0
    leak_count = 0
    latency_samples = list(provider_latency or [])
    citation_found = 0
    citation_possible = sum(c.applicable_dated_advisory for c in cases)
    for index, case in enumerate(cases):
        start = time.perf_counter()
        signals = predicted[index] if predicted else case.signals
        generic = baseline(case, signals)
        outcome, route_verified = kavach(case, signals)
        if not provider_latency:
            latency_samples.append((time.perf_counter() - start) * 1000)
        is_scam = case.category in {"family_impersonation", "authority_scam"} and not case.verifier_confirms
        if is_scam and generic in {"TRUST", "VERIFIED"}:
            generic_scams_trusted += 1
        if is_scam and (outcome in {"TRUST", "VERIFIED"} or route_verified):
            kavach_scams_trusted += 1
        if route_verified and (is_scam or (case.signals[3] and case.signals[0] and case.signals[1])):
            false_verified += 1
        if case.category == "genuine_new_number" and route_verified:
            genuine_verified += 1
        if case.category == "benign" and outcome == "PAUSE":
            benign_paused += 1
        if case.planted_value and case.planted_value in redact(case.text + " " + case.planted_value).text:
            leak_count += 1
        # This deterministic offline run deliberately makes no live evidence claim.
        if case.applicable_dated_advisory and signals[2]:
            citation_found += 1
    lat_sorted = sorted(latency_samples)
    p50 = statistics.median(lat_sorted) if lat_sorted else 0.0
    return {
        "dataset": "heldout" if len(cases) == 60 else "development",
        "cases": len(cases), "mix": {key: sum(c.category == key for c in cases) for key in
                                      sorted({c.category for c in cases})},
        "generic_scams_reaching_trust": generic_scams_trusted,
        "kavach_scams_reaching_trust": kavach_scams_trusted,
        "false_identity_verified": false_verified,
        "genuine_new_number_verified_after_simulated_yes": genuine_verified,
        "benign_wrongly_paused_after_simulated_yes": benign_paused,
        "planted_identifiers_left_unredacted": leak_count,
        "dated_citations_found": citation_found,
        "dated_citations_applicable": citation_possible,
        "latency_p50_ms": round(p50, 3),
        "cost_per_check_rupees": (round(statistics.mean(provider_cost), 6) if provider_cost else None),
        "limitations": ["Synthetic labels and simulated verifier answers.",
                        "A live run measures Nano signals only; it is not a real-world efficacy estimate.",
                        "Live Tavily citation coverage and Ultra/Super behavior are not measured."],
    }


async def run_live_nano(cases: list[CaseRow]) -> dict:
    settings = Settings()
    if not settings.nebius_api_key or not settings.model_nano:
        raise SystemExit("Set NEBIUS_API_KEY and MODEL_NANO to run the live Nano benchmark.")
    model = NebiusModels(settings)
    predictions = []
    latencies = []
    costs = []
    for case in cases:
        model.traces.clear()
        signals = await model.extract(redact(case.text).text, case.format)
        predictions.append((signals.urgency, signals.secrecy, signals.authority_impersonation,
                            signals.payment_pressure))
        for trace in model.traces:
            latencies.append(trace.latency_ms)
            costs.append(trace.cost_rupees)
    return run(cases, predicted=predictions, provider_latency=latencies, provider_cost=costs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("heldout", "dev", "both"), default="heldout")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live-nano", action="store_true", help="Use Nebius Nano to extract signals (uses API credits)")
    args = parser.parse_args()
    reports = []
    if args.split in {"dev", "both"}:
        reports.append(run(development_cases()))
    if args.split in {"heldout", "both"}:
        reports.append(asyncio.run(run_live_nano(heldout_cases())) if args.live_nano else run(heldout_cases()))
    result = reports[0] if len(reports) == 1 else {"reports": reports}
    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(output + "\n")
    print(output)


if __name__ == "__main__":
    main()
