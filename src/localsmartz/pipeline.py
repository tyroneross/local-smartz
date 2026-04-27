"""Deterministic LangGraph pipeline for the orchestrator-routed research flow.

**Default backend as of 2026-04-13.** Set ``LOCALSMARTZ_PIPELINE=orchestrator``
(or ``deepagents``) in the environment to opt out and fall back to the legacy
prompt-driven DeepAgents path in ``agent.py``.

## What this fixes that the prompt-driven path can't

The prompt-driven orchestrator relies on the main-agent LLM to:
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

## Tool execution

Each specialist node is a real ReAct executor — ``langchain.agents.create_agent``
(the same entry point deepagents wraps internally) bound to the role's scoped
tool subset. Researcher actually calls ``web_search``; analyzer actually runs
``python_exec``; fact_checker spot-verifies via ``web_search``. Tool registry
and scoping reuse ``agent._build_tool_set`` and ``agent._scope_tools`` — no
duplication with the DeepAgents path.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Optional, TypedDict

from langchain.agents import create_agent as create_react_agent
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
    """True when the graph pipeline is active (NEW DEFAULT).

    Semantics (read each call, not cached, so tests can toggle via monkeypatch):
      - unset or empty → True (default is graph)
      - "graph", "1", "true", "yes", "on" → True
      - "orchestrator", "deepagents", "0", "false", "off", "no" → False

    Anything else defaults to True to avoid silently dropping users into the
    legacy path when a config typo happens."""
    val = os.environ.get("LOCALSMARTZ_PIPELINE", "").strip().lower()
    if val in ("orchestrator", "deepagents", "0", "false", "off", "no"):
        return False
    return True


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

def _role_llm(role: str, profile: dict):
    """Construct a bare chat model bound to the role's configured model.

    Provider-aware (Phase 2 cloud toggle): dispatches through
    ``agent._create_model`` so the pipeline and the main agent share a
    single LangChain-model factory. Keeps the registry-based F22 guard
    (qwen3.5 → ``reasoning=false``) and the one-way
    ``with_retry``-before-``create_deep_agent`` prohibition in a single
    place.

    Honors per-role overrides (profile + ``global_config.agent_models``) via
    ``get_agent_model``. Falls back to the profile planning model when the
    role has no explicit entry.

    **Do NOT wrap in ``with_retry`` at any caller of this.** ``create_agent``
    calls ``llm.bind_tools(...)`` on the return value; ``RunnableRetry``
    doesn't expose ``bind_tools``, which silently breaks tool registration
    for every specialist.
    """
    from localsmartz.agent import _create_model

    model_name = get_agent_model(profile, role) or profile.get("planning_model")
    return _create_model(profile, role, model_name=model_name)


def _role_system_prompt(role: str) -> str:
    meta = AGENT_ROLES.get(role)
    if not isinstance(meta, dict):
        return ""
    return meta.get("system_focus", "")


# ── Tool registry + per-role ReAct executors ────────────────────────────

def _build_tool_registry(profile: dict) -> list:
    """Assemble the flat tool list shared across all specialists.

    Delegates to ``agent._build_tool_set`` to avoid duplicating the
    profile-aware tool selection logic (lite vs full, plugin tools, MCP).
    Plugin tools and MCP are OFF here — the graph is a tighter surface by
    design, and MCP lifecycle (close_mcp_clients) doesn't mesh with
    LangGraph's lifetime model yet.
    """
    from localsmartz.agent import _build_tool_set
    tools, _mcp_clients = _build_tool_set(
        profile,
        include_plugin_tools=False,
        include_mcp=False,
    )
    return tools


def _scope_tools_for_role(all_tools: list, role: str) -> list:
    """Return the subset of ``all_tools`` whose names appear in the role's
    ``AGENT_ROLES[role]["tools"]`` allow-list. Reuses ``agent._scope_tools``
    so both backends use the same filter."""
    from localsmartz.agent import _scope_tools
    wanted = agent_tool_names(role)
    return _scope_tools(all_tools, wanted)


def _build_role_agent(role: str, profile: dict, all_tools: list):
    """Compile a ReAct executor for one role.

    Uses ``langchain.agents.create_agent`` — the canonical pattern deepagents
    wraps internally (``deepagents/graph.py`` line 12). Each specialist gets:

    - Its own ChatOllama (per-role model via ``get_agent_model``)
    - Its role-specific tool subset (no tool-name hallucination surface)
    - Its ``system_focus`` from ``AGENT_ROLES``

    Returns a CompiledStateGraph that accepts ``{"messages": [...]}`` and
    runs the full tool-call loop until the model stops calling tools.
    """
    llm = _role_llm(role, profile)
    tools = _scope_tools_for_role(all_tools, role)
    system_prompt = _role_system_prompt(role)
    return create_react_agent(
        llm,
        tools=tools,
        system_prompt=system_prompt,
    )


def _extract_final_text(result: dict) -> str:
    """Pull the last AI message's text content from a ReAct executor result.

    Handles both string content and list-of-segments content (some adapters).
    Returns empty string if no AI message is found."""
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    seg.get("text", "") if isinstance(seg, dict) else str(seg)
                    for seg in content
                )
    return ""


def _run_role_agent(
    role: str,
    user_content: str,
    agent_exec,
) -> str:
    """Invoke the compiled ReAct executor and return the assistant's final text.

    This is what replaced the old tool-less ``_invoke_role`` at runtime. The
    executor runs the full tool-call loop (researcher actually hits web_search,
    analyzer actually runs python_exec) and returns once the LLM produces a
    plain text response with no further tool calls.
    """
    result = agent_exec.invoke(
        {"messages": [{"role": "user", "content": user_content}]}
    )
    return _extract_final_text(result)


def _invoke_role(
    role: str,
    user_content: str,
    profile: dict,
) -> str:
    """Back-compat tool-less single-turn harness. Retained ONLY so the unit
    tests in ``tests/test_pipeline.py`` that monkeypatch
    ``pipeline._invoke_role`` continue to work. Runtime paths go through
    ``_run_role_agent``.
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


