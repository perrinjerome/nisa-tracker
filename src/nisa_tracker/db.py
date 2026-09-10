import csv
import os
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def today():
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


def now_iso():
    return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")

DB_PATH = os.path.join(os.path.dirname(__file__), "nisa.db")

def _csv_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.getcwd(), here, os.path.dirname(here)):
        candidate = os.path.join(base, "input.csv")
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(os.getcwd(), "input.csv")

FUNDS = {
    "JP90C000H1T1": {
        "name": "eMAXIS Slim 全世界株式 (オール・カントリー)",
        "code": "0331418A",
    },
    "JP90C000GKC6": {
        "name": "eMAXIS Slim 米国株式 (S&P500)",
        "code": "03311187",
    },
}

TSUMITATE_LIMIT = 1_200_000      # yearly, yen
GROWTH_LIMIT = 2_400_000         # yearly, yen
YEARLY_LIMIT = 3_600_000         # combined, yen
LIFETIME_LIMIT = 18_000_000      # yen
GROWTH_LIFETIME_LIMIT = 12_000_000

def normalize(name):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name).lower())


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS purchases (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL,
            name       TEXT NOT NULL,
            isin       TEXT NOT NULL,
            code       TEXT NOT NULL,
            account    TEXT NOT NULL,
            quantity   REAL NOT NULL,
            unit_price REAL NOT NULL,
            fee        REAL NOT NULL DEFAULT 0,
            tax        REAL NOT NULL DEFAULT 0,
            invested   REAL NOT NULL,
            UNIQUE (date, isin, quantity, unit_price, account)
        );

        CREATE TABLE IF NOT EXISTS prices (
            isin TEXT NOT NULL,
            date TEXT NOT NULL,
            nav  REAL NOT NULL,
            PRIMARY KEY (isin, date)
        );

        CREATE INDEX IF NOT EXISTS idx_prices_isin_date ON prices (isin, date);

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversation_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_conv_msgs_conv
            ON conversation_messages (conversation_id, id);
        """
    )
    conn.commit()
    conn.close()


def parse_date(value):
    value = value.strip()
    if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", value):
        month, day, year = value.split("/")
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return value


def fund_for(display_name):
    key = normalize(display_name)
    for isin, fund in FUNDS.items():
        if normalize(fund["name"]) == key:
            return isin, fund["name"]
    raise ValueError(f"Unknown fund: {display_name}")


def load_purchases_csv(path=None):
    if path is None:
        path = _csv_path()
    if not os.path.isfile(path):
        return 0, 0
    init_db()
    conn = connect()
    added = skipped = 0

    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row.get("取引") != "投信金額買付" or not row.get("約定数量"):
                continue

            isin, name = fund_for(row["銘柄"])
            quantity = float(row["約定数量"])
            unit_price = float(row["約定単価"])
            fee = float(row.get("手数料/諸経費等") or 0)
            tax = float(row.get("税額") or 0)
            invested = quantity * unit_price / 10000 + fee + tax
            account = row["預り"]

            params = (
                parse_date(row["約定日"]),
                name,
                isin,
                FUNDS[isin]["code"],
                account,
                quantity,
                unit_price,
                fee,
                tax,
                invested,
            )

            try:
                conn.execute(
                    """
                    INSERT INTO purchases
                        (date, name, isin, code, account, quantity, unit_price,
                         fee, tax, invested)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    params,
                )
                added += 1
            except sqlite3.IntegrityError:
                skipped += 1

    conn.commit()
    conn.close()
    return added, skipped


def update_prices(records):
    init_db()
    conn = connect()
    conn.executemany(
        """
        INSERT INTO prices (isin, date, nav)
        VALUES (:isin, :date, :nav)
        ON CONFLICT (isin, date) DO UPDATE SET nav = excluded.nav
        """,
        records,
    )
    conn.commit()
    conn.close()


def add_purchase(date_label, isin, account, amount, nav):
    init_db()
    conn = connect()
    quantity = amount * 10000 / nav
    params = (
        date_label,
        FUNDS[isin]["name"],
        isin,
        FUNDS[isin]["code"],
        account,
        quantity,
        nav,
        0.0,
        0.0,
        amount,
    )
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO purchases
            (date, name, isin, code, account, quantity, unit_price, fee, tax,
             invested)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params,
    )
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        return None

    return {
        "date": date_label,
        "isin": isin,
        "name": FUNDS[isin]["name"],
        "account": account,
        "quantity": quantity,
        "nav": nav,
        "amount": amount,
    }


