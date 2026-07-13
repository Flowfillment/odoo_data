"""Phase 4: per-customer KPI computation and report rendering.

Computes the customer-report KPI set (roadmap phase 4) from the pipeline
outputs and renders a 2-3 page A4 HTML report in two variants:

- ``customer``: for the customer - contains **no margin data whatsoever**.
  Margins are excluded here, at data-assembly level, so no template change
  can leak them.
- ``internal``: for sales - includes margins.

KPI definitions (anchor: the validated phase-2 model):

- Turnover           = sum of fact ``price_subtotal_eur``
- Invoiced Amount    = sum of fact ``Invoiced Amount`` (= -balance, EUR)
- Gross margin       = verbatim Python port of the workbook's Gross Profit
                       DAX (spec section 4.3): rental lines carry zero
                       cost; lines whose product has no cost relationship
                       count as full revenue; reversal lines (debit != 0)
                       add the cost back
- Order backlog      = confirmed sale orders (state sale/done):
                       ordered vs delivered quantities and
                       invoiced vs to-invoice amounts (untaxed)
- Open invoices      = posted out_invoice/out_refund with payment_state
                       not_paid/partial: residual per invoice, grouped per
                       currency, overdue flagged against the report date
- Discounts          = subtotal-weighted average discount %% and share of
                       lines carrying a discount (invoice lines, with the
                       order lines as secondary source)
- Days to payment    = only when ``output/payments.csv`` exists
                       (invoice_move_id, payment_date, amount) - produced
                       by the payments extract once the reconciliation
                       probe has confirmed the data shape.
"""

from __future__ import annotations

import datetime as dt
import html
import os
from dataclasses import dataclass, field
from typing import Any

from src.transform import TransformError, parse_m2o, read_staging_csv, to_float, to_int

REPORT_DIR = "report"  # under the input dir


# --- Number formatting (Dutch conventions) ------------------------------------


