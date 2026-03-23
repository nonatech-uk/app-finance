# CLAUDE.md - Project State & Handoff

*Read this file first. Read DECISIONS.md and SCHEMA.md for architecture. This file captures current state for session continuity.*

---

## Quick Start

```bash
cd /Users/stu/Documents/Code/finance

# Activate venv (Python 3.12.12)
source .venv/bin/activate

# Start API (from project root, not ui/)
uvicorn src.api.app:app --host 0.0.0.0 --port 8000

# Start UI (separate terminal)
cd ui && npm run dev   # runs on :5173, proxies /api -> :8000
```

**DB**: PostgreSQL on `192.168.128.9:5432/finance` (credentials in `config/.env`)
**GitHub**: `git@github.com:nonatech-uk/finance.git`
**Owner**: Stuart Bevan

---

## What This Is

Personal finance system replacing Bankivity (iBank). Event-sourced: `raw_transaction` is immutable append-only source of truth. Everything above is a projection.

**Stack**: Python 3.12, FastAPI, psycopg2, PostgreSQL, React + Vite + TypeScript + Tailwind v4 + TanStack Query + Recharts.

---

## Current State (as of Feb 2026)

### Git History (main)
1. `1d9adcf` - Initial commit: data pipeline + loaders
2. `32a0938` - FastAPI REST API
3. `71e42b3` - React UI
4. `f76e72d` - CLAUDE.md project state handoff
5. `63a7471` - Podman daily sync container + FD dedup fix
6. `684e71c` - Update deploy script for NAS paths and healthchecks

### What Was Fixed
- **fd_5682 balance**: Was £68,634, now £3,224.61 (correct). All iBank transactions suppressed via `source_superseded` rule. CSV gap (Mar 2024 - Jan 2025) filled from additional CSV exports.
- **fd_8897 balance**: Now -£3,562.41 (correct). Same approach — all iBank suppressed.
- **Opening balance transactions**: Synthetic opening balance records inserted for both accounts to anchor the running total.
- **Monzo current balance**: Was inflated due to (a) iBank data pollution, (b) 213 declined API transactions. Fixed by: suppressing all Monzo iBank via SOURCE_SUPERSEDED, adding `suppress_declined()` Rule 0b to suppress transactions with `decline_reason` set. Final balance: £326.30 ✓
- **Monzo business account**: `acc_0000AvlkSBLkRzlxFfskfT`, 127 transactions, balance £6,943.38 ✓ (no iBank data, no dedup needed)
- **Dedup system**: Added `source_superseded` as Rule 0, `declined` as Rule 0b. Runs before all other dedup rules.

### Database Counts
- `raw_transaction`: ~26,800 rows (includes gap-filling CSVs + opening balances)
- `active_transaction` (view): ~14,600 rows
- `dedup_group`: ~14,600 groups
- `canonical_merchant`: 3,891
- `category`: 136

### Data Sources Loaded
| Source | Count | Notes |
|--------|-------|-------|
| ibank | 17,550 | Bankivity migration, `is_dirty=true`, covers 2014-2026. **Suppressed** for FD + Monzo accounts |
| monzo_api | 3,798 | Direct API, full history. 213 declined txns suppressed |
| first_direct_csv | ~4,500 | Bank CSV export, covers 2020-02 to 2026-02 (gap filled) |
| wise_api | 610 | Direct API |
| wise_csv | 246 | CSV supplement |

---

## Architecture Overview

### Data Pipeline
```
Bank APIs / CSVs / iBank export
        ↓
  scripts/*_load.py        → raw_transaction (immutable, append-only)
        ↓
  scripts/run_cleaning.py  → cleaned_transaction + merchant_raw_mapping + canonical_merchant
        ↓
  scripts/run_dedup.py     → dedup_group + dedup_group_member
        ↓
  active_transaction VIEW  → excludes non-preferred dedup group members
        ↓
  FastAPI REST API          → src/api/
        ↓
  React UI                  → ui/
```

### Dedup Logic (4 rules, run in order)
- **Rule 0** `source_superseded`: Blanket suppression of unreliable source for an account (e.g. iBank suppressed for FD + Monzo accounts)
- **Rule 0b** `declined`: Suppress Monzo API transactions with `decline_reason` set (never settled, excluded from CSV/balance)
- **Rule 1** `ibank_internal`: Same source, same (date, amount, currency, merchant)
- **Rule 2** `cross_source_date_amount`: Different sources, same (institution, account_ref, date, amount, currency) — uses ROW_NUMBER positional matching
- Source priority: monzo_api/wise_api (1) > first_direct_csv/wise_csv (2) > ibank (3)
- Config in `src/dedup/config.py`, matcher in `src/dedup/matcher.py`

