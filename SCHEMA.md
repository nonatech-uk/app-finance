# Database Schema — MCP Consumer Reference

This document describes the PostgreSQL schema for the personal finance system.
It is written for read-only consumers (MCP servers, reporting tools, LLMs) that
need to understand the data model without reading application code.

**Database**: PostgreSQL on `192.168.128.9:5432/finance`

---

## Overview

The system is **event-sourced**: `raw_transaction` is the immutable, append-only
source of truth. Every other table is either a projection (cleaning, dedup,
merchant resolution) or user-contributed metadata (overrides, notes, tags).

Data flows through a pipeline:

```
Bank APIs / CSVs / iBank export
  → raw_transaction          (immutable ingest)
  → cleaned_transaction      (normalised merchant strings)
  → merchant_raw_mapping     (links cleaned strings → canonical merchants)
  → dedup_group/member       (removes duplicates across sources)
  → active_transaction VIEW  (the "live" set, excluding dedup losers)
```

### Key principle for consumers

**Always query `active_transaction`, never `raw_transaction` directly**, unless
you specifically need to see suppressed/deduplicated records. The view excludes
rows that lost deduplication (non-preferred dedup group members).

---

## Volume and Date Characteristics

| Metric | Approximate Value |
|--------|-------------------|
| `raw_transaction` rows | ~27,000 |
| `active_transaction` rows | ~15,000 |
| Date range | 2014 – present |
| Currencies | GBP, EUR, USD (GBP dominant) |
| Accounts | ~10 active |
| `canonical_merchant` rows | ~4,000 |
| `category` rows | ~140 |

Data arrives from: Monzo API, Wise API, First Direct CSV, Wise CSV, iBank
(Bankivity) migration, manual cash entries. The `source` column on
`raw_transaction` identifies origin.

---

## Tables — Grouped by Domain

### 1. Core Transaction Layer

#### `raw_transaction` — The source of truth

Immutable, append-only. Every bank transaction lands here exactly once per
source. Never UPDATE or DELETE rows (except via account deletion).

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid PK | Stable identifier, referenced everywhere |
| `ingested_at` | timestamptz | When the row was written (internal) |
| `source` | text | Origin: `monzo_api`, `wise_api`, `first_direct_csv`, `wise_csv`, `ibank`, `cash`, `opening_balance` |
| `institution` | text | Bank name: `monzo`, `first_direct`, `wise` |
| `account_ref` | text | Bank's account identifier (e.g. `acc_000...` for Monzo) |
| `transaction_ref` | text | Bank's transaction ID, used for idempotent ingest |
| `posted_at` | date | **Human-meaningful.** When the transaction occurred |
| `amount` | numeric(18,4) | **Human-meaningful.** Signed: negative = money out, positive = money in |
| `currency` | char(3) | ISO currency code |
| `raw_merchant` | text | Original merchant string from the bank (before cleaning) |
| `raw_memo` | text | Original memo/description from the bank |
| `is_dirty` | boolean | True for iBank-sourced rows (lower quality data) |
| `raw_data` | jsonb | Full API response or CSV row, preserved for debugging |

**Invariant**: Rows are never modified after insert. Corrections are handled by
inserting new rows and using dedup rules to suppress the old ones.

#### `active_transaction` — VIEW (use this for reporting)

```sql
SELECT * FROM raw_transaction rt
WHERE NOT EXISTS (
    SELECT 1 FROM dedup_group_member dgm
    WHERE dgm.raw_transaction_id = rt.id AND NOT dgm.is_preferred
);
```

Same columns as `raw_transaction`. Excludes rows that lost deduplication.
**This is the canonical set of transactions for all reporting queries.**

#### `account` — Account metadata

