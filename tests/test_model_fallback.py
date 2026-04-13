"""Profile model fallback — when the configured planning model isn't pulled
in Ollama, fall back to the largest available model with a warning."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from localsmartz.ollama import resolve_available_model


def test_returns_requested_when_available():
    with patch("localsmartz.ollama.check_server", return_value=True), \
         patch("localsmartz.ollama.model_available", return_value=True):
        chosen, msg = resolve_available_model("qwen3:8b")
    assert chosen == "qwen3:8b"
    assert msg is None


def test_falls_back_to_largest_available_when_requested_missing():
    available = [("tiny:1b", 0.5), ("qwen3:8b-q4_K_M", 5.2), ("gpt-oss:120b", 65.0)]
    with patch("localsmartz.ollama.check_server", return_value=True), \
         patch("localsmartz.ollama.model_available", return_value=False), \
         patch("localsmartz.ollama.list_models_with_size", return_value=available):
        chosen, msg = resolve_available_model("llama3.1:70b-instruct-q5_K_M")
    assert chosen == "gpt-oss:120b"
    assert msg is not None
    assert "llama3.1:70b" in msg
    assert "gpt-oss:120b" in msg
    assert "ollama pull" in msg


def test_skips_models_below_min_gb():
    # Only tiny embedding-class models present
    available = [("nomic-embed:latest", 0.3), ("micro:1b", 0.7)]
    with patch("localsmartz.ollama.check_server", return_value=True), \
         patch("localsmartz.ollama.model_available", return_value=False), \
         patch("localsmartz.ollama.list_models_with_size", return_value=available):
        chosen, msg = resolve_available_model("qwen3:8b", min_gb=1.0)
    assert chosen is None
    assert msg is not None
    assert "no other suitable model" in msg.lower()


def test_returns_error_when_ollama_down():
    with patch("localsmartz.ollama.check_server", return_value=False):
        chosen, msg = resolve_available_model("qwen3:8b")
    assert chosen is None
    assert "not running" in msg.lower()


def test_returns_error_when_no_models_at_all():
    with patch("localsmartz.ollama.check_server", return_value=True), \
         patch("localsmartz.ollama.model_available", return_value=False), \
         patch("localsmartz.ollama.list_models_with_size", return_value=[]):
        chosen, msg = resolve_available_model("qwen3:8b")
    assert chosen is None
    assert "ollama pull qwen3:8b" in msg


def test_preflight_mutates_profile_to_fallback(monkeypatch, capsys):
    """When the requested model isn't pulled but a substitute is, _preflight
    should mutate the profile in-place and return True (with a stderr warning)."""
    from localsmartz import __main__ as main_mod

    profile = {"planning_model": "llama3.1:70b-instruct-q5_K_M", "name": "full"}
    monkeypatch.setattr(
        "localsmartz.ollama.check_server", lambda: True
    )
    monkeypatch.setattr(
        "localsmartz.ollama.model_available", lambda m: False
    )
    monkeypatch.setattr(
        "localsmartz.ollama.list_models_with_size",
        lambda: [("qwen3:8b-q4_K_M", 5.2), ("gpt-oss:120b", 65.0)],
    )
    ok = main_mod._preflight(profile)
    assert ok is True
    assert profile["planning_model"] == "gpt-oss:120b"
    err = capsys.readouterr().err
    assert "llama3.1:70b" in err
    assert "gpt-oss:120b" in err


def test_preflight_returns_false_when_no_fallback(monkeypatch, capsys):
    from localsmartz import __main__ as main_mod

    profile = {"planning_model": "llama3.1:70b-instruct-q5_K_M", "name": "full"}
    monkeypatch.setattr("localsmartz.ollama.check_server", lambda: True)
    monkeypatch.setattr("localsmartz.ollama.model_available", lambda m: False)
    monkeypatch.setattr("localsmartz.ollama.list_models_with_size", lambda: [])
    ok = main_mod._preflight(profile)
    assert ok is False
    err = capsys.readouterr().err
    assert "Error" in err
