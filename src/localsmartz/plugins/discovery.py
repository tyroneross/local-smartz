"""Discover plugins and standalone skills under a root directory."""

from __future__ import annotations

from pathlib import Path


def is_plugin_dir(path: Path) -> bool:
    """A plugin dir contains .claude-plugin/plugin.json."""
    return (path / ".claude-plugin" / "plugin.json").is_file()


def is_skill_dir(path: Path) -> bool:
    """A standalone skill dir contains SKILL.md."""
    return (path / "SKILL.md").is_file()


def find_plugin_dirs(root: Path) -> list[Path]:
    """Return immediate-child plugin directories under `root`.

    We only look one level deep — the expected layout is
    ``root/<plugin-name>/.claude-plugin/plugin.json``.
    """
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and is_plugin_dir(p))


def find_skill_dirs(plugin_root: Path) -> list[Path]:
    """Find all SKILL.md-containing directories under a plugin's skills/ folder.

    A plugin may have ``skills/<name>/SKILL.md`` or multiple nested skills.
    """
    skills_dir = plugin_root / "skills"
    if not skills_dir.is_dir():
        return []
    result: list[Path] = []
    for candidate in skills_dir.rglob("SKILL.md"):
        if candidate.is_file():
            result.append(candidate.parent)
    return sorted(result)


def find_command_files(plugin_root: Path) -> list[Path]:
    commands_dir = plugin_root / "commands"
    if not commands_dir.is_dir():
        return []
    return sorted(p for p in commands_dir.glob("*.md") if p.is_file())


def find_hooks_file(plugin_root: Path) -> Path | None:
    candidate = plugin_root / "hooks" / "hooks.json"
    return candidate if candidate.is_file() else None


def find_mcp_file(plugin_root: Path) -> Path | None:
    candidate = plugin_root / ".mcp.json"
    return candidate if candidate.is_file() else None


def find_agent_files(plugin_root: Path) -> list[Path]:
    """Return all agent .md files under a plugin root.

    Walks two locations:
    - ``<plugin_root>/agents/*.md``          (root-level agents)
    - ``<plugin_root>/skills/*/agents/*.md`` (skill-nested sub-agents)

    Returns absolute paths, sorted for determinism.
    """
    result: list[Path] = []
    # Root-level agents/
    root_agents = plugin_root / "agents"
    if root_agents.is_dir():
        for p in root_agents.glob("*.md"):
            if p.is_file():
                result.append(p)
    # Skill-nested agents/
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for skill_candidate in skills_dir.iterdir():
            if not skill_candidate.is_dir():
                continue
            nested = skill_candidate / "agents"
            if nested.is_dir():
                for p in nested.glob("*.md"):
                    if p.is_file():
                        result.append(p)
    return sorted(result)
