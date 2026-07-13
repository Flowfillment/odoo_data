# Sales Analysis pipeline — roadmap & session handoff

Goal: replace the Power Automate → SharePoint → Power Query pipeline behind
`VTU Report - Sales Analysis V1.0.xlsx` with a local Python pipeline pulling
straight from Odoo Online 17 (JSON-RPC).

The functional spec is the reverse-engineered workbook documentation:
[`sales-analysis-v1.0-powerquery-powerpivot.md`](sales-analysis-v1.0-powerquery-powerpivot.md).
Section references below (§) point into that document.

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
staging data covering every §3.8–3.11 rule; **not yet reconciled against
the live workbook's numbers** (see phase 3 checklist).

Design decisions a future session should know:

- **Maintenance data is config, not code:** `config/transform_rules.json`
  holds the `special_category` rules (rental account + product-id lists,
  §3.9/§5.5), the company mapping, and the UoM factor table. The transform
  warns about unmapped company ids and unknown UoM ids at run time.
- **PRM B.V. (id 2) added** to the company mapping (fixes §5.4). Unmapped
  company ids keep their raw Odoo display name and trigger a warning.
- **UoM factors: only the 6 rows the spec documents (§3.2) are seeded.**
  The legacy workbook embeds 16. Missing ids surface as a run-time warning
  (quantity then stays unconverted, the legacy null-factor behaviour) —
  copy the remaining rows from the workbook's `dim_uom` query into the
  rules file as they surface.
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

- [ ] **Reconcile first:** run extract + transform on the live machine and
  compare `report_invoiced.csv` totals (Turnover, Invoiced Amount, per
  company/month) against the current workbook before rebuilding on it.
  While at it, note any "UoM id without a factor" warnings and complete
  `config/transform_rules.json` from the workbook's `dim_uom` query.
- [ ] **Before touching measures:** export the exact DAX definitions from
  the live workbook (Power Pivot → Manage) — §4.3 is a reconstruction
  (§5.8).
- [ ] The stale `Merge1` connection in the workbook (§5.6) — remove when
  the workbook is rebuilt on the new outputs.
- [ ] Also still open: the `CurrencyValue` verification from phase 1 (see
  above).

## Handoff — how to start the phase 3 session

1. Read this file and the spec (`docs/sales-analysis-v1.0-powerquery-powerpivot.md`).
2. Extract layer: `src/datasets.py` is the source contract in code;
   `python pull_report_data.py --limit 5` is a quick smoke test.
3. Transform layer: `python transform_report_data.py` reads `output/` and
   writes `output/report/`; business rules live in
   `config/transform_rules.json`. Extract and transform are decoupled —
   either can run alone.
4. The workbook rebuild happens on the local Windows machine (Excel);
   this repo can prepare everything up to and including the CSV outputs.
