#!/usr/bin/env python3

import json
import logging
import os
import re
import sys
import threading
import time

import requests
from flask import (
    Flask,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
)

from nisa_tracker import ai, db, fetch


def _template_folder():
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    if os.path.isdir(local):
        return local
    return os.path.join(sys.prefix, "templates")


app = Flask(__name__, template_folder=_template_folder())
app.config["TEMPLATES_AUTO_RELOAD"] = True

STRINGS = {
    "ja": {
        "title": "NISA 口座トラッカー",
        "lang_ja": "日本語",
        "lang_en": "English",
        "refresh": "価格更新",
        "last_update": "最終更新",
        "no_data": "データがありません。価格を更新してください。",
        "current_value": "現在評価額",
        "total_cost": "投資元本",
        "total_profit": "評価損益",
        "portfolio": "ポートフォリオ",
        "fund": "ファンド",
        "cost": "元本",
        "value": "評価額",
        "profit": "損益",
        "profit_pct": "損益率",
        "per_fund": "ファンド別",
        "price_history": "推移",
        "yearly_limit": "年間投資上限",
        "lifetime_limit": "生涯投資上限",
        "used": "使用",
        "remaining": "残り",
        "forecast": "10年予測",
        "forecast_value": "10年後の予測金額",
        "forecast_contributed": "予測追加投資",
        "forecast_profit": "予測利益",
        "tsumitate_projection": "積立投資シミュレーション（今後10年）",
        "invested": "投資額",
        "projected_profit": "予測利益",
        "monthly": "毎月積立額",
        "monthly_growth": "毎月成長投資額",
        "annual_return": "想定年間利回り(%)",
        "save": "保存",
        "tsumitate": "つみたて",
        "growth": "成長",
        "purchases": "購入履歴",
        "date": "約定日",
        "pending_warning": "1年以内に上限を超える見込みです",
        "no_warning": "上限内で計画可能",
        "account": "口座区分",
        "quantity": "口数",
        "unit_price": "基準価額",
        "yen": "円",
        "over": "超過見込み",
        "lifetime": "生涯",
        "buy": "買付",
        "buy_amount": "金額 (円)",
        "buy_submit": "買付を記録",
        "buy_placeholder": "例: 50000",
        "today": "今日",
        "chat": "アシスタント",
        "chat_placeholder": "質問を入力（例: 今の損益は？）",
        "chat_send": "送信",
        "chat_new": "新しい会話",
        "chat_delete_confirm": "この会話を削除しますか？",
        "chat_default_model": "デフォルト",
        "chat_config_note": "AI チャットは未設定です（OPENAI_API_URL と OPENAI_API_TOKEN を設定してください）",
    },
    "en": {
        "title": "NISA Account Tracker",
        "lang_ja": "日本語",
        "lang_en": "English",
        "refresh": "Refresh prices",
        "last_update": "Last update",
        "no_data": "No data. Click refresh to fetch prices.",
        "current_value": "Current value",
        "total_cost": "Total invested",
        "total_profit": "Total profit",
        "portfolio": "Portfolio",
        "fund": "Fund",
        "cost": "Cost",
        "value": "Value",
        "profit": "Profit",
        "profit_pct": "Return",
        "per_fund": "Per fund",
        "price_history": "Price history",
        "yearly_limit": "Limits",
        "lifetime_limit": "Lifetime limit",
        "used": "Used",
        "remaining": "Remaining",
        "forecast": "10-year forecast",
        "forecast_value": "Projected value",
        "forecast_contributed": "Projected contributions",
        "forecast_profit": "Projected profit",
        "tsumitate_projection": "Tsumitate simulation (next 10 years)",
        "invested": "Invested",
        "projected_profit": "Projected profit",
        "monthly": "Monthly tsumitate",
        "monthly_growth": "Monthly growth investment",
        "annual_return": "Assumed annual return (%)",
        "save": "Save",
        "tsumitate": "Tsumitate",
        "growth": "Growth",
        "purchases": "Purchase history",
        "date": "Trade date",
        "pending_warning": "Projected to exceed the limit within 12 months",
        "no_warning": "Within limits over the next year",
        "account": "Account",
        "quantity": "Units",
        "unit_price": "NAV per 10k units",
        "yen": "yen",
        "over": "exceeded",
        "lifetime": "Lifetime",
        "buy": "Buy",
        "buy_amount": "Amount (¥)",
        "buy_submit": "Record purchase",
        "buy_placeholder": "e.g. 50000",
        "today": "Today",
        "chat": "Assistant",
        "chat_placeholder": "Ask something (e.g. what is my current profit?)",
        "chat_send": "Send",
        "chat_new": "New chat",
        "chat_delete_confirm": "Delete this conversation?",
        "chat_default_model": "Default",
        "chat_config_note": "AI chat is not configured (set OPENAI_API_URL and OPENAI_API_TOKEN)",
    },
}