def eur(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "–"
    text = f"{value:,.{decimals}f}"
    return "€ " + text.replace(",", " ").replace(".", ",").replace(" ", ".")


def num(value: float | None, decimals: int = 0) -> str:
    if value is None:
        return "–"
    text = f"{value:,.{decimals}f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


def pct(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "–"
    return num(value * 100, decimals) + "%"


# --- Customer selection ---------------------------------------------------------


@dataclass
class Customer:
    ids: set[int]
    label: str
    payment_terms: list[str]


def find_customer(partners: list[dict[str, str]], query: str) -> Customer | list[str]:
    """Match a customer by id or name (case-insensitive substring).

    Returns the Customer, or a list of candidate labels when the match is
    ambiguous. All partner records sharing the matched commercial name are
    included, so invoices booked on contacts roll up to the company.
    """
    query_l = query.strip().lower()

    if query_l.isdigit():
        matches = [p for p in partners if p["id"] == query_l]
    else:
        matches = [
            p for p in partners
            if query_l in (p["commercial_company_name"] or "").lower()
            or query_l in (p["name"] or "").lower()
        ]
    if not matches:
        return []

    labels = sorted({p["commercial_company_name"] or p["name"] for p in matches})
    if len(labels) > 1:
        return labels

    label = labels[0]
    group = [
        p for p in partners
        if (p["commercial_company_name"] or p["name"]) == label
    ]
    terms = sorted({p["payment_term"] for p in group if p.get("payment_term")})
    return Customer(ids={int(p["id"]) for p in group}, label=label, payment_terms=terms)


# --- KPI computation -------------------------------------------------------------


def margin_per_line(row: dict[str, Any], standard_prices: dict[int, float | None]) -> float:
    """Verbatim port of the Gross Profit DAX (spec section 4.3)."""
    balance = to_float(row["balance"]) or 0.0
    revenue = -balance
    if row["special_category"] == "Rental Order":
        standard_price: float | None = 0.0
    else:
        product_id = to_int(row["product_id"])
        standard_price = standard_prices.get(product_id) if product_id is not None else None
    if standard_price is None:  # ISBLANK(StandardPrice) -> full revenue
        return revenue
    qty = to_float(row["quantity_product_uom"]) or 0.0
    cost = standard_price * qty
    debit = to_float(row["debit"]) or 0.0
    return revenue - cost if debit == 0 else revenue + cost


@dataclass
class Kpis:
    """All computed values; margin fields are stripped for the customer variant."""

    customer: Customer
    generated_at: dt.datetime
    period: str
    # revenue
    turnover: float = 0.0
    invoiced: float = 0.0
    products: list[dict[str, Any]] = field(default_factory=list)  # per product
    # margins (internal only)
    gross_profit: float | None = None
    margin_pct: float | None = None
    # orders
    qty_ordered: float = 0.0
    qty_delivered: float = 0.0
    amount_invoiced_orders: float = 0.0
    amount_to_invoice: float = 0.0
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    # invoices & payment
    payment_terms: list[str] = field(default_factory=list)
    open_invoices: list[dict[str, Any]] = field(default_factory=list)
    open_per_currency: dict[str, float] = field(default_factory=dict)
    overdue_count: int = 0
    avg_days_to_pay: float | None = None
    paid_invoice_count: int = 0
    # discounts
    avg_discount: float | None = None
    discounted_share: float | None = None


def compute_kpis(input_dir: str, customer: Customer, today: dt.date) -> Kpis:
    report_dir = os.path.join(input_dir, REPORT_DIR)
    fact = read_staging_csv(report_dir, "report_invoiced.csv")
    dim_product = read_staging_csv(report_dir, "dim_product.csv")
    moves = read_staging_csv(input_dir, "account_move.csv")
    move_lines = read_staging_csv(input_dir, "account_move_line.csv")
    orders = read_staging_csv(input_dir, "sale_order.csv")
    order_lines = read_staging_csv(input_dir, "sale_order_line.csv")

    kpis = Kpis(
        customer=customer,
        generated_at=dt.datetime.now(),
        period="april 2025 – heden",
        payment_terms=customer.payment_terms,
    )

    products_by_id = {to_int(p["product_id"]): p for p in dim_product}
    standard_prices = {
        pid: to_float(p["standard_price"]) for pid, p in products_by_id.items()
    }

    # --- Revenue + margins from the validated fact -----------------------
    per_product: dict[Any, dict[str, Any]] = {}
    rows = [r for r in fact if to_int(r["PartnerID"]) in customer.ids]
    for row in rows:
        kpis.turnover += to_float(row["price_subtotal_eur"]) or 0.0
        kpis.invoiced += to_float(row["Invoiced Amount"]) or 0.0

        product_id = to_int(row["product_id"])
        product = products_by_id.get(product_id)
        key = product_id if product else f"cat:{row['special_category']}"
        bucket = per_product.setdefault(
            key,
            {
                "name": (product or {}).get("display_name_dutch")
                or f"({row['special_category']})",
                "category": (product or {}).get("report_category_name", "Z. Category N/A"),
                "qty": 0.0,
                "turnover": 0.0,
                "margin": 0.0,
            },
        )
        bucket["qty"] += to_float(row["quantity"]) or 0.0
        bucket["turnover"] += to_float(row["price_subtotal_eur"]) or 0.0
        bucket["margin"] += margin_per_line(row, standard_prices)

    kpis.products = sorted(per_product.values(), key=lambda b: -b["turnover"])
    kpis.gross_profit = sum(b["margin"] for b in per_product.values())
    revenue = kpis.invoiced
    kpis.margin_pct = (kpis.gross_profit / revenue) if abs(revenue) >= 0.01 else 0.0

    # --- Orders: delivered / invoiced / backlog ---------------------------
    confirmed: dict[int | None, dict[str, str]] = {}
    for order in orders:
        partner_id, _ = parse_m2o(order["partner_id"])
        if order["state"] in ("sale", "done") and partner_id in customer.ids:
            confirmed[to_int(order["id"])] = order
    for line in order_lines:
        order_id, order_name = parse_m2o(line["order_id"])
        order = confirmed.get(order_id)
        if order is None:
            continue
        kpis.qty_ordered += to_float(line["product_uom_qty"]) or 0.0
        kpis.qty_delivered += to_float(line["qty_delivered"]) or 0.0
        kpis.amount_invoiced_orders += to_float(line["untaxed_amount_invoiced"]) or 0.0
        to_invoice = to_float(line["untaxed_amount_to_invoice"]) or 0.0
        kpis.amount_to_invoice += to_invoice
        if to_invoice > 0.005:
            open_order = next(
                (o for o in kpis.open_orders if o["name"] == order_name), None
            )
            if open_order is None:
                open_order = {"name": order_name, "date": order["date_order"][:10],
                              "to_invoice": 0.0}
                kpis.open_orders.append(open_order)
            open_order["to_invoice"] += to_invoice
    kpis.open_orders.sort(key=lambda o: -o["to_invoice"])

    # --- Open invoices ------------------------------------------------------
    for move in moves:
        if move["State"] != "posted" or move["move_type"] not in ("out_invoice", "out_refund"):
            continue
        if parse_m2o(move["PartnerID"])[0] not in customer.ids:
            continue
        if move.get("payment_state") not in ("not_paid", "partial"):
            continue
        residual = to_float(move.get("amount_residual", "")) or 0.0
        if abs(residual) < 0.005:
            continue
        if move["move_type"] == "out_refund":
            residual = -residual
        due = (move.get("invoice_date_due") or "")[:10]
        overdue = bool(due) and dt.date.fromisoformat(due) < today
        kpis.open_invoices.append(
            {
                "name": move["Name"],
                "date": move["accounting_date"][:10],
                "due": due or "–",
                "currency": move["CurrencyValue"] or "EUR",
                "residual": residual,
                "overdue": overdue,
                "term": move.get("payment_term") or "",
            }
        )
        kpis.open_per_currency[move["CurrencyValue"] or "EUR"] = (
            kpis.open_per_currency.get(move["CurrencyValue"] or "EUR", 0.0) + residual
        )
        kpis.overdue_count += int(overdue)
    kpis.open_invoices.sort(key=lambda i: i["date"])

    # --- Discounts (invoice lines of this customer's posted invoices) --------
    customer_moves = {
        to_int(m["account_move_id"]) for m in moves
        if m["State"] == "posted" and parse_m2o(m["PartnerID"])[0] in customer.ids
    }
    weighted = total_weight = discounted = line_count = 0.0
    for line in move_lines:
        if parse_m2o(line["account_move_id"])[0] not in customer_moves:
            continue
        discount = to_float(line.get("discount", "")) or 0.0
        subtotal = abs(to_float(line["price_subtotal"]) or 0.0)
        gross = subtotal / (1 - discount / 100) if discount < 100 else subtotal
        weighted += gross * discount / 100
        total_weight += gross
        line_count += 1
        discounted += int(discount > 0)
    if line_count:
        kpis.avg_discount = (weighted / total_weight) if total_weight else 0.0
        kpis.discounted_share = discounted / line_count

    # --- Days to payment (optional, needs the payments extract) ---------------
    payments_path = os.path.join(input_dir, "payments.csv")
    if os.path.exists(payments_path):
        payments = read_staging_csv(input_dir, "payments.csv")
        invoice_dates = {
            to_int(m["account_move_id"]): (m["InvoiceDate"] or m["accounting_date"])[:10]
            for m in moves
            if m["State"] == "posted" and parse_m2o(m["PartnerID"])[0] in customer.ids
        }
        total_days = paid = 0
        for payment in payments:
            invoice_id = to_int(payment["invoice_move_id"])
            invoice_date = invoice_dates.get(invoice_id)
            if not invoice_date or not payment["payment_date"]:
                continue
            total_days += (
                dt.date.fromisoformat(payment["payment_date"][:10])
                - dt.date.fromisoformat(invoice_date)
            ).days
            paid += 1
        if paid:
            kpis.avg_days_to_pay = total_days / paid
            kpis.paid_invoice_count = paid

    return kpis


# --- Rendering --------------------------------------------------------------------

# Brand (Van Thiel United brandbook): PANTONE 2955 C = #003865 (navy),
# PANTONE 285 C = #0072CE (blue); grey tints max 30% black; heads in
# condensed bold (Oswald-equivalent); body 10pt; blue logo bar at the
# bottom of every page.
NAVY = "#003865"
BLUE = "#0072CE"

CSS = f"""
@page {{ size: A4; margin: 0; }}
* {{ box-sizing: border-box; -webkit-print-color-adjust: exact;
     print-color-adjust: exact; }}
body {{ font-family: Calibri, Carlito, 'Segoe UI', Arial, sans-serif;
       color: #1a1a1a; font-size: 10pt; line-height: 1.45; margin: 0; }}
h1, h2, .head {{ font-family: Oswald, 'Arial Narrow', 'Liberation Sans Narrow',
       Arial, sans-serif; font-weight: 700; text-transform: uppercase; }}
/* page frame: thead/tfoot spacers repeat per printed page */
table.frame {{ width: 100%; border-collapse: collapse; }}
td.frame-space {{ height: 10mm; padding: 0; }}
td.frame-foot {{ height: 18mm; padding: 0; }}
td.frame-body {{ padding: 0 14mm; }}
/* bottom brand bar, repeated on every page */
.botbar {{ position: fixed; left: 0; right: 0; bottom: 0; height: 12mm;
          background: {BLUE}; color: #fff; }}
.botbar .in {{ display: flex; justify-content: space-between;
              align-items: center; height: 100%; padding: 0 14mm; }}
.botbar .brand {{ font-family: Oswald, 'Arial Narrow', Arial, sans-serif;
                 font-weight: 700; letter-spacing: 1px; font-size: 11pt; }}
.botbar .site {{ font-size: 8pt; }}
/* page-1 banner (bleeds to the paper edges) */
.banner {{ background: {NAVY}; color: #fff; margin: -10mm -14mm 0;
          padding: 10mm 14mm 7mm; }}
.banner .title {{ font-size: 21pt; letter-spacing: .5px; margin: 0; }}
.banner .customer {{ color: #9DC6E8; font-size: 14pt; margin: 1mm 0 4mm; }}
.banner .meta {{ font-size: 9pt; color: #fff; }}
.banner .meta .sep {{ color: #9DC6E8; padding: 0 6px; }}
.banner .logo {{ float: right; margin-left: 8mm; }}
.banner .logo img {{ height: 16mm; display: block; }}
.badge {{ display: inline-block; padding: 1px 10px; border-radius: 3px;
         font-weight: 700; font-size: 8.5pt; letter-spacing: .5px; }}
.badge.internal {{ background: #7a1f1f; color: #fff; }}
.badge.customer {{ background: {BLUE}; color: #fff; }}
h2 {{ font-size: 13pt; margin: 22px 0 6px; color: {BLUE};
     letter-spacing: .5px; page-break-after: avoid; }}
.tiles {{ display: flex; gap: 8px; margin: 8mm 0 2mm; }}
.tile {{ flex: 1 1 0; min-width: 0; border: 1px solid #D9D9D9;
        border-top: 3px solid {BLUE}; padding: 8px 9px; }}
.tile .v {{ font-size: 12pt; font-weight: 650; white-space: nowrap;
           color: {NAVY}; }}
.tile .l {{ font-size: 7.5pt; color: {NAVY}; text-transform: uppercase;
           letter-spacing: .4px; }}
table {{ width: 100%; border-collapse: collapse; margin: 6px 0; }}
th {{ text-align: left; font-size: 8.5pt; text-transform: uppercase;
     letter-spacing: .3px; color: #fff; background: {NAVY};
     padding: 4px 6px; }}
td {{ padding: 3.5px 6px; border-bottom: 1px solid #D9D9D9; }}
td.n, th.n {{ text-align: right; font-variant-numeric: tabular-nums; }}
tr.cat td {{ background: #EDEDED; font-weight: 600; }}
tr.total td {{ border-top: 2px solid {NAVY}; border-bottom: none;
              font-weight: 650; }}
.overdue {{ color: #a11212; font-weight: 600; }}
.note {{ color: #1a1a1a; font-size: 8.5pt; font-style: italic; }}
.footer {{ margin-top: 26px; padding-top: 6px; border-top: 1px solid #D9D9D9;
          color: #1a1a1a; font-size: 8pt; }}
"""


def esc(value: Any) -> str:
    return html.escape(str(value))


def render_html(kpis: Kpis, internal: bool, logo_data_uri: str | None = None) -> str:
    """Render the report. For the customer variant every margin value is
    absent from the produced document (data-level exclusion)."""
    c: list[str] = []
    variant_badge = (
        '<span class="badge internal">INTERN – VERTROUWELIJK</span>'
        if internal else '<span class="badge customer">Klantrapportage</span>'
    )
    logo = (
        f'<span class="logo"><img src="{logo_data_uri}" alt="logo"></span>'
        if logo_data_uri else ""
    )
    c.append(
        f'<div class="banner">{logo}'
        f'<h1 class="title">Klantrapportage</h1>'
        f'<div class="customer head">{esc(kpis.customer.label)}</div>'
        f'<div class="meta">Periode: {esc(kpis.period)}<span class="sep">|</span>'
        f"Datum: {kpis.generated_at.strftime('%d-%m-%Y')}"
        f'<span class="sep">|</span>{variant_badge}</div>'
        f"</div>"
    )

    # Tiles
    tiles = [
        ("Omzet (EUR)", eur(kpis.turnover, 0)),
        ("Gefactureerd (EUR)", eur(kpis.invoiced, 0)),
        ("Nog te factureren", eur(kpis.amount_to_invoice, 0)),
        ("Openstaand", " / ".join(
            f"{eur(v, 0)} {esc(cur)}" if cur != "EUR" else eur(v, 0)
            for cur, v in kpis.open_per_currency.items()) or eur(0, 0)),
    ]
    if internal:
        tiles.insert(2, ("Brutomarge", eur(kpis.gross_profit, 0)))
        tiles.insert(3, ("Marge %", pct(kpis.margin_pct)))
    c.append('<div class="tiles">' + "".join(
        f'<div class="tile"><div class="v">{v}</div><div class="l">{esc(l)}</div></div>'
        for l, v in tiles) + "</div>")

    # Products per category
    c.append("<h2>Afgenomen producten</h2>")
    margin_cols = '<th class="n">Marge</th><th class="n">Marge %</th>' if internal else ""
    c.append(f'<table><tr><th>Product</th><th class="n">Aantal</th>'
             f'<th class="n">Omzet</th>{margin_cols}</tr>')
    by_category: dict[str, list[dict[str, Any]]] = {}
    for product in kpis.products:
        by_category.setdefault(product["category"], []).append(product)
    for category in sorted(by_category):
        products = by_category[category]
        cat_turnover = sum(p["turnover"] for p in products)
        if internal:
            cat_margin = sum(p["margin"] for p in products)
            cat_pct = cat_margin / cat_turnover if abs(cat_turnover) >= 0.01 else 0.0
            extra = (f'<td class="n">{eur(cat_margin, 0)}</td>'
                     f'<td class="n">{pct(cat_pct)}</td>')
        else:
            extra = ""
        c.append(f'<tr class="cat"><td>{esc(category)}</td><td></td>'
                 f'<td class="n">{eur(cat_turnover, 0)}</td>{extra}</tr>')
        for p in products:
            if internal:
                p_pct = p["margin"] / p["turnover"] if abs(p["turnover"]) >= 0.01 else 0.0
                extra = (f'<td class="n">{eur(p["margin"], 0)}</td>'
                         f'<td class="n">{pct(p_pct)}</td>')
            c.append(f'<tr><td>{esc(p["name"])}</td><td class="n">{num(p["qty"])}</td>'
                     f'<td class="n">{eur(p["turnover"], 0)}</td>{extra if internal else ""}</tr>')
    total_extra = ""
    if internal:
        total_extra = (f'<td class="n">{eur(kpis.gross_profit, 0)}</td>'
                       f'<td class="n">{pct(kpis.margin_pct)}</td>')
    c.append(f'<tr class="total"><td>Totaal</td><td></td>'
             f'<td class="n">{eur(kpis.turnover, 0)}</td>{total_extra}</tr></table>')

    # Orders & delivery
    c.append("<h2>Orders &amp; levering</h2>")
    c.append(f"""<table>
<tr><th>Besteld (stuks)</th><th>Geleverd (stuks)</th>
<th class="n">Gefactureerd (excl. btw)</th><th class="n">Nog te factureren</th></tr>
<tr><td>{num(kpis.qty_ordered)}</td><td>{num(kpis.qty_delivered)}</td>
<td class="n">{eur(kpis.amount_invoiced_orders, 0)}</td>
<td class="n">{eur(kpis.amount_to_invoice, 0)}</td></tr></table>""")
    if kpis.open_orders:
        c.append('<table><tr><th>Order</th><th>Datum</th>'
                 '<th class="n">Nog te factureren</th></tr>')
        for order in kpis.open_orders[:10]:
            c.append(f'<tr><td>{esc(order["name"])}</td><td>{esc(order["date"])}</td>'
                     f'<td class="n">{eur(order["to_invoice"])}</td></tr>')
        c.append("</table>")

    # Invoicing & payment
    c.append("<h2>Facturatie &amp; betaling</h2>")
    terms = ", ".join(kpis.payment_terms) or "onbekend"
    c.append(f"<p>Betalingstermijn: <strong>{esc(terms)}</strong>")
    if kpis.avg_days_to_pay is not None:
        c.append(f" &nbsp;·&nbsp; gemiddelde betaaltijd: "
                 f"<strong>{num(kpis.avg_days_to_pay)} dagen</strong> "
                 f'<span class="note">(o.b.v. {kpis.paid_invoice_count} betaalde facturen)</span>')
    else:
        c.append(' &nbsp;·&nbsp; <span class="note">gemiddelde betaaltijd: nog niet '
                 "beschikbaar (betaaldata-extract volgt)</span>")
    c.append("</p>")
    if kpis.open_invoices:
        c.append('<table><tr><th>Factuur</th><th>Datum</th><th>Vervaldatum</th>'
                 '<th class="n">Openstaand</th><th>Valuta</th></tr>')
        for invoice in kpis.open_invoices:
            due = (f'<span class="overdue">{esc(invoice["due"])} (vervallen)</span>'
                   if invoice["overdue"] else esc(invoice["due"]))
            c.append(f'<tr><td>{esc(invoice["name"])}</td><td>{esc(invoice["date"])}</td>'
                     f'<td>{due}</td><td class="n">{eur(invoice["residual"])}</td>'
                     f'<td>{esc(invoice["currency"])}</td></tr>')
        c.append("</table>")
        if kpis.overdue_count:
            c.append(f'<p class="overdue">{kpis.overdue_count} factuur/facturen over de '
                     "vervaldatum.</p>")
    else:
        c.append("<p>Geen openstaande facturen.</p>")

    # Discounts
    c.append("<h2>Kortingen</h2>")
    if kpis.avg_discount is not None:
        c.append(f"<p>Gewogen gemiddelde korting: <strong>{pct(kpis.avg_discount)}</strong>"
                 f" &nbsp;·&nbsp; aandeel factuurregels met korting: "
                 f"<strong>{pct(kpis.discounted_share)}</strong></p>")
    else:
        c.append("<p>Geen factuurregels gevonden.</p>")

    source_note = (
        "Bron: Odoo, via de gevalideerde Sales Analysis-pipeline. Marges volgens de "
        "vastgelegde Gross Profit-definitie (spec §4.3)."
        if internal else
        "Bron: Odoo. Bedragen exclusief btw, tenzij anders vermeld."
    )
    c.append(f'<div class="footer">{source_note} &nbsp;·&nbsp; Gegenereerd op '
             f"{kpis.generated_at.strftime('%d-%m-%Y %H:%M')}.</div>")

    title = f"Klantrapportage {esc(kpis.customer.label)}"
    botbar = ('<div class="botbar"><div class="in">'
              '<span class="brand">VAN THIEL UNITED</span>'
              '<span class="site">www.vanthielunited.com</span></div></div>')
    frame = (
        "<table class='frame'>"
        "<thead><tr><td class='frame-space'></td></tr></thead>"
        f"<tbody><tr><td class='frame-body'>{''.join(c)}</td></tr></tbody>"
        "<tfoot><tr><td class='frame-foot'></td></tr></tfoot></table>"
    )
    return (f"<!DOCTYPE html><html lang='nl'><head><meta charset='utf-8'>"
            f"<title>{title}</title><style>{CSS}</style></head>"
            f"<body>{botbar}{frame}</body></html>")
