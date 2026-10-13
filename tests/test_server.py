import json

from fastapi.testclient import TestClient


def test_process_endpoint_offline(monkeypatch, data_dir):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.setenv("KB_PATH", str(data_dir / "knowledge_base.json"))
    monkeypatch.setenv("MOCK_WEB_PATH", str(data_dir / "mock_web_index.json"))
    from podcast_agent import server

    server._shared.cache_clear()
    client = TestClient(server.app)
    assert client.get("/healthz").json()["kb_entries"] > 10
    payload = json.loads((data_dir / "transcripts" / "ep002_ai_healthcare.json").read_text())
    r = client.post("/v1/process", json={"transcript": payload})
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["episode"]["episode_id"] == "ep002"
    assert "## Fact-check" in body["markdown"]
    assert client.post("/v1/process", json={}).status_code == 422