def _dispatch_role(
    role: str,
    user_content: str,
    profile: dict,
    agents: dict | None,
) -> str:
    """Unified dispatch: if a compiled ReAct executor exists in ``agents``,
    run it (real tool-calling path). Otherwise fall back to the tool-less
    ``_invoke_role`` — tests patch that symbol directly.

    Tests that pre-date the tool-execution rewire monkeypatch
    ``pipeline._invoke_role``; those stay green because we route through it
    when ``agents`` is None. Runtime ``build_graph`` always supplies a
    non-empty ``agents`` dict, so production goes through the real ReAct
    executor every time.
    """
    if agents and role in agents:
        return _run_role_agent(role, user_content, agents[role])
    return _invoke_role(role, user_content, profile)


def _emit_stage(sink: Optional[Callable[[dict], None]], role: str) -> None:
    """Best-effort SSE stage event. The sink is optional — unit tests and
    the synchronous ``run()`` helper pass None."""
    if sink is None:
        return
    try:
        sink({"type": "stage", "stage": role})
    except Exception:
        # Never let a broken sink take down a node. The heartbeat pulse in
        # serve.py will still fire; the UI loses one stage label.
        pass


def _make_researcher_node(
    profile: dict,
    agents: dict | None = None,
    sink: Optional[Callable[[dict], None]] = None,
):
    def node(state: PipelineState) -> dict:
        _emit_stage(sink, "researcher")
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
        output = _dispatch_role("researcher", user, profile, agents)
        return {"researcher_output": output}
    return node


def _make_analyzer_node(
    profile: dict,
    agents: dict | None = None,
    sink: Optional[Callable[[dict], None]] = None,
):
    def node(state: PipelineState) -> dict:
        _emit_stage(sink, "analyzer")
        user = (
            f"Original query: {state['prompt']}\n\n"
            "Run python_exec to compute actual numbers driven by the query "
            "(math, dates, unit conversions, local-file parsing). Do NOT "
            "describe what you would compute — return the real values. "
            "You are running in parallel with the researcher, so no prior "
            "research is available on disk yet."
        )
        output = _dispatch_role("analyzer", user, profile, agents)
        return {"analyzer_output": output}
    return node


def _make_fact_checker_node(
    profile: dict,
    agents: dict | None = None,
    sink: Optional[Callable[[dict], None]] = None,
):
    def node(state: PipelineState) -> dict:
        _emit_stage(sink, "fact_checker")
        user = (
            f"Query: {state['prompt']}\n\n"
            f"Researcher findings:\n{state.get('researcher_output', '(none)')}\n\n"
            f"Analyzer findings:\n{state.get('analyzer_output', '(none)')}\n\n"
            "Return ONLY the JSON verdict specified in your instructions."
        )
        output = _dispatch_role("fact_checker", user, profile, agents)
        verdict, missing = _parse_fact_verdict(output)
        return {
            "fact_verdict": verdict,
            "missing_facts": missing,
            "fact_check_iterations": state.get("fact_check_iterations", 0) + 1,
        }
    return node


def _make_writer_node(
    profile: dict,
    agents: dict | None = None,
    sink: Optional[Callable[[dict], None]] = None,
):
    def node(state: PipelineState) -> dict:
        _emit_stage(sink, "writer")
        user = (
            f"Query: {state['prompt']}\n\n"
            f"Research:\n{state.get('researcher_output', '(none)')}\n\n"
            f"Analysis:\n{state.get('analyzer_output', '(none)')}\n\n"
            "Synthesize the final answer following your pyramid-principle "
            "guidance (governing thought first, then key lines, then support)."
        )
        output = _dispatch_role("writer", user, profile, agents)
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

