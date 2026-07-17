"""SEC-003(b): POST /api/secrets must still persist cloud-LLM + LangSmith
presets to the Keychain/file store while local_only is on, but must NOT
write them into this process's environment (a live key in os.environ can be
auto-discovered by any code path, bypassing the local_only boundary)."""
from __future__ import annotations

import json
import os
import threading
from http.client import HTTPConnection

import pytest

from localsmartz import global_config


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Force the file-fallback secrets store (no real Keychain in CI).
    monkeypatch.setattr("localsmartz.secrets._keyring", lambda: None)
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
    srv.server_close()
    thread.join(timeout=2.0)


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


def test_secrets_set_openai_skips_env_write_under_local_only(server, monkeypatch):
    global_config.save_global({"local_only": True})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status, body = _post(server, "/api/secrets", {"provider": "OpenAI", "value": "sk-new-key-1234"})

    assert status == 200
    assert body["ok"] is True
    # Still persisted to the store.
    from localsmartz import secrets as _secrets
    assert _secrets.get("OpenAI") == "sk-new-key-1234"
    # But NOT written into this process's environment.
    assert os.environ.get("OPENAI_API_KEY") is None


def test_secrets_set_langsmith_skips_env_write_under_local_only(server, monkeypatch):
    global_config.save_global({"local_only": True})
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    status, body = _post(server, "/api/secrets", {"provider": "LangSmith", "value": "ls-new-key-5678"})

    assert status == 200
    from localsmartz import secrets as _secrets
    assert _secrets.get("LangSmith") == "ls-new-key-5678"
    assert os.environ.get("LANGSMITH_API_KEY") is None


def test_secrets_set_still_writes_env_when_local_only_off(server, monkeypatch):
    """Regression: default (local_only off) behavior is unchanged."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status, body = _post(server, "/api/secrets", {"provider": "OpenAI", "value": "sk-new-key-9999"})
    assert status == 200
    assert os.environ.get("OPENAI_API_KEY") == "sk-new-key-9999"


def test_secrets_set_unrelated_provider_still_writes_env_under_local_only(server, monkeypatch):
    """A non-cloud-LLM preset (e.g. Tavily) is unaffected by local_only —
    it isn't part of the privacy boundary."""
    global_config.save_global({"local_only": True})
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    status, body = _post(server, "/api/secrets", {"provider": "Tavily", "value": "tv-new-key-4321"})
    assert status == 200
    assert os.environ.get("TAVILY_API_KEY") == "tv-new-key-4321"
