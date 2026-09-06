import json
import logging
import os
import statistics

import pytest

from nisa_tracker import ai, db


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def test_available_requires_env(monkeypatch):
    monkeypatch.setattr(ai, "API_URL", "http://x/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "")
    assert not ai.available()
    monkeypatch.setattr(ai, "API_TOKEN", "t")
    assert ai.available()


def test_list_models(monkeypatch):
    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        assert url == "http://mock/v1/models"
        assert headers is not None
        assert headers["Authorization"] == "Bearer tok"
        return FakeResponse(
            {
                "object": "list",
                "data": [
                    {"id": "gpt-4o-mini"},
                    {"id": "gpt-4o", "owned_by": "x"},
                    {"junk": 1},
                ],
            }
        )

    monkeypatch.setattr(ai.requests, "get", fake_get)
    assert ai.list_models() == ["gpt-4o-mini", "gpt-4o"]


def test_list_models_on_failure(monkeypatch):
    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")

    class Boom:
        def raise_for_status(self):
            raise ai.requests.exceptions.HTTPError("boom")

    monkeypatch.setattr(ai.requests, "get", lambda *a, **k: Boom())
    assert ai.list_models() == []

    monkeypatch.setattr(ai, "API_URL", "")
    assert ai.list_models() == []


def test_run_chat_uses_selected_model(db_seed, monkeypatch):
    captured = []

    def fake_post(url, headers=None, json=None, timeout=None):
        assert json is not None
        captured.append(json["model"])
        return FakeResponse({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    monkeypatch.setattr(ai, "API_MODEL", "gpt-4o-mini-default")
    monkeypatch.setattr(ai.requests, "post", fake_post)

    ai.run_chat([{"role": "user", "content": "hi"}], model="gpt-X")
    assert captured[-1] == "gpt-X"

    ai.run_chat([{"role": "user", "content": "hi"}], model="   ")
    assert captured[-1] == "gpt-4o-mini-default"

    ai.run_chat([{"role": "user", "content": "hi"}], model=123)
    assert captured[-1] == "gpt-4o-mini-default"

    ai.run_chat([{"role": "user", "content": "hi"}])
    assert captured[-1] == "gpt-4o-mini-default"


def test_python_eval_portfolio_variables(db_seed):
    code = (
        'result = (portfolio["total_cost"], portfolio["total_profit"],'
        ' portfolio["last_price_date"], portfolio["nisa_usage"]["lifetime_total"])'
    )
    cost, profit, date_, lifetime = json.loads(ai.python_eval(code)["result"])
    assert cost == pytest.approx(350_000)
    assert profit > 0
    assert date_ == "2026-01-31"
    assert lifetime == pytest.approx(300_000)


def test_python_eval_buy_history_variables(db_seed):
    code = (
        "tsu = [p for p in buy_history if p['account'] == 'NISA (つみたて)']\n"
        "result = {'dates': sorted(p['date'] for p in buy_history),"
        " 'is_missing_name': any(not p['name'] for p in buy_history),"
        " 'tsumitate_buys': len(tsu),"
        " 'total_invested': sum(p['invested'] for p in buy_history)}"
    )
    out = json.loads(ai.python_eval(code)["result"])
    assert out["dates"] == ["2024-01-01", "2025-01-01", "2026-01-01"]
    assert out["is_missing_name"] is False
    assert out["tsumitate_buys"] == 1
    assert out["total_invested"] == pytest.approx(350_000)


def test_python_eval_price_history_variables(db_seed):
    isin = db_seed
    code = (
        "names = {p['name'] for p in price_history}\n"
        "latest = {n: max(p['nav'] for p in price_history if p['name'] == n)"
        " for n in names}\n"
        "result = {'first': price_history[0], 'latest_by_name': latest,"
        " 'prices_len': len(price_history)}"
    )
    out = json.loads(ai.python_eval(code)["result"])
    assert out["first"]["isin"] == isin
    assert out["first"]["nav"] == 22000
    assert out["latest_by_name"] == {db.FUNDS[isin]["name"]: 26000}
    assert out["prices_len"] >= 3


def test_python_eval_nisa_usage(db_seed):
    # taxable purchase is not counted against NISA quotas
    code = (
        "u = portfolio['nisa_usage']\n"
        "result = {'yearly_2024': u['yearly']['2024'],"
        " 'lifetime_total': u['lifetime_total'],"
        " 'lifetime_growth': u['lifetime_growth']}"
    )
    out = json.loads(ai.python_eval(code)["result"])
    assert out["yearly_2024"] == {"tsumitate": 100_000, "growth": 0}
    assert out["lifetime_total"] == pytest.approx(300_000)
    assert out["lifetime_growth"] == pytest.approx(200_000)


def test_python_eval_can_forecast_from_data(db_seed):
    db.set_setting("monthly_tsumitate", "50000")
    db.set_setting("annual_return_pct", "5")
    code = (
        "cap = portfolio['total_value']\n"
        "for _ in range(12):\n"
        "    cap = (cap + 50000) * (1 + 0.05 / 12)\n"
        "result = {'start': portfolio['total_value'], 'year_from_now': round(cap, 2)}"
    )
    out = json.loads(ai.python_eval(code)["result"])
    assert out["year_from_now"] > out["start"]
    assert out["year_from_now"] > (out["start"] + 12 * 50000)


def test_python_eval_funds_map(db_seed):
    code = (
        "snap = dict(funds)\n"
        "result = {'count': len(snap),"
        " 'has_code': all('code' in v and 'name' in v for v in snap.values()),"
        " 'last_price_date': portfolio['last_price_date']}"
    )
    out = json.loads(ai.python_eval(code)["result"])
    assert out["count"] == len(db.FUNDS)
    assert out["has_code"] is True
    assert out["last_price_date"] == "2026-01-31"


def test_run_chat_tool_loop(db_seed, monkeypatch):
    calls = []
    jmod = json

    def fake_post(url, headers=None, json=None, timeout=None):
        assert headers is not None and json is not None
        calls.append({"url": url, "headers": headers, "json": json})
        assert headers["Authorization"] == "Bearer tok"
        assert url.endswith("/chat/completions")
        if len(calls) == 1:
            msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "python_eval",
                            "arguments": '{"code": "result = portfolio[\\"total_cost\\"]"}',
                        },
                    }
                ],
            }
        else:
            tool_msgs = [m for m in json["messages"] if m["role"] == "tool"]
            assert len(tool_msgs) == 1
            raw = jmod.loads(tool_msgs[0]["content"])
            assert jmod.loads(raw["result"]) == pytest.approx(350_000)
            msg = {"role": "assistant", "content": "done"}

        return FakeResponse({"choices": [{"message": msg}]})

    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    monkeypatch.setattr(ai.requests, "post", fake_post)

    reply = ai.run_chat([{"role": "user", "content": "what is my portfolio?"}])
    assert reply == "done"
    assert calls[0]["json"]["tools"][0]["function"]["name"] == "python_eval"


