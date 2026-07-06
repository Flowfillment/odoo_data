# odoo_data

Pull data from an **Odoo Online 17** instance over the **JSON-RPC** external API.

Two pipelines share one model-agnostic JSON-RPC client:

- `pull_report_data.py` — **phase 1 (staging)** of the Sales Analysis report:
  exports the five Odoo-sourced CSVs of the report's source contract to a
  local folder. The transform/merge logic (phase 2) is deliberately out of
  scope here.
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
| `account_move_line.csv` | `account.move.line` | invoice lines; `--since` filters `date` |
| `product_template.csv` | `product.template` | `product_id` = variant id (fact key) |
| `res_currency.csv` | `res.currency` | `latest_rate` / `latest_rate_date` = Odoo `rate` / `date` |
| `res_partner.csv` | `res.partner` | id, name, commercial_company_name, country_id |

The sixth source of the report, `product_template_name.xlsx`, is a manually
maintained mapping and is not pulled from Odoo.

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
├── .env.example          # documents required env vars
├── requirements.txt      # requests, python-dotenv
├── pull_report_data.py   # phase 1: pull the 5 Sales Analysis staging CSVs
├── pull_partners.py      # ad-hoc partner export (flattened columns)
├── scripts/
│   ├── run-report-pull.ps1  # Windows runner for pull_report_data.py
│   └── run-pull.ps1         # Windows runner for pull_partners.py
└── src/
    ├── config.py         # load & validate env vars
    ├── datasets.py       # source contract: model -> CSV column specs
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
