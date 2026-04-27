"""Smoke tests for the new serve endpoints: patterns, cloud estimate, preflight."""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import HTTPServer

import pytest

from localsmartz.serve import LocalSmartzHandler


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), LocalSmartzHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()


def _get_json(port: int, path: str) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    status = resp.status
    body = resp.read().decode("utf-8")
    conn.close()
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, {"_raw": body}


def _post_json(port: int, path: str, payload: dict) -> tuple[int, dict]:
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
    response_body = resp.read().decode("utf-8")
    conn.close()
    try:
        return status, json.loads(response_body)
    except json.JSONDecodeError:
        return status, {"_raw": response_body}


def test_api_patterns_returns_all_seven(server: int) -> None:
    status, data = _get_json(server, "/api/patterns")
    assert status == 200
    names = {p["name"] for p in data["patterns"]}
    assert names == {
        "single", "chain", "router", "critic_loop",
        "reflection", "orchestrator", "parallel",
    }


def test_api_cloud_estimate_known_model(server: int) -> None:
    status, data = _post_json(
        server,
        "/api/cloud/estimate",
        {"model": "claude-sonnet-4-6", "prompt": "x" * 4000, "pattern": "single"},
    )
    assert status == 200
    assert data["rate_known"] is True
    assert data["estimated_usd"] > 0


def test_api_cloud_estimate_requires_model(server: int) -> None:
    status, _ = _post_json(server, "/api/cloud/estimate", {"prompt": "hi"})
    assert status == 400


def test_api_cloud_estimate_unknown_model_returns_rate_unknown(server: int) -> None:
    status, data = _post_json(
        server,
        "/api/cloud/estimate",
        {"model": "definitely-not-a-real-model", "prompt": "hi"},
    )
    assert status == 200
    assert data["rate_known"] is False


def test_api_pattern_preflight_surfaces_missing_models(server: int, monkeypatch) -> None:
    # Force the ollama model_available probe to say False for all.
    monkeypatch.setattr("localsmartz.models.preflight.model_available", lambda name: False)
    status, data = _get_json(server, "/api/patterns/critic_loop/preflight")
    assert status == 200
    assert data["pattern"] == "critic_loop"
    assert data["ok"] is False
    assert any(m.get("role") == "writer" for m in data["missing"])
