"""Tests that /api/status correctly reports Ollama running/offline state.

These guard the state contradiction fixed in this cycle: SetupView was
showing a green "Ollama Ready" row alongside a red error message because
the two reads (direct port check vs. temp-backend /api/status) could
disagree. The backend must be the single source of truth.
"""

import json
import threading
from http.client import HTTPConnection
from unittest.mock import patch

import pytest
from http.server import HTTPServer

from localsmartz.serve import LocalSmartzHandler


@pytest.fixture
def server():
    """Start a test server on an ephemeral port."""
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


_MOCK_PROFILE = {
    "name": "lite",
    "planning_model": "qwen3:8b",
    "execution_model": "qwen3:8b",
}


# ---------------------------------------------------------------------------
# Contract: when Ollama is running and the model is available, ready == True
# ---------------------------------------------------------------------------

def test_status_running_and_ready(server):
    """/api/status returns ready=True and ollama.running=True when Ollama is up."""
    with (
        patch("localsmartz.profiles.get_profile", return_value=_MOCK_PROFILE),
        patch("localsmartz.ollama.check_server", return_value=True),
        patch("localsmartz.ollama.get_version", return_value="0.21.0"),
        patch("localsmartz.ollama.list_models", return_value=["qwen3:8b"]),
        patch("localsmartz.ollama.model_available", return_value=True),
        patch("localsmartz.ollama.resolve_available_model", return_value=("qwen3:8b", None)),
    ):
        status, body = _get(server, "/api/status")

    assert status == 200
    assert body["ollama"]["running"] is True, "running must be True when Ollama responds"
    assert body["ready"] is True, "ready must be True when Ollama is up and model is installed"
    assert body["missing_models"] == []


# ---------------------------------------------------------------------------
# Contract: when Ollama is offline, ready == False and running == False
# (no contradiction between the two fields)
# ---------------------------------------------------------------------------

def test_status_offline_is_not_ready(server):
    """/api/status ready and ollama.running are both False when Ollama is down."""
    with (
        patch("localsmartz.profiles.get_profile", return_value=_MOCK_PROFILE),
        patch("localsmartz.ollama.check_server", return_value=False),
    ):
        status, body = _get(server, "/api/status")

    assert status == 200
    assert body["ollama"]["running"] is False
    assert body["ready"] is False, (
        "ready must be False when Ollama is offline — "
        "a True ready with False running would produce the state contradiction seen in SetupView"
    )


# ---------------------------------------------------------------------------
# Contract: when Ollama is running but the required model is missing,
# ollama.running == True but ready == False. The model appears in missing_models.
# ---------------------------------------------------------------------------

def test_status_running_model_missing(server):
    """/api/status: Ollama running but model not installed — running=True, ready=False."""
    with (
        patch("localsmartz.profiles.get_profile", return_value=_MOCK_PROFILE),
        patch("localsmartz.ollama.check_server", return_value=True),
        patch("localsmartz.ollama.get_version", return_value="0.21.0"),
        patch("localsmartz.ollama.list_models", return_value=[]),
        patch("localsmartz.ollama.model_available", return_value=False),
        patch(
            "localsmartz.ollama.resolve_available_model",
            return_value=(None, "Model not found"),
        ),
    ):
        status, body = _get(server, "/api/status")

    assert status == 200
    assert body["ollama"]["running"] is True, (
        "ollama.running must be True when Ollama itself is up, "
        "even if the required model is missing"
    )
    assert body["ready"] is False
    assert "qwen3:8b" in body["missing_models"]


# ---------------------------------------------------------------------------
# Contract: response shape always includes the fields SetupSSEClient.Status
# relies on (ready, missing_models, ram_gb). Missing fields would produce a
# Swift decode error that shows the wrong error message in SetupView.
# ---------------------------------------------------------------------------

def test_status_shape_for_swift_decoder(server):
    """/api/status always includes ready, missing_models, and ram_gb."""
    with (
        patch("localsmartz.profiles.get_profile", return_value=_MOCK_PROFILE),
        patch("localsmartz.ollama.check_server", return_value=True),
        patch("localsmartz.ollama.get_version", return_value="0.21.0"),
        patch("localsmartz.ollama.list_models", return_value=["qwen3:8b"]),
        patch("localsmartz.ollama.model_available", return_value=True),
        patch("localsmartz.ollama.resolve_available_model", return_value=("qwen3:8b", None)),
    ):
        status, body = _get(server, "/api/status")

    assert status == 200
    assert "ready" in body, "missing 'ready' breaks SetupSSEClient.Status decode"
    assert "missing_models" in body, "missing 'missing_models' breaks SetupSSEClient.Status decode"
    assert "ram_gb" in body, "missing 'ram_gb' breaks SetupSSEClient.Status decode"
    assert isinstance(body["missing_models"], list)
