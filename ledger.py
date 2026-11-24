"""Private USD household ledger. Monetary values are integer thousandths of a dollar."""
import sqlite3
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import requests


def open_ledger(path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
            included INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS balances (
            account TEXT NOT NULL, day TEXT NOT NULL, amount INTEGER NOT NULL,
            PRIMARY KEY(account, day));
        CREATE TABLE IF NOT EXISTS activity (
            plan TEXT NOT NULL, id TEXT NOT NULL, day TEXT NOT NULL,
            category TEXT NOT NULL, amount INTEGER NOT NULL, kind TEXT NOT NULL,
            PRIMARY KEY(plan, id));
    """)
    return db


def snapshot(db, key, name, source, day, amount, included=True):
    db.execute("""INSERT INTO accounts VALUES (?,?,?,?) ON CONFLICT(id)
                  DO UPDATE SET name=excluded.name""", (key, name, source, included))
    db.execute("INSERT OR REPLACE INTO balances VALUES (?,?,?)", (key, day, amount))


class ProviderError(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def read_api(url, token):
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    response = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                            timeout=35, allow_redirects=False)
    if response.status_code != 200:
        status = response.status_code
        if url.startswith("https://api.ynab.com/") and status == 404:
            message = "YNAB 404: Use Find YNAB plans and copy the household plan ID into ynab.plan_id."
        elif url.startswith("https://api.schwabapi.com/") and status == 401:
            message = ("Schwab 401: Supply a current OAuth access token from Accounts and Trading, "
                       "or configure app_key, app_secret and refresh_token to renew access.")
        else:
            message = f"Provider returned HTTP {status}. Check the connection settings."
        raise ProviderError(status, message)
    return response.json()


def list_ynab_plans(settings):
    return read_api("https://api.ynab.com/v1/plans", settings["access_token"])["data"]["plans"]


def sync_ynab(db, settings, day):
    plan = settings["plan_id"].strip()
    base = "https://api.ynab.com/v1/plans/" + quote(plan, safe="")
    token = settings["access_token"]
    accounts = read_api(base + "/accounts", token)["data"]["accounts"]
    transactions = read_api(base + "/transactions", token)["data"]["transactions"]
    budget_ids = {a["id"] for a in accounts if a["on_budget"] and not a.get("deleted")}
    rows = []
    for tx in transactions:
        if tx.get("deleted") or tx["account_id"] not in budget_ids or tx.get("transfer_account_id"):
            continue
        if tx.get("payee_name") in {"Starting Balance", "Reconciliation Balance Adjustment"}:
            continue
        for part in tx.get("subtransactions") or [tx]:
            if part.get("deleted") or part.get("transfer_account_id"):
                continue
            category = part.get("category_name") or "Uncategorized"
            amount = part["amount"]
            income = category in {"Inflow: Ready to Assign", "Ready to Assign", "Inflow: To be Budgeted"}
            kind = "income" if income else "spending"
            if category == "Uncategorized":
                kind = "review"
            rows.append((plan, part["id"], tx["date"], category, amount, kind))
    with db:
        db.execute("DELETE FROM activity WHERE plan=?", (plan,))
        db.executemany("INSERT INTO activity VALUES (?,?,?,?,?,?)", rows)
        for account in accounts:
            if not account.get("deleted"):
                snapshot(db, f"ynab/{plan}/{account['id']}", account["name"], "YNAB",
                         day, account["balance"], account["on_budget"])


def sync_schwab(db, settings, day):
    endpoint = "https://api.schwabapi.com/trader/v1/accounts"
    token = settings.get("access_token", "").strip()
    can_refresh = all(settings.get(key) for key in ("app_key", "app_secret", "refresh_token"))
    if token:
        try:
            accounts = read_api(endpoint, token)
        except ProviderError as exc:
            if exc.status != 401 or not can_refresh:
                raise
            token = ""
    if not token:
        if not can_refresh:
            raise ProviderError(401, "Schwab requires an OAuth access token or refresh credentials.")
        response = requests.post(
            "https://api.schwabapi.com/v1/oauth/token",
            auth=(settings["app_key"].strip(), settings["app_secret"].strip()),
            data={"grant_type": "refresh_token", "refresh_token": settings["refresh_token"].strip()},
            timeout=35, allow_redirects=False,
        )
        if response.status_code != 200:
            raise ProviderError(response.status_code, f"Schwab token renewal returned HTTP {response.status_code}. "
                                "Check the app key and secret, then renew authorization if the refresh token expired.")
        accounts = read_api(endpoint, response.json()["access_token"])
    with db:
        for entry in accounts:
            account = entry["securitiesAccount"]
            number = account["accountNumber"]
            snapshot(db, "schwab/" + number, "Schwab •" + number[-4:], "Schwab", day,
                     milli(account["currentBalances"]["liquidationValue"]))


def milli(value):
    return int((Decimal(str(value)) * 1000).to_integral_value())


def as_of(db, day):
    return db.execute("""
        SELECT a.id, a.name, a.source, a.included, b.day, b.amount FROM accounts a
        JOIN balances b ON b.account=a.id AND b.day=(
            SELECT MAX(day) FROM balances WHERE account=a.id AND day<=?)
        ORDER BY a.name, a.source
    """, (day,)).fetchall()