_BUSY = threading.Lock()


def get_lang():
    lang = request.cookies.get("lang", "ja")
    return lang if lang in STRINGS else "ja"


def _fmt(n):
    if n is None:
        return "-"
    return f"¥{n:,.0f}"


def _fmt_pct(n):
    if n is None:
        return "-"
    return f"{n:+.1f}%"


def refresh_prices():
    if not _BUSY.acquire(blocking=False):
        return False
    try:
        records = []
        for isin, fund in db.FUNDS.items():
            records.extend(fetch.get_nav(isin, fund["code"], fund["name"]))
        if records:
            db.update_prices(
                [
                    {"isin": r["isin"], "date": r["date"], "nav": r["nav"]}
                    for r in records
                ]
            )
        return True
    finally:
        _BUSY.release()


def scheduler():
    while True:
        time.sleep(24 * 60 * 60)
        refresh_prices()


@app.route("/")
def index():
    lang = get_lang()
    t = STRINGS[lang]

    val = db.valuation()
    hist = db.history()
    usage = db.nisa_usage()
    fc = db.forecast()
    proj = db.tsumitate_projection()
    purchases = db.get_purchases()

    fc_display = dict(fc)
    if proj["points"]:
        last = proj["points"][-1]
        fc_display["projected_value"] = last["total"]
        fc_display["projected_contributed"] = last["invested"]
        fc_display["projected_profit"] = last["profit"]

    yearly_limit_rows = []
    for y in usage["yearly"]:
        d = usage["yearly"][y]
        yearly_limit_rows.append(
            {
                "year": y,
                "tsumitate": d["tsumitate"],
                "growth": d["growth"],
                "total": d["tsumitate"] + d["growth"],
            }
        )

    lifetime = {
        "tsumitate": usage["lifetime_tsumitate"],
        "growth": usage["lifetime_growth"],
        "total": usage["lifetime_total"],
    }

    return render_template(
        "index.html",
        t=t,
        lang=lang,
        val=val,
        fmt=_fmt,
        fmt_pct=_fmt_pct,
        funds=val["funds"],
        hist=hist,
        hist_json=json.dumps(hist),
        yearly_limit_rows=yearly_limit_rows,
        lifetime=lifetime,
        fc=fc_display,
        fc_json=json.dumps(fc),
        proj=proj,
        proj_json=json.dumps(proj),
        purchases=purchases,
        catalog=[
            {"isin": isin, "name": fund["name"]}
            for isin, fund in db.FUNDS.items()
        ],
        limits={
            "tsumitate": db.TSUMITATE_LIMIT,
            "growth": db.GROWTH_LIMIT,
            "yearly": db.YEARLY_LIMIT,
            "lifetime": db.LIFETIME_LIMIT,
            "growth_lifetime": db.GROWTH_LIFETIME_LIMIT,
        },
        chat_available=ai.available(),
    )


@app.route("/lang/<lang>")
def set_lang(lang):
    lang = lang if lang in STRINGS else "ja"
    resp = make_response(redirect(request.referrer or "/"))
    resp.set_cookie("lang", lang)
    return resp


@app.route("/refresh")
def refresh():
    refresh_prices()
    return redirect("/")


@app.route("/settings", methods=["POST"])
def settings():
    def parse(key):
        raw = request.form.get(key, "").strip()
        return float(raw) if raw else 0.0

    db.set_setting("monthly_tsumitate", max(0, parse("monthly_tsumitate")))
    db.set_setting("monthly_growth", max(0, parse("monthly_growth")))
    db.set_setting("annual_return_pct", max(0, parse("annual_return_pct")))
    return redirect(request.referrer or "/")


def map_fund(value):
    if value in db.FUNDS:
        return value
    key = db.normalize(value)
    for isin in db.FUNDS:
        if db.normalize(db.FUNDS[isin]["name"]) == key:
            return isin
    return None


