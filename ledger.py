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


def read_api(url, token):
    response = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                            timeout=35, allow_redirects=False)
    if response.status_code != 200:
        raise ValueError(f"Provider returned HTTP {response.status_code}. Check access and retry.")
    return response.json()


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
    accounts = read_api("https://api.schwabapi.com/trader/v1/accounts", settings["access_token"])
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
