"""Tests for the minimal stdio MCP client.

Spawns the in-repo mock server script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from localsmartz.plugins.mcp_client import MCPClient

MOCK = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"


def test_initialize_lists_and_calls():
    assert MOCK.is_file(), f"mock server missing at {MOCK}"
    with MCPClient(sys.executable, [str(MOCK)]) as client:
        info = client.initialize(timeout=10)
        assert isinstance(info, dict)
        assert info.get("serverInfo", {}).get("name") == "mock-mcp"

        tools = client.list_tools(timeout=10)
        assert isinstance(tools, list) and len(tools) == 1
        assert tools[0]["name"] == "echo"

        result = client.call_tool("echo", {"msg": "hi"}, timeout=10)
        assert isinstance(result, dict)
        # mock returns {"content": [{"type": "text", "text": "..."}], "isError": False}
        content = result.get("content", [])
        assert content and content[0]["text"] == '{"msg": "hi"}'


def test_call_unknown_tool_raises():
    import pytest

    from localsmartz.plugins.mcp_client import MCPClientError

    with MCPClient(sys.executable, [str(MOCK)]) as client:
        client.initialize(timeout=10)
        with pytest.raises(MCPClientError):
            client.call_tool("no-such-tool", {}, timeout=10)
