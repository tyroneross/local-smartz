"""Ensure the fast path takes pinned Planner + trivial prompts.

The Planner agent's system prompt forces write_todos. For trivial factual
prompts ("what is the capital of france"), pushing through the full
DeepAgents graph with an 8B model causes tool-call hallucinations
(``repo_browser.write_todos``) and ~30s latency. The gate at
`serve._stream_research` must allow fast-path when the prompt is trivial
regardless of whether Planner is pinned."""

from localsmartz.profiles import is_fast_path


def test_trivial_prompt_is_fast_path():
    assert is_fast_path("what is the capital of france") is True
    assert is_fast_path("what's 2 + 2?") is True
    assert is_fast_path("hi") is True


def test_research_prompts_are_not_fast_path():
    assert is_fast_path("research the history of france") is False
    assert is_fast_path("analyze the capital city data") is False
    assert is_fast_path("write a report on france") is False


def test_fast_path_gate_allows_none_and_planner():
    """Reproduce the serve.py:1303 gate check as a unit. The gate should
    open for `focus_agent is None` OR `focus_agent == 'planner'`. Any
    other pinned agent still means the user asked for that specific role,
    so defer to the agent graph."""

    def allow_fast_path(focus_agent: str | None, prompt: str) -> bool:
        return (
            (focus_agent is None or focus_agent == "planner")
            and is_fast_path(prompt)
        )

    trivial = "what is the capital of france"
    assert allow_fast_path(None, trivial) is True
    assert allow_fast_path("planner", trivial) is True
    # Other focus agents opt out — the user pinned them for a reason.
    assert allow_fast_path("researcher", trivial) is False
    assert allow_fast_path("writer", trivial) is False
    # Research-y prompt never takes the fast path.
    assert allow_fast_path(None, "research lorem ipsum") is False
    assert allow_fast_path("planner", "research lorem ipsum") is False
