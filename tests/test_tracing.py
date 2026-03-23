"""Tests for localsmartz.tracing module."""

import os
from localsmartz.tracing import configure_tracing


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
