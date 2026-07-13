# Sales Analysis pipeline — roadmap & session handoff

Goal: replace the Power Automate → SharePoint → Power Query pipeline behind
`VTU Report - Sales Analysis V1.0.xlsx` with a local Python pipeline pulling
straight from Odoo Online 17 (JSON-RPC).

The functional spec is the reverse-engineered workbook documentation:
[`sales-analysis-v1.0-powerquery-powerpivot.md`](sales-analysis-v1.0-powerquery-powerpivot.md).
Section references below (§) point into that document. The consolidated
as-is description of the validated POC (architecture, data model,
validation status, assumptions) is
[`sales-analysis-pipeline-as-is.md`](sales-analysis-pipeline-as-is.md).

```
Phase 1  EXTRACT    Odoo -> staging CSVs            DONE
Phase 2  TRANSFORM  staging CSVs -> fact + dims     DONE
Phase 3  REPORT     Excel / Power Pivot on top      NEXT (not started)
```

---

## Phase 1 — Extract (done)

`pull_report_data.py` exports the five Odoo-sourced CSVs of the source
contract (§2) to `output/`, driven by the specs in `src/datasets.py`.
Verified against the live Odoo instance on 2026-07-06. Windows runner:
`scripts/run-report-pull.ps1`. See the README for usage.

Design decisions a future session should know:

- **Staging stays raw.** No `state` filter, no joins, no derived columns —
  those belong to phase 2. Single exception: a server-side `--since` date
  floor (default `2025-04-01`, the report's own hard cutoff, §5.2) on
  `account_move` / `account_move_line` to keep volume sane. `--all-dates`
  disables it.
- **`account_move_line` is filtered to 800* revenue accounts**
  (`account_id.code =like '800%'`). Discovered 2026-07-13: the legacy
  Power Automate flow applied this filter before writing the CSV (it was
  invisible in the workbook, so the original spec §2.2 missed it — now
  annotated there). Part of the source contract, so it lives in the
  extract, not the transform.
- **Column names follow the source contract (§2) exactly**, so the transform
  can be built against a stable, documented schema.
- **Many2one rendering:** fields the legacy transform splits itself
  (`PartnerID`, `company_id`, `product_id`, `product_uom_id`,
  `company_currency_id`) keep the raw `[id,"Display Name"]` shape;
  `account_id` and `report_category_name` are written as plain display
  names; `CurrencyID` as numeric id.
- **Legacy quirks (§5.7) deliberately cleaned up:** everything UTF-8 with
  standard CSV quoting (no Windows-1252), no unnamed junk columns, empty
  values as `""` instead of the literal text `"False"`. The legacy files'
  unreferenced columns (4 in `account_move_line.csv`, 1 in
  `product_template.csv`) are dropped. **Phase 2 must therefore skip the
  corresponding legacy cleaning steps** (§3.6 step 1, §3.8 step 3).
- **Custom fields:** `product.template.report_category` (many2one) feeds
  `report_category_name`; `prodin_reference` is pulled as-is. Missing custom
  fields warn and write an empty column instead of failing.

Open verification items:

- [ ] **`CurrencyValue`** is mapped to the currency code (e.g. `EUR`) — the
  spec only says "kept in output" (§2.1). Compare once against a legacy
  Power Automate file; if it held something else (e.g. the rate), fix the
  one-line mapping in `src/datasets.py`.

## Manual source: `product_template_name.xlsx`

The Dutch product-name mapping (§2.6) is maintained by hand and cannot be
re-pulled from anywhere. Agreed location (2026-07-06):
**`output/product_template_name.xlsx`**, next to the staging CSVs on the
local machine.

- Safe next to the extract: `pull_report_data.py` only (over)writes the five
  contract CSVs by their exact names and never deletes anything else in
  `output/`.
- `output/` is gitignored, so this file lives only on the local machine —
  keep a backup copy; it is the only file in `output/` that cannot be
  regenerated.
- Phase 2 reads it from this path and must fail with a clear message when
  it is missing.

## Phase 2 — Transform (done)

**Decision (2026-07-06): the transform runs locally in Python**, replacing
the Power Query layer (§3). Built 2026-07-06: `transform_report_data.py`
(CLI) + `src/transform.py` (logic) read the staging CSVs and the manual
xlsx from `output/` and write the report tables to `output/report/`:
`report_invoiced.csv` (fact, §3.11 columns), `dim_product.csv`,
`dim_partner.csv`, `dim_currency.csv`, `dim_date.csv`, `dim_uom.csv`,
`dim_company.csv`, `refresh_date_time.csv`. Windows runner:
`scripts/run-report-transform.ps1`. Verified end-to-end against synthetic
staging data covering every §3.8–3.11 rule.

**Validated against the live workbook (2026-07-13):** after adding the
800*-revenue-account extract filter (which the legacy Power Automate flow
applied invisibly — see phase 1 notes), Invoiced Amount (`balance`)
matches to the cent, Turnover (`price_subtotal_eur`) matches to full
float precision (14+ digits), Quantity matches, and the
`special_category` cut matches. Watch out when loading the CSVs into
Excel: Power Query's automatic type detection (first ~200 rows, per
column) can silently type a decimal column as whole number — type the
numeric columns explicitly (Decimal, en-US locale).

