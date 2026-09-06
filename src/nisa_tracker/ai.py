import json
import logging
import os
import subprocess
import sys

import requests

from nisa_tracker import db

logger = logging.getLogger(__name__)

API_URL = os.environ.get("OPENAI_API_URL", "").rstrip("/")
API_TOKEN = os.environ.get("OPENAI_API_TOKEN", "")
API_MODEL = os.environ.get("OPENAI_API_MODEL", "gpt-4o-mini")

PAYLOAD_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
PYTHON_EVAL_WORKER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "python_env_worker.py"
)
PYTHON_EVAL_TIMEOUT = float(os.environ.get("PYTHON_EVAL_TIMEOUT", "15"))
MAX_PYTHON_CODE_CHARS = 8000
MAX_PYTHON_RESULT_CHARS = 8000


def _env_file_candidates():
    return [
        os.path.join(os.getcwd(), ".env"),
        PAYLOAD_ENV_PATH,
    ]


def load_env_file(path=None):
    """Load KEY=VALUE pairs from a .env file, without overriding existing variables."""
    if path is None:
        paths = [p for p in _env_file_candidates() if os.path.isfile(p)]
        if not paths:
            return
        path = paths[0]
    if not path or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


def reload_env():
    global API_URL, API_TOKEN, API_MODEL
    API_URL = os.environ.get("OPENAI_API_URL", "").rstrip("/")
    API_TOKEN = os.environ.get("OPENAI_API_TOKEN", "")
    API_MODEL = os.environ.get("OPENAI_API_MODEL", "gpt-4o-mini")


load_env_file()
reload_env()

