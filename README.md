# odoo_data

Pull data from an **Odoo Online 17** instance over the **JSON-RPC** external API.

Roadmap, session handoff and the report's functional spec live in
[`docs/`](docs/roadmap.md); the consolidated as-is description of the
validated pipeline (POC) is
[`docs/sales-analysis-pipeline-as-is.md`](docs/sales-analysis-pipeline-as-is.md).

The Sales Analysis pipeline runs in two decoupled steps, plus an ad-hoc
export that shares the same JSON-RPC client:

- `pull_report_data.py` — **phase 1 (staging)**: exports the five
  Odoo-sourced CSVs of the report's source contract to a local folder.
- `transform_report_data.py` — **phase 2 (transform)**: turns the staging
  CSVs into the fact + dimension tables the report consumes (the Python
  replacement of the workbook's Power Query layer).
- `refresh_report_data.py` — **full refresh**: phase 1 + phase 2 back to
  back, finishing with a run report (durations per phase, records
  downloaded, observations) appended to `output/refresh_log.md`.
- `pull_partners.py` — the original ad-hoc partner export (flattened,
  human-readable columns).

## Requirements

- Python 3.9+
- An Odoo Online 17 instance and an API key
  (Preferences → Account Security → New API Key)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env with your Odoo URL, database, username, and API key

# enable the secret-scanning git hook (one-time per clone)
git config core.hooksPath .githooks   # Windows: .\scripts\setup-hooks.ps1
```

`.env` is gitignored — your credentials are never committed. The pre-commit
hook in `.githooks/` additionally blocks any commit that would introduce an
API key, token, or password into tracked files.

| Variable        | Description                                                        |
| --------------- | ------------------------------------------------------------------ |
| `ODOO_URL`      | Base URL, e.g. `https://yourcompany.odoo.com` (no `/jsonrpc`)       |
| `ODOO_DB`       | Database name (usually your subdomain, e.g. `yourcompany`)          |
| `ODOO_USERNAME` | Login email of the API user                                        |
| `ODOO_API_KEY`  | API key (used in place of the password for the external API)       |

## Usage — Sales Analysis staging CSVs (phase 1)

`pull_report_data.py` exports the five Odoo-sourced files consumed by the
*VTU Report – Sales Analysis* workbook (source contract §2 of its
documentation). Column names follow that contract exactly:

| File | Odoo model | Notes |
| --- | --- | --- |
| `account_move.csv` | `account.move` | invoice headers; `--since` filters `date` |
| `account_move_line.csv` | `account.move.line` | invoice lines on 800* revenue accounts only (like the legacy flow); `--since` filters `date` |
| `product_template.csv` | `product.template` | `product_id` = variant id (fact key) |
| `res_currency.csv` | `res.currency` | `latest_rate` / `latest_rate_date` = Odoo `rate` / `date` |
| `res_partner.csv` | `res.partner` | id, name, commercial_company_name, country_id |

The sixth source of the report, `product_template_name.xlsx`, is a manually
maintained mapping and is not pulled from Odoo. It lives at
`output/product_template_name.xlsx`, next to the staging CSVs. That is safe:
the pull only (over)writes the five CSVs above by their exact names and
never deletes anything else in `output/`. Since `output/` is gitignored,
keep a backup of the xlsx — it is the only file there that cannot be
regenerated.

This is staging only — no state filters, joins, or derived columns (that is
phase 2). The single exception: the two `account.move` datasets take a
server-side `--since` date floor (default `2025-04-01`, the report's own
cutoff) so the export volume stays sane.

```powershell
# Windows: pulls latest code, checks venv/.env, runs the export, summarises
.\scripts\run-report-pull.ps1
.\scripts\run-report-pull.ps1 --limit 5           # quick smoke test
.\scripts\run-report-pull.ps1 --only res_partner  # single dataset
```

```bash
python pull_report_data.py                        # all five CSVs -> output/
python pull_report_data.py --only account_move,account_move_line
python pull_report_data.py --since 2024-01-01     # widen the date window
python pull_report_data.py --all-dates            # full history
python pull_report_data.py --output-dir "C:/Odoo/CSV Library"
```

Deliberate differences vs. the legacy Power Automate CSVs (see the report
docs "Notes & Quirks"): every file is **UTF-8** with standard CSV quoting
(no Windows-1252, no unnamed junk columns), and empty values are written as
empty strings instead of the literal text `"False"`. Many2one fields that
the transform splits itself (e.g. `PartnerID`, `company_id`, `product_id`)
keep the raw `[id,"Display Name"]` shape. If a custom field (e.g.
`prodin_reference`) doesn't exist on your instance, the script warns and
writes the column empty instead of failing.

## Usage — Sales Analysis transform (phase 2)

`transform_report_data.py` replaces the workbook's Power Query layer (§3 of
the report documentation). It reads the staging CSVs plus the manual
`product_template_name.xlsx` from `output/` and writes the report tables to
`output/report/`:

