# VTU Report – Sales Analysis V1.0 — Power Query & Power Pivot Documentation

**Workbook:** `VTU Report - Sales Analysis V1.0.xlsx`
**Extracted from:** embedded DataMashup (Power Query M, `Section1.m`) and the Power Pivot data model
**Date:** 2026-07-06

---

## 1. Architecture Overview

```
Odoo ──(Power Automate, out of scope)──▶ CSV files on SharePoint/OneDrive
        Site:   https://admin197.sharepoint.com/sites/Odoo/
        Folder: .../CSV Library/
                          │
                          ▼
                 Power Query (12 queries)
        staging ──▶ merge/enrich ──▶ "Report - Invoiced"
                          │
                          ▼
                 Power Pivot data model
        6 tables, 4 relationships, 16 measures
                          │
                          ▼
        7 PivotTables on 6 sheets:
        Product · Customer · Product Category · Over Time ·
        FX Rates & Check vs P&L · Refresh DateTime
```

The CSV files in the SharePoint `CSV Library` folder are the **true source** for this report. All content originates from Odoo models: `account_move`, `account_move_line`, `product_template`, `res_currency`, `res_partner`, plus one manually maintained Excel file (`product_template_name.xlsx`) for Dutch product names.

---

## 2. Source Files & Required Fields (source contract)

Only the fields below are actually consumed by the Power Query layer. Any other columns in the CSVs are ignored or dropped.

### 2.1 `account_move.csv` (invoice headers) — parsed as 13 columns, UTF-8
| Field | Used for |
|---|---|
| `account_move_id` | Join key to `account_move_line.csv` |
| `Name` | Invoice number (kept in output, used in pivots) |
| `PartnerID` | Format `[id,"Partner Name"]`; split into numeric `PartnerID` + `partner_name`. Key to `res_partner.csv` |
| `CurrencyID` | Key to `res_currency.csv` |
| `CurrencyValue` | Kept in output |
| `State` | **Filter only**: rows kept where `State = "posted"`, then dropped |
| `accounting_date` | Accounting date; **filter** `>= 2025-04-01`; relationship to date dimension |
| `company_id` | Format `[1,"Van Thiel United B.V."]`; text-replaced to friendly names (see §3.8) |
| `InvoiceDate`, `AmountTotal`, `Currency`, `WriteDate`, `move_type` | Loaded then **removed** — not needed |

The SharePoint file metadata `Date modified` of `account_move.csv` is also read (query `refresh_date_time`) to display the data refresh timestamp.

### 2.2 `account_move_line.csv` (invoice lines) — parsed as 16 columns, Windows-1252

> **Pre-filtered at the source (discovered 2026-07-13, was not visible in the workbook):** the Power Automate flow filtered the lines before writing this CSV — only rows where `account_id` is set **and its display name starts with `800`** (revenue accounts) were exported. The Power Query layer below therefore never saw receivable/VAT/cost lines. The Python extract replicates this as a server-side domain (`account_id.code =like '800%'`) in `src/datasets.py`.
| Field | Used for |
|---|---|
| `account_move_id` | Format `[id, ...]`; id extracted; join key to header |
| `account_id` | Text like `"800550 Omzet NL Verhuur"`; drives `special_category` |
| `debit`, `credit`, `balance` | Amounts (company currency, EUR). `balance * -1` = Invoiced Amount / Turnover basis |
| `quantity` | Line quantity in the line's UoM |
| `product_uom_id` | Format `[id, ...]`; id extracted; lookup to `dim_uom` for factor |
| `product_id` | Format `[id, ...]`; id extracted; relationship to product dimension |
| `currency_rate` | Used to compute `price_subtotal_eur = price_subtotal / currency_rate` |
| `price_subtotal` | Line subtotal in document currency |
| `price_unit` | Typed but **not used downstream** |
| `company_currency_id` | Cleaned but **not used downstream** |
| (4 remaining columns) | Not referenced by any step |

