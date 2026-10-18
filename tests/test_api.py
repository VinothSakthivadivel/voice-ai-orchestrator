import json

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import frames, silence, voice


def test_health_and_listing():
    c = TestClient(app)
    assert c.get("/health").json()["status"] == "ok"
    assert len(c.get("/api/providers").json()["providers"]) >= 24
    langs = c.get("/api/languages").json()["languages"]
    assert any(l["code"] == "sat-IN" for l in langs)
    assert c.get("/").status_code == 200
    assert c.get("/static/app.js").status_code == 200


def test_bot_crud_strips_keys():
    c = TestClient(app)
    r = c.post("/api/bots", json={"name": "T", "language": "ta-IN", "stt": {"provider": "azure",
                                                                              "options": {"api_key": "leak"}}})
    assert r.status_code == 200
    bot = r.json()
    assert "api_key" not in json.dumps(bot) and bot["llm"]["max_tokens"] == 300
    assert c.get(f"/api/bots/{bot['id']}").json()["name"] == "T"
    assert c.post("/api/bots", json={"name": "bad", "llm": {"provider": "nope"}}).status_code == 400
    assert c.delete(f"/api/bots/{bot['id']}").json()["deleted"]


def test_access_token(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_TOKEN", "s3cret")
    c = TestClient(app)
    assert c.get("/api/bots").status_code == 401
    assert c.get("/api/bots", headers={"X-Access-Token": "s3cret"}).status_code == 200
    assert c.get("/health").status_code == 200
    with c.websocket_connect("/ws/call?token=bad") as ws:
        assert ws.receive_json()["layer"] == "auth"


def test_test_provider_endpoint_mock_and_missing_key(monkeypatch):
    c = TestClient(app)
    r = c.post("/api/test-provider", json={"layer": "tts", "provider": "mock", "language": "hi-IN"}).json()
    assert r["ok"] and r["audio_b64"]
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    r = c.post("/api/test-provider", json={"layer": "stt", "provider": "deepgram"}).json()
    assert not r["ok"] and "DEEPGRAM_API_KEY" in r["error"]


def test_websocket_call_with_mock_providers():
    c = TestClient(app)
    with c.websocket_connect("/ws/call") as ws:
        ws.send_json({"type": "start", "profile_id": "demo-mock", "keys": {"openai": {"api_key": "sk-test"}}})
        got, audio = [], 0
        for f in frames(silence(0.4) + voice(1.0) + silence(0.9)):
            ws.send_bytes(f)
        ws.send_json({"type": "text", "text": "hello there"})
        for _ in range(400):
            m = ws.receive()
            if m.get("bytes") is not None:
                audio += 1
                continue
            j = json.loads(m["text"])
            got.append(j["type"])
            if j["type"] == "metrics" and got.count("metrics") >= 1 and audio:
                break
        assert "ready" in got and "transcript" in got and "llm_delta" in got and audio > 0
        ws.send_json({"type": "stop"})


def test_log_redaction_keeps_access_log_args(monkeypatch):
    import logging
    from app.security import RedactingFilter, redact

    monkeypatch.setenv("OPENAI_API_KEY", "sk-abcdefghijklmnopqrstuvwxyz")
    rec = logging.LogRecord("uvicorn.access", 20, "x", 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4", "GET", "/?key=AIzaSyA1234567890abcdefghijkl", "1.1", 200), None)
    RedactingFilter().filter(rec)
    msg = rec.getMessage()
    assert "AIzaSy" not in msg and "200" in msg
    assert "sk-abc" not in redact("token sk-abcdefghijklmnopqrstuvwxyz used")
