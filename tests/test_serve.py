"""Tests for the SSE server module."""

import json
import threading
from http.client import HTTPConnection
from unittest.mock import patch

import pytest

from localsmartz.serve import LocalSmartzHandler
from http.server import HTTPServer


@pytest.fixture
def server():
    """Start test server on an ephemeral port."""
    srv = HTTPServer(("127.0.0.1", 0), LocalSmartzHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()


def _get(port: int, path: str) -> tuple[int, dict]:
    """Helper — make a GET request, return (status, json_body)."""
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    status = resp.status
    body = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return status, body


def _get_raw(port: int, path: str) -> tuple[int, str, str]:
    """Helper — make a GET request, return (status, content_type, body_text)."""
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    status = resp.status
    ctype = resp.getheader("Content-Type", "")
    body = resp.read().decode("utf-8")
    conn.close()
    return status, ctype, body


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    """Helper — make a POST request with JSON, return (status, json_body)."""
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(payload)
    conn.request(
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    status = resp.status
    response_body = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return status, response_body


def _delete(port: int, path: str, payload: dict) -> tuple[int, dict]:
    """Helper — make a DELETE request with JSON, return (status, json_body)."""
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(payload)
    conn.request(
        "DELETE",
        path,
        body=body,
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    status = resp.status
    response_body = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return status, response_body


# ── Web UI ──

def test_ui_serves_html(server):
    """GET / returns the web UI HTML page."""
    status, ctype, body = _get_raw(server, "/")
    assert status == 200
    assert "text/html" in ctype
    assert "<title>Local Smartz</title>" in body
    assert "id=\"output\"" in body


def test_ui_trailing_slash(server):
    """GET / with trailing slash also serves UI."""
    status, ctype, body = _get_raw(server, "/")
    assert status == 200
    assert "<title>Local Smartz</title>" in body


# ── Health endpoint ──

def test_health_returns_ok(server):
    """GET /api/health returns {ok: true, profile: ...}."""
    mock_profile = {"name": "lite", "planning_model": "qwen3:8b", "execution_model": "qwen3:8b"}
    with patch("localsmartz.profiles.get_profile", return_value=mock_profile):
        status, body = _get(server, "/api/health")
    assert status == 200
    assert body["ok"] is True
    assert body["profile"] == "lite"


# ── Status endpoint ──

def test_status_returns_profile(server):
    """GET /api/status returns profile and ollama state."""
    mock_profile = {"name": "lite", "planning_model": "qwen3:8b", "execution_model": "qwen3:8b"}
    with patch("localsmartz.profiles.get_profile", return_value=mock_profile), \
         patch("localsmartz.ollama.check_server", return_value=True), \
         patch("localsmartz.ollama.get_version", return_value="0.5.0"), \
         patch("localsmartz.ollama.list_models", return_value=["qwen3:8b"]), \
         patch("localsmartz.ollama.model_available", return_value=True):
        status, body = _get(server, "/api/status")
    assert status == 200
    assert body["profile"] == "lite"
    assert body["ready"] is True
    assert body["missing_models"] == []
    assert body["ollama"]["running"] is True
    assert body["ollama"]["version"] == "0.5.0"


def test_status_ollama_offline(server):
    """GET /api/status when Ollama is down."""
    mock_profile = {"name": "lite", "planning_model": "qwen3:8b", "execution_model": "qwen3:8b"}
    with patch("localsmartz.profiles.get_profile", return_value=mock_profile), \
         patch("localsmartz.ollama.check_server", return_value=False):
        status, body = _get(server, "/api/status")
    assert status == 200
    assert body["ready"] is False
    assert body["ollama"]["running"] is False
    assert body["ollama"]["version"] is None


# ── Threads endpoint ──

def test_threads_empty(server):
    """GET /api/threads returns empty list when no threads."""
    with patch("localsmartz.threads.list_threads", return_value=[]):
        status, body = _get(server, "/api/threads")
    assert status == 200
    assert body == []


def test_threads_with_data(server):
    """GET /api/threads returns thread list."""
    mock_threads = [
        {"id": "t1", "title": "AI trends", "entry_count": 3, "updated_at": 1773576000},
    ]
    with patch("localsmartz.threads.list_threads", return_value=mock_threads):
        status, body = _get(server, "/api/threads")
    assert status == 200
    assert len(body) == 1
    assert body[0]["id"] == "t1"
    assert body[0]["last_updated"].endswith("Z")


# ── Research endpoint ──

def test_research_missing_prompt(server):
    """GET /api/research without prompt returns 400."""
    status, body = _get(server, "/api/research")
    assert status == 400
    assert "error" in body


def test_research_post_missing_prompt(server):
    """POST /api/research without prompt returns 400."""
    status, body = _post(server, "/api/research", {})
    assert status == 400
    assert "error" in body


# ── 404 ──

def test_unknown_path(server):
    """Unknown paths return 404."""
    status, body = _get(server, "/api/nonexistent")
    assert status == 404


# ── CORS ──

def test_options_cors(server):
    """OPTIONS returns CORS headers."""
    conn = HTTPConnection("127.0.0.1", server, timeout=5)
    conn.request("OPTIONS", "/api/health")
    resp = conn.getresponse()
    assert resp.status == 204
    assert resp.getheader("Access-Control-Allow-Origin") == "*"
    conn.close()


def test_get_models(server):
    """GET /api/models returns model list with current and profile."""
    status, data = _get(server, "/api/models")
    assert status == 200
    assert "models" in data
    assert "current" in data
    assert "profile" in data


def test_post_model_select_invalid(server):
    """POST /api/models/select rejects unavailable models."""
    status, data = _post(server, "/api/models/select", {"model": "nonexistent-model-xyz"})
    assert status == 400


def test_get_folders(server):
    """GET /api/folders returns workspace and configured folders."""
    status, data = _get(server, "/api/folders")
    assert status == 200
    assert "workspace" in data
    assert isinstance(data["folders"], list)


def test_post_folder_add_nonexistent(server):
    """POST /api/folders rejects nonexistent paths."""
    status, data = _post(server, "/api/folders", {"path": "/nonexistent/path/xyz"})
    assert status == 400


def test_cors_includes_delete(server):
    """CORS OPTIONS response includes DELETE in allowed methods."""
    conn = HTTPConnection("127.0.0.1", server, timeout=5)
    conn.request("OPTIONS", "/api/folders")
    resp = conn.getresponse()
    methods = resp.getheader("Access-Control-Allow-Methods", "")
    conn.close()
    assert resp.status == 204
    assert "DELETE" in methods
