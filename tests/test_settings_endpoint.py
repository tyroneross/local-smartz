"""GET/POST /api/settings round-trip + validation, POST
/api/agents/<name>/enabled, and the /api/status + /api/agents surface
changes (active_model, local_only, effective_model, enabled flags)."""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from localsmartz import global_config


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture
def server():
    from http.server import HTTPServer
    from localsmartz.serve import LocalSmartzHandler

    srv = HTTPServer(("127.0.0.1", 0), LocalSmartzHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()


def _get(port: int, path: str) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    status = resp.status
    body = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return status, body


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(
        "POST", path, body=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    status = resp.status
    body = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return status, body


# ── GET /api/settings ──────────────────────────────────────────────────────

def test_get_settings_defaults(server):
    status, body = _get(server, "/api/settings")
    assert status == 200
    assert body["local_only"] is False
    assert body["active_model"] == ""
    assert body["disabled_agents"] == []
    assert "profile" in body


def test_get_settings_reflects_persisted_state(server):
    global_config.save_global({"local_only": True, "disabled_agents": ["analyzer"]})
    status, body = _get(server, "/api/settings")
    assert status == 200
    assert body["local_only"] is True
    assert body["disabled_agents"] == ["analyzer"]


# ── POST /api/settings round-trip ─────────────────────────────────────────

def test_post_settings_local_only_round_trip(server):
    status, body = _post(server, "/api/settings", {"local_only": True})
    assert status == 200
    assert body["local_only"] is True
    assert global_config.get("local_only") is True


def test_post_settings_disabled_agents_round_trip(server):
    status, body = _post(server, "/api/settings", {"disabled_agents": ["analyzer", "planner"]})
    assert status == 200
    assert sorted(body["disabled_agents"]) == ["analyzer", "planner"]


def test_post_settings_active_model_empty_clears(server):
    global_config.set("active_model", "old:model")
    status, body = _post(server, "/api/settings", {"active_model": ""})
    assert status == 200
    assert body["active_model"] == ""


def test_post_settings_active_model_uninstalled_400(server, monkeypatch):
    monkeypatch.setattr("localsmartz.ollama.model_available", lambda name: False)
    status, body = _post(server, "/api/settings", {"active_model": "not-installed:tag"})
    assert status == 400
    assert "error" in body
    assert global_config.get("active_model") == ""


def test_post_settings_active_model_installed_ok(server, monkeypatch):
    monkeypatch.setattr("localsmartz.ollama.model_available", lambda name: True)
    status, body = _post(server, "/api/settings", {"active_model": "installed:tag"})
    assert status == 200
    assert body["active_model"] == "installed:tag"


def test_post_settings_disabled_agents_orchestrator_rejected_400(server):
    status, body = _post(server, "/api/settings", {"disabled_agents": ["orchestrator"]})
    assert status == 400
    assert "orchestrator" in body["error"]
    assert global_config.get("disabled_agents") == []


def test_post_settings_disabled_agents_all_disabled_400(server):
    from localsmartz.profiles import RUNNABLE_ROLES

    status, body = _post(server, "/api/settings", {"disabled_agents": list(RUNNABLE_ROLES)})
    assert status == 400
    assert global_config.get("disabled_agents") == []


def test_post_settings_disabled_agents_unknown_role_400(server):
    status, body = _post(server, "/api/settings", {"disabled_agents": ["ghost"]})
    assert status == 400


def test_post_settings_empty_body_400(server):
    status, body = _post(server, "/api/settings", {})
    assert status == 400


def test_post_settings_wrong_type_400(server):
    status, body = _post(server, "/api/settings", {"local_only": "yes"})
    assert status == 400
    status, body = _post(server, "/api/settings", {"disabled_agents": "analyzer"})
    assert status == 400
    status, body = _post(server, "/api/settings", {"active_model": 123})
    assert status == 400


def test_post_settings_multi_key_partial_failure_persists_nothing(server, monkeypatch):
    """All-or-nothing: if ANY key fails validation, no key from this
    request is persisted."""
    monkeypatch.setattr("localsmartz.ollama.model_available", lambda name: False)
    status, body = _post(
        server, "/api/settings",
        {"local_only": True, "active_model": "bogus:tag"},
    )
    assert status == 400
    assert global_config.get("local_only") is False


# ── POST /api/agents/<name>/enabled ───────────────────────────────────────

def test_agent_enabled_disable_round_trip(server):
    status, body = _post(server, "/api/agents/analyzer/enabled", {"enabled": False})
    assert status == 200
    assert body == {"ok": True, "agent": "analyzer", "enabled": False}
    assert "analyzer" in global_config.get("disabled_agents")

    status, body = _post(server, "/api/agents/analyzer/enabled", {"enabled": True})
    assert status == 200
    assert "analyzer" not in global_config.get("disabled_agents")


def test_agent_enabled_unknown_agent_400(server):
    status, body = _post(server, "/api/agents/ghost/enabled", {"enabled": False})
    assert status == 400
    assert "error" in body


def test_agent_enabled_orchestrator_rejected_400(server):
    status, body = _post(server, "/api/agents/orchestrator/enabled", {"enabled": False})
    assert status == 400


def test_agent_enabled_wrong_type_400(server):
    status, body = _post(server, "/api/agents/analyzer/enabled", {"enabled": "no"})
    assert status == 400


def test_agent_enabled_all_disabled_guard_400(server):
    from localsmartz.profiles import RUNNABLE_ROLES

    global_config.set("disabled_agents", [r for r in RUNNABLE_ROLES if r != "writer"])
    status, body = _post(server, "/api/agents/writer/enabled", {"enabled": False})
    assert status == 400


# ── GET /api/agents gains "enabled" ────────────────────────────────────────

def test_get_agents_includes_enabled_flag(server):
    global_config.set("disabled_agents", ["analyzer"])
    status, body = _get(server, "/api/agents")
    assert status == 200
    by_name = {a["name"]: a for a in body["agents"]}
    assert by_name["analyzer"]["enabled"] is False
    assert by_name["writer"]["enabled"] is True


# ── GET /api/status gains active_model + local_only ───────────────────────

def test_status_includes_active_model_and_local_only(server, monkeypatch):
    mock_profile = {"name": "lite", "planning_model": "qwen3:8b", "execution_model": "qwen3:8b"}
    global_config.set("active_model", "pinned:model")
    global_config.set("local_only", True)
    monkeypatch.setattr("localsmartz.profiles.get_profile", lambda *a, **k: mock_profile)
    monkeypatch.setattr("localsmartz.ollama.check_server", lambda: True)
    monkeypatch.setattr("localsmartz.ollama.get_version", lambda: "0.5.0")
    monkeypatch.setattr("localsmartz.ollama.list_models", lambda: ["qwen3:8b"])
    monkeypatch.setattr("localsmartz.ollama.model_available", lambda m: True)
    status, body = _get(server, "/api/status")
    assert status == 200
    assert body["active_model"] == "pinned:model"
    assert body["local_only"] is True
    assert body["effective_model"] == "pinned:model"
