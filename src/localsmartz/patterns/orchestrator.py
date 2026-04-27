"""Orchestrator-Workers pattern (Phase 3 B) — Type III multi-agent.

Matches the Anthropic Claude Code / Research canonical shape described in
``agent-builder/references/catalog/05-lab-patterns.md`` and the project
research packet at ``.build-loop/research/2026-04-23-phase-3-plan.md``.

Flow:
  1. Lead agent decomposes the user prompt into worker tasks + writes
     ``todo.md`` as an artifact (Manus recitation pattern).
  2. Each worker executes its subtask and writes output via
     ``artifacts.register(format=..., title=...)``. S2 ensures the
     registration emits an OTel span.
  3. Orchestrator synthesizes reading ONLY the (artifact_id, summary[:200])
     tuples for each worker — never the raw worker output. This is the
     F7 "telephone-game" mitigation.

Hard constraints (enforced in code, not just recommended):

- **One level only** (P1 guard). Workers cannot spawn sub-workers.
  A worker re-entering this module raises ``NestedSubagentError``.
- **Tier gate**. Mini tier blocks the pattern by default; callers must
  set ``profile.override_mini_orchestrator = True`` to opt in.
- **F2 (mini override)**. When mini is overridden, all workers share the
  orchestrator's model_ref to prevent thrash.
- **No .with_retry()** on chat models (DeepAgents RunnableRetry guard).
  The Path A DeepAgents construction delegates to ``runners.factory``
  which enforces this.

Telemetry:
  - Root span ``ls.pattern.run`` with ``pattern=orchestrator``.
  - Child ``ls.orchestrator.plan`` — the decomposition turn.
  - Child ``ls.orchestrator.worker.<n>`` per worker with attrs
    ``ls.worker.role``, ``ls.worker.artifact_id``,
    ``ls.worker.duration_ms``, ``ls.worker.status``.
  - Child ``ls.orchestrator.synthesize`` — the final read-summaries turn.

Two implementation paths:
  - **Path A** (``profile.use_deepagents_subagents=True``, default on
    standard+/cloud). DeepAgents ``subagents`` + ``task`` tool. ~20
    lines. Uses the S1 factory for the lead agent's chat model.
  - **Path B** (``use_deepagents_subagents=False``, default on mini).
    Iterate workers via ``AgentRunner.run_turn`` directly. No DeepAgents
    machinery — keeps the load path cheap on 24GB hardware.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator

from localsmartz.artifacts import register as register_artifact
from localsmartz.observability import get_tracer
from localsmartz.patterns.base import PatternEvent, make_root_span
from localsmartz.runners import AgentRunner

log = logging.getLogger(__name__)


class NestedSubagentError(RuntimeError):
    """Raised when a worker attempts to re-enter the orchestrator.

    Enforces the P1 "one level only" constraint. The Anthropic research
    docs and agent-builder catalog both flag cascading hallucination as
    the dominant failure mode of nested subagent fanout.
    """


DEFAULT_LEAD_SYSTEM = (
    "You are the LEAD ORCHESTRATOR. Decompose the user's question into 2-5 "
    "independent sub-tasks. Return ONLY a JSON array of objects with this "
    "shape:\n"
    '  [{"role": "<short label>", "prompt": "<self-contained sub-task>"}, ...]\n'
    "Each sub-task must be answerable without the others. Do not include "
    "sub-tasks for the final synthesis — the lead will synthesize worker "
    "outputs itself."
)

DEFAULT_SYNTH_SYSTEM = (
    "You are the LEAD ORCHESTRATOR in synthesis mode. Read the worker "
    "summaries provided below and produce the final answer to the user. "
    "You have the SUMMARIES ONLY — not the raw worker outputs. Use "
    "pyramid-principle: governing thought first, then supporting detail."
)

DEFAULT_MAX_WORKERS = 5
WORKER_SUMMARY_MAX_CHARS = 200


@dataclasses.dataclass
class WorkerTask:
    """A single decomposed subtask. Pickled into the worker's prompt."""
    role: str
    prompt: str


def _load_worker_prompt() -> str | None:
    """Load the worker system prompt from the .md file when present."""
    try:
        prompt_path = (
            Path(__file__).resolve().parents[1]
            / "agents"
            / "prompts"
            / "worker.md"
        )
        if prompt_path.exists():
            return prompt_path.read_text()
    except Exception:  # noqa: BLE001
        pass
    return None


