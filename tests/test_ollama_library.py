"""Tests for the ollama.com library scraper + popular-ranking cache.

Does NOT hit the network. Uses a fixture HTML snippet cribbed from a real
``ollama.com/search?c=tools`` response so the parser is exercised against
the actual DOM shape (x-test-* attribute layout, href=/library/<name>).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from localsmartz import ollama_library
from localsmartz.ollama_library import (
    _dedupe_by_family_prefer_newer,
    _family_stem,
    _parse_cards,
    _parse_pull_count,
    _parse_updated_days,
    fetch_library,
    get_popular,
    load_cached,
    save_cache,
)


# Minimal reproduction of the ollama.com/search card layout. Two families
# so the dedupe-by-family path is exercised (gemma4 newer + gemma3 older
# should collapse into one gemma entry keeping gemma4). qwen3.5 adds a
# second family with decimal version to confirm the family-stem regex.
FIXTURE_HTML = """
<html><body>
<a href="/library/gemma4" class="group">
  <span x-test-search-response-title>gemma4</span>
  <span x-test-pull-count>145.3K</span>
  <span x-test-size>e2b</span>
  <span x-test-size>e4b</span>
  <span x-test-size>26b</span>
  <span x-test-size>31b</span>
  <span x-test-capability>tools</span>
  <span x-test-capability>vision</span>
  <span x-test-capability>thinking</span>
  <span x-test-updated>2 hours ago</span>
</a>
<a href="/library/gemma3" class="group">
  <span x-test-search-response-title>gemma3</span>
  <span x-test-pull-count>35.4M</span>
  <span x-test-size>1b</span>
  <span x-test-size>4b</span>
  <span x-test-size>12b</span>
  <span x-test-size>27b</span>
  <span x-test-capability>tools</span>
  <span x-test-capability>vision</span>
  <span x-test-updated>4 months ago</span>
</a>
<a href="/library/qwen3.5" class="group">
  <span x-test-search-response-title>qwen3.5</span>
  <span x-test-pull-count>2.9M</span>
  <span x-test-size>4b</span>
  <span x-test-size>14b</span>
  <span x-test-size>32b</span>
  <span x-test-capability>tools</span>
  <span x-test-updated>3 days ago</span>
</a>
<a href="/library/qwen3" class="group">
  <span x-test-search-response-title>qwen3</span>
  <span x-test-pull-count>20.6M</span>
  <span x-test-size>1.7b</span>
  <span x-test-size>8b</span>
  <span x-test-size>32b</span>
  <span x-test-capability>tools</span>
  <span x-test-updated>9 months ago</span>
