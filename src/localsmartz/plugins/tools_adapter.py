"""Convert plugin commands and MCP tools into LangChain-compatible callables.

This is only the adapter shell — wiring into ``agent.py`` is out of scope.
Deliberately minimal: depends on ``langchain_core.tools.StructuredTool`` if
available, and falls back to a plain callable wrapper otherwise so imports
don't fail on machines without langchain installed.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any, Callable

from localsmartz.plugins.loader import Command, MCPServer
from localsmartz.plugins.mcp_client import MCPClient

try:  # pragma: no cover — optional dep
    from langchain_core.tools import StructuredTool, Tool  # type: ignore

    _HAS_LANGCHAIN = True
except Exception:  # pragma: no cover
    StructuredTool = None  # type: ignore
    Tool = None  # type: ignore
    _HAS_LANGCHAIN = False


def _wrap_callable(name: str, description: str, func: Callable[..., Any]) -> Any:
    """Return a LangChain Tool if available, else a plain object with .name/.run."""
    if _HAS_LANGCHAIN and StructuredTool is not None:
        return StructuredTool.from_function(
            func=func, name=name, description=description
        )

    class _ShimTool:
        def __init__(self, name: str, description: str, func: Callable[..., Any]):
            self.name = name
            self.description = description
            self._func = func

        def run(self, *args: Any, **kwargs: Any) -> Any:
            return self._func(*args, **kwargs)

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return self._func(*args, **kwargs)

    return _ShimTool(name, description, func)


# ---------------------------------------------------------------------------
# Command → tool
# ---------------------------------------------------------------------------

def command_to_tool(cmd: Command) -> Any:
    """Expose a plugin command as a callable tool.

    The command body is a markdown file with shell hints; we don't parse it
    here. For now the tool returns the body text so the agent can execute or
    render it.
    """

    def run(arguments: str = "") -> str:
        # Minimal: return the command body + arguments.
        # Real execution goes through the agent's tool router.
        return f"[plugin:{cmd.plugin_name}/{cmd.name}] args={arguments!r}\n\n{cmd.body}"

    tool_name = f"{cmd.plugin_name}__{cmd.name}".replace("-", "_")
    description = cmd.description or f"Plugin command {cmd.name}"
    return _wrap_callable(tool_name, description, run)


def commands_to_tools(commands: list[Command]) -> list[Any]:
    return [command_to_tool(c) for c in commands]


# ---------------------------------------------------------------------------
# MCP server tool → tool
# ---------------------------------------------------------------------------

def mcp_tool_to_callable(
    server: MCPServer,
    tool_schema: dict[str, Any],
    client_factory: Callable[[MCPServer], MCPClient] | None = None,
) -> Any:
    """Expose a single MCP tool as a callable.

    ``tool_schema`` is an item from ``tools/list`` — has ``name`` and ``description``.
    ``client_factory`` is an override used by tests; default spawns a fresh client per call.
    """
    tool_name = tool_schema.get("name", "unnamed_tool")
    tool_desc = tool_schema.get("description") or f"MCP tool {tool_name}"
    full_name = f"{server.plugin_name}__{server.name}__{tool_name}".replace("-", "_")

    def make_client() -> MCPClient:
        if client_factory is not None:
            return client_factory(server)
        return MCPClient(server.command, server.args)

    def run(**kwargs: Any) -> Any:
        with make_client() as client:
            client.initialize()
            return client.call_tool(tool_name, kwargs)

    return _wrap_callable(full_name, tool_desc, run)


def mcp_server_to_tools(
    server: MCPServer,
    client_factory: Callable[[MCPServer], MCPClient] | None = None,
) -> list[Any]:
    """Spawn the server once to enumerate tools, then build callables.

    Each callable spawns its own client on invoke — simpler isolation, at the
    cost of per-call startup. Optimizations (persistent clients, pooling) are
    deferred to agent wiring.
    """

    def factory() -> MCPClient:
        if client_factory is not None:
            return client_factory(server)
        return MCPClient(server.command, server.args)

    with factory() as client:
        client.initialize()
        tools_list = client.list_tools()

    return [mcp_tool_to_callable(server, spec, client_factory) for spec in tools_list]


# ---------------------------------------------------------------------------
# Escape hatch: literal shell command exposure (not used by default)
# ---------------------------------------------------------------------------

def run_shell_snippet(snippet: str, timeout: int = 30) -> str:
    """Utility for command bodies that embed shell. Caller opts in."""
    try:
        out = subprocess.run(
            shlex.split(snippet),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "<timeout>"
    return out.stdout + ("\n" + out.stderr if out.stderr else "")
