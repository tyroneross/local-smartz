"""Deterministic DOE score for specialist role selection.

The current graph still runs a fixed role sequence for non-fast prompts. This
module makes the desired role-routing contract measurable before pruning runtime
work, so speed improvements do not silently drop required specialists.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

from localsmartz.routing import AgentRole, select_agent_roles


@dataclass(frozen=True)
class AgentRoleDOECase:
    name: str
    prompt: str
    expected_roles: tuple[AgentRole, ...]
    weight: float
    factors: tuple[str, ...]


AGENT_ROLE_DOE_CASES: tuple[AgentRoleDOECase, ...] = (
    AgentRoleDOECase("fast_math", "what is 2+2?", (), 1.0, ("fast_path",)),
    AgentRoleDOECase(
        "current_stock",
        "what is the latest price of Apple stock?",
        ("researcher", "fact_checker", "writer"),
        1.5,
        ("current_data", "research"),
    ),
    AgentRoleDOECase(
        "compare_languages",
        "compare Python and Rust for a backend API",
        ("researcher", "analyzer", "fact_checker", "writer"),
        1.3,
        ("comparison", "analysis"),
    ),
    AgentRoleDOECase(
        "summarize_doc",
        "summarize this document",
        ("researcher", "writer"),
        1.2,
        ("local_context", "summarization"),
    ),
    AgentRoleDOECase(
        "csv_chart",
        "pull data from this CSV and chart revenue",
        ("analyzer", "writer"),
        1.3,
        ("local_context", "analysis"),
    ),
    AgentRoleDOECase(
        "citations",
        "find citations for claims about local model latency",
        ("researcher", "fact_checker", "writer"),
        1.4,
        ("sources", "research"),
    ),
    AgentRoleDOECase(
        "model_choice",
        "which model should I use for local agents, qwen or gpt-oss?",
        ("researcher", "writer"),
        1.1,
        ("recommendation", "model_selection"),
    ),
    AgentRoleDOECase(
        "debug_launch",
        "debug why the macOS app is stuck launching",
        ("planner", "researcher", "analyzer", "writer"),
        1.2,
        ("debugging", "local_context"),
    ),
    AgentRoleDOECase(
        "write_report",
        "write a report on climate change",
        ("researcher", "fact_checker", "writer"),
        1.2,
        ("composition", "research"),
    ),
    AgentRoleDOECase(
        "analyze_earnings",
        "analyze the Q3 earnings",
        ("researcher", "analyzer", "fact_checker", "writer"),
        1.3,
        ("finance", "analysis"),
    ),
)


@dataclass(frozen=True)
class AgentRoleDOERow:
    name: str
    ok: bool
    expected_roles: tuple[AgentRole, ...]
    actual_roles: tuple[AgentRole, ...]
    missing_roles: tuple[AgentRole, ...]
    extra_roles: tuple[AgentRole, ...]
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
    classifier: Callable[[str], tuple[AgentRole, ...]],
    cases: Iterable[AgentRoleDOECase],
    *,
    repetitions: int,
) -> dict[str, float]:
    materialized = tuple(cases)
    timings: list[float] = []
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


def run_agent_role_doe(
    classifier: Callable[[str], tuple[AgentRole, ...]] = select_agent_roles,
    *,
    cases: Iterable[AgentRoleDOECase] = AGENT_ROLE_DOE_CASES,
    repetitions: int = 500,
) -> dict[str, object]:
    rows: list[AgentRoleDOERow] = []
    materialized = tuple(cases)
    for case in materialized:
        actual = classifier(case.prompt)
        missing = tuple(role for role in case.expected_roles if role not in actual)
        extra = tuple(role for role in actual if role not in case.expected_roles)
        rows.append(
            AgentRoleDOERow(
                name=case.name,
                ok=not missing and not extra,
                expected_roles=case.expected_roles,
                actual_roles=actual,
                missing_roles=missing,
                extra_roles=extra,
                weight=case.weight,
                factors=case.factors,
                prompt=case.prompt,
            )
        )

    total_weight = sum(row.weight for row in rows)
    correct_weight = sum(row.weight for row in rows if row.ok)
    missing_weight = sum(row.weight * len(row.missing_roles) for row in rows)
    extra_weight = sum(row.weight * len(row.extra_roles) for row in rows)
    speed = _measure_classifier_us(classifier, materialized, repetitions=repetitions)
    accuracy = correct_weight / total_weight if total_weight else 0.0
    score = (
        2000.0
        + accuracy * 1000.0
        - missing_weight * 75.0
        - extra_weight * 25.0
        - speed["median_us"] * 2.0
        - max(0.0, speed["p95_us"] - 20.0)
    )

    return {
        "score": round(score, 3),
        "weighted_accuracy": round(accuracy, 6),
        "correct_weight": round(correct_weight, 3),
        "total_weight": round(total_weight, 3),
        "missing_weight": round(missing_weight, 3),
        "extra_weight": round(extra_weight, 3),
        "speed": {key: round(value, 3) for key, value in speed.items()},
        "rows": [asdict(row) for row in rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="localsmartz-agent-role-doe",
        description="Score deterministic specialist role selection.",
    )
    parser.add_argument("--repetitions", type=int, default=500)
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Exit non-zero if the score falls below this value.",
    )
    args = parser.parse_args(argv)

    payload = run_agent_role_doe(repetitions=max(1, args.repetitions))
    score = float(payload["score"])
    if args.score_only:
        print(payload["score"])
    else:
        print(json.dumps(payload, indent=2))
    if args.min_score is not None and score < args.min_score:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