Enriches the (institution, account_ref) pairs found in transactions.
Not every account has a row here — accounts are auto-created when metadata
is first set.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid PK | Internal identifier |
| `institution` | text | Matches `raw_transaction.institution` |
| `account_ref` | text | Matches `raw_transaction.account_ref` |
| `name` | text | Internal name |
| `display_name` | text | **Human-meaningful.** User-chosen label |
| `currency` | char(3) | Primary currency |
| `account_type` | text | `current`, `savings`, `credit_card`, `investment`, `cash`, `pension`, `property`, `vehicle`, `mortgage` |
| `is_active` | boolean | Whether the account is still in use |
| `is_archived` | boolean | Hidden from default views |
| `exclude_from_reports` | boolean | Excluded from spending/income reports |
| `scope` | text | `personal` or `business` — controls access and filtering |
| `display_order` | integer | Non-null = favourite account, sorted by this |
| `is_taxable` | boolean | Flag for tax-relevant accounts |

**Join pattern**: `account a ON a.institution = rt.institution AND a.account_ref = rt.account_ref`

#### `account_alias` — Legacy account reference mapping

Maps old account refs to canonical ones. Used during iBank migration.

#### `account_transfer_relationship` — Transfer pair hints

Schema only — not currently populated via the UI. For future automatic
transfer matching between known account pairs.

---

### 2. Cleaning & Merchant Resolution

The pipeline normalises raw merchant strings into canonical merchants:

```
raw_transaction.raw_merchant
  → cleaned_transaction.cleaned_merchant   (normalised string)
  → merchant_raw_mapping                   (links to canonical)
  → canonical_merchant                     (the entity)
  → category (via category_hint)           (spending category)
```

#### `cleaned_transaction` — Normalised merchant strings

One row per `raw_transaction`. Produced by `scripts/run_cleaning.py`.

| Column | Type | Purpose |
|--------|------|---------|
| `raw_transaction_id` | uuid FK | Links back to `raw_transaction.id` |
| `cleaned_merchant` | text | **Key column.** Normalised merchant string (lowercased, stripped of noise) |
| `cleaning_version` | text | Version of cleaning rules used |
| `cleaning_rules` | text[] | Which rules fired |
| `is_fee` | boolean | Transaction flagged as a bank fee |
| `confidence` | numeric(4,3) | Cleaning confidence score |

#### `merchant_raw_mapping` — Cleaned string → canonical merchant

Maps each distinct `cleaned_merchant` string to a `canonical_merchant`. Many
cleaned strings can map to one canonical (e.g. "tesco express" and "tesco metro"
both map to "Tesco").

| Column | Type | Purpose |
|--------|------|---------|
| `cleaned_merchant` | text PK | The normalised string |
| `canonical_merchant_id` | uuid FK | Points to `canonical_merchant.id` |
| `match_type` | text | How the match was made: `exact`, `fuzzy`, `manual` |
| `confidence` | numeric(4,3) | Match confidence |
| `mapped_by` | text | `auto` or `human` |

#### `canonical_merchant` — The merchant entity

The deduplicated merchant directory. Each represents one real-world business.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid PK | Stable identifier |
| `name` | text | **Human-meaningful.** Canonical name (from cleaning) |
| `display_name` | text | **Human-meaningful.** User-chosen display name (overrides `name`) |
| `category_hint` | text FK | Points to `category.full_path` — the merchant's default category |
| `category_method` | text | How category was set: `human`, `source_hint`, `llm`, `fuzzy_merge` |
| `category_confidence` | numeric(3,2) | Confidence in the category assignment |
| `category_set_at` | timestamptz | When category was last set |
| `merged_into_id` | uuid | If non-null, this merchant was merged into another. **Filter with `WHERE merged_into_id IS NULL`** |

**Important**: Always filter `merged_into_id IS NULL` when listing active merchants.

#### `merchant_display_rule` — Regex-based display name rules

Pattern-matching rules that auto-set display names and optionally merge
merchants. Applied by the categorisation engine.

#### `merchant_split_rule` — Amount-based merchant routing

When a cleaned_merchant matches a pattern AND amount matches criteria, the
transaction's canonical merchant is overridden. Used for splitting generic
merchants (e.g. routing "Amazon" transactions of specific amounts to
sub-merchants).

---

### 3. Deduplication

The same real-world transaction may appear in multiple sources (e.g. iBank
export AND Monzo API). The dedup system groups these and picks one "preferred"
member per group.

#### `dedup_group` — A set of duplicate transactions

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid PK | Group identifier |
| `canonical_id` | uuid | The preferred transaction's ID |
| `match_rule` | text | Which rule matched: `source_superseded`, `declined`, `ibank_internal`, `cross_source_date_amount` |
| `confidence` | numeric(3,2) | Match confidence |

#### `dedup_group_member` — Members of a dedup group

| Column | Type | Purpose |
|--------|------|---------|
| `dedup_group_id` | uuid FK | Points to `dedup_group.id` |
| `raw_transaction_id` | uuid FK | Points to `raw_transaction.id` |
| `is_preferred` | boolean | **True = this row survives into `active_transaction`**. False = suppressed |

**Dedup rules** (applied in order):
1. **Rule 0 `source_superseded`**: Blanket suppression of unreliable sources for specific accounts (e.g. all iBank data for Monzo/FD accounts)
2. **Rule 0b `declined`**: Suppress Monzo transactions with `decline_reason` set
3. **Rule 1 `ibank_internal`**: Same source duplicates (same date, amount, currency, merchant)
4. **Rule 2 `cross_source_date_amount`**: Cross-source matches with positional matching

Source priority: `monzo_api`/`wise_api` (1) > `first_direct_csv`/`wise_csv` (2) > `ibank` (3)

---

### 4. Category System

#### `category` — Hierarchical spending categories

Tree structure using materialised paths.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid PK | Stable identifier |
| `full_path` | text UNIQUE | **Human-meaningful.** Materialised path, colon-separated: `Food:Groceries`, `Transport:Fuel` |
| `name` | text | Leaf name: `Groceries`, `Fuel` |
| `parent_id` | uuid FK | Self-referential tree |
| `category_type` | text | `income` or `expense` |
| `is_active` | boolean | Soft delete |

**Special paths**: Paths starting with `+` are system categories excluded from
reports: `+Transfer`, `+Ignore`.

#### Category resolution order (most to least specific)

A transaction's effective category is resolved through this precedence chain:

1. `transaction_category_override.category_path` — explicit user/system override
2. `transaction_merchant_override` → `canonical_merchant.category_hint` — override merchant's category
3. `canonical_merchant.category_hint` (via cleaning chain) — default merchant category

For **split transactions**, each `transaction_split_line` has its own `category_path`.

#### `transaction_category_override` — Per-transaction category override

| Column | Type | Purpose |
|--------|------|---------|
| `raw_transaction_id` | uuid PK | One override per transaction |
| `category_path` | text | Points to `category.full_path` |
| `source` | text | `user` (manual) or `system` (auto-set, e.g. `+Transfer`) |

#### `source_category_mapping` — Bank-provided category hints

Maps source-specific category strings (e.g. Monzo's category names) to
internal categories. Used by the categorisation engine to generate suggestions.

#### `category_suggestion` — Pending category assignments for review

Machine-generated category suggestions that await human review.

| Column | Type | Purpose |
|--------|------|---------|
| `canonical_merchant_id` | uuid FK | Which merchant this suggestion is for |
| `suggested_category_id` | uuid FK | The suggested category |
| `method` | text | `source_hint`, `llm`, `fuzzy_merge` |
| `confidence` | numeric(3,2) | 0.00–1.00 |
| `status` | text | `pending`, `accepted`, `rejected` |

---

### 5. User Annotations

These tables store user-contributed metadata on transactions.

#### `transaction_note` — Free-text notes

One note per transaction. Source is `user` or `system`.

#### `transaction_tag` — Tags on transactions

| Column | Type | Purpose |
|--------|------|---------|
| `raw_transaction_id` | uuid FK | |
| `tag` | text | The tag string |
| `source` | text | `user` (manual) or `rule` (auto-applied by tag_rule) |
| `tag_rule_id` | integer FK | If source=`rule`, which rule created this |

**Unique constraint**: `(raw_transaction_id, tag)` — one tag per transaction.

#### `tag_rule` — Automatic tagging rules

Rules that auto-apply tags to transactions matching criteria (date range,
account, merchant pattern, category pattern). Applied via full reconciliation.

#### `transaction_merchant_override` — Per-transaction merchant override

Overrides the canonical merchant for a specific transaction. Created by
split rules or manual user action.

#### `transaction_split_line` — Split transaction lines

Divides a single transaction into multiple lines with individual amounts and
categories. Lines must sum to the parent transaction's amount.

#### `tag` — Tag definitions (legacy)

Original tag registry. Largely superseded by the freeform `transaction_tag.tag`
strings. May be removed in future.

---

### 6. Economic Events (Transfers & FX)

Links two transactions as a transfer or FX conversion.

#### `economic_event` — The event

| Column | Type | Purpose |
|--------|------|---------|
| `event_type` | text | `inter_account_transfer` or `fx_conversion` |
| `match_status` | text | `single_leg`, `manual`, `auto` |
| `description` | text | Human-readable summary, e.g. "500.00 GBP -> 580.00 EUR" |

#### `economic_event_leg` — Transaction legs

Each event has 2 legs (source and target).

| Column | Type | Purpose |
|--------|------|---------|
| `economic_event_id` | uuid FK | Parent event |
| `raw_transaction_id` | uuid FK | The transaction |
| `leg_type` | text | `source` (debit) or `target` (credit) |

#### `fx_event` — FX details for cross-currency events

Stores exchange rate details when the two legs are in different currencies.

#### `event_tag` — Tags on events

Associates tags with economic events (not widely used yet).

---

### 7. Stock Portfolio

Separate from the transaction pipeline. Manually entered holdings and trades.

#### `stock_holding` — Ticker-level positions

| Column | Type | Purpose |
|--------|------|---------|
| `symbol` | text UNIQUE | Ticker symbol (e.g. `AAPL`) |
| `name` | text | Company name |
| `country` | char(2) | Country code |
| `currency` | char(3) | Trading currency |
| `scope` | text | `personal` or `business` |

#### `stock_trade` — Buy/sell records

| Column | Type | Purpose |
|--------|------|---------|
| `holding_id` | uuid FK | Which holding |
| `trade_type` | text | `buy` or `sell` |
| `trade_date` | date | When traded |
| `quantity` | numeric(18,6) | Shares |
| `price_per_share` | numeric(18,4) | Price in holding's currency |
| `total_cost` | numeric(18,4) | Computed: quantity × price ± fees |
| `fees` | numeric(18,4) | Trading fees |
| `gbp_total_cost` | numeric | GBP equivalent (for UK CGT calculations) |

**Current shares** = `SUM(CASE WHEN trade_type='buy' THEN quantity ELSE -quantity END)`

#### `stock_price` — Cached daily close prices

Fetched from Yahoo Finance. Unique on `(holding_id, price_date)`.

#### `stock_dividend` — Dividend records (schema only, no UI yet)

#### `fx_rate` — Cached FX rates

Daily exchange rates for GBP valuation of foreign holdings.

#### `tax_year_income` — UK tax year income data

Used by the CGT calculator to determine basic/higher rate tax bands.
Tax years are formatted as `YYYY/YY` (e.g. `2025/26`).

---

### 8. Other Assets

Manually valued non-stock assets (property, vehicles, etc.).

#### `asset_holding` — Asset register

| Column | Type | Purpose |
|--------|------|---------|
| `name` | text | **Human-meaningful.** Asset description |
| `asset_type` | text | `property`, `vehicle`, `other`, etc. |
| `currency` | char(3) | Valuation currency |

#### `asset_valuation` — Point-in-time valuations

| Column | Type | Purpose |
|--------|------|---------|
| `holding_id` | uuid FK | Which asset |
| `valuation_date` | date | As-of date |
| `gross_value` | numeric(18,4) | Market value |
| `tax_payable` | numeric(18,4) | Estimated tax liability |

**Net value** = `gross_value - tax_payable` (computed in application, not stored).

---

### 9. Receipts

Receipt images/PDFs with OCR extraction and transaction matching.

#### `receipt`

| Column | Type | Purpose |
|--------|------|---------|
| `id` | uuid PK | |
| `original_filename` | text | Upload filename |
| `mime_type` | text | File type |
| `file_path` | text | Relative path on disk |
| `ocr_status` | text | `pending`, `completed`, `failed` |
| `extracted_date` | date | OCR-extracted transaction date |
| `extracted_amount` | numeric(18,4) | OCR-extracted amount |
| `extracted_currency` | char(3) | OCR-extracted currency |
| `extracted_merchant` | text | OCR-extracted merchant name |
| `match_status` | text | `pending_ocr`, `pending_match`, `auto_matched`, `manually_matched`, `no_match` |
| `matched_transaction_id` | uuid FK | The matched `raw_transaction.id` |
| `source` | text | `web` or `email` |

---

### 10. Amazon Order Matching

#### `amazon_order_item` — Scraped Amazon order history

Line items from Amazon orders. Used to suggest split lines for Amazon
transactions.

#### `amazon_order_match` — Links orders to transactions

Maps Amazon order IDs to `raw_transaction` rows for split suggestions.

---

### 11. Application & Auth

#### `app_user` — Application users

| Column | Type | Purpose |
|--------|------|---------|
| `email` | text PK | User's email (from Authelia) |
| `allowed_scopes` | text[] | `{personal}`, `{business}`, or `{personal,business}` |
| `role` | text | `admin` (read-write) or `readonly` |

#### `app_setting` — Key-value configuration store

Stores application settings (CalDAV, receipt config, webhook secrets, API keys).
Keys are dot-namespaced: `caldav.enabled`, `receipt.alert_days`, etc.

#### `alert` — System alerts (schema only, minimal use)

#### `ob_connection` — Open Banking connections (future)

#### `recurring_pattern` — Detected recurring transactions (future)

---

### 12. Xero Integration

#### `xero_account_mapping` — Category → Xero account code mapping

#### `xero_sync_log` — Tracks which transactions have been pushed to Xero

---

## Canonical Query Patterns

These are the standard join patterns used throughout the application.
A read-only consumer should replicate these.

### Pattern 1: Full transaction with merchant and category

This is the standard query for listing transactions with resolved merchant
names and categories. Used by the transaction list, account detail, and
spending reports.

```sql
SELECT
    rt.id, rt.posted_at, rt.amount, rt.currency,
    rt.raw_merchant, rt.raw_memo,
    rt.institution, rt.account_ref, rt.source,
    -- Merchant resolution (override > default)
    COALESCE(cm_override.display_name, cm_override.name,
             cm.display_name, cm.name) AS merchant_name,
    -- Category resolution (override > override merchant > default merchant)
    COALESCE(tcat.full_path, cat_override.full_path,
             cat.full_path) AS category_path,
    COALESCE(tcat.category_type, cat_override.category_type,
             cat.category_type) AS category_type
FROM active_transaction rt
-- Account metadata (for scope filtering, archive check)
LEFT JOIN account a
    ON a.institution = rt.institution AND a.account_ref = rt.account_ref
-- Cleaning chain: raw → cleaned → mapping → canonical merchant
LEFT JOIN cleaned_transaction ct ON ct.raw_transaction_id = rt.id
LEFT JOIN merchant_raw_mapping mrm ON mrm.cleaned_merchant = ct.cleaned_merchant
LEFT JOIN canonical_merchant cm ON cm.id = mrm.canonical_merchant_id
-- Merchant override (split rules or manual)
LEFT JOIN transaction_merchant_override tmo ON tmo.raw_transaction_id = rt.id
LEFT JOIN canonical_merchant cm_override ON cm_override.id = tmo.canonical_merchant_id
-- Category from default merchant
LEFT JOIN category cat ON cat.full_path = cm.category_hint
-- Category from override merchant
LEFT JOIN category cat_override ON cat_override.full_path = cm_override.category_hint
-- Explicit category override (highest priority)
LEFT JOIN transaction_category_override tco ON tco.raw_transaction_id = rt.id
LEFT JOIN category tcat ON tcat.full_path = tco.category_path
WHERE a.is_archived IS NOT TRUE
ORDER BY rt.posted_at DESC, rt.id DESC
```

### Pattern 2: Account balances

Balance is simply the sum of all active transactions for an account.
There is no separate balance table.

```sql
SELECT
    rt.institution, rt.account_ref, rt.currency,
    COUNT(*) AS transaction_count,
    MIN(rt.posted_at) AS earliest_date,
    MAX(rt.posted_at) AS latest_date,
    SUM(rt.amount) AS balance
FROM active_transaction rt
LEFT JOIN account a
    ON a.institution = rt.institution AND a.account_ref = rt.account_ref
WHERE a.is_archived IS NOT TRUE
GROUP BY rt.institution, rt.account_ref, rt.currency
```

### Pattern 3: Monthly income/expense

```sql
SELECT
    TO_CHAR(rt.posted_at, 'YYYY-MM') AS month,
    SUM(CASE WHEN rt.amount > 0 THEN rt.amount ELSE 0 END) AS income,
    SUM(CASE WHEN rt.amount < 0 THEN rt.amount ELSE 0 END) AS expense,
    SUM(rt.amount) AS net
FROM active_transaction rt
LEFT JOIN account a
    ON a.institution = rt.institution AND a.account_ref = rt.account_ref
WHERE rt.currency = 'GBP'
  AND a.is_archived IS NOT TRUE
  -- Exclude system categories (+Transfer, +Ignore)
  AND NOT EXISTS (
      SELECT 1 FROM transaction_category_override tco
      WHERE tco.raw_transaction_id = rt.id
        AND tco.category_path LIKE '+%'
  )
GROUP BY TO_CHAR(rt.posted_at, 'YYYY-MM')
ORDER BY month DESC
```

### Pattern 4: Spending by category (handles splits)

Split transactions have per-line categories. Unsplit transactions use the
standard resolution chain. The `effective_lines` CTE unifies both:

```sql
WITH effective_lines AS (
    -- Unsplit: standard category resolution
    SELECT rt.amount, COALESCE(tcat.full_path, cat_override.full_path,
                                cat.full_path) AS category_path
    FROM active_transaction rt
    LEFT JOIN ... -- (full merchant/category chain as above)
    WHERE NOT EXISTS (SELECT 1 FROM transaction_split_line sl
                      WHERE sl.raw_transaction_id = rt.id)
    UNION ALL
    -- Split: each line has its own category
    SELECT sl.amount, sl.category_path
    FROM active_transaction rt
    JOIN transaction_split_line sl ON sl.raw_transaction_id = rt.id
)
SELECT category_path, SUM(amount) AS total, COUNT(*) AS txn_count
FROM effective_lines
WHERE category_path NOT LIKE '+%'
GROUP BY category_path
```

### Pattern 5: Scope filtering

All user-facing queries filter by scope (personal/business) based on the
authenticated user's `allowed_scopes`. The account table is the join point:

```sql
-- For a specific scope:
WHERE a.scope = 'personal'

-- For all of a user's scopes:
WHERE a.scope = ANY('{personal,business}'::text[])
```

### Pattern 6: Tags

```sql
-- Get tags for transactions (batch):
SELECT raw_transaction_id, array_agg(tag ORDER BY tag)
FROM transaction_tag
WHERE raw_transaction_id = ANY('{...}'::uuid[])
GROUP BY raw_transaction_id

-- Filter transactions by tag:
WHERE EXISTS (
    SELECT 1 FROM transaction_tag tt
    WHERE tt.raw_transaction_id = rt.id AND tt.tag = 'holiday'
)
```

### Pattern 7: Keyset pagination

The transaction list uses keyset pagination on `(posted_at, id)`:

```sql
WHERE (rt.posted_at, rt.id) < ('2025-01-15'::date, 'uuid-here'::uuid)
ORDER BY rt.posted_at DESC, rt.id DESC
LIMIT 51  -- fetch limit+1 to detect has_more
```

---

## Tables a Consumer Should Query vs Avoid

### Query freely (read-only safe)

| Table | Notes |
|-------|-------|
| `active_transaction` (view) | **Primary data source.** Always use this, not `raw_transaction` |
| `account` | Account metadata. Join on (institution, account_ref) |
| `category` | Full category tree |
| `canonical_merchant` | Merchant directory. Filter `merged_into_id IS NULL` |
| `merchant_raw_mapping` | Cleaned string → merchant mapping |
| `cleaned_transaction` | Normalised merchant strings |
| `transaction_category_override` | Category overrides |
| `transaction_merchant_override` | Merchant overrides |
| `transaction_note` | User notes |
| `transaction_tag` | Tags |
| `transaction_split_line` | Split line items |
| `economic_event` / `economic_event_leg` / `fx_event` | Transfer/FX links |
| `dedup_group` / `dedup_group_member` | Dedup info (for diagnostics) |
| `stock_holding` / `stock_trade` / `stock_price` / `stock_dividend` | Portfolio data |
| `asset_holding` / `asset_valuation` | Other assets |
| `receipt` | Receipt records |
| `amazon_order_item` / `amazon_order_match` | Amazon order data |
| `tag_rule` | Automatic tagging rules |
| `category_suggestion` | Pending category suggestions |
| `tax_year_income` | Tax year income for CGT |
| `fx_rate` | Cached exchange rates |

### Avoid or use with caution

| Table | Reason |
|-------|--------|
| `raw_transaction` | Use `active_transaction` instead — raw includes dedup losers |
| `app_user` | Contains auth data |
| `app_setting` | Contains secrets (API keys, webhook tokens) |
| `ob_connection` | Contains OAuth tokens |
| `alert` | Minimal use, mostly empty |
| `recurring_pattern` | Schema exists but not yet populated |
| `tag` | Legacy table, largely unused |
| `xero_account_mapping` / `xero_sync_log` | Xero integration internals |
| `merchant_display_rule` / `merchant_split_rule` | Rule configuration, not data |

---

## Business Logic in the Database

### Views

- **`active_transaction`**: The only view. Filters `raw_transaction` to exclude
  non-preferred dedup group members. Definition shown above.

### No triggers or functions

All business logic lives in application code (Python). There are no database
triggers, stored procedures, or computed columns. The database is a simple
store.

### Constraints of note

- `stock_trade.trade_type` CHECK: must be `buy` or `sell`
- `app_user.role` CHECK: must be `admin` or `readonly`
- `tax_year_income.tax_year` CHECK: must match `^\d{4}/\d{2}$`
- `canonical_merchant.name` has a UNIQUE constraint
- `stock_holding.symbol` has a UNIQUE constraint
- `stock_price` has a UNIQUE constraint on `(holding_id, price_date)`
- `transaction_category_override` has PK on `raw_transaction_id` (one override per txn)
- `transaction_tag` has UNIQUE on `(raw_transaction_id, tag)`