Design decisions a future session should know:

- **Maintenance data is config, not code:** `config/transform_rules.json`
  holds the `special_category` rules (rental account + product-id lists,
  §3.9/§5.5), the company mapping, and the UoM factor table. The transform
  warns about unmapped company ids and unknown UoM ids at run time.
- **PRM B.V. (id 2) added** to the company mapping (fixes §5.4). Unmapped
  company ids keep their raw Odoo display name and trigger a warning.
- **UoM factors live in the rules file** (10 rows as of 2026-07-13: the 6
  the spec documents plus m/Liter/ton/liters, surfaced by the first live
  run; boxes was archived in Odoo instead — see the parked-points list).
  Missing ids surface as a run-time warning and quantity then stays
  unconverted, the legacy null-factor behaviour.
- **Week numbering: legacy Power Query semantics kept by default**
  (week 1 contains Jan 1, weeks start Monday) so historical week buckets
  stay comparable; `--iso-weeks` switches to true ISO 8601 (§3.4 quirk —
  decided 2026-07-06: keep legacy as default, revisit in phase 3 if the
  business wants ISO).
- **Refresh timestamp** (`refresh_date_time.csv`) is the pipeline's own
  generated-at time (replaces the SharePoint file metadata of §3.1); the
  `join_id1 = 1` helper column is kept for workbook compatibility.
- **Legacy `"False"`-cleaning steps skipped** (§3.6 step 1, §3.8 step 3):
  the phase-1 extract already writes clean empty strings.
- Missing `output/product_template_name.xlsx` fails with a clear
  restore-from-backup message, as agreed above.

## Phase 2 follow-ups — parked improvement points (2026-07-13)

Discussed and deliberately parked until the transform output has been
validated against the live workbook (validation makes every later change
testable). Business-logic scope — the structural pipeline backlog is
[`pipeline-improvements.md`](pipeline-improvements.md).

- [ ] **Rental detection matches the full account display name**
  (`"800550 Omzet NL Verhuur"`, §3.9). Renaming the account in Odoo would
  silently drop all Rental Orders. Match on the account *code* prefix
  (`800550`) instead.
- [ ] **Special/RSS product-id lists** are config now, but still a
  manually maintained list — a new special product silently classifies as
  "Normal". Structural fix: own this in Odoo (e.g. `report_category` or a
  product field) so the source system knows and the transform only reads.
- [ ] **Double currency conversion** (§5.3): Turnover uses
  `price_subtotal / currency_rate`, Invoiced Amount uses `balance * -1` —
  two routes to EUR that differ by rounding. Ask the business whether
  that was ever intentional, or whether Turnover should be balance-based.