### active_transaction VIEW
```sql
SELECT * FROM raw_transaction rt
WHERE NOT EXISTS (
  SELECT 1 FROM dedup_group_member dgm
  WHERE dgm.raw_transaction_id = rt.id AND NOT dgm.is_preferred
);
```

---

## Daily Sync Container (Podman)

Container running on `192.168.128.9`. Syncs Monzo + Wise transactions daily at 3am.

### Components
- **`scripts/entrypoint.sh`** — Container entrypoint. Starts Monzo auth server (background) + FastAPI via uvicorn (foreground).
- **`src/ingestion/monzo_auth.py`** — Persistent HTTP server on `0.0.0.0:9876`. Serves status page, handles OAuth callback, provides JSON polling for app approval.
- **`src/api/app.py`** — FastAPI app on `0.0.0.0:8000`. Serves API under `/api/v1/` and the React SPA as static files (fallback to `index.html` for client-side routing).
- **`scripts/daily_sync.py`** — Orchestrator: Wise sync (30 days) → Monzo sync (headless refresh, 30 days) → cleaning → dedup → healthcheck pings.
- **`Containerfile`** — Multi-stage build: Node (builds React UI) → Python 3.12-slim (API + static files). Exposes 8000 + 9876.
- **`deploy/run.sh`** — Build + run with secrets from `/zfs/Apps/AppData/finance/`.
- **`deploy/finance-sync.timer`** — Systemd timer, 3am daily.

### Container Build Notes
- The Containerfile runs `tsc -b && vite build` which enforces strict TypeScript checking — stricter than the dev server's `vite dev` which skips type checking.
- If the container build fails with TS errors (unused variables, type mismatches), fix them in the source before rebuilding. Common issues:
  - `TS6133`: Unused variables/imports — remove them
  - `TS2345`: Type assignability — ensure function signatures match usage (e.g. optional vs required fields)
- After building, restart with `systemctl restart finance.service`

### Key Design Decisions
- Monzo auth uses `headless=True` for daily sync — only attempts token refresh. Raises `AuthRequiredError` if interactive flow needed.
- Re-auth available at `https://finance.mees.st/` from any LAN device.
- `MONZO_TOKEN_FILE` env var controls token file location (default: `tokens.json`).
- `MONZO_REDIRECT_URI` env var overrides redirect URI for container (set to `https://finance.mees.st/oauth/callback`).
- Per-source healthcheck URLs via `HEALTHCHECK_MONZO_URL` and `HEALTHCHECK_WISE_URL` env vars.
- Wise `_api_get()` and `_headers()` now have timeout=30, 401 handling, and empty token guard.
- Monzo `_api_get()` and `list_accounts()` now have timeout=30 and 401 → `AuthRequiredError`.

### Authentication
- **Authelia** handles authentication via Traefik forward-auth middleware.
- Traefik passes `Remote-Email`, `Remote-Name`, `Remote-Groups` headers to the API.
- `app_user` table controls per-user access: email, display_name, allowed_scopes (personal/business), role (admin/readonly).
- `auth_enabled` setting defaults to `True`; set to `false` in `config/.env` for local dev (uses `dev_user_email`).

### Traefik Routing
- `finance.mees.st` → Authelia middleware → `finance-api` service (port 8000) — serves UI + API
- `finance.mees.st/oauth/*` → `finance-oauth` service (port 9876) — Monzo OAuth callback (no auth)

### Deployment
```bash
# On 192.168.128.9
# Secrets in /zfs/Apps/AppData/finance/ (.env + tokens.json)
./deploy/run.sh
# Install timer
cp deploy/finance-sync.{service,timer} /etc/systemd/system/
systemctl enable --now finance-sync.timer
```

---

## API Endpoints (FastAPI on :8000)

All under `/api/v1/`:

| Method | Path | Notes |
|--------|------|-------|
| GET | /transactions | Cursor-paginated (keyset on posted_at DESC, id DESC), 12 filter params |
| GET | /transactions/{id} | Full detail + dedup group + economic event |
| GET | /accounts | Derived from active_transaction GROUP BY institution/account_ref/currency |
| GET | /accounts/{institution}/{account_ref} | Summary + recent txns |
| GET | /categories | Recursive tree |
| GET | /categories/spending | Aggregated with date range + currency filters |
| GET | /merchants | Cursor-paginated, search + unmapped filter |
| PUT | /merchants/{id}/mapping | Only write endpoint — sets category_hint |
| GET | /stats/monthly | Income/expense by month |
| GET | /stats/overview | Dashboard summary stats |
| GET | /health | Pool status |

---

## UI (React)

In production, the React SPA is built during the container image build (multi-stage Containerfile) and served by FastAPI as static files. No separate UI server needed.

