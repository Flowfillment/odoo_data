# Pipeline improvement backlog — data engineering / architecture

Living document. The full-refresh run report
(`refresh_report_data.py` → `output/refresh_log.md`) points here for the
structural improvements that go beyond a single run's warnings.

Scope: the pipeline as a data-engineering system (extract → transform →
outputs). **Business-logic improvements to the transform itself (spec §3
fidelity, categorisation rules, reconciliation against the live workbook)
are deliberately out of scope** — they are tracked in `docs/roadmap.md`
under phases 2/3.

## Already in place (2026-07-13)

- Layered architecture with decoupled, individually runnable stages
  (staging contract in `src/datasets.py`, transform reads only staging).
- Raw staging kept unmodified (no silent transformation at extract time) —
  reprocessing is always possible without re-extracting.
- Business maintenance data in config (`config/transform_rules.json`),
  not code; unknown ids surface as warnings instead of silent drift.
- Idempotent runs: same inputs → same outputs, no partial-state between
  datasets; safe to re-run at any time.
- Observability: per-phase metrics (`output/metrics/*.json`), run report +
  history (`output/refresh_log.md`, `output/refresh_history.jsonl`),
  record-count-delta and warning surfacing per run.
- Secrets outside the repo (`.env`, gitignored) plus a secret-scanning
  pre-commit hook.

## Backlog (roughly in order of value)

1. **Data-quality gates between the phases.** The transform trusts the
   staging schema blindly (a missing/renamed column is a raw `KeyError`).
   Add a cheap contract check at transform start — expected columns per
   file, key uniqueness (`account_move_id`, `product_id`), non-null rates —
   failing with a clear message. This is the standard "expectations at the
   layer boundary" pattern and protects against silent Odoo schema drift.
2. **Referential-integrity checks on the outputs.** Count fact rows whose
   `product_id`/`PartnerID`/`CurrencyID` have no dimension row (in Power
   Pivot these silently land in a blank member). Report them as run-report
   observations rather than discovering them in a pivot.
3. **Atomic file writes.** A crash mid-write currently leaves a truncated
   CSV that looks valid. Write to `<name>.csv.tmp` and rename at the end —
   cheap, and standard practice for file-based pipelines.
4. **Reconciliation numbers in the run report.** Sum of `Invoiced Amount`
   and `price_subtotal_eur` per company in the report — lets a human spot
   "the totals moved oddly" immediately and doubles as the phase-3
   reconciliation tool against the live workbook.
5. **Incremental extraction (only when volume demands it).** Every run is
   a full reload (~100k lines). Fine today — full reloads are the simplest
   correct pattern — but the `--since` mechanism is already the natural
   watermark if extract time becomes a problem. Requires thinking about
   updated/deleted historical invoices before switching.
6. **Tests in CI.** The transform logic is pure and testable but the
   synthetic-fixture verification lives only in a session scratchpad. Add
   a small pytest suite (unit tests per §3 rule + one end-to-end fixture
   run) and a GitHub Actions workflow so regressions are caught on PRs.
7. **Pin dependencies.** `requirements.txt` uses `>=` ranges; a
   `pip freeze`-generated lock (or `uv`/`pip-tools`) makes local runs
   reproducible.
8. **Scheduling.** The runners are already non-interactive; a Windows Task
   Scheduler job (or moving the pipeline to a small server/container)
   turns the manual refresh into a scheduled one. The run history file
   then becomes the monitoring trail.
9. **Timezone-aware timestamps.** `refresh_date_time` and the metrics use
   naive local time; fine for a single-machine pipeline, but stamp them
   with an explicit timezone if scheduling/servers enter the picture.
10. **Column-typed storage format.** CSV is required by the Excel front
    end, but if the outputs ever feed anything else (Power BI, a
    database), Parquet alongside CSV gives types, compression, and speed.
