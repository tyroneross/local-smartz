"""Pricing freshness tests (feat: c9)."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from localsmartz import cost


def test_last_verified_is_alias_for_last_updated():
    assert cost.LAST_VERIFIED == cost.LAST_UPDATED


def test_pricing_freshness_envelope_shape():
    env = cost.pricing_freshness()
    assert set(env.keys()) == {"last_verified", "age_days", "stale", "stale_threshold_days"}
    assert isinstance(env["age_days"], int)
    assert env["age_days"] >= 0
    assert env["last_verified"] == cost.LAST_VERIFIED.isoformat()


def test_stale_threshold_30_days():
    assert cost.PRICING_STALE_AFTER_DAYS == 30


def test_stale_when_old(monkeypatch):
    # Simulate 31 days passed by patching LAST_UPDATED to be 31 days ago.
    fake_date = date.today() - timedelta(days=31)
    monkeypatch.setattr(cost, "LAST_UPDATED", fake_date)
    monkeypatch.setattr(cost, "LAST_VERIFIED", fake_date)

    env = cost.pricing_freshness()
    assert env["age_days"] == 31
    assert env["stale"] is True


def test_fresh_when_recent(monkeypatch):
    fake_date = date.today() - timedelta(days=5)
    monkeypatch.setattr(cost, "LAST_UPDATED", fake_date)
    monkeypatch.setattr(cost, "LAST_VERIFIED", fake_date)

    env = cost.pricing_freshness()
    assert env["age_days"] == 5
    assert env["stale"] is False


def test_refresh_pricing_script_check_mode(tmp_path: Path):
    """scripts/refresh-pricing.py --check prints envelope JSON, exits 0."""
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "refresh-pricing.py"
    assert script.exists()

    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "last_verified" in payload
    assert "stale" in payload
    assert "pricing_urls" in payload
    assert "anthropic" in payload["pricing_urls"]
    assert "openai" in payload["pricing_urls"]
    assert "groq" in payload["pricing_urls"]


def test_refresh_pricing_script_human_mode():
    """No flags → human-readable summary, exit 0."""
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "refresh-pricing.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0
    assert "Last verified:" in result.stdout
    assert "anthropic" in result.stdout
    assert "openai" in result.stdout
    assert "groq" in result.stdout


def test_refresh_pricing_does_not_scrape():
    """Verify the script does not import requests/httpx for HTTP calls.

    We're paranoid: the user's hard constraint was 'do NOT scrape — just
    document'. Grep the script source.
    """
    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "scripts" / "refresh-pricing.py").read_text()
    # We DO allow a docstring mention of 'scrape'; we forbid actual
    # HTTP-call imports.
    forbidden_imports = ["import requests", "import httpx", "from urllib.request"]
    for tok in forbidden_imports:
        assert tok not in src, f"Forbidden import in refresh-pricing.py: {tok}"
