"""Tiny stdio JSON-RPC 2.0 MCP mock server.

Implements just enough for MCPClient tests using MCP spec 2024-11-05
Content-Length framing:
- initialize      -> returns serverInfo
- tools/list      -> returns one fake tool
- tools/call      -> echoes args
- notifications/initialized -> silent
"""

import json
import sys


def write(msg: dict) -> None:
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


def read_message() -> dict | None:
    """Read one Content-Length framed JSON message from stdin.

    Returns None on EOF or malformed frame.
    """
    stdin = sys.stdin.buffer
    headers: dict[str, str] = {}
    while True:
        line = stdin.readline()
        if not line:
            return None
        if line.endswith(b"\r\n"):
            line = line[:-2]
        elif line.endswith(b"\n"):
            line = line[:-1]
        if line == b"":
            break
        try:
            decoded = line.decode("utf-8", errors="replace")
        except Exception:
            continue
        if ":" in decoded:
            k, _, v = decoded.partition(":")
            headers[k.strip().lower()] = v.strip()
    raw_len = headers.get("content-length")
    if raw_len is None:
        return None
    try:
        n = int(raw_len)
    except ValueError:
        return None
    if n < 0:
        return None
    body = b""
    remaining = n
    while remaining > 0:
        chunk = stdin.read(remaining)
        if not chunk:
            return None
        body += chunk
        remaining -= len(chunk)
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def main() -> int:
    while True:
        req = read_message()
        if req is None:
            break
        if not isinstance(req, dict):
            continue
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {}) or {}

        # notification — no id, no reply
        if req_id is None:
            if method == "notifications/initialized":
                continue
            continue

        if method == "initialize":
            write(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mock-mcp", "version": "0.0.1"},
                    },
                }
            )
        elif method == "tools/list":
            write(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo the arguments back",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"msg": {"type": "string"}},
                                },
                            }
                        ]
                    },
                }
            )
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "echo":
                write(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(args)}],
                            "isError": False,
                        },
                    }
                )
            else:
                write(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"unknown tool {name}"},
                    }
                )
        else:
            write(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"unknown method {method}"},
                }
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