</a>
</body></html>
"""


# ── Primitives ──────────────────────────────────────────────────────────

def test_parse_pull_count_handles_all_units():
    assert _parse_pull_count("2.9M") == 2_900_000
    assert _parse_pull_count("145.3K") == 145_300
    assert _parse_pull_count("1.1B") == 1_100_000_000
    assert _parse_pull_count("42") == 42
    assert _parse_pull_count("") == 0
    assert _parse_pull_count("not-a-number") == 0


def test_parse_updated_days_covers_common_units():
    assert _parse_updated_days("2 hours ago") == 0
    assert _parse_updated_days("3 days ago") == 3
    assert _parse_updated_days("1 week ago") == 7
    assert _parse_updated_days("4 months ago") == 120
    assert _parse_updated_days("1 year ago") == 365
    assert _parse_updated_days("2 years ago") == 730
    # Unparseable → sentinel "very old" so it sinks in the newer-first sort.
    assert _parse_updated_days("") == 3650
    assert _parse_updated_days("ages ago") == 3650


def test_family_stem_collapses_major_versions():
    assert _family_stem("gemma4") == "gemma"
    assert _family_stem("gemma3") == "gemma"
    assert _family_stem("gemma") == "gemma"
    assert _family_stem("qwen3.5") == "qwen"
    assert _family_stem("qwen3") == "qwen"
    assert _family_stem("llama3.3") == "llama"
    # Suffix-bearing variants stay grouped with their family+suffix cohort.
    assert _family_stem("gemma3-instruct") == "gemma-instruct"


# ── Parsing + enrichment ────────────────────────────────────────────────

def test_parse_cards_extracts_all_four_families():
    cards = _parse_cards(FIXTURE_HTML)
    names = [c["name"] for c in cards]
    assert names == ["gemma4", "gemma3", "qwen3.5", "qwen3"]


def test_parse_cards_populates_pull_counts_numeric_and_raw():
    cards = _parse_cards(FIXTURE_HTML)
    by_name = {c["name"]: c for c in cards}
    assert by_name["gemma4"]["pulls"] == 145_300
    assert by_name["gemma4"]["pulls_raw"] == "145.3K"
    assert by_name["gemma3"]["pulls"] == 35_400_000


def test_parse_cards_extracts_sizes_and_capabilities():
    cards = _parse_cards(FIXTURE_HTML)
    gemma4 = next(c for c in cards if c["name"] == "gemma4")
    # Size tags are preserved in page order (no re-sort).
    assert gemma4["sizes"][:4] == ["e2b", "e4b", "26b", "31b"]
    # Capabilities deduped + sorted.
    assert gemma4["capabilities"] == ["thinking", "tools", "vision"]


def test_parse_cards_computes_updated_days():
    cards = _parse_cards(FIXTURE_HTML)
    by_name = {c["name"]: c for c in cards}
    assert by_name["gemma4"]["updated_days"] == 0       # hours → 0
    assert by_name["qwen3.5"]["updated_days"] == 3
    assert by_name["gemma3"]["updated_days"] == 120
    assert by_name["qwen3"]["updated_days"] == 270


# ── Family dedup: newer wins even with fewer pulls ──────────────────────

def test_dedup_prefers_newer_family_member_over_more_pulled_one():
    """gemma4 has 145K lifetime pulls; gemma3 has 35M. Strict pull-sort
    would bury gemma4. Within-family dedup keeps the newer release, then
    the cross-family sort by pulls still ranks gemma above qwen."""
    cards = _parse_cards(FIXTURE_HTML)
    deduped = _dedupe_by_family_prefer_newer(cards)
    names = [c["name"] for c in deduped]
    assert names == ["gemma4", "qwen3.5"]  # not gemma3, not qwen3


def test_dedup_stable_when_only_one_family_member():
    """Single-member families (no collision) pass through unchanged."""
    cards = [
        {"name": "mistral", "family": "mistral", "updated_days": 90, "pulls": 10},
        {"name": "phi4", "family": "phi", "updated_days": 10, "pulls": 5},
    ]
    deduped = _dedupe_by_family_prefer_newer(cards)
    assert [c["name"] for c in deduped] == ["mistral", "phi4"]


# ── Full fetch (mocked httpx) ───────────────────────────────────────────

def test_fetch_library_sorts_deduped_families_by_pulls(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.text = FIXTURE_HTML
    mock_resp.raise_for_status = MagicMock()
    with patch("localsmartz.ollama_library.httpx.get", return_value=mock_resp) as mock_get:
        result = fetch_library(capability="tools")
    # Request shape — capability propagates as ?c=tools.
    _, kwargs = mock_get.call_args
    assert kwargs.get("params") == {"c": "tools"}
    names = [c["name"] for c in result]
    # After dedup: [gemma4, qwen3.5]. Sorted by pulls desc:
    # gemma4 (145K) vs qwen3.5 (2.9M) → qwen3.5 first.
    assert names == ["qwen3.5", "gemma4"]


def test_fetch_library_returns_empty_on_network_error():
    with patch(
        "localsmartz.ollama_library.httpx.get",
        side_effect=httpx.ConnectError("refused"),
    ):
        assert fetch_library(capability="tools") == []


# ── Cache ───────────────────────────────────────────────────────────────

def test_save_and_load_cache_roundtrip(tmp_path):
    entries = [{"name": "gemma4", "pulls": 100}]
    save_cache(tmp_path, entries)
    loaded, fetched_at = load_cached(tmp_path)
    assert loaded == entries
    assert fetched_at is not None


def test_load_cached_returns_empty_when_ttl_expired(tmp_path):
    # Write a cache with a fetched_at in the distant past.
    payload = {"fetched_at": time.time() - 10_000, "entries": [{"name": "x"}]}
    (tmp_path / "library-cache.json").write_text(json.dumps(payload))
    loaded, fetched_at = load_cached(tmp_path, ttl_seconds=60)
    assert loaded == []
    # Expired caches still surface the timestamp so UI can show "stale".
    assert fetched_at is not None


def test_get_popular_falls_back_to_stale_on_fetch_failure(tmp_path):
    """Network dies; we still have a day-old cache; the UI shouldn't go
    empty."""
    (tmp_path / "library-cache.json").write_text(json.dumps({
        "fetched_at": time.time() - 10_000,  # older than default TTL
        "entries": [{"name": "stale-model", "pulls": 1}],
    }))
    with patch(
        "localsmartz.ollama_library.httpx.get",
        side_effect=httpx.ConnectError("refused"),
    ):
        payload = get_popular(tmp_path, limit=10, refresh=True)
    assert payload["source"] == "stale-fallback"
    assert payload["entries"] == [{"name": "stale-model", "pulls": 1}]


def test_get_popular_serves_fresh_live_fetch_when_cache_cold(tmp_path):
    mock_resp = MagicMock()
    mock_resp.text = FIXTURE_HTML
    mock_resp.raise_for_status = MagicMock()
    with patch("localsmartz.ollama_library.httpx.get", return_value=mock_resp):
        payload = get_popular(tmp_path, limit=10, refresh=False)
    assert payload["source"] == "live"
    assert payload["entries"][0]["name"] == "qwen3.5"  # highest-pull deduped entry
    # Cache file should now exist so the next call returns "cache".
    assert (tmp_path / "library-cache.json").exists()


def test_get_popular_serves_empty_source_when_no_cache_and_no_network(tmp_path):
    with patch(
        "localsmartz.ollama_library.httpx.get",
        side_effect=httpx.ConnectError("refused"),
    ):
        payload = get_popular(tmp_path, limit=10, refresh=True)
    assert payload["source"] == "empty"
    assert payload["entries"] == []
