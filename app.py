import hmac
from contextlib import closing
from datetime import date

import requests
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from ledger import as_of, milli, open_ledger, snapshot, sync_schwab, sync_ynab

st.set_page_config(page_title="Fairfax Finance")
st.title("Fairfax Finance")
try:
    config = st.secrets.to_dict()
except StreamlitSecretNotFoundError:
    config = {}
settings = config.get("ledger", {})
password = settings.get("password", "")
if not password:
    st.info("Set ledger.password in Streamlit Secrets to open the household ledger.")
    st.stop()
entered = st.sidebar.text_input("Household password", type="password")
if not hmac.compare_digest(entered.encode(), password.encode()):
    st.info("Enter your household password to view financial data.")
    st.stop()


def dollars(amount):
    return f"${amount / 1000:,.2f}"


today = date.today()
with closing(open_ledger(settings.get("database_path", "data/ledger.sqlite3"))) as db:
    dashboard, accounts, connections, goals = st.tabs(
        ["Overview", "Accounts & valuations", "Connections", "Goal projection"]
    )
    with connections:
        for provider, sync, required in [
            ("YNAB", sync_ynab, ["plan_id", "access_token"]),
            ("Schwab", sync_schwab, ["access_token"]),
        ]:
            section = config.get(provider.lower(), {})
            configured = all(section.get(field) for field in required)
            if st.button(f"Sync {provider}", disabled=not configured):
                try:
                    sync(db, section, today.isoformat())
                except ValueError as exc:
                    if str(exc).startswith("Provider returned HTTP"):
                        st.error(str(exc))
                    else:
                        st.error("Unexpected provider data. Sync rolled back.")
                except (requests.RequestException, KeyError, TypeError, ArithmeticError):
                    st.error("Sync failed. Check the connection and provider credentials.")
                else:
                    st.success(f"{provider} sync complete.")
            if not configured:
                st.caption(f"Configure [{provider.lower()}] in Streamlit Secrets to enable sync.")
    with accounts:
        with st.form("valuation"):
            name = st.text_input("Manual account or asset name")
            day = st.date_input("Valuation date", max_value=today)
            amount = st.number_input("Value in USD (enter debts as negative)", step=0.01)
            if st.form_submit_button("Save valuation"):
                if name.strip():
                    with db:
                        snapshot(db, "manual/" + name.strip().casefold(), name.strip(), "Manual",
                                 day.isoformat(), milli(amount))
                    st.success("Valuation saved.")
                else:
                    st.error("Enter an account or asset name.")
        current = as_of(db, today.isoformat())
        if current:
            labels = {r["id"]: f"{r['name']} ({r['source']})" for r in current}
            with st.form("coverage"):
                chosen = st.multiselect("Accounts included in household net worth", list(labels),
                                        default=[r["id"] for r in current if r["included"]],
                                        format_func=labels.get)
                if st.form_submit_button("Save coverage"):
                    with db:
                        db.executemany("UPDATE accounts SET included=? WHERE id=?",
                                       [(key in chosen, key) for key in labels])
                    st.success("Coverage saved.")
    with dashboard:
        cutoff = st.date_input("Report through", max_value=today)
        balances = [r for r in as_of(db, cutoff.isoformat()) if r["included"]]
        if balances:
            st.metric("Tracked net worth", dollars(sum(r["amount"] for r in balances)))
            st.dataframe([{"Account": r["name"], "Source": r["source"], "Last valued": r["day"],
                           "Balance": dollars(r["amount"])} for r in balances], hide_index=True)
        else:
            st.info("Add a valuation or sync accounts to see tracked net worth.")
        st.subheader("Month-to-date cash flow")
        rows = db.execute("""SELECT kind, category, SUM(amount) AS total FROM activity
                             WHERE day BETWEEN ? AND ? GROUP BY kind, category ORDER BY kind, category""",
                          (cutoff.replace(day=1).isoformat(), cutoff.isoformat())).fetchall()
        if rows:
            income = sum(r["total"] for r in rows if r["kind"] == "income")
            spending = -sum(r["total"] for r in rows if r["kind"] == "spending")
            for col, label, value in zip(st.columns(3), ["Income", "Net spending", "Surplus"],
                                         [income, spending, income - spending]):
                col.metric(label, dollars(value))
            st.dataframe([{"Type": r["kind"], "Category": r["category"],
                           "Signed flow": dollars(r["total"])} for r in rows], hide_index=True)
            if any(r["kind"] == "review" for r in rows):
                st.warning("Uncategorized activity is excluded from totals. Categorize in YNAB and sync again.")
        else:
            st.caption("No categorized or uncategorized YNAB activity for this period.")

    with goals:
        st.subheader("Project a savings milestone")
        st.caption("Choose the balances available for this goal. Contributions arrive at month end.")
        available = {r["id"]: r for r in current if r["included"]}
        selected = st.multiselect("Starting accounts", list(available),
                                  format_func=lambda key: available[key]["name"])
        starting = sum(available[key]["amount"] for key in selected) / 1000
        st.write(f"Starting balance: {dollars(milli(starting))}")
        contribution = st.number_input("Monthly contribution ($)", min_value=0.0, step=50.0)
        annual = st.number_input("Assumed annual return (%)", min_value=-99.0, max_value=100.0)
        target = st.number_input("Target balance ($)", min_value=1.0, value=10000.0)
        rate = (1 + annual / 100) ** (1 / 12) - 1
        balance = starting
        for month in range(601):
            if balance >= target:
                st.metric("Months to goal", month)
                break
            balance = balance * (1 + rate) + contribution
        else:
            st.write("Goal falls outside the 50-year projection.")
        st.caption("Illustrative nominal dollars with a constant return and contribution, before taxes and fees.")
