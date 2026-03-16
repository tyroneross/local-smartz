"""Subagent prompt templates for the DeepAgents task tool.

DeepAgents' built-in `task` tool spawns subagents with context isolation.
These templates provide focused instructions the main agent can reference
when delegating specialized work via task().

The main agent decides when and how to delegate — these are reference
prompts, not rigid agent definitions.
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """Load a subagent prompt template by name.

    Available: planner, researcher, analyzer, writer, reviewer
    """
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise ValueError(f"No prompt template: {name}. Available: {[p.stem for p in _PROMPTS_DIR.glob('*.md')]}")
    return path.read_text(encoding="utf-8")


def list_prompts() -> list[str]:
    """List available prompt template names."""
    return sorted(p.stem for p in _PROMPTS_DIR.glob("*.md"))
