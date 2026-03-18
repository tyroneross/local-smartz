"""Tests for Ollama health check module."""

from unittest.mock import patch, MagicMock

from localsmartz.ollama import (
    check_server,
    is_installed,
    list_models_with_size,
    model_available,
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
    import httpx
    with patch("localsmartz.ollama.httpx.get", side_effect=httpx.ConnectError("refused")):
        assert list_models_with_size() == []