For local development:

### Pages
- **Dashboard**: 4 stat cards, 12-month income/expense BarChart, top spending categories
- **Transactions**: Debounced search, institution/currency/date filters, paginated table, click-to-open detail slide-over
- **Accounts**: Grouped by institution, balance cards, links to AccountDetail
- **AccountDetail**: Stats + recent transactions for one account
- **Categories**: Left=recursive tree, Right=date range + spending chart + table
- **Merchants**: Search, "Unmapped only" checkbox, category assignment

---

## File Layout

```
finance/
├── config/
│   ├── .env                    # DB credentials (gitignored)
│   ├── settings.py             # Pydantic Settings (db, api, pool)
│   └── cleaning_rules.json     # Merchant cleaning rules
├── scripts/
│   ├── wise_bulk_load.py       # Wise API → raw_transaction
│   ├── wise_csv_load.py        # Wise CSV → raw_transaction
│   ├── fd_csv_load.py          # First Direct CSV → raw_transaction
│   ├── monzo_bulk_load.py      # Monzo API → raw_transaction
│   ├── load_ibank_transactions.py  # iBank/Bankivity migration
│   ├── load_ibank_categories.py    # iBank category import
│   ├── amazon_load.py          # Amazon order history
│   ├── run_cleaning.py         # Merchant cleaning pipeline
│   ├── run_dedup.py            # Deduplication pipeline
│   └── daily_sync.py           # Daily Wise+Monzo sync orchestrator
├── src/
│   ├── ingestion/
│   │   ├── writer.py           # Idempotent raw layer writer
│   │   ├── monzo.py            # Monzo OAuth + API client (headless mode)
│   │   ├── monzo_auth.py       # Persistent LAN auth server for container
│   │   ├── wise.py             # Wise API client
│   │   └── wise_fx.py          # Wise FX rate enrichment
│   ├── cleaning/
│   │   ├── processor.py        # Cleaning pipeline orchestrator
│   │   ├── rules.py            # Rule engine
│   │   └── matcher.py          # Fuzzy merchant matching
│   ├── dedup/
│   │   ├── config.py           # Source priorities + supersession + cross-source pairs
│   │   └── matcher.py          # Dedup matching + group creation
│   └── api/
│       ├── app.py              # FastAPI app + lifespan + CORS + SPA serving
│       ├── deps.py             # Connection pool + auth (Authelia headers)
│       ├── models.py           # Pydantic response models
│       └── routers/
│           ├── transactions.py
│           ├── accounts.py
│           ├── categories.py
│           ├── merchants.py
│           └── stats.py
├── ui/                         # React + Vite + TypeScript
│   ├── vite.config.ts          # Proxy + Tailwind plugin
│   └── src/
│       ├── api/                # Types, client, domain modules
│       ├── hooks/              # TanStack Query hooks
│       ├── components/         # common/ (5) + layout/ (2)
│       └── pages/              # Dashboard, Transactions, Accounts,
│                               # AccountDetail, Categories, Merchants
├── deploy/
│   ├── run.sh                  # Podman build + run
│   ├── finance-sync.service    # Systemd oneshot unit
│   └── finance-sync.timer      # 3am daily trigger
├── Containerfile               # Multi-stage build (Node + Python)
├── .wise                       # Wise API token (gitignored)
├── DECISIONS.md                # Architecture & design decisions
├── SCHEMA.md                   # Full database schema
├── README.md                   # Project overview
└── LICENSE                     # MIT, Stuart Bevan
```

---

## Credentials & Secrets

- **DB**: See `config/.env` (gitignored)
- **Wise API token**: `.wise` file (gitignored)
- **Monzo OAuth tokens**: `tokens.json` (gitignored)
- **Monzo client ID**: `oauth2client_0000B3VKN1klMnFavWSOCw` (in monzo.py)
- **GitHub**: `git@github.com:nonatech-uk/finance.git`

---

## What To Do Next

**Immediate**: General data quality:
1. Review Wise and other accounts for balance discrepancies
2. Consider adding merchant-name fuzzy matching as a secondary dedup signal
3. Improve cleaning rules for First Direct CSV merchant strings

**Future** (from DECISIONS.md):
- UI context selector (Personal/Business/All) — global scope provider, stored in localStorage, flows to all API calls. Foundation in place: `account.scope` column + API filtering. Needs: React context, top-bar toggle, scope param on hardcoded endpoints.
- Xero-replacement module for business account — own category tree, client dimension, VAT tracking. Queries `scope='business'`.
- Inter-account transfer matching (economic events)
- LLM-assisted categorisation
- Monzo webhook endpoint via Cloudflare Tunnel
- Recurring pattern detection
- Alerting & forecasting
