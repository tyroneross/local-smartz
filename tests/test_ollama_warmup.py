"""Tests for ollama.warmup_model — covers success, HTTP error, timeout, and
the idempotency contract (warmup is safe to call when model is already resident)."""

from unittest.mock import patch, MagicMock

import httpx

from localsmartz.ollama import ensure_model_ready, evict_model, is_model_loaded, warmup_model


def test_warmup_success():
    """200 OK → (True, duration_ms, None)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("localsmartz.ollama.httpx.post", return_value=mock_resp):
        ok, duration_ms, err = warmup_model("qwen3:8b-q4_K_M")
    assert ok is True
    assert err is None
    assert duration_ms >= 0


def test_warmup_404_model_not_pulled():
    """404 from Ollama (model not pulled) → (False, ms, error with body)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "model 'bogus' not found"
    with patch("localsmartz.ollama.httpx.post", return_value=mock_resp):
        ok, duration_ms, err = warmup_model("bogus:latest")
    assert ok is False
    assert err is not None
    assert "404" in err
    assert "bogus" in err


def test_warmup_timeout():
    """Network timeout → (False, ms, error mentioning timeout)."""
    with patch(
        "localsmartz.ollama.httpx.post",
        side_effect=httpx.TimeoutException("slow"),
    ):
        ok, duration_ms, err = warmup_model("qwen3:8b-q4_K_M", timeout=1.0)
    assert ok is False
    assert err is not None
    assert "timed out" in err.lower()


def test_warmup_connection_error():
    """Any unexpected exception → (False, ms, formatted error) — never raises."""
    with patch(
        "localsmartz.ollama.httpx.post",
        side_effect=httpx.ConnectError("refused"),
    ):
        ok, duration_ms, err = warmup_model("qwen3:8b-q4_K_M")
    assert ok is False
    assert err is not None
    assert "Warmup failed" in err


def test_warmup_sends_keep_alive_and_empty_prompt():
    """Contract: we POST prompt='', stream=False, and pass keep_alive through
    so Ollama pins the model in VRAM. Confirms this hasn't regressed."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("localsmartz.ollama.httpx.post", return_value=mock_resp) as mock_post:
        warmup_model("qwen3:8b-q4_K_M", keep_alive="1h")
    _, kwargs = mock_post.call_args
    body = kwargs.get("json") or {}
    assert body["model"] == "qwen3:8b-q4_K_M"
    assert body["prompt"] == ""
    assert body["keep_alive"] == "1h"
    assert body["stream"] is False


def test_warmup_idempotent_same_model_twice():
    """Calling warmup twice should both succeed — the function is a thin
    wrapper over Ollama's own idempotent /api/generate."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("localsmartz.ollama.httpx.post", return_value=mock_resp):
        ok1, _, _ = warmup_model("qwen3:8b-q4_K_M")
        ok2, _, _ = warmup_model("qwen3:8b-q4_K_M")
    assert ok1 is True
    assert ok2 is True


def test_warmup_keep_alive_minus_one_pins_indefinitely():
    """keep_alive="-1" is the pin-forever value used at server boot + on
    model switch, so the active planning model stays VRAM-resident and
    users never pay the cold-load cost silently."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("localsmartz.ollama.httpx.post", return_value=mock_resp) as mock_post:
        ok, _, err = warmup_model("qwen3:8b-q4_K_M", keep_alive="-1")
    assert ok is True
    assert err is None
    _, kwargs = mock_post.call_args
    body = kwargs.get("json") or {}
    assert body["keep_alive"] == "-1"


def test_is_model_loaded_matches_running_models():
    """Resident-model detection should match either ``name`` or ``model``."""
    running = [
        {"name": "qwen3:8b"},
        {"model": "llama3.2:3b-instruct-q4_K_M"},
    ]
    with patch("localsmartz.ollama.list_running_models", return_value=running):
        assert is_model_loaded("qwen3:8b-q4_K_M") is True
        assert is_model_loaded("llama3.2:3b") is True
        assert is_model_loaded("gpt-oss:20b") is False


def test_ensure_model_ready_skips_warmup_when_already_loaded():
    """Hot-path requests should not reissue /api/generate warmups."""
    with patch("localsmartz.ollama.list_running_models", return_value=[{"name": "qwen3:8b"}]), \
         patch("localsmartz.ollama.warmup_model") as warmup:
        ok, duration_ms, err, already_loaded = ensure_model_ready("qwen3:8b-q4_K_M")
    assert ok is True
    assert duration_ms == 0
    assert err is None
    assert already_loaded is True
    warmup.assert_not_called()


def test_evict_sends_keep_alive_zero():
    """evict_model POSTs keep_alive=0 (integer) so Ollama drops the model
    from VRAM immediately. Used on model switch."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("localsmartz.ollama.httpx.post", return_value=mock_resp) as mock_post:
        ok, err = evict_model("qwen3:8b-q4_K_M")
    assert ok is True
    assert err is None
    _, kwargs = mock_post.call_args
    body = kwargs.get("json") or {}
    assert body["model"] == "qwen3:8b-q4_K_M"
    assert body["prompt"] == ""
    assert body["keep_alive"] == 0
    assert body["stream"] is False


def test_evict_404_treated_as_soft_success():
    """A 404 from /api/generate means the model isn't pulled — there's
    nothing resident to evict, so report success."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "model not found"
    with patch("localsmartz.ollama.httpx.post", return_value=mock_resp):
        ok, err = evict_model("phantom:latest")
    assert ok is True
    assert err is None


def test_evict_empty_name_rejected():
    """Defensive: caller passing an empty name should get a descriptive
    failure, not silently succeed or raise."""
    ok, err = evict_model("")
    assert ok is False
    assert err is not None


def test_evict_timeout_is_soft_failure():
    """httpx.TimeoutException should be caught and returned as (False, msg),
    never raised — callers (model-select path) must not be killed by
    transient network problems."""
    with patch(
        "localsmartz.ollama.httpx.post",
        side_effect=httpx.TimeoutException("slow"),
    ):
        ok, err = evict_model("qwen3:8b-q4_K_M", timeout=0.5)
    assert ok is False
    assert err is not None
    assert "timed out" in err.lower()
