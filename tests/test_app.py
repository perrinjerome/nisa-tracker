import pytest

from nisa_tracker import ai, app, db


@pytest.fixture
def client(db_seed):
    app.app.config["TESTING"] = True
    with app.app.test_client() as c:
        yield c, db_seed


def test_index_renders(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert b"NISA" in r.data


def test_lang_cookie(client):
    c, _ = client
    c.get("/lang/en")
    r = c.get("/")
    assert r.status_code == 200
    assert b"Portfolio" in r.data


def test_chat_disabled_returns_503(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ai, "API_URL", "")
    monkeypatch.setattr(ai, "API_TOKEN", "")
    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503


def test_chat_invalid_body(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    r = c.post("/api/chat", json={"messages": []})
    assert r.status_code == 400
    r = c.post("/api/chat", json={"nope": 1})
    assert r.status_code == 400
    r = c.post("/api/chat", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert r.status_code == 400


def test_chat_upstream_failure(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ai, "API_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502
    assert "error" in r.json


def test_api_models(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    monkeypatch.setattr(ai, "list_models", lambda: ["gpt-4o", "gpt-4o-mini"])
    r = c.get("/api/models")
    assert r.status_code == 200
    assert r.json == {"models": ["gpt-4o", "gpt-4o-mini"]}


def test_api_models_disabled(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ai, "API_URL", "")
    monkeypatch.setattr(ai, "API_TOKEN", "")
    r = c.get("/api/models")
    assert r.status_code == 503


def test_chat_forwards_model(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    captured = {}

    def fake_run(messages, model=None):
        captured["messages"] = messages
        captured["model"] = model
        return "yes"

    monkeypatch.setattr(ai, "run_chat", fake_run)

    r = c.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "model": "gpt-X"},
    )
    assert r.status_code == 200
    assert r.json["reply"] == "yes"
    assert captured["model"] == "gpt-X"

    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "model": 5})
    assert r.status_code == 200
    assert captured["model"] is None


def test_chat_persists_and_continues(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    captured = {}

    def fake_run(messages, model=None):
        captured["messages"] = list(messages)
        return "first reply"

    monkeypatch.setattr(ai, "run_chat", fake_run)

    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "q1"}]})
    assert r.status_code == 200
    cid = r.json["conversation_id"]
    assert captured["messages"] == [{"role": "user", "content": "q1"}]

    conv = c.get(f"/api/conversations/{cid}").json
    assert conv["title"] == "q1"
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["content"] == "first reply"

    r = c.post(
        "/api/chat",
        json={"conversation_id": cid, "messages": [{"role": "user", "content": "q2"}]},
    )
    assert r.json["conversation_id"] == cid

    conv = c.get(f"/api/conversations/{cid}").json
    assert [m["content"] for m in conv["messages"]] == [
        "q1",
        "first reply",
        "q2",
        "first reply",
    ]
    assert len(captured["messages"]) == 3
    assert [m["content"] for m in captured["messages"]] == ["q1", "first reply", "q2"]

    listing = c.get("/api/conversations").json["conversations"]
    assert listing[0]["id"] == cid
    assert listing[0]["count"] == 4


def test_chat_invalid_conversation_id(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    base = {"messages": [{"role": "user", "content": "hi"}]}

    r = c.post("/api/chat", json={**base, "conversation_id": "abc"})
    assert r.status_code == 400

    r = c.post("/api/chat", json={**base, "conversation_id": 99999})
    assert r.status_code == 404


def test_conversations_api(client):
    c, _ = client
    assert c.get("/api/conversations").json == {"conversations": []}

    cid = db.create_conversation()
    db.add_message(cid, "user", "hello")
    db.add_message(cid, "assistant", "world")

    listing = c.get("/api/conversations").json["conversations"]
    assert listing[0]["id"] == cid
    assert listing[0]["count"] == 2
    assert listing[0]["title"]  # empty title falls back to a timestamp

    conv = c.get(f"/api/conversations/{cid}").json
    assert [m["content"] for m in conv["messages"]] == ["hello", "world"]

    assert c.get("/api/conversations/99999").status_code == 404
    assert c.delete(f"/api/conversations/{cid}").status_code == 204
    assert c.delete(f"/api/conversations/{cid}").status_code == 404
    assert c.get("/api/conversations").json == {"conversations": []}


def test_buy_validation(client):
    c, isin = client
    base = {"date": "2024-02-01", "amount": 50000, "fund": isin, "account": "NISA (つみたて)"}

    r = c.post("/api/buy", json={"date": "nope", "amount": 50000, "fund": isin})
    assert r.status_code == 400

    r = c.post("/api/buy", json={"date": "2024-02-01", "amount": 0, "fund": isin})
    assert r.status_code == 400

    r = c.post("/api/buy", json={"date": "2024-02-01", "amount": 50000, "fund": "XX0000000000"})
    assert r.status_code == 404

    r = c.post("/api/buy", json=base)
    assert r.status_code == 201
    assert r.json["amount"] == 50000

    r = c.post("/api/buy", json=base)
    assert r.status_code == 409


def test_buy_no_price_on_date(client):
    c, isin = client
    r = c.post(
        "/api/buy",
        json={"date": "2020-01-01", "amount": 50000, "fund": isin},
    )
    assert r.status_code == 422