def test_run_chat_disabled(db_seed, monkeypatch):
    monkeypatch.setattr(ai, "API_URL", "")
    monkeypatch.setattr(ai, "API_TOKEN", "")
    with pytest.raises(RuntimeError, match="not configured"):
        ai.run_chat([{"role": "user", "content": "hi"}])


def test_run_chat_turn_budget(db_seed, monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        assert json is not None
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{len(json['messages'])}",
                    "type": "function",
                    "function": {
                        "name": "python_eval",
                        "arguments": '{"code": "1"}',
                    },
                }
            ],
        }
        return FakeResponse({"choices": [{"message": msg}]})

    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    monkeypatch.setattr(ai.requests, "post", fake_post)

    # a stub that always demands another tool call must hit the turn budget
    with pytest.raises(RuntimeError, match="too many tool calls"):
        ai.run_chat([{"role": "user", "content": "hi"}])


def _tool_schema(name):
    for tool in ai.TOOLS:
        assert isinstance(tool, dict)
        function = tool.get("function")
        assert isinstance(function, dict)
        if function.get("name") == name:
            return function
    return None


def test_python_eval_is_registered(db_seed):
    schema = _tool_schema("python_eval")
    assert schema is not None
    assert schema["parameters"]["required"] == ["code"]
    assert callable(ai.EXECUTE["python_eval"])
    assert "python_eval" in ai.SYSTEM_PROMPT


def test_python_eval_logs_submitted_code(db_seed, caplog):
    code = "result = portfolio['total_value'] / 2"
    with caplog.at_level(logging.INFO, logger="nisa_tracker.ai"):
        res = ai.python_eval(code)
    assert "error" not in res
    assert any(
        r.name == "nisa_tracker.ai" and code in r.getMessage() for r in caplog.records
    )


def test_python_eval_math_and_statistics(db_seed):
    code = (
        'nums = [p["invested"] for p in buy_history]\n'
        "result = (math.sqrt(16), round(statistics.mean(nums), 2), statistics.stdev(nums))"
    )
    res = ai.python_eval(code)
    result = json.loads(res["result"])
    assert result == [4.0, pytest.approx(116_666.67), pytest.approx(statistics.stdev([100_000, 200_000, 50_000]))]


