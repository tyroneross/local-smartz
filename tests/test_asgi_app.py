"""Tests for the ASGI bridge (``localsmartz.asgi_app``).

Uses Starlette's ``TestClient`` so the suite runs without booting uvicorn.
Coverage focus:
  - Route table is correct (health, status, agents respond; unknown 404s)
  - The SSE research endpoint reports the "Ollama not running" error when
    no Ollama is available (no mocking of the underlying agent — we just
    verify the preflight path surfaces a clean SSE error event).

The heavy streaming happy-path lives behind a live Ollama and is exercised
manually per the build-loop validation gate; reproducing it here would
require mocking the entire agent graph which defeats the point.
"""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from localsmartz.asgi_app import build_app


@pytest.fixture
def client():
    app = build_app()
    with TestClient(app) as c:
        yield c


def test_health_endpoint_delegates(client: TestClient) -> None:
    """/api/health should return the stdlib handler's payload without error."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    # The stdlib handler responds with {"ok": True, ...}
    assert data.get("ok") is True


def test_unknown_endpoint_404(client: TestClient) -> None:
    # Starlette's catch-all OPTIONS route swallows "any path", so an
    # unknown GET surfaces as 405 (Method Not Allowed). Either response
    # is correct from the Swift client's perspective — what matters is
    # that the request didn't reach a real handler.
    resp = client.get("/api/does-not-exist")
    assert resp.status_code in (404, 405)


def test_options_cors(client: TestClient) -> None:
    """OPTIONS preflight should return 204 with CORS headers — required
    for the Swift client's JSON POSTs to succeed."""
    resp = client.request("OPTIONS", "/api/research")
    assert resp.status_code == 204
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_research_rejects_empty_prompt(client: TestClient) -> None:
    """Empty prompt → 400 before we even touch Ollama. Guards against a
    reverse-proxy calling /api/research with no body and the server
    hanging on the preflight."""
    resp = client.post("/api/research", json={"prompt": ""})
    assert resp.status_code == 400
    assert "prompt" in resp.json().get("error", "").lower()


def test_research_surfaces_ollama_down_as_sse_error() -> None:
    """When Ollama isn't reachable, the research endpoint should emit a
    single SSE error event and close cleanly — no process-level exception."""
    app = build_app()
    with TestClient(app) as c:
        with patch("localsmartz.ollama.check_server", return_value=False):
            with c.stream(
                "POST",
                "/api/research",
                json={"prompt": "hello"},
            ) as r:
                assert r.status_code == 200
                assert "text/event-stream" in r.headers.get("content-type", "")
                body = b"".join(r.iter_bytes())
        text = body.decode("utf-8")
        assert "\"type\": \"error\"" in text
        assert "Ollama not running" in text


def test_route_table_covers_expected_endpoints() -> None:
    """Contract smoke test: the ASGI app must serve every endpoint the
    stdlib handler does, so the Swift client can switch transports
    transparently. If we drop one here, a user-facing feature regresses
    silently."""
    app = build_app()
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    # Sample the must-have endpoints.
    expected = {
        "/api/health",
        "/api/status",
        "/api/research",
        "/api/threads",
        "/api/models",
        "/api/models/catalog",
        "/api/models/select",
        "/api/models/warmup",
        "/api/agents",
        "/api/agents/{name}/model",
        "/api/setup",
        "/api/folders",
        "/api/secrets",
        "/api/logs",
    }
    missing = expected - paths
    assert not missing, f"ASGI app missing routes: {missing}"
