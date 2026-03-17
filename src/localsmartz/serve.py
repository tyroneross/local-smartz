"""Lightweight HTTP server with SSE streaming for the macOS app.

Endpoints:
    GET  /api/health              → Backend readiness
    GET  /api/status              → Profile, model, Ollama state
    GET  /api/research?prompt=... → SSE stream of research events
    GET  /api/threads             → Thread history
    POST /api/setup               → SSE stream of model download progress

Uses stdlib http.server — no new dependencies.
Same pattern as stratagem/ui.py.
"""

import json
import sys
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _json_bytes(data: dict, status: int = 200) -> tuple[bytes, int]:
    """Serialize dict to JSON bytes."""
    return json.dumps(data).encode("utf-8"), status


class LocalSmartzHandler(BaseHTTPRequestHandler):
    """HTTP request handler with SSE support."""

    # Set by start_server() — profile override from CLI (None = auto-detect)
    _default_profile: str | None = None

    # Suppress default logging to stderr
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/health":
            self._handle_health()
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/research":
            self._handle_research(parsed)
        elif path == "/api/threads":
            self._handle_threads()
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/setup":
            self._handle_setup()
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── Helpers ──

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors_headers()
        self.end_headers()

    def _send_event(self, data: dict):
        try:
            line = f"data: {json.dumps(data)}\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    # ── Endpoints ──

    def _handle_health(self):
        from localsmartz.profiles import get_profile

        profile = get_profile(self._default_profile)
        self._json_response({"ok": True, "profile": profile["name"]})

    def _handle_status(self):
        from localsmartz.profiles import get_profile
        from localsmartz.ollama import check_server, get_version, list_models

        profile = get_profile(self._default_profile)
        ollama_ok = check_server()
        version = get_version() if ollama_ok else None
        models = list_models() if ollama_ok else []

        self._json_response({
            "profile": profile["name"],
            "planning_model": profile["planning_model"],
            "execution_model": profile["execution_model"],
            "ollama": {
                "running": ollama_ok,
                "version": version,
                "models": models,
            },
        })

    def _handle_threads(self):
        from localsmartz.threads import list_threads

        cwd = str(Path.cwd())
        threads = list_threads(cwd)
        self._json_response(threads)

    def _handle_research(self, parsed):
        """Stream research results as Server-Sent Events."""
        params = parse_qs(parsed.query)
        prompt = params.get("prompt", [None])[0]

        if not prompt:
            self._json_response({"error": "No prompt provided"}, 400)
            return

        thread_id = params.get("thread_id", [None])[0]
        profile_name = params.get("profile", [None])[0] or self._default_profile

        self._start_sse()

        try:
            self._stream_research(prompt, profile_name, thread_id)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            detail = traceback.format_exc()
            try:
                self._send_event({"type": "error", "message": f"{e}\n\n{detail}"})
            except Exception:
                pass

    def _stream_research(self, prompt: str, profile_name: str | None, thread_id: str | None):
        """Run research agent and emit SSE events."""
        from localsmartz.agent import create_agent, extract_final_response
        from localsmartz.profiles import get_profile
        from localsmartz.ollama import check_server, model_available, list_models
        from localsmartz.threads import create_thread, append_entry

        # Preflight: Ollama must be running
        if not check_server():
            self._send_event({
                "type": "error",
                "message": "Ollama not running \u2192 Start it with: ollama serve",
            })
            return

        # Preflight: required model must be available
        from localsmartz.ollama import model_available
        profile = get_profile(profile_name)
        model = profile["planning_model"]
        if not model_available(model):
            available = ", ".join(list_models()) or "none"
            self._send_event({
                "type": "error",
                "message": f"Model '{model}' not found \u2192 Pull it with: ollama pull {model}\nAvailable models: {available}",
            })
            return

        cwd = Path.cwd()

        # Ensure storage
        storage = cwd / ".localsmartz"
        for subdir in ["threads", "artifacts", "memory", "scripts", "reports"]:
            (storage / subdir).mkdir(parents=True, exist_ok=True)

        # Create thread if specified
        if thread_id:
            create_thread(thread_id, str(cwd), title=prompt[:60])

        agent, profile, checkpointer = create_agent(
            profile_name=profile_name,
            thread_id=thread_id,
            cwd=cwd,
        )

        config = {"configurable": {"thread_id": thread_id or "default"}}
        input_msg = {"messages": [{"role": "user", "content": prompt}]}

        start_time = time.time()
        tools_used = set()

        # Lite profile: loop detection and turn limits
        from localsmartz.validation import LoopDetector
        from localsmartz.drift import create_drift_detector
        is_lite = profile["name"] == "lite"
        max_turns = profile.get("max_turns", 20)
        loop_detector = LoopDetector(max_repeats=3)
        drift_detector = create_drift_detector(profile)
        turn_count = 0
        loop_broken = False

        # Stream agent execution
        for chunk in agent.stream(input_msg, config=config, stream_mode="updates"):
            for node_name, state_update in chunk.items():
                if state_update is None:
                    continue
                messages = state_update.get("messages", [])
                # LangGraph wraps state in Overwrite objects
                if hasattr(messages, "value"):
                    messages = messages.value
                if not isinstance(messages, list):
                    continue
                for msg in messages:
                    # Tool calls from the AI
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            name = tc.get("name", "unknown")
                            tools_used.add(name)
                            turn_count += 1
                            self._send_event({"type": "tool", "name": name})

                            # Loop detection
                            if is_lite and loop_detector.record(name, tc.get("args")):
                                self._send_event({
                                    "type": "tool_error",
                                    "name": name,
                                    "message": f"Loop detected: {name} called {loop_detector.max_repeats}x with same args. Stopping.",
                                })
                                loop_broken = True

                            # Drift detection
                            for de in drift_detector.record_tool_call(name, tc.get("args"), turn_count):
                                self._send_event({
                                    "type": "tool_error",
                                    "name": "drift",
                                    "message": f"{de.signal.value} [{de.severity.value}] {de.message}",
                                })

                    # Tool error results
                    if hasattr(msg, "type") and msg.type == "tool":
                        content = msg.content if isinstance(msg.content, str) else str(msg.content)
                        is_error = content.startswith("Error")
                        if is_error:
                            self._send_event({
                                "type": "tool_error",
                                "name": getattr(msg, "name", "unknown"),
                                "message": content[:200],
                            })
                        for de in drift_detector.record_tool_result(getattr(msg, "name", "unknown"), content, is_error, turn_count):
                            self._send_event({
                                "type": "tool_error",
                                "name": "drift",
                                "message": f"{de.signal.value} [{de.severity.value}] {de.message}",
                            })

                    # AI text output
                    if hasattr(msg, "type") and msg.type == "ai":
                        if hasattr(msg, "content") and isinstance(msg.content, str) and msg.content.strip():
                            self._send_event({"type": "text", "content": msg.content})

            # Enforce turn limit and loop break
            if turn_count >= max_turns or loop_broken:
                if turn_count >= max_turns:
                    self._send_event({
                        "type": "tool_error",
                        "name": "system",
                        "message": f"Turn limit ({max_turns}) reached. Returning partial results.",
                    })
                break

        # Get final result
        full_result = agent.invoke(None, config=config)
        response = extract_final_response(full_result) if full_result else "No response generated."

        duration_ms = int((time.time() - start_time) * 1000)
        self._send_event({"type": "done", "duration_ms": duration_ms})

        # Log to thread
        if thread_id and full_result:
            try:
                append_entry(
                    thread_id=thread_id,
                    cwd=str(cwd),
                    query=prompt,
                    summary=response[:500],
                    artifacts=[],
                    turns=len(full_result.get("messages", [])),
                )
            except Exception:
                pass

    def _handle_setup(self):
        """Stream model setup progress as SSE events."""
        from localsmartz.profiles import get_profile
        from localsmartz.ollama import (
            check_server, is_installed, model_available, pull_model,
        )

        self._start_sse()

        try:
            profile = get_profile(self._default_profile)

            # Check Ollama
            if not is_installed():
                self._send_event({
                    "type": "error",
                    "message": "Ollama is not installed. Download from https://ollama.com/download",
                })
                return

            if not check_server():
                self._send_event({
                    "type": "text",
                    "content": "Starting Ollama...",
                })
                import subprocess
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                # Wait for startup
                for _ in range(10):
                    time.sleep(1)
                    if check_server():
                        break

                if not check_server():
                    self._send_event({
                        "type": "error",
                        "message": "Could not start Ollama. Please start it manually: ollama serve",
                    })
                    return

            self._send_event({"type": "text", "content": "Ollama is running."})

            # Check and pull models
            models = [profile["planning_model"]]
            if profile["execution_model"] != profile["planning_model"]:
                models.append(profile["execution_model"])

            for model in models:
                if model_available(model):
                    self._send_event({"type": "text", "content": f"Model {model}: ready"})
                else:
                    self._send_event({"type": "text", "content": f"Downloading {model}..."})
                    ok = pull_model(model)
                    if ok:
                        self._send_event({"type": "text", "content": f"Model {model}: ready"})
                    else:
                        self._send_event({
                            "type": "tool_error",
                            "name": "pull_model",
                            "message": f"Failed to download {model}",
                        })

            self._send_event({"type": "done", "duration_ms": 0})

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._send_event({"type": "error", "message": str(e)})
            except Exception:
                pass


def start_server(port: int = 11435, profile_name: str | None = None):
    """Start the Local Smartz HTTP server.

    Args:
        port: Port to listen on
        profile_name: Profile override ("full" or "lite"). Auto-detect if None.
    """
    # Store profile_name on handler class so all requests use consistent profile
    LocalSmartzHandler._default_profile = profile_name
    server = HTTPServer(("127.0.0.1", port), LocalSmartzHandler)
    print(f"Local Smartz server running at http://localhost:{port}", file=sys.stderr)
    print(f"Press Ctrl+C to stop.", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.shutdown()