def _valid_date(value):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if not m:
        return False
    year, month, day = (int(g) for g in m.groups())
    return 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


def _valid_amount(amount):
    if isinstance(amount, bool) or amount is None:
        return None
    if isinstance(amount, (int, float)):
        value = float(amount)
        return value if value > 0 else None
    if isinstance(amount, str) and re.fullmatch(r"\d+(\.\d+)?", amount):
        value = float(amount)
        return value if value > 0 else None
    return None


@app.route("/api/buy", methods=["POST"])
def api_buy():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="invalid request body"), 400

    date_label = str(data.get("date") or "")
    amount = _valid_amount(data.get("amount"))
    account = str(data.get("account") or "NISA (つみたて)").strip()

    if not _valid_date(date_label):
        return jsonify(error="invalid date"), 400
    if amount is None:
        return jsonify(error="invalid amount"), 400
    if not account:
        return jsonify(error="invalid account"), 400

    isin = map_fund(str(data.get("fund") or ""))
    if isin is None:
        return jsonify(error="unknown fund"), 404

    nav = db.nav_for_date(isin, date_label)
    if nav is None:
        return jsonify(error="no price on or before date"), 422

    purchase = db.add_purchase(date_label, isin, account, amount, nav)
    if purchase is None:
        return jsonify(error="duplicate purchase"), 409

    return jsonify(purchase), 201


def _chat_title(content):
    title = re.sub(r"\s+", " ", content).strip()
    return title[:50]


@app.route("/api/conversations")
def api_conversations():
    conversations = db.list_conversations()
    for c in conversations:
        c["title"] = c["title"] or c["updated_at"]
    return jsonify(conversations=conversations)


@app.route("/api/conversations/<int:conversation_id>")
def api_conversation(conversation_id):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        return jsonify(error="unknown conversation"), 404
    if not conv["title"]:
        conv["title"] = conv["updated_at"] or ""
    return jsonify(conv)


@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
def api_conversation_delete(conversation_id):
    if not db.delete_conversation(conversation_id):
        return jsonify(error="unknown conversation"), 404
    return "", 204


@app.route("/api/models")
def api_models():
    if not ai.available():
        return jsonify(error="AI chat is not configured"), 503
    return jsonify(models=ai.list_models())


@app.route("/api/chat", methods=["POST"])
def api_chat():
    if not ai.available():
        return jsonify(error="AI chat is not configured"), 503

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        return jsonify(error="invalid request body"), 400

    messages = []
    for item in data["messages"][-20:]:
        role = str(item.get("role") or "")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            messages.append({"role": role, "content": content})
    if not messages or messages[-1]["role"] != "user":
        return jsonify(error="the last message must be from the user"), 400

    conversation_id = data.get("conversation_id")
    if conversation_id is not None and type(conversation_id) is not int:
        return jsonify(error="invalid conversation_id"), 400

    if conversation_id is None:
        conversation_id = db.create_conversation()
    conversation = db.get_conversation(conversation_id)
    if conversation is None:
        return jsonify(error="unknown conversation"), 404

    for m in messages:
        db.add_message(conversation_id, m["role"], m["content"])
    if not conversation["title"] and messages[0]["role"] == "user":
        db.set_conversation_title(conversation_id, _chat_title(messages[0]["content"]))

    stored = [
        {"role": m["role"], "content": m["content"]}
        for m in conversation["messages"]
    ]
    model_messages = (stored + messages)[-40:]
    model = data.get("model") if isinstance(data.get("model"), str) else None

    try:
        reply = ai.run_chat(model_messages, model=model)
    except (
        requests.exceptions.RequestException,
        KeyError,
        IndexError,
        ValueError,
        RuntimeError,
    ) as exc:
        return jsonify(error=f"request to the AI endpoint failed: {exc}"), 502

    db.add_message(conversation_id, "assistant", reply)
    return jsonify(reply=reply, conversation_id=conversation_id)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.init_db()
    if not db.get_purchases():
        print("No purchases — importing input.csv...")
        added, skipped = db.load_purchases_csv()
        print(f"Imported {added} purchases ({skipped} duplicates).")

    print("Refreshing prices at startup...")
    refresh_prices()

    threading.Thread(target=scheduler, daemon=True).start()

    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()