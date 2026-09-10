import pytest

from nisa_tracker import db


def test_parse_date_formats():
    assert db.parse_date("1/2/2024") == "2024-01-02"
    assert db.parse_date("2024-01-02") == "2024-01-02"


def test_missing_csv_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_csv_path", lambda: str(tmp_path / "missing.csv"))
    assert db.load_purchases_csv() == (0, 0)


def test_normalize_case_spaces():
    assert db.normalize(" ｅＭＡＸＩＳ Ｓｌｉｍ ") == "emaxisslim"


def test_add_and_get_purchases_desc(db_seed):
    rows = db.get_purchases()
    assert [r["date"] for r in rows] == ["2026-01-01", "2025-01-01", "2024-01-01"]


def test_duplicate_purchase_returns_none(db_seed):
    isin = db_seed
    first = db.add_purchase("2024-06-01", isin, "NISA (成長)", 50_000, 23_000)
    dup = db.add_purchase("2024-06-01", isin, "NISA (成長)", 50_000, 23_000)
    assert first is not None
    assert dup is None
    assert len(db.get_purchases()) == 4


def test_prices_insert_and_query(db_seed):
    rows = db.get_prices(db_seed)
    assert [r["nav"] for r in rows] == [22000, 24000, 26000]
    assert db.current_prices()[0]["nav"] == 26000
    assert db.nav_for_date(db_seed, "2024-06-01") == 22000
    assert db.last_price_date() == "2026-01-31"


def test_valuation_totals(db_seed):
    val = db.valuation()
    assert val["total_cost"] == pytest.approx(350_000)
    assert val["total_profit"] > 0
    assert len(val["funds"]) == 1
    fund = val["funds"][0]
    assert fund["value"] == pytest.approx(fund["quantity"] / 10000 * 26000)
    assert fund["profit"] == pytest.approx(fund["value"] - fund["cost"])


def test_nisa_usage_by_account(db_seed):
    usage = db.nisa_usage()
    assert usage["lifetime_tsumitate"] == pytest.approx(100_000)
    assert usage["lifetime_growth"] == pytest.approx(200_000)
    # the taxable (特定/一般) purchase does not consume NISA quota
    assert usage["lifetime_total"] == pytest.approx(300_000)
    assert usage["yearly"]["2024"]["tsumitate"] == pytest.approx(100_000)


def test_forecast_uses_settings(db_seed):
    db.set_setting("monthly_tsumitate", "50000")
    db.set_setting("monthly_growth", "10000")
    db.set_setting("annual_return_pct", "5")
    fc = db.forecast()
    assert fc["monthly_tsumitate"] == 50000.0
    assert fc["projected_value"] > fc["current_value"]
    assert fc["projected_contributed"] == pytest.approx(60_000 * 121)
    assert fc["projected_profit"] == pytest.approx(
        fc["projected_value"] - fc["current_value"] - fc["projected_contributed"]
    )
    assert not fc["over_limit_monthly"]
    assert not fc["over_lifetime"]


def test_forecast_over_limit_when_blowing_yearly_cap(db_seed):
    db.set_setting("monthly_tsumitate", "300000")
    db.set_setting("monthly_growth", "0")
    db.set_setting("annual_return_pct", "5")
    fc = db.forecast()
    assert fc["over_limit_monthly"]
    assert fc["over_lifetime"]


def test_projection_endpoints(db_seed):
    proj = db.tsumitate_projection(months=24)
    assert len(proj["points"]) == 25
    first, last = proj["points"][0], proj["points"][-1]
    assert first["total"] == pytest.approx(db.valuation()["total_value"])
    assert last["total"] > last["invested"]


def test_conversation_roundtrip(db_seed):
    cid = db.create_conversation()
    conv = db.get_conversation(cid)
    assert conv["id"] == cid
    assert conv["messages"] == []

    db.add_message(cid, "user", "hello")
    db.add_message(cid, "assistant", "hi")
    db.add_message(cid, "user", "again")

    got = db.get_conversation(cid)
    assert [m["role"] for m in got["messages"]] == ["user", "assistant", "user"]
    assert [m["content"] for m in got["messages"]] == ["hello", "hi", "again"]

    listing = db.list_conversations()
    assert listing[0]["id"] == cid
    assert listing[0]["count"] == 3


def test_conversation_list_orders_by_last_activity(db_seed):
    first = db.create_conversation()
    second = db.create_conversation()
    db.add_message(first, "user", "old")
    db.add_message(second, "user", "new")
    listing = db.list_conversations()
    assert [c["id"] for c in listing] == [second, first]
    assert listing[0]["count"] == 1


def test_conversation_title_and_delete(db_seed):
    cid = db.create_conversation()
    db.add_message(cid, "user", "portrait")
    db.set_conversation_title(cid, "portrait")
    assert db.get_conversation(cid)["title"] == "portrait"

    assert db.delete_conversation(cid)
    assert db.get_conversation(cid) is None
    assert db.list_conversations() == []
    assert not db.delete_conversation(cid)