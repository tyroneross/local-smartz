"""Small live-answer eval matrix for local model selection.

This complements ``query_doe``: DOE scores whether a query is routed to the
right path, while this module checks whether candidate models actually answer a
small representative set correctly enough to be used as local agents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Multi-provider parity scorecard
# ---------------------------------------------------------------------------

# Default model picks per cloud provider when --multi-provider is run without
# explicit --cloud-models. Single small representative model each — keeps the
# scorecard cheap. Override via --cloud-models 'anthropic:claude-haiku-4,...'.
DEFAULT_CLOUD_MODELS: dict[str, list[str]] = {
    "anthropic": ["claude-haiku-4"],
    "openai": ["gpt-4o-mini"],
    "groq": ["llama-3.3-70b-versatile"],
}


def _provider_has_key(provider: str) -> bool:
    """Best-effort check: does the provider have a usable API key?

    Reads env vars only — ``main_multi_provider`` calls
    ``secrets.export_to_env()`` once at start so a Keychain-stored key
    populates the env var BEFORE this check fires. NEVER raises —
    missing key is a normal "skip" path, not an error.
    """
    if provider == "ollama":
        return True  # daemon liveness checked separately at run time
    env_var = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
    }.get(provider)
    if env_var and os.environ.get(env_var):
        return True
    return False


def _parse_cloud_models(raw: str | None) -> dict[str, list[str]]:
    """Parse ``--cloud-models 'anthropic:claude-haiku-4,groq:llama-3.3-70b-versatile'``.

    Empty/None → DEFAULT_CLOUD_MODELS.
    """
    if not raw:
        return {p: list(m) for p, m in DEFAULT_CLOUD_MODELS.items()}
    out: dict[str, list[str]] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        prov, model = part.split(":", 1)
        out.setdefault(prov.strip(), []).append(model.strip())
    return out


def run_multi_provider(
    *,
    ollama_models: Iterable[str],
    cloud_models: dict[str, list[str]],
    tasks: Iterable[ModelEvalTask] = MODEL_EVAL_TASKS,
    system: str | None = "Answer exactly as requested. Be concise.",
    on_skip: Any = None,  # callable(provider, reason) -> None
) -> list[ModelEvalResult]:
    """Run the eval matrix across Ollama + each cloud provider that has a key.

    Cloud providers without API keys are skipped silently (one ``on_skip``
    callback per skipped provider). Ollama daemon unreachable is also skipped
    but reported the same way — never raises a hard error.
    """
    materialized_tasks = list(tasks)
    results: list[ModelEvalResult] = []

    # Ollama
    ollama_list = list(ollama_models)
    if ollama_list:
        try:
            results.extend(
                run_model_matrix(
                    provider="ollama",
                    models=ollama_list,
                    tasks=materialized_tasks,
                    system=system,
                )
            )
        except Exception as exc:  # noqa: BLE001
            if on_skip:
                on_skip("ollama", f"daemon unreachable: {exc}")

    # Cloud providers
    for provider in ("anthropic", "openai", "groq"):
        models = cloud_models.get(provider, [])
        if not models:
            continue
        if not _provider_has_key(provider):
            if on_skip:
                on_skip(provider, "no API key (env or secrets)")
            continue
        try:
            results.extend(
                run_model_matrix(
                    provider=provider,
                    models=models,
                    tasks=materialized_tasks,
                    system=system,
                )
            )
        except Exception as exc:  # noqa: BLE001
            if on_skip:
                on_skip(provider, f"runtime error: {exc}")

    return results


def write_scorecard_tsv(results: list[ModelEvalResult], path: Path) -> None:
    """Write a flat TSV (one row per task-result) to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["provider\tmodel\ttask\tok\tlatency_ms\tweight\tcategory\terror"]
    for r in results:
        for tr in r.results:
            err = (tr.error or "").replace("\t", " ").replace("\n", " ")
            rows.append(
                f"{tr.provider}\t{tr.model}\t{tr.task}\t{int(tr.ok)}\t"
                f"{tr.latency_ms}\t{tr.weight}\t{tr.category}\t{err}"
            )
    path.write_text("\n".join(rows) + "\n")


