"""Tests for model drift detection."""

import pytest

from localsmartz.drift import (
    DriftDetector,
    DriftEvent,
    DriftSignal,
    Severity,
    _Thresholds,
    _jaccard_similarity,
    create_drift_detector,
)


# ── Jaccard similarity ──


def test_jaccard_identical():
    assert _jaccard_similarity("hello world", "hello world") == 1.0


def test_jaccard_disjoint():
    assert _jaccard_similarity("hello world", "foo bar") == 0.0


def test_jaccard_partial():
    sim = _jaccard_similarity("AI trends 2026", "AI trends latest")
    assert 0.3 < sim < 0.7


def test_jaccard_empty():
    assert _jaccard_similarity("", "hello") == 0.0
    assert _jaccard_similarity("hello", "") == 0.0


# ── Tool monopoly ──


def test_tool_monopoly_fires_above_threshold():
    d = DriftDetector(_Thresholds(monopoly_threshold=0.6, max_turns=20))
    # 4 web_search + 1 scrape = 5 calls, web_search at 80%
    for i in range(4):
        d.record_tool_call("web_search", {"query": f"q{i}"}, i)
    d.record_tool_call("scrape_url", {"url": "http://x"}, 4)
    # 5th web_search = 5/6 = 83%
    events = d.record_tool_call("web_search", {"query": "q5"}, 5)
    signals = [e.signal for e in events]
    assert DriftSignal.TOOL_MONOPOLY in signals


def test_tool_monopoly_no_fire_below_threshold():
    d = DriftDetector(_Thresholds(monopoly_threshold=0.6, max_turns=20))
    # Evenly distributed: 2 each of 3 tools = 33% each
    for i, name in enumerate(["web_search", "scrape_url", "python_exec"] * 2):
        events = d.record_tool_call(name, {}, i)
        monopoly = [e for e in events if e.signal == DriftSignal.TOOL_MONOPOLY]
        assert monopoly == []


def test_tool_monopoly_lite_lower_threshold():
    d = DriftDetector(_Thresholds(monopoly_threshold=0.5, max_turns=10))
    # 2 other + 3 web_search = 5 calls, web_search at 60% — fires at threshold 50%
    d.record_tool_call("scrape_url", {}, 0)
    d.record_tool_call("python_exec", {}, 1)
    for i in range(3):
        d.record_tool_call("web_search", {"query": f"q{i}"}, i + 2)
    # Check all events — monopoly fires when 5th call makes ratio checkable
    events = d.get_events()
    monopoly = [e for e in events if e.signal == DriftSignal.TOOL_MONOPOLY]
    assert len(monopoly) == 1


# ── Forbidden tool ──


def test_forbidden_tool_fires():
    allowed = {"web_search", "scrape_url"}
    d = DriftDetector(_Thresholds(allowed_tools=allowed, max_turns=20))
    events = d.record_tool_call("task", {}, 0)
    assert len(events) == 1
    assert events[0].signal == DriftSignal.FORBIDDEN_TOOL
    assert events[0].severity == Severity.CRITICAL


def test_forbidden_tool_no_fire_when_allowed():
    allowed = {"web_search", "scrape_url", "task"}
    d = DriftDetector(_Thresholds(allowed_tools=allowed, max_turns=20))
    events = d.record_tool_call("task", {}, 0)
    forbidden = [e for e in events if e.signal == DriftSignal.FORBIDDEN_TOOL]
    assert forbidden == []


def test_forbidden_tool_no_fire_when_no_allowlist():
    d = DriftDetector(_Thresholds(allowed_tools=None, max_turns=20))
    events = d.record_tool_call("anything_goes", {}, 0)
    forbidden = [e for e in events if e.signal == DriftSignal.FORBIDDEN_TOOL]
    assert forbidden == []


# ── Stale search ──


def test_stale_search_fires_on_similar_query():
    d = DriftDetector(_Thresholds(stale_window=5, stale_similarity=0.7, max_turns=20))
    d.record_tool_call("web_search", {"query": "Tokyo population 2026"}, 0)
    events = d.record_tool_call("web_search", {"query": "Tokyo population 2026 current"}, 1)
    stale = [e for e in events if e.signal == DriftSignal.STALE_SEARCH]
    assert len(stale) == 1