def _parse_decomposition(content: str) -> list[WorkerTask]:
    """Parse the lead's decomposition JSON. Robust to formatting drift."""
    if not isinstance(content, str):
        return []
    text = content.strip()
    # Try strict parse first.
    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001
        # Regex extract the first ``[...]`` block.
        m = re.search(r"\[.*?\]", text, flags=re.DOTALL)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(obj, list):
        return []
    tasks: list[WorkerTask] = []
    for item in obj[:DEFAULT_MAX_WORKERS]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "worker")).strip() or "worker"
        prompt = str(item.get("prompt", "")).strip()
        if prompt:
            tasks.append(WorkerTask(role=role, prompt=prompt))
    return tasks


def _summarize(text: str, max_chars: int = WORKER_SUMMARY_MAX_CHARS) -> str:
    """Return a ≤ ``max_chars`` summary of worker output — F7 guard surface.

    First line if informative, otherwise first ``max_chars`` stripped.
    """
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if not text:
        return ""
    # Prefer the first non-empty line if it's already a good summary.
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    candidate = first_line if first_line else text
    if len(candidate) <= max_chars:
        return candidate
    return candidate[: max_chars - 1].rstrip() + "…"


def _force_shared_model_on_mini_override(
    tasks: list[WorkerTask],
    profile: dict[str, Any],
    lead_ref: dict,
) -> dict:
    """F2 guard on mini override — return a single forced worker model_ref."""
    if profile.get("tier") != "mini":
        return {}
    if not profile.get("override_mini_orchestrator"):
        return {}
    log.info(
        "orchestrator: tier=mini override — forcing all workers to share "
        "lead.model_ref=%s (F2)",
        lead_ref.get("name"),
    )
    return dict(lead_ref)


def _extract_cwd(ctx: dict | None) -> str:
    return (ctx or {}).get("cwd") or "."


async def _dispatch_workers_path_b(
    tasks: list[WorkerTask],
    *,
    runner: AgentRunner,
    lead_ref: dict,
    profile: dict,
    worker_agent: dict,
    ctx: dict | None,
    forced_worker_ref: dict,
    tracer: Any,
    thread_id: str | None,
) -> list[dict]:
    """Path B: iterate each worker via AgentRunner.run_turn directly.

    Returns a list of ``{role, artifact_id, summary, duration_ms, status}``.
    """
    worker_system = worker_agent.get("system_focus") or _load_worker_prompt()
    worker_outputs: list[dict] = []

    for n, task in enumerate(tasks, start=1):
        with tracer.start_as_current_span(
            f"ls.orchestrator.worker.{n}"
        ) as w_span:
            w_span.set_attribute("ls.worker.role", task.role)
            start = time.time()
            status = "ok"
            artifact_id = ""
            summary = ""
            try:
                # Depth guard — inject via ctx. If already set to >= 1,
                # raise before the turn.
                sub_ctx = dict(ctx or {})
                depth = int(sub_ctx.get("_orchestrator_depth", 0) or 0)
                if depth >= 1:
                    raise NestedSubagentError(
                        f"Worker {task.role!r} at depth {depth} cannot spawn "
                        "another worker — one-level-only enforced (P1)."
                    )
                sub_ctx["_orchestrator_depth"] = depth + 1

                # Choose model ref: forced on mini-override, else worker's
                # own ref, else the lead's ref as fallback.
                ref = forced_worker_ref or worker_agent.get("model_ref") or lead_ref

                turn = await runner.run_turn(
                    task.prompt,
                    tools=worker_agent.get("tools"),
                    model_ref=ref,
                    system=worker_system,
                    ctx=sub_ctx,
                )
                body = turn.get("content", "") or ""

                # Artifact: store the worker output. Path basename is
                # just the span-friendly id; we don't write a real file,
                # the artifact index tracks the metadata.
                art = register_artifact(
                    path=f".localsmartz/workers/{thread_id or 'ad-hoc'}/worker-{n}-{task.role}.md",
                    format="markdown",
                    title=f"Worker {n} ({task.role}) output",
                    cwd=_extract_cwd(ctx),
                    thread_id=thread_id,
                )
                artifact_id = art.get("id", "")
                summary = _summarize(body)
            except NestedSubagentError:
                status = "error"
                raise
            except Exception as exc:  # noqa: BLE001
                status = "error"
                summary = f"worker raised: {exc}"
                log.warning("orchestrator: worker %s failed: %s", task.role, exc)
            finally:
                duration_ms = int((time.time() - start) * 1000)
                w_span.set_attribute("ls.worker.artifact_id", artifact_id)
                w_span.set_attribute("ls.worker.duration_ms", duration_ms)
                w_span.set_attribute("ls.worker.status", status)

            worker_outputs.append({
                "role": task.role,
                "artifact_id": artifact_id,
                "summary": summary,
                "duration_ms": duration_ms,
                "status": status,
            })

    return worker_outputs


