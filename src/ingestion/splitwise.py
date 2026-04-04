"""Splitwise API client: fetch expenses, create expenses, category mapping."""

import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from config.settings import settings


def _headers() -> dict:
    token = settings.splitwise_api_key
    if not token:
        raise RuntimeError("Splitwise API key not configured (SPLITWISE_API_KEY / .env)")
    return {"Authorization": f"Bearer {token}"}


def _api_get(url: str, params: dict | None = None, max_retries: int = 5) -> requests.Response:
    """GET with exponential backoff on 429."""
    for attempt in range(max_retries):
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code == 429:
            wait = 2 ** attempt
            print(f"  Splitwise rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code == 401:
            raise RuntimeError(
                "Splitwise API key invalid or expired (401). "
                "Check SPLITWISE_API_KEY in .env"
            )
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Splitwise API rate limited after {max_retries} retries: {url}")


def _api_post(url: str, data: dict, max_retries: int = 5) -> requests.Response:
    """POST with exponential backoff on 429."""
    for attempt in range(max_retries):
        resp = requests.post(url, headers=_headers(), data=data, timeout=30)
        if resp.status_code == 429:
            wait = 2 ** attempt
            print(f"  Splitwise rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code == 401:
            raise RuntimeError(
                "Splitwise API key invalid or expired (401). "
                "Check SPLITWISE_API_KEY in .env"
            )
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Splitwise API rate limited after {max_retries} retries: {url}")


def get_current_user() -> dict:
    """GET /get_current_user — returns user info including id."""
    resp = _api_get(f"{settings.splitwise_api_base}/get_current_user")
    return resp.json().get("user", {})


def get_groups() -> list[dict]:
    """GET /get_groups — returns all groups the user belongs to."""
    resp = _api_get(f"{settings.splitwise_api_base}/get_groups")
    return resp.json().get("groups", [])


def get_group(group_id: int) -> dict:
    """GET /get_group/{id} — returns group info including members."""
    resp = _api_get(f"{settings.splitwise_api_base}/get_group/{group_id}")
    return resp.json().get("group", {})


def fetch_expenses(
    group_id: int | None = None,
    dated_after: datetime | None = None,
    dated_before: datetime | None = None,
    limit: int = 100,
) -> list[dict]:
    """Fetch expenses with limit/offset pagination.

    Returns all expenses across pages, excluding deleted ones.
    """
    all_expenses = []
    offset = 0

    while True:
        params: dict = {"limit": limit, "offset": offset}
        if group_id is not None:
            params["group_id"] = group_id
        if dated_after:
            params["dated_after"] = dated_after.isoformat()
        if dated_before:
            params["dated_before"] = dated_before.isoformat()

        resp = _api_get(f"{settings.splitwise_api_base}/get_expenses", params=params)
        expenses = resp.json().get("expenses", [])

        if not expenses:
            break

        # Filter out deleted expenses
        active = [e for e in expenses if not e.get("deleted_by")]
        all_expenses.extend(active)

        if len(expenses) < limit:
            break
        offset += limit

    return all_expenses


def create_expense(
    cost: str,
    description: str,
    date: str,
    currency_code: str,
    category_id: int,
    group_id: int,
    payer_user_id: int,
    splits: list[dict],
    details: str | None = None,
) -> dict:
    """POST /create_expense.

    splits: list of {"user_id": int, "owed_share": str}
    The payer pays the full cost; each member owes their owed_share.
    """
    data = {
        "cost": cost,
        "description": description,
        "date": date,
        "currency_code": currency_code,
        "category_id": category_id,
        "group_id": group_id,
        "split_equally": "false",
    }
    if details:
        data["details"] = details

    # Add user shares — payer pays full amount, each user owes their share
    for i, split in enumerate(splits):
        data[f"users__{i}__user_id"] = split["user_id"]
        if split["user_id"] == payer_user_id:
            data[f"users__{i}__paid_share"] = cost
        else:
            data[f"users__{i}__paid_share"] = "0"
        data[f"users__{i}__owed_share"] = split["owed_share"]

    resp = _api_post(f"{settings.splitwise_api_base}/create_expense", data=data)
    result = resp.json()

    # Check for errors in the response
    errors = result.get("errors", {})
    if errors:
        raise RuntimeError(f"Splitwise create_expense failed: {errors}")

    expenses = result.get("expenses", [])
    if not expenses:
        raise RuntimeError(f"Splitwise create_expense returned no expenses: {result}")

    return expenses[0]


def get_expense(expense_id: int) -> dict:
    """GET /get_expense/{id} — returns full expense detail including comments."""
    resp = _api_get(f"{settings.splitwise_api_base}/get_expense/{expense_id}")
    return resp.json().get("expense", {})


_CONVERSION_RE = re.compile(
    r"converted this transaction from (\w{3}) \(([\d,.]+)\)"
)


def get_original_currency(expense: dict) -> tuple[str, str] | None:
    """Extract original currency/amount from conversion comments.

    Returns (currency_code, amount_str) or None if no conversion found.
    """
    for comment in expense.get("comments", []):
        content = comment.get("content", "")
        m = _CONVERSION_RE.search(content)
        if m:
            return m.group(1), m.group(2).replace(",", "")
    return None


def get_user_share(expense: dict, user_id: int) -> float | None:
    """Extract the user's net share from an expense they paid.

    Only returns a value for expenses the user paid (paid_share > 0).
    Returns the net_balance (positive = owed back) or None if user didn't pay.
    """
    for user in expense.get("users", []):
        if user.get("user", {}).get("id") == user_id or user.get("user_id") == user_id:
            paid = float(user.get("paid_share", "0"))
            if paid <= 0:
                return None
            net = float(user.get("net_balance", "0"))
            return net
    return None


# -- Category mapping --

# Finance category path -> Splitwise category ID
# Splitwise categories: https://dev.splitwise.com/ (GET /get_categories)
# Common IDs: 1=Utilities, 2=Electricity, 3=Heat/gas, 4=Trash, 5=Water,
# 6=Internet, 9=Household supplies, 12=Groceries, 13=Dining out,
# 14=Liquor, 15=Entertainment, 17=Rent, 18=General, 19=Other,
# 30=Transportation, 31=Taxi, 32=Gas/fuel, 33=Parking
SPLITWISE_CATEGORY_MAP = {
    # Eating Out
    "Eating Out": 13,
    "Eating Out:Meals": 13,
    "Eating Out:Drinks": 14,
    "Eating Out:Snacks": 13,
    # Household
    "Household:Groceries": 12,
    "Household:Groceries:Wine": 14,
    "Household:Utilities": 1,
    "Household:Consumables": 9,
    "Household:Electricals": 9,
    "Household:Home Improvement": 9,
    "Household:Maintenance": 9,
    "Household:Repairs": 9,
    "Household:Insurance": 18,
    "Household:Garden": 9,
    "Household:Cellular": 6,
    "Household": 9,
    # Cars / Transport
    "Cars:Petrol": 32,
    "Cars:Parking": 33,
    "Cars:Maintenance": 18,
    "Cars:Insurance": 18,
    "Cars": 30,
    # Travel
    "Travel:Eating Out": 13,
    "Travel:Groceries": 12,
    "Travel:Transport": 30,
    "Travel:Transport:Cabs": 31,
    "Travel:Transport:Flights": 30,
    "Travel:Transport:Trains": 30,
    "Travel:Transport:Rental": 30,
    "Travel:Accomodation": 17,
    "Travel:Parking": 33,
    "Travel": 18,
    # Fun
    "Fun": 15,
    "Fun:Visits / Shows": 15,
    "Fun:Books": 15,
    "Fun:Digital Media": 15,
    # Personal
    "Personal": 18,
    "Personal:Gifts": 18,
}


def map_finance_category(category_path: str | None) -> int:
    """Map a finance category path to a Splitwise category ID.

    Tries exact match, then walks up the hierarchy.
    Falls back to 18 (General).
    """
    if not category_path:
        return 18

    if category_path in SPLITWISE_CATEGORY_MAP:
        return SPLITWISE_CATEGORY_MAP[category_path]

    # Walk up: "Travel:Transport:Cabs" -> "Travel:Transport" -> "Travel"
    parts = category_path.split(":")
    for i in range(len(parts) - 1, 0, -1):
        parent = ":".join(parts[:i])
        if parent in SPLITWISE_CATEGORY_MAP:
            return SPLITWISE_CATEGORY_MAP[parent]

    return 18  # General
