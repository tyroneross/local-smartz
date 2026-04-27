"""Pattern tests: single, chain, router, critic_loop.

All patterns are fed a stub runner that returns canned turn dicts so the
assertions don't require a live Ollama server.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from localsmartz.patterns import REGISTRY, get_pattern
from localsmartz.patterns.critic_loop import _parse_critic, _enforce_mini_tier_colocation


class StubRunner:
    """Returns a queue of canned responses in order.

    If the queue empties, returns an empty AssistantTurn.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def run_turn(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        model_ref: dict[str, Any],
        system: str | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {"prompt": prompt, "model_ref": model_ref, "system": system}
        )
        if self.responses:
            return self.responses.pop(0)
        return {"content": "", "tool_calls": [], "usage": {}, "model": model_ref["name"], "provider": model_ref["provider"]}


def _drain(async_gen) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def _run():
        async for ev in async_gen:
            events.append(ev)

    asyncio.run(_run())
    return events


def test_registry_exposes_all_phase_1_2_and_3_patterns() -> None:
    assert set(REGISTRY.keys()) == {
        "single", "chain", "router", "critic_loop",
        # Phase 3 (2026-04-23)
        "reflection", "orchestrator", "parallel",
    }
    assert get_pattern("single")["required_roles"] == ["primary"]
    assert get_pattern("critic_loop")["required_roles"] == ["writer", "critic"]
    assert get_pattern("reflection")["required_roles"] == ["primary", "reflector"]
    assert get_pattern("orchestrator")["required_roles"] == ["orchestrator", "worker"]
    assert get_pattern("parallel")["required_roles"] == ["sampler", "synthesizer"]


def test_single_pattern_yields_turn_and_done() -> None:
    runner = StubRunner([{"content": "42", "tool_calls": [], "model": "m", "provider": "ollama"}])
    agents = {"primary": {"model_ref": {"provider": "ollama", "name": "m"}}}
    profile = {"name": "lite", "tier": "mini", "planning_model": "m"}
    events = _drain(
        REGISTRY["single"]["run"](
            "what is the meaning of life?",
            agents=agents,
            profile=profile,
            stream=True,
            runner=runner,
        )
    )
    types = [e["type"] for e in events]
    assert types == ["pattern_start", "turn", "done"]
    assert events[1]["content"] == "42"


def test_chain_pattern_feeds_output_to_next_stage() -> None:
    runner = StubRunner(
        [
            {"content": "gather output", "tool_calls": [], "model": "m", "provider": "ollama"},
            {"content": "analyze output", "tool_calls": [], "model": "m", "provider": "ollama"},
            {"content": "final answer", "tool_calls": [], "model": "m", "provider": "ollama"},
        ]
    )
    agents = {"primary": {"model_ref": {"provider": "ollama", "name": "m"}}}
    profile = {"name": "lite", "tier": "mini", "planning_model": "m"}
    events = _drain(REGISTRY["chain"]["run"]("question", agents=agents, profile=profile, stream=True, runner=runner))
    turn_contents = [e["content"] for e in events if e["type"] == "turn"]
    assert turn_contents == ["gather output", "analyze output", "final answer"]
    # Stage 2 receives stage 1 output as input.
    assert runner.calls[1]["prompt"] == "gather output"


def test_router_pattern_picks_specialist() -> None:
    runner = StubRunner(
        [
            {"content": "code", "tool_calls": [], "model": "m", "provider": "ollama"},
            {"content": "specialist answer", "tool_calls": [], "model": "m", "provider": "ollama"},
        ]
    )
    agents = {
        "router": {"model_ref": {"provider": "ollama", "name": "m"}},
        "specialist": {"model_ref": {"provider": "ollama", "name": "m"}},
    }
    profile = {"name": "lite", "tier": "mini", "planning_model": "m"}
    ctx = {
        "specialists": [
            {"label": "code", "system_focus": "you are a coder"},
            {"label": "research", "system_focus": "you research"},
        ]
    }
    events = _drain(
        REGISTRY["router"]["run"]("write a fib function", agents=agents, profile=profile, stream=True, runner=runner, ctx=ctx)
    )
    roles = [e.get("role") for e in events if e["type"] == "turn"]
    assert roles == ["router", "specialist:code"]