def test_python_eval_data_is_available(db_seed):
    isin = db_seed
    code = (
        'result = {"funds": list(funds)[0], "buys": len(buy_history),'
        ' "sales": len(sales_history), "prices": len(price_history),'
        ' "value": portfolio["total_value"], "today_is_iso": len(today) == 10}'
    )
    res = ai.python_eval(code)
    result = json.loads(res["result"])
    assert result["funds"] == isin
    assert result["buys"] == 3
    assert result["sales"] == 0
    assert result["prices"] == 3
    assert result["value"] == pytest.approx(db.valuation()["total_value"])
    assert result["today_is_iso"] is True


def test_python_eval_captures_print(db_seed):
    res = ai.python_eval('print("total:", portfolio["total_cost"])\nresult = 42')
    assert json.loads(res["result"]) == 42
    assert "total:" in res["stdout"]


def test_python_eval_arithmetic_error_is_reported(db_seed):
    res = ai.python_eval("result = sum(portfolio['total_cost'], 0)")
    assert "error" in res
    assert "TypeError" in res["error"]


def test_python_eval_rejects_import(db_seed):
    res = ai.python_eval("import os\nresult = 1")
    assert "error" in res
    assert "import" in res["error"]


def test_python_eval_rejects_while(db_seed):
    res = ai.python_eval("while True:\n    pass")
    assert "error" in res
    assert "while" in res["error"]


def test_python_eval_rejects_dunder_attribute(db_seed):
    res = ai.python_eval("result = buy_history[0].__class__.__name__")
    assert "error" in res
    assert "dunder" in res["error"]


def test_python_eval_unknown_name_is_error(db_seed):
    res = ai.python_eval("result = os.listdir('/')")
    assert "error" in res
    assert "os" in res["error"]


def test_python_eval_rejects_non_string_code(db_seed):
    assert "error" in ai.python_eval(1234)
    assert "error" in ai.python_eval("")


def test_python_eval_timeout(db_seed, monkeypatch):
    monkeypatch.setattr(ai, "PYTHON_EVAL_TIMEOUT", 1.0)
    res = ai.python_eval("for i in range(10**9):\n    pass\nresult = 1")
    assert "error" in res


def test_run_chat_with_python_eval_tool(db_seed, monkeypatch):
    calls = []
    jmod = json

    def fake_post(url, headers=None, json=None, timeout=None):
        assert headers is not None and json is not None
        calls.append((url, headers, json))
        if len(calls) == 1:
            msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_py",
                        "type": "function",
                        "function": {
                            "name": "python_eval",
                            "arguments": '{"code": "result = len(buy_history)"}',
                        },
                    }
                ],
            }
        else:
            tool_msgs = [m for m in json["messages"] if m["role"] == "tool"]
            assert jmod.loads(tool_msgs[0]["content"])["result"] == "3"
            msg = {"role": "assistant", "content": "computed"}

        return FakeResponse({"choices": [{"message": msg}]})

    monkeypatch.setattr(ai, "API_URL", "http://mock/v1")
    monkeypatch.setattr(ai, "API_TOKEN", "tok")
    monkeypatch.setattr(ai.requests, "post", fake_post)

    reply = ai.run_chat([{"role": "user", "content": "how many buys?"}])
    assert reply == "computed"
    # tools are advertised to the model on the first request
    advertised = {t["function"]["name"] for t in calls[0][2]["tools"]}
    assert "python_eval" in advertised


def test_load_env_file_and_reload(db_seed, tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# me\nOPENAI_API_URL=http://from-file/v1\nOPENAI_API_TOKEN=file-tok\n"
        "OPENAI_API_MODEL=custom-model\n"
    )
    monkeypatch.delenv("OPENAI_API_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_MODEL", raising=False)
    ai.load_env_file(str(env_path))
    assert os.environ["OPENAI_API_URL"] == "http://from-file/v1"
    assert os.environ["OPENAI_API_TOKEN"] == "file-tok"

    ai.reload_env()
    assert ai.API_URL == "http://from-file/v1"
    assert ai.API_TOKEN == "file-tok"
    assert ai.API_MODEL == "custom-model"


def test_real_env_overrides_dotenv(db_seed, tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_URL=http://from-file/v1\nOPENAI_API_TOKEN=file-tok\n")
    monkeypatch.setenv("OPENAI_API_URL", "http://real-env/v1")
    ai.load_env_file(str(env_path))
    assert os.environ["OPENAI_API_URL"] == "http://real-env/v1"