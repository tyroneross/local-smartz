"""Multi-provider parity scorecard tests.

Cloud providers without API keys are SKIPPED gracefully — never crash.
Ollama is run if the daemon is reachable; otherwise also skipped.

These tests exercise the orchestration layer (skip logic, output shape,
file writes), not the actual provider SDKs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from localsmartz import model_eval
from localsmartz.model_eval import (
    DEFAULT_CLOUD_MODELS,
    ModelEvalResult,
    ModelEvalTaskResult,
    _parse_cloud_models,
    _provider_has_key,
    run_multi_provider,
    write_scorecard_md,
    write_scorecard_tsv,
)


def _fake_result(provider: str, model: str, *, ok: bool = True) -> ModelEvalResult:
    """Build a minimal ModelEvalResult for write/output assertions."""
    return ModelEvalResult(
        provider=provider,
        model=model,
        results=[
            ModelEvalTaskResult(
                task="arithmetic_exact",
                provider=provider,
                model=model,
                ok=ok,
                latency_ms=42,
                reply="360" if ok else "wrong",
                weight=1.0,
                category="math",
            )
        ],
    )


# --- _parse_cloud_models -------------------------------------------------


def test_parse_cloud_models_default_when_empty():
    assert _parse_cloud_models(None) == {p: list(m) for p, m in DEFAULT_CLOUD_MODELS.items()}
    assert _parse_cloud_models("") == {p: list(m) for p, m in DEFAULT_CLOUD_MODELS.items()}


def test_parse_cloud_models_parses_pairs():
    out = _parse_cloud_models("anthropic:claude-haiku-4,groq:llama-3.3-70b-versatile")
    assert out["anthropic"] == ["claude-haiku-4"]
    assert out["groq"] == ["llama-3.3-70b-versatile"]
    assert "openai" not in out


def test_parse_cloud_models_ignores_malformed():
    out = _parse_cloud_models("anthropic:m1,malformed-no-colon,groq:m2,")
    assert out == {"anthropic": ["m1"], "groq": ["m2"]}


# --- _provider_has_key ---------------------------------------------------


def test_provider_has_key_ollama_always_true(monkeypatch):
    # Ollama doesn't use an API key — daemon liveness checked at run time.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _provider_has_key("ollama") is True


def test_provider_has_key_env_var(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert _provider_has_key("anthropic") is True


def test_provider_has_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # _provider_has_key reads env only — main_multi_provider runs
    # secrets.export_to_env() at startup so Keychain → env happens once.
    assert _provider_has_key("anthropic") is False
    assert _provider_has_key("openai") is False
    assert _provider_has_key("groq") is False


def test_provider_has_key_via_export_to_env(monkeypatch):
    """Keychain → env bridge happens upstream in main_multi_provider.

    This regression-tests the fix for the 2026-05-08 bug where
    ``_provider_has_key`` looked up ``secrets.get(f"{provider}_api_key")``
    (e.g. ``"groq_api_key"``) — which never matched the canonical
    capitalized provider name (``"Groq"``) used in PRESET_PROVIDERS.

    After the fix, the helper reads env vars only; the upstream caller
    (``main_multi_provider``) runs ``secrets.export_to_env()`` to populate
    them from Keychain. We simulate the post-export state by setting the
    env var directly.
    """
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert _provider_has_key("groq") is True


# --- run_multi_provider --------------------------------------------------


def test_run_multi_provider_skips_cloud_without_keys(monkeypatch):
    """When no keys present, only Ollama is attempted; cloud all skipped."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    skips: list[tuple[str, str]] = []

    def on_skip(provider: str, reason: str) -> None:
        skips.append((provider, reason))

    # Stub out the actual ollama matrix (don't need a live daemon for this test).
    with patch("localsmartz.model_eval.run_model_matrix", return_value=[]):
        with patch("localsmartz.secrets.get", return_value=None):
            results = run_multi_provider(
                ollama_models=["fake-model"],
                cloud_models=DEFAULT_CLOUD_MODELS,
                on_skip=on_skip,
            )

    assert results == []
    skipped_providers = sorted(p for p, _ in skips)
    assert skipped_providers == ["anthropic", "groq", "openai"]
    for _, reason in skips:
        assert "no API key" in reason


