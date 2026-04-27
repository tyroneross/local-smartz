"""Deterministic DOE score for query routing quality and speed.

The production latency win comes from sending obvious one-turn prompts through
the fast path while keeping research, current-data, local-context, and
composition tasks on the agent graph. This harness scores that boundary without
calling an LLM or starting the server.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

from localsmartz.profiles import is_fast_path


@dataclass(frozen=True)
class QueryDOECase:
    name: str
    prompt: str
    expected_fast_path: bool
    weight: float
    factors: tuple[str, ...]


QUERY_DOE_CASES: tuple[QueryDOECase, ...] = (
    QueryDOECase("greeting", "hello", True, 0.7, ("short", "conversation")),
    QueryDOECase("simple_math", "what is 2+2?", True, 1.0, ("short", "math")),
    QueryDOECase("tip_math", "calculate 18% tip on $42", True, 1.0, ("short", "math")),
    QueryDOECase("unit_conversion", "convert 12 miles to kilometers", True, 0.8, ("short", "math")),
    QueryDOECase("definition", "define entropy", True, 1.0, ("short", "factual")),
    QueryDOECase("meaning", "meaning of serendipity", True, 0.9, ("short", "factual")),
    QueryDOECase("author", "who wrote Hamlet?", True, 1.0, ("short", "factual")),
    QueryDOECase("capital", "what's the capital of Peru?", True, 1.0, ("short", "factual")),
    QueryDOECase("date_fact", "when did ww2 start?", True, 1.0, ("short", "factual")),
    QueryDOECase(
        "count_fact",
        "how many planets are in the solar system?",
        True,
        1.0,
        ("short", "factual"),
    ),
    QueryDOECase("fun_fact", "give me a fun fact", True, 0.7, ("short", "conversation")),
    QueryDOECase("place_fact", "where is Machu Picchu?", True, 0.9, ("short", "factual")),
    QueryDOECase(
        "research_competitors",
        "research competitors for Stripe",
        False,
        1.5,
        ("research", "market"),
    ),
    QueryDOECase(
        "analysis_finance",
        "analyze the Q3 earnings",
        False,
        1.4,
        ("analysis", "finance"),
    ),
    QueryDOECase(
        "comparison",
        "compare Python and Rust for a backend API",
        False,
        1.4,
        ("analysis", "comparison"),
    ),
    QueryDOECase(
        "summarize_document",
        "summarize this document",
        False,
        1.5,
        ("local_context", "summarization"),
    ),
    QueryDOECase(
        "write_report",
        "write a report on climate change",
        False,
        1.4,
        ("composition", "research"),
    ),
    QueryDOECase(
        "latest_stock",
        "what is the latest price of Apple stock?",
        False,
        2.0,
        ("current_data", "finance", "factual_prefix"),
    ),
    QueryDOECase(
        "current_ceo",
        "who is the current CEO of OpenAI?",
        False,
        1.8,
        ("current_data", "factual_prefix"),
    ),
    QueryDOECase(
        "today_news",
        "what happened today in AI news?",
        False,
        2.0,
        ("current_data", "news", "factual_prefix"),
    ),
    QueryDOECase(
        "buying_recommendation",
        "what's the best laptop to buy this month?",
        False,
        1.6,
        ("current_data", "recommendation", "factual_prefix"),
    ),
    QueryDOECase(
        "recent_sources",
        "look up recent sources on local LLM routing",
        False,
        1.7,
        ("current_data", "research"),
    ),
    QueryDOECase(
        "csv_analysis",
        "pull data from this CSV and chart revenue",
        False,
        1.6,
        ("local_context", "analysis"),
    ),
    QueryDOECase(
        "path_summary",
        "read /Users/me/project/README.md and summarize risks",
        False,
        1.7,
        ("local_context", "summarization"),
    ),
    QueryDOECase(
        "multi_question",
        "What is A? What is B? What is C?",
        False,
        1.2,
        ("multi_question", "factual_prefix"),
    ),
    QueryDOECase(
        "citations",
        "find citations for claims about local model latency",
        False,
        1.6,
        ("research", "citations"),
    ),
    QueryDOECase(
        "model_selection",
        "which model should I use for local agents, qwen or gpt-oss?",
        False,
        1.4,
        ("recommendation", "model_selection"),
    ),
    QueryDOECase(
        "debug_app",
        "debug why the macOS app is stuck launching",
        False,
        1.3,
        ("debugging", "local_context"),
    ),
)


@dataclass(frozen=True)
class QueryDOERow:
    name: str
    ok: bool
    expected_fast_path: bool
    actual_fast_path: bool
    weight: float
    factors: tuple[str, ...]
    prompt: str


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return ordered[idx]


def _measure_classifier_us(
    classifier: Callable[[str], bool],
    cases: Iterable[QueryDOECase],
    *,
    repetitions: int,
) -> dict[str, float]:
    timings: list[float] = []
    materialized = tuple(cases)
    for _ in range(repetitions):
        for case in materialized:
            started = time.perf_counter_ns()
            classifier(case.prompt)
            timings.append((time.perf_counter_ns() - started) / 1000.0)
    return {
        "median_us": statistics.median(timings) if timings else 0.0,
        "p95_us": _percentile(timings, 0.95),
        "max_us": max(timings) if timings else 0.0,
    }


def run_query_doe(
    classifier: Callable[[str], bool] = is_fast_path,
    *,
    cases: Iterable[QueryDOECase] = QUERY_DOE_CASES,
    repetitions: int = 500,
) -> dict[str, object]:
    materialized = tuple(cases)
    rows: list[QueryDOERow] = []
    for case in materialized:
        actual = classifier(case.prompt)
        rows.append(
            QueryDOERow(
                name=case.name,
                ok=actual is case.expected_fast_path,
                expected_fast_path=case.expected_fast_path,
                actual_fast_path=actual,
                weight=case.weight,
                factors=case.factors,
                prompt=case.prompt,
            )
        )

    total_weight = sum(row.weight for row in rows)
    correct_weight = sum(row.weight for row in rows if row.ok)
    false_fast_weight = sum(
        row.weight
        for row in rows
        if row.actual_fast_path and not row.expected_fast_path
    )
    false_heavy_weight = sum(
        row.weight
        for row in rows
        if not row.actual_fast_path and row.expected_fast_path
    )
    speed = _measure_classifier_us(classifier, materialized, repetitions=repetitions)
    accuracy = correct_weight / total_weight if total_weight else 0.0

    by_factor: dict[str, dict[str, float]] = {}
    for row in rows:
        for factor in row.factors:
            bucket = by_factor.setdefault(
                factor,
                {"weight": 0.0, "correct_weight": 0.0, "accuracy": 0.0},
            )
            bucket["weight"] += row.weight
            if row.ok:
                bucket["correct_weight"] += row.weight
    for bucket in by_factor.values():
        bucket["accuracy"] = (
            bucket["correct_weight"] / bucket["weight"]
            if bucket["weight"]
            else 0.0
        )

    # Keep the metric positive because the build-loop metric parser accepts
    # unsigned numbers. The offset does not affect improvement ordering.
    score = (
        2000.0
        +
        accuracy * 1000.0
        - false_fast_weight * 100.0
        - false_heavy_weight * 20.0
        - speed["median_us"] * 5.0
        - max(0.0, speed["p95_us"] - 10.0)
    )

    return {
        "score": round(score, 3),
        "weighted_accuracy": round(accuracy, 6),
        "correct_weight": round(correct_weight, 3),
        "total_weight": round(total_weight, 3),
        "false_fast_weight": round(false_fast_weight, 3),
        "false_heavy_weight": round(false_heavy_weight, 3),
        "speed": {key: round(value, 3) for key, value in speed.items()},
        "by_factor": {
            key: {k: round(v, 6) for k, v in value.items()}
            for key, value in sorted(by_factor.items())
        },
        "rows": [asdict(row) for row in rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="localsmartz-query-doe",
        description="Score fast-path query routing accuracy and classifier speed.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=500,
        help="Classifier timing repetitions per query case.",
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="Print only the numeric optimization score.",
    )
    args = parser.parse_args(argv)

    payload = run_query_doe(repetitions=max(1, args.repetitions))
    if args.score_only:
        print(payload["score"])
    else:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
