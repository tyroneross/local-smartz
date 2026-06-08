"""Shared runtime routing for local research requests.

Both the CLI and HTTP server need the same answer to one question:
which execution path should handle this prompt?

Keeping that decision in one place makes local behavior easier to reason
about and avoids the CLI drifting away from the server defaults.
"""

from __future__ import annotations

import re
from typing import Literal


ResearchRuntime = Literal[
    "fast_path",
    "graph_pipeline",
    "full_agent",
    "coding_harness",
    "coding_loop",
]
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

_ABSOLUTE_OR_RELATIVE_PATH_RE = re.compile(
    r"(?:^|\s)(?:~?/|/Users/|/private/|/tmp/|\./|\../)[^\s]+"
)

_CODING_STRONG_TERMS = (
    "coding harness",
    "code harness",
    "coding work",
    "light coding",
    "local coding",
    "build loop",
    "build-loop",
    "next.js app",
    "nextjs app",
    "react app",
    "app router",
    "codebase",
    "repo",
    "repository",
)

_CODING_ACTION_TERMS = (
    "implement",
    "wire",
    "wire up",
    "refactor",
    "edit",
    "patch",
    "test",
    "debug",
    "fix",
    "build",
    "create",
    "add",
    "update",
    "change",
    "propose",
    "implementation",
    "assess",
)

_CODING_LOOP_ACTION_TERMS = (
    "implement",
    "fix",
    "refactor",
    "edit",
    "patch",
    "test",
    "add",
    "update",
    "change",
    "wire",
    "wire up",
    "build a",
    "build out",
    "create",
)

_CODING_OBJECT_TERMS = (
    "harness",
    "plugin",
    "plugins",
    "mcp",
    "model selector",
    "ollama",
    "local model",
    "local models",
    "next.js",
    "nextjs",
    "react",
    "tsx",
    "component",
    "components",
    "screen",
    "ui",
    "fixture",
    "fixtures",
    "persona",
    "personas",
    "python",
    "swift",
    "pytest",
)

_CODING_TYPO_TERMS = (
    "codieng",
    "cidubg",
    "harnes",
    "garbess",
    "vyukd",
)


def _ordered_roles(roles: set[AgentRole]) -> tuple[AgentRole, ...]:
    return tuple(role for role in _ROLE_ORDER if role in roles)


def _term_in_text(text: str, term: str) -> bool:
    if " " in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def _any_term_in_text(text: str, terms: tuple[str, ...]) -> bool:
    return any(_term_in_text(text, term) for term in terms)


def is_coding_intent(prompt: str) -> bool:
    """Return True when the prompt should use repo-grounded coding context.

    This is intentionally conservative: generic technical comparison prompts
    such as "compare Python and Rust for a backend API" should still use the
    research graph. The coding harness is for local workspace / plugin /
    build-loop / edit-oriented prompts where answering without inspecting the
    repo is likely to produce generic advice.
    """
    if not isinstance(prompt, str):
        return False
    t = prompt.lower().strip()
    if not t:
        return False

    if _any_term_in_text(t, _CODING_STRONG_TERMS):
        return True

    has_path = _ABSOLUTE_OR_RELATIVE_PATH_RE.search(prompt) is not None
    if has_path and _any_term_in_text(t, _CODING_OBJECT_TERMS):
        return True

    has_action = _any_term_in_text(t, _CODING_ACTION_TERMS)
    has_object = _any_term_in_text(t, _CODING_OBJECT_TERMS)
    if has_action and has_object:
        return True

    has_typo = _any_term_in_text(t, _CODING_TYPO_TERMS)
    if has_typo and ("model" in t or "ollama" in t or "harness" in t):
        return True

    return False


def is_coding_loop_intent(prompt: str) -> bool:
    """True for action-oriented local coding tasks.

    ``coding_harness`` answers questions from repo context. ``coding_loop`` is
    the stronger route for prompts that ask Local Smartz to build, fix, patch,
    or refactor something. The separate predicate keeps explanatory prompts
    like "how can we use this as a coding harness?" read-only.
    """
    if not isinstance(prompt, str):
        return False
    t = prompt.lower().strip()
    if not t or not is_coding_intent(prompt):
        return False
    return _any_term_in_text(t, _CODING_LOOP_ACTION_TERMS)


def select_research_runtime(
    prompt: str,
    *,
    focus_agent: str | None = None,
) -> ResearchRuntime:
    """Return the runtime path for ``prompt``.

    Routing policy:
    - action-oriented local coding prompts use ``coding_loop``
    - local coding prompts use ``coding_harness`` unless focus mode is pinned
    - trivial prompts use ``fast_path`` unless focus mode pins a non-planner
      agent
    - the deterministic graph pipeline is the default non-focus path when
      enabled
    - focus mode and explicit graph opt-out fall back to the legacy full
      DeepAgents runtime
    """
    from localsmartz import pipeline as _pipeline
    from localsmartz.profiles import is_fast_path

    if focus_agent is None and is_coding_loop_intent(prompt):
        return "coding_loop"

    if focus_agent is None and is_coding_intent(prompt):
        return "coding_harness"

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
    "is_coding_intent",
    "is_coding_loop_intent",
    "select_agent_roles",
    "select_research_runtime",
]
