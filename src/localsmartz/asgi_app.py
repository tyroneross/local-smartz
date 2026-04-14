"""ASGI bridge for the Local Smartz backend.

Ships an alternative server runtime based on Starlette + uvicorn. The default
stdlib ``ThreadingHTTPServer`` in ``serve.py`` stays intact so the existing
test suite (355+ tests bound to ``LocalSmartzHandler``) keeps passing during
the transition.

What the ASGI variant unlocks:

- **Real stream cancellation**. When the Swift client hits Stop, the
  ``/api/research`` endpoint notices via ``await request.is_disconnected()``
  inside the SSE generator and breaks the ``agent.stream()`` loop on the
  next chunk. The stdlib handler only noticed disconnects on the next
  ``wfile.write``, which meant the GPU/CPU kept generating for seconds or
  minutes after the user clicked Stop (AP-9 in the deepagents anti-patterns).
- **Async all the way down**. The heartbeat pulse piggy-backs on the same
  event loop rather than spinning a dedicated thread per request.

The delegating endpoints (``/api/status``, ``/api/models``, etc.) call the
existing sync handler methods inside ``asyncio.to_thread`` — no duplicated
business logic. ``/api/research`` is implemented natively because its
semantics depend on the async event loop.

Launch with ``localsmartz --serve --asgi`` or programmatically via
``run_asgi(port=11435)``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import traceback
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from localsmartz import serve as _serve


# ────────────────────────────────────────────────────────────────────────────
# Shim: call an existing LocalSmartzHandler method from an ASGI handler.
# ────────────────────────────────────────────────────────────────────────────


class _HandlerShim:
    """Minimal ``LocalSmartzHandler``-compatible stand-in.

    The stdlib handler's ``_handle_*`` methods call helpers like
    ``self._json_response(...)`` and ``self._read_json_body()``. This shim
    accepts the same calls from a single ASGI request and captures the
    response so we can wrap it in a Starlette ``Response``.

    We intentionally don't subclass ``BaseHTTPRequestHandler`` — instantiating
    it requires a socket. Instead we provide the methods each ``_handle_*``
    actually uses.
    """

    # Class-level state the handler reads (kept in sync with the stdlib side).
    _default_profile = _serve.LocalSmartzHandler._default_profile
    _model_override = _serve.LocalSmartzHandler._model_override

    def __init__(self, method: str, path: str, body: bytes, query: str = ""):
        self.command = method
        # path + query so urlparse on self.path works the same as the stdlib.
        self.path = f"{path}?{query}" if query else path
        self._body = body
        self._query = query
        self._response_status: int = 200
        self._response_body: bytes = b""
        self._response_content_type: str = "application/json"
        self._headers_sent: dict[str, str] = {}

    # ── Incoming body ──────────────────────────────────────────────────

    def _read_json_body(self) -> dict:
        if not self._body:
            return {}
        try:
            obj = json.loads(self._body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError("Body must be a JSON object")
        return obj

    # ── Outgoing response ──────────────────────────────────────────────

    def _json_response(self, data: dict, status: int = 200):
        self._response_status = status
        self._response_content_type = "application/json"
        self._response_body = json.dumps(data).encode("utf-8")

    def _send_plain(self, body: str | bytes, status: int = 200, content_type: str = "text/plain; charset=utf-8"):
        self._response_status = status
        self._response_content_type = content_type
        self._response_body = body.encode("utf-8") if isinstance(body, str) else body

    # Compatibility helpers some handlers use directly ──────────────────

    def send_response(self, status: int):
        self._response_status = status

    def send_header(self, name: str, value: str):
        self._headers_sent[name] = value
        if name.lower() == "content-type":
            self._response_content_type = value

    def end_headers(self):
        pass

    @property
    def wfile(self):  # pragma: no cover — only used by _serve_ui
        class _Fake:
            def write(_self, data):  # noqa: N805
                self._response_body += data

            def flush(_self):
                pass

        return _Fake()

    def to_starlette_response(self) -> Response:
        return Response(
            content=self._response_body,
            status_code=self._response_status,
            media_type=self._response_content_type,
            headers={
                k: v
                for k, v in self._headers_sent.items()
                if k.lower() not in {"content-type", "content-length"}
            },
        )


async def _delegate_sync(
    handler_name: str,
    request: Request,
    *args,
    method: str | None = None,
) -> Response:
    """Invoke a ``LocalSmartzHandler._handle_*`` method via the shim.

    The handler methods are synchronous and may do real I/O (subprocess
    pulls, file reads). We offload to ``asyncio.to_thread`` so the event
    loop stays responsive.
    """
    body = await request.body()
    shim = _HandlerShim(
        method or request.method,
        request.url.path,
        body,
        request.url.query,
    )
    fn = getattr(_serve.LocalSmartzHandler, handler_name)

    def _run():
        # Some handlers take extra positional args (e.g. ``parsed`` for
        # query parsing, ``agent_name`` for /api/agents/<name>/model).
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())[1:]  # drop self
        call_args: list = []
        for p, supplied in zip(params, args, strict=False):
            call_args.append(supplied)
        if len(args) > len(params):
            call_args.extend(args[len(params):])
        return fn(shim, *call_args)

    await asyncio.to_thread(_run)
    return shim.to_starlette_response()


# ────────────────────────────────────────────────────────────────────────────
# Native async research endpoint with real cancellation.
# ────────────────────────────────────────────────────────────────────────────


def _sse_pack(event: dict) -> bytes:
    """Format an event dict as an SSE ``data:`` line."""
    return f"data: {json.dumps(event)}\n\n".encode("utf-8")


async def _research_stream(
    request: Request,
    prompt: str,
    thread_id: str | None,
    focus_agent: str | None,
) -> AsyncIterator[bytes]:
    """Async SSE generator for /api/research.

    Mirrors ``LocalSmartzHandler._stream_research`` but:
      1. Each yielded chunk is an SSE-formatted bytes payload.
      2. Before every yield we check ``await request.is_disconnected()``.
         On disconnect the generator returns immediately, which propagates
         ``GeneratorExit`` through the underlying ``agent.stream()`` loop
         and halts the Ollama call on the next iteration. This is the
         whole point of the ASGI migration — the stdlib server couldn't
         do this reliably.
    """
    from localsmartz.agent import (
        create_agent,
        extract_final_response,
        fast_path_stream,
    )
    from localsmartz.observability import get_tracer
    from localsmartz.profiles import get_profile, is_fast_path
    from localsmartz.ollama import (
        check_server,
        list_models,
        model_available,
        resolve_available_model,
        warmup_model,
    )
    from localsmartz.threads import create_thread, append_entry
    from localsmartz.validation import LoopDetector
    from localsmartz.drift import create_drift_detector

    async def _send(event: dict) -> bool:
        """Yield an SSE event. Returns False if the peer disconnected."""
        if await request.is_disconnected():
            return False
        return True

    # Preflight: Ollama must be running.
    if not check_server():
        yield _sse_pack({
            "type": "error",
            "message": "Ollama not running \u2192 Start it with: ollama serve",
        })
        return

    cwd = Path.cwd()
    model_override = (
        _serve.LocalSmartzHandler._model_override
        or _serve._saved_model_override(cwd)
    )

    profile = get_profile(
        _serve.LocalSmartzHandler._default_profile,
        model_override=model_override,
    )
    model = profile["planning_model"]

    if not model_available(model):
        chosen, msg = resolve_available_model(model)
        if chosen is None:
            available = ", ".join(list_models()) or "none"
            yield _sse_pack({
                "type": "error",
                "message": (
                    f"Model '{model}' not found \u2192 Pull it with: ollama pull {model}\n"
                    f"Available models: {available}"
                ),
            })
            return
        model_override = chosen
        model = chosen
        if msg:
            yield _sse_pack({"type": "text", "content": f"[note] {msg}\n\n"})

    yield _sse_pack({"type": "status", "stage": "loading_model", "model": model})
    warm_ok, warm_ms, warm_err = await asyncio.to_thread(
        warmup_model, model, "-1"
    )
    if not warm_ok:
        yield _sse_pack({"type": "text", "content": f"[warmup] {warm_err}\n\n"})
    yield _sse_pack({
        "type": "status",
        "stage": "ready",
        "model": model,
        "warmup_ms": warm_ms,
    })

    # Prepare storage
    storage = cwd / ".localsmartz"
    for subdir in ["threads", "artifacts", "memory", "scripts", "reports"]:
        (storage / subdir).mkdir(parents=True, exist_ok=True)

    if thread_id:
        create_thread(thread_id, str(cwd), title=prompt[:60])

    start_time = time.time()

    # Fast path — same heuristic as stdlib.
    allow_fast_path = focus_agent is None or focus_agent == "planner"
    if allow_fast_path and is_fast_path(prompt):
        first_text = ""
        tracer = get_tracer("local-smartz.research")
        fast_span_cm = tracer.start_as_current_span("research.fast_path")
        fast_span = fast_span_cm.__enter__()
        fast_span.set_attribute("routing.path", "fast_path")
        fast_span.set_attribute("routing.reason", "trivial_prompt")
        fast_span.set_attribute("agent.focus", focus_agent or "none")
        fast_span.set_attribute("model.name", model)
        try:
            # fast_path_stream is a sync generator; iterate in a thread.
            def _pull_one(it):
                try:
                    return next(it)
                except StopIteration:
                    return None

            it = fast_path_stream(prompt, profile, model_override=model_override)
            while True:
                if await request.is_disconnected():
                    break
                event = await asyncio.to_thread(_pull_one, it)
                if event is None:
                    break
                if event.get("type") == "done":
                    yield _sse_pack({
                        "type": "done",
                        "duration_ms": int((time.time() - start_time) * 1000),
                        "thread_id": thread_id or "",
                    })
                    continue
                if event.get("type") == "text":
                    content = event.get("content", "")
                    if isinstance(content, str):
                        first_text += content
                yield _sse_pack(event)
        finally:
            fast_span_cm.__exit__(None, None, None)
        if thread_id:
            try:
                await asyncio.to_thread(
                    append_entry,
                    thread_id=thread_id,
                    cwd=str(cwd),
                    query=prompt,
                    summary=first_text[:500],
                    artifacts=[],
                    turns=1,
                )
            except Exception:
                pass
        return

    if focus_agent:
        yield _sse_pack({
            "type": "text",
            "content": f"[focus] running as `{focus_agent}` agent only\n\n",
        })

    # Full agent path.
    agent, profile, checkpointer, mcp_clients = await asyncio.to_thread(
        create_agent,
        _serve.LocalSmartzHandler._default_profile,
        thread_id,
        cwd,
        model_override,
        focus_agent=focus_agent,
    )

    config = {"configurable": {"thread_id": thread_id or "default"}}
    input_msg = {"messages": [{"role": "user", "content": prompt}]}

    is_lite = profile["name"] == "lite"
    max_turns = profile.get("max_turns", 20)
    loop_detector = LoopDetector(max_repeats=3)
    drift_detector = create_drift_detector(profile)
    turn_count = 0
    loop_broken = False
    tools_used: set[str] = set()

    _valid_tool_names: set[str] = set()
    try:
        bound_tools = getattr(agent, "tools", None) or getattr(agent, "_tools", None)
        if bound_tools:
            for _tool in bound_tools:
                _name = getattr(_tool, "name", None)
                if isinstance(_name, str) and _name:
                    _valid_tool_names.add(_name)
    except Exception:
        _valid_tool_names = set()

    tracer = get_tracer("local-smartz.research")
    full_span_cm = tracer.start_as_current_span("research.full_agent")
    full_span = full_span_cm.__enter__()
    full_span.set_attribute("routing.path", "full_agent")
    full_span.set_attribute("agent.focus", focus_agent or "none")
    full_span.set_attribute("model.name", model)
    full_span.set_attribute("profile.name", profile.get("name", "unknown"))

    # Heartbeat: a background task that yields into a queue so the main
    # generator stays the single async yielder. We use an asyncio.Queue
    # to multiplex agent-stream events and heartbeat ticks.
    queue: asyncio.Queue = asyncio.Queue()

    async def _heartbeat() -> None:
        last_touch = time.time()

        while True:
            await asyncio.sleep(1.0)
            if await request.is_disconnected():
                return
            if time.time() - last_touch >= 15.0:
                await queue.put({"type": "heartbeat", "elapsed_s": int(time.time() - last_touch)})
                last_touch = time.time()

    def _pull_next(it):
        try:
            return next(it)
        except StopIteration:
            return None

    try:
        # Run the sync agent.stream in a background thread and feed the
        # async queue so we can interleave is_disconnected() checks.
        agent_stream = agent.stream(
            input_msg, config=config, stream_mode=["updates", "messages"]
        )
        heartbeat_task = asyncio.create_task(_heartbeat())

        try:
            while True:
                if await request.is_disconnected():
                    break

                # Try the queue first (heartbeats).
                while not queue.empty():
                    yield _sse_pack(queue.get_nowait())

                item = await asyncio.to_thread(_pull_next, agent_stream)
                if item is None:
                    break

                mode, payload = item

                if mode == "messages":
                    try:
                        msg_chunk, _meta = payload
                    except (TypeError, ValueError):
                        continue
                    chunk_type = type(msg_chunk).__name__
                    if chunk_type != "AIMessageChunk" and getattr(msg_chunk, "type", None) != "ai":
                        continue
                    content = getattr(msg_chunk, "content", None)
                    if isinstance(content, str) and content:
                        yield _sse_pack({"type": "text", "content": content})
                    elif isinstance(content, list):
                        for seg in content:
                            text = seg.get("text") if isinstance(seg, dict) else None
                            if isinstance(text, str) and text:
                                yield _sse_pack({"type": "text", "content": text})
                    continue

                # mode == "updates"
                chunk = payload
                if not isinstance(chunk, dict):
                    continue
                for node_name, state_update in chunk.items():
                    if state_update is None:
                        continue
                    messages = state_update.get("messages", [])
                    if hasattr(messages, "value"):
                        messages = messages.value
                    if not isinstance(messages, list):
                        continue
                    for msg in messages:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                name = tc.get("name", "unknown")
                                if not _serve._is_valid_tool_name(
                                    name, valid_names=_valid_tool_names or None
                                ):
                                    yield _sse_pack({
                                        "type": "tool_error",
                                        "name": name,
                                        "message": (
                                            f"Invalid tool name '{name}' — "
                                            "tool names do not include dots or slashes."
                                        ),
                                    })
                                    continue
                                tools_used.add(name)
                                turn_count += 1
                                yield _sse_pack({"type": "tool", "name": name})
                                if is_lite and loop_detector.record(name, tc.get("args")):
                                    yield _sse_pack({
                                        "type": "tool_error",
                                        "name": name,
                                        "message": f"Loop detected: {name} called {loop_detector.max_repeats}x with same args. Stopping.",
                                    })
                                    loop_broken = True
                                for de in drift_detector.record_tool_call(
                                    name, tc.get("args"), turn_count
                                ):
                                    yield _sse_pack({
                                        "type": "tool_error",
                                        "name": "drift",
                                        "message": f"{de.signal.value} [{de.severity.value}] {de.message}",
                                    })
                        if hasattr(msg, "type") and msg.type == "tool":
                            content = msg.content if isinstance(msg.content, str) else str(msg.content)
                            is_error = content.startswith("Error")
                            if is_error:
                                yield _sse_pack({
                                    "type": "tool_error",
                                    "name": getattr(msg, "name", "unknown"),
                                    "message": content[:200],
                                })
                            for de in drift_detector.record_tool_result(
                                getattr(msg, "name", "unknown"), content, is_error, turn_count
                            ):
                                yield _sse_pack({
                                    "type": "tool_error",
                                    "name": "drift",
                                    "message": f"{de.signal.value} [{de.severity.value}] {de.message}",
                                })
                if turn_count >= max_turns or loop_broken:
                    if turn_count >= max_turns:
                        yield _sse_pack({
                            "type": "tool_error",
                            "name": "system",
                            "message": f"Turn limit ({max_turns}) reached. Returning partial results.",
                        })
                    break
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        full_span.set_attribute("turn_count", turn_count)
        full_span.set_attribute("tools_used", ",".join(sorted(tools_used)))
        full_span_cm.__exit__(None, None, None)

    # Final result + done event.
    if not await request.is_disconnected():
        try:
            full_result = await asyncio.to_thread(agent.invoke, None, config)
            response = (
                extract_final_response(full_result)
                if full_result else "No response generated."
            )
            if thread_id and full_result:
                try:
                    await asyncio.to_thread(
                        append_entry,
                        thread_id=thread_id,
                        cwd=str(cwd),
                        query=prompt,
                        summary=response[:500],
                        artifacts=[],
                        turns=len(full_result.get("messages", [])),
                    )
                except Exception:
                    pass
        except Exception as exc:
            yield _sse_pack({"type": "error", "message": str(exc)})

        yield _sse_pack({
            "type": "done",
            "duration_ms": int((time.time() - start_time) * 1000),
            "thread_id": thread_id or "",
        })

    if mcp_clients:
        from localsmartz.plugins.agent_integration import close_mcp_clients
        await asyncio.to_thread(close_mcp_clients, mcp_clients)


async def research_endpoint(request: Request) -> Response:
    """GET or POST /api/research — SSE stream with cancellation support."""
    if request.method == "GET":
        params = request.query_params
        prompt = params.get("prompt", "")
        thread_id = params.get("thread_id") or None
        focus_agent = params.get("agent") or None
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        prompt = body.get("prompt", "") if isinstance(body, dict) else ""
        thread_id = (body.get("thread_id") if isinstance(body, dict) else None) or None
        focus_agent = (body.get("agent") if isinstance(body, dict) else None) or None

    if not isinstance(prompt, str) or not prompt.strip():
        return JSONResponse({"error": "No prompt provided"}, status_code=400)
    if thread_id is not None and not isinstance(thread_id, str):
        return JSONResponse({"error": "thread_id must be a string"}, status_code=400)

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
    }

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in _research_stream(
                request, prompt.strip(), thread_id, focus_agent
            ):
                yield chunk
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            return
        except Exception as exc:  # noqa: BLE001
            detail = traceback.format_exc()
            try:
                yield _sse_pack({"type": "error", "message": f"{exc}\n\n{detail}"})
            except Exception:
                pass

    return StreamingResponse(gen(), headers=headers, media_type="text/event-stream")


# ────────────────────────────────────────────────────────────────────────────
# Delegating endpoints — all non-SSE, all cheap.
# ────────────────────────────────────────────────────────────────────────────


def _make_delegate(handler_name: str):
    async def _endpoint(request: Request) -> Response:
        return await _delegate_sync(handler_name, request)

    _endpoint.__name__ = f"asgi_{handler_name}"
    return _endpoint


async def agent_model_set_endpoint(request: Request) -> Response:
    agent_name = request.path_params.get("name", "")
    body = await request.body()
    shim = _HandlerShim(
        "POST", request.url.path, body, request.url.query
    )

    def _run():
        _serve.LocalSmartzHandler._handle_agent_model_set(shim, agent_name)

    await asyncio.to_thread(_run)
    return shim.to_starlette_response()


async def options_endpoint(request: Request) -> Response:
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )


def build_app() -> Starlette:
    """Construct the Starlette app with all routes wired up."""
    routes = [
        # SSE / streaming
        Route("/api/research", research_endpoint, methods=["GET", "POST"]),
        # Simple delegating endpoints
        Route("/api/health", _make_delegate("_handle_health"), methods=["GET"]),
        Route("/api/status", _make_delegate("_handle_status"), methods=["GET"]),
        Route("/api/threads", _make_delegate("_handle_threads"), methods=["GET"]),
        Route("/api/models", _make_delegate("_handle_models"), methods=["GET"]),
        Route("/api/models/catalog", _make_delegate("_handle_models_catalog"), methods=["GET"]),
        Route("/api/models/warmup", _make_delegate("_handle_warmup_status"), methods=["GET"]),
        Route("/api/agents", _make_delegate("_handle_agents"), methods=["GET"]),
        Route("/api/agents/models", _make_delegate("_handle_agents_models"), methods=["GET"]),
        Route("/api/skills", _make_delegate("_handle_list_skills"), methods=["GET"]),
        Route("/api/ollama/info", _make_delegate("_handle_ollama_info"), methods=["GET"]),
        Route("/api/observability/info", _make_delegate("_handle_observability_info"), methods=["GET"]),
        Route("/api/folders", _make_delegate("_handle_folders"), methods=["GET"]),
        Route("/api/secrets", _make_delegate("_handle_secrets_list"), methods=["GET"]),
        Route("/api/logs", _make_delegate("_handle_logs_list"), methods=["GET"]),
        # POSTs
        Route("/api/setup", _make_delegate("_handle_setup"), methods=["POST"]),
        Route("/api/models/select", _make_delegate("_handle_model_select"), methods=["POST"]),
        Route("/api/models/pull", _make_delegate("_handle_model_pull"), methods=["POST"]),
        Route("/api/models/warmup", _make_delegate("_handle_warmup_start"), methods=["POST"]),
        Route("/api/skills/refactor", _make_delegate("_handle_skill_refactor"), methods=["POST"]),
        Route("/api/skills/new", _make_delegate("_handle_skill_new"), methods=["POST"]),
        Route("/api/plugins/save", _make_delegate("_handle_plugin_save"), methods=["POST"]),
        Route("/api/folders", _make_delegate("_handle_folder_add"), methods=["POST"]),
        Route("/api/secrets", _make_delegate("_handle_secrets_set"), methods=["POST"]),
        Route("/api/issues/report", _make_delegate("_handle_issues_report"), methods=["POST"]),
        Route("/api/agents/{name}/model", agent_model_set_endpoint, methods=["POST"]),
        # DELETEs
        Route("/api/folders", _make_delegate("_handle_folder_delete"), methods=["DELETE"]),
        Route("/api/models", _make_delegate("_handle_model_delete"), methods=["DELETE"]),
        Route("/api/secrets", _make_delegate("_handle_secrets_delete"), methods=["DELETE"]),
        Route("/api/logs", _make_delegate("_handle_logs_clear"), methods=["DELETE"]),
        # CORS
        Route("/{path:path}", options_endpoint, methods=["OPTIONS"]),
    ]

    app = Starlette(routes=routes)
    return app


def run_asgi(port: int = 11435, profile_name: str | None = None) -> None:
    """Start the Starlette/uvicorn server.

    Mirrors ``serve.start_server`` for setup work (crash diagnostics,
    log buffer, boot warmup) but runs on the async event loop.
    """
    import uvicorn
    from localsmartz import __version__ as _version
    from localsmartz import log_buffer as _log_buffer
    from localsmartz.ollama import check_server
    from localsmartz.profiles import get_profile

    _serve._install_crash_diagnostics()
    _serve.LocalSmartzHandler._default_profile = profile_name

    _log_buffer.info("startup", f"local-smartz {_version} (asgi) starting on port {port}")

    try:
        _cwd = Path.cwd()
        _saved = _serve._saved_model_override(_cwd)
        _profile = get_profile(profile_name, model_override=_saved or None)
        _boot_model = _profile.get("planning_model") if isinstance(_profile, dict) else None
        if _boot_model and check_server():
            print(f"  Warming {_boot_model} (background, keep_alive=-1)...")
            _serve._warmup_in_background(_boot_model, keep_alive="-1")
    except Exception as exc:  # noqa: BLE001
        _log_buffer.info("startup", f"asgi boot warmup skipped: {exc}")

    app = build_app()
    print(f"\n  Local Smartz (ASGI) running at http://localhost:{port}")
    print(f"  Press Ctrl+C to stop.\n")
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        # Disable uvicorn's access log — we have our own log buffer.
        access_log=False,
    )
