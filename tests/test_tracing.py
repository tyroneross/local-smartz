"""Tests for localsmartz.tracing module."""

import os
from localsmartz.tracing import configure_tracing


def _set_local_only(tmp_path, monkeypatch, value: bool) -> None:
    """Isolate HOME and persist local_only via the real global_config path,
    matching how serve.py/CLI would set it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from localsmartz import global_config

    global_config.save_global({"local_only": value})


def test_returns_false_when_no_env(tmp_path, monkeypatch):
    """No .env, no env vars → tracing disabled."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert configure_tracing(tmp_path) is False


def test_returns_true_when_env_var_set(tmp_path, monkeypatch):
    """LANGSMITH_TRACING=true in env → tracing enabled."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert configure_tracing(tmp_path) is True


def test_loads_dotenv_file(tmp_path, monkeypatch):
    """Reads LANGSMITH_ vars from .env file."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    (tmp_path / ".env").write_text('LANGSMITH_TRACING=true\nLANGSMITH_PROJECT=TestProject\n')
    result = configure_tracing(tmp_path)
    assert result is True
    assert os.environ.get("LANGSMITH_PROJECT") == "TestProject"


def test_dotenv_does_not_overwrite_existing(tmp_path, monkeypatch):
    """Existing env vars take precedence over .env file."""
    monkeypatch.setenv("LANGSMITH_PROJECT", "ExistingProject")
    (tmp_path / ".env").write_text('LANGSMITH_TRACING=true\nLANGSMITH_PROJECT=FileProject\n')
    configure_tracing(tmp_path)
    assert os.environ.get("LANGSMITH_PROJECT") == "ExistingProject"


def test_ignores_non_langsmith_vars(tmp_path, monkeypatch):
    """Only loads LANGSMITH_ prefixed vars from .env."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text('SECRET_KEY=should_not_load\nLANGSMITH_TRACING=true\n')
    configure_tracing(tmp_path)
    assert os.environ.get("SECRET_KEY") is None


def test_force_flag_enables_tracing(tmp_path, monkeypatch):
    """force=True sets LANGSMITH_TRACING=true."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    result = configure_tracing(tmp_path, force=True)
    assert result is True


def test_strips_quotes(tmp_path, monkeypatch):
    """Quoted values in .env have quotes stripped."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    (tmp_path / ".env").write_text('LANGSMITH_TRACING=true\nLANGSMITH_PROJECT="QuotedProject"\n')
    configure_tracing(tmp_path)
    assert os.environ["LANGSMITH_PROJECT"] == "QuotedProject"


# ── SEC-001: Local-Only must block LangSmith tracing egress ────────────────

def test_local_only_blocks_trace_flag(tmp_path, monkeypatch):
    """local_only on + the --trace path (force=True, mirrors __main__.py
    pre-setting LANGSMITH_TRACING=true) → tracing stays disabled."""
    _set_local_only(tmp_path, monkeypatch, True)
    result = configure_tracing(tmp_path, force=True)
    assert result is False
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_local_only_overrides_preset_env_var(tmp_path, monkeypatch):
    """A pre-set LANGSMITH_TRACING=true (e.g. inherited from a parent shell)
    cannot re-enable tracing for this process while local_only is on."""
    _set_local_only(tmp_path, monkeypatch, True)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    result = configure_tracing(tmp_path)
    assert result is False
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_local_only_prints_stderr_notice(tmp_path, monkeypatch, capsys):
    _set_local_only(tmp_path, monkeypatch, True)
    configure_tracing(tmp_path, force=True)
    captured = capsys.readouterr()
    assert "Local-Only: LangSmith tracing disabled" in captured.err


def test_local_only_off_leaves_trace_flag_unchanged(tmp_path, monkeypatch):
    """local_only off (explicit) → --trace path behaves exactly as before."""
    _set_local_only(tmp_path, monkeypatch, False)
    result = configure_tracing(tmp_path, force=True)
    assert result is True
    assert os.environ["LANGSMITH_TRACING"] == "true"