- [ ] **Data-driven dimensions**: pull `uom.uom` factors and `res.company`
  names from Odoo in phase 1 instead of maintaining copies in
  `config/transform_rules.json`. Caveat: the legacy `hours = 8` factor
  (hours -> days) is a reporting choice that is NOT in Odoo, so a config
  override on top stays needed.
- [x] **boxes (uom id 39)** — closed 2026-07-13: the unit was never
  really used and has been archived in Odoo. No factor added on purpose;
  affects only the unvalidated margin measures, not Turnover / Invoiced
  Amount / Quantity. If the "UoM id without a factor: 39" warning ever
  (re)appears in a refresh, historical revenue lines reference it and
  the decision reopens.
- [ ] **Dutch-names gap**: ~514 products without a row in the manual
  `product_template_name.xlsx` (legacy had the same gap; English fallback
  applies). Data maintenance, not logic.
- [ ] **ISO week numbering**: implemented behind `--iso-weeks`, default
  stays legacy. Business decision whether/when to switch.

## Operations — full refresh & run report (added 2026-07-13)

`refresh_report_data.py` (Windows: `scripts/run-full-refresh.ps1`, which
also syncs the repo first) runs phase 1 + phase 2 back to back and ends
with a run report: durations per phase, records per dataset, deltas vs the
previous run, and warnings from either phase. History accumulates in
`output/refresh_log.md` / `output/refresh_history.jsonl`; both phase
scripts also take `--metrics-json` standalone. The structural
data-engineering improvement backlog lives in
[`pipeline-improvements.md`](pipeline-improvements.md) — transform
business-logic improvements stay in the phase 2/3 sections here.

## Phase 3 — Report (next)

Rebuild the workbook on the phase-2 outputs (`output/report/`): Power Pivot
model (relationships §4.2, measures §4.3) and pivots (§4.4).

- [x] **Reconcile first** — done 2026-07-13: Invoiced Amount to the cent,
  Turnover to full float precision, Quantity and the `special_category`
  cut all match the live workbook (details in the phase 2 section).
  The `boxes` (uom id 39) follow-up is closed too: the unit was archived
  in Odoo on 2026-07-13 (see the parked-points list).
- [x] **Export the exact DAX definitions** — done 2026-07-13: all six
  explicit measures recorded verbatim in spec §4.3 (replacing the
  reconstruction, which was wrong on two points). They work unchanged on
  the phase-2 outputs **provided** the rebuilt model names the tables
  `Report - Invoiced` and `dim_product` — see the porting note in §4.3.
- [ ] **Validate the margin measures** after the rebuild: Gross Profit /
  Margin % / Cost of Sales depend on `quantity_product_uom` and
  `standard_price`, which the 2026-07-13 reconciliation did not cover
  (only Turnover, Invoiced Amount, Quantity, special_category).
  **POC note (2026-07-13): the owner accepts the margin measures as-is
  for the POC**; this item stays open for when the model graduates
  beyond POC.
- [ ] The stale `Merge1` connection in the workbook (§5.6) — remove when
  the workbook is rebuilt on the new outputs.
- [ ] Also still open: the `CurrencyValue` verification from phase 1 (see
  above).

## Handoff — how to start the phase 3 session

1. Read `docs/sales-analysis-pipeline-as-is.md` (the validated as-is),
   this file, and the spec (`docs/sales-analysis-v1.0-powerquery-powerpivot.md`).
2. Extract layer: `src/datasets.py` is the source contract in code;
   `python pull_report_data.py --limit 5` is a quick smoke test.
3. Transform layer: `python transform_report_data.py` reads `output/` and
   writes `output/report/`; business rules live in
   `config/transform_rules.json`. Extract and transform are decoupled —
   either can run alone.
4. The workbook rebuild happens on the local Windows machine (Excel);
   this repo can prepare everything up to and including the CSV outputs.
