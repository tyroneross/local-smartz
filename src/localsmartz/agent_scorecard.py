"""Deterministic scorecard for local-smartz agent-system readiness.

The model eval suite answers "can this provider answer small prompts?".
This scorecard answers a different question: "is the agent harness choosing
the right execution path, specialists, coordination pattern, and audit surface
for local-first multi-agent work?"

V1 is intentionally deterministic. LLM-as-judge can be layered on later, but
the first guardrail should be cheap enough to run after every routing or
pattern change.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from localsmartz.agent_role_doe import AGENT_ROLE_DOE_CASES, run_agent_role_doe
from localsmartz.routing import AgentRole, ResearchRuntime, select_agent_roles


TierName = str

_TIER_RANK: dict[TierName, int] = {
    "mini": 0,
    "standard": 1,
    "full": 2,
}

_PATTERN_MIN_TIER: dict[str, TierName] = {
    "single": "mini",
    "chain": "mini",
    "router": "mini",
    "critic_loop": "mini",
    "reflection": "mini",
    "parallel": "mini",
    "orchestrator": "standard",
}

_RECOMMENDED_PATTERN_BY_CASE: dict[str, str] = {
    "fast_math": "single",
    "current_stock": "chain",
    "compare_languages": "critic_loop",
    "summarize_doc": "chain",
    "csv_chart": "chain",
    "citations": "chain",
    "model_choice": "critic_loop",
    "debug_launch": "reflection",
    "write_report": "critic_loop",
    "analyze_earnings": "chain",
}

_CASE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "fast_math": ("routing", "latency"),
    "current_stock": ("freshness", "source_quality", "trajectory"),
    "compare_languages": ("reasoning", "critique", "decision_quality"),
    "summarize_doc": ("context", "compression", "artifact_hygiene"),
    "csv_chart": ("tool_use", "analysis", "artifact_hygiene"),
    "citations": ("source_quality", "guardrails", "traceability"),
    "model_choice": ("decision_quality", "local_safety", "critique"),
    "debug_launch": ("state", "traceability", "recovery"),
    "write_report": ("structure", "critique", "quality"),
    "analyze_earnings": ("analysis", "freshness", "source_quality"),
}

RESEARCH_BASIS: tuple[dict[str, str], ...] = (
    {
        "source": "LLM wiki agent-state architecture",
        "finding": "Score traceability, cost, quality, tool patterns, latency, and guardrail decisions.",
    },
    {
        "source": "LLM wiki local-first multi-agent architecture",
        "finding": "Prefer single, chain, routing, and evaluator-optimizer locally; gate orchestrator-workers by tier.",
    },
    {
        "source": "LangChain AgentEvals",
        "finding": "Use deterministic trajectory checks when expected tool/role paths are known; reserve LLM judges for open quality.",
    },
    {
        "source": "OpenAI Agents SDK tracing and guardrails",
        "finding": "Agent runs should expose spans for generations, tools, handoffs, guardrails, and custom events.",
    },
    {
        "source": "Anthropic effective agents and multi-agent research",
        "finding": "Start simple, evaluate early on small samples, and scale agent effort only when task value justifies cost.",
    },
)


@dataclass(frozen=True)
class AgentScorecardCase:
    name: str
    prompt: str
    expected_runtime: ResearchRuntime
    expected_roles: tuple[AgentRole, ...]
    recommended_pattern: str
    weight: float
    dimensions: tuple[str, ...]


@dataclass(frozen=True)
class AgentScorecardRow:
    name: str
    prompt: str
    expected_runtime: str
    actual_runtime: str
    runtime_ok: bool
    expected_roles: tuple[AgentRole, ...]
    actual_roles: tuple[AgentRole, ...]
    missing_roles: tuple[AgentRole, ...]
    extra_roles: tuple[AgentRole, ...]
    roles_ok: bool
    recommended_pattern: str
    pattern_available: bool
    pattern_tier_ok: bool
    score: float
    weight: float
    dimensions: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return (
            self.runtime_ok
            and self.roles_ok
            and self.pattern_available
            and self.pattern_tier_ok
            and self.score >= 90.0
        )


@dataclass(frozen=True)
class AgentScorecardCheck:
    name: str
    category: str
    ok: bool
    score: float
    weight: float
    evidence: str
    recommendation: str
    critical: bool = True


@dataclass(frozen=True)
class AgentScorecardResult:
    score: float
    grade: str
    tier: TierName
    rows: tuple[AgentScorecardRow, ...]
    checks: tuple[AgentScorecardCheck, ...]
    research_basis: tuple[dict[str, str], ...]

    @property
    def pass_count(self) -> int:
        return sum(1 for row in self.rows if row.ok)

    @property
    def fail_count(self) -> int:
        return len(self.rows) - self.pass_count

    @property
    def check_pass_count(self) -> int:
        return sum(1 for check in self.checks if check.ok)

    @property
    def check_fail_count(self) -> int:
        return len(self.checks) - self.check_pass_count


def _default_runtime_selector(prompt: str) -> ResearchRuntime:
    from localsmartz.routing import select_research_runtime

    return select_research_runtime(prompt)


def _current_tier() -> TierName:
    try:
        from localsmartz.profiles import detect_tier

        tier = str(detect_tier().get("tier", "mini") or "mini")
    except Exception:  # noqa: BLE001
        tier = "mini"
    return tier if tier in _TIER_RANK else "mini"


def _default_cases() -> tuple[AgentScorecardCase, ...]:
    return tuple(
        AgentScorecardCase(
            name=case.name,
            prompt=case.prompt,
            expected_runtime="fast_path" if case.name == "fast_math" else "graph_pipeline",
            expected_roles=case.expected_roles,
            recommended_pattern=_RECOMMENDED_PATTERN_BY_CASE[case.name],
            weight=case.weight,
            dimensions=_CASE_DIMENSIONS.get(case.name, ()),
        )
        for case in AGENT_ROLE_DOE_CASES
    )


AGENT_SCORECARD_CASES: tuple[AgentScorecardCase, ...] = _default_cases()


def _pattern_allowed(pattern: str, tier: TierName) -> bool:
    min_tier = _PATTERN_MIN_TIER.get(pattern, "mini")
    return _TIER_RANK.get(tier, 0) >= _TIER_RANK.get(min_tier, 0)


def _role_score(
    expected: tuple[AgentRole, ...],
    actual: tuple[AgentRole, ...],
) -> float:
    missing = [role for role in expected if role not in actual]
    extra = [role for role in actual if role not in expected]
    if not missing and not extra:
        return 1.0
    denominator = max(1, len(expected))
    penalty = (len(missing) * 0.5 + len(extra) * 0.25) / denominator
    return max(0.0, 1.0 - penalty)


def _score_row(
    *,
    runtime_ok: bool,
    role_score: float,
    pattern_available: bool,
    pattern_tier_ok: bool,
) -> float:
    score = (
        (1.0 if runtime_ok else 0.0) * 0.35
        + role_score * 0.35
        + (1.0 if pattern_available else 0.0) * 0.20
        + (1.0 if pattern_tier_ok else 0.0) * 0.10
    )
    return round(score * 100.0, 3)


def _system_checks(registry: dict[str, dict]) -> tuple[AgentScorecardCheck, ...]:
    from localsmartz import benchmarking, observability
    from localsmartz.runners import AgentRunner

    available = set(registry)
    expected_patterns = {
        "single",
        "chain",
        "router",
        "critic_loop",
        "reflection",
        "orchestrator",
        "parallel",
    }
    role_doe = run_agent_role_doe(repetitions=1)
    orchestrator_description = str(registry.get("orchestrator", {}).get("description", ""))

    runner_ok = callable(getattr(AgentRunner, "run_turn", None)) and all(
        callable(spec.get("run")) for spec in registry.values()
    )
    trace_ok = callable(getattr(observability, "get_tracer", None)) and callable(
        getattr(observability, "redact_pii_attributes", None)
    )
    golden_ok = len(getattr(benchmarking, "GOLDEN_TASKS", ())) >= 3 and callable(
        getattr(benchmarking, "_grade_reply", None)
    )

    checks = [
        AgentScorecardCheck(
            name="pattern_registry_coverage",
            category="trajectory",
            ok=expected_patterns.issubset(available),
            score=100.0 if expected_patterns.issubset(available) else 0.0,
            weight=1.2,
            evidence=f"available={sorted(available)}",
            recommendation="Keep all shipped pattern names discoverable through localsmartz.patterns.REGISTRY.",
        ),
        AgentScorecardCheck(
            name="deterministic_role_contract",
            category="trajectory",
            ok=float(role_doe.get("weighted_accuracy", 0.0)) >= 1.0,
            score=100.0 if float(role_doe.get("weighted_accuracy", 0.0)) >= 1.0 else 0.0,
            weight=1.2,
            evidence=f"weighted_accuracy={role_doe.get('weighted_accuracy')}",
            recommendation="Fix select_agent_roles before pruning or rerouting graph roles.",
        ),
        AgentScorecardCheck(
            name="runner_abstraction",
            category="open_source_harness",
            ok=runner_ok,
            score=100.0 if runner_ok else 0.0,
            weight=1.0,
            evidence="patterns consume localsmartz.runners.AgentRunner instead of provider SDKs directly",
            recommendation="Keep provider-specific code behind runners.",
        ),
        AgentScorecardCheck(
            name="audit_trace_hooks",
            category="observability",
            ok=trace_ok,
            score=100.0 if trace_ok else 0.0,
            weight=1.1,
            evidence="get_tracer and PII redaction are available for OTel-compatible spans",
            recommendation="Keep spans and redaction available before adding deeper multi-agent workflows.",
        ),
        AgentScorecardCheck(
            name="local_safety_gates",
            category="local_model_safety",
            ok="debate" not in available and "Requires standard+" in orchestrator_description,
            score=100.0
            if "debate" not in available and "Requires standard+" in orchestrator_description
            else 0.0,
            weight=1.1,
            evidence=f"debate_present={'debate' in available}; orchestrator_description={orchestrator_description}",
            recommendation="Keep debate deferred and require tier gates for orchestrator-workers.",
        ),
        AgentScorecardCheck(
            name="golden_answer_harness",
            category="outcome_quality",
            ok=golden_ok,
            score=100.0 if golden_ok else 0.0,
            weight=0.9,
            evidence=f"golden_tasks={len(getattr(benchmarking, 'GOLDEN_TASKS', ()))}",
            recommendation="Keep answer-quality smoke tests separate from the agent-trajectory scorecard.",
        ),
    ]
    return tuple(checks)


def run_agent_scorecard(
    *,
    cases: Iterable[AgentScorecardCase] = AGENT_SCORECARD_CASES,
    runtime_selector: Callable[[str], str] = _default_runtime_selector,
    role_selector: Callable[[str], tuple[AgentRole, ...]] = select_agent_roles,
    tier: TierName | None = None,
    registry: dict[str, dict] | None = None,
) -> AgentScorecardResult:
    """Run the deterministic agent-system scorecard."""
    from localsmartz.patterns import REGISTRY

    selected_tier = tier or _current_tier()
    if selected_tier not in _TIER_RANK:
        selected_tier = "mini"
    active_registry = registry or REGISTRY

    rows: list[AgentScorecardRow] = []
    for case in tuple(cases):
        actual_runtime = str(runtime_selector(case.prompt))
        actual_roles = role_selector(case.prompt)
        missing = tuple(role for role in case.expected_roles if role not in actual_roles)
        extra = tuple(role for role in actual_roles if role not in case.expected_roles)
        role_ok = not missing and not extra
        pattern_available = case.recommended_pattern in active_registry
        pattern_tier_ok = _pattern_allowed(case.recommended_pattern, selected_tier)
        rows.append(
            AgentScorecardRow(
                name=case.name,
                prompt=case.prompt,
                expected_runtime=case.expected_runtime,
                actual_runtime=actual_runtime,
                runtime_ok=actual_runtime == case.expected_runtime,
                expected_roles=case.expected_roles,
                actual_roles=actual_roles,
                missing_roles=missing,
                extra_roles=extra,
                roles_ok=role_ok,
                recommended_pattern=case.recommended_pattern,
                pattern_available=pattern_available,
                pattern_tier_ok=pattern_tier_ok,
                score=_score_row(
                    runtime_ok=actual_runtime == case.expected_runtime,
                    role_score=_role_score(case.expected_roles, actual_roles),
                    pattern_available=pattern_available,
                    pattern_tier_ok=pattern_tier_ok,
                ),
                weight=case.weight,
                dimensions=case.dimensions,
            )
        )

    checks = _system_checks(active_registry)
    total_weight = sum(row.weight for row in rows) + sum(check.weight for check in checks)
    weighted = sum(row.score * row.weight for row in rows)
    weighted += sum(check.score * check.weight for check in checks)
    overall = round(weighted / total_weight, 3) if total_weight else 0.0
    critical_failures = [
        check for check in checks if check.critical and not check.ok
    ]
    if overall >= 90.0 and all(row.ok for row in rows) and not critical_failures:
        grade = "pass"
    elif overall >= 75.0:
        grade = "warn"
    else:
        grade = "fail"

    return AgentScorecardResult(
        score=overall,
        grade=grade,
        tier=selected_tier,
        rows=tuple(rows),
        checks=checks,
        research_basis=RESEARCH_BASIS,
    )


def scorecard_to_dict(result: AgentScorecardResult) -> dict[str, object]:
    return {
        "score": result.score,
        "grade": result.grade,
        "tier": result.tier,
        "pass": result.pass_count,
        "fail": result.fail_count,
        "check_pass": result.check_pass_count,
        "check_fail": result.check_fail_count,
        "rows": [asdict(row) for row in result.rows],
        "checks": [asdict(check) for check in result.checks],
        "research_basis": list(result.research_basis),
    }


def write_agent_scorecard_md(result: AgentScorecardResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Agent System Scorecard",
        "",
        f"- **Score:** {result.score:.3f}",
        f"- **Grade:** {result.grade}",
        f"- **Hardware tier:** {result.tier}",
        f"- **Task contract:** {result.pass_count} pass / {result.fail_count} fail",
        f"- **System checks:** {result.check_pass_count} pass / {result.check_fail_count} fail",
        "",
        "## Task Contract",
        "",
        "| case | score | runtime | roles | pattern | dimensions |",
        "|---|---:|---|---|---|---|",
    ]
    for row in result.rows:
        runtime = f"{row.actual_runtime} ({'ok' if row.runtime_ok else 'fail'})"
        roles = ",".join(row.actual_roles) if row.actual_roles else "none"
        pattern = (
            f"{row.recommended_pattern}"
            f" ({'ok' if row.pattern_available and row.pattern_tier_ok else 'fail'})"
        )
        lines.append(
            f"| {row.name} | {row.score:.1f} | {runtime} | {roles} | "
            f"{pattern} | {', '.join(row.dimensions)} |"
        )

    lines.extend([
        "",
        "## System Checks",
        "",
        "| check | category | score | evidence |",
        "|---|---|---:|---|",
    ])
    for check in result.checks:
        evidence = check.evidence.replace("|", "/")
        lines.append(
            f"| {check.name} | {check.category} | {check.score:.1f} | {evidence} |"
        )

    lines.extend(["", "## Research Basis", ""])
    for item in result.research_basis:
        lines.append(f"- **{item['source']}:** {item['finding']}")
    lines.append("")
    path.write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="localsmartz-agent-scorecard",
        description="Score local-smartz agent routing, roles, patterns, and audit readiness.",
    )
    parser.add_argument("--tier", choices=sorted(_TIER_RANK), default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--stamp", default=None)
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Exit non-zero if the overall score falls below this value.",
    )
    args = parser.parse_args(argv)

    result = run_agent_scorecard(tier=args.tier)
    payload = scorecard_to_dict(result)
    if args.output:
        out = Path(args.output)
    elif args.markdown:
        stamp = args.stamp or time.strftime("%Y-%m-%d", time.gmtime())
        out = Path(".build-loop/evals") / f"{stamp}-agent-scorecard.md"
    else:
        out = None

    if out is not None:
        if args.markdown or out.suffix.lower() == ".md":
            write_agent_scorecard_md(result, out)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2) + "\n")

    if args.score_only:
        print(result.score)
    elif args.markdown and out is None:
        lines_path = Path("/tmp/localsmartz-agent-scorecard.md")
        write_agent_scorecard_md(result, lines_path)
        print(lines_path.read_text())
        try:
            lines_path.unlink()
        except OSError:
            pass
    elif not args.markdown or args.json:
        print(json.dumps(payload, indent=2))
    elif out is not None:
        print(f"Wrote {out}")

    if args.min_score is not None and result.score < args.min_score:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
