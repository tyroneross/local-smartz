"""Deterministic LangGraph pipeline for the orchestrator-routed research flow.

**Enabled only when** ``LOCALSMARTZ_PIPELINE=graph`` is set in the environment.
The default path remains the DeepAgents ``create_deep_agent(subagents=[...])``
pipeline in ``agent.py`` — this module is an alternate execution backend for
users hitting unreliable re-dispatch on small local models.

## What this fixes that the prompt-driven path can't

The prompt-driven orchestrator (default) relies on the main-agent LLM to:
1. Emit multiple ``task()`` calls in one turn for parallel fan-out.
2. Inspect the ``fact_checker`` return and decide to re-call ``researcher``
   with the ``missing_facts``.
3. Bound the loop at 2 iterations.

Small models (qwen3:8b) forget step 2 or skip step 1. This graph encodes
all three as deterministic edges:

- ``researcher`` and ``analyzer`` fire in parallel via LangGraph's ``Send``
- ``fact_checker`` is always visited after them
- A conditional edge reads ``state.fact_verdict`` and routes:
  - ``verdict == "ok"`` → ``writer`` → END
  - ``verdict == "needs_more"`` and iterations < 2 → ``researcher`` with
    ``state.missing_facts``
  - ``verdict == "needs_more"`` and iterations >= 2 → ``writer`` (cut loss)

The writer always terminates the graph. Role prompts, tool lists, and
models come from ``AGENT_ROLES`` and ``get_agent_model`` — no prompt
duplication with the default path.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send

from localsmartz.profiles import (
    AGENT_ROLES,
    agent_tool_names,
    get_agent_model,
    get_profile,
)


# Maximum re-dispatch rounds after the first fact-check. 2 means the
# researcher can run up to 3 times total (initial + 2 redos). More than
# that and we're just burning tokens on a model that can't converge.
MAX_FACT_CHECK_ITERATIONS = 2


def is_enabled() -> bool:
    """True when the env flag is set. Read each call (not cached) so tests
    can toggle via monkeypatch without reloading the module."""
    val = os.environ.get("LOCALSMARTZ_PIPELINE", "").strip().lower()
    return val in ("graph", "1", "true", "yes", "on")


# ── State shape ─────────────────────────────────────────────────────────

class PipelineState(TypedDict):
    """State flowing through the pipeline graph.

    ``messages`` accumulates LLM messages across nodes using LangGraph's
    ``add_messages`` reducer (append + dedupe by id). The other fields are
    per-run scratch space the supervisor reads to make routing decisions.
    """

    # User's original prompt, never mutated after initial input.
    prompt: str
    # Cumulative conversation history (system + all specialist outputs).
    # Used for deep reviewer / writer reads; intermediate nodes write to
    # their own ``_output`` slots for routing.
    messages: Annotated[list, add_messages]
    # Specialist outputs (most recent per role).
    researcher_output: str
    analyzer_output: str
    fact_verdict: Literal["", "ok", "needs_more"]
    missing_facts: list[str]
    fact_check_iterations: int
    # Final synthesis from writer — what the caller reads at graph end.
    final_answer: str


# ── Node builders ───────────────────────────────────────────────────────

def _role_llm(role: str, profile: dict) -> ChatOllama:
    """Construct a ChatOllama bound to the role's configured model.

    Honors per-role overrides (profile + ``global_config.agent_models``)
    via ``get_agent_model``. Falls back to the profile planning model when
    the role has no explicit entry.
    """
    model_name = get_agent_model(profile, role) or profile.get("planning_model")
    return ChatOllama(
        model=model_name,
        temperature=0,
        num_ctx=4096,
    )


def _role_system_prompt(role: str) -> str:
    meta = AGENT_ROLES.get(role)
    if not isinstance(meta, dict):
        return ""
    return meta.get("system_focus", "")


def _invoke_role(
    role: str,
    user_content: str,
    profile: dict,
) -> str:
    """Single-turn LLM call for a role, bypassing DeepAgents. Returns the
    assistant's text content. Tool calls are NOT executed here — this is a
    minimal harness sized for deterministic routing tests and small-model
    stability. Tool-bound specialist nodes are a future extension.
    """
    llm = _role_llm(role, profile)
    messages = [
        {"role": "system", "content": _role_system_prompt(role)},
        {"role": "user", "content": user_content},
    ]
    resp = llm.invoke(messages)
    content = getattr(resp, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Some LangChain adapters return content as list-of-segments.
        return "".join(
            seg.get("text", "") if isinstance(seg, dict) else str(seg)
            for seg in content
        )
    return str(content)


def _parse_fact_verdict(text: str) -> tuple[str, list[str]]:
    """Pull the JSON verdict out of the fact-checker's response.

    The role prompt pins the contract to
    ``{"verdict": "ok"|"needs_more", "missing_facts": [...]}`` but small
    models wrap the JSON in prose. Accept the first JSON object with the
    right shape anywhere in the response. On parse failure fall back to
    ``"ok"`` — the graph defers to the writer rather than spinning.
    """
    if not isinstance(text, str):
        return "ok", []
    # Greedy: take the first balanced-ish {...} block.
    m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", text, re.DOTALL)
    if not m:
        return "ok", []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return "ok", []
    verdict = obj.get("verdict", "ok")
    if verdict not in ("ok", "needs_more"):
        verdict = "ok"
    missing = obj.get("missing_facts", [])
    if not isinstance(missing, list):
        missing = []
    return verdict, [str(x) for x in missing if x]


# ── Nodes ───────────────────────────────────────────────────────────────

def _orchestrator_entry(state: PipelineState) -> dict:
    """Entry point. Today it's a no-op passthrough — the deterministic
    graph always fans out. Kept as a named node so Phoenix traces show
    the pipeline start clearly and so future versions can add routing
    logic here (e.g. classify "this is single-facet, skip analyzer").
    """
    return {
        "fact_check_iterations": 0,
        "researcher_output": "",
        "analyzer_output": "",
        "fact_verdict": "",
        "missing_facts": [],
        "final_answer": "",
    }


def _make_researcher_node(profile: dict):
    def node(state: PipelineState) -> dict:
        prompt = state["prompt"]
        missing = state.get("missing_facts") or []
        if missing:
            # Re-dispatch with the fact-checker's concrete gaps — this is
            # the whole point of the flag-gated path.
            user = (
                f"Original query: {prompt}\n\n"
                f"A prior fact-check flagged these gaps — address each one:\n- "
                + "\n- ".join(missing)
            )
        else:
            user = prompt
        output = _invoke_role("researcher", user, profile)
        return {"researcher_output": output}
    return node


def _make_analyzer_node(profile: dict):
    def node(state: PipelineState) -> dict:
        user = (
            f"Original query: {state['prompt']}\n\n"
            "Plan the computation or aggregation needed to answer this. "
            "Describe the steps you would run in python_exec."
        )
        output = _invoke_role("analyzer", user, profile)
        return {"analyzer_output": output}
    return node


def _make_fact_checker_node(profile: dict):
    def node(state: PipelineState) -> dict:
        user = (
            f"Query: {state['prompt']}\n\n"
            f"Researcher findings:\n{state.get('researcher_output', '(none)')}\n\n"
            f"Analyzer plan:\n{state.get('analyzer_output', '(none)')}\n\n"
            "Return ONLY the JSON verdict specified in your instructions."
        )
        output = _invoke_role("fact_checker", user, profile)
        verdict, missing = _parse_fact_verdict(output)
        return {
            "fact_verdict": verdict,
            "missing_facts": missing,
            "fact_check_iterations": state.get("fact_check_iterations", 0) + 1,
        }
    return node


def _make_writer_node(profile: dict):
    def node(state: PipelineState) -> dict:
        user = (
            f"Query: {state['prompt']}\n\n"
            f"Research:\n{state.get('researcher_output', '(none)')}\n\n"
            f"Analysis plan:\n{state.get('analyzer_output', '(none)')}\n\n"
            "Synthesize the final answer following your pyramid-principle "
            "guidance (governing thought first, then key lines, then support)."
        )
        output = _invoke_role("writer", user, profile)
        return {"final_answer": output}
    return node


# ── Routing ─────────────────────────────────────────────────────────────

def _fanout_from_entry(state: PipelineState) -> list[Send]:
    """After entry, fire researcher + analyzer in parallel via Send."""
    return [
        Send("researcher", state),
        Send("analyzer", state),
    ]


def _after_fact_check(state: PipelineState) -> str:
    """Decide the next node after fact-check.

    - verdict=ok → writer (happy path)
    - verdict=needs_more + budget left → researcher (re-dispatch with gaps)
    - verdict=needs_more + budget exhausted → writer (cut loss with best
      available research; better to ship an imperfect answer than spin)
    """
    verdict = state.get("fact_verdict", "ok")
    iters = state.get("fact_check_iterations", 0)
    if verdict == "needs_more" and iters <= MAX_FACT_CHECK_ITERATIONS:
        return "researcher"
    return "writer"


# ── Graph builder ───────────────────────────────────────────────────────

def build_graph(profile: dict | None = None, cwd: Path | None = None):
    """Compile the orchestrator graph. ``cwd`` accepted for parity with
    ``agent.create_agent`` — currently unused, reserved for a future
    checkpointer integration.
    """
    profile = profile or get_profile()
    _ = cwd  # reserved

    builder: StateGraph = StateGraph(PipelineState)

    builder.add_node("entry", _orchestrator_entry)
    builder.add_node("researcher", _make_researcher_node(profile))
    builder.add_node("analyzer", _make_analyzer_node(profile))
    builder.add_node("fact_checker", _make_fact_checker_node(profile))
    builder.add_node("writer", _make_writer_node(profile))

    builder.add_edge(START, "entry")
    # Parallel fan-out from entry.
    builder.add_conditional_edges(
        "entry",
        _fanout_from_entry,
        ["researcher", "analyzer"],
    )
    # Both specialists converge on fact_checker — LangGraph waits for all
    # inbound Send edges to settle before activating a downstream node.
    builder.add_edge("researcher", "fact_checker")
    builder.add_edge("analyzer", "fact_checker")
    # Fact-check verdict routes to writer or re-dispatches to researcher.
    builder.add_conditional_edges(
        "fact_checker",
        _after_fact_check,
        ["researcher", "writer"],
    )
    builder.add_edge("writer", END)

    return builder.compile()


def run(prompt: str, profile: dict | None = None) -> dict:
    """Synchronous one-shot run — used by the CLI flag path. Returns the
    final state dict. SSE-bridged invocations go through ``astream`` in
    ``serve.py`` so stage events can fire as each node finishes."""
    graph = build_graph(profile=profile)
    initial: PipelineState = {
        "prompt": prompt,
        "messages": [],
        "researcher_output": "",
        "analyzer_output": "",
        "fact_verdict": "",
        "missing_facts": [],
        "fact_check_iterations": 0,
        "final_answer": "",
    }
    result = graph.invoke(initial)
    return dict(result) if result else {}


# Tests and future SSE integration need this list. Kept as a tuple so
# accidental mutation surfaces as an error.
NODE_NAMES: tuple[str, ...] = (
    "entry",
    "researcher",
    "analyzer",
    "fact_checker",
    "writer",
)


__all__ = [
    "MAX_FACT_CHECK_ITERATIONS",
    "NODE_NAMES",
    "PipelineState",
    "build_graph",
    "is_enabled",
    "run",
]
