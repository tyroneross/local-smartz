"""Reflection (Reflexion-style) — single agent self-critiques and revises.

Phase 3 pattern A. Same model generates an answer, then reflects on its
own output against a structured rubric, then revises. Cheapest Phase 3
pattern: same-model-two-prompts, no co-residency requirement, works on
the 24GB mini floor.

Flow:
  1. primary → draft
  2. reflector → {"score": 0..1, "revision_reason": "...", "suggest": "..."}
  3. if score >= pass_threshold OR delta < min_delta OR iterations >= max:
        stop
     else:
        primary re-runs with suggest as an instruction
  4. return the latest primary draft

Guards:
  - F5 (compound error): ``budget.max_iterations`` default 3, ``max_turns`` default 6.
  - P3 (stagnation): rubric score must improve by ``min_delta`` (default 0.05)
    between iterations. Flat-or-dropping scores abort early with verdict=stagnant.
  - F2 (mini tier): if writer != reflector model refs, the reflector is
    forced to share the primary's model. Log at INFO.

Telemetry:
  - Root span ``ls.pattern.run`` with ``pattern=reflection``.
  - Child span per iteration ``ls.reflection.<n>`` with attrs
    ``ls.reflection.index``, ``ls.reflection.rubric_score``,
    ``ls.reflection.revision_reason``.
  - Root sets final ``ls.reflection.final_score`` on exit.

Persistence:
  - Each reflector verdict is appended via ``threads.append_entry`` with
    ``kind="reflection"`` so the reflections are queryable in later
    threads and render into ``context.md``.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator

from localsmartz.observability import get_tracer
from localsmartz.patterns.base import PatternEvent, make_root_span
from localsmartz.runners import AgentRunner

log = logging.getLogger(__name__)


DEFAULT_PRIMARY_SYSTEM = (
    "You are the PRIMARY. Produce a clear, well-structured answer to the "
    "user's question. Use pyramid-principle short-form: governing thought "
    "first, then 2-4 supporting lines. Do not ask the user for clarification."
)

DEFAULT_REFLECTOR_SYSTEM = (
    "You are the REFLECTOR. Read the user's question and the primary's draft, "
    "then return a JSON object with this exact shape (and nothing else):\n"
    '  {"score": <float 0.0-1.0>, "revision_reason": "<short phrase>", '
    '"suggest": "<concrete revision instruction>"}\n'
    "Rubric: completeness, accuracy, clarity, actionability. Score >= 0.85 "
    "means ship; < 0.85 means ``suggest`` names ONE concrete edit."
)

DEFAULT_PASS_THRESHOLD = 0.85
DEFAULT_MIN_DELTA = 0.05
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_MAX_TURNS = 6


def _load_reflector_prompt() -> str | None:
    """Prefer the .md prompt file when present; fall back to the default."""
    try:
        prompt_path = (
            Path(__file__).resolve().parents[1]
            / "agents"
            / "prompts"
            / "reflector.md"
        )
        if prompt_path.exists():
            return prompt_path.read_text()
    except Exception:  # noqa: BLE001
        pass
    return None


def _parse_reflector(content: str) -> tuple[float, str, str]:
    """Parse reflector JSON. Returns ``(score, revision_reason, suggest)``.

    Robust to small-model drift: tries strict JSON, then a regex extract of
    the first ``{...}`` block, then sensible defaults.
    """
    if not isinstance(content, str):
        return 0.0, "reflector produced non-string content", ""
    text = content.strip()
    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*?\}", text, flags=re.DOTALL)
        if not m:
            return 0.0, "reflector output not parseable", ""
        try:
            obj = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return 0.0, "reflector output not parseable", ""
    try:
        score = float(obj.get("score", 0.0))
    except Exception:  # noqa: BLE001
        score = 0.0
    # Clamp score to [0, 1] — a dropping score with a bogus value shouldn't
    # mask the stagnation check.
    score = max(0.0, min(1.0, score))
    revision_reason = str(obj.get("revision_reason", "")).strip()
    suggest = str(obj.get("suggest", "")).strip()
    return score, revision_reason, suggest


def _enforce_mini_tier_colocation(
    agents: dict[str, dict[str, Any]],
    profile: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """F2 guard: on mini tier, reflector shares the primary's model_ref."""
    if profile.get("tier") != "mini":
        return agents
    primary = agents.get("primary") or {}
    reflector = agents.get("reflector") or {}
    primary_ref = primary.get("model_ref")
    reflector_ref = reflector.get("model_ref")
    if (
        isinstance(primary_ref, dict)
        and isinstance(reflector_ref, dict)
        and primary_ref.get("name")
        and primary_ref.get("name") != reflector_ref.get("name")
    ):
        log.info(
            "reflection: tier=mini forcing reflector.model_ref=primary (F2) — "
            "was %s, now %s",
            reflector_ref.get("name"),
            primary_ref.get("name"),
        )
        adjusted = dict(agents)
        adjusted["reflector"] = {**reflector, "model_ref": dict(primary_ref)}
        return adjusted
    return agents


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
    pass_threshold = float(
        (ctx or {}).get("threshold")
        or budget.get("reflection_threshold", DEFAULT_PASS_THRESHOLD)
    )
    min_delta = float(budget.get("min_reflection_delta", DEFAULT_MIN_DELTA))
    max_iter = int(budget.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
    max_turns = int(budget.get("max_turns") or DEFAULT_MAX_TURNS)
    # F5 hard cap — even if profile over-sets, never exceed 3.
    if max_iter > 3:
        max_iter = 3

    agents = _enforce_mini_tier_colocation(agents, profile)
    primary = agents.get("primary") or {}
    # On mini we may have forced the reflector to share primary; default to
    # primary when the reflector slot is absent (same single-agent trick
    # critic_loop uses on mini).
    reflector = agents.get("reflector") or primary
    primary_ref = primary.get("model_ref") or {
        "provider": "ollama",
        "name": profile.get("planning_model", ""),
    }
    reflector_ref = reflector.get("model_ref") or primary_ref

    span_cm, attrs = make_root_span("reflection", profile, thread_id)
    tracer = get_tracer("localsmartz.patterns.reflection")
    with span_cm as root_span:
        for k, v in attrs.items():
            root_span.set_attribute(k, v)

        yield {"type": "pattern_start", "pattern": "reflection"}

        turns_used = 0
        last_content = ""
        last_score = 0.0
        last_reason = ""
        prev_score: float | None = None
        verdict = "pass"  # default if we never loop
        current_suggest = ""

        for iteration in range(1, max_iter + 1):
            if turns_used + 2 > max_turns:
                log.info(
                    "reflection: turn budget exhausted before iter %d", iteration
                )
                break

            with tracer.start_as_current_span(
                f"ls.reflection.{iteration}"
            ) as it_span:
                it_span.set_attribute("ls.reflection.index", iteration)

                # PRIMARY turn --------------------------------------------
                primary_system = primary.get("system_focus") or DEFAULT_PRIMARY_SYSTEM
                if iteration > 1 and current_suggest:
                    primary_system += (
                        "\n\nRevise based on reflector feedback: "
                        + current_suggest
                    )
                primary_turn = await runner.run_turn(
                    prompt,
                    tools=primary.get("tools"),
                    model_ref=primary_ref,
                    system=primary_system,
                    ctx=ctx,
                )
                last_content = primary_turn.get("content", "") or ""
                turns_used += 1
                yield {
                    "type": "turn",
                    "role": "primary",
                    "content": last_content,
                    "iteration": iteration,
                }

                # REFLECTOR turn ------------------------------------------
                reflector_system = (
                    reflector.get("system_focus")
                    or _load_reflector_prompt()
                    or DEFAULT_REFLECTOR_SYSTEM
                )
                reflector_prompt = (
                    f"USER QUESTION:\n{prompt}\n\n"
                    f"PRIMARY DRAFT:\n{last_content}\n\n"
                    "Return the JSON object only."
                )
                reflector_turn = await runner.run_turn(
                    reflector_prompt,
                    model_ref=reflector_ref,
                    system=reflector_system,
                    ctx=ctx,
                )
                turns_used += 1
                score, reason, suggest = _parse_reflector(
                    reflector_turn.get("content", "") or ""
                )
                last_score = score
                last_reason = reason
                current_suggest = suggest

                it_span.set_attribute("ls.reflection.rubric_score", score)
                it_span.set_attribute("ls.reflection.revision_reason", reason)

                # Persistence: write a reflection-kind thread entry so
                # later threads (and the UI timeline) can see the loop.
                if thread_id:
                    try:
                        from localsmartz.threads import append_entry

                        append_entry(
                            thread_id=thread_id,
                            cwd=(ctx or {}).get("cwd") or ".",
                            query=prompt,
                            summary=(
                                f"iter={iteration} score={score:.2f} "
                                f"reason={reason!r}"
                            ),
                            artifacts=[],
                            turns=2,
                            rationale=suggest or None,
                            kind="reflection",
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.debug(
                            "reflection: append_entry failed (non-fatal): %s",
                            exc,
                        )

                yield {
                    "type": "iteration",
                    "iteration": iteration,
                    "score": score,
                    "verdict": "pass" if score >= pass_threshold else "revise",
                    "content": reason,
                }

                # Pass threshold → done.
                if score >= pass_threshold:
                    verdict = "pass"
                    break

                # Stagnation check — P3 guard. Only triggers after the
                # first iteration (there's no baseline to compare to on
                # iter 1).
                if prev_score is not None:
                    delta = score - prev_score
                    if delta < min_delta:
                        verdict = "stagnant"
                        log.info(
                            "reflection: stagnant at iter %d — "
                            "score=%.3f prev=%.3f delta=%.3f (< %.3f)",
                            iteration, score, prev_score, delta, min_delta,
                        )
                        break

                prev_score = score
                # If we're about to loop again, queue the suggest for the
                # next primary turn (already set via current_suggest).

        else:
            # ``for`` ran to completion without ``break`` → budget exhausted.
            verdict = "budget_exhausted" if last_score < pass_threshold else "pass"

        root_span.set_attribute("ls.reflection.final_score", last_score)
        root_span.set_attribute("ls.reflection.verdict", verdict)

        yield {
            "type": "turn",
            "role": "final",
            "content": last_content,
            "score": last_score,
            "verdict": verdict,
        }
        yield {"type": "done", "thread_id": thread_id or ""}
