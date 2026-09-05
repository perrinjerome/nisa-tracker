# NISA Account Tracker

Track a Japanese NISA account: import purchase history exported from the SBI
Securities website, fetch daily fund NAV from the Fund Library, and view on a
local web dashboard the current value, historical value/profit graph,
NISA limit usage, and a 10-year forecast. UI is available in Japanese and
English.

## Files

- `input.csv` — purchase history exported from SBI Securities (see below)
- `db.py` — SQLite storage, CSV import, valuation, NISA limit usage, forecast
- `fetch.py` — downloads daily NAV from toushin-lib.fwg.ne.jp into the DB
- `app.py` — Flask web app
- `nisa.db` — SQLite database (created on first run; delete to start over)

## Export purchases from SBI Securities

SBI証券 のマイページ → 取引・投信 → 「取引履歴」から絞り込んでCSVダウンロードします。
The exported file should have these columns, in this order:

```
約定日,銘柄,銘柄コード,市場,取引,期限,預り,課税,約定数量,約定単価,手数料/諸経費等,税額,受渡日
```

Save it as `input.csv` in this folder (replace the bundled sample).

Notes:

- Dates use **M/D/Y** (e.g. `8/4/2026` = 2026-08-04). The import assumes this
  format; it is NOT D/M/Y.
- `銘柄` is matched by normalizing spaces and case, so you can keep the
  full-width names as exported (`ｅＭＡＸＩＳ　Ｓｌｉｍ　米国株式（Ｓ＆Ｐ５００）`).
- `約定単価` is the NAV per 10,000 units (万口), so invested cost is computed as
  `約定数量 × 約定単価 ÷ 10,000`.
- Rows are deduplicated on (date, fund, quantity, unit price, account);
  re-importing the same file is safe.

Each fund needs an entry in `db.FUNDS` (ISIN + Fund Library code). The bundled
eMAXIS Slim funds are already mapped; add new ones there. The CSV import
explains which names could not be matched.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

This installs Flask + requests into the venv (requirements live in
`pyproject.toml`) and provides the `nisa-app` / `nisa-fetch` commands.

## Run

```bash
.venv/bin/python app.py
```

Opens at http://localhost:5000.

On startup the app imports `input.csv` (only when the DB is empty), refreshes
prices, then keeps refreshing in the background every 24h. Use the 「価格更新」
button to refresh manually.

## Using the dashboard

- **Cards** show total invested, current value, and profit (¥ and %, green/red).
- **Value graph** plots total value and total profit with **1Y / 5Y / All**
  buttons to set the time range (no reload, instant).
- **Tsumitate simulation** (stacked chart): cumulative invested over time plus
  a 10-year projection at the monthly amounts and assumed annual return from
  the forecast settings; projected profit is stacked on top of invested.
- **NISA limits** show yearly usage for つみたて/成長/combined vs. the new-NISA
  caps, including a forecast of when you would exceed them.
- **Forecast** (10 years) shows projected value, contributions, and profit
  from the settings below it.
- **Forecast settings** (monthly つみたて amount, monthly 成長 amount, assumed
  annual return) are editable on the page and stored in the DB.
- **Buy a fund** (`+` button or `POST /api/buy` with
  `{"date":"YYYY-MM-DD","amount":100000,"fund":"<ISIN>"}`) adds a purchase using
  the fund's NAV on or before that date; duplicates are rejected. Costs count
  against NISA limits by account type.

## CLI tools

- Import / refresh manually:
  ```bash
  .venv/bin/python db.py -i input.csv      # import purchases
  .venv/bin/python fetch.py                # fetch latest NAV for all funds
  ```
- Or use the installed commands: `nisa-app` / `nisa-fetch`.

## NISA limits (new NISA)

- Tsumitate yearly: ¥1,200,000
- Growth yearly: ¥2,400,000
- Combined yearly: ¥3,600,000
- Lifetime: ¥18,000,000 (growth: ¥12,000,000)

Constants live in `db.py`.