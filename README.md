# Fairfax Finance

A private, single-household USD ledger built with Streamlit and SQLite. Start with a dated balance for each asset and debt, then import cash flow and compare progress toward a savings goal.

## Run locally

Use Python 3.11 or newer. From a virtual environment in the repository directory:

```sh
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Copy `.streamlit/secrets.example.toml` to `.streamlit/secrets.toml` and set a long, unique household password. The example contains only empty credentials. For Community Cloud, paste the TOML into the app's **Settings → Secrets** and choose private viewer access. Keep credentials, statement exports, and database backups outside source control. The login gates every ledger page and sync action. Run hosted instances over HTTPS.

## Storage

The default database is `data/ledger.sqlite3`. Set `ledger.database_path` to a private path on persistent storage. SQLite is an unencrypted file. Protect it with OS permissions and encrypted storage. Back up the file with the app stopped. Community Cloud local files can disappear on restart or redeployment. Use local or persistent-volume hosting for the authoritative ledger.

## Bring in accounts

- **YNAB.** Set a personal access token and one explicit USD plan UUID in the secrets. Under Connections, click Sync YNAB. A full refresh replaces that plan's stored transaction history atomically and records today's account balances. Splits use their component categories. Transfers between linked accounts are skipped. English Ready to Assign inflows count as income, categorized refunds reduce spending, and uncategorized activity appears for review. The importer assumes the standard English starting balance and reconciliation payee names. Review unusual payees and category conventions against YNAB. Use one plan per database.
- **Schwab.** Supply an already-authorized OAuth bearer access token for Accounts and Trading under schwab.access_token. The app reads account liquidation values using the accounts endpoint. Replace the token in Secrets when it expires. App keys alone cannot authorize account reads. This draft implements balance snapshots only, with manual token renewal.
- **Retirement, loans, and physical assets.** Enter a dated value under Accounts & valuations, using the same name for later snapshots. Enter debt as negative. Saving the same account and date replaces that valuation. Historical statement balances can be entered this way.
- **Overlapping accounts.** On-budget YNAB accounts are included by default. Tracking accounts start excluded. Use the coverage selector to choose one representation of each real account before interpreting net worth. Coverage also applies to historical reports.

## Read reports

Overview shows the latest included snapshot on or before the report date for each account, with its actual valuation date. API syncs start balance history on the day they run. Historical cash flow can therefore predate balance coverage. The monthly surplus uses recorded income less net categorized spending. Goal projection uses selected current balances, a monthly contribution, and a constant annual return. Resale inventory, investment lots, performance attribution, and statement reconciliation can be added as later modules.

References: [YNAB API](https://api.ynab.com/), [Schwab developer portal](https://developer.schwab.com/products/trader-api--individual), [Streamlit Secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).
