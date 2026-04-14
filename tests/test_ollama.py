"""Tests for Ollama health check module."""

import json
from unittest.mock import patch, MagicMock

import httpx
import pytest

from localsmartz.ollama import (
    check_server,
    delete_model,
    is_installed,
    list_models_with_size,
    list_running_models,
    model_available,
    show_model,
    stream_pull,
    validate_for_profile,
)
from localsmartz.profiles import get_profile


def test_check_server_down():
    """When Ollama isn't running, check_server returns False."""
    import httpx
    with patch("localsmartz.ollama.httpx.get", side_effect=httpx.ConnectError("refused")):
        assert check_server() is False


def test_check_server_up():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("localsmartz.ollama.httpx.get", return_value=mock_resp):
        assert check_server() is True


def test_is_installed():
    with patch("localsmartz.ollama.shutil.which", return_value="/usr/local/bin/ollama"):
        assert is_installed() is True

    with patch("localsmartz.ollama.shutil.which", return_value=None):
        assert is_installed() is False


def test_model_available_exact():
    with patch("localsmartz.ollama.list_models", return_value=["qwen3:8b-q4_K_M", "llama3.1:70b"]):
        assert model_available("qwen3:8b-q4_K_M") is True
        assert model_available("nonexistent:latest") is False


def test_model_available_partial():
    with patch("localsmartz.ollama.list_models", return_value=["qwen3:8b"]):
        # Should match base+variant: qwen3 + 8b
        assert model_available("qwen3:8b-q4_K_M") is True


def test_validate_not_installed():
    with patch("localsmartz.ollama.is_installed", return_value=False):
        profile = get_profile("lite")
        ok, messages = validate_for_profile(profile)
        assert ok is False
        assert any("not installed" in m for m in messages)


def test_validate_not_running():
    with patch("localsmartz.ollama.is_installed", return_value=True), \
         patch("localsmartz.ollama.check_server", return_value=False):
        profile = get_profile("lite")
        ok, messages = validate_for_profile(profile)
        assert ok is False
        assert any("not running" in m for m in messages)


def test_validate_ready():
    with patch("localsmartz.ollama.is_installed", return_value=True), \
         patch("localsmartz.ollama.check_server", return_value=True), \
         patch("localsmartz.ollama.get_version", return_value="0.6.1"), \
         patch("localsmartz.ollama.model_available", return_value=True):
        profile = get_profile("lite")
        ok, messages = validate_for_profile(profile)
        assert ok is True
        assert any("ready" in m for m in messages)


