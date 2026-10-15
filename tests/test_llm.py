import json

import httpx
import pytest

from agent.llm import OllamaLLM, OpenAICompatLLM, make_llm
from agent.triage_agent import TriageResult, classify

SCHEMA = TriageResult.model_json_schema()
GOOD = TriageResult(category="refund", urgency="high", reason="r", suggested_note="n").model_dump_json()


def make(handler, **kw):
    return OpenAICompatLLM("m", "https://api.example.com/v1", api_key="sk-test",
                           transport=httpx.MockTransport(handler), **kw)


def ok_reply(content="hi", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(200, json={"choices": [{"message": msg}]})


def test_auth_header_and_plain_chat():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["url"] = str(req.url)
        return ok_reply("hello")

    out = make(handler).chat([{"role": "user", "content": "x"}])
    assert out == {"content": "hello", "tool_calls": []}
    assert seen["auth"] == "Bearer sk-test" and seen["url"].endswith("/v1/chat/completions")


def test_structured_output_degrades_json_schema_to_json_object():
    formats = []

    def handler(req):
        body = json.loads(req.content)
        fmt = (body.get("response_format") or {}).get("type")
        formats.append(fmt)
        if fmt == "json_schema":
            return httpx.Response(400, json={"error": {"message": "unsupported response_format"}})
        assert "JSON schema" in body["messages"][0]["content"]  # schema is also stated in the prompt
        return ok_reply(GOOD)

    llm = make(handler)
    result, source = classify(llm, "refund not received")
    assert source == "llm" and result.category == "refund"
    assert formats == ["json_schema", "json_object"]


def test_tool_call_history_is_converted_to_openai_format():
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.content)
        return ok_reply("done")

    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "list_tickets", "arguments": {"status": 2}}}]},
        {"role": "tool", "tool_name": "list_tickets", "content": "{}"},
    ]
    make(handler).chat(history, tools=[{"type": "function", "function": {"name": "list_tickets"}}])
    msgs = captured["body"]["messages"]
    call = msgs[1]["tool_calls"][0]
    assert call["id"] and json.loads(call["function"]["arguments"]) == {"status": 2}
    assert msgs[2]["tool_call_id"] == call["id"] and "tool_name" not in msgs[2]


def test_tool_calls_parsed_from_response():
    tc = [{"id": "1", "type": "function", "function": {"name": "get_ticket", "arguments": '{"ticket_id": 4}'}}]
    out = make(lambda req: ok_reply("", tc)).chat([{"role": "user", "content": "x"}], tools=[{}])
    assert out["tool_calls"] == [{"name": "get_ticket", "arguments": {"ticket_id": 4}}]


def test_auth_error_is_readable_and_not_degraded():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "Incorrect API key"}})

    with pytest.raises(Exception, match="401.*Incorrect API key"):
        make(handler).chat([{"role": "user", "content": "x"}], schema=SCHEMA)
    assert len(calls) == 1  # 401 must not trigger the response_format fallbacks


def test_classify_falls_back_when_provider_errors():
    llm = make(lambda req: httpx.Response(401, json={"error": "bad key"}))
    result, source = classify(llm, "refund not received")
    assert source == "fallback"


def test_health():
    assert make(lambda r: httpx.Response(200, json={"data": []})).health() == (True, "ok")
    assert make(lambda r: httpx.Response(404)).health() == (True, "ok")  # no /models listing
    ok, msg = make(lambda r: httpx.Response(401)).health()
    assert not ok and "key" in msg.lower()


def test_is_cloud_flag():
    assert make(lambda r: ok_reply()).is_cloud is True
    local = OpenAICompatLLM("m", "http://localhost:1234/v1")
    assert local.is_cloud is False


def test_factory_presets_and_env(monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert isinstance(make_llm(), OllamaLLM)  # default stays local Ollama
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    g = make_llm("groq", "llama-3.3-70b-versatile")
    assert g.base_url == "https://api.groq.com/openai/v1"
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("LLM_MODEL", "my-model")
    c = make_llm()
    assert c.base_url == "http://localhost:8000/v1" and c.model == "my-model" and not c.is_cloud
    with pytest.raises(ValueError):
        make_llm("nope")
    monkeypatch.delenv("LLM_MODEL")
    with pytest.raises(ValueError):
        make_llm("openai")  # model missing


def test_ollama_hosted_is_cloud():
    assert OllamaLLM(model="x", host="https://ollama.com", api_key="k").is_cloud is True
    assert OllamaLLM(model="x", host="http://localhost:11434").is_cloud is False