def write_scorecard_md(results: list[ModelEvalResult], path: Path, *, skips: list[tuple[str, str]] | None = None) -> None:
    """Write a human-readable markdown scorecard, one table per provider."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Multi-Provider Parity Scorecard", ""]
    if skips:
        lines.append("## Skipped Providers")
        lines.append("")
        for prov, reason in skips:
            lines.append(f"- **{prov}** — {reason}")
        lines.append("")
    by_provider: dict[str, list[ModelEvalResult]] = {}
    for r in results:
        by_provider.setdefault(r.provider, []).append(r)
    for provider, rs in sorted(by_provider.items()):
        lines.append(f"## {provider}")
        lines.append("")
        lines.append("| model | pass | fail | weighted_score | median_latency_ms |")
        lines.append("|---|---|---|---|---|")
        for r in rs:
            ml = r.median_latency_ms
            ml_text = f"{ml:.0f}" if isinstance(ml, float) else "n/a"
            lines.append(
                f"| {r.model} | {r.pass_count} | {r.fail_count} | "
                f"{r.weighted_score:.3f} | {ml_text} |"
            )
        lines.append("")
    if not by_provider:
        lines.append("_No providers produced results — all skipped or unreachable._")
        lines.append("")
    path.write_text("\n".join(lines))


def main_multi_provider(args: argparse.Namespace) -> int:
    """Entry point for ``--multi-provider`` mode. Always exit 0 on graceful skip."""
    # Bridge Keychain-stored keys into env vars BEFORE _provider_has_key
    # runs. The cloud SDKs and the env-var probe both look at os.environ;
    # without this, a Groq/Anthropic/OpenAI key stored only in Keychain
    # would cause the eval to silently skip the provider.
    try:
        from localsmartz import secrets as _secrets

        _secrets.export_to_env()
    except Exception:  # noqa: BLE001 — best-effort; missing module is a skip
        pass
    cloud_models = _parse_cloud_models(args.cloud_models)
    ollama_models = _parse_models(args.models, limit=max(1, args.limit_models))
    tasks = list(MODEL_EVAL_TASKS)
    if args.limit_tasks is not None:
        tasks = tasks[: max(1, args.limit_tasks)]

    skips: list[tuple[str, str]] = []

    def on_skip(provider: str, reason: str) -> None:
        skips.append((provider, reason))
        print(f"[skip] {provider}: {reason}", file=sys.stderr)

    results = run_multi_provider(
        ollama_models=ollama_models,
        cloud_models=cloud_models,
        tasks=tasks,
        on_skip=on_skip,
    )

    out_dir = Path(args.out_dir) if args.out_dir else Path(".build-loop/evals")
    stamp = args.stamp or time.strftime("%Y-%m-%d", time.gmtime())
    tsv_path = out_dir / f"{stamp}-multi-provider-parity.tsv"
    md_path = out_dir / f"{stamp}-multi-provider-parity.md"
    write_scorecard_tsv(results, tsv_path)
    write_scorecard_md(results, md_path, skips=skips)

    payload = {
        "stamp": stamp,
        "tsv": str(tsv_path),
        "md": str(md_path),
        "providers_run": sorted({r.provider for r in results}),
        "providers_skipped": [{"provider": p, "reason": r} for p, r in skips],
        "matrix": matrix_to_dict(results),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Wrote {tsv_path}")
        print(f"Wrote {md_path}")
        if skips:
            print(f"Skipped: {[p for p, _ in skips]}")
    return 0


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
    parser.add_argument(
        "--multi-provider",
        action="store_true",
        help=(
            "Run the eval matrix across Ollama + each cloud provider with a key. "
            "Cloud providers without keys are skipped with one stderr line each."
        ),
    )
    parser.add_argument(
        "--cloud-models",
        default=None,
        help=(
            "Comma-separated 'provider:model' pairs for --multi-provider, e.g. "
            "'anthropic:claude-haiku-4,groq:llama-3.3-70b-versatile'. Defaults to "
            "one small model per cloud provider."
        ),
    )
    parser.add_argument("--out-dir", default=None, help="Scorecard output dir (default .build-loop/evals)")
    parser.add_argument("--stamp", default=None, help="Date stamp for output filenames (default UTC today)")
    args = parser.parse_args(argv)

    if args.multi_provider:
        return main_multi_provider(args)

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