def test_stale_search_no_fire_on_different_query():
    d = DriftDetector(_Thresholds(stale_window=5, stale_similarity=0.7, max_turns=20))
    d.record_tool_call("web_search", {"query": "Tokyo population 2026"}, 0)
    events = d.record_tool_call("web_search", {"query": "Rust vs Python benchmarks"}, 1)
    stale = [e for e in events if e.signal == DriftSignal.STALE_SEARCH]
    assert stale == []


def test_stale_search_respects_window():
    d = DriftDetector(_Thresholds(stale_window=2, stale_similarity=0.7, max_turns=20))
    d.record_tool_call("web_search", {"query": "Tokyo population 2026"}, 0)
    # Push it out of window
    d.record_tool_call("web_search", {"query": "alpha beta gamma"}, 1)
    d.record_tool_call("web_search", {"query": "delta epsilon zeta"}, 2)
    # Now repeat — should be outside window
    events = d.record_tool_call("web_search", {"query": "Tokyo population 2026"}, 3)
    stale = [e for e in events if e.signal == DriftSignal.STALE_SEARCH]
    assert stale == []


# ── Empty result ignored ──


def test_empty_result_ignored_fires():
    d = DriftDetector(_Thresholds(max_turns=20))
    # First search
    d.record_tool_call("web_search", {"query": "q1"}, 0)
    # Mark it as having results
    d.record_tool_result("web_search", "Result: https://example.com ...", False, 0)
    # Second search without scraping in between
    events = d.record_tool_call("web_search", {"query": "q2"}, 1)
    ignored = [e for e in events if e.signal == DriftSignal.EMPTY_RESULT_IGNORED]
    assert len(ignored) == 1


def test_empty_result_ignored_no_fire_after_scrape():
    d = DriftDetector(_Thresholds(max_turns=20))
    d.record_tool_call("web_search", {"query": "q1"}, 0)
    d.record_tool_result("web_search", "Result: https://example.com ...", False, 0)
    # Scrape in between
    d.record_tool_call("scrape_url", {"url": "https://example.com"}, 1)
    d.record_tool_result("scrape_url", "Page content...", False, 1)
    # Now another search is fine
    events = d.record_tool_call("web_search", {"query": "q2"}, 2)
    ignored = [e for e in events if e.signal == DriftSignal.EMPTY_RESULT_IGNORED]
    assert ignored == []


def test_empty_result_ignored_no_fire_when_search_errored():
    d = DriftDetector(_Thresholds(max_turns=20))
    d.record_tool_call("web_search", {"query": "q1"}, 0)
    d.record_tool_result("web_search", "Error: connection timeout", True, 0)
    events = d.record_tool_call("web_search", {"query": "q1 retry"}, 1)
    ignored = [e for e in events if e.signal == DriftSignal.EMPTY_RESULT_IGNORED]
    assert ignored == []


# ── Turn budget waste ──


def test_turn_budget_waste_fires():
    d = DriftDetector(_Thresholds(budget_waste_threshold=0.5, max_turns=10))
    # 6 calls to web_search = 60% of 10-turn budget
    for i in range(6):
        d.record_tool_call("web_search", {"query": f"q{i}"}, i)
    events = d.get_events()
    waste = [e for e in events if e.signal == DriftSignal.TURN_BUDGET_WASTE]
    assert len(waste) == 1


def test_turn_budget_waste_no_fire_below():
    d = DriftDetector(_Thresholds(budget_waste_threshold=0.5, max_turns=20))
    # 5 calls to web_search = 25% of 20-turn budget — under threshold
    for i in range(5):
        d.record_tool_call("web_search", {"query": f"q{i}"}, i)
    events = d.get_events()
    waste = [e for e in events if e.signal == DriftSignal.TURN_BUDGET_WASTE]
    assert waste == []


# ── Error repetition ──


def test_error_repetition_fires_at_threshold():
    d = DriftDetector(_Thresholds(error_repeat_threshold=3, max_turns=20))
    for i in range(3):
        d.record_tool_call("scrape_url", {"url": "http://fail"}, i)
        d.record_tool_result("scrape_url", "Error: timeout", True, i)
    events = d.get_events()
    errors = [e for e in events if e.signal == DriftSignal.ERROR_REPETITION]
    assert len(errors) == 1
    assert errors[0].severity == Severity.WARNING


