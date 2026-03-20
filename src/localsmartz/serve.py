"""Lightweight HTTP server with SSE streaming for the macOS app.

Endpoints:
    GET  /api/health              → Backend readiness
    GET  /api/status              → Profile, model, Ollama state
    GET  /api/research?prompt=... → SSE stream of research events (legacy)
    POST /api/research            → SSE stream of research events
    GET  /api/threads             → Thread history
    POST /api/setup               → SSE stream of model download progress

Uses stdlib http.server — no new dependencies.
Same pattern as stratagem/ui.py.
"""

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _json_bytes(data: dict, status: int = 200) -> tuple[bytes, int]:
    """Serialize dict to JSON bytes."""
    return json.dumps(data).encode("utf-8"), status


def _iso_timestamp(value) -> str:
    """Convert a Unix timestamp to an ISO 8601 UTC string."""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        timestamp = time.time()
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _saved_model_override(cwd: Path) -> str | None:
    """Load a persisted planning model for non-interactive server mode."""
    try:
        from localsmartz.config import load_config

        config = load_config(cwd)
        if config:
            return config.get("planning_model")
    except Exception:
        pass
    return None


_UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local Smartz</title>
<style>
:root {
  --bg: #f5f5f5; --surface: #fff; --border: #e0e0e0;
  --fg: #1a1a1a; --fg-muted: #666; --accent: #0066cc;
  --error: #cc2200; --success: #228833;
  --tool-bg: #eef2ff; --tool-fg: #4455aa; --radius: 6px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1a1a1a; --surface: #242424; --border: #333;
    --fg: #e0e0e0; --fg-muted: #888; --accent: #4a9eff;
    --error: #ff4444; --success: #44cc44;
    --tool-bg: #2a2a3a; --tool-fg: #8899dd;
  }
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: var(--bg); color: var(--fg); height: 100vh; overflow: hidden;
}
#app { display: grid; grid-template-columns: 200px 1fr; height: 100vh; }
aside {
  background: var(--surface); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow-y: auto;
}
.sidebar-hd {
  padding: 16px 16px 8px; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .05em; color: var(--fg-muted);
}
.thread {
  padding: 8px 16px; font-size: 13px; cursor: pointer;
  border-left: 2px solid transparent; color: var(--fg-muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.thread:hover { color: var(--fg); background: var(--bg); }
.thread.active { color: var(--fg); font-weight: 500; border-left-color: var(--accent); }
.thread-empty { padding: 8px 16px; font-size: 12px; color: var(--fg-muted); font-style: italic; }
.sidebar-ft { margin-top: auto; padding: 16px; border-top: 1px solid var(--border); }
#setup-btn {
  width: 100%; padding: 8px; background: var(--surface); color: var(--fg-muted);
  border: 1px solid var(--border); border-radius: var(--radius); cursor: pointer; font-size: 13px;
}
#setup-btn:hover { color: var(--fg); border-color: var(--fg-muted); }
main { display: flex; flex-direction: column; padding: 24px; overflow: hidden; }
header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
header h1 { font-size: 16px; font-weight: 600; }
#status { font-size: 12px; color: var(--fg-muted); display: flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--fg-muted); flex-shrink: 0; }
.dot.ok { background: var(--success); }
.dot.off { background: var(--error); }
textarea {
  width: 100%; padding: 12px; font-size: 14px; font-family: inherit;
  background: var(--surface); color: var(--fg); border: 1px solid var(--border);
  border-radius: var(--radius); resize: vertical; min-height: 64px; line-height: 1.5;
}
textarea:focus { outline: none; border-color: var(--accent); }
textarea::placeholder { color: var(--fg-muted); }
.actions { display: flex; gap: 8px; margin: 8px 0 16px; }
.actions button {
  padding: 8px 24px; font-size: 13px; font-weight: 500;
  border-radius: var(--radius); border: none; cursor: pointer;
}
#run-btn { background: var(--accent); color: #fff; }
#run-btn:hover:not(:disabled) { opacity: .9; }
#run-btn:disabled { opacity: .4; cursor: not-allowed; }
#stop-btn { background: var(--surface); color: var(--fg-muted); border: 1px solid var(--border); }
#stop-btn:disabled { opacity: .4; cursor: not-allowed; }
#stop-btn:not(:disabled):hover { color: var(--error); border-color: var(--error); }
#output {
  flex: 1; overflow-y: auto; padding: 16px;
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-wrap: break-word;
}
#output:empty::before { content: "Output will appear here..."; color: var(--fg-muted); font-style: italic; }
.tool-badge {
  display: inline-block; padding: 2px 8px; margin: 2px 4px 2px 0;
  font-size: 11px; font-weight: 500;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--tool-bg); color: var(--tool-fg); border-radius: 3px; vertical-align: middle;
}
.err-line { display: block; color: var(--error); padding: 2px 0; font-size: 13px; }
.err-block {
  display: block; color: var(--error); padding: 8px 12px; margin: 8px 0;
  border-left: 3px solid var(--error); font-size: 13px;
}
.done-line {
  display: block; color: var(--fg-muted); padding: 12px 0 0; font-size: 12px;
  border-top: 1px solid var(--border); margin-top: 12px;
}
@media (max-width: 640px) { #app { grid-template-columns: 1fr; } aside { display: none; } }
</style>
</head>
<body>
<div id="app">
<aside>
  <div class="sidebar-hd">Threads</div>
  <div id="thread-list"><div class="thread-empty">No threads yet</div></div>
  <div class="sidebar-ft"><button id="setup-btn">Setup</button></div>
</aside>
<main>
  <header><h1>Local Smartz</h1><div id="status"><span class="dot"></span>Loading...</div></header>
  <textarea id="prompt" placeholder="Research prompt... (Cmd+Enter to run)" rows="3" autofocus></textarea>
  <div class="actions">
    <button id="run-btn">Run</button>
    <button id="stop-btn" disabled>Stop</button>
  </div>
  <div id="output"></div>
</main>
</div>
<script>
!function() {
  const $ = id => document.getElementById(id);
  const out = $('output'), pr = $('prompt'), runBtn = $('run-btn'), stopBtn = $('stop-btn');
  let ctrl = null, activeThread = null;

  const setRunning = v => { runBtn.disabled = v; stopBtn.disabled = !v; pr.disabled = v; };
  const append = el => { out.appendChild(el); out.scrollTop = out.scrollHeight; };
  const makeEl = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  };

  function handleEvent(d) {
    if (d.type === 'text') append(makeEl('span', '', d.content));
    else if (d.type === 'tool') append(makeEl('span', 'tool-badge', d.name));
    else if (d.type === 'tool_error') append(makeEl('span', 'err-line', '[' + d.name + '] ' + d.message));
    else if (d.type === 'done') {
      append(makeEl('span', 'done-line', 'Done (' + (d.duration_ms / 1000).toFixed(1) + 's)'));
      setRunning(false); fetchThreads();
    } else if (d.type === 'error') {
      append(makeEl('span', 'err-block', d.message));
      setRunning(false);
    }
  }

  async function streamSSE(url, options) {
    ctrl = new AbortController();
    setRunning(true);
    try {
      const req = Object.assign({ method: 'GET' }, options || {});
      req.signal = ctrl.signal;
      const res = await fetch(url, req);
      if (!res.ok) {
        const body = await res.text();
        try { handleEvent({ type: 'error', message: JSON.parse(body).error }); }
        catch(x) { handleEvent({ type: 'error', message: body || 'Request failed' }); }
        ctrl = null; setRunning(false); return;
      }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buf += dec.decode(chunk.value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        for (let i = 0; i < lines.length; i++) {
          if (lines[i].indexOf('data: ') === 0) {
            try { handleEvent(JSON.parse(lines[i].slice(6))); } catch(x) {}
          }
        }
      }
    } catch(e) {
      if (e.name !== 'AbortError') handleEvent({ type: 'error', message: e.message });
    }
    ctrl = null; setRunning(false);
  }

  runBtn.addEventListener('click', () => {
    const text = pr.value.trim();
    if (!text) return;
    out.innerHTML = '';
    const payload = { prompt: text };
    if (activeThread) payload.thread_id = activeThread;
    streamSSE('/api/research', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  });
  stopBtn.addEventListener('click', () => { if (ctrl) ctrl.abort(); });
  $('setup-btn').addEventListener('click', () => {
    out.innerHTML = '';
    streamSSE('/api/setup', { method: 'POST' });
  });
  pr.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); if (!runBtn.disabled) runBtn.click(); }
  });

  async function fetchThreads() {
    try {
      const r = await fetch('/api/threads'), threads = await r.json(), el = $('thread-list');
      if (!Array.isArray(threads) || !threads.length) {
        el.innerHTML = '<div class="thread-empty">No threads yet</div>'; return;
      }
      el.innerHTML = '';
      threads.forEach(t => {
        const id = t.thread_id || t.id || '', title = t.title || id;
        const d = makeEl('div', 'thread' + (id === activeThread ? ' active' : ''), title);
        d.onclick = () => { activeThread = (activeThread === id) ? null : id; fetchThreads(); };
        el.appendChild(d);
      });
    } catch(e) {}
  }

  async function fetchStatus() {
    try {
      const r = await fetch('/api/status'), d = await r.json();
      const ok = d.ready === true;
      const online = d.ollama && d.ollama.running;
      const label = ok ? 'Ready (' + d.profile + ')' : (online ? 'Setup required' : 'Offline');
      $('status').innerHTML = '<span class="dot ' + (ok ? 'ok' : 'off') + '"></span>' + label;
    } catch(e) {
      $('status').innerHTML = '<span class="dot off"></span>Offline';
    }
  }

  fetchStatus(); fetchThreads(); setInterval(fetchStatus, 30000);
}();
</script>
</body>
</html>"""


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
        elif path == "":
            self._serve_ui()
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/research":
            self._handle_research_post()
        elif path == "/api/setup":
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

    def _read_json_body(self) -> dict:
        content_length = self.headers.get("Content-Length", "0")
        try:
            length = int(content_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header") from exc

        raw_body = self.rfile.read(length) if length > 0 else b""
        if not raw_body:
            raise ValueError("Request body is required")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON") from exc

        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")

        return payload

    def _serialize_thread(self, thread: dict) -> dict:
        return {
            "id": thread.get("id", ""),
            "title": thread.get("title", ""),
            "entry_count": thread.get("entry_count", 0),
            "last_updated": _iso_timestamp(thread.get("updated_at")),
        }

    # ── Endpoints ──

    def _serve_ui(self):
        body = _UI_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_health(self):
        from localsmartz.profiles import get_profile

        cwd = Path.cwd()
        model_override = _saved_model_override(cwd)
        profile = get_profile(self._default_profile, model_override=model_override)
        self._json_response({"ok": True, "profile": profile["name"]})

    def _handle_status(self):
        from localsmartz.profiles import get_profile
        from localsmartz.ollama import check_server, get_version, list_models, model_available

        cwd = Path.cwd()
        model_override = _saved_model_override(cwd)
        profile = get_profile(self._default_profile, model_override=model_override)
        ollama_ok = check_server()
        version = get_version() if ollama_ok else None
        models = list_models() if ollama_ok else []
        required_models = [profile["planning_model"]]
        if profile["execution_model"] != profile["planning_model"]:
            required_models.append(profile["execution_model"])
        missing_models = [
            model for model in required_models
            if ollama_ok and not model_available(model)
        ]

        self._json_response({
            "profile": profile["name"],
            "planning_model": profile["planning_model"],
            "execution_model": profile["execution_model"],
            "ready": ollama_ok and not missing_models,
            "missing_models": missing_models,
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
        self._json_response([self._serialize_thread(thread) for thread in threads])

    def _handle_research(self, parsed):
        """Stream research results as Server-Sent Events."""
        params = parse_qs(parsed.query)
        prompt = params.get("prompt", [None])[0]
        thread_id = params.get("thread_id", [None])[0]
        profile_name = params.get("profile", [None])[0] or self._default_profile
        self._handle_research_request(prompt, thread_id, profile_name)

    def _handle_research_post(self):
        """Accept research requests as JSON for app clients."""
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._json_response({"error": str(exc)}, 400)
            return

        prompt = payload.get("prompt")
        thread_id = payload.get("thread_id")
        profile_name = payload.get("profile") or self._default_profile
        self._handle_research_request(prompt, thread_id, profile_name)

    def _handle_research_request(
        self,
        prompt: str | None,
        thread_id: str | None,
        profile_name: str | None,
    ):
        if not isinstance(prompt, str) or not prompt.strip():
            self._json_response({"error": "No prompt provided"}, 400)
            return

        if thread_id is not None and not isinstance(thread_id, str):
            self._json_response({"error": "thread_id must be a string"}, 400)
            return

        self._start_sse()

        try:
            self._stream_research(prompt.strip(), profile_name, thread_id)
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

        cwd = Path.cwd()
        model_override = _saved_model_override(cwd)

        # Preflight: required model must be available
        profile = get_profile(profile_name, model_override=model_override)
        model = profile["planning_model"]
        if not model_available(model):
            available = ", ".join(list_models()) or "none"
            self._send_event({
                "type": "error",
                "message": f"Model '{model}' not found \u2192 Pull it with: ollama pull {model}\nAvailable models: {available}",
            })
            return

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
            model_override=model_override,
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
            cwd = Path.cwd()
            model_override = _saved_model_override(cwd)
            profile = get_profile(self._default_profile, model_override=model_override)

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
    print(f"\n  Local Smartz running at http://localhost:{port}", file=sys.stderr)
    print(f"  Press Ctrl+C to stop.\n", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.shutdown()
