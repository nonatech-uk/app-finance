"""Stock price fetching and caching.

Uses Yahoo Finance v8 API directly (no yfinance dependency).
"""

import re
from datetime import date, datetime
from decimal import Decimal

import requests


YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def _fetch_price(symbol: str) -> tuple[Decimal, str]:
    """Fetch the current price for a single symbol from Yahoo Finance.

    Returns (price, yahoo_currency) where yahoo_currency may be 'GBp' for pence.
    """
    resp = requests.get(
        YAHOO_QUOTE_URL.format(symbol=symbol),
        params={"range": "1d", "interval": "1d"},
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    if price is None:
        raise ValueError(f"No price in response for {symbol}")
    yahoo_ccy = meta.get("currency", "")
    return Decimal(str(price)), yahoo_ccy


def _fetch_fx_rate(base_currency: str) -> Decimal | None:
    """Fetch the current FX rate for base_currency to GBP from Yahoo Finance.

    Uses the {BASE}GBP=X ticker (e.g. USDGBP=X gives 1 USD in GBP).
    """
    symbol = f"{base_currency}GBP=X"
    price, _ = _fetch_price(symbol)
    return price


def _fetch_hl_fund_price(url: str) -> tuple[Decimal, date]:
    """Scrape a fund price from an HL fund factsheet page.

    Returns (price_in_pounds, price_date).
    """
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    html = resp.text

    # Price in pence from <span class="bid price-divide">117.03p</span>
    m = re.search(r'bid price-divide[^>]*>(\d+\.?\d*)p', html)
    if not m:
        raise ValueError(f"No price found on HL page: {url}")
    price_pence = Decimal(m.group(1))
    price_pounds = price_pence / 100

    # Date from "Prices as at 6 March 2026"
    price_date = date.today()
    dm = re.search(r'Prices?\s*as\s*(?:at|of)\s*(\d{1,2}\s+\w+\s+\d{4})', html, re.I)
    if dm:
        try:
            price_date = datetime.strptime(dm.group(1).strip(), "%d %B %Y").date()
        except ValueError:
            pass

    return price_pounds, price_date


def fetch_current_prices(conn) -> dict:
    """Fetch current prices for all active holdings and upsert into stock_price.

    Also fetches FX rates for non-GBP currencies and upserts into fx_rate.
    Returns {"updated": int, "fx_updated": int, "errors": list[dict]}.
    """
    cur = conn.cursor()
    cur.execute("SELECT id, symbol, currency, price_url FROM stock_holding WHERE is_active")
    holdings = cur.fetchall()

    if not holdings:
        return {"updated": 0, "fx_updated": 0, "errors": []}

    today = date.today()
    updated = 0
    errors = []

    for holding_id, symbol, currency, price_url in holdings:
        try:
            if price_url:
                # Custom price source (e.g. HL fund pages)
                price, price_date = _fetch_hl_fund_price(price_url)
                source = "hl"
            else:
                price, yahoo_ccy = _fetch_price(symbol)
                price_date = today
                source = "yahoo"

                # Yahoo returns UK stocks in GBp (pence) — convert to GBP (pounds)
                if yahoo_ccy == "GBp" and currency == "GBP":
                    price = price / 100

            cur.execute("""
                INSERT INTO stock_price (holding_id, price_date, close_price, currency, source)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (holding_id, price_date)
                DO UPDATE SET close_price = EXCLUDED.close_price,
                              source = EXCLUDED.source,
                              fetched_at = now()
            """, (str(holding_id), price_date, price, currency, source))
            updated += 1
        except Exception as e:
            errors.append({"symbol": symbol, "error": str(e)})

    # Fetch FX rates for non-GBP currencies
    foreign_currencies = {c for _, _, c, _ in holdings if c != "GBP"}
    fx_updated = 0
    for ccy in foreign_currencies:
        try:
            rate = _fetch_fx_rate(ccy)
            cur.execute("""
                INSERT INTO fx_rate (base_currency, quote_currency, rate_date, rate, source)
                VALUES (%s, 'GBP', %s, %s, 'yahoo')
                ON CONFLICT (base_currency, quote_currency, rate_date)
                DO UPDATE SET rate = EXCLUDED.rate, fetched_at = now()
            """, (ccy, today, rate))
            fx_updated += 1
        except Exception as e:
            errors.append({"symbol": f"{ccy}GBP=X", "error": str(e)})

    conn.commit()
    return {"updated": updated, "fx_updated": fx_updated, "errors": errors}


def get_latest_prices(conn) -> dict:
    """Get the most recent cached price for each active holding.

    Returns {holding_id_str: {"close_price": Decimal, "price_date": date, ...}}.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (sp.holding_id)
            sp.holding_id, sh.symbol, sp.close_price, sp.currency,
            sp.price_date, sp.fetched_at
        FROM stock_price sp
        JOIN stock_holding sh ON sh.id = sp.holding_id
        WHERE sh.is_active
        ORDER BY sp.holding_id, sp.price_date DESC
    """)
    columns = [desc[0] for desc in cur.description]
    result = {}
    for row in cur.fetchall():
        d = dict(zip(columns, row))
        result[str(d["holding_id"])] = d
    return result


def get_latest_fx_rates(conn) -> dict:
    """Get the most recent FX rate to GBP for each currency.

    Returns {base_currency: {"rate": Decimal, "rate_date": date}}.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (base_currency)
            base_currency, rate, rate_date
        FROM fx_rate
        WHERE quote_currency = 'GBP'
        ORDER BY base_currency, rate_date DESC
    """)
    result = {}
    for base_ccy, rate, rate_date in cur.fetchall():
        result[base_ccy] = {"rate": rate, "rate_date": rate_date}
    return result