def test_list_models_with_size():
    """Returns (name, size_gb) tuples sorted by size ascending."""
    mock_data = {
        "models": [
            {"name": "big:70b", "size": 40_000_000_000},
            {"name": "small:8b", "size": 5_000_000_000},
            {"name": "mid:20b", "size": 14_000_000_000},
        ]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_data
    mock_resp.raise_for_status = MagicMock()

    with patch("localsmartz.ollama.httpx.get", return_value=mock_resp):
        result = list_models_with_size()
        assert len(result) == 3
        assert result[0][0] == "small:8b"
        assert result[2][0] == "big:70b"
        assert abs(result[0][1] - 5.0) < 0.1


def test_list_models_with_size_empty():
    """Returns empty list when no models."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": []}
    mock_resp.raise_for_status = MagicMock()

    with patch("localsmartz.ollama.httpx.get", return_value=mock_resp):
        assert list_models_with_size() == []


def test_list_models_with_size_error():
    """Returns empty list on connection error."""
    with patch("localsmartz.ollama.httpx.get", side_effect=httpx.ConnectError("refused")):
        assert list_models_with_size() == []


# ── /api/pull — stream_pull ──────────────────────────────────────────────


class _FakeStreamResponse:
    """Context manager mimicking the object httpx.Client.stream() yields."""

    def __init__(self, status_code: int, lines: list[str] | None = None, body: str = ""):
        self.status_code = status_code
        self._lines = lines or []
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        for line in self._lines:
            yield line

    def read(self):
        return self._body.encode("utf-8")


class _FakeClient:
    """Mimics httpx.Client well enough to drive stream_pull in tests."""

    def __init__(self, response: _FakeStreamResponse):
        self._response = response
        self.last_request: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, **kwargs):
        self.last_request = {"method": method, "url": url, **kwargs}
        return self._response


def test_stream_pull_yields_progress_chunks():
    """stream_pull parses each NDJSON line and yields the dicts in order,
    and sends the documented request body."""
    lines = [
        json.dumps({"status": "pulling manifest"}),
        json.dumps({
            "status": "downloading",
            "digest": "sha256:abc",
            "total": 1000,
            "completed": 250,
        }),
        json.dumps({"status": "success"}),
    ]
    fake_resp = _FakeStreamResponse(status_code=200, lines=lines)
    fake_client = _FakeClient(fake_resp)
    with patch("localsmartz.ollama.httpx.Client", return_value=fake_client):
        chunks = list(stream_pull("qwen3:8b-q4_K_M"))

    assert len(chunks) == 3
    assert chunks[0] == {"status": "pulling manifest"}
    assert chunks[1]["digest"] == "sha256:abc"
    assert chunks[1]["completed"] == 250
    assert chunks[2] == {"status": "success"}

    # Request body contract: model + stream=True + insecure default False
    req = fake_client.last_request
    assert req["method"] == "POST"
    assert req["url"].endswith("/api/pull")
    assert req["json"] == {
        "model": "qwen3:8b-q4_K_M",
        "stream": True,
        "insecure": False,
    }


def test_stream_pull_propagates_error_status():
    """A 4xx/5xx from /api/pull raises RuntimeError with the status code."""
    fake_resp = _FakeStreamResponse(status_code=404, body="model not found")
    fake_client = _FakeClient(fake_resp)
    with patch("localsmartz.ollama.httpx.Client", return_value=fake_client):
        with pytest.raises(RuntimeError) as excinfo:
            list(stream_pull("phantom:latest"))
    assert "404" in str(excinfo.value)


def test_stream_pull_skips_malformed_lines():
    """Stray non-JSON lines shouldn't abort an in-flight pull."""
    lines = [
        "not json at all",
        json.dumps({"status": "downloading", "completed": 1}),
        "",  # blank, filtered by iter_lines loop
        json.dumps({"status": "success"}),
    ]
    fake_resp = _FakeStreamResponse(status_code=200, lines=lines)
    fake_client = _FakeClient(fake_resp)
    with patch("localsmartz.ollama.httpx.Client", return_value=fake_client):
        chunks = list(stream_pull("x:y"))
    assert [c["status"] for c in chunks] == ["downloading", "success"]


def test_stream_pull_passes_insecure_flag():
    fake_resp = _FakeStreamResponse(status_code=200, lines=[])
    fake_client = _FakeClient(fake_resp)
    with patch("localsmartz.ollama.httpx.Client", return_value=fake_client):
        list(stream_pull("m:t", insecure=True))
    assert fake_client.last_request["json"]["insecure"] is True


# ── /api/ps — list_running_models ────────────────────────────────────────


def test_list_running_models_returns_empty_on_down():
    """ConnectError → []; never raises."""
    with patch("localsmartz.ollama.httpx.get", side_effect=httpx.ConnectError("refused")):
        assert list_running_models() == []


def test_list_running_models_parses_response():
    """/api/ps with two resident models → two dicts preserving key fields."""
    payload = {
        "models": [
            {
                "name": "gemma3",
                "model": "gemma3",
                "size": 6_591_830_464,
                "digest": "sha256:aaa",
                "details": {
                    "format": "gguf",
                    "family": "gemma3",
                    "parameter_size": "4.3B",
                    "quantization_level": "Q4_K_M",
                },
                "expires_at": "2025-10-17T16:47:07.93355-07:00",
                "size_vram": 5_333_539_264,
                "context_length": 4096,
            },
            {
                "name": "qwen3:8b-q4_K_M",
                "model": "qwen3:8b-q4_K_M",
                "size": 5_200_000_000,
                "size_vram": 4_800_000_000,
                "expires_at": "2025-10-17T17:00:00-07:00",
                "context_length": 8192,
                "details": {"parameter_size": "8B", "quantization_level": "Q4_K_M"},
            },
        ]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    with patch("localsmartz.ollama.httpx.get", return_value=mock_resp):
        result = list_running_models()

    assert len(result) == 2
    assert result[0]["name"] == "gemma3"
    assert result[0]["size_vram"] == 5_333_539_264
    assert result[1]["context_length"] == 8192


def test_list_running_models_handles_unexpected_shape():
    """If the API ever returns something unexpected, fall back to []."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": "whoops"}
    mock_resp.raise_for_status = MagicMock()
    with patch("localsmartz.ollama.httpx.get", return_value=mock_resp):
        assert list_running_models() == []


# ── /api/show — show_model ───────────────────────────────────────────────


def test_show_model_returns_dict():
    """A 200 response is returned intact, including nested details + caps."""
    payload = {
        "modelfile": "FROM qwen3:8b\nPARAMETER temperature 0.7",
        "parameters": "temperature 0.7\ntop_p 0.9",
        "template": "{{ .Prompt }}",
        "license": "apache-2.0",
        "details": {
            "format": "gguf",
            "family": "qwen3",
            "parameter_size": "8B",
            "quantization_level": "Q4_K_M",
        },
        "capabilities": ["completion", "tools"],
        "model_info": {"general.architecture": "qwen3"},
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    with patch("localsmartz.ollama.httpx.post", return_value=mock_resp) as mock_post:
        result = show_model("qwen3:8b-q4_K_M")

    assert result == payload
    assert result["details"]["parameter_size"] == "8B"
    assert "tools" in result["capabilities"]
    # Contract: POST /api/show with {"model": name}
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"model": "qwen3:8b-q4_K_M"}


def test_show_model_returns_none_on_404():
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "model 'phantom' not found"
    with patch("localsmartz.ollama.httpx.post", return_value=mock_resp):
        assert show_model("phantom:latest") is None


def test_show_model_returns_none_on_exception():
    """Any network error → None, never raises."""
    with patch("localsmartz.ollama.httpx.post", side_effect=httpx.ConnectError("refused")):
        assert show_model("anything:latest") is None


def test_show_model_empty_name_returns_none():
    assert show_model("") is None


# ── /api/delete — delete_model ───────────────────────────────────────────


def test_delete_model_http_delete():
    """200 OK → (True, None). Uses httpx.request('DELETE', ...) with the
    model in the JSON body, matching Ollama's documented contract."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("localsmartz.ollama.httpx.request", return_value=mock_resp) as mock_req:
        ok, err = delete_model("qwen3:8b-q4_K_M")
    assert ok is True
    assert err is None
    args, kwargs = mock_req.call_args
    assert args[0] == "DELETE"
    assert args[1].endswith("/api/delete")
    assert kwargs["json"] == {"model": "qwen3:8b-q4_K_M"}


def test_delete_model_404_soft_success():
    """A 404 means the model was already gone — report success (idempotent)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "model not found"
    with patch("localsmartz.ollama.httpx.request", return_value=mock_resp):
        ok, err = delete_model("phantom:latest")
    assert ok is True
    assert err is None


def test_delete_model_empty_name_rejected():
    ok, err = delete_model("")
    assert ok is False
    assert err is not None


def test_delete_model_500_surfaces_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "internal server error"
    with patch("localsmartz.ollama.httpx.request", return_value=mock_resp):
        ok, err = delete_model("x:y")
    assert ok is False
    assert err is not None
    assert "500" in err


def test_delete_model_timeout_is_soft_failure():
    with patch(
        "localsmartz.ollama.httpx.request",
        side_effect=httpx.TimeoutException("slow"),
    ):
        ok, err = delete_model("x:y")
    assert ok is False
    assert err is not None
    assert "timed out" in err.lower()