def test_error_repetition_lite_lower_threshold():
    d = DriftDetector(_Thresholds(error_repeat_threshold=2, max_turns=10))
    for i in range(2):
        d.record_tool_call("scrape_url", {"url": "http://fail"}, i)
        d.record_tool_result("scrape_url", "Error: timeout", True, i)
    events = d.get_events()
    errors = [e for e in events if e.signal == DriftSignal.ERROR_REPETITION]
    assert len(errors) == 1


def test_error_repetition_resets_on_success():
    d = DriftDetector(_Thresholds(error_repeat_threshold=3, max_turns=20))
    # 2 errors, 1 success, 2 more errors — should NOT fire
    d.record_tool_call("scrape_url", {"url": "http://fail"}, 0)
    d.record_tool_result("scrape_url", "Error: timeout", True, 0)
    d.record_tool_call("scrape_url", {"url": "http://fail"}, 1)
    d.record_tool_result("scrape_url", "Error: timeout", True, 1)
    d.record_tool_call("scrape_url", {"url": "http://ok"}, 2)
    d.record_tool_result("scrape_url", "Page content", False, 2)
    d.record_tool_call("scrape_url", {"url": "http://fail"}, 3)
    d.record_tool_result("scrape_url", "Error: timeout", True, 3)
    d.record_tool_call("scrape_url", {"url": "http://fail"}, 4)
    d.record_tool_result("scrape_url", "Error: timeout", True, 4)
    events = d.get_events()
    errors = [e for e in events if e.signal == DriftSignal.ERROR_REPETITION]
    assert errors == []


# ── Deduplication ──


def test_dedup_same_signal_same_tool():
    d = DriftDetector(_Thresholds(allowed_tools={"web_search"}, max_turns=20))
    events1 = d.record_tool_call("task", {}, 0)
    events2 = d.record_tool_call("task", {}, 1)
    assert len(events1) == 1
    assert events2 == []  # Deduplicated


def test_dedup_same_signal_different_tool():
    allowed = {"web_search"}
    d = DriftDetector(_Thresholds(allowed_tools=allowed, max_turns=20))
    events1 = d.record_tool_call("task", {}, 0)
    events2 = d.record_tool_call("edit_file", {}, 1)
    assert len(events1) == 1
    assert len(events2) == 1  # Different tool, fires again


# ── Factory ──


def test_factory_lite():
    profile = {"name": "lite", "max_turns": 10}
    d = create_drift_detector(profile)
    # Should have lite thresholds — forbidden tool fires for "task"
    events = d.record_tool_call("task", {}, 0)
    assert any(e.signal == DriftSignal.FORBIDDEN_TOOL for e in events)


def test_factory_full():
    profile = {"name": "full", "max_turns": 20}
    d = create_drift_detector(profile)
    # Full profile: all tools allowed, task should not trigger forbidden
    events = d.record_tool_call("task", {}, 0)
    assert not any(e.signal == DriftSignal.FORBIDDEN_TOOL for e in events)


# ── Summary ──


def test_summary_structure():
    d = DriftDetector(_Thresholds(max_turns=20))
    d.record_tool_call("web_search", {"query": "test"}, 0)
    d.record_tool_call("scrape_url", {"url": "http://x"}, 1)
    summary = d.get_summary()

    assert summary["total_calls"] == 2
    assert "web_search" in summary["distribution"]
    assert summary["distribution"]["web_search"]["count"] == 1
    assert summary["distribution"]["web_search"]["pct"] == 50
    assert isinstance(summary["events"], list)


def test_summary_empty():
    d = DriftDetector()
    summary = d.get_summary()
    assert summary["total_calls"] == 0
    assert summary["distribution"] == {}
    assert summary["drift_events"] == 0


# ── get_events filtering ──


def test_get_events_severity_filter():
    allowed = {"web_search"}
    d = DriftDetector(_Thresholds(allowed_tools=allowed, max_turns=20))
    # forbidden_tool = CRITICAL
    d.record_tool_call("task", {}, 0)
    # Make a search that produces empty_result_ignored = INFO
    d.record_tool_call("web_search", {"query": "q1"}, 1)
    d.record_tool_result("web_search", "Results: https://example.com/tokyo-population ...", False, 1)
    d.record_tool_call("web_search", {"query": "q2"}, 2)

    all_events = d.get_events()
    assert len(all_events) >= 2

    critical_only = d.get_events(min_severity=Severity.CRITICAL)
    assert all(e.severity == Severity.CRITICAL for e in critical_only)
    assert len(critical_only) < len(all_events)
