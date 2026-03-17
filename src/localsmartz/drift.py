"""Model drift detection — observability for behavioral patterns in local LLMs.

Small models (8B) exhibit drift patterns during research: tool monopoly,
forbidden tool usage, stale searches, ignored results, budget waste, and
error repetition. This module detects these patterns without blocking —
pure observer that returns events for logging/display.

Drift signals:
    tool_monopoly       — >60% calls to one tool (lite: >50%)
    forbidden_tool      — tool not in profile's allowed set
    stale_search        — web_search with >70% word overlap to recent query
    empty_result_ignored — got results but searches again instead of scraping
    turn_budget_waste   — >50% of max_turns on one tool (lite: >40%)
    error_repetition    — same tool errors 3x consecutively (lite: 2x)
"""

from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DriftSignal(Enum):
    TOOL_MONOPOLY = "tool_monopoly"
    FORBIDDEN_TOOL = "forbidden_tool"
    STALE_SEARCH = "stale_search"
    EMPTY_RESULT_IGNORED = "empty_result_ignored"
    TURN_BUDGET_WASTE = "turn_budget_waste"
    ERROR_REPETITION = "error_repetition"


@dataclass
class DriftEvent:
    signal: DriftSignal
    severity: Severity
    tool: str
    message: str
    turn: int


@dataclass
class _Thresholds:
    monopoly_threshold: float = 0.6
    budget_waste_threshold: float = 0.5
    error_repeat_threshold: int = 3
    stale_window: int = 5
    stale_similarity: float = 0.7
    allowed_tools: set[str] | None = None  # None = all allowed
    max_turns: int = 20


# Lite tools — no task, ls, edit_file
_LITE_ALLOWED = {
    "web_search", "scrape_url", "read_text_file", "python_exec",
    "create_report", "write_todos", "write_file", "read_file",
}


