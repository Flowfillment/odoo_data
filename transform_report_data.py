#!/usr/bin/env python3
"""Transform the Sales Analysis staging CSVs (phase 2) into fact + dimensions.

Python replacement for the workbook's Power Query layer (VTU Report - Sales
Analysis documentation, section 3). Reads the staging CSVs written by
``pull_report_data.py`` plus the manually maintained
``product_template_name.xlsx`` from the input folder, and writes the
ready-to-use report tables:

    report_invoiced.csv     final fact table          (spec section 3.11)
    dim_product.csv         products + Dutch names    (3.6)
    dim_partner.csv         customers                 (3.7)
    dim_currency.csv        currencies                (3.7)
    dim_date.csv            daily calendar            (3.4)
    dim_uom.csv             UoM factor table          (3.2, from the rules file)
    dim_company.csv         company labels            (3.3, from the rules file)
    refresh_date_time.csv   pipeline run timestamp    (replaces 3.1)

Business maintenance data (special_category rules, company mapping, UoM
factors) lives in ``config/transform_rules.json`` - edit that file, not the
code, when the rules change.

Usage:
    python transform_report_data.py                     # output/ -> output/report/
    python transform_report_data.py --cutoff 2024-04-01
    python transform_report_data.py --iso-weeks         # true ISO 8601 week numbers
    python transform_report_data.py --input-dir "C:/Odoo/CSV Library" --output-dir out
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

from src.transform import (
    FACT_COLUMNS,
    TransformError,
    build_dim_company,
    build_dim_currency,
    build_dim_date,
    build_dim_partner,
    build_dim_product,
    build_dim_uom,
    build_fact,
    build_refresh_date_time,
    load_rules,
    read_dutch_names,
    read_staging_csv,
    write_output_csv,
)

DEFAULT_CUTOFF = "2025-04-01"  # the report's hard cutoff (spec section 5.2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform the Sales Analysis staging CSVs (phase 2) into "
        "the fact + dimension tables the report consumes."
    )
    parser.add_argument(
        "--input-dir",
        default="output",
        help="Directory holding the staging CSVs and product_template_name.xlsx "
        "(default: output/).",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join("output", "report"),
        help="Directory the report tables are written to (default: output/report/).",
    )
    parser.add_argument(
        "--cutoff",
        default=DEFAULT_CUTOFF,
        metavar="YYYY-MM-DD",
        help="Accounting-date floor for the fact table and start of dim_date "
        f"(default: {DEFAULT_CUTOFF}, the report's cutoff).",
    )
    parser.add_argument(
        "--iso-weeks",
        action="store_true",
        help="Use true ISO 8601 week numbers in dim_date instead of the legacy "
        "Power Query numbering (changes historical week buckets).",
    )
    parser.add_argument(
        "--rules",
        default=os.path.join("config", "transform_rules.json"),
        help="Rules file with special_category lists, company mapping and UoM "
        "factors (default: config/transform_rules.json).",
    )
    parser.add_argument(
        "--product-names",
        default=None,
        metavar="XLSX",
        help="Path to the manual Dutch product-name mapping "
        "(default: <input-dir>/product_template_name.xlsx).",
    )
    parser.add_argument(
        "--metrics-json",
        default=None,
        metavar="PATH",
        help="Write machine-readable run metrics (table row counts, filter "
        "stats, warnings, duration) to this JSON file. Used by "
        "refresh_report_data.py.",
    )
    args = parser.parse_args(argv)

    try:
        args.cutoff_date = dt.date.fromisoformat(args.cutoff)
    except ValueError:
        parser.error(f"--cutoff must be a YYYY-MM-DD date, got {args.cutoff!r}")

    if args.product_names is None:
        args.product_names = os.path.join(args.input_dir, "product_template_name.xlsx")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = dt.datetime.now()
    started = time.monotonic()
    table_rows: dict[str, int] = {}

    try:
        rules = load_rules(args.rules)
        dutch_names = read_dutch_names(args.product_names)

        headers = read_staging_csv(args.input_dir, "account_move.csv")
        lines = read_staging_csv(args.input_dir, "account_move_line.csv")
        products = read_staging_csv(args.input_dir, "product_template.csv")
        currencies = read_staging_csv(args.input_dir, "res_currency.csv")
        partners = read_staging_csv(args.input_dir, "res_partner.csv")

        fact, stats = build_fact(headers, lines, rules, args.cutoff_date)
        dim_product = build_dim_product(products, dutch_names)

        # Calendar from the cutoff through today (like the legacy dim_date),
        # stretched further only if a fact row somehow lies beyond today.
        calendar_end = generated_at.date()
        if fact:
            max_fact_date = max(dt.date.fromisoformat(r["accounting_date"]) for r in fact)
            calendar_end = max(calendar_end, max_fact_date)
        dim_date = build_dim_date(args.cutoff_date, calendar_end, args.iso_weeks)

        outputs = [
            ("report_invoiced.csv", FACT_COLUMNS, fact),
            (
                "dim_product.csv",
                ("id", "product_id", "display_name", "internal_reference",
                 "prodin_reference", "product_name_english", "product_name_dutch",
                 "prodin_ref_name", "display_name_dutch", "list_price",
                 "standard_price", "report_category_name"),
                dim_product,
            ),
            (
                "dim_partner.csv",
                ("id", "name", "commercial_company_name", "country_id"),
                build_dim_partner(partners),
            ),
            (
                "dim_currency.csv",
                ("id", "currency", "latest_rate", "latest_rate_date", "symbol"),
                build_dim_currency(currencies),
            ),
            (
                "dim_date.csv",
                ("Date", "Year", "MonthName", "MonthNumber", "yyyy-qq",
                 "WeekNumber", "Week_2d", "Year_2d", "YY-WW", "YearWeekKey"),
                dim_date,
            ),
            ("dim_uom.csv", ("uom_id", "name", "factor"), build_dim_uom(rules)),
            (
                "dim_company.csv",
                ("company_id", "company_name"),
                build_dim_company(rules),
            ),
            (
                "refresh_date_time.csv",
                ("refresh_file_datetime", "join_id1"),
                build_refresh_date_time(generated_at),
            ),
        ]
        for name, columns, rows in outputs:
            write_output_csv(os.path.join(args.output_dir, name), columns, rows)
            table_rows[name.removesuffix(".csv")] = len(rows)
            print(f"  {name}: {len(rows)} row(s)")
    except TransformError as exc:
        print(f"Transform error: {exc}", file=sys.stderr)
        return 2

    dropped = stats.headers_not_posted + stats.headers_before_cutoff
    print(
        f"\nFact: kept {stats.headers_kept}/{stats.headers_total} invoice(s) "
        f"({stats.headers_not_posted} not posted, {stats.headers_before_cutoff} "
        f"before {args.cutoff}) and {stats.lines_kept}/{stats.lines_total} "
        f"line(s) ({stats.lines_total - stats.lines_kept} on dropped invoices)."
        if dropped or stats.lines_total != stats.lines_kept
        else f"\nFact: kept all {stats.headers_kept} invoice(s) and "
        f"{stats.lines_kept} line(s)."
    )

    products_with_dutch = sum(1 for p in dim_product if p["product_name_dutch"])
    print(
        f"Dutch names: matched {products_with_dutch}/{len(dim_product)} product(s) "
        f"from {os.path.basename(args.product_names)} ({len(dutch_names)} mapping row(s))."
    )

    warnings: list[str] = []
    if stats.unknown_company_ids:
        warnings.append(
            f"company id(s) without a mapping in {args.rules}: "
            f"{sorted(stats.unknown_company_ids)} - raw display name(s) kept. "
            "Add them to the rules file."
        )
    if stats.missing_uom_ids:
        listed = ", ".join(
            f"{uom_id} ({name or 'unknown'})" for uom_id, name in sorted(stats.missing_uom_ids.items())
        )
        warnings.append(
            f"UoM id(s) without a factor in {args.rules}: {listed} - "
            "quantities for these lines were left unconverted. Add the factors "
            "from the workbook's dim_uom query to the rules file."
        )
    for warning in warnings:
        print(f"  WARNING: {warning}", file=sys.stderr)

    if args.metrics_json:
        metrics = {
            "phase": "transform",
            "started_at": generated_at.strftime("%Y-%m-%d %H:%M:%S"),
            "cutoff": args.cutoff,
            "duration_seconds": round(time.monotonic() - started, 3),
            "tables": table_rows,
            "fact": {
                "headers_total": stats.headers_total,
                "headers_kept": stats.headers_kept,
                "headers_not_posted": stats.headers_not_posted,
                "headers_before_cutoff": stats.headers_before_cutoff,
                "lines_total": stats.lines_total,
                "lines_kept": stats.lines_kept,
            },
            "dutch_names": {
                "products_matched": products_with_dutch,
                "products_total": len(dim_product),
                "mapping_rows": len(dutch_names),
            },
            "warnings": warnings,
        }
        directory = os.path.dirname(args.metrics_json)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.metrics_json, "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=2)

    print(f"Done. Report tables written to {args.output_dir}/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
