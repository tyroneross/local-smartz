"""Shared runtime routing for local research requests.

Both the CLI and HTTP server need the same answer to one question:
which execution path should handle this prompt?

Keeping that decision in one place makes local behavior easier to reason
about and avoids the CLI drifting away from the server defaults.
"""

from __future__ import annotations

from typing import Literal


ResearchRuntime = Literal["fast_path", "graph_pipeline", "full_agent"]
AgentRole = Literal["planner", "researcher", "analyzer", "fact_checker", "writer"]

_ROLE_ORDER: tuple[AgentRole, ...] = (
    "planner",
    "researcher",
    "analyzer",
    "fact_checker",
    "writer",
)

_PLANNER_TERMS = (
    "plan",
    "steps",
    "roadmap",
    "debug",
    "fix",
    "investigate why",
)
_RESEARCH_TERMS = (
    "research",
    "latest",
    "current",
    "recent",
    "today",
    "news",
    "sources",
    "citations",
    "compare",
    "recommend",
    "which model",
    "market",
    "stock",
    "price",
    "earnings",
    "climate",
    "read ",
    "summarize this",
    "pdf",
    "document",
    "url",
    "debug why",
)
_ANALYZER_TERMS = (
    "analyze",
    "calculate",
    "compute",
    "chart",
    "graph",
    "csv",
    "spreadsheet",
    "data",
    "benchmark",
    "statistics",
    "revenue",
    "q3",
    "compare",
    "debug",
)
_FACT_CHECK_TERMS = (
    "research",
    "latest",
    "current",
    "recent",
    "today",
    "news",
    "sources",
    "citations",
    "compare",
    "report",
    "stock",
    "price",
    "market",
    "earnings",
    "climate",
    "q3",
)
_WRITER_TERMS = (
    "write",
    "report",
    "summarize",
    "draft",
    "compose",
)


def _ordered_roles(roles: set[AgentRole]) -> tuple[AgentRole, ...]:
    return tuple(role for role in _ROLE_ORDER if role in roles)


def select_research_runtime(
    prompt: str,
    *,
    focus_agent: str | None = None,
) -> ResearchRuntime:
    """Return the runtime path for ``prompt``.

    Routing policy:
    - trivial prompts use ``fast_path`` unless focus mode pins a non-planner
      agent
    - the deterministic graph pipeline is the default non-focus path when
      enabled
    - focus mode and explicit graph opt-out fall back to the legacy full
      DeepAgents runtime
    """
    from localsmartz import pipeline as _pipeline
    from localsmartz.profiles import is_fast_path

    allow_fast_path = focus_agent is None or focus_agent == "planner"
    if allow_fast_path and is_fast_path(prompt):
        return "fast_path"

    if focus_agent is None and _pipeline.is_enabled():
        return "graph_pipeline"

    return "full_agent"


def select_agent_roles(prompt: str) -> tuple[AgentRole, ...]:
    """Return the specialist roles likely needed for a non-fast-path prompt.

    This is intentionally deterministic and cheap. It does not change the
    current graph execution path yet; it gives us an eval-able contract for
    future role pruning and keeps model/agent routing decisions visible.
    """
    from localsmartz.profiles import is_fast_path

    if not isinstance(prompt, str) or is_fast_path(prompt):
        return ()

    t = prompt.lower().strip()
    roles: set[AgentRole] = set()

    if any(term in t for term in _PLANNER_TERMS):
        roles.add("planner")
    if any(term in t for term in _RESEARCH_TERMS):
        roles.add("researcher")
    if any(term in t for term in _ANALYZER_TERMS):
        roles.add("analyzer")
    if any(term in t for term in _FACT_CHECK_TERMS):
        roles.add("fact_checker")
    if any(term in t for term in _WRITER_TERMS) or roles:
        roles.add("writer")

    if not roles:
        roles.add("writer")
    return _ordered_roles(roles)


__all__ = [
    "AgentRole",
    "ResearchRuntime",
    "select_agent_roles",
    "select_research_runtime",
]