_SPECIALIST_ROLES: tuple[str, ...] = (
    "researcher",
    "analyzer",
    "fact_checker",
    "writer",
)


def is_role_agent_cache_enabled() -> bool:
    """True unless explicitly disabled for A/B benchmarking or debugging."""
    val = os.environ.get("LOCALSMARTZ_DISABLE_ROLE_AGENT_CACHE", "").strip().lower()
    return val not in ("1", "true", "yes", "on")


def _profile_cache_key(profile: dict) -> str:
    """Stable cache key for reusable specialist executors.

    We only cache the expensive per-profile role executors. The graph itself
    still compiles per request because the node closures capture a request-
    scoped SSE sink.
    """
    return json.dumps(profile, sort_keys=True, separators=(",", ":"), default=str)


@lru_cache(maxsize=8)
def _build_agents_for_roles_cached(profile_key: str) -> tuple[tuple[str, Any], ...]:
    """Compile specialist executors once per effective profile."""
    profile = json.loads(profile_key)
    all_tools = _build_tool_registry(profile)
    return tuple(
        (role, _build_role_agent(role, profile, all_tools))
        for role in _SPECIALIST_ROLES
    )


def clear_agents_cache() -> None:
    """Clear the cached specialist executors.

    Test code calls this to keep cache assertions isolated.
    """
    _build_agents_for_roles_cached.cache_clear()


def _build_agents_for_roles(profile: dict) -> dict:
    """Compile one ReAct executor per specialist role, sharing a single
    tool registry. Returns a dict ``{role: agent_executor}``. Roles whose
    scoping returns zero tools are still compiled — the LLM can still
    reason, it just won't have tool access (writer in particular, for
    instance, only produces text).
    """
    if not is_role_agent_cache_enabled():
        all_tools = _build_tool_registry(profile)
        return {
            role: _build_role_agent(role, profile, all_tools)
            for role in _SPECIALIST_ROLES
        }
    return dict(_build_agents_for_roles_cached(_profile_cache_key(profile)))


def build_graph(
    profile: dict | None = None,
    cwd: Path | None = None,
    *,
    sink: Optional[Callable[[dict], None]] = None,
    agents: dict | None = None,
):
    """Compile the orchestrator graph.

    Args:
        profile: Profile dict (auto-detected when None).
        cwd: Reserved for future checkpointer integration. Unused today.
        sink: Optional SSE event sink. When serve.py supplies one, each node
            emits a ``{"type":"stage","stage":<role>}`` event as it enters.
            Unit tests and the synchronous ``run()`` helper pass None.
        agents: Pre-compiled role executors. When None, the graph is
            tool-less and dispatches through ``_invoke_role`` — that path is
            only used by unit tests that patch ``pipeline._invoke_role``.
            Real runtime callers (``pipeline.run`` and ``serve._run_graph_pipeline``)
            get a populated dict via ``_build_agents_for_roles``.
    """
    profile = profile or get_profile()
    _ = cwd  # reserved

    builder: StateGraph = StateGraph(PipelineState)

    builder.add_node("entry", _orchestrator_entry)
    builder.add_node("researcher", _make_researcher_node(profile, agents, sink))
    builder.add_node("analyzer", _make_analyzer_node(profile, agents, sink))
    builder.add_node("fact_checker", _make_fact_checker_node(profile, agents, sink))
    builder.add_node("writer", _make_writer_node(profile, agents, sink))

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


def run(
    prompt: str,
    profile: dict | None = None,
    *,
    sink: Optional[Callable[[dict], None]] = None,
    with_agents: bool = False,
) -> dict:
    """Synchronous one-shot run — used by the CLI flag path and by tests.

    Args:
        prompt: The user query.
        profile: Profile dict (auto-detected when None).
        sink: Optional SSE sink for stage events.
        with_agents: Default False to keep existing graph-topology tests
            green (they monkeypatch ``_invoke_role`` directly). Set True
            when you want the real tool-calling behavior — production code
            paths go through ``serve._run_graph_pipeline`` which compiles
            agents explicitly.
    """
    profile = profile or get_profile()
    agents = _build_agents_for_roles(profile) if with_agents else None
    graph = build_graph(profile=profile, sink=sink, agents=agents)
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
    "_build_agents_for_roles",
    "clear_agents_cache",
    "is_role_agent_cache_enabled",
    "_build_role_agent",
    "_build_tool_registry",
    "_run_role_agent",
    "_scope_tools_for_role",
    "build_graph",
    "is_enabled",
    "run",
]
