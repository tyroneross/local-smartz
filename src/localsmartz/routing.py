"""Shared runtime routing for local research requests.

Both the CLI and HTTP server need the same answer to one question:
which execution path should handle this prompt?

Keeping that decision in one place makes local behavior easier to reason
about and avoids the CLI drifting away from the server defaults.
"""

from __future__ import annotations

from typing import Literal


ResearchRuntime = Literal["fast_path", "graph_pipeline", "full_agent"]


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


__all__ = ["ResearchRuntime", "select_research_runtime"]
