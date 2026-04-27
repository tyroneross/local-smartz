"""Benchmark helpers for measuring local-smartz request latency.

Primary use case: compare startup and request timings before/after
performance changes and run A/B checks with
``LOCALSMARTZ_DISABLE_ROLE_AGENT_CACHE=1``.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass
class RunMetrics:
    run_index: int
    first_byte_ms: int | None = None
    first_event_ms: int | None = None
    first_stage_ms: int | None = None
    first_text_ms: int | None = None
    wall_duration_ms: int | None = None
    server_duration_ms: int | None = None
    warmup_ms: int | None = None
    text_chars: int = 0
    event_counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def _median(values: list[int]) -> float | None:
    return float(statistics.median(values)) if values else None


def _percentile(values: list[int], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return float(ordered[idx])


def summarize_runs(runs: list[RunMetrics], *, startup_ms: int | None = None) -> dict[str, Any]:
    """Aggregate per-run metrics into a compact summary dict."""
    wall_values = [r.wall_duration_ms for r in runs if r.wall_duration_ms is not None]
    first_text_values = [r.first_text_ms for r in runs if r.first_text_ms is not None]
    first_stage_values = [r.first_stage_ms for r in runs if r.first_stage_ms is not None]
    first_event_values = [r.first_event_ms for r in runs if r.first_event_ms is not None]
    first_byte_values = [r.first_byte_ms for r in runs if r.first_byte_ms is not None]
    server_values = [r.server_duration_ms for r in runs if r.server_duration_ms is not None]
    warmup_values = [r.warmup_ms for r in runs if r.warmup_ms is not None]
    text_values = [r.text_chars for r in runs]
    followup_wall = [
        r.wall_duration_ms for r in runs[1:] if r.wall_duration_ms is not None
    ]

    first_run = asdict(runs[0]) if runs else None
    summary = {
        "startup_ms": startup_ms,
        "run_count": len(runs),
        "first_run": first_run,
        "median_wall_ms": _median(wall_values),
        "p95_wall_ms": _percentile(wall_values, 0.95),
        "median_first_byte_ms": _median(first_byte_values),
        "median_first_event_ms": _median(first_event_values),
        "median_first_stage_ms": _median(first_stage_values),
        "median_first_text_ms": _median(first_text_values),
        "median_server_duration_ms": _median(server_values),
        "median_warmup_ms": _median(warmup_values),
        "median_text_chars": _median(text_values),
        "error_count": sum(1 for r in runs if r.error),
        "runs": [asdict(r) for r in runs],
    }
    if first_run and followup_wall:
        summary["followup_median_wall_ms"] = _median(followup_wall)
        summary["first_run_minus_followup_ms"] = (
            first_run["wall_duration_ms"] - summary["followup_median_wall_ms"]
            if first_run["wall_duration_ms"] is not None
            and summary["followup_median_wall_ms"] is not None
            else None
        )
    return summary


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(base_url: str, *, timeout_s: float = 30.0) -> int:
    """Wait until the backend health endpoint responds with HTTP 200."""
    started = time.perf_counter()
    deadline = started + timeout_s
    with httpx.Client(timeout=2.0) as client:
        while time.perf_counter() < deadline:
            try:
                resp = client.get(f"{base_url}/api/health")
                if resp.status_code == 200:
                    return int((time.perf_counter() - started) * 1000)
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise TimeoutError(f"Backend at {base_url} did not become healthy within {timeout_s:.1f}s")


def measure_research_request(
    base_url: str,
    *,
    prompt: str,
    run_index: int,
    profile: str | None = None,
    agent: str | None = None,
    cwd: str | None = None,
    timeout_s: float = 600.0,
    stop_after: str = "done",
) -> RunMetrics:
    """Send one SSE research request and capture timing milestones."""
    started = time.perf_counter()
    metrics = RunMetrics(run_index=run_index)
    payload: dict[str, str] = {"prompt": prompt}
    if profile:
        payload["profile"] = profile
    if agent:
        payload["agent"] = agent
    if cwd:
        payload["cwd"] = cwd

    buffer = ""
    with httpx.stream(
        "POST",
        f"{base_url}/api/research",
        headers={"Accept": "text/event-stream"},
        json=payload,
        timeout=timeout_s,
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_text():
            now_ms = int((time.perf_counter() - started) * 1000)
            if chunk and metrics.first_byte_ms is None:
                metrics.first_byte_ms = now_ms
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.startswith("data: "):
                    continue
                if metrics.first_event_ms is None:
                    metrics.first_event_ms = int((time.perf_counter() - started) * 1000)
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                event_type = str(event.get("type", "unknown"))
                metrics.event_counts[event_type] = metrics.event_counts.get(event_type, 0) + 1
                if event_type == "text":
                    content = event.get("content", "")
                    if isinstance(content, str):
                        if content and metrics.first_text_ms is None:
                            metrics.first_text_ms = int((time.perf_counter() - started) * 1000)
                            if stop_after == "first_text":
                                metrics.wall_duration_ms = metrics.first_text_ms
                                return metrics
                        metrics.text_chars += len(content)
                elif event_type == "stage":
                    if metrics.first_stage_ms is None:
                        metrics.first_stage_ms = int((time.perf_counter() - started) * 1000)
                        if stop_after == "first_stage":
                            metrics.wall_duration_ms = metrics.first_stage_ms
                            return metrics
                elif event_type == "status":
                    if (
                        event.get("stage") == "ready"
                        and metrics.warmup_ms is None
                        and isinstance(event.get("warmup_ms"), int)
                    ):
                        metrics.warmup_ms = int(event["warmup_ms"])
                elif event_type == "done":
                    if isinstance(event.get("duration_ms"), int):
                        metrics.server_duration_ms = int(event["duration_ms"])
                    metrics.wall_duration_ms = int((time.perf_counter() - started) * 1000)
                    return metrics
                elif event_type == "error":
                    message = event.get("message")
                    metrics.error = str(message) if message is not None else "unknown error"
                    metrics.wall_duration_ms = int((time.perf_counter() - started) * 1000)
                    return metrics

    metrics.wall_duration_ms = int((time.perf_counter() - started) * 1000)
    return metrics


def spawn_server(
    *,
    cwd: Path,
    port: int,
    python_executable: str,
    profile: str | None = None,
    observe: bool = False,
    disable_role_cache: bool = False,
) -> tuple[subprocess.Popen, int, str]:
    """Launch a local-smartz server subprocess and wait for health."""
    command = [python_executable, "-m", "localsmartz", "--serve", "--port", str(port)]
    if profile:
        command.extend(["--profile", profile])
    if observe:
        command.append("--observe")

    log_file = tempfile.NamedTemporaryFile(
        prefix="localsmartz-benchmark-",
        suffix=".log",
        delete=False,
    )
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if disable_role_cache:
        env["LOCALSMARTZ_DISABLE_ROLE_AGENT_CACHE"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        startup_ms = wait_for_health(f"http://127.0.0.1:{port}")
        return process, startup_ms, log_file.name
    except Exception:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        raise


def _human_summary(summary: dict[str, Any], *, base_url: str, log_path: str | None) -> str:
    lines = [
        f"base_url: {base_url}",
        f"runs: {summary['run_count']}",
    ]
    if summary.get("startup_ms") is not None:
        lines.append(f"startup_ms: {summary['startup_ms']}")
    for key in (
        "median_wall_ms",
        "p95_wall_ms",
        "median_first_byte_ms",
        "median_first_event_ms",
        "median_first_stage_ms",
        "median_first_text_ms",
        "median_server_duration_ms",
        "median_warmup_ms",
        "first_run_minus_followup_ms",
    ):
        value = summary.get(key)
        if value is not None:
            lines.append(f"{key}: {value}")
    if log_path:
        lines.append(f"log_path: {log_path}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="localsmartz-benchmark",
        description="Benchmark local-smartz startup and SSE request timings.",
    )
    parser.add_argument("--prompt", required=True, help="Prompt to send to /api/research")
    parser.add_argument("--runs", type=int, default=3, help="Number of benchmark runs")
    parser.add_argument("--base-url", default=None, help="Use an existing server instead of spawning one")
    parser.add_argument("--cwd", default=".", help="Workspace directory for spawned server or request cwd")
    parser.add_argument("--profile", default=None, help="Optional profile for spawned server/request")
    parser.add_argument("--agent", default=None, help="Optional focused agent for request")
    parser.add_argument("--port", type=int, default=0, help="Port for spawned server (0 = auto)")
    parser.add_argument("--python", default=sys.executable, help="Python executable for spawned server")
    parser.add_argument("--observe", action="store_true", help="Spawn server with --observe")
    parser.add_argument(
        "--disable-role-cache",
        action="store_true",
        help="Spawn server with LOCALSMARTZ_DISABLE_ROLE_AGENT_CACHE=1 for A/B checks",
    )
    parser.add_argument("--timeout", type=float, default=600.0, help="Per-request timeout in seconds")
    parser.add_argument(
        "--stop-after",
        choices=("done", "first_stage", "first_text"),
        default="done",
        help="Stop measurement at the first stage/text milestone instead of waiting for completion",
    )
    parser.add_argument("--json", action="store_true", help="Print the full JSON payload")
    parser.add_argument("--output", default=None, help="Optional path to write JSON results")
    args = parser.parse_args(argv)

    workspace = Path(args.cwd).resolve()
    base_url = args.base_url
    process: subprocess.Popen | None = None
    startup_ms: int | None = None
    log_path: str | None = None

    try:
        if base_url is None:
            port = args.port or _free_port()
            process, startup_ms, log_path = spawn_server(
                cwd=workspace,
                port=port,
                python_executable=args.python,
                profile=args.profile,
                observe=args.observe,
                disable_role_cache=args.disable_role_cache,
            )
            base_url = f"http://127.0.0.1:{port}"

        runs = [
            measure_research_request(
                base_url,
                prompt=args.prompt,
                run_index=idx + 1,
                profile=args.profile,
                agent=args.agent,
                cwd=str(workspace),
                timeout_s=args.timeout,
                stop_after=args.stop_after,
            )
            for idx in range(args.runs)
        ]
        summary = summarize_runs(runs, startup_ms=startup_ms)
        payload = {
            "base_url": base_url,
            "log_path": log_path,
            "summary": summary,
        }
        if args.output:
            Path(args.output).write_text(json.dumps(payload, indent=2))
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(_human_summary(summary, base_url=base_url, log_path=log_path))
        return 0
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


# ── Golden-task harness (Phase 2, Item 6) ─────────────────────────────────
#
# A tiny, in-process harness that exercises one fixed task per provider and
# captures a pass/fail verdict plus the raw reply so the Settings → "Run
# eval suite" button can show cross-provider deltas without a live
# server. The tasks here are intentionally small and deterministic enough
# that a local qwen3.5:9b (or a cloud Sonnet / Groq 70B) can answer them
# in a single turn. Callers add more tasks over time by appending to
# ``GOLDEN_TASKS`` below; the harness doesn't try to be a benchmark —
# it's a smoke test for the cloud-toggle wiring.

from dataclasses import dataclass as _dataclass


@_dataclass
class GoldenTask:
    name: str
    prompt: str
    # A list of substrings, ALL of which must appear in the model reply
    # (case-insensitive) for the task to pass. Keeps the grader dumb and
    # stable.
    must_contain: list[str]


GOLDEN_TASKS: list[GoldenTask] = [
    # ── Happy-path (3) ────────────────────────────────────────────────
    GoldenTask(
        name="arithmetic_simple",
        prompt="What is 15% of 2400? Respond with just the number.",
        must_contain=["360"],
    ),
    GoldenTask(
        name="capital_city",
        prompt="What is the capital of France? Respond with one word.",
        must_contain=["paris"],
    ),
    GoldenTask(
        name="bulleted_list",
        prompt=(
            "List three primary colors as a bulleted list. "
            "One color per bullet, no extra prose."
        ),
        must_contain=["red", "blue", "yellow"],
    ),
    # ── Failure-mode probes (7) ───────────────────────────────────────
    # Catalog: ~/dev/git-folder/agent-builder/references/catalog/
    #   06-local-and-open-source-models.md § Common Failure Modes
    GoldenTask(
        # Probe F5 compound-error: force a 2-step transformation the
        # model often short-circuits on.
        name="multi_step_transform",
        prompt=(
            "Take the word 'observability', reverse it, then count "
            "how many letters it has. Respond exactly as: "
            "REVERSED=<word> COUNT=<n>"
        ),
        must_contain=["reversed=ytilibavresbo", "count=13"],
    ),
    GoldenTask(
        # Probe F22 reasoning-mangling: reasoning models sometimes
        # wrap JSON in explanatory prose. Confirm strict format holds.
        name="strict_json_shape",
        prompt=(
            'Return ONLY a JSON object with keys "a" and "b" and '
            'integer values 1 and 2. No markdown, no fence, no prose. '
            'Example of correct reply: {"a":1,"b":2}'
        ),
        must_contain=['"a":', '"b":', "1", "2"],
    ),
    GoldenTask(
        # Probe F8 silent-drop / refusal handling: model must acknowledge
        # an impossible request explicitly rather than hallucinating.
        name="impossible_refusal",
        prompt=(
            "What was the closing price of Apple stock on "
            "December 31, 2099? Answer with the literal word UNKNOWN "
            "if you cannot know."
        ),
        must_contain=["unknown"],
    ),
    GoldenTask(
        # Probe empty-result ignored: prompt explicitly requires
        # reporting a negative finding.
        name="negative_result",
        prompt=(
            "Does the word 'hippopotamus' contain the letter 'z'? "
            "Respond with only YES or NO."
        ),
        must_contain=["no"],
    ),
    GoldenTask(
        # Probe F4 tool/format hallucination: request a constrained
        # output the model often tries to embellish.
        name="constrained_output",
        prompt=(
            "Reply with the single character 'X' and nothing else. "
            "No prose, no punctuation, no quotes."
        ),
        must_contain=["x"],
    ),
    GoldenTask(
        # Probe long-context handling: short prompt, long structured
        # reply. Catches models that truncate at ~512 tokens.
        name="structured_long_reply",
        prompt=(
            "List the numbers 1 through 20, one per line, each "
            "prefixed with 'n='. Example: n=1"
        ),
        must_contain=["n=1", "n=10", "n=20"],
    ),
    GoldenTask(
        # Probe ordering / instruction-following: model often flips
        # adjacent constraints.
        name="ordered_instruction",
        prompt=(
            "Reply with exactly three words in this order: "
            "first CAT, then DOG, then BIRD. Separate with spaces."
        ),
        must_contain=["cat dog bird"],
    ),
]


@_dataclass
class GoldenTaskResult:
    task: str
    provider: str
    model: str
    ok: bool
    latency_ms: int
    reply: str
    error: str | None = None


@_dataclass
class BenchmarkResult:
    provider: str
    model: str
    results: list[GoldenTaskResult]

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)


def _grade_reply(reply: str, must_contain: list[str]) -> bool:
    lowered = reply.lower()
    return all(tok.lower() in lowered for tok in must_contain)


def run_golden_on_provider(
    provider: str,
    *,
    model: str | None = None,
    tasks: list[GoldenTask] | None = None,
) -> BenchmarkResult:
    """Run the golden-task set against a single provider.

    ``provider`` is one of ``ollama | anthropic | openai | groq``. When
    ``model`` is ``None`` we pick a sensible default per provider:
      - ollama  → ``qwen3.5:9b-q4_K_M`` (tier-matched mini floor)
      - anthropic → ``claude-sonnet-4-5-20250929`` (documented in cost.py)
      - openai  → ``gpt-4.1-mini``
      - groq    → ``llama-3.3-70b-versatile``

    Each task runs synchronously (``asyncio.run``) via the shared
    ``runners.get_runner`` protocol — keeps local + cloud paths on the
    same code, and means the harness itself can't regress if a specific
    pattern wiring changes.
    """
    import asyncio
    from localsmartz.runners import get_runner

    tasks = tasks or GOLDEN_TASKS
    defaults = {
        "ollama": "qwen3.5:9b-q4_K_M",
        "anthropic": "claude-sonnet-4-5-20250929",
        "openai": "gpt-4.1-mini",
        "groq": "llama-3.3-70b-versatile",
    }

    if model is None and provider == "ollama":
        # Prefer an actually-installed model over the catalog default.
        # Fallback chain:
        #   1. First installed model whose name appears in the registry
        #   2. First installed model (any)
        #   3. Active profile's planning_model (guaranteed installed because
        #      the backend is already running)
        #   4. Hardcoded catalog default (legacy path)
        from localsmartz.ollama import list_models as _list_ollama
        from localsmartz.models.registry import get_all_recs as _all_recs
        from localsmartz.profiles import get_profile as _get_profile

        installed = _list_ollama()
        if installed:
            registry_names = {r["name"] for r in _all_recs()}
            # Normalise: strip quant suffix for matching (e.g. qwen3:8b-q4_K_M → qwen3:8b)
            def _base(n: str) -> str:
                return n.split("-")[0] if "-" in n.split(":")[-1] else n

            registry_bases = {_base(n) for n in registry_names}
            registry_match = next(
                (m for m in installed if _base(m) in registry_bases),
                None,
            )
            resolved_model = registry_match or installed[0]
        else:
            # Ollama not reachable; fall back to profile's planning_model
            try:
                resolved_model = _get_profile()["planning_model"]
            except Exception:
                resolved_model = defaults["ollama"]
    else:
        resolved_model = model or defaults.get(provider, "qwen3.5:9b-q4_K_M")

    try:
        runner = get_runner(provider)
    except (ImportError, ValueError) as exc:
        # Cloud SDK missing or unknown provider — mark all tasks as errored
        # so the UI can show the cause instead of silently counting them
        # as failed.
        return BenchmarkResult(
            provider=provider,
            model=resolved_model,
            results=[
                GoldenTaskResult(
                    task=t.name,
                    provider=provider,
                    model=resolved_model,
                    ok=False,
                    latency_ms=0,
                    reply="",
                    error=str(exc),
                )
                for t in tasks
            ],
        )

    model_ref = {"provider": provider, "name": resolved_model}
    if provider == "groq":
        model_ref["base_url"] = "https://api.groq.com/openai/v1"

    results: list[GoldenTaskResult] = []
    for task in tasks:
        started = time.perf_counter()
        try:
            reply_turn = asyncio.run(
                runner.run_turn(
                    task.prompt,
                    model_ref=model_ref,  # type: ignore[arg-type]
                )
            )
            reply = str(reply_turn.get("content", "") or "")
            ok = _grade_reply(reply, task.must_contain)
            err = None
        except Exception as exc:  # noqa: BLE001 — surfaced to UI
            reply = ""
            ok = False
            err = str(exc)
        latency_ms = int((time.perf_counter() - started) * 1000)
        results.append(
            GoldenTaskResult(
                task=task.name,
                provider=provider,
                model=resolved_model,
                ok=ok,
                latency_ms=latency_ms,
                reply=reply[:2000],  # cap so huge cloud replies don't blow the response
                error=err,
            )
        )

    return BenchmarkResult(
        provider=provider,
        model=resolved_model,
        results=results,
    )


def diff_results(a: BenchmarkResult, b: BenchmarkResult) -> dict[str, Any]:
    """Compare two ``BenchmarkResult`` instances, same-task pairwise.

    Returns a dict suitable for JSON serialization::

        {
            "left": {"provider": ..., "model": ..., "pass": N, "fail": M},
            "right": {...},
            "agree": [task_name, ...],
            "disagree": [{task, left_ok, right_ok, left_reply, right_reply}, ...],
        }
    """
    by_task_a = {r.task: r for r in a.results}
    by_task_b = {r.task: r for r in b.results}
    tasks = sorted(set(by_task_a) | set(by_task_b))

    agree: list[str] = []
    disagree: list[dict[str, Any]] = []
    for t in tasks:
        ra = by_task_a.get(t)
        rb = by_task_b.get(t)
        if ra is None or rb is None:
            disagree.append(
                {
                    "task": t,
                    "left_ok": bool(ra and ra.ok),
                    "right_ok": bool(rb and rb.ok),
                    "left_reply": ra.reply if ra else "",
                    "right_reply": rb.reply if rb else "",
                    "note": "task missing from one side",
                }
            )
        elif ra.ok == rb.ok:
            agree.append(t)
        else:
            disagree.append(
                {
                    "task": t,
                    "left_ok": ra.ok,
                    "right_ok": rb.ok,
                    "left_reply": ra.reply,
                    "right_reply": rb.reply,
                }
            )

    return {
        "left": {
            "provider": a.provider,
            "model": a.model,
            "pass": a.pass_count,
            "fail": a.fail_count,
        },
        "right": {
            "provider": b.provider,
            "model": b.model,
            "pass": b.pass_count,
            "fail": b.fail_count,
        },
        "agree": agree,
        "disagree": disagree,
    }


def benchmark_to_dict(result: BenchmarkResult) -> dict[str, Any]:
    """JSON-friendly view of a ``BenchmarkResult``."""
    return {
        "provider": result.provider,
        "model": result.model,
        "pass": result.pass_count,
        "fail": result.fail_count,
        "results": [
            {
                "task": r.task,
                "ok": r.ok,
                "latency_ms": r.latency_ms,
                "reply": r.reply,
                "error": r.error,
            }
            for r in result.results
        ],
    }