### 2.3 `product_template.csv` (products) — parsed as 8 columns, UTF-8
| Field | Used for |
|---|---|
| `id` | Product template id; join key to `product_template_name.xlsx` |
| `product_id` | Product variant id; **relationship key to the fact table** |
| `display_name` | Format `[REF] Name`; split into `internal_reference` and English name |
| `prodin_reference` | Preferred reference (value `"False"` cleaned to empty) |
| `list_price` | Sales list price; used in Standard Price / pricing measures |
| `standard_price` | Cost price; used in Standard Cost / Gross Profit |
| `report_category_name` | Reporting category; blanks replaced with `"Z. Category N/A"` |

### 2.4 `res_currency.csv` (currencies) — parsed as 8 columns, UTF-8
Used: `id`, `currency`, `latest_rate`, `latest_rate_date`, `symbol`. Three unnamed junk columns (``, `_1`, `_2`) are removed.

### 2.5 `res_partner.csv` (customers) — parsed as 4 columns, UTF-8
Used: `id` (key), `commercial_company_name` (customer label in pivots), `country_id` (country name extracted from `[id,"Name"]` quotes).

### 2.6 `product_template_name.xlsx` — Sheet1 (manual mapping, not from Odoo)
Used: `ID` (= product template id), `Naam` (Dutch product name).

---

## 3. Power Query Layer (query by query)

Twelve queries. Load destinations:

| Query | Type | Loads to |
|---|---|---|
| `refresh_date_time` | Utility | Data model |
| `dim_uom` | Static embedded table | Staging only (merged into fact) |
| `dim_company` | Static embedded table | Worksheet table only |
| `dim_date` | Generated calendar | Data model |
| `dim_product_name_dutch` | Source (xlsx) | Staging (merged into `dim_product`) + worksheet |
| `dim_product` | Source + enrichment | Data model |
| `dim_currency` | Source | Data model |
| `dim_partner` | Source | Data model |
| `fact_account_move` | Source (headers) | Staging only |
| `fact_account_move_line` | Source (lines) | Staging only |
| `fact_account_move_line_merged` | Merge | Staging only |
| `Report - Invoiced` | Final fact | **Data model** |

> A leftover connection `Query - Merge1` exists in the workbook but has no corresponding query in the mashup — stale artifact, safe to ignore/remove.

### 3.1 `refresh_date_time`
Reads SharePoint file metadata (`Date modified`) of `account_move.csv` → single-row table `refresh_file_datetime` (datetime) + constant `join_id1 = 1`. Shows when Power Automate last wrote the file.

### 3.2 `dim_uom` (embedded static table)
16 rows hard-coded in the query (uom_id, name, factor). Examples: `1=units (1)`, `4=hours (8)`, `36=100 units (0.01)`, `37=1000 units (0.001)`, `12=kg (1)`, `13=g (1000)`.
Purpose: convert line quantity to product-UoM quantity: `quantity_product_uom = quantity / factor`.

### 3.3 `dim_company` (embedded static table)
3 rows: `1 = VTU - NL (B.V.)`, `3 = VTU - UK (Ltd.)`, `2 = PRM B.V.`. Loaded to a worksheet table only — the fact table's company label is produced by text replacement instead (§3.8).

### 3.4 `dim_date`
Generated calendar: fixed start **2025-04-01**, end = today (`DateTime.LocalNow()`), daily grain.
Columns: `Date`, `Year`, `MonthName` (`"04. Apr"` style, sortable), `MonthNumber`, `yyyy-qq` (`"2025-Q2"`), `WeekNumber` (Monday start via `Date.WeekOfYear(_, Day.Monday)` — note: not true ISO 8601), `Week_2d`, `Year_2d`, `YY-WW` (`"25-14"`), `YearWeekKey` (`Year*100+Week`).

### 3.5 `dim_product_name_dutch`
Reads `product_template_name.xlsx` Sheet1 → `ID` (Int64), `Naam` (text). Manual Dutch-name mapping.

