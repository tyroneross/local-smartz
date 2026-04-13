"""Bridge plugin Registry -> agent tools / system prompt.

Helper module consumed by ``localsmartz.agent``. Keeps plugin-aware wiring
out of ``agent.py`` so the main agent file stays focused on the LangGraph
composition.

Three integration points:

1. ``build_skill_prompt_section`` -- renders active skills as markdown for
   injection into the system prompt.
2. ``build_plugin_tools`` -- turns registered plugin commands into
   LangChain-compatible tool callables. v1 is "guidance-only": the tool
   returns the command body text so the model can follow its instructions.
   No shell execution.
3. ``build_mcp_tools`` -- spawns each registered MCP server, enumerates
   its tools, and wraps each as a LangChain tool. Returns the started
   clients alongside so the caller can close them at session end.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from localsmartz import global_config
from localsmartz.plugins import Registry
from localsmartz.plugins.loader import Command, MCPServer, Skill
from localsmartz.plugins.mcp_client import MCPClient

# Env vars passed through to plugin command shells. Everything else is stripped
# so plugins don't see API keys, OAuth tokens, or other ambient credentials.
_SAFE_ENV_KEYS = ("HOME", "PATH", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR")
_SHELL_BLOCK_RE = re.compile(
    r"```(?:bash|sh|shell|zsh)\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE
)
_DEFAULT_EXEC_TIMEOUT = 30
_MAX_OUTPUT_BYTES = 8192

try:  # pragma: no cover -- optional dep path already verified in adapter
    from langchain_core.tools import StructuredTool, tool as _lc_tool  # type: ignore

    _HAS_LANGCHAIN = True
except Exception:  # pragma: no cover
    StructuredTool = None  # type: ignore
    _lc_tool = None  # type: ignore
    _HAS_LANGCHAIN = False


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skills -> prompt
# ---------------------------------------------------------------------------

def get_active_skills(registry: Registry | None = None) -> list[Skill]:
    """Resolve active skills via global_config + registry."""
    reg = registry or Registry.from_default_root()
    try:
        active_names = set(global_config.get("active_skills") or [])
    except ValueError:
        active_names = set()
    if not active_names:
        return []
    # list_skills(active_only=True) reads the registry's own active file;
    # we intersect with global_config for the Settings UI to be the
    # source of truth for the agent runtime.
    all_skills = reg.list_skills(active_only=False)
    return [s for s in all_skills if s.name in active_names]


def build_skill_prompt_section(skills: list[Skill]) -> str:
    """Render a list of skills as a markdown section for the system prompt.

    Returns an empty string if ``skills`` is empty -- caller can unconditionally
    append without worrying about a stray heading.
    """
    if not skills:
        return ""
    parts: list[str] = [
        "",
        "## Available Skills",
        "",
        "The following skills are loaded and available as guidance. "
        "Reference them when relevant.",
        "",
    ]
    for i, s in enumerate(skills):
        if i > 0:
            parts.append("---")
            parts.append("")
        parts.append(f"### {s.name}")
        parts.append(s.description)
        parts.append("")
        if s.body.strip():
            parts.append(s.body.rstrip())
            parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Commands -> tools
# ---------------------------------------------------------------------------

def _safe_tool_name(raw: str) -> str:
    """Return a name usable as a Python / LangChain tool identifier."""
    cleaned = []
    for ch in raw:
        if ch.isalnum() or ch == "_":
            cleaned.append(ch)
        else:
            cleaned.append("_")
    name = "".join(cleaned).strip("_") or "plugin_tool"
    if name[0].isdigit():
        name = f"t_{name}"
    return name


def _extract_shell_block(body: str) -> str | None:
    """Return the first fenced bash/sh/shell/zsh code block from ``body``.

    Returns None if no shell block is present.
    """
    m = _SHELL_BLOCK_RE.search(body or "")
    if not m:
        return None
    return m.group(1).strip() or None


def _resolve_workspace_cwd() -> str:
    """Best workspace dir to run plugin commands in.

    Order: global_config['workspace'] > $HOME > current cwd.
    """
    try:
        ws = global_config.get("workspace") or ""
    except Exception:  # noqa: BLE001
        ws = ""
    if ws and Path(str(ws)).is_dir():
        return str(ws)
    home = os.path.expanduser("~")
    if Path(home).is_dir():
        return home
    return os.getcwd()


def _stripped_env() -> dict[str, str]:
    """Return an env dict containing only safe pass-through keys."""
    return {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}


def _exec_shell(snippet: str, timeout: int) -> str:
    """Run a shell snippet and return a string suitable for surfacing to the LLM."""
    try:
        result = subprocess.run(
            ["bash", "-c", snippet],
            cwd=_resolve_workspace_cwd(),
            env=_stripped_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"
    except FileNotFoundError:
        return "ERROR: bash not found on PATH"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {e}"

    out = (result.stdout or "")[:_MAX_OUTPUT_BYTES]
    if result.returncode != 0:
        err = (result.stderr or "")[:2048]
        return f"ERROR (exit {result.returncode}): {err}\n\nstdout:\n{out}"
    return out


def _make_command_tool(cmd: Command, timeout: int = _DEFAULT_EXEC_TIMEOUT) -> Any:
    """Wrap one plugin command as a tool callable.

    Behavior:
      * If ``Bash`` is in ``cmd.allowed_tools``, extract the first fenced shell
        block from the command body and run it via ``bash -c`` with a stripped
        environment and ``timeout`` seconds.
      * Otherwise (or when no shell block is present), return the command body
        as guidance text so the model can follow it manually.

    Trust model: installed plugins are trusted (the user explicitly installed
    them via ``localsmartz plugins install``). v1 ships subprocess execution
    with timeout + stripped env; richer sandboxing (sandbox-exec / bubblewrap)
    is deferred.
    """
    tool_name = _safe_tool_name(f"plugin_{cmd.plugin_name}_{cmd.name}")
    description = cmd.description or f"Plugin command {cmd.plugin_name}/{cmd.name}"

    has_bash = "Bash" in (cmd.allowed_tools or [])
    snippet = _extract_shell_block(cmd.body) if has_bash else None

    def run(arguments: str = "") -> str:
        prefix = f"[plugin:{cmd.plugin_name}/{cmd.name}]"
        if arguments:
            prefix += f" args={arguments!r}"
        if snippet is None:
            tag = "guidance-only" if not has_bash else "guidance-only — no shell block found"
            return f"{prefix} [{tag}]\n\n{cmd.body}"
        return f"{prefix}\n{_exec_shell(snippet, timeout)}"

    if _HAS_LANGCHAIN and StructuredTool is not None:
        return StructuredTool.from_function(
            func=run, name=tool_name, description=description
        )
    run.name = tool_name  # type: ignore[attr-defined]
    run.description = description  # type: ignore[attr-defined]
    return run


def build_plugin_tools(
    profile_name: str,
    registry: Registry | None = None,
) -> list[Any]:
    """Return a list of LangChain-compatible tool callables for plugin commands.

    The lite profile gets an empty list -- smaller models already struggle
    with the tool budget, and guidance tools are low-leverage compared to
    the core research tools.
    """
    if profile_name == "lite":
        return []
    reg = registry or Registry.from_default_root()
    commands = reg.list_commands()
    return [_make_command_tool(c) for c in commands]


# ---------------------------------------------------------------------------
# MCP servers -> tools
# ---------------------------------------------------------------------------

def _make_mcp_tool(
    client: MCPClient,
    server: MCPServer,
    tool_schema: dict[str, Any],
) -> Any:
    """Wrap one MCP tool. Reuses the already-started ``client``."""
    raw_name = tool_schema.get("name") or "tool"
    description = tool_schema.get("description") or f"MCP tool {raw_name}"
    full_name = _safe_tool_name(f"mcp_{server.plugin_name}_{server.name}_{raw_name}")

    def run(**kwargs: Any) -> Any:
        """Invoke the MCP tool on the shared client."""
        try:
            return client.call_tool(raw_name, kwargs)
        except Exception as e:  # noqa: BLE001 -- surfaced to the agent
            return {"error": f"MCP call failed: {e}"}

    if _HAS_LANGCHAIN and StructuredTool is not None:
        return StructuredTool.from_function(
            func=run, name=full_name, description=description
        )
    run.name = full_name  # type: ignore[attr-defined]
    run.description = description  # type: ignore[attr-defined]
    return run


def build_mcp_tools(
    registry: Registry | None = None,
) -> tuple[list[Any], list[MCPClient]]:
    """Start every registered MCP server and return (tools, started_clients).

    Failures are logged and skipped -- one misbehaving server must not crash
    the agent. Caller is responsible for calling ``close()`` on each returned
    client when the session ends.
    """
    reg = registry or Registry.from_default_root()
    tools: list[Any] = []
    clients: list[MCPClient] = []

    for server in reg.list_mcp_servers():
        client = MCPClient(server.command, server.args)
        try:
            client.start()
            client.initialize()
            schemas = client.list_tools()
        except Exception as e:  # noqa: BLE001 -- defensive; includes FileNotFoundError
            log.warning(
                "MCP server %s/%s failed to start: %s",
                server.plugin_name,
                server.name,
                e,
            )
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            continue

        clients.append(client)
        for schema in schemas:
            if not isinstance(schema, dict):
                continue
            tools.append(_make_mcp_tool(client, server, schema))

    return tools, clients


def close_mcp_clients(clients: list[MCPClient]) -> None:
    """Best-effort shutdown for a list of MCP clients."""
    for c in clients:
        try:
            c.close()
        except Exception as e:  # noqa: BLE001
            log.debug("Error closing MCP client: %s", e)
