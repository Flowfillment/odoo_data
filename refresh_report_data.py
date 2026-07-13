#!/usr/bin/env python3
"""Full refresh of the Sales Analysis pipeline: phase 1 + phase 2 + run report.

Runs the extract (``pull_report_data.py``) and the transform
(``transform_report_data.py``) back to back with default settings, times
both, and finishes with a small run report:

- records downloaded per staging dataset (and pull duration per dataset),
- rows produced per report table,
- duration per phase and for the whole run,
- comparison against the previous full refresh,
- observations (warnings raised by either phase, notable count changes).

The report is printed and appended to ``output/refresh_log.md``; the raw
numbers of every run are appended to ``output/refresh_history.jsonl`` so
performance can be tracked over time. Per-phase metrics of the latest run
live in ``output/metrics/``. The structural improvement backlog the report
points to is ``docs/pipeline-improvements.md``.

The git fetch/sync step is the Windows runner's job
(``scripts/run-full-refresh.ps1``), which passes its timing in via
``--sync-seconds`` so it shows up in the report.

Usage:
    python refresh_report_data.py
    python refresh_report_data.py --all-dates          # forwarded to phase 1
    python refresh_report_data.py --iso-weeks          # forwarded to phase 2
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any

import pull_report_data
import transform_report_data

METRICS_DIR = os.path.join("output", "metrics")
LOG_PATH = os.path.join("output", "refresh_log.md")
HISTORY_PATH = os.path.join("output", "refresh_history.jsonl")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full Sales Analysis refresh (extract + transform) "
        "and finish with a run report."
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Forwarded to phase 1 (server-side date floor).",
    )
    parser.add_argument(
        "--all-dates",
        action="store_true",
        help="Forwarded to phase 1: pull the full history.",
    )
    parser.add_argument(
        "--cutoff",
        default=None,
        metavar="YYYY-MM-DD",
        help="Forwarded to phase 2 (fact/date-dimension cutoff).",
    )
    parser.add_argument(
        "--iso-weeks",
        action="store_true",
        help="Forwarded to phase 2: ISO 8601 week numbers in dim_date.",
    )
    parser.add_argument(
        "--sync-seconds",
        type=float,
        default=None,
        help=argparse.SUPPRESS,  # set by scripts/run-full-refresh.ps1
    )
    return parser.parse_args(argv)


def fmt_duration(seconds: float) -> str:
    if seconds >= 60:
        minutes, secs = divmod(seconds, 60)
        return f"{int(minutes)}m {secs:04.1f}s"
    return f"{seconds:.1f}s"


def fmt_count(value: int) -> str:
    return f"{value:,}".replace(",", ".")  # 95.372 (Dutch-style thousands)


def fmt_delta(value: int) -> str:
    return f"{value:+,}".replace(",", ".")


def read_previous_run(path: str) -> dict[str, Any] | None:
    """Last entry of the history file, if any."""
    try:
        with open(path, encoding="utf-8") as fh:
            last = None
            for line in fh:
                if line.strip():
                    last = line
            return json.loads(last) if last else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def build_report(
    run: dict[str, Any],
    extract: dict[str, Any],
    transform: dict[str, Any],
    previous: dict[str, Any] | None,
) -> str:
    lines: list[str] = []
    lines.append(f"## Full refresh - {run['finished_at']}")
    lines.append("")

    # --- Phase timings ---------------------------------------------------
    lines.append("| Phase | Duration | Result |")
    lines.append("|---|---|---|")
    if run.get("sync_seconds") is not None:
        lines.append(f"| git sync | {fmt_duration(run['sync_seconds'])} | code up to date |")
    lines.append(
        f"| 1 extract | {fmt_duration(extract['duration_seconds'])} | "
        f"{fmt_count(extract['total_records'])} records, "
        f"{len(extract['datasets'])} staging files |"
    )
    fact = transform["fact"]
    lines.append(
        f"| 2 transform | {fmt_duration(transform['duration_seconds'])} | "
        f"{fmt_count(transform['tables'].get('report_invoiced', 0))} fact rows, "
        f"{len(transform['tables'])} report tables |"
    )
    lines.append(f"| **total** | **{fmt_duration(run['total_seconds'])}** | |")
    lines.append("")

    # --- Extract detail ----------------------------------------------------
    lines.append("Extract per dataset:")
    lines.append("")
    lines.append("| Dataset | Records | Duration | Records/s |")
    lines.append("|---|---|---|---|")
    for name, data in extract["datasets"].items():
        rate = data["records"] / data["seconds"] if data["seconds"] else 0
        lines.append(
            f"| {name} | {fmt_count(data['records'])} | "
            f"{fmt_duration(data['seconds'])} | {fmt_count(int(rate))} |"
        )
    lines.append("")

    # --- Transform detail --------------------------------------------------
    dutch = transform["dutch_names"]
    lines.append(
        f"Transform: kept {fmt_count(fact['headers_kept'])}/"
        f"{fmt_count(fact['headers_total'])} invoices "
        f"({fmt_count(fact['headers_not_posted'])} not posted, "
        f"{fmt_count(fact['headers_before_cutoff'])} before {transform['cutoff']}) "
        f"and {fmt_count(fact['lines_kept'])}/{fmt_count(fact['lines_total'])} lines. "
        f"Dutch names matched {fmt_count(dutch['products_matched'])}/"
        f"{fmt_count(dutch['products_total'])} products."
    )
    lines.append("")

    # --- Comparison with the previous run -----------------------------------
    if previous:
        lines.append(
            f"Vs previous run ({previous['finished_at']}): "
            f"records {fmt_count(extract['total_records'])} "
            f"({fmt_delta(extract['total_records'] - previous['extract_records'])}), "
            f"extract {fmt_duration(extract['duration_seconds'])} "
            f"({extract['duration_seconds'] - previous['extract_seconds']:+.1f}s), "
            f"transform {fmt_duration(transform['duration_seconds'])} "
            f"({transform['duration_seconds'] - previous['transform_seconds']:+.1f}s)."
        )
        lines.append("")

    # --- Observations -------------------------------------------------------
    observations: list[str] = []
    for phase_name, metrics in (("extract", extract), ("transform", transform)):
        for dataset in metrics.get("datasets", {}).values():
            observations.extend(f"[{phase_name}] {w}" for w in dataset.get("warnings", []))
        observations.extend(f"[{phase_name}] {w}" for w in metrics.get("warnings", []))
    if previous and previous["extract_records"]:
        delta = extract["total_records"] - previous["extract_records"]
        if abs(delta) / previous["extract_records"] > 0.10:
            observations.append(
                f"record count changed {fmt_delta(delta)} (>10%) vs the "
                "previous run - verify this is expected."
            )
    slowest = max(extract["datasets"].items(), key=lambda kv: kv[1]["seconds"], default=None)
    if slowest and extract["duration_seconds"]:
        share = slowest[1]["seconds"] / extract["duration_seconds"]
        if share > 0.5:
            observations.append(
                f"{slowest[0]} accounts for {share:.0%} of the extract time - "
                "the first candidate if the refresh ever needs to be faster."
            )

    lines.append("Observations:")
    lines.append("")
    if observations:
        lines.extend(f"- {obs}" for obs in observations)
    else:
        lines.append("- clean run, no warnings.")
    lines.append(
        "- structural improvement backlog: docs/pipeline-improvements.md "
        "(transform business logic is tracked separately)."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    extract_metrics_path = os.path.join(METRICS_DIR, "extract.json")
    transform_metrics_path = os.path.join(METRICS_DIR, "transform.json")

    # --- Phase 1: extract ---------------------------------------------------
    extract_argv = ["--metrics-json", extract_metrics_path]
    if args.all_dates:
        extract_argv.append("--all-dates")
    elif args.since:
        extract_argv.extend(["--since", args.since])
    print("==> Phase 1: extract (pull_report_data.py)")
    rc = pull_report_data.main(extract_argv)
    if rc != 0:
        print(f"Full refresh aborted: extract failed (exit {rc}).", file=sys.stderr)
        return rc

    # --- Phase 2: transform ---------------------------------------------------
    transform_argv = ["--metrics-json", transform_metrics_path]
    if args.cutoff:
        transform_argv.extend(["--cutoff", args.cutoff])
    if args.iso_weeks:
        transform_argv.append("--iso-weeks")
    print("\n==> Phase 2: transform (transform_report_data.py)")
    rc = transform_report_data.main(transform_argv)
    if rc != 0:
        print(f"Full refresh aborted: transform failed (exit {rc}).", file=sys.stderr)
        return rc

    # --- Run report -------------------------------------------------------------
    with open(extract_metrics_path, encoding="utf-8") as fh:
        extract = json.load(fh)
    with open(transform_metrics_path, encoding="utf-8") as fh:
        transform = json.load(fh)

    # Sum of the phases (each times itself); orchestrator overhead is
    # negligible and a stubbed/cached phase can't skew the total.
    total_seconds = (
        (args.sync_seconds or 0.0)
        + extract["duration_seconds"]
        + transform["duration_seconds"]
    )
    run = {
        "finished_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sync_seconds": args.sync_seconds,
        "total_seconds": round(total_seconds, 3),
    }
    previous = read_previous_run(HISTORY_PATH)
    report = build_report(run, extract, transform, previous)

    print("\n" + report)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(report + "\n")
    history_entry = {
        "finished_at": run["finished_at"],
        "sync_seconds": args.sync_seconds,
        "extract_records": extract["total_records"],
        "extract_seconds": extract["duration_seconds"],
        "extract_datasets": {
            name: data["records"] for name, data in extract["datasets"].items()
        },
        "transform_seconds": transform["duration_seconds"],
        "fact_rows": transform["tables"].get("report_invoiced", 0),
        "total_seconds": run["total_seconds"],
        "warning_count": sum(
            len(d.get("warnings", [])) for d in extract["datasets"].values()
        )
        + len(transform.get("warnings", [])),
    }
    with open(HISTORY_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(history_entry) + "\n")

    print(f"Report appended to {LOG_PATH}; history in {HISTORY_PATH}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