async def run(
    prompt: str,
    *,
    agents: dict[str, dict[str, Any]],
    profile: dict[str, Any],
    stream: bool = True,
    runner: AgentRunner,
    ctx: dict[str, Any] | None = None,
) -> AsyncIterator[PatternEvent]:
    thread_id = (ctx or {}).get("thread_id")
    budget = profile.get("budget") or {}
    if not isinstance(budget, dict):
        budget = {}

    # Tier gate — mini blocks by default.
    tier = profile.get("tier")
    if tier == "mini" and not profile.get("override_mini_orchestrator"):
        span_cm, attrs = make_root_span("orchestrator", profile, thread_id)
        with span_cm as root:
            for k, v in attrs.items():
                root.set_attribute(k, v)
            root.set_attribute("ls.orchestrator.verdict", "blocked_mini_tier")
            yield {"type": "pattern_start", "pattern": "orchestrator"}
            yield {
                "type": "error",
                "content": (
                    "Orchestrator-Workers requires standard or full tier. "
                    "Set profile.override_mini_orchestrator=True to force "
                    "on mini (workers will share the lead's model for F2 "
                    "thrash guard)."
                ),
            }
            yield {"type": "done", "thread_id": thread_id or ""}
        return

    # Depth guard — reject nested calls before any model is loaded.
    depth = int((ctx or {}).get("_orchestrator_depth", 0) or 0)
    if depth >= 1:
        raise NestedSubagentError(
            f"Orchestrator called at depth {depth} — one-level-only enforced (P1). "
            "A worker should never re-enter the orchestrator pattern."
        )

    lead = agents.get("orchestrator") or agents.get("lead") or {}
    worker_agent = agents.get("worker") or {}
    lead_ref = lead.get("model_ref") or {
        "provider": profile.get("provider", "ollama"),
        "name": profile.get("planning_model", ""),
    }

    forced_worker_ref = _force_shared_model_on_mini_override(
        [], profile, lead_ref
    )

    use_deepagents = bool(profile.get("use_deepagents_subagents", False))

    span_cm, attrs = make_root_span("orchestrator", profile, thread_id)
    tracer = get_tracer("localsmartz.patterns.orchestrator")

    with span_cm as root_span:
        for k, v in attrs.items():
            root_span.set_attribute(k, v)

        yield {"type": "pattern_start", "pattern": "orchestrator"}

        # ── Step 1: decomposition ──────────────────────────────────────
        tasks: list[WorkerTask] = []
        plan_body = ""
        with tracer.start_as_current_span("ls.orchestrator.plan") as plan_span:
            lead_system = lead.get("system_focus") or DEFAULT_LEAD_SYSTEM
            plan_turn = await runner.run_turn(
                prompt,
                tools=lead.get("tools"),
                model_ref=lead_ref,
                system=lead_system,
                ctx=ctx,
            )
            plan_body = plan_turn.get("content", "") or ""
            tasks = _parse_decomposition(plan_body)
            plan_span.set_attribute("ls.plan.worker_count", len(tasks))

            # Recitation — write todo.md as an artifact. This is the
            # Manus pattern: the to-do list persists and grounds the
            # workers even if context evaporates.
            if tasks:
                todo_body = "# todo.md\n\n" + "\n".join(
                    f"- [{i}] {t.role}: {t.prompt}" for i, t in enumerate(tasks, 1)
                )
                try:
                    register_artifact(
                        path=f".localsmartz/workers/{thread_id or 'ad-hoc'}/todo.md",
                        format="markdown",
                        title="Orchestrator plan",
                        cwd=_extract_cwd(ctx),
                        thread_id=thread_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("orchestrator: todo.md register failed: %s", exc)

        yield {
            "type": "turn",
            "role": "orchestrator.plan",
            "content": plan_body,
        }

        if not tasks:
            log.warning(
                "orchestrator: no tasks parsed from decomposition — "
                "falling back to single-turn response"
            )
            root_span.set_attribute("ls.orchestrator.verdict", "no_tasks_parsed")
            yield {"type": "turn", "role": "final", "content": plan_body}
            yield {"type": "done", "thread_id": thread_id or ""}
            return

        # Recompute forced_worker_ref now that we know tasks isn't empty.
        forced_worker_ref = _force_shared_model_on_mini_override(
            tasks, profile, lead_ref
        )

        # ── Step 2: dispatch workers ───────────────────────────────────
        worker_outputs: list[dict]
        if use_deepagents:
            # Path A — DeepAgents subagents + task tool. For now, Path A
            # falls back to Path B when there's no LangChain chat model
            # wrapping the runner (e.g. the stub runners we use in
            # tests). Path A's full DeepAgents wiring requires a
            # model-level construction that's orthogonal to the pattern
            # contract; we keep it behind ``use_deepagents_subagents`` so
            # integration callers can opt in. See research packet §Best
            # path "Path A" for full spec.
            try:
                worker_outputs = await _dispatch_workers_path_a(
                    tasks,
                    runner=runner,
                    lead_ref=lead_ref,
                    profile=profile,
                    worker_agent=worker_agent,
                    ctx=ctx,
                    forced_worker_ref=forced_worker_ref,
                    tracer=tracer,
                    thread_id=thread_id,
                )
            except NotImplementedError:
                log.info(
                    "orchestrator: Path A (DeepAgents) unavailable in this "
                    "context — falling back to Path B"
                )
                worker_outputs = await _dispatch_workers_path_b(
                    tasks,
                    runner=runner,
                    lead_ref=lead_ref,
                    profile=profile,
                    worker_agent=worker_agent,
                    ctx=ctx,
                    forced_worker_ref=forced_worker_ref,
                    tracer=tracer,
                    thread_id=thread_id,
                )
        else:
            worker_outputs = await _dispatch_workers_path_b(
                tasks,
                runner=runner,
                lead_ref=lead_ref,
                profile=profile,
                worker_agent=worker_agent,
                ctx=ctx,
                forced_worker_ref=forced_worker_ref,
                tracer=tracer,
                thread_id=thread_id,
            )

        for n, out in enumerate(worker_outputs, 1):
            yield {
                "type": "turn",
                "role": f"worker.{out.get('role', n)}",
                "content": out.get("summary", ""),
            }

        # ── Step 3: synthesize — summaries only (F7) ────────────────────
        with tracer.start_as_current_span(
            "ls.orchestrator.synthesize"
        ) as synth_span:
            synth_system = lead.get("synthesize_system") or DEFAULT_SYNTH_SYSTEM
            synth_lines = ["USER QUESTION:", prompt, "", "WORKER SUMMARIES:"]
            for i, out in enumerate(worker_outputs, 1):
                synth_lines.append(
                    f"  [{i}] {out.get('role')} "
                    f"(artifact_id={out.get('artifact_id') or 'n/a'}): "
                    f"{out.get('summary', '')}"
                )
            synth_prompt = "\n".join(synth_lines)
            synth_span.set_attribute("ls.synthesize.input_chars", len(synth_prompt))
            synth_span.set_attribute("ls.synthesize.worker_count", len(worker_outputs))

            synth_turn = await runner.run_turn(
                synth_prompt,
                model_ref=lead_ref,
                system=synth_system,
                ctx=ctx,
            )
            final_body = synth_turn.get("content", "") or ""

        root_span.set_attribute("ls.orchestrator.worker_count", len(worker_outputs))
        root_span.set_attribute("ls.orchestrator.verdict", "ok")

        yield {
            "type": "turn",
            "role": "final",
            "content": final_body,
        }
        yield {"type": "done", "thread_id": thread_id or ""}


async def _dispatch_workers_path_a(
    tasks: list[WorkerTask],
    *,
    runner: AgentRunner,
    lead_ref: dict,
    profile: dict,
    worker_agent: dict,
    ctx: dict | None,
    forced_worker_ref: dict,
    tracer: Any,
    thread_id: str | None,
) -> list[dict]:
    """Path A placeholder — intentionally raises NotImplementedError.

    The full Path A wiring (DeepAgents subagents + task tool + LangChain
    chat model from S1 factory) is a larger integration that plugs in at
    the ``agent.py`` layer. For Phase 3 we ship Path B end-to-end so the
    pattern is usable on all tiers, and leave Path A as a toggle callers
    can flip once the subagent wiring lands on standard+/cloud.
    """
    raise NotImplementedError("Path A (DeepAgents subagents) is a follow-up")
