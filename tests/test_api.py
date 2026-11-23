import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from side_panel import api
from side_panel.admission import Admission
from side_panel.forms import FormStore

from .test_forms import CHUNKS
from .test_turn import FakeLLM, pairs

IDS = {"get-help", "contact-bdc", "costs", "publication-submission", "stay-connected"}
CHAT = {"values": {"cf_subject": "Login"}, "messages": [{"role": "user", "text": "shorten it"}], "summary": "S"}


def _client(monkeypatch, fetch, llm):
    monkeypatch.setattr(api, "store", FormStore("http://mcp", refresh=600, fetch=fetch))
    monkeypatch.setattr(api, "get_llm", lambda: llm)
    monkeypatch.setattr(api, "admission", Admission(0, 0))
    return TestClient(api.app)


@pytest.fixture
def llm():
    return FakeLLM({"summary": "Login loop.", "reply": "Here's a draft.", "values": pairs(cf_subject="Login loop")})


@pytest.fixture
def client(monkeypatch, llm):
    async def fetch(url):
        return CHUNKS
    return _client(monkeypatch, fetch, llm)


@pytest.fixture
def client_down(monkeypatch, llm):
    async def fetch(url):
        raise httpx.ConnectError("down")
    return _client(monkeypatch, fetch, llm)


def test_tools_lists_definitions(client):
    r = client.get("/tools")
    assert r.status_code == 200 and {f["id"] for f in r.json()["form"]} == IDS
    assert client.get("/health").json() == {"status": "ok", "forms": 5}


def test_no_forms_is_503(client_down):
    r = client_down.get("/tools")
    assert r.status_code == 503 and "doc-mcp" in r.json()["detail"]
    assert client_down.get("/health").json() == {"status": "ok", "forms": 0}
    assert client_down.post("/tools/form/get-help/open", json={}).status_code == 503


def test_open_and_chat_shapes(client):
    r = client.post("/tools/form/get-help/open",
                    json={"history": [{"role": "user", "content": "my login loops"}], "question": "Why?"})
    assert r.status_code == 200
    assert r.json() == {"summary": "Login loop.", "values": {"cf_subject": "Login loop"}, "reply": "Here's a draft.",
                        "blocked": False}
    r = client.post("/tools/form/get-help/chat", json=CHAT)
    assert r.status_code == 200 and set(r.json()) == {"values", "reply", "blocked"}


def test_unknown_form_404(client):
    assert client.post("/tools/form/nope/open", json={}).status_code == 404
    assert client.post("/tools/form/nope/chat", json=CHAT).status_code == 404


def test_chat_needs_a_last_user_message(client):
    assert client.post("/tools/form/get-help/chat", json={**CHAT, "messages": []}).status_code == 422
    last_tool = [{"role": "user", "text": "hi"}, {"role": "tool", "text": "hello"}]
    assert client.post("/tools/form/get-help/chat", json={**CHAT, "messages": last_tool}).status_code == 422
    assert client.post("/tools/form/get-help/open", json={"history": [{"role": "tool", "content": "x"}]}).status_code == 422


def test_busy_is_503(client, monkeypatch):
    full = Admission(1, 0)
    full.busy = 1
    monkeypatch.setattr(api, "admission", full)
    r = client.post("/tools/form/get-help/chat", json=CHAT)
    assert r.status_code == 503 and r.json()["busy"] is True and "Retry-After" in r.headers
    assert client.get("/tools").status_code == 200  # only the LLM turns wait for a slot


def test_llm_error_is_json_502(client, llm):
    llm.raises = openai.APIError("down", httpx.Request("POST", "http://x"), body=None)
    r = client.post("/tools/form/get-help/chat", json=CHAT)
    assert r.status_code == 502 and "APIError" in r.json()["detail"]


def test_a_failed_turn_is_json_the_browser_can_read(client, llm, monkeypatch):
    # found live: the catch-all handler ran outside CORS, so the browser saw a CORS failure, not the error
    origin = {"Origin": "http://localhost:5173"}
    llm.raises = RuntimeError("boom")
    r = client.post("/tools/form/get-help/chat", json=CHAT, headers=origin)
    assert r.status_code == 500 and "RuntimeError: boom" in r.json()["detail"]
    assert "access-control-allow-origin" in r.headers

    def no_key():
        raise openai.OpenAIError("Missing credentials")
    monkeypatch.setattr(api, "get_llm", no_key)  # the LLM isn't set up: the provider side's failure
    r = client.post("/tools/form/get-help/open", json={}, headers=origin)
    assert r.status_code == 502 and "Missing credentials" in r.json()["detail"]
    assert "access-control-allow-origin" in r.headers


def test_cors_allows_the_browser(client):
    r = client.options("/tools", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] in ("*", "http://localhost:5173")
