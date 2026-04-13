"""Minimal stdio JSON-RPC 2.0 MCP client.

Pure stdlib. Implements ``initialize``, ``tools/list``, ``tools/call``.

Uses the MCP spec 2024-11-05 stdio framing: each message is an HTTP-style
``Content-Length: N\\r\\n\\r\\n`` header followed by exactly ``N`` bytes of
UTF-8 JSON body (no trailing newline). See:
https://spec.modelcontextprotocol.io/specification/2024-11-05/basic/transports/#stdio
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from queue import Empty, Queue
from typing import Any


class MCPClientError(RuntimeError):
    pass


class MCPTimeoutError(MCPClientError):
    pass


class MCPClient:
    """Spawns and talks to a stdio MCP server.

    Usage:

        with MCPClient("node", ["server.js"]) as c:
            c.initialize()
            tools = c.list_tools()
            result = c.call_tool("echo", {"msg": "hi"})
    """

    PROTOCOL_VERSION = "2024-11-05"
    CLIENT_INFO = {"name": "localsmartz", "version": "0.1.0"}

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        default_timeout: float = 30.0,
    ):
        self.command = command
        self.args = args or []
        self.env = {**os.environ, **(env or {})}
        self.cwd = cwd
        self.default_timeout = default_timeout
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._responses: dict[int, dict[str, Any]] = {}
        self._response_queue: Queue = Queue()
        self._reader_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            cwd=self.cwd,
            bufsize=0,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        while True:
            # Parse the header block: lines terminated with CRLF, ended by empty CRLF line.
            headers: dict[str, str] = {}
            while True:
                line = stdout.readline()
                if not line:
                    # EOF — server exited.
                    return
                # Strip CRLF (or LF tolerantly).
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
                # Malformed frame — bail. Don't busy-loop on garbage.
                return
            try:
                n = int(raw_len)
            except ValueError:
                return
            if n < 0:
                return
            body = b""
            remaining = n
            while remaining > 0:
                chunk = stdout.read(remaining)
                if not chunk:
                    return
                body += chunk
                remaining -= len(chunk)
            try:
                msg = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            # Ignore server-initiated notifications (no id).
            if isinstance(msg, dict) and "id" not in msg:
                continue
            self._response_queue.put(msg)

    def _stderr_loop(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for raw in self._proc.stderr:
            try:
                self._stderr_lines.append(raw.decode("utf-8", errors="replace").rstrip())
            except Exception:
                pass

    def _send(self, method: str, params: dict[str, Any] | None = None, *, notification: bool = False) -> int | None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPClientError("MCP process not started")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        req_id: int | None = None
        if not notification:
            self._next_id += 1
            req_id = self._next_id
            payload["id"] = req_id
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self._proc.stdin.write(header + body)
            self._proc.stdin.flush()
        except BrokenPipeError as e:
            raise MCPClientError(f"Broken pipe sending {method}: {e}") from e
        return req_id

    def _await_response(self, req_id: int, timeout: float) -> dict[str, Any]:
        # Pull from queue until we see the matching id (notifications, events ignored for now).
        remaining = timeout
        while remaining > 0:
            try:
                msg = self._response_queue.get(timeout=remaining)
            except Empty:
                break
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    raise MCPClientError(f"MCP error {err.get('code')}: {err.get('message')}")
                return msg.get("result", {}) if isinstance(msg, dict) else {}
            # Not ours — could be a server-initiated request or notification.
            # Stash/ignore; do not block the expected reply.
            # (We don't support server->client requests in this minimal client.)
        raise MCPTimeoutError(f"Timed out waiting for response id={req_id} method")

    def _request(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        timeout = self.default_timeout if timeout is None else timeout
        req_id = self._send(method, params)
        assert req_id is not None
        return self._await_response(req_id, timeout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initialize(self, timeout: float | None = None) -> dict[str, Any]:
        params = {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": self.CLIENT_INFO,
        }
        result = self._request("initialize", params, timeout=timeout)
        # per spec, client sends notifications/initialized
        try:
            self._send("notifications/initialized", {}, notification=True)
        except MCPClientError:
            pass
        return result if isinstance(result, dict) else {}

    def list_tools(self, timeout: float | None = None) -> list[dict[str, Any]]:
        result = self._request("tools/list", {}, timeout=timeout)
        if isinstance(result, dict):
            tools = result.get("tools", [])
            if isinstance(tools, list):
                return tools
        return []

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        params = {"name": name, "arguments": arguments or {}}
        result = self._request("tools/call", params, timeout=timeout)
        return result if isinstance(result, dict) else {"result": result}

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def stderr_output(self) -> str:
        return "\n".join(self._stderr_lines)
