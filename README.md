# NISA Account Tracker

Track a Japanese NISA account: import purchase history exported from the SBI
Securities website, fetch daily fund NAV from the Fund Library, and view on a
local web dashboard the current value, historical value/profit graph,
NISA limit usage, and a 10-year forecast. UI is available in Japanese and
English.

## Ask questions about your investments — an AI agent built in

Beyond the charts, an AI assistant lives on the dashboard. Ask it in plain
language, in Japanese or English, and it answers from *your actual data*:

- "How far am I from the NISA lifetime limit?"
- "What is my return on the eMAXIS Slim funds?"
- "Show me how my tsumitate purchases have compounded over the years."
- "If I keep saving ¥50,000/month, where will I be in 10 years?"

Every answer is computed server-side by running read-only Python in a sandbox
pre-loaded with your portfolio snapshot, so the numbers it quotes match your
real purchase and NAV history. It never invents holdings, prices, or
predictions, and full conversation history is stored locally and resumes after
a restart.

> Bring your own LLM: point it at any OpenAI-compatible API via environment
> variables (`OPENAI_API_URL`, `OPENAI_API_TOKEN`, `OPENAI_API_MODEL`) — no
> data ever leaves your machine unless you decide to use a remote provider.

## Setting up

### Export purchases from SBI Securities

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

### Configuration (.env)

Copy [`.env.example`](.env.example) to `.env` and fill it in:

```bash
OPENAI_API_URL=http://localhost:11434/v1   # endpoint base URL
OPENAI_API_TOKEN=your-token                # bearer token
OPENAI_API_MODEL=gpt-4o-mini               # optional, default model name
PYTHON_EVAL_TIMEOUT=15                     # optional, sandbox time limit (seconds)
```

When the assistant tab is open, models are listed from the endpoint
(`GET /v1/models`) and shown in a dropdown in the chat box — pick the model per
conversation and your choice is remembered (localStorage). The default while
the list is loading (or if the endpoint does not expose `/models`) is
`OPENAI_API_MODEL`.


`.env` is gitignored and never committed — secrets stay local. The optional
settings can also be mixed with the environment:
`.env` for convenience, OS env for secrets.

### Setup and run

```bash
uv run nisa-app
```

Opens at http://localhost:5000

On startup the app imports `input.csv` (only when the DB is empty), refreshes
prices, then keeps refreshing in the background every 24h. Use the 「価格更新」
button to refresh manually.

## Using the dashboard

- **Assistant** ask general questions to the AI assistant.
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
  .venv/bin/python -m nisa_tracker.db -i input.csv   # import purchases
  .venv/bin/nisa-fetch                               # fetch latest NAV for all funds
  ```
- Or use the installed command: `nisa-app` / `nisa-fetch`.


## AI chat assistant

The dashboard includes an assistant that answers questions about your holdings
using live data from the tracker. It talks to any OpenAI-compatible
`/chat/completions` endpoint through function (tool) calls.


### Assistant data access (`python_eval`)

With the variables set, the "Assistant" section appears on the dashboard. The
agent uses a single `python_eval` tool to read the tracker data and compute every
answer server-side: portfolio valuation, purchase history, NAV price series, NISA
quota usage, statistics and custom forecasts. If the variables are missing, the
section shows a note instead; the chat feature stays disabled.

The `python_eval` code is executed with **RestrictedPython** in a **separate
subprocess** so a runaway or hostile script cannot hang the server or touch the
machine.

The sandbox provides:

- `portfolio` — valuation (cost, value, profit, return) and NISA usage
- `buy_history` — chronological purchase records
- `sales_history` — sale records (empty in the current tracker)
- `price_history` (alias `prices`) — NAV series per fund
- `funds` — ISIN → {name, code}
- `today` — ISO date
- `math` and `statistics` modules

The agent writes code whose final value is assigned to a `result` variable;
`print()` output is captured too. The sandbox rejects imports, `while` loops,
`__dunder__` attribute access, and undefined or dangerous names (e.g. `os`),
and enforces a subprocess timeout (`PYTHON_EVAL_TIMEOUT`, default 15 s). The
implementation lives in `python_env_worker.py`; `ai.py` feeds it a read-only
snapshot of the tracker data.

Every script the agent submits to the sandbox is logged through the standard
`logging` module (logger `nisa_tracker.ai`) and appears in the console next to
the Flask/werkzeug access logs, so you can audit what the assistant computed.

The system prompt tells the agent to answer in the user's language, quote JPY,
and never invent holdings, prices or projections. The agent is instructed to use
`python_eval` for every data need and to quote exactly what the sandbox returns.

### Conversation history

Chat conversations are stored in SQLite (`conversations` /
`conversation_messages` tables) and survive restarts. The sidebar lists past
conversations (most recently active first); click one to resume the discussion,
use 「新しい会話 / New chat」 to start fresh, and × to delete. The open
conversation is remembered in localStorage and re-opened on reload. Titles are
auto-generated from the first user message. `POST /api/chat` accepts an
optional `conversation_id`; when omitted a new conversation is created. Only
new messages should be sent per request — the server loads the stored history
and feeds the last 40 messages to the model.