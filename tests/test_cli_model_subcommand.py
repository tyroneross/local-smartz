"""CLI `localsmartz model ...` tests."""
from __future__ import annotations

from unittest.mock import patch

from localsmartz.cli import model as model_cli


def test_recommend_prints_full_tier_set(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "localsmartz.profiles.detect_tier",
        lambda: {"tier": "full", "ram_gb": 128, "gpu_vram_gb": 0, "legacy_profile": "full"},
    )
    rc = model_cli.main(["recommend"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "qwen3.5:9b" in out
    assert "qwen3.5:122b" in out
    assert "qwen3-next:80b" in out


def test_list_when_ollama_unreachable_returns_error(capsys, monkeypatch) -> None:
    monkeypatch.setattr("localsmartz.ollama.check_server", lambda: False)
    rc = model_cli.main(["list"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not running" in err.lower()


def test_doctor_runs_cleanly(capsys, monkeypatch) -> None:
    monkeypatch.setattr("localsmartz.ollama.check_server", lambda: True)
    monkeypatch.setattr("localsmartz.ollama.get_version", lambda: "0.99.0")
    monkeypatch.setattr("localsmartz.ollama.list_running_models", lambda: [])
    monkeypatch.setattr("localsmartz.observability.probe_collector", lambda endpoint=None, timeout=1.0: False)
    monkeypatch.setattr(
        "localsmartz.profiles.detect_tier",
        lambda: {"tier": "mini", "ram_gb": 24, "gpu_vram_gb": 0, "legacy_profile": "lite"},
    )
    rc = model_cli.main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Tier:" in out
    assert "Ollama:" in out
    assert "Phoenix:" in out
