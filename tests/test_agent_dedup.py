"""Tests for per-turn tool-call deduplication (deliverable 1b).

TurnCallDeduplicator tracks (tool_name, sorted_args_tuple) keys within a
single user-message turn. If the model re-emits the same call, the second
invocation is a cache hit — the caller skips re-running the tool and returns
the first result.

The cache MUST reset on next_turn() — it must NOT leak across user messages.
"""
from __future__ import annotations

import pytest

from localsmartz.validation import TurnCallDeduplicator


class TestTurnCallDeduplicatorCacheKey:
    """Verify the key-shape contract."""

    def test_same_name_same_args_produces_same_key(self):
        d = TurnCallDeduplicator()
        k1 = d._make_key("web_search", {"query": "arista vs juniper"})
        k2 = d._make_key("web_search", {"query": "arista vs juniper"})
        assert k1 == k2

    def test_same_name_different_args_produces_different_key(self):
        d = TurnCallDeduplicator()
        k1 = d._make_key("web_search", {"query": "arista"})
        k2 = d._make_key("web_search", {"query": "juniper"})
        assert k1 != k2

    def test_different_name_same_args_produces_different_key(self):
        d = TurnCallDeduplicator()
        k1 = d._make_key("web_search", {"query": "foo"})
        k2 = d._make_key("scrape_url", {"query": "foo"})
        assert k1 != k2

    def test_key_is_order_independent_for_args(self):
        """Args dict ordering must not matter — both orderings hash to the same key."""
        d = TurnCallDeduplicator()
        k1 = d._make_key("web_search", {"query": "foo", "max_results": 5})
        k2 = d._make_key("web_search", {"max_results": 5, "query": "foo"})
        assert k1 == k2

    def test_none_args_treated_as_empty(self):
        d = TurnCallDeduplicator()
        k1 = d._make_key("web_search", None)
        k2 = d._make_key("web_search", {})
        assert k1 == k2


class TestTurnCallDeduplicatorBehavior:
    """Core dedup contract."""

    def test_first_call_is_not_duplicate(self):
        d = TurnCallDeduplicator()
        _, is_dup = d.check_and_record("web_search", {"query": "foo"}, result="result-A")
        assert is_dup is False

    def test_second_identical_call_is_duplicate(self):
        d = TurnCallDeduplicator()
        d.check_and_record("web_search", {"query": "foo"}, result="result-A")
        _, is_dup = d.check_and_record("web_search", {"query": "foo"}, result="result-B")
        assert is_dup is True

    def test_duplicate_returns_cached_result_not_new_one(self):
        d = TurnCallDeduplicator()
        d.check_and_record("web_search", {"query": "foo"}, result="result-A")
        cached, _ = d.check_and_record("web_search", {"query": "foo"}, result="result-B")
        assert cached == "result-A"

    def test_tool_runs_only_once_even_on_third_emission(self):
        """Three identical emissions — only the first counts."""
        d = TurnCallDeduplicator()
        calls: list[str] = []

        def fake_tool(args: dict) -> str:
            calls.append("run")
            return "search-output"

        args = {"query": "arista vs juniper"}
        for _ in range(3):
            if not d.is_duplicate("web_search", args):
                result = fake_tool(args)
                d.check_and_record("web_search", args, result=result)

        assert len(calls) == 1, "Tool should have run exactly once"

    def test_different_args_not_deduplicated(self):
        d = TurnCallDeduplicator()
        _, dup1 = d.check_and_record("web_search", {"query": "arista"}, result="r1")
        _, dup2 = d.check_and_record("web_search", {"query": "juniper"}, result="r2")
        assert dup1 is False
        assert dup2 is False

    def test_different_tool_names_not_deduplicated(self):
        d = TurnCallDeduplicator()
        _, dup1 = d.check_and_record("web_search", {"query": "foo"}, result="r1")
        _, dup2 = d.check_and_record("scrape_url", {"query": "foo"}, result="r2")
        assert dup1 is False
        assert dup2 is False


class TestTurnCallDeduplicatorTurnIsolation:
    """Cache MUST NOT leak across user-message turns."""

    def test_next_turn_resets_cache(self):
        d = TurnCallDeduplicator()
        d.check_and_record("web_search", {"query": "foo"}, result="r1")
        assert d.is_duplicate("web_search", {"query": "foo"}) is True

        d.next_turn()

        assert d.is_duplicate("web_search", {"query": "foo"}) is False

    def test_call_count_resets_after_next_turn(self):
        d = TurnCallDeduplicator()
        d.check_and_record("web_search", {"query": "a"}, result="r1")
        d.check_and_record("scrape_url", {"url": "b"}, result="r2")
        assert d.call_count == 2

        d.next_turn()
        assert d.call_count == 0

    def test_same_call_in_two_consecutive_turns_is_not_suppressed(self):
        """A repeated call across turns (turn 1 then turn 2) must not be
        treated as a duplicate — that would break legitimate re-queries."""
        d = TurnCallDeduplicator()
        d.check_and_record("web_search", {"query": "foo"}, result="r1")
        d.next_turn()
        _, is_dup = d.check_and_record("web_search", {"query": "foo"}, result="r2")
        assert is_dup is False

    def test_multi_turn_sequence(self):
        """Three turns with the same call each — each turn's first call is fresh."""
        d = TurnCallDeduplicator()
        for turn in range(3):
            _, is_dup = d.check_and_record("web_search", {"query": "foo"}, result=f"r{turn}")
            assert is_dup is False, f"Turn {turn} first call should not be a dup"
            # Same call again within same turn
            _, is_dup2 = d.check_and_record("web_search", {"query": "foo"}, result="extra")
            assert is_dup2 is True, f"Turn {turn} second call should be a dup"
            d.next_turn()