def test_critic_loop_parses_json_verdict() -> None:
    score, verdict, feedback = _parse_critic(
        json.dumps({"score": 0.9, "verdict": "pass", "feedback": "great"})
    )
    assert score == 0.9
    assert verdict == "pass"
    assert feedback == "great"


def test_critic_loop_extracts_json_from_prose() -> None:
    raw = "Here's my verdict:\n{\"score\": 0.5, \"verdict\": \"revise\", \"feedback\": \"add sources\"}\nthanks"
    score, verdict, _ = _parse_critic(raw)
    assert score == 0.5
    assert verdict == "revise"


def test_critic_loop_mini_tier_forces_colocation() -> None:
    agents = {
        "writer": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
        "critic": {"model_ref": {"provider": "ollama", "name": "qwen3.5:27b"}},
    }
    adjusted = _enforce_mini_tier_colocation(agents, {"tier": "mini"})
    assert adjusted["critic"]["model_ref"]["name"] == "qwen3.5:9b"


def test_critic_loop_standard_tier_preserves_different_models() -> None:
    agents = {
        "writer": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
        "critic": {"model_ref": {"provider": "ollama", "name": "qwen3.5:27b"}},
    }
    adjusted = _enforce_mini_tier_colocation(agents, {"tier": "standard"})
    assert adjusted["critic"]["model_ref"]["name"] == "qwen3.5:27b"


def test_critic_loop_stops_on_pass_verdict() -> None:
    runner = StubRunner(
        [
            # iter 1: writer draft, critic passes immediately
            {"content": "draft 1", "tool_calls": [], "model": "m", "provider": "ollama"},
            {
                "content": json.dumps({"score": 0.9, "verdict": "pass", "feedback": "ok"}),
                "tool_calls": [],
                "model": "m",
                "provider": "ollama",
            },
        ]
    )
    agents = {
        "writer": {"model_ref": {"provider": "ollama", "name": "m"}},
        "critic": {"model_ref": {"provider": "ollama", "name": "m"}},
    }
    profile = {"name": "lite", "tier": "mini", "planning_model": "m"}
    events = _drain(
        REGISTRY["critic_loop"]["run"]("prompt", agents=agents, profile=profile, stream=True, runner=runner)
    )
    iteration_events = [e for e in events if e["type"] == "iteration"]
    assert len(iteration_events) == 1
    assert iteration_events[0]["verdict"] == "pass"


def test_critic_loop_honors_max_iterations() -> None:
    # Critic always says revise; loop must still stop at max_iterations.
    revise = json.dumps({"score": 0.2, "verdict": "revise", "feedback": "try harder"})
    draft = {"content": "draft", "tool_calls": [], "model": "m", "provider": "ollama"}
    critic = {"content": revise, "tool_calls": [], "model": "m", "provider": "ollama"}
    runner = StubRunner([draft, critic, draft, critic, draft, critic])

    agents = {
        "writer": {"model_ref": {"provider": "ollama", "name": "m"}},
        "critic": {"model_ref": {"provider": "ollama", "name": "m"}},
    }
    profile = {
        "name": "lite",
        "tier": "mini",
        "planning_model": "m",
        "budget": {"max_iterations": 2, "max_turns": 10},
    }
    events = _drain(
        REGISTRY["critic_loop"]["run"]("prompt", agents=agents, profile=profile, stream=True, runner=runner)
    )
    iteration_events = [e for e in events if e["type"] == "iteration"]
    assert len(iteration_events) == 2  # Hits max_iterations.
