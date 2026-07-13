# Sales Analysis pipeline — as-is description (validated POC)

**Status: validated proof of concept** — declared 2026-07-13 by the owner.
The pipeline replaces the legacy Power Automate → SharePoint → Power Query
chain behind `VTU Report - Sales Analysis V1.0.xlsx` and its output has
been reconciled against that live workbook (see [Validation
status](#validation-status)). This document is the single as-is reference:
what the pipeline is, what it produces, how it runs, and under which
assumptions. History and decisions live in [`roadmap.md`](roadmap.md); the
legacy workbook's reverse-engineered spec is
[`sales-analysis-v1.0-powerquery-powerpivot.md`](sales-analysis-v1.0-powerquery-powerpivot.md)
(referenced below as §).

## Architecture

```
Odoo Online 17 ──(JSON-RPC, .env credentials)──▶  PHASE 1  EXTRACT
                                                  pull_report_data.py
                                                  output/*.csv  (5 staging files,
                                                  raw source contract §2)
                    output/product_template_name.xlsx  (manual, see Sources)
                                                        │
                                                        ▼
config/transform_rules.json ───────────────▶     PHASE 2  TRANSFORM
(business rules, maintained by hand)              transform_report_data.py
                                                  output/report/*.csv
                                                  (fact + 6 dims + timestamp)
                                                        │
                                                        ▼
                                                  PHASE 3  REPORT (planned)
                                                  Excel: Power Pivot model +
                                                  pivots on the CSVs, no
                                                  transformation logic left
```

`refresh_report_data.py` (Windows: `scripts/run-full-refresh.ps1`) runs
phases 1+2 back to back and ends with a run report. Everything runs on one
local Windows machine; the cloud session only develops code — data never
leaves the machine.

## Sources

| Source | Kind | Notes |
|---|---|---|
| `account.move` / `account.move.line` | Odoo, JSON-RPC | Server-side floors: `date >= 2025-04-01` (the report's own cutoff) and, on lines, `account_id.code =like '800%'` — the revenue-account filter the legacy flow applied invisibly (§2.2 annotation) |
| `product.template`, `res.currency`, `res.partner` | Odoo, JSON-RPC | Full pulls |
| `output/product_template_name.xlsx` | **Manual file** | Dutch product names (§2.6). **Cannot be regenerated — keep a backup.** The transform fails clearly when it is missing |
| `config/transform_rules.json` | Config (in git) | `special_category` rules, company mapping (incl. the PRM B.V. fix), UoM factor table. Editing this file is how business rules are maintained |

## Output: the data model (star schema)

Written to `output/report/`, UTF-8 CSV. **Fact grain: one row per invoice
line on an 800* revenue account of a posted invoice with accounting date ≥
2025-04-01.**

| Table | Contents | Keys / relationships (§4.2) |
|---|---|---|
| `report_invoiced.csv` | Fact, §3.11 columns incl. `special_category`, `quantity_product_uom`, `price_subtotal_eur`, `Invoiced Amount` | `PartnerID` → dim_partner, `CurrencyID` → dim_currency, `accounting_date` → dim_date, `product_id` → dim_product |
| `dim_product.csv` | Products + references + Dutch names | key `product_id` (variant id) |
| `dim_partner.csv` / `dim_currency.csv` / `dim_date.csv` | Customers / currencies / calendar (from cutoff through today) | keys `id` / `id` / `Date` |
| `dim_uom.csv` / `dim_company.csv` | Static lookups from the rules file | not in the Power Pivot model (worksheet reference only, §4.1) |
| `refresh_date_time.csv` | Pipeline run timestamp | standalone |

The six explicit DAX measures (Avg Sales Price, Standard Price, Standard
Cost, Cost of Sales, Gross Profit, Margin %) are recorded **verbatim** in
§4.3, exported from the live workbook on 2026-07-13, with a porting note:
name the tables `Report - Invoiced` and `dim_product` in the rebuilt model
and they paste in unchanged.

## Operations

- **Full refresh:** `.\scripts\run-full-refresh.ps1` — git sync, both
  phases, run report (durations, records per dataset, deltas vs previous
  run, warnings). History: `output/refresh_log.md` +
  `output/refresh_history.jsonl`; latest per-phase metrics:
  `output/metrics/*.json`.
- **Self-signalling:** unknown company ids, unknown UoM ids and >10%
  record-count jumps surface as run-report warnings instead of silent
  drift.
- **Failure behaviour:** missing staging files or the manual xlsx abort
  with actionable messages; phases are idempotent and individually
  re-runnable.

## Validation status

Reconciled against the live legacy workbook on **2026-07-13**:

| Measure basis | Result |
|---|---|
| Invoiced Amount (`balance`) | matches to the cent |
| Turnover (`price_subtotal_eur`) | matches to full float precision (14+ digits) |
| Quantity (`quantity`) | matches |
| `special_category` cut | matches |
| Margin measures (Gross Profit, Margin %, Cost of Sales) | **not separately reconciled — accepted as-is for the POC** (owner decision 2026-07-13). They depend on `quantity_product_uom` and `standard_price`; formal validation is a phase-3 checklist item |

Pitfall recorded for anyone loading the CSVs into Excel: Power Query's
automatic type detection samples ~200 rows per column and can silently
type a decimal column as whole number — type numeric columns explicitly
(Decimal, en-US locale).

## Known limitations & assumptions (accepted for the POC)

1. Special/RSS classification and UoM factors are **maintained lists** in
   the rules file; new cases surface as warnings, not automatically.
2. Rental detection matches the full account display name — renaming the
   account in Odoo would break it silently (parked fix: match the code).
3. Turnover and Invoiced Amount take **two different routes to EUR**
   (§5.3, legacy behaviour kept deliberately).
4. ~514 products lack a Dutch name in the manual xlsx (English fallback,
   same gap as legacy). `CurrencyValue` content is unverified (no measure
   uses it).
5. Week numbering is legacy Power Query style, not ISO 8601 (`--iso-weeks`
   available).
6. Full reload every run (~40k invoices); fine at current volume.
   Single-machine, manual/on-demand scheduling; timestamps are naive local
   time.

Improvement backlogs: business logic in
[`roadmap.md`](roadmap.md#phase-2-follow-ups--parked-improvement-points-2026-07-13),
data-engineering/architecture in
[`pipeline-improvements.md`](pipeline-improvements.md).

## Document map

| Document | Role |
|---|---|
| this file | as-is reference of the validated POC |
| [`roadmap.md`](roadmap.md) | phases, decisions, open items, session handoff |
| [`sales-analysis-v1.0-powerquery-powerpivot.md`](sales-analysis-v1.0-powerquery-powerpivot.md) | legacy workbook spec (source contract §2, transform rules §3, model §4, quirks §5) + exact DAX (§4.3) |
| [`pipeline-improvements.md`](pipeline-improvements.md) | structural data-engineering backlog |
| `../README.md` | setup & usage of all entry points |
| `../config/transform_rules.json` | the maintained business rules themselves |
