"""Parallelization / Ensemble pattern (Phase 3 Pattern C).

N samples of the same prompt drawn from a sampler agent, then a
synthesizer agent merges them into one answer. Useful for ensemble
voting, reducing model variance, and catching obvious hallucinations
through disagreement.

Tier-gated concurrency:
  - mini:     semaphore=1 (sequential samples with varied temperatures —
              F1 OOM guard, single model loaded at a time)
  - standard: semaphore=2
  - full:     semaphore=3

Guards:
  - **P4 (parallel dupes)**. Temperatures must be >= 0.3. Low-temp
    sampling defeats the ensemble value — we raise rather than silently
    produce near-identical draws.
  - **F1 (OOM on mini)**. The semaphore ceiling keeps concurrent model
    loads bounded.

Synthesizer selection (``profile.parallel.synthesizer``):
  - ``same_model``: synthesizer uses the sampler's model (default on mini).
  - ``stronger_model``: synthesizer uses a larger model via
    ``agents['synthesizer'].model_ref`` (default on standard+).
  - ``user_select``: synthesizer is explicitly whatever model_ref the
    caller passed in.

Telemetry:
  - Root ``ls.pattern.run`` with ``pattern=parallel``.
  - Children ``ls.sample.<n>`` with attrs ``ls.sample.{index, temperature,
    duration_ms, status}``.
  - Final child ``ls.synthesize``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

from localsmartz.observability import get_tracer
from localsmartz.patterns.base import PatternEvent, make_root_span
from localsmartz.runners import AgentRunner

log = logging.getLogger(__name__)


DEFAULT_SAMPLER_SYSTEM = (
    "You are a SAMPLER. Produce one independent answer to the user's "
    "question. Aim for a confident, direct response."
)

DEFAULT_SYNTH_SYSTEM = (
    "You are the SYNTHESIZER. You have several independent SAMPLE "
    "answers to the same user question. Merge them: keep what they "
    "agree on, resolve disagreements, drop weakly-supported claims. "
    "Return ONE final answer. Pyramid-principle: governing thought first."
)

DEFAULT_PARALLEL_N = 3
PARALLEL_MIN_TEMPERATURE = 0.3
DEFAULT_TEMPERATURES = (0.3, 0.5, 0.7, 0.9, 0.4)  # first N used

TIER_SEMAPHORE = {"mini": 1, "standard": 2, "full": 3}


class ParallelTemperatureError(ValueError):
    """Raised when a sample temperature is below PARALLEL_MIN_TEMPERATURE.

    Low temperatures collapse samples into near-duplicates and defeat
    the ensemble premise (P4 mitigation).
    """


def _temperatures_for(n: int, profile: dict) -> list[float]:
    """Select N temperatures. Caller override > DEFAULT_TEMPERATURES."""
    pconf = (profile.get("parallel") or {}) if isinstance(profile.get("parallel"), dict) else {}
    override = pconf.get("temperatures")
    if isinstance(override, (list, tuple)) and override:
        temps = [float(t) for t in override[:n]]
    else:
        temps = list(DEFAULT_TEMPERATURES[:n])
    # Pad with DEFAULT_TEMPERATURES cycle if override was short.
    while len(temps) < n:
        temps.append(DEFAULT_TEMPERATURES[len(temps) % len(DEFAULT_TEMPERATURES)])
    for t in temps:
        if t < PARALLEL_MIN_TEMPERATURE:
            raise ParallelTemperatureError(
                f"parallel: temperature {t} < {PARALLEL_MIN_TEMPERATURE} "
                "(P4 guard — low-temp draws collapse samples into duplicates)"
            )
    return temps


def _semaphore_limit(profile: dict) -> int:
    budget = profile.get("budget") or {}
    explicit = budget.get("parallel_concurrency") if isinstance(budget, dict) else None
    if isinstance(explicit, int) and explicit >= 1:
        return explicit
    tier = profile.get("tier", "standard")
    return TIER_SEMAPHORE.get(tier, 2)


def _synthesizer_ref(profile: dict, agents: dict, sampler_ref: dict) -> dict:
    pconf = (profile.get("parallel") or {}) if isinstance(profile.get("parallel"), dict) else {}
    mode = pconf.get("synthesizer")
    if not mode:
        # Tier-driven default.
        mode = "same_model" if profile.get("tier") == "mini" else "stronger_model"

    if mode == "same_model":
        return dict(sampler_ref)
    if mode == "user_select":
        # Use whatever's in the synthesizer slot; fall back to sampler.
        syn = (agents.get("synthesizer") or {}).get("model_ref")
        return dict(syn) if isinstance(syn, dict) else dict(sampler_ref)
    # stronger_model (default non-mini)
    syn = (agents.get("synthesizer") or {}).get("model_ref")
    if isinstance(syn, dict) and syn.get("name"):
        return dict(syn)
    # Fall back to sampler if no stronger model configured — don't block
    # the run just because the caller forgot to wire a synthesizer slot.
    log.info(
        "parallel: synthesizer mode=stronger_model but no synthesizer slot "
        "configured — falling back to sampler model (%s)",
        sampler_ref.get("name"),
    )
    return dict(sampler_ref)


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
    n = int(budget.get("parallel_n") or DEFAULT_PARALLEL_N)
    n = max(1, min(n, 5))  # cap at 5 — past that latency dominates

    temperatures = _temperatures_for(n, profile)
    sem_limit = _semaphore_limit(profile)

    sampler = agents.get("sampler") or {}
    sampler_ref = sampler.get("model_ref") or {
        "provider": profile.get("provider", "ollama"),
        "name": profile.get("planning_model", ""),
    }
    synthesizer_ref = _synthesizer_ref(profile, agents, sampler_ref)

    span_cm, attrs = make_root_span("parallel", profile, thread_id)
    tracer = get_tracer("localsmartz.patterns.parallel")
    semaphore = asyncio.Semaphore(sem_limit)

    # ctx-scoped counter the tests can read to verify semaphore bound.
    concurrency_tracker = {"current": 0, "peak": 0}

    with span_cm as root_span:
        for k, v in attrs.items():
            root_span.set_attribute(k, v)
        root_span.set_attribute("ls.parallel.n", n)
        root_span.set_attribute("ls.parallel.semaphore", sem_limit)

        yield {"type": "pattern_start", "pattern": "parallel"}

        async def _one_sample(index: int, temperature: float) -> dict:
            async with semaphore:
                concurrency_tracker["current"] += 1
                concurrency_tracker["peak"] = max(
                    concurrency_tracker["peak"], concurrency_tracker["current"]
                )
                try:
                    with tracer.start_as_current_span(
                        f"ls.sample.{index}"
                    ) as s_span:
                        s_span.set_attribute("ls.sample.index", index)
                        s_span.set_attribute("ls.sample.temperature", temperature)
                        start = time.time()
                        status = "ok"
                        content = ""
                        try:
                            # Propagate temperature via the ctx so runners
                            # that read it (cloud Anthropic/OpenAI) apply it.
                            # LocalOllamaRunner ignores it today; P4 enforcement
                            # is the pattern's job, not the runner's.
                            turn_ctx = dict(ctx or {})
                            turn_ctx["temperature"] = temperature
                            sample_system = (
                                sampler.get("system_focus")
                                or DEFAULT_SAMPLER_SYSTEM
                            )
                            turn = await runner.run_turn(
                                prompt,
                                tools=sampler.get("tools"),
                                model_ref=sampler_ref,
                                system=sample_system,
                                ctx=turn_ctx,
                            )
                            content = turn.get("content", "") or ""
                        except Exception as exc:  # noqa: BLE001
                            status = "error"
                            content = f"sample raised: {exc}"
                            log.warning(
                                "parallel: sample %d failed: %s", index, exc
                            )
                        finally:
                            duration_ms = int((time.time() - start) * 1000)
                            s_span.set_attribute("ls.sample.duration_ms", duration_ms)
                            s_span.set_attribute("ls.sample.status", status)
                    return {
                        "index": index,
                        "temperature": temperature,
                        "content": content,
                        "status": status,
                        "duration_ms": duration_ms,
                    }
                finally:
                    concurrency_tracker["current"] -= 1

        samples = await asyncio.gather(*(
            _one_sample(i + 1, temperatures[i]) for i in range(n)
        ))

        for s in samples:
            yield {
                "type": "turn",
                "role": f"sampler.{s['index']}",
                "content": s["content"],
            }

        root_span.set_attribute("ls.parallel.peak_concurrency", concurrency_tracker["peak"])

        # ── Synthesize ─────────────────────────────────────────────────
        with tracer.start_as_current_span("ls.synthesize") as syn_span:
            syn_prompt_lines = [
                f"USER QUESTION:\n{prompt}",
                "",
                "SAMPLE ANSWERS:",
            ]
            for s in samples:
                syn_prompt_lines.append(f"\n[{s['index']}] (temp={s['temperature']}):")
                syn_prompt_lines.append(s["content"])
                syn_prompt_lines.append("")
            syn_prompt = "\n".join(syn_prompt_lines)
            syn_span.set_attribute("ls.synthesize.input_chars", len(syn_prompt))
            syn_span.set_attribute("ls.synthesize.sample_count", len(samples))

            synth_system = (
                (agents.get("synthesizer") or {}).get("system_focus")
                or DEFAULT_SYNTH_SYSTEM
            )
            synth_turn = await runner.run_turn(
                syn_prompt,
                model_ref=synthesizer_ref,
                system=synth_system,
                ctx=ctx,
            )
            final_body = synth_turn.get("content", "") or ""

        yield {"type": "turn", "role": "final", "content": final_body}
        yield {"type": "done", "thread_id": thread_id or ""}