def nav_for_date(isin, date_label):
    conn = connect()
    row = conn.execute(
        "SELECT nav FROM prices WHERE isin = ? AND date <= ? "
        "ORDER BY date DESC LIMIT 1",
        (isin, date_label),
    ).fetchone()
    conn.close()
    return row["nav"] if row else None


def get_purchases():
    conn = connect()
    rows = conn.execute("SELECT * FROM purchases ORDER BY date DESC").fetchall()
    conn.close()
    return rows


def get_prices(isin=None):
    conn = connect()
    if isin:
        rows = conn.execute(
            "SELECT * FROM prices WHERE isin = ? ORDER BY date",
            (isin,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM prices ORDER BY date").fetchall()
    conn.close()
    return rows


def current_prices():
    conn = connect()
    rows = conn.execute(
        """
        SELECT p.isin, p.date, p.nav
        FROM prices p
        JOIN (SELECT isin, MAX(date) AS d FROM prices GROUP BY isin) m
          ON p.isin = m.isin AND p.date = m.d
        """
    ).fetchall()
    conn.close()
    return rows


def last_price_date():
    conn = connect()
    row = conn.execute("SELECT MAX(date) AS d FROM prices").fetchone()
    conn.close()
    return row["d"]


def get_settings():
    conn = connect()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key, value):
    conn = connect()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def create_conversation():
    stamp = now_iso()
    conn = connect()
    cur = conn.execute(
        "INSERT INTO conversations (title, created_at, updated_at) "
        "VALUES ('', ?, ?)",
        (stamp, stamp),
    )
    conn.commit()
    conversation_id = cur.lastrowid
    conn.close()
    return conversation_id


def list_conversations():
    conn = connect()
    rows = conn.execute(
        """
        SELECT c.id, c.title, c.updated_at, COUNT(m.id) AS count
        FROM conversations c
        LEFT JOIN conversation_messages m ON m.conversation_id = c.id
        GROUP BY c.id
        ORDER BY c.updated_at DESC, c.id DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversation(conversation_id):
    conn = connect()
    conv = conn.execute(
        "SELECT id, title, updated_at FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if conv is None:
        conn.close()
        return None
    messages = conn.execute(
        "SELECT id, role, content FROM conversation_messages "
        "WHERE conversation_id = ? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    conn.close()
    return {
        "id": conv["id"],
        "title": conv["title"],
        "updated_at": conv["updated_at"],
        "messages": [dict(m) for m in messages],
    }


def add_message(conversation_id, role, content):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO conversation_messages (conversation_id, role, content) "
        "VALUES (?, ?, ?)",
        (conversation_id, role, content),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now_iso(), conversation_id),
    )
    conn.commit()
    message_id = cur.lastrowid
    conn.close()
    return message_id


def set_conversation_title(conversation_id, title):
    conn = connect()
    conn.execute(
        "UPDATE conversations SET title = ? WHERE id = ?",
        (title, conversation_id),
    )
    conn.commit()
    conn.close()


def delete_conversation(conversation_id):
    conn = connect()
    conn.execute(
        "DELETE FROM conversation_messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    cur = conn.execute(
        "DELETE FROM conversations WHERE id = ?",
        (conversation_id,),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def valuation():
    init_db()
    purchases = get_purchases()
    latest = {r["isin"]: r["nav"] for r in current_prices()}

    funds = {}
    for p in purchases:
        fund = funds.setdefault(
            p["isin"],
            {
                "isin": p["isin"],
                "name": p["name"],
                "code": p["code"],
                "cost": 0.0,
                "quantity": 0.0,
                "units": 0,
            },
        )
        fund["cost"] += p["invested"]
        fund["quantity"] += p["quantity"]
        fund["units"] += 1

    total_cost = 0.0
    total_value = 0.0

    for isin, fund in funds.items():
        nav = latest.get(isin)
        fund["nav"] = nav
        fund["value"] = fund["quantity"] / 10000 * nav if nav else None
        fund["profit"] = (
            fund["value"] - fund["cost"] if fund["value"] is not None else None
        )
        fund["profit_pct"] = (
            fund["profit"] / fund["cost"] * 100
            if fund["profit"] is not None
            else None
        )
        total_cost += fund["cost"]
        total_value += fund["value"] or 0.0

    return {
        "funds": sorted(funds.values(), key=lambda f: f["cost"], reverse=True),
        "total_cost": total_cost,
        "total_value": total_value,
        "total_profit": total_value - total_cost,
        "total_profit_pct": (
            (total_value - total_cost) / total_cost * 100 if total_cost else 0.0
        ),
        "last_price_date": last_price_date(),
    }


def history():
    purchases = get_purchases()
    conn = connect()
    price_rows = conn.execute(
        "SELECT isin, date, nav FROM prices ORDER BY date"
    ).fetchall()
    conn.close()

    prices = {}
    for r in price_rows:
        prices.setdefault(r["isin"], {})[r["date"]] = r["nav"]

    dates = set()
    for p in purchases:
        dates.add(p["date"])
    for r in price_rows:
        dates.add(r["date"])

    series = {}
    for isin, fund in FUNDS.items():
        series[isin] = {"name": fund["name"], "cost": [], "value": []}

    for date_label in sorted(dates):
        total_cost = 0.0
        total_value = 0.0
        for isin, fund in FUNDS.items():
            holdings = sum(
                p["quantity"]
                for p in purchases
                if p["isin"] == isin and p["date"] <= date_label
            )
            nav = prices.get(isin, {}).get(date_label)
            isin_cost = sum(
                p["invested"]
                for p in purchases
                if p["isin"] == isin and p["date"] <= date_label
            )
            isin_value = holdings / 10000 * nav if nav is not None else None
            if isin_value is not None:
                series[isin]["cost"].append(
                    {"date": date_label, "value": round(isin_cost, 2)}
                )
                series[isin]["value"].append(
                    {"date": date_label, "value": round(isin_value, 2)}
                )
                total_cost += isin_cost
                total_value += isin_value

    total_costs = {}
    total_values = {}
    for isin, fund in FUNDS.items():
        for point in series[isin]["cost"]:
            total_costs[point["date"]] = total_costs.get(point["date"], 0.0) + point["value"]
        for point in series[isin]["value"]:
            total_values[point["date"]] = total_values.get(point["date"], 0.0) + point["value"]

    series["total"] = {
        "name": "Total",
        "cost": [
            {"date": d, "value": round(v, 2)}
            for d, v in sorted(total_costs.items())
        ],
        "value": [
            {"date": d, "value": round(v, 2)}
            for d, v in sorted(total_values.items())
        ],
    }

    total_profits = {}
    for d in sorted(set(total_costs) & set(total_values)):
        total_profits[d] = total_values[d] - total_costs[d]
    series["total"]["profit"] = [
        {"date": d, "value": round(v, 2)}
        for d, v in sorted(total_profits.items())
    ]

    return series


def nisa_usage():
    purchases = get_purchases()

    yearly = {}
    lifetime_tsumitate = 0.0
    lifetime_growth = 0.0

    for p in purchases:
        account = p["account"]
        if account == "NISA (つみたて)":
            year = p["date"][:4]
            yearly.setdefault(year, {"tsumitate": 0.0, "growth": 0.0})[
                "tsumitate"
            ] += p["invested"]
            lifetime_tsumitate += p["invested"]
        elif account == "NISA (成長)":
            year = p["date"][:4]
            yearly.setdefault(year, {"tsumitate": 0.0, "growth": 0.0})[
                "growth"
            ] += p["invested"]
            lifetime_growth += p["invested"]

    lifetime_total = lifetime_tsumitate + lifetime_growth

    return {
        "yearly": dict(sorted(yearly.items())),
        "lifetime_tsumitate": lifetime_tsumitate,
        "lifetime_growth": lifetime_growth,
        "lifetime_total": lifetime_total,
    }


def start_of_next_month(d):
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def forecast():
    purchases = get_purchases()
    settings = get_settings()
    monthly_tsumitate = (
        float(settings["monthly_tsumitate"])
        if "monthly_tsumitate" in settings
        else None
    )
    monthly_growth = (
        float(settings["monthly_growth"])
        if "monthly_growth" in settings
        else None
    )
    annual_return = float(settings.get("annual_return_pct") or 5.0)
    monthly_return = annual_return / 100 / 12

    window = today() - timedelta(days=120)
    nisa_tsumitate = [
        p["invested"]
        for p in purchases
        if p["account"] == "NISA (つみたて)"
        and p["date"] >= window.isoformat()
    ]
    growth_tsumitate = [
        p["invested"]
        for p in purchases
        if p["account"] == "NISA (成長)"
        and p["date"] >= window.isoformat()
    ]

    if monthly_tsumitate is None:
        monthly_tsumitate = round(sum(nisa_tsumitate) / len(nisa_tsumitate)) if nisa_tsumitate else 0.0
    elif monthly_tsumitate < 0:
        monthly_tsumitate = 0.0
    if monthly_growth is None:
        monthly_growth = round(sum(growth_tsumitate) / len(growth_tsumitate)) if growth_tsumitate else 0.0
    elif monthly_growth < 0:
        monthly_growth = 0.0

    usage = nisa_usage()
    current_total = valuation()["total_value"]

    now = today()
    cursor = date(now.year, now.month, 1)
    months = []

    value = current_total
    contributed = 0.0
    prev_year = now.year
    tsumitate_used = usage["yearly"].get(str(now.year), {}).get("tsumitate", 0.0)
    growth_used = usage["yearly"].get(str(now.year), {}).get("growth", 0.0)

    for i in range(121):
        value = value * (1 + monthly_return)

        year = cursor.year
        if year != prev_year:
            tsumitate_used = 0.0
            growth_used = 0.0
        prev_year = year

        tsumitate_used += monthly_tsumitate
        growth_used += monthly_growth
        contributed += monthly_tsumitate + monthly_growth

        months.append(
            {
                "date": cursor.isoformat(),
                "value": round(value, 2),
                "contributed": round(contributed, 2),
                "tsumitate_used": round(tsumitate_used, 2),
                "growth_used": round(growth_used, 2),
                "year": str(year),
            }
        )

        cursor = start_of_next_month(cursor)

    last = months[-1]
    over_limit_monthly = (
        tsumitate_used > TSUMITATE_LIMIT or growth_used > GROWTH_LIMIT
    )
    over_lifetime = (
        usage["lifetime_total"] + contributed > LIFETIME_LIMIT
        or usage["lifetime_growth"] + growth_used > GROWTH_LIFETIME_LIMIT
    )

    return {
        "months": months,
        "monthly_tsumitate": monthly_tsumitate,
        "monthly_growth": monthly_growth,
        "annual_return_pct": annual_return,
        "current_value": current_total,
        "projected_value": last["value"],
        "projected_contributed": last["contributed"],
        "projected_profit": last["value"] - current_total - last["contributed"],
        "over_limit_monthly": over_limit_monthly,
        "over_lifetime": over_lifetime,
    }


def tsumitate_projection(months=120):
    settings = get_settings()
    annual_return = float(settings.get("annual_return_pct") or 5.0)
    monthly_return = annual_return / 100 / 12
    monthly_tsumitate = float(settings["monthly_tsumitate"]) if "monthly_tsumitate" in settings else None
    monthly_growth = float(settings["monthly_growth"]) if "monthly_growth" in settings else None
    if monthly_tsumitate is None or monthly_growth is None:
        window = today() - timedelta(days=365)
        recent_purchases = [p for p in get_purchases() if p["date"] >= window.isoformat()]
        n_months = max(1, len({p["date"][:7] for p in recent_purchases}))
        if monthly_tsumitate is None:
            monthly_tsumitate = (
                sum(p["invested"] for p in recent_purchases if p["account"] == "NISA (つみたて)") / n_months
            )
        if monthly_growth is None:
            monthly_growth = (
                sum(p["invested"] for p in recent_purchases if p["account"] == "NISA (成長)") / n_months
            )
    monthly_tsumitate = max(0.0, monthly_tsumitate)
    monthly_growth = max(0.0, monthly_growth)
    deposit = monthly_tsumitate + monthly_growth

    val = valuation()
    now_month = f"{today():%Y-%m}-01"
    points = [
        {
            "date": now_month,
            "invested": round(val["total_cost"], 2),
            "profit": round(val["total_profit"], 2),
            "total": round(val["total_value"], 2),
        }
    ]

    capital = val["total_value"]
    invested = val["total_cost"]

    cursor = start_of_next_month(today())
    for _ in range(months):
        invested += deposit
        capital = (capital + deposit) * (1 + monthly_return)
        profit = capital - invested
        points.append(
            {
                "date": cursor.isoformat(),
                "invested": round(invested, 2),
                "profit": round(profit, 2),
                "total": round(capital, 2),
            }
        )
        cursor = start_of_next_month(cursor)

    return {
        "points": points,
        "monthly_deposit": round(deposit, 2),
        "annual_return_pct": annual_return,
    }


if __name__ == "__main__":
    import getopt
    import sys

    opts, _ = getopt.getopt(sys.argv[1:], "i:", ["input="])
    path = _csv_path()
    for o, a in opts:
        if o in ("-i", "--input"):
            path = a

    added, skipped = load_purchases_csv(path)
    print(f"Added {added}, skipped {skipped}")