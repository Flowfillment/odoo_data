# Sales Analysis pipeline — roadmap & session handoff

Goal: replace the Power Automate → SharePoint → Power Query pipeline behind
`VTU Report - Sales Analysis V1.0.xlsx` with a local Python pipeline pulling
straight from Odoo Online 17 (JSON-RPC).

The functional spec is the reverse-engineered workbook documentation:
[`sales-analysis-v1.0-powerquery-powerpivot.md`](sales-analysis-v1.0-powerquery-powerpivot.md).
Section references below (§) point into that document.

```
Phase 1  EXTRACT    Odoo -> staging CSVs            DONE
Phase 2  TRANSFORM  staging CSVs -> fact + dims     NEXT (not started)
Phase 3  REPORT     Excel / Power Pivot on top      later
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

## Phase 2 — Transform (next)

**Decision (2026-07-06): the transform runs locally in Python**, replacing
the Power Query layer (§3). Python reads the staging CSVs and writes a
ready-to-use fact + dimension set; Excel becomes a thin shell (Power Pivot
model + pivots only, no Power Query logic).

Scope — replicate §3 faithfully, then simplify:

1. **Fact:** filter headers (`State = "posted"`,
   `accounting_date >= 2025-04-01`), inner-join headers × lines on
   `account_move_id` (§3.8–3.10), derive `quantity_product_uom` (via the
   UoM factor table, §3.2), `price_subtotal_eur`, `special_category`
   (§3.9), `Invoiced Amount = balance * -1` (§3.11). Output columns: §3.11.
2. **Dimensions:** `dim_product` enrichment incl. Dutch names from the
   manually maintained `output/product_template_name.xlsx` (see above),
   `dim_partner`, `dim_currency`, `dim_date` (§3.4), `dim_uom`,
   `dim_company` (§3.2–3.3).
3. **Refresh timestamp:** legacy read SharePoint file metadata (§3.1);
   replace with a generated-at timestamp written by the pipeline.

Known issues in the legacy logic to resolve while porting (§5):

- [ ] Company mapping misses PRM B.V. (id 2) — §3.8/§5.4. Add it.
- [ ] Hard-coded product-id lists in `special_category` (§3.9/§5.5) — move
  to a config file so maintenance doesn't require a code change.
- [ ] Week numbering in `dim_date` is not true ISO 8601 (§3.4) — decide to
  keep or fix (changes historical week buckets).
- [ ] The stale `Merge1` connection in the workbook (§5.6) — remove when
  the workbook is rebuilt on the new outputs.

## Phase 3 — Report (later)

Rebuild the workbook on the phase-2 outputs: Power Pivot model
(relationships §4.2, measures §4.3) and pivots (§4.4).

- [ ] **Before touching measures:** export the exact DAX definitions from
  the live workbook (Power Pivot → Manage) — §4.3 is a reconstruction
  (§5.8).

## Handoff — how to start the phase 2 session

1. Read this file and the spec (`docs/sales-analysis-v1.0-powerquery-powerpivot.md`).
2. Extract layer: `src/datasets.py` is the source contract in code;
   `python pull_report_data.py --limit 5` is a quick smoke test.
3. Build the transform as a separate entrypoint (e.g. `transform_report_data.py`)
   reading from `output/` — keep extract and transform decoupled so either
   can run alone.