class DriftDetector:
    """Observer for model behavioral drift during research.

    Non-blocking — returns list of DriftEvent on each record call.
    Deduplicates: each (signal, tool) pair fires at most once.
    """

    def __init__(self, thresholds: _Thresholds | None = None):
        self._t = thresholds or _Thresholds()
        self._tool_calls: list[tuple[str, dict, int]] = []  # (name, args, turn)
        self._tool_counts: dict[str, int] = {}
        self._search_queries: list[str] = []  # recent web_search queries
        self._last_search_had_results: bool = False
        self._consecutive_errors: list[str] = []  # tool names of consecutive errors
        self._fired: set[tuple[DriftSignal, str]] = set()  # dedup key
        self._events: list[DriftEvent] = []

    def record_tool_call(self, name: str, args: dict | None, turn: int) -> list[DriftEvent]:
        """Record a tool call. Returns any new drift events detected."""
        args = args or {}
        self._tool_calls.append((name, args, turn))
        self._tool_counts[name] = self._tool_counts.get(name, 0) + 1

        new_events: list[DriftEvent] = []

        # 1. Forbidden tool
        if self._t.allowed_tools is not None and name not in self._t.allowed_tools:
            new_events.extend(self._fire(
                DriftSignal.FORBIDDEN_TOOL, Severity.CRITICAL, name, turn,
                f"{name} not in allowed tools for this profile",
            ))

        # 2. Tool monopoly — check after >=5 calls
        total = len(self._tool_calls)
        if total >= 5:
            ratio = self._tool_counts[name] / total
            if ratio > self._t.monopoly_threshold:
                count = self._tool_counts[name]
                pct = int(ratio * 100)
                new_events.extend(self._fire(
                    DriftSignal.TOOL_MONOPOLY, Severity.WARNING, name, turn,
                    f"{name}: {pct}% of calls ({count}/{total})",
                ))

        # 3. Turn budget waste
        if self._t.max_turns > 0:
            ratio = self._tool_counts[name] / self._t.max_turns
            if ratio > self._t.budget_waste_threshold:
                count = self._tool_counts[name]
                new_events.extend(self._fire(
                    DriftSignal.TURN_BUDGET_WASTE, Severity.WARNING, name, turn,
                    f"{name}: {count}/{self._t.max_turns} turns ({int(ratio * 100)}% of budget)",
                ))

        # 4. Stale search — only for web_search
        if name == "web_search":
            query = args.get("query", "")
            if query:
                # Check against recent queries in window
                window = self._search_queries[-self._t.stale_window:]
                for prev_query in window:
                    sim = _jaccard_similarity(query, prev_query)
                    if sim > self._t.stale_similarity:
                        new_events.extend(self._fire(
                            DriftSignal.STALE_SEARCH, Severity.WARNING, name, turn,
                            f"query '{query[:40]}' is {int(sim * 100)}% similar to recent search",
                        ))
                        break
                self._search_queries.append(query)

        # 5. Empty result ignored — if last search had results and now searching again
        if name == "web_search" and self._last_search_had_results:
            # Only fire if the previous call was also web_search (no scrape in between)
            if len(self._tool_calls) >= 2:
                prev_name = self._tool_calls[-2][0]
                if prev_name == "web_search":
                    new_events.extend(self._fire(
                        DriftSignal.EMPTY_RESULT_IGNORED, Severity.INFO, name, turn,
                        f"previous web_search returned results but model searched again instead of scraping",
                    ))

        self._events.extend(new_events)
        return new_events

    def record_tool_result(self, name: str, content: str, is_error: bool, turn: int) -> list[DriftEvent]:
        """Record a tool result. Returns any new drift events detected."""
        new_events: list[DriftEvent] = []

        # Track whether web_search returned useful results
        if name == "web_search":
            self._last_search_had_results = not is_error and bool(content and len(content) > 20)

        # Error repetition
        if is_error:
            self._consecutive_errors.append(name)
            # Check for consecutive errors from same tool
            threshold = self._t.error_repeat_threshold
            if len(self._consecutive_errors) >= threshold:
                recent = self._consecutive_errors[-threshold:]
                if all(t == name for t in recent):
                    severity = Severity.CRITICAL if len(self._consecutive_errors) > threshold else Severity.WARNING
                    new_events.extend(self._fire(
                        DriftSignal.ERROR_REPETITION, severity, name, turn,
                        f"{name} errored {threshold}x consecutively",
                    ))
        else:
            self._consecutive_errors.clear()

        self._events.extend(new_events)
        return new_events

    def get_events(self, min_severity: Severity | None = None) -> list[DriftEvent]:
        """Get all recorded drift events, optionally filtered by minimum severity."""
        if min_severity is None:
            return list(self._events)

        severity_order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
        min_level = severity_order[min_severity]
        return [e for e in self._events if severity_order[e.severity] >= min_level]

    def get_summary(self) -> dict:
        """Get a summary of tool usage and drift events."""
        total = len(self._tool_calls)
        distribution = {}
        for name, count in sorted(self._tool_counts.items(), key=lambda x: -x[1]):
            distribution[name] = {
                "count": count,
                "pct": round(count / total * 100) if total > 0 else 0,
            }

        return {
            "total_calls": total,
            "distribution": distribution,
            "drift_events": len(self._events),
            "events": [
                {
                    "signal": e.signal.value,
                    "severity": e.severity.value,
                    "tool": e.tool,
                    "message": e.message,
                    "turn": e.turn,
                }
                for e in self._events
            ],
        }

    def _fire(
        self,
        signal: DriftSignal,
        severity: Severity,
        tool: str,
        turn: int,
        message: str,
    ) -> list[DriftEvent]:
        """Create a drift event if not already fired for this (signal, tool) pair."""
        key = (signal, tool)
        if key in self._fired:
            return []
        self._fired.add(key)
        return [DriftEvent(signal=signal, severity=severity, tool=tool, message=message, turn=turn)]


def _jaccard_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between word sets of two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def create_drift_detector(profile: dict) -> DriftDetector:
    """Factory — creates a DriftDetector with profile-aware thresholds.

    Args:
        profile: Profile dict from get_profile() — must have "name" key.

    Returns:
        DriftDetector configured for the profile's constraints.
    """
    is_lite = profile.get("name") == "lite"
    max_turns = profile.get("max_turns", 20)

    if is_lite:
        thresholds = _Thresholds(
            monopoly_threshold=0.5,
            budget_waste_threshold=0.4,
            error_repeat_threshold=2,
            stale_window=3,
            stale_similarity=0.7,
            allowed_tools=_LITE_ALLOWED,
            max_turns=max_turns,
        )
    else:
        thresholds = _Thresholds(
            monopoly_threshold=0.6,
            budget_waste_threshold=0.5,
            error_repeat_threshold=3,
            stale_window=5,
            stale_similarity=0.7,
            allowed_tools=None,  # Full profile: all tools allowed
            max_turns=max_turns,
        )

    return DriftDetector(thresholds)
