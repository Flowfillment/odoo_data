# odoo_data

Pull data from an **Odoo Online 17** instance over the **JSON-RPC** external API.

The first pipeline connects, authenticates with an API key, and exports
**Partners/Contacts** (`res.partner`) to a CSV file. The JSON-RPC client is
model-agnostic, so later pulls (products, sales orders, invoices) can reuse it.

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

## Usage

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
├── pull_partners.py      # CLI entrypoint: connect -> pull res.partner -> write CSV
└── src/
    ├── config.py         # load & validate env vars
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
