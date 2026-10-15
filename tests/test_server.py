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


def test_healthz_reports_the_build_commit(monkeypatch, data_dir):
    """A rollout is visible from outside: /healthz names the commit the running image was built from."""
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.setenv("KB_PATH", str(data_dir / "knowledge_base.json"))
    monkeypatch.setenv("MOCK_WEB_PATH", str(data_dir / "mock_web_index.json"))
    monkeypatch.setenv("GIT_COMMIT", "sha-abc1234")
    from podcast_agent import server

    server._shared.cache_clear()
    server._commit.cache_clear()
    body = TestClient(server.app).get("/healthz").json()
    server._commit.cache_clear()
    assert body["status"] == "ok" and body["commit"] == "sha-abc1234"
