"""Dataset specifications for the VTU Sales Analysis staging CSVs (phase 1).

Each spec maps one Odoo model to one CSV file of the report's source
contract (VTU Report - Sales Analysis documentation, section 2). CSV column
names follow that contract exactly, so the downstream transform (phase 2)
can be built against a stable, documented schema.

Rendering conventions per column:

- ``m2o_raw``:  many2one rendered as ``[id,"Display Name"]`` — the same shape
  the report's transform layer expects to split itself (e.g. ``PartnerID``,
  ``company_id``, ``product_id``).
- ``m2o_id``:   numeric id only (e.g. ``CurrencyID``).
- ``m2o_name``: display name only (e.g. ``account_id`` ->
  ``"800550 Omzet NL Verhuur"``).
- ``scalar``:   value as returned by Odoo; ``False``/``None`` -> empty string.

Deliberate differences vs. the legacy Power Automate CSVs (documented in the
report's "Notes & Quirks"): every file is UTF-8 with standard CSV quoting
(no Windows-1252, no unnamed junk columns), and empty values are written as
empty strings instead of the literal text ``"False"``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

RENDERERS = ("scalar", "m2o_raw", "m2o_id", "m2o_name")


@dataclass(frozen=True)
class Column:
    """One CSV column: header name, source Odoo field, and how to render it."""

    name: str
    source: str
    render: str = "scalar"

    def __post_init__(self) -> None:
        if self.render not in RENDERERS:
            raise ValueError(f"Unknown renderer {self.render!r} for column {self.name!r}")


@dataclass(frozen=True)
class Dataset:
    """One staging CSV: the Odoo model it comes from and its column layout.

    ``date_field`` names the Odoo field a ``--since`` filter applies to;
    datasets without one (dimensions) are always pulled in full.
    """

    name: str
    model: str
    columns: tuple[Column, ...]
    date_field: str | None = None
    static_domain: tuple[Any, ...] = field(default=())

    @property
    def csv_name(self) -> str:
        return f"{self.name}.csv"

    @property
    def source_fields(self) -> list[str]:
        """Unique Odoo fields to request, in first-use order."""
        seen: dict[str, None] = {}
        for column in self.columns:
            seen.setdefault(column.source)
        return list(seen)

    def domain(self, since: str | None) -> list[Any]:
        domain = list(self.static_domain)
        if since and self.date_field:
            domain.append([self.date_field, ">=", since])
        return domain


def render_value(value: Any, render: str) -> Any:
    """Render one Odoo field value for CSV output.

    Odoo returns many2one fields as ``[id, "Display Name"]`` and empty fields
    of any type as ``False``.
    """
    if value is False or value is None:
        return ""
    is_m2o = isinstance(value, list) and len(value) == 2
    if render == "m2o_raw":
        if is_m2o:
            # json.dumps gives the exact [id,"Name"] shape (quotes escaped).
            return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
        return value
    if render == "m2o_id":
        return value[0] if is_m2o else value
    if render == "m2o_name":
        return value[1] if is_m2o else value
    return value


# --- Source contract (documentation section 2) ------------------------------

ACCOUNT_MOVE = Dataset(
    # 2.1 invoice headers. The transform filters State/accounting_date itself,
    # so this stays a raw export (server-side --since only trims volume).
    name="account_move",
    model="account.move",
    date_field="date",
    columns=(
        Column("account_move_id", "id"),
        Column("Name", "name"),
        Column("PartnerID", "partner_id", "m2o_raw"),
        Column("CurrencyID", "currency_id", "m2o_id"),
        Column("CurrencyValue", "currency_id", "m2o_name"),
        Column("State", "state"),
        Column("accounting_date", "date"),
        Column("company_id", "company_id", "m2o_raw"),
        Column("InvoiceDate", "invoice_date"),
        Column("AmountTotal", "amount_total"),
        Column("Currency", "currency_id", "m2o_name"),
        Column("WriteDate", "write_date"),
        Column("move_type", "move_type"),
    ),
)

ACCOUNT_MOVE_LINE = Dataset(
    # 2.2 invoice lines. Only the fields the transform actually consumes;
    # the legacy file's four unreferenced trailing columns are dropped.
    name="account_move_line",
    model="account.move.line",
    date_field="date",
    columns=(
        Column("account_move_id", "move_id", "m2o_raw"),
        Column("account_id", "account_id", "m2o_name"),
        Column("debit", "debit"),
        Column("credit", "credit"),
        Column("balance", "balance"),
        Column("quantity", "quantity"),
        Column("product_uom_id", "product_uom_id", "m2o_raw"),
        Column("product_id", "product_id", "m2o_raw"),
        Column("currency_rate", "currency_rate"),
        Column("price_subtotal", "price_subtotal"),
        Column("price_unit", "price_unit"),
        Column("company_currency_id", "company_currency_id", "m2o_raw"),
    ),
)

PRODUCT_TEMPLATE = Dataset(
    # 2.3 products. product_id is the variant id: the fact table's
    # relationship key. prodin_reference / report_category are custom
    # fields — pull_report_data warns and emits them empty if absent.
    name="product_template",
    model="product.template",
    columns=(
        Column("id", "id"),
        Column("product_id", "product_variant_id", "m2o_id"),
        Column("display_name", "display_name"),
        Column("prodin_reference", "prodin_reference"),
        Column("list_price", "list_price"),
        Column("standard_price", "standard_price"),
        Column("report_category_name", "report_category", "m2o_name"),
    ),
)

RES_CURRENCY = Dataset(
    # 2.4 currencies. Odoo's computed `rate`/`date` are the latest known
    # rate and its date; the legacy junk columns are not reproduced.
    name="res_currency",
    model="res.currency",
    columns=(
        Column("id", "id"),
        Column("currency", "name"),
        Column("latest_rate", "rate"),
        Column("latest_rate_date", "date"),
        Column("symbol", "symbol"),
    ),
)

RES_PARTNER = Dataset(
    # 2.5 customers. `name` rides along for human readability; the report
    # itself uses id, commercial_company_name and country_id.
    name="res_partner",
    model="res.partner",
    columns=(
        Column("id", "id"),
        Column("name", "name"),
        Column("commercial_company_name", "commercial_company_name"),
        Column("country_id", "country_id", "m2o_raw"),
    ),
)

DATASETS: dict[str, Dataset] = {
    dataset.name: dataset
    for dataset in (
        ACCOUNT_MOVE,
        ACCOUNT_MOVE_LINE,
        PRODUCT_TEMPLATE,
        RES_CURRENCY,
        RES_PARTNER,
    )
}
