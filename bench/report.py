"""Render a runner JSON result as a readable Markdown benchmark report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


_METRICS = (
    ("cases", "Cases"),
    ("generic_scams_reaching_trust", "Scams reaching trust in generic analysis"),
    ("kavach_scams_reaching_trust", "Scams reaching trust in Kavach"),
    ("false_identity_verified", "False identity verifications"),
    ("genuine_new_number_verified_after_simulated_yes", "Genuine new numbers verified after simulated yes"),
    ("benign_wrongly_paused_after_simulated_yes", "Benign messages paused"),
    ("planted_identifiers_left_unredacted", "Planted identifiers left unredacted"),
    ("dated_citations_found", "Dated citations found"),
    ("latency_p50_ms", "Signal extraction latency p50 (ms)"),
    ("cost_per_check_rupees", "Average model cost per check (₹)"),
)


def _format(value: Any) -> str:
    if value is None:
        return "Not measured"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def render_report(payload: dict[str, Any]) -> str:
    reports = payload.get("reports", [payload])
    lines = ["# Kavach synthetic benchmark", "",
             "These results use fabricated cases and simulated verifier answers. They are not an estimate of real-world scam detection performance.", ""]
    for report in reports:
        split = str(report.get("dataset", "benchmark")).title()
        lines.extend([f"## {split}", "", f"Case mix: `{json.dumps(report.get('mix', {}), sort_keys=True)}`", "",
                      "| Metric | Result |", "| --- | ---: |"])
        for key, label in _METRICS:
            if key == "dated_citations_found" and "dated_citations_applicable" in report:
                value = f"{_format(report.get(key))} / {_format(report.get('dated_citations_applicable'))} applicable"
            else:
                value = _format(report.get(key))
            lines.append(f"| {label} | {value} |")
        lines.extend(["", "Limitations:"])
        for limitation in report.get("limitations", []):
            lines.append(f"- {limitation}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON written by bench.run")
    parser.add_argument("--output", type=Path, help="Markdown path (default: print to stdout)")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    report = render_report(payload)
    if args.output:
        args.output.write_text(report)
    else:
        print(report)


if __name__ == "__main__":
    main()