| File | Contents |
| --- | --- |
| `report_invoiced.csv` | final fact table (§3.11): posted invoices ≥ cutoff, headers × lines, `special_category`, `Invoiced Amount` |
| `dim_product.csv` | products enriched with references and Dutch names (§3.6) |
| `dim_partner.csv` / `dim_currency.csv` | customer / currency dimensions (§3.7) |
| `dim_date.csv` | daily calendar from the cutoff through today (§3.4) |
| `dim_uom.csv` / `dim_company.csv` | static lookup tables, from the rules file |
| `refresh_date_time.csv` | pipeline run timestamp (replaces the SharePoint file metadata of §3.1) |

Business maintenance data lives in **`config/transform_rules.json`** — edit
that file, not the code, when rules change: the `special_category` product-id
lists and rental account, the company mapping (includes PRM B.V., fixing the
legacy gap), and the UoM factor table. The transform warns about any company
or UoM id it meets that is missing from the rules file.

```powershell
# Windows: pulls latest code, checks venv/.env, runs the transform, summarises
.\scripts\run-report-transform.ps1
```

```bash
python transform_report_data.py                    # output/ -> output/report/
python transform_report_data.py --cutoff 2024-04-01
python transform_report_data.py --iso-weeks        # true ISO 8601 week numbers
python transform_report_data.py --input-dir "C:/Odoo/CSV Library" --output-dir out
```

Week numbers in `dim_date` default to the legacy Power Query numbering
(week 1 contains January 1, weeks start Monday) so historical week buckets
stay comparable; `--iso-weeks` switches to true ISO 8601.

The transform fails with a clear message when
`output/product_template_name.xlsx` is missing — that manual mapping cannot
be regenerated, so restore it from backup (or point `--product-names` at it).

## Usage — full refresh (phase 1 + 2 + run report)

The recommended day-to-day entry point. On Windows,
`.\scripts\run-full-refresh.ps1` syncs the repo with GitHub (timed), then
runs `refresh_report_data.py`, which executes both phases and finishes
with a run report:

- duration per phase (git sync, extract, transform) and the total,
- records downloaded per staging dataset with pull rate,
- fact filter statistics and Dutch-name match rate,
- a comparison against the previous refresh (record and duration deltas),
- observations: warnings raised by either phase, record-count jumps
  (>10%), and the slowest dataset.

The report is printed and appended to `output/refresh_log.md`; the raw
numbers of every run go to `output/refresh_history.jsonl` (both
gitignored, machine-local). Per-phase metrics of the latest run are in
`output/metrics/*.json` — both phase scripts also accept `--metrics-json`
when run standalone. The structural improvement backlog the report links
to is [`docs/pipeline-improvements.md`](docs/pipeline-improvements.md).

```powershell
.\scripts\run-full-refresh.ps1              # sync + extract + transform + report
.\scripts\run-full-refresh.ps1 --iso-weeks  # extra args forwarded
```

```bash
python refresh_report_data.py               # without the git sync step
python refresh_report_data.py --all-dates   # forwarded to phase 1
```

## Usage — partner export

On Windows, the convenience script pulls the latest code, checks the venv and
`.env`, runs the export, and prints a summary — the recommended way to run:

```powershell
.\scripts\run-pull.ps1            # pull latest, then export all partners
.\scripts\run-pull.ps1 --limit 5  # extra args pass through to pull_partners.py
```

Or invoke the script directly:

```bash
# Quick connectivity test — pull at most 5 partners
python pull_partners.py --limit 5

# Pull all partners
python pull_partners.py

# Only companies
python pull_partners.py --companies-only

# Custom output path / fields
python pull_partners.py --output data/partners.csv --fields id,name,email,vat
```

Output is written to `output/partners.csv` by default (the `output/` directory
is gitignored). Odoo `many2one` fields (e.g. `country_id`) are flattened to
their display name; `many2many` fields (e.g. `category_id`) are joined with `;`.

## Project structure

```
odoo_data/
├── docs/                 # roadmap, report spec, pipeline improvement backlog
├── config/
│   └── transform_rules.json  # special_category lists, company map, UoM factors
├── .env.example          # documents required env vars
├── requirements.txt      # requests, python-dotenv, openpyxl
├── pull_report_data.py       # phase 1: pull the 5 Sales Analysis staging CSVs
├── transform_report_data.py  # phase 2: staging CSVs -> fact + dimension tables
├── refresh_report_data.py    # full refresh: phase 1 + 2 + run report
├── pull_partners.py      # ad-hoc partner export (flattened columns)
├── scripts/
│   ├── run-full-refresh.ps1      # Windows runner: sync + refresh_report_data.py
│   ├── run-report-pull.ps1       # Windows runner for pull_report_data.py
│   ├── run-report-transform.ps1  # Windows runner for transform_report_data.py
│   └── run-pull.ps1              # Windows runner for pull_partners.py
└── src/
    ├── config.py         # load & validate env vars
    ├── datasets.py       # source contract: model -> CSV column specs
    ├── transform.py      # phase 2 transform logic (port of the Power Query layer)
    └── odoo_client.py    # OdooClient: JSON-RPC transport, auth, execute_kw, search_read
```

## Using the client for other models

```python
from src.config import load_config
from src.odoo_client import OdooClient

cfg = load_config()
client = OdooClient(cfg.url, cfg.db, cfg.username, cfg.api_key)

products = client.search_read(
    "product.product",
    domain=[["sale_ok", "=", True]],
    fields=["id", "name", "list_price"],
    limit=10,
)
```
