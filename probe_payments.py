#!/usr/bin/env python3
"""One-off phase 4 exploration: how does this Odoo instance expose payment data?

The customer report needs the average time between invoice date and
payment. That date lives in the reconciliation between the invoice's
receivable line and the payment, not on the invoice itself. This script
checks, read-only, what the live instance offers, so the payments extract
can be designed on facts instead of assumptions:

1. Do the phase-4 fields exist on account.move / account.move.line /
   res.partner / sale.order(.line)? (The pull warns too, but this shows it
   in one overview.)
2. What does account.partial.reconcile look like, and does its max_date
   give a usable payment date for a sample of paid customer invoices?

Run it once and share the output:

    python probe_payments.py
"""

from __future__ import annotations

import datetime as dt
import sys

from src.config import ConfigError, load_config
from src.odoo_client import OdooClient, OdooError

EXPECTED_FIELDS = {
    "account.move": [
        "amount_residual", "payment_state", "invoice_date_due",
        "invoice_payment_term_id",
    ],
    "account.move.line": ["discount"],
    "res.partner": ["property_payment_term_id"],
    "sale.order": ["invoice_status", "payment_term_id"],
    "sale.order.line": [
        "qty_delivered", "qty_invoiced", "discount",
        "untaxed_amount_invoiced", "untaxed_amount_to_invoice",
        "display_type",
    ],
    "account.partial.reconcile": [
        "debit_move_id", "credit_move_id", "amount", "max_date",
    ],
}


def check_fields(client: OdooClient) -> None:
    print("== 1. Field availability ==")
    for model, wanted in EXPECTED_FIELDS.items():
        try:
            available = set(client.execute_kw(model, "fields_get", [], {"attributes": []}))
        except OdooError as exc:
            print(f"  {model}: MODEL NOT ACCESSIBLE ({exc})")
            continue
        missing = [f for f in wanted if f not in available]
        status = "all present" if not missing else f"MISSING: {missing}"
        print(f"  {model}: {status}")


def probe_reconciles(client: OdooClient) -> None:
    print("\n== 2. Payment dates via account.partial.reconcile ==")
    invoices = client.search_read(
        "account.move",
        domain=[
            ["move_type", "=", "out_invoice"],
            ["payment_state", "=", "paid"],
            ["date", ">=", "2025-04-01"],
        ],
        fields=["id", "name", "invoice_date", "amount_total", "payment_state"],
        limit=3,
        order="id desc",
    )
    if not invoices:
        print("  No paid customer invoices found - nothing to probe.")
        return

    for invoice in invoices:
        print(f"\n  Invoice {invoice['name']} (id {invoice['id']}), "
              f"invoice_date={invoice['invoice_date']}, total={invoice['amount_total']}:")
        # The invoice's receivable line is the debit side; the payment's
        # line is the credit side of the partial reconcile.
        partials = client.search_read(
            "account.partial.reconcile",
            domain=[["debit_move_id.move_id", "=", invoice["id"]]],
            fields=["id", "amount", "max_date", "debit_move_id", "credit_move_id"],
            limit=10,
        )
        if not partials:
            print("    no partial reconciles found via debit_move_id.move_id "
                  "(unexpected for a paid invoice - flag this).")
            continue
        for partial in partials:
            days = ""
            if invoice["invoice_date"] and partial.get("max_date"):
                delta = (
                    dt.date.fromisoformat(partial["max_date"])
                    - dt.date.fromisoformat(invoice["invoice_date"])
                ).days
                days = f"  -> {delta} day(s) after invoice date"
            print(f"    reconcile {partial['id']}: amount={partial['amount']}, "
                  f"max_date={partial.get('max_date')}{days}")
            print(f"      credit side: {partial.get('credit_move_id')}")

    total = client.execute_kw(
        "account.partial.reconcile", "search_count",
        [[["max_date", ">=", "2025-04-01"]]],
    )
    print(f"\n  Volume check: {total} partial reconcile(s) since 2025-04-01.")


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    client = OdooClient(config.url, config.db, config.username, config.api_key)
    try:
        uid = client.authenticate()
        print(f"Authenticated to {config.url} (db={config.db}) as uid={uid}.\n")
        check_fields(client)
        probe_reconciles(client)
    except OdooError as exc:
        print(f"Odoo error: {exc}", file=sys.stderr)
        return 1
    print("\nDone - share this output to design the payments extract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
