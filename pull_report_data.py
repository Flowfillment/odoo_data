#!/usr/bin/env python3
"""Pull the VTU Sales Analysis staging CSVs (phase 1) from Odoo Online 17.

Exports the five Odoo-sourced CSV files of the report's source contract
(VTU Report - Sales Analysis documentation, section 2) to a local folder:

    account_move.csv        invoice headers   (account.move)
    account_move_line.csv   invoice lines     (account.move.line)
    product_template.csv    products          (product.template)
    res_currency.csv        currencies        (res.currency)
    res_partner.csv         customers         (res.partner)

The sixth source, product_template_name.xlsx, is a manually maintained
mapping file and is intentionally not pulled from Odoo. It lives alongside
the staging CSVs (output/product_template_name.xlsx); this script only
writes the five CSV files above and never deletes other files there.

This is staging only: no filtering on invoice state, no joins, no derived
columns — that is the transform (phase 2). The one exception is the
server-side ``--since`` date filter on the two account.move datasets, which
exists purely to keep the export volume sane. It defaults to 2025-04-01,
the report's own hard cutoff, so nothing the transform needs is lost.

Usage:
    python pull_report_data.py                          # all five CSVs
    python pull_report_data.py --only res_partner
    python pull_report_data.py --only account_move,account_move_line
    python pull_report_data.py --limit 5                # quick smoke test
    python pull_report_data.py --since 2024-01-01       # widen the window
    python pull_report_data.py --all-dates              # no date filter
    python pull_report_data.py --output-dir "C:/Odoo/CSV Library"
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from typing import Any

from src.config import ConfigError, load_config
from src.datasets import DATASETS, Dataset, render_value
from src.odoo_client import OdooClient, OdooError

DEFAULT_SINCE = "2025-04-01"


def write_csv(path: str, dataset: Dataset, records: list[dict[str, Any]]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([column.name for column in dataset.columns])
        for record in records:
            writer.writerow(
                [render_value(record.get(column.source), column.render) for column in dataset.columns]
            )


def missing_fields(client: OdooClient, dataset: Dataset) -> set[str]:
    """Fields in the spec that this Odoo instance doesn't have (custom
    fields like prodin_reference may be absent) — minus ``id``, which
    fields_get doesn't list but every model has."""
    available = set(
        client.execute_kw(dataset.model, "fields_get", [], {"attributes": []})
    )
    return {f for f in dataset.source_fields if f != "id" and f not in available}


def pull_dataset(
    client: OdooClient,
    dataset: Dataset,
    since: str | None,
    limit: int | None,
    batch_size: int,
    output_dir: str,
) -> dict[str, Any]:
    started = time.monotonic()
    warnings: list[str] = []
    missing = missing_fields(client, dataset)
    if missing:
        warnings.append(
            f"{dataset.model} is missing field(s) {sorted(missing)}; "
            "the column(s) will be written empty."
        )
        print(f"  WARNING: {warnings[-1]}", file=sys.stderr)
    fields = [f for f in dataset.source_fields if f not in missing]

    domain = dataset.domain(since)
    if limit is not None:
        records = client.search_read(
            dataset.model, domain=domain, fields=fields, limit=limit, order="id"
        )
    else:
        records = list(
            client.search_read_all(
                dataset.model, domain=domain, fields=fields, batch_size=batch_size
            )
        )

    path = os.path.join(output_dir, dataset.csv_name)
    write_csv(path, dataset, records)

    window = f", {dataset.date_field} >= {since}" if since and dataset.date_field else ""
    seconds = time.monotonic() - started
    print(
        f"  {dataset.csv_name}: {len(records)} record(s) from {dataset.model}{window} "
        f"({seconds:.1f}s)"
    )
    return {"records": len(records), "seconds": round(seconds, 3), "warnings": warnings}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull the Sales Analysis staging CSVs (phase 1) from Odoo."
    )
    parser.add_argument(
        "--only",
        default=None,
        metavar="NAMES",
        help="Comma-separated dataset names to pull (default: all). "
        f"Choices: {', '.join(DATASETS)}.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory the CSV files are written to (default: output/).",
    )
    parser.add_argument(
        "--since",
        default=DEFAULT_SINCE,
        metavar="YYYY-MM-DD",
        help="Server-side accounting-date floor for account_move and "
        f"account_move_line (default: {DEFAULT_SINCE}, the report's cutoff).",
    )
    parser.add_argument(
        "--all-dates",
        action="store_true",
        help="Disable the --since date filter and pull the full history.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum records per dataset (quick smoke test; default: all).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Records per JSON-RPC round trip when pulling all (default: 500).",
    )
    parser.add_argument(
        "--metrics-json",
        default=None,
        metavar="PATH",
        help="Write machine-readable run metrics (per-dataset record counts, "
        "durations, warnings) to this JSON file. Used by refresh_report_data.py.",
    )
    args = parser.parse_args(argv)

    if args.since != DEFAULT_SINCE:
        try:
            dt.date.fromisoformat(args.since)
        except ValueError:
            parser.error(f"--since must be a YYYY-MM-DD date, got {args.since!r}")

    if args.only:
        names = [name.strip() for name in args.only.split(",") if name.strip()]
        unknown = [name for name in names if name not in DATASETS]
        if unknown:
            parser.error(
                f"Unknown dataset(s): {', '.join(unknown)}. Choices: {', '.join(DATASETS)}."
            )
        args.datasets = [DATASETS[name] for name in names]
    else:
        args.datasets = list(DATASETS.values())

    return args


def write_metrics(path: str, metrics: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    since = None if args.all_dates else args.since
    started = time.monotonic()
    metrics: dict[str, Any] = {
        "phase": "extract",
        "started_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "since": since,
        "datasets": {},
    }

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    client = OdooClient(
        url=config.url,
        db=config.db,
        username=config.username,
        api_key=config.api_key,
    )

    try:
        uid = client.authenticate()
        print(f"Authenticated to {config.url} (db={config.db}) as uid={uid}.")

        total = 0
        for dataset in args.datasets:
            result = pull_dataset(
                client,
                dataset,
                since=since,
                limit=args.limit,
                batch_size=args.batch_size,
                output_dir=args.output_dir,
            )
            metrics["datasets"][dataset.name] = result
            total += result["records"]
    except OdooError as exc:
        print(f"Odoo error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # network/transport errors
        print(f"Failed to pull report data: {exc}", file=sys.stderr)
        return 1

    metrics["total_records"] = total
    metrics["duration_seconds"] = round(time.monotonic() - started, 3)
    if args.metrics_json:
        write_metrics(args.metrics_json, metrics)

    print(f"Done. {total} record(s) across {len(args.datasets)} file(s) in {args.output_dir}/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
