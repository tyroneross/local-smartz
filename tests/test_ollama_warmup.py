"""Tests for ollama.warmup_model — covers success, HTTP error, timeout, and
the idempotency contract (warmup is safe to call when model is already resident)."""

from unittest.mock import patch, MagicMock

import httpx

from localsmartz.ollama import warmup_model


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
