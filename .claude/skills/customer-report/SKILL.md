---
name: customer-report
description: Generate the standardised per-customer sales report (2-3 page PDF) in customer and/or internal variant. Use when asked for a klantrapport, customer report/overview, or a sales one-pager about a specific customer.
---

# Customer report (klantrapport)

Standardised, reproducible generation of the per-customer sales PDF.
Two variants exist and the difference is a hard rule:

- **customer** — goes to the customer. Contains **no margin data**. The
  generator excludes margins at data-assembly level; never work around
  this by editing the HTML/PDF afterwards.
- **internal** — for sales staff. Includes gross margin and margin %.

## Process (follow in order)

1. **Fresh data.** Ask whether the data may be stale; when in doubt run
   the full refresh first (Windows):
   `.\scripts\run-full-refresh.ps1`
   Review its run report for warnings before continuing.
2. **Identify the customer.** Ask for the customer if not given. Run:
   `python generate_customer_report.py --customer "<name or id>" --variant both`
   If the name is ambiguous the script lists candidates - ask the user to
   pick; never guess between candidates.
3. **Review before delivery** (open the PDF):
   - The customer variant must contain no margins anywhere.
   - Totals plausible? Cross-check the turnover tile against the
     sales-wide report if anything looks off - the margin definition is
     anchored to the validated model (spec §4.3 in
     `docs/sales-analysis-v1.0-powerquery-powerpivot.md`).
   - "Gemiddelde betaaltijd" shows "nog niet beschikbaar" until the
     payments extract exists - that is expected, not a bug.
4. **Deliver.** PDFs land in `output/customer_reports/`
   (`<customer> - <date> - <variant>.pdf`). Hand the user the file
   path(s); do not email or upload anywhere unless explicitly asked.

## Notes

- KPI definitions live in `src/customer_report.py` (docstring) and the
  phase 4 section of `docs/roadmap.md`. Do not improvise different
  definitions in conversation; change them in code via a reviewed commit.
- The data window starts 2025-04 (= all Odoo history).
- Requires a Chromium-family browser for PDF (Edge is standard on
  Windows); without one the script leaves an HTML file to print manually.
