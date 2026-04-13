"""Parse validated plugin directories into typed dataclasses."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from localsmartz.plugins.discovery import (
    find_command_files,
    find_hooks_file,
    find_mcp_file,
    find_skill_dirs,
    is_plugin_dir,
    is_skill_dir,
)
from localsmartz.plugins.validator import parse_frontmatter


@dataclass
class Skill:
    name: str
    description: str
    body: str
    source_path: Path
    plugin_name: str | None  # None if standalone skill


@dataclass
class Command:
    name: str
    description: str
    allowed_tools: list[str]
    body: str
    source_path: Path
    plugin_name: str


@dataclass
class MCPServer:
    name: str
    plugin_name: str
    command: str
    args: list[str]


@dataclass
class Plugin:
    name: str
    version: str
    description: str
    author_name: str
    install_path: Path
    skills: list[Skill] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)
    mcp_servers: list[MCPServer] = field(default_factory=list)
    has_hooks: bool = False


def _substitute_plugin_root(value: str, plugin_root: Path) -> str:
    return value.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))


def load_skill(skill_md_path: Path, plugin_name: str | None) -> Skill | None:
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except OSError:
        return None
    data, body, _err = parse_frontmatter(text)
    if data is None:
        return None
    name = data.get("name", "").strip()
    description = data.get("description", "").strip()
    if not name:
        return None
    return Skill(
        name=name,
        description=description,
        body=body,
        source_path=skill_md_path,
        plugin_name=plugin_name,
    )


def load_command(cmd_path: Path, plugin_name: str) -> Command | None:
    try:
        text = cmd_path.read_text(encoding="utf-8")
    except OSError:
        return None
    data, body, _err = parse_frontmatter(text)
    if data is None:
        return None
    description = data.get("description", "").strip()
    tools_raw = data.get("allowed-tools", "")
    allowed = [t.strip() for t in tools_raw.split(",") if t.strip()]
    return Command(
        name=cmd_path.stem,
        description=description,
        allowed_tools=allowed,
        body=body,
        source_path=cmd_path,
        plugin_name=plugin_name,
    )


def load_mcp_servers(mcp_path: Path, plugin_name: str, plugin_root: Path) -> list[MCPServer]:
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    servers = data.get("mcpServers") if isinstance(data, dict) and "mcpServers" in data else data
    if not isinstance(servers, dict):
        return []
    out: list[MCPServer] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        cmd = spec.get("command")
        if not isinstance(cmd, str):
            continue
        args = spec.get("args") or []
        if not isinstance(args, list):
            continue
        substituted_args = [
            _substitute_plugin_root(str(a), plugin_root) for a in args if isinstance(a, str)
        ]
        substituted_cmd = _substitute_plugin_root(cmd, plugin_root)
        out.append(
            MCPServer(
                name=name,
                plugin_name=plugin_name,
                command=substituted_cmd,
                args=substituted_args,
            )
        )
    return out


def load_plugin(plugin_root: Path) -> Plugin | None:
    """Parse a fully-validated plugin directory. Returns None if essential metadata missing."""
    if not is_plugin_dir(plugin_root):
        return None
    try:
        meta = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None

    name = meta.get("name", "")
    if not name:
        return None
    plugin = Plugin(
        name=name,
        version=meta.get("version", ""),
        description=meta.get("description", ""),
        author_name=(meta.get("author") or {}).get("name", "") if isinstance(meta.get("author"), dict) else "",
        install_path=plugin_root,
    )

    for skill_dir in find_skill_dirs(plugin_root):
        skill = load_skill(skill_dir / "SKILL.md", plugin_name=name)
        if skill is not None:
            plugin.skills.append(skill)

    for cmd_path in find_command_files(plugin_root):
        command = load_command(cmd_path, plugin_name=name)
        if command is not None:
            plugin.commands.append(command)

    mcp = find_mcp_file(plugin_root)
    if mcp is not None:
        plugin.mcp_servers = load_mcp_servers(mcp, plugin_name=name, plugin_root=plugin_root)

    plugin.has_hooks = find_hooks_file(plugin_root) is not None
    return plugin


def load_standalone_skill(skill_dir: Path) -> Skill | None:
    if not is_skill_dir(skill_dir):
        return None
    return load_skill(skill_dir / "SKILL.md", plugin_name=None)
