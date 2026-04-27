"""Critic loop (Evaluator-Optimizer) — writer generates, critic scores, refine.

The load-bearing Phase 2 pattern. Matches research doc §Ship in v1 #4 and
§Failure points F2, F5.

Flow:
  1. writer → content
  2. critic → {"verdict":"pass|revise", "score": float, "feedback": str}
  3. if score < threshold AND iterations < max: writer re-runs with feedback
  4. else return latest writer content

Guards:
  - F2 (mini tier): if ``profile.tier == "mini"`` and writer != critic, force
    critic to share the writer's model ref. Log at INFO.
  - F5 (compound error): honor ``budget.max_iterations`` (default 3) and
    ``budget.max_turns`` (sum of writer+critic turns).

Telemetry:
  - Root span ``ls.pattern.run`` with ``pattern=critic_loop``.
  - Child span per iteration with ``ls.iteration.score`` +
    ``ls.iteration.verdict`` + ``ls.iteration.index``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator

from localsmartz.observability import get_tracer
from localsmartz.patterns.base import PatternEvent, make_root_span
from localsmartz.runners import AgentRunner

log = logging.getLogger(__name__)


DEFAULT_WRITER_SYSTEM = (
    "You are the WRITER. Produce a clear, well-structured answer to the user's "
    "question. Use pyramid-principle short-form: governing thought first, then "
    "2-4 MECE key lines with support. Do not ask the user for clarification."
)

DEFAULT_CRITIC_SYSTEM = (
    "You are the CRITIC. Read the user's question and the writer's draft, then "
    "return a JSON object with this exact shape (and nothing else):\n"
    '  {"score": <float 0.0-1.0>, "verdict": "pass"|"revise", "feedback": "<one sentence>"}\n'
    "Rubric (mentally): completeness, accuracy, clarity, actionability. Score "
    ">= 0.75 → verdict=pass. Otherwise verdict=revise with feedback naming the "
    "single most impactful fix."
)

DEFAULT_THRESHOLD = 0.75
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_MAX_TURNS = 8


def _parse_critic(content: str) -> tuple[float, str, str]:
    """Parse the critic's JSON blob. Returns (score, verdict, feedback).

    Robust to small-model formatting drift: tries strict JSON, then a
    regex extract of ``{...}``, then sensible defaults.
    """
    if not isinstance(content, str):
        return 0.0, "revise", "critic produced non-string content"
    try:
        obj = json.loads(content)
    except Exception:
        m = re.search(r"\{[^{}]*\}", content, flags=re.DOTALL)
        if not m:
            return 0.0, "revise", "critic output not parseable"
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return 0.0, "revise", "critic output not parseable"

    try:
        score = float(obj.get("score", 0.0))
    except Exception:
        score = 0.0
    verdict = str(obj.get("verdict", "revise")).lower().strip()
    if verdict not in ("pass", "revise"):
        verdict = "pass" if score >= DEFAULT_THRESHOLD else "revise"
    feedback = str(obj.get("feedback", "")).strip()
    return score, verdict, feedback


def _enforce_mini_tier_colocation(
    agents: dict[str, dict[str, Any]],
    profile: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """F2 guard: on mini tier, force critic.model_ref == writer.model_ref.

    Returns a shallow-copied ``agents`` dict with the adjusted critic slot;
    leaves the caller's dict untouched.
    """
    if profile.get("tier") != "mini":
        return agents
    writer = agents.get("writer") or {}
    critic = agents.get("critic") or {}
    writer_ref = writer.get("model_ref")
    critic_ref = critic.get("model_ref")
    if (
        isinstance(writer_ref, dict)
        and isinstance(critic_ref, dict)
        and writer_ref.get("name")
        and writer_ref.get("name") != critic_ref.get("name")
    ):
        log.info(
            "critic_loop: tier=mini forcing critic.model_ref=writer (F2) — "
            "was %s, now %s",
            critic_ref.get("name"),
            writer_ref.get("name"),
        )
        adjusted = dict(agents)
        adjusted["critic"] = {**critic, "model_ref": dict(writer_ref)}
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
    budget = (profile.get("budget") or {}) if isinstance(profile.get("budget"), dict) else {}
    threshold = float((ctx or {}).get("threshold") or budget.get("threshold", DEFAULT_THRESHOLD))
    max_iter = int(budget.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
    max_turns = int(budget.get("max_turns") or DEFAULT_MAX_TURNS)

    agents = _enforce_mini_tier_colocation(agents, profile)
    writer = agents.get("writer") or {}
    critic = agents.get("critic") or writer  # mini tier may have same slot
    writer_ref = writer.get("model_ref") or {
        "provider": "ollama",
        "name": profile.get("planning_model", ""),
    }
    critic_ref = critic.get("model_ref") or writer_ref

    span_cm, attrs = make_root_span("critic_loop", profile, thread_id)
    tracer = get_tracer("localsmartz.patterns.critic_loop")
    with span_cm as root_span:
        for k, v in attrs.items():
            root_span.set_attribute(k, v)

        yield {"type": "pattern_start", "pattern": "critic_loop"}

        turns_used = 0
        last_content = ""
        last_score = 0.0
        last_feedback = ""
        current_input = prompt

        for iteration in range(1, max_iter + 1):
            if turns_used + 2 > max_turns:
                log.info("critic_loop: turn budget exhausted before iter %d", iteration)
                break

            with tracer.start_as_current_span(f"ls.iteration.{iteration}") as it_span:
                it_span.set_attribute("ls.iteration.index", iteration)

                # Writer turn ------------------------------------------------
                writer_system = writer.get("system_focus") or DEFAULT_WRITER_SYSTEM
                if iteration > 1 and last_feedback:
                    writer_system += (
                        "\n\nRevise based on critic feedback: " + last_feedback
                    )
                writer_turn = await runner.run_turn(
                    current_input,
                    tools=writer.get("tools"),
                    model_ref=writer_ref,
                    system=writer_system,
                    ctx=ctx,
                )
                last_content = writer_turn.get("content", "") or ""
                turns_used += 1
                yield {
                    "type": "turn",
                    "role": "writer",
                    "content": last_content,
                    "iteration": iteration,
                }

                # Critic turn ------------------------------------------------
                critic_prompt = (
                    f"USER QUESTION:\n{prompt}\n\n"
                    f"WRITER DRAFT:\n{last_content}\n\n"
                    "Return the JSON object only."
                )
                critic_system = critic.get("system_focus") or DEFAULT_CRITIC_SYSTEM
                critic_turn = await runner.run_turn(
                    critic_prompt,
                    model_ref=critic_ref,
                    system=critic_system,
                    ctx=ctx,
                )
                turns_used += 1
                score, verdict, feedback = _parse_critic(
                    critic_turn.get("content", "") or ""
                )
                last_score = score
                last_feedback = feedback

                it_span.set_attribute("ls.iteration.score", score)
                it_span.set_attribute("ls.iteration.verdict", verdict)

                yield {
                    "type": "iteration",
                    "iteration": iteration,
                    "score": score,
                    "verdict": verdict,
                    "content": feedback,
                }

                if verdict == "pass" or score >= threshold:
                    break

                # Feed the writer the feedback next loop.
                current_input = prompt

        root_span.set_attribute("ls.critic_loop.final_score", last_score)
        yield {
            "type": "turn",
            "role": "final",
            "content": last_content,
            "score": last_score,
        }
        yield {"type": "done", "thread_id": thread_id or ""}
