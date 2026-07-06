#!/usr/bin/env python3
"""Pull Partners/Contacts (res.partner) from Odoo Online 17 into a CSV file.

Usage:
    python pull_partners.py                       # pull all partners
    python pull_partners.py --limit 5             # pull at most 5 (quick test)
    python pull_partners.py --companies-only      # only companies
    python pull_partners.py --output data/p.csv   # custom output path
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Any

from src.config import ConfigError, load_config
from src.odoo_client import OdooClient, OdooError

# res.partner fields to export. Order here is the CSV column order.
DEFAULT_FIELDS = [
    "id",
    "name",
    "display_name",
    "is_company",
    "email",
    "phone",
    "mobile",
    "street",
    "city",
    "zip",
    "country_id",
    "vat",
    "customer_rank",
    "supplier_rank",
    "category_id",
]


def flatten_value(value: Any) -> Any:
    """Flatten an Odoo field value into something CSV-friendly.

    - many2one values arrive as ``[id, "Display Name"]`` -> keep the name.
    - many2many/one2many values arrive as a list of ids -> join with ";".
    - missing values arrive as ``False`` -> render as an empty string.
    """
    if value is False or value is None:
        return ""
    if isinstance(value, list):
        # many2one: [id, name]
        if len(value) == 2 and isinstance(value[0], int) and isinstance(value[1], str):
            return value[1]
        # many2many / one2many: [id, id, ...]
        return ";".join(str(v) for v in value)
    return value


def write_csv(path: str, fields: list[str], records: list[dict[str, Any]]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: flatten_value(record.get(field)) for field in fields})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull res.partner records from Odoo into CSV.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of partners to pull (default: all).",
    )
    parser.add_argument(
        "--output",
        default="output/partners.csv",
        help="CSV output path (default: output/partners.csv).",
    )
    parser.add_argument(
        "--companies-only",
        action="store_true",
        help="Only pull companies (is_company = True).",
    )
    parser.add_argument(
        "--fields",
        default=None,
        help="Comma-separated list of fields to export (default: a sensible partner set).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Records per JSON-RPC round trip when pulling all (default: 200).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    fields = [f.strip() for f in args.fields.split(",")] if args.fields else DEFAULT_FIELDS
    domain: list[Any] = [["is_company", "=", True]] if args.companies_only else []

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

        if args.limit is not None:
            records = client.search_read(
                "res.partner", domain=domain, fields=fields, limit=args.limit
            )
        else:
            records = list(
                client.search_read_all(
                    "res.partner",
                    domain=domain,
                    fields=fields,
                    batch_size=args.batch_size,
                )
            )
    except OdooError as exc:
        print(f"Odoo error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # network/transport errors
        print(f"Failed to pull partners: {exc}", file=sys.stderr)
        return 1

    write_csv(args.output, fields, records)
    print(f"Wrote {len(records)} partner record(s) to {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