def test_run_multi_provider_ollama_unreachable(monkeypatch):
    """Ollama daemon unreachable → reported via on_skip, not raised."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("connection refused")

    skips: list[tuple[str, str]] = []
    with patch("localsmartz.model_eval.run_model_matrix", side_effect=boom):
        with patch("localsmartz.secrets.get", return_value=None):
            results = run_multi_provider(
                ollama_models=["fake"],
                cloud_models={},
                on_skip=lambda p, r: skips.append((p, r)),
            )

    assert results == []
    assert any(p == "ollama" and "connection refused" in r for p, r in skips)


def test_run_multi_provider_includes_provider_with_key(monkeypatch):
    """Provider with a key + working stub → result included."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    fake_groq = [_fake_result("groq", "llama-3.3-70b-versatile")]
    fake_ollama = [_fake_result("ollama", "qwen3:8b")]

    def stub_matrix(*, provider: str, models: Any, **_kw: Any) -> list[ModelEvalResult]:
        if provider == "ollama":
            return fake_ollama
        if provider == "groq":
            return fake_groq
        return []

    with patch("localsmartz.model_eval.run_model_matrix", side_effect=stub_matrix):
        results = run_multi_provider(
            ollama_models=["qwen3:8b"],
            cloud_models={"groq": ["llama-3.3-70b-versatile"]},
        )

    providers = sorted({r.provider for r in results})
    assert providers == ["groq", "ollama"]


# --- scorecard writers ---------------------------------------------------


def test_write_scorecard_tsv_shape(tmp_path: Path):
    results = [_fake_result("ollama", "qwen3:8b"), _fake_result("groq", "llama", ok=False)]
    out = tmp_path / "scorecard.tsv"
    write_scorecard_tsv(results, out)
    text = out.read_text()
    header = text.splitlines()[0]
    assert header.split("\t") == [
        "provider", "model", "task", "ok", "latency_ms", "weight", "category", "error",
    ]
    assert "ollama\tqwen3:8b\tarithmetic_exact\t1\t" in text
    assert "groq\tllama\tarithmetic_exact\t0\t" in text


def test_write_scorecard_md_with_skips(tmp_path: Path):
    results = [_fake_result("ollama", "qwen3:8b")]
    out = tmp_path / "scorecard.md"
    write_scorecard_md(
        results,
        out,
        skips=[("anthropic", "no API key"), ("openai", "no API key")],
    )
    text = out.read_text()
    assert "# Multi-Provider Parity Scorecard" in text
    assert "## Skipped Providers" in text
    assert "anthropic" in text
    assert "## ollama" in text
    assert "qwen3:8b" in text


def test_write_scorecard_md_no_results(tmp_path: Path):
    out = tmp_path / "scorecard.md"
    write_scorecard_md([], out, skips=[("anthropic", "no API key")])
    assert "_No providers produced results" in out.read_text()


# --- CLI integration -----------------------------------------------------


def test_cli_multi_provider_writes_scorecards(monkeypatch, tmp_path: Path, capsys):
    """End-to-end: --multi-provider with no keys → exits 0, writes scorecards, all cloud skipped."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # Stub the Keychain bridge so a real local Groq key in Keychain doesn't
    # leak into the test (this test simulates "no keys anywhere").
    monkeypatch.setattr("localsmartz.secrets.export_to_env", lambda: 0)

    fake_ollama = [_fake_result("ollama", "qwen3:8b")]

    with patch("localsmartz.model_eval.run_model_matrix", return_value=fake_ollama):
        with patch("localsmartz.secrets.get", return_value=None):
            rc = model_eval.main(
                [
                    "--multi-provider",
                    "--out-dir", str(tmp_path),
                    "--stamp", "2026-05-08",
                    "--models", "qwen3:8b",
                    "--limit-tasks", "1",
                    "--json",
                ]
            )

    assert rc == 0
    tsv = tmp_path / "2026-05-08-multi-provider-parity.tsv"
    md = tmp_path / "2026-05-08-multi-provider-parity.md"
    assert tsv.exists()
    assert md.exists()
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload["providers_run"] == ["ollama"]
    assert sorted(p["provider"] for p in payload["providers_skipped"]) == [
        "anthropic", "groq", "openai",
    ]


def test_main_multi_provider_calls_export_to_env(monkeypatch, tmp_path: Path):
    """The Keychain → env bridge MUST fire before _provider_has_key runs.

    Regression test for the 2026-05-08 fix. If a future change skips the
    ``secrets.export_to_env()`` call at the top of ``main_multi_provider``,
    a Keychain-only Groq key would silently disappear from the scorecard.
    """
    import argparse

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    called = {"export": 0}

    def _fake_export() -> int:
        called["export"] += 1
        return 0

    monkeypatch.setattr("localsmartz.secrets.export_to_env", _fake_export)

    args = argparse.Namespace(
        cloud_models="",
        models="",
        limit_models=1,
        limit_tasks=1,
        out_dir=str(tmp_path),
        stamp="2026-05-08-export-test",
        json=False,
    )
    with patch("localsmartz.model_eval.run_model_matrix", return_value=[]):
        rc = model_eval.main_multi_provider(args)
    assert rc == 0
    assert called["export"] == 1
