"""Small live-answer eval matrix for local model selection.

This complements ``query_doe``: DOE scores whether a query is routed to the
right path, while this module checks whether candidate models actually answer a
small representative set correctly enough to be used as local agents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from localsmartz.runners import AgentRunner, get_runner
from localsmartz.runners.base import ModelRef


@dataclass(frozen=True)
class ModelEvalTask:
    name: str
    prompt: str
    must_contain: tuple[str, ...]
    must_not_contain: tuple[str, ...] = ()
    weight: float = 1.0
    category: str = "core"


MODEL_EVAL_TASKS: tuple[ModelEvalTask, ...] = (
    ModelEvalTask(
        name="arithmetic_exact",
        prompt="What is 15% of 2400? Respond with just the number.",
        must_contain=("360",),
        category="math",
    ),
    ModelEvalTask(
        name="format_json",
        prompt=(
            'Return ONLY compact JSON with keys "route" and "confidence". '
            'Use route="fast_path" and confidence=0.9.'
        ),
        must_contain=('"route"', "fast_path", '"confidence"', "0.9"),
        must_not_contain=("```",),
        category="format",
    ),
    ModelEvalTask(
        name="current_data_boundary",
        prompt=(
            "What is the latest closing price of Apple stock? "
            "If you do not have live market data, respond exactly: NEEDS_LIVE_DATA."
        ),
        must_contain=("needs_live_data",),
        weight=1.4,
        category="current_data",
    ),
    ModelEvalTask(
        name="negative_result",
        prompt="Does the word 'hippopotamus' contain the letter 'z'? Respond with only YES or NO.",
        must_contain=("no",),
        category="reasoning",
    ),
    ModelEvalTask(
        name="ordered_words",
        prompt="Reply with exactly three words in this order: CAT DOG BIRD.",
        must_contain=("cat dog bird",),
        weight=0.8,
        category="format",
    ),
    ModelEvalTask(
        name="agent_model_choice",
        prompt=(
            "For a local app choosing between qwen3:8b and gpt-oss:20b, "
            "name the main tradeoff in one sentence and include the word TRADEOFF."
        ),
        must_contain=("tradeoff",),
        weight=0.8,
        category="model_selection",
    ),
)


PREFERRED_LOCAL_EVAL_MODELS: tuple[str, ...] = (
    "gpt-oss:20b",
    "qwen3:8b-q4_K_M",
    "gemma4:26b",
    "llama3.2:3b",
)


@dataclass(frozen=True)
class ModelEvalTaskResult:
    task: str
    provider: str
    model: str
    ok: bool
    latency_ms: int
    reply: str
    weight: float
    category: str
    error: str | None = None


@dataclass(frozen=True)
class ModelEvalResult:
    provider: str
    model: str
    results: list[ModelEvalTaskResult]

    @property
    def pass_count(self) -> int:
        return sum(1 for result in self.results if result.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for result in self.results if not result.ok)

    @property
    def weighted_score(self) -> float:
        total = sum(result.weight for result in self.results)
        if total == 0:
            return 0.0
        passed = sum(result.weight for result in self.results if result.ok)
        return passed / total

    @property
    def median_latency_ms(self) -> float | None:
        latencies = [result.latency_ms for result in self.results if result.latency_ms >= 0]
        return float(statistics.median(latencies)) if latencies else None


def default_local_eval_models(
    installed: Iterable[str] | None = None,
    *,
    limit: int = 2,
) -> list[str]:
    """Pick a small, useful local comparison set from installed Ollama models."""
    if installed is None:
        from localsmartz.ollama import list_models

        installed = list_models()

    installed_set = set(installed)
    selected = [name for name in PREFERRED_LOCAL_EVAL_MODELS if name in installed_set]
    if len(selected) < limit:
        for name in installed:
            if name in selected:
                continue
            if "embed" in name.lower():
                continue
            selected.append(name)
            if len(selected) >= limit:
                break
    return selected[: max(1, limit)]


def _grade_reply(reply: str, task: ModelEvalTask) -> bool:
    lowered = reply.lower()
    return all(token.lower() in lowered for token in task.must_contain) and not any(
        token.lower() in lowered for token in task.must_not_contain
    )


def _model_ref(provider: str, model: str) -> ModelRef:
    ref: ModelRef = {"provider": provider, "name": model}
    if provider == "groq":
        ref["base_url"] = "https://api.groq.com/openai/v1"
    return ref


async def _run_one(
    runner: AgentRunner,
    task: ModelEvalTask,
    *,
    provider: str,
    model: str,
    system: str | None,
) -> ModelEvalTaskResult:
    started = time.perf_counter()
    try:
        turn = await runner.run_turn(
            task.prompt,
            model_ref=_model_ref(provider, model),
            system=system,
        )
        reply = str(turn.get("content", "") or "")
        error = None
        ok = _grade_reply(reply, task)
    except Exception as exc:  # noqa: BLE001 - eval output should surface model errors
        reply = ""
        error = str(exc)
        ok = False
    latency_ms = int((time.perf_counter() - started) * 1000)
    return ModelEvalTaskResult(
        task=task.name,
        provider=provider,
        model=model,
        ok=ok,
        latency_ms=latency_ms,
        reply=reply[:2000],
        weight=task.weight,
        category=task.category,
        error=error,
    )


def run_model_eval(
    *,
    provider: str = "ollama",
    model: str,
    tasks: Iterable[ModelEvalTask] = MODEL_EVAL_TASKS,
    runner: AgentRunner | None = None,
    system: str | None = "Answer exactly as requested. Be concise.",
) -> ModelEvalResult:
    """Run the model eval task set for one provider/model pair."""
    materialized = list(tasks)
    runner = runner or get_runner(provider)
    results = [
        asyncio.run(
            _run_one(
                runner,
                task,
                provider=provider,
                model=model,
                system=system,
            )
        )
        for task in materialized
    ]
    return ModelEvalResult(provider=provider, model=model, results=results)


def run_model_matrix(
    *,
    provider: str = "ollama",
    models: Iterable[str],
    tasks: Iterable[ModelEvalTask] = MODEL_EVAL_TASKS,
    system: str | None = "Answer exactly as requested. Be concise.",
) -> list[ModelEvalResult]:
    materialized_tasks = list(tasks)
    return [
        run_model_eval(
            provider=provider,
            model=model,
            tasks=materialized_tasks,
            system=system,
        )
        for model in models
    ]


def result_to_dict(result: ModelEvalResult) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "model": result.model,
        "pass": result.pass_count,
        "fail": result.fail_count,
        "weighted_score": round(result.weighted_score, 6),
        "median_latency_ms": result.median_latency_ms,
        "results": [asdict(row) for row in result.results],
    }


def matrix_to_dict(results: Iterable[ModelEvalResult]) -> dict[str, Any]:
    materialized = list(results)
    return {
        "models": [result_to_dict(result) for result in materialized],
        "ranking": sorted(
            (
                {
                    "provider": result.provider,
                    "model": result.model,
                    "weighted_score": round(result.weighted_score, 6),
                    "median_latency_ms": result.median_latency_ms,
                    "pass": result.pass_count,
                    "fail": result.fail_count,
                }
                for result in materialized
            ),
            key=lambda row: (
                -float(row["weighted_score"]),
                float(row["median_latency_ms"] or 1_000_000_000),
            ),
        ),
    }


def _parse_models(raw: str | None, *, limit: int) -> list[str]:
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return default_local_eval_models(limit=limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="localsmartz-model-eval",
        description="Run a small live-answer eval matrix for candidate local models.",
    )
    parser.add_argument("--provider", default="ollama")
    parser.add_argument(
        "--models",
        default=None,
        help="Comma-separated model names. Defaults to installed gpt-oss/qwen candidates.",
    )
    parser.add_argument("--limit-models", type=int, default=2)
    parser.add_argument(
        "--limit-tasks",
        type=int,
        default=None,
        help="Run only the first N eval tasks for a faster smoke.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON results.")
    parser.add_argument("--score-only", action="store_true", help="Print best weighted score.")
    args = parser.parse_args(argv)

    models = _parse_models(args.models, limit=max(1, args.limit_models))
    tasks = list(MODEL_EVAL_TASKS)
    if args.limit_tasks is not None:
        tasks = tasks[: max(1, args.limit_tasks)]

    payload = matrix_to_dict(
        run_model_matrix(provider=args.provider, models=models, tasks=tasks)
    )
    if args.score_only:
        best = payload["ranking"][0]["weighted_score"] if payload["ranking"] else 0.0
        print(best)
    elif args.json:
        print(json.dumps(payload, indent=2))
    else:
        for row in payload["ranking"]:
            latency = row["median_latency_ms"]
            latency_text = f"{latency:.0f}ms" if isinstance(latency, float) else "n/a"
            print(
                f"{row['model']}: score={row['weighted_score']:.3f} "
                f"pass={row['pass']} fail={row['fail']} median={latency_text}"
            )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