### 3.6 `dim_product`
From `product_template.csv`:
1. Clean `prodin_reference` (`"False"` → empty).
2. Derive `product_name_english` = text after `"] "` in `display_name` (fallback: full `display_name`).
3. Derive `internal_reference` = text between `[` and `]` in `display_name`.
4. Left-join Dutch names on `id = ID` → `product_name_dutch`.
5. `prodin_ref_name` = `"[ref] name"` where name = Dutch name (fallback English), ref = `prodin_reference` (fallback `internal_reference`; if both empty, name only).
6. `display_name_dutch` = `"[internal_reference] Dutch-or-English name"`.
7. Empty `report_category_name` → `"Z. Category N/A"`.

### 3.7 `dim_currency` / `dim_partner`
Straight imports with cleanup as described in §2.4 / §2.5.

### 3.8 `fact_account_move` (invoice headers, staging)
1. Import `account_move.csv` (13 cols).
2. **Filter `State = "posted"`.**
3. Clean `InvoiceDate` (`"False"` → empty), set types.
4. **Filter `accounting_date >= 2025-04-01`.**
5. Remove `InvoiceDate`, `AmountTotal`, `Currency`, `State`, `WriteDate`, `move_type`.
6. Replace `company_id`: `[1,"Van Thiel United B.V."]` → `VTU - NL (B.V.)`; `[3,"van Thiel United UK Ltd."]` → `VTU - UK (Ltd.)`. *(Note: PRM B.V. (id 2) has no replacement rule — if PRM invoices appear they keep the raw `[2,"..."]` value.)*
7. Split `PartnerID` `[id,"name"]` → numeric `PartnerID` + `partner_name` (strip `[` and `]`).

### 3.9 `fact_account_move_line` (invoice lines, staging)
1. Import `account_move_line.csv` (16 cols, cp-1252).
2. Extract numeric ids from `product_uom_id`, `product_id`, `account_move_id` (text between `[` and `,`); `company_currency_id` from quotes.
3. Set numeric types (`debit`, `credit`, `balance`, `quantity`, `currency_rate`, `price_subtotal`, `price_unit`).
4. Left-join `dim_uom` on `product_uom_id = uom_id` → `uom_factor`.
5. `quantity_product_uom` = `quantity / uom_factor` (or `quantity` if factor null).
6. `price_subtotal_eur` = `price_subtotal / currency_rate` (or `price_subtotal` if rate null/0).
7. `special_category` (business classification):
   - `account_id = "800550 Omzet NL Verhuur"` → **"Rental Order"**
   - `product_id` null or in `{3030, 1728, 24, 3566, 7}` → **"Special Category"**
   - `product_id` in `{1242, 1138, 1241, 1243, 1089}` → **"Normal - RSS"**
   - else → **"Normal"**

### 3.10 `fact_account_move_line_merged`
**Inner join** headers × lines on `account_move_id` (so lines of non-posted or pre-2025-04 invoices drop out). Expands from lines: `account_id`, `debit`, `credit`, `balance`, `product_id`, `quantity`, `price_subtotal`, `quantity_product_uom`, `price_subtotal_eur`, `special_category`. Drops `account_move_id`.

### 3.11 `Report - Invoiced` (final fact)
= `fact_account_move_line_merged` + `Invoiced Amount = balance * -1`.
Final columns: `Name`, `PartnerID`, `partner_name`, `CurrencyID`, `CurrencyValue`, `accounting_date`, `company_id`, `account_id`, `debit`, `credit`, `balance`, `product_id`, `quantity`, `price_subtotal`, `quantity_product_uom`, `price_subtotal_eur`, `special_category`, `Invoiced Amount`.

---

## 4. Power Pivot Layer

### 4.1 Model tables
`Report - Invoiced` (fact), `dim_product`, `dim_partner`, `dim_currency`, `dim_date`, `refresh_date_time`.
(`dim_company`, `dim_uom`, `dim_product_name_dutch` and the staging fact queries are **not** in the model.)

