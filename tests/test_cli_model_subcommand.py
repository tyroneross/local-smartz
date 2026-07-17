"""CLI `localsmartz model ...` tests."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from localsmartz.cli import model as model_cli


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


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


def test_add_already_installed_prints_role_suggestions(capsys, monkeypatch) -> None:
    """model add of an already-installed catalog model prints suggested assign lines."""

    def _fake_install(name):
        # Simulate Ollama returning immediately (already present) — rc 0, no events.
        return iter([{"type": "done", "duration_ms": 0, "bytes": 0}])

    monkeypatch.setattr("localsmartz.models.install.install", _fake_install)
    rc = model_cli.main(["add", "gemma4:26b"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Suggested: localsmartz model assign vision gemma4:26b" in out
    assert "Suggested: localsmartz model assign strong gemma4:26b" in out


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


# ── `localsmartz model local-only on|off|status` ──────────────────────────

def test_local_only_status_defaults_off(capsys, fake_home) -> None:
    rc = model_cli.main(["local-only", "status"])
    assert rc == 0
    assert "off" in capsys.readouterr().out


def test_local_only_on_persists(capsys, fake_home) -> None:
    from localsmartz import global_config

    rc = model_cli.main(["local-only", "on"])
    assert rc == 0
    assert global_config.get("local_only") is True
    out = capsys.readouterr().out
    assert "on" in out

    rc = model_cli.main(["local-only", "status"])
    assert rc == 0
    assert "on" in capsys.readouterr().out


def test_local_only_off_persists(capsys, fake_home) -> None:
    from localsmartz import global_config

    global_config.set("local_only", True)
    rc = model_cli.main(["local-only", "off"])
    assert rc == 0
    assert global_config.get("local_only") is False


# ── `localsmartz model global <model|clear>` ──────────────────────────────

def test_model_global_sets_pin_when_installed(capsys, fake_home, monkeypatch) -> None:
    from localsmartz import global_config

    monkeypatch.setattr("localsmartz.ollama.model_available", lambda name: True)
    rc = model_cli.main(["global", "qwen3:8b"])
    assert rc == 0
    assert global_config.get("active_model") == "qwen3:8b"
    assert "qwen3:8b" in capsys.readouterr().out


def test_model_global_rejects_uninstalled(capsys, fake_home, monkeypatch) -> None:
    from localsmartz import global_config

    monkeypatch.setattr("localsmartz.ollama.model_available", lambda name: False)
    rc = model_cli.main(["global", "not-installed:tag"])
    assert rc == 1
    assert global_config.get("active_model") == ""
    assert "Error" in capsys.readouterr().err


def test_model_global_clear(capsys, fake_home) -> None:
    from localsmartz import global_config

    global_config.set("active_model", "something")
    rc = model_cli.main(["global", "clear"])
    assert rc == 0
    assert global_config.get("active_model") == ""


# ── `localsmartz model agents enable|disable <role>` ──────────────────────

def test_model_agents_disable_persists(capsys, fake_home) -> None:
    from localsmartz import global_config

    rc = model_cli.main(["agents", "disable", "analyzer"])
    assert rc == 0
    assert "analyzer" in global_config.get("disabled_agents")
    assert "disabled" in capsys.readouterr().out


def test_model_agents_enable_persists(capsys, fake_home) -> None:
    from localsmartz import global_config

    global_config.set("disabled_agents", ["analyzer"])
    rc = model_cli.main(["agents", "enable", "analyzer"])
    assert rc == 0
    assert "analyzer" not in global_config.get("disabled_agents")


def test_model_agents_disable_orchestrator_rejected(capsys, fake_home) -> None:
    rc = model_cli.main(["agents", "disable", "orchestrator"])
    assert rc == 1
    assert "Error" in capsys.readouterr().err


def test_model_agents_disable_unknown_role_rejected(capsys, fake_home) -> None:
    rc = model_cli.main(["agents", "disable", "ghost"])
    assert rc == 1
    assert "Error" in capsys.readouterr().err


def test_model_agents_disable_all_rejected(capsys, fake_home) -> None:
    from localsmartz.profiles import RUNNABLE_ROLES

    for role in RUNNABLE_ROLES[:-1]:
        rc = model_cli.main(["agents", "disable", role])
        assert rc == 0
    capsys.readouterr()
    rc = model_cli.main(["agents", "disable", RUNNABLE_ROLES[-1]])
    assert rc == 1
    assert "Error" in capsys.readouterr().err
