"""Cloud-only token budget warn tests (feat: c8)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from localsmartz.serve import _emit_budget_warn, _read_budget_threshold


def test_below_threshold_no_event():
    assert _emit_budget_warn(session_tokens=100, threshold=1000, provider="anthropic") is None


def test_at_threshold_event_fires():
    out = _emit_budget_warn(session_tokens=1000, threshold=1000, provider="anthropic")
    assert out is not None
    assert out["type"] == "budget_warn"
    assert out["session_tokens"] == 1000
    assert out["threshold"] == 1000
    assert out["advisory"] is True
    assert out["provider"] == "anthropic"


def test_above_threshold_event_fires():
    out = _emit_budget_warn(session_tokens=2500, threshold=1000, provider="groq")
    assert out is not None
    assert out["session_tokens"] == 2500


def test_disabled_when_threshold_none():
    out = _emit_budget_warn(session_tokens=999_999, threshold=None, provider="anthropic")
    assert out is None


def test_ollama_exempt_even_above_threshold():
    """Ollama runs cost the user nothing at the wallet level — never warn."""
    out = _emit_budget_warn(session_tokens=10_000_000, threshold=100, provider="ollama")
    assert out is None


def test_no_abort_field():
    """Advisory only — must not include an 'abort' or 'block' field."""
    out = _emit_budget_warn(session_tokens=5000, threshold=1000, provider="anthropic")
    assert out is not None
    assert "abort" not in out
    assert "block" not in out


# --- _read_budget_threshold ---------------------------------------------


def test_read_threshold_missing_file(tmp_path: Path):
    out = _read_budget_threshold(tmp_path / "does-not-exist.json")
    assert out is None


def test_read_threshold_null_disabled(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"budget_tokens_warn": None}))
    assert _read_budget_threshold(cfg) is None


def test_read_threshold_positive_int(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"budget_tokens_warn": 50000}))
    assert _read_budget_threshold(cfg) == 50000


def test_read_threshold_zero_disables(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"budget_tokens_warn": 0}))
    assert _read_budget_threshold(cfg) is None


def test_read_threshold_string_value(tmp_path: Path):
    """Tolerant of strings like '50000' but rejects garbage."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"budget_tokens_warn": "50000"}))
    assert _read_budget_threshold(cfg) == 50000

    cfg.write_text(json.dumps({"budget_tokens_warn": "not-a-number"}))
    assert _read_budget_threshold(cfg) is None


def test_read_threshold_malformed_json(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text("not json {{")
    assert _read_budget_threshold(cfg) is None


def test_example_config_committed_does_not_enable():
    """The committed docs/config.example.json must have budget_tokens_warn=null.

    Lives under docs/ rather than .localsmartz/ because the latter is
    gitignored (user-specific runtime data); docs/ is discoverable.
    """
    repo_root = Path(__file__).resolve().parent.parent
    example = repo_root / "docs" / "config.example.json"
    assert example.exists()
    data = json.loads(example.read_text())
    assert data.get("budget_tokens_warn") is None