SYSTEM_PROMPT = """You are the portfolio assistant for a personal Japanese NISA account tracker.

The user tracks purchases and fund NAV (基準価額) history for their own individual savings
account and wants help understanding their investments.

Rules:
- Money is always in Japanese Yen (JPY, ¥). Never use another currency.
- Dates are ISO 8601 (YYYY-MM-DD); months are YYYY-MM. Be precise, do not guess future NAV values.
- A NAV (基準価額) is the net asset value per 10,000 units. Holdings quantity (口数) is in units,
  so value = quantity / 10000 * nav. Cost minus value gives profit.
- Purchases belong to one of these account types:
  * "NISA (つみたて)" — the tsumitate (regular investment) quota of the NISA wrapper
  * "NISA (成長)" — the growth quota of the NISA wrapper
  * "特定/一般" — a taxable (non-NISA) account
- Current Japanese NISA limits: yearly 3,600,000 JPY combined (1,200,000 for tsumitate,
  2,400,000 for growth), lifetime 18,000,000 JPY total (12,000,000 for growth alone).
- Answer in the same language the user writes in.
- Be concise and concrete. Never invent holdings, prices, or projections. When quoting
  numbers, round to whole yen by default.
- Use ONLY the "python_eval" tool to access the tracker data: it runs read-only Python in a
  restricted sandbox and provides the data as variables. Compute every answer (portfolio
  value, cost, profit, purchase history, NAV history, NISA quota usage, statistics, custom
  forecasts) inside python_eval and quote exactly what it returns. If the user asks
  something the data cannot answer, say so plainly.

Variables available inside python_eval code:
- portfolio — {total_cost, total_value, total_profit, total_profit_pct, last_price_date,
  nisa_usage: {yearly: {YYYY: {tsumitate, growth}}, lifetime_tsumitate, lifetime_growth,
  lifetime_total}}
- buy_history — chronological purchase records {date, isin, name, account, quantity,
  unit_price, invested} (dates ISO)
- sales_history — list of sales (empty in the current tracker)
- price_history (alias prices) — NAV history points {date, isin, name, nav}
- funds — ISIN -> {name, code}
- today — ISO date string
- math and statistics modules

Usage: write code whose final value is stored in a variable named `result`; print() output
is returned too. Imports, `while` loops and __dunder__ attribute access are rejected.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "python_eval",
            "description": (
                "Run read-only Python in a restricted sandbox to access the tracker data "
                "variables (portfolio, buy_history, sales_history, price_history/prices, "
                "funds, today) and compute answers, statistics or custom forecasts. Assign "
                "the final value to `result`; print() output is also returned. Imports, "
                "`while` loops and __dunder__ attribute access are rejected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python source code (max 8000 characters). The data variables "
                            "hold read-only snapshots, so compute only over them."
                        ),
                    }
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
]


def python_env_data():
    """Read-only snapshot of the tracker data made available in the Python sandbox."""
    val = db.valuation()
    usage = db.nisa_usage()
    price_history = [
        {
            "date": r["date"],
            "isin": r["isin"],
            "name": db.FUNDS.get(r["isin"], {}).get("name", r["isin"]),
            "nav": r["nav"],
        }
        for r in db.get_prices()
    ]
    buy_history = [
        {
            "date": p["date"],
            "isin": p["isin"],
            "name": p["name"],
            "account": p["account"],
            "quantity": p["quantity"],
            "unit_price": p["unit_price"],
            "invested": p["invested"],
        }
        for p in sorted(db.get_purchases(), key=lambda p: p["date"])
    ]
    return {
        "today": db.today().isoformat(),
        "portfolio": {
            "total_cost": round(val["total_cost"], 2),
            "total_value": round(val["total_value"], 2),
            "total_profit": round(val["total_profit"], 2),
            "total_profit_pct": round(val["total_profit_pct"], 2),
            "last_price_date": val["last_price_date"],
            "nisa_usage": usage,
        },
        "buy_history": buy_history,
        "sales_history": [],
        "price_history": price_history,
        "prices": price_history,
        "funds": {isin: {"name": fund["name"], "code": fund["code"]} for isin, fund in db.FUNDS.items()},
    }


def log_python_eval(code):
    logger.info("restricted python requested:\n%s", code)


def python_eval(code=None):
    if not code or not isinstance(code, str):
        return {"error": "python_eval requires a non-empty `code` string"}
    if len(code) > MAX_PYTHON_CODE_CHARS:
        return {"error": f"python code is too long (max {MAX_PYTHON_CODE_CHARS} characters)"}
    log_python_eval(code)
    request = json.dumps({"code": code, "data": python_env_data()}).encode("utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, PYTHON_EVAL_WORKER],
            input=request,
            capture_output=True,
            timeout=PYTHON_EVAL_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": "python evaluation timed out; keep loops and data sizes small"}
    try:
        out = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        stderr = proc.stderr.decode("utf-8", "replace")[-500:]
        return {"error": f"python worker crashed: {stderr}".strip()}
    if not out.get("ok"):
        return {"error": out.get("error") or "python evaluation failed"}
    result = {}
    if out.get("result") is not None:
        result["result"] = out["result"][:MAX_PYTHON_RESULT_CHARS]
    if out.get("stdout"):
        result["stdout"] = out["stdout"][-MAX_PYTHON_RESULT_CHARS:]
    return result or {"result": None}


EXECUTE = {
    "python_eval": python_eval,
}


def available():
    return bool(API_URL and API_TOKEN)


def list_models(timeout=10):
    """List model ids from the OpenAI-compatible endpoint (GET /models).

    Returns the list of model ids; on any failure an empty list is returned so
    the UI falls back to the default model.
    """
    if not available():
        return []
    try:
        resp = requests.get(
            f"{API_URL}/models",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return []
    models = []
    for item in data.get("data", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            models.append(item["id"])
    return models


def run_chat(messages, model=None, timeout=120):
    """Run a tool-calling chat round-trip against the OpenAI-compatible endpoint.

    `messages` is a list of {role, content} dicts (user/assistant, plain text).
    `model` overrides the default model; falls back to the configured default.
    Returns the final assistant text reply.
    """
    if not available():
        raise RuntimeError("OPENAI_API_URL / OPENAI_API_TOKEN are not configured")
    if not isinstance(model, str) or not model.strip():
        model = API_MODEL
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)
    payload = {
        "model": model,
        "messages": msgs,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{API_URL}/chat/completions"
    for _ in range(12):
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        msgs.append(
            {
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": msg.get("tool_calls"),
            }
        )
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg.get("content") or ""
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                result = EXECUTE[name](**args)
            except TypeError as exc:
                result = {"error": f"bad arguments for {name}: {exc}"}
            except (OSError, ValueError, KeyError, IndexError) as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
    raise RuntimeError("model did not finish after too many tool calls")