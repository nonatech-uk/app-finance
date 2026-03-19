# Finance

Personal finance system replacing Bankivity (iBank). Ingests transactions from multiple sources into an immutable raw layer, deduplicates cross-source overlaps, normalises merchants, and categorises spending. Includes stock portfolio tracking, other asset valuations, receipt OCR, and UK CGT calculations.

## Architecture

```
raw_transaction          immutable, append-only source of truth
    │
cleaned_transaction      rule-based merchant string cleaning
    │
canonical_merchant       normalisation layer (query-time lookup)
    │
category                 hierarchical taxonomy
    │
economic_event           links related transactions (transfers, FX)
```

All raw transactions are preserved exactly as received. Everything above is a derived projection that can be reprocessed from raw data at any time.

See [SCHEMA.md](SCHEMA.md) for the full database schema, canonical query patterns, and guidance for external consumers (e.g. MCP servers).

## Data Sources

| Source | Method | Notes |
|--------|--------|-------|
| Monzo | Direct API (OAuth) | Current account + business, daily sync |
| Wise | Activities API + CSV | Multi-currency, daily sync |
| First Direct | CSV export | Manual upload via UI |
| iBank (Bankivity) | Historical migration | 2014–2026, mostly superseded by API sources |
| Amazon | Order history CSV | Matched to transactions for split suggestions |
| Cash | Manual entry via UI | For cash spending tracking |

## Deduplication

Four rules, run in order:

1. **`source_superseded`** — blanket suppression of an unreliable source for an account where another source is authoritative
2. **`declined`** — suppress Monzo API transactions with `decline_reason` set (never settled)
3. **`ibank_internal`** — same source, same (date, amount, currency, merchant)
4. **`cross_source_date_amount`** — different sources, same (institution, account_ref, date, amount, currency) with ROW_NUMBER positional matching

Source priority: monzo_api/wise_api (1) > first_direct_csv/wise_csv (2) > ibank (3)

The `active_transaction` view provides a deduplicated lens over raw data without modifying it.

## Stack

- **Python 3.12+**
- **PostgreSQL** (on NAS)
- **FastAPI** (REST API on :8000)
- **React 19 + Vite + TypeScript + Tailwind v4 + TanStack Query + Recharts** (UI served by FastAPI in production, Vite dev server on :5173 locally)
- **psycopg2** (sync DB access with connection pool)
- **Podman** (daily sync container on NAS)

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example config/.env  # fill in DB credentials and API tokens
```

## API

FastAPI on `:8000`, all endpoints under `/api/v1/`:

| Method | Path | Notes |
|--------|------|-------|
| GET | /transactions | Cursor-paginated, 15+ filter params |
| GET | /transactions/{id} | Full detail + dedup group + economic event + splits |
| POST | /transactions/{id}/link-transfer | Link two transactions as transfer/FX |
| POST | /transactions/bulk/* | Bulk category, tag, note, merchant operations |
| GET | /accounts | Derived from active_transaction + account metadata |
| GET | /accounts/favourites | Favourite accounts with balances |
| GET | /categories | Recursive tree |
| GET | /categories/spending | Aggregated with date range + split handling |
| GET | /merchants | Cursor-paginated, search, sort, scope filter |
| POST | /merchants/bulk-merge | Merge multiple merchants |
| POST | /categorisation/run | Trigger categorisation engine |
| GET | /stocks/holdings | Portfolio with computed P&L |
| GET | /stocks/cgt | UK Capital Gains Tax calculations |
| GET | /assets/summary | Other asset valuations |
| POST | /receipts/upload | Receipt OCR + auto-match |
| GET | /tag-rules | Automatic tagging rules |
| GET | /stats/monthly | Income/expense by month |
| GET | /stats/overview | Dashboard summary stats |
| GET | /settings | App configuration |
| GET | /health | Pool status |

```bash
# Start API (from project root)
uvicorn src.api.app:app --host 0.0.0.0 --port 8000
```

## UI

React SPA with dark theme. Pages: Dashboard, Transactions, Accounts, AccountDetail, Categories, Merchants, Stocks, Assets, Receipts.

```bash
cd ui && npm run dev   # :5173, proxies /api -> :8000
```

## Scripts

```bash
# Data ingestion
python scripts/monzo_bulk_load.py           # Monzo API -> raw_transaction
python scripts/wise_bulk_load.py            # Wise Activities API -> raw_transaction
python scripts/wise_csv_load.py             # Wise CSV export -> raw_transaction
python scripts/fd_csv_load.py               # First Direct CSV -> raw_transaction
python scripts/load_ibank_transactions.py   # iBank/Bankivity migration

# Processing pipeline
python scripts/run_cleaning.py              # Apply merchant cleaning rules
python scripts/run_dedup.py                 # Cross-source deduplication
python scripts/run_dedup.py --stats         # View dedup statistics

# Daily sync (runs inside container)
python scripts/daily_sync.py                # Wise + Monzo sync, clean, dedup

# Ancillary
python scripts/amazon_load.py              # Amazon order history matching
python scripts/load_ibank_categories.py    # Category taxonomy from iBank
```

## Deployment

Podman container on the NAS (192.168.128.9). Multi-stage build: Node builds the React SPA, Python serves it via FastAPI alongside the API. The container runs two services:

- **FastAPI** (port 8000) — API + static UI, behind Authelia via Traefik
- **Monzo OAuth server** (port 9876) — handles Monzo authentication callbacks

Traefik routes `finance.mees.st` through Authelia forward-auth to the API, with `/oauth/*` bypassing auth for Monzo callbacks.

```bash
# Build and run
./deploy/run.sh

# Manual sync
podman exec finance-sync python scripts/daily_sync.py

# Install systemd timer (3am daily)
cp deploy/finance-sync.{service,timer} /etc/systemd/system/
systemctl enable --now finance-sync.timer
```

Monzo re-authentication available at `https://finance.mees.st/oauth/callback` flow.

## Project Structure

```
config/
    settings.py          # Pydantic settings from .env
src/
    ingestion/
        monzo.py         # Monzo OAuth + transaction fetcher
        monzo_auth.py    # Persistent Monzo auth server for container
        wise.py          # Wise API client (activities, card, transfer detail)
        wise_fx.py       # FX event builder for Wise statements
        writer.py        # Raw layer writer (idempotent)
    cleaning/
        rules.py         # Institution-specific cleaning rules
        processor.py     # Batch cleaning pipeline
        matcher.py       # Canonical merchant matching (exact/prefix/fuzzy)
    dedup/
        config.py        # Source priorities, supersession, cross-source pairs
        matcher.py       # Dedup matching engine
    api/
        app.py           # FastAPI app + lifespan + CORS + SPA serving
        deps.py          # Connection pool + auth (Authelia headers)
        models.py        # Pydantic response models
        queries.py       # Reusable query builders (importable by MCP servers)
        routers/         # transactions, accounts, categories, merchants,
                         # stats, stocks, assets, receipts, tag_rules, settings, cash
scripts/                 # CLI entry points + daily_sync orchestrator
deploy/
    run.sh               # Podman build + run
    finance-sync.service # Systemd oneshot for daily sync
    finance-sync.timer   # 3am daily trigger
ui/                      # React + Vite + TypeScript
Containerfile            # Multi-stage build (Node + Python)
SCHEMA.md                # Full database schema for external consumers
DECISIONS.md             # Architecture & design decisions
```

## License

MIT