### 4.2 Relationships (all many-to-one from the fact)
| From: Report - Invoiced | To | Type |
|---|---|---|
| `PartnerID` | `dim_partner[id]` | Many : 1 |
| `CurrencyID` | `dim_currency[id]` | Many : 1 |
| `accounting_date` | `dim_date[Date]` | Many : 1 |
| `product_id` | `dim_product[product_id]` | Many : 1 |

`refresh_date_time` is standalone (no relationship; displayed on the Refresh DateTime sheet).

### 4.3 Measures
**Implicit (auto-created by Excel):**
On `Report - Invoiced`: `Sum of debit`, `Sum of credit`, `Sum of balance`, `Sum of quantity`, `Sum of price_subtotal_eur`, `Count of price_subtotal_eur`.
On `dim_product`: `Sum of list_price`, `Average of list_price`, `Sum of standard_price`, `Average of standard_price`.

**Explicit (hand-written DAX).** Definitions below are reconstructed from the compressed model binary — intent is confirmed, exact syntax should be verified in Excel (Power Pivot → Manage) before reuse:

| Measure | Reconstructed logic |
|---|---|
| `Avg Sales Price` | `ABS(DIVIDE(SUM('Report - Invoiced'[balance]), SUM('Report - Invoiced'[quantity_product_uom]), BLANK()))` — realized price per unit |
| `Standard Cost` | `SUMX('Report - Invoiced', [quantity_product_uom] * RELATED(dim_product[standard_price]))` — qty × cost price |
| `Standard Price` | per-unit list price: `ABS(DIVIDE(SUMX('Report - Invoiced', [quantity_product_uom] * RELATED(dim_product[list_price])), SUM([quantity_product_uom])))` |
| `Cost of Sales` | Uses `VAR IsRentalOrder = [special_category] = "Rental Order"` — rental lines treated differently (debit-based) from normal lines (qty × standard_price) |
| `Gross Profit` | `SUMX('Report - Invoiced', ...)` with `VAR IsRentalOrder`, `RELATED(dim_product[standard_price])`, `Qty = [quantity_product_uom]`, `Revenue = [balance] * -1`; returns Revenue − Cost per line (rental lines use debit; BLANK handling for missing cost) |
| `Margin %` | `IF(ABS(<revenue>) < 0.01, <blank/0>, DIVIDE([Gross Profit], <revenue>))` — guards against divide-by-tiny-revenue |

### 4.4 Pivot usage
The report pivots use custom captions: **Quantity** = `Sum of quantity`, **Turnover** = `Sum of price_subtotal_eur`. Slicing fields: `dim_partner[commercial_company_name]`, `dim_product[report_category_name]`, `dim_product[display_name]` / `[display_name_dutch]`, `dim_date[Year]` / `[MonthName]` / `[yyyy-qq]`, `Report - Invoiced[company_id]` / `[special_category]` / `[Name]`.

---

## 5. Notes, Quirks & Verification Items

1. **`accounting_date`** — header typo (`accouning_date`) fixed on 2026-07-06 in Power Automate and in the downstream workbook steps. The V1.0 workbook file analyzed here (and its extracted `Section1.m`) still show the old name.
2. **Hard-coded cutoff 2025-04-01** appears twice: fact filter and `dim_date` start.
3. **Currency conversion is done twice, differently:** `price_subtotal_eur` converts document-currency subtotal via `currency_rate`, while `debit`/`credit`/`balance` are already company-currency (EUR). Turnover in pivots uses `price_subtotal_eur`; Invoiced Amount uses `balance * -1`.
4. **Company replacement misses PRM B.V. (id 2)** — see §3.8.
5. **Hard-coded product-id lists** in `special_category` need maintenance when new special/RSS products are added.
6. **`Merge1` connection is stale** (no query behind it).
7. **CSV parsing is fragile:** `QuoteStyle.None` with fixed column counts (13/16/8/4), and `account_move_line.csv` is read as **Windows-1252** while the others are UTF-8. The Python replacement should verify actual delimiter/quoting behavior against real files.
8. **Explicit DAX measures** (§4.3) are reconstructions — export exact definitions from Power Pivot before the analysis phase.
