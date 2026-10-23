import pytest
from fastapi.testclient import TestClient

from agent import hardware
from agent.scheduler import make_trigger


def test_trigger_validation():
    make_trigger("cron", "0 9 * * 1-5")
    make_trigger("interval", "30")
    make_trigger("once", "2099-01-01T10:00")
    for t, v in [("cron", "bad"), ("interval", "0"), ("once", "2000-01-01T00:00"), ("weekly", "x")]:
        with pytest.raises(ValueError):
            make_trigger(t, v)


def test_recommendation_tiers():
    cpu8 = {"ram_gb": 8, "gpus": [], "apple_silicon": False}
    cpu16 = {"ram_gb": 16, "gpus": [], "apple_silicon": False}
    gpu12 = {"ram_gb": 32, "gpus": [{"name": "RTX", "vram_gb": 12}], "apple_silicon": False}
    gpu24 = {"ram_gb": 64, "gpus": [{"name": "RTX", "vram_gb": 24}], "apple_silicon": False}
    assert hardware.recommend(cpu8)["model"] == "qwen3:4b"
    assert hardware.recommend(cpu16)["model"] == "qwen3:8b"
    assert hardware.recommend(gpu12)["model"] == "qwen3:14b"
    assert hardware.recommend(gpu24)["model"] == "qwen3:30b"
    assert hardware.pick_installed(["llama3.1:8b", "qwen3:8b"]) == "qwen3:8b"


@pytest.fixture
def client(cfg, fake_llm, tmp_path, monkeypatch):
    import agent.server as srv
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path / "data")
    llm = fake_llm([[("list_dir", {"path": "."})], [("write_file", {"path": "w.txt", "content": "hey"})], "All done"])
    app = srv.create_app(cfg, llm)
    with TestClient(app, base_url="http://127.0.0.1:7860") as c:
        yield c


def test_status_and_schedules(client):
    s = client.get("/api/status").json()
    assert s["llm_online"] and s["mode"] == "careful" and "run_shell" in s["tools"]
    r = client.post("/api/schedules", json={"name": "news", "prompt": "get news", "trigger_type": "cron",
                                            "trigger_value": "0 9 * * *"})
    assert r.status_code == 200 and r.json()["next_run"]
    assert client.post("/api/schedules", json={"name": "x", "prompt": "y", "trigger_type": "cron",
                                               "trigger_value": "nope"}).status_code == 400
    jid = r.json()["id"]
    assert client.post(f"/api/schedules/{jid}/toggle").json()["enabled"] is False
    assert client.delete(f"/api/schedules/{jid}").status_code == 200


def test_mode_and_cross_origin_block(client):
    assert client.post("/api/mode", json={"mode": "trusted"}).json()["mode"] == "trusted"
    assert client.post("/api/mode", json={"mode": "nope"}).status_code == 400
    evil = client.post("/api/mode", json={"mode": "trusted"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 403


def test_websocket_chat_with_approval(client, cfg):
    with client.websocket_connect("/ws", headers={"Host": "127.0.0.1:7860"}) as ws:
        ws.send_json({"type": "hello", "session": "s1"})
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "chat", "text": "list then write"})
        seen = []
        while True:
            e = ws.receive_json()
            seen.append(e["type"])
            if e["type"] == "approval_request":
                assert e["name"] == "write_file"
                ws.send_json({"type": "approval", "id": e["id"], "decision": "allow"})
            if e["type"] == "done":
                break
        assert "assistant" in seen and "approval_request" in seen
    from pathlib import Path
    assert (Path(cfg["agent"]["workspace"]) / "w.txt").read_text() == "hey"


def test_websocket_rejects_foreign_origin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"Host": "127.0.0.1:7860", "Origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_autofix_in_chat_and_fix_endpoint(cfg, fake_llm, tmp_path, monkeypatch):
    import agent.server as srv
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path / "data2")
    cfg["agent"]["autofix_typos"] = True
    llm = fake_llm(["install git please", "Done.", "list my files"])
    app = srv.create_app(cfg, llm, start_scheduler=False)
    with TestClient(app, base_url="http://127.0.0.1:7860") as c:
        with c.websocket_connect("/ws", headers={"Host": "127.0.0.1:7860"}) as ws:
            ws.send_json({"type": "hello", "session": "fx"})
            ws.receive_json()
            ws.send_json({"type": "chat", "text": "isntall gti plz"})
            events = []
            while True:
                e = ws.receive_json()
                events.append(e)
                if e["type"] == "done":
                    break
        fixed = [e for e in events if e["type"] == "fixed"]
        assert fixed and fixed[0]["text"] == "install git please"
        r = c.post("/api/fix_text", json={"text": "lsit my fiels"}).json()
        assert r == {"text": "list my files", "changed": True, "options": []}
        assert c.post("/api/settings", json={"autofix_typos": False}).json()["autofix_typos"] is False


def test_typo_clarify_question_over_websocket(cfg, fake_llm, tmp_path, monkeypatch):
    import json
    import agent.server as srv
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path / "data3")
    cfg["agent"]["autofix_typos"] = True
    fix = json.dumps({"text": "move the video", "unsure": True,
                      "options": ["move the video", "move the .mov file"]})
    llm = fake_llm([fix, "Moved it."])
    app = srv.create_app(cfg, llm, start_scheduler=False)
    with TestClient(app, base_url="http://127.0.0.1:7860") as c:
        with c.websocket_connect("/ws", headers={"Host": "127.0.0.1:7860"}) as ws:
            ws.send_json({"type": "hello", "session": "cq"})
            ws.receive_json()
            ws.send_json({"type": "chat", "text": "mov the vid"})
            events = []
            while True:
                e = ws.receive_json()
                events.append(e)
                if e["type"] == "clarify":
                    assert e["options"] == ["move the video", "move the .mov file"] and e["original"] == "mov the vid"
                    ws.send_json({"type": "answer", "id": e["id"], "text": "move the .mov file"})
                if e["type"] == "done":
                    break
    st = app.state.lp.sessions["cq"]
    assert st.messages[0]["content"] == "move the .mov file"
    assert [e for e in events if e["type"] == "clarify_resolved"][0]["answer"] == "move the .mov file"
