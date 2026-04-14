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

import functools
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
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


# Process-level warmup state. Keyed by model name. Values:
#   {"stage": "idle"|"loading"|"ready"|"error",
#    "started_at": float, "finished_at": float,
#    "error": str | None, "duration_ms": int}
# Accessed from the main request thread, background warmup threads, and
# startup. Always take _WARMUP_STATE_LOCK before reading/writing.
_WARMUP_STATE: dict[str, dict] = {}
_WARMUP_STATE_LOCK = threading.Lock()


def _warmup_in_background(model: str, keep_alive: str = "30m") -> None:
    """Kick off a background warmup and record state — used at server boot
    and from the ``/api/models/warmup`` POST handler.
    """
    from localsmartz.ollama import warmup_model

    if not model:
        return
    with _WARMUP_STATE_LOCK:
        current = _WARMUP_STATE.get(model, {})
        if current.get("stage") == "loading":
            return
        _WARMUP_STATE[model] = {
            "stage": "loading",
            "started_at": time.time(),
            "error": None,
            "duration_ms": 0,
        }

    def _run():
        ok, ms, err = warmup_model(model, keep_alive=keep_alive)
        with _WARMUP_STATE_LOCK:
            started = _WARMUP_STATE.get(model, {}).get("started_at", time.time())
            _WARMUP_STATE[model] = {
                "stage": "ready" if ok else "error",
                "started_at": started,
                "finished_at": time.time(),
                "error": err,
                "duration_ms": ms,
            }

    threading.Thread(target=_run, name=f"warmup-{model}", daemon=True).start()


class _HeartbeatPulse:
    """Background keep-alive for silent SSE periods.

    Fires a ``{"type": "heartbeat", "elapsed_s": N}`` event when the main
    stream has been silent longer than ``interval_s``. Required because the
    agent loop blocks in-place during tool calls and model cold-loads —
    without this the client sees "Thinking…" with no liveness signal, and
    intermediaries drop the idle connection.

    The sender (``send_event``) is expected to be serialized by the caller
    (the handler's ``_send_event`` takes a per-request lock).
    """

    def __init__(self, send_event, interval_s: float = 15.0):
        self._send = send_event
        self._interval = float(interval_s)
        self._stop = threading.Event()
        self._touch_lock = threading.Lock()
        self._last_event = time.time()
        self._thread: threading.Thread | None = None

    def touch(self) -> None:
        """Reset the silence timer. Call after every real event."""
        with self._touch_lock:
            self._last_event = time.time()

    def _run(self) -> None:
        # Poll in ticks of half the interval (capped at 1s) so stop() is
        # responsive in prod (interval=15s → 1s tick) and tests with short
        # intervals still observe emissions.
        poll_s = max(0.02, min(1.0, self._interval / 2.0))
        while not self._stop.wait(timeout=poll_s):
            with self._touch_lock:
                elapsed = time.time() - self._last_event
            if elapsed >= self._interval:
                try:
                    self._send({"type": "heartbeat", "elapsed_s": int(elapsed)})
                except Exception:
                    # Client disconnected — stop quietly.
                    return
                with self._touch_lock:
                    self._last_event = time.time()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="sse-heartbeat", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def _is_valid_tool_name(name: str, *, valid_names: set[str] | None = None) -> bool:
    """Small local model tool-name sanity check.

    Qwen3 and similar 8B models occasionally fabricate namespace prefixes
    (e.g. ``repo_browser.write_todos``) when forced to emit tool calls for
    prompts that don't need them. Our tools never contain dots or slashes —
    reject those outright before the LangGraph loop retries endlessly.
    """
    if not isinstance(name, str) or not name:
        return False
    if "." in name or "/" in name:
        return False
    if valid_names is not None and name not in valid_names:
        return False
    return True


_UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local Smartz</title>
<style>
:root {
  --bg: #0c0c0c; --surface: #161616; --surface-raised: #1c1c1c;
  --border: rgba(255,255,255,0.06); --border-hover: rgba(255,255,255,0.12);
  --fg: #e4e4e4; --fg-secondary: rgba(228,228,228,0.6); --fg-muted: rgba(228,228,228,0.3);
  --teal: #0ea5e9; --teal-dim: rgba(14,165,233,0.15); --teal-glow: rgba(14,165,233,0.25);
  --amber: #f59e0b; --amber-dim: rgba(245,158,11,0.15);
  --green: #22c55e; --green-dim: rgba(34,197,94,0.2);
  --red: #ef4444;
  --radius: 10px;
}
.wizard-container { max-width: 480px; margin: 0 auto; padding-top: 40px; }
.wizard-steps { display: flex; gap: 8px; justify-content: center; margin-bottom: 32px; }
.wizard-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--fg-muted); transition: background .3s; }
.wizard-dot.done { background: var(--teal); }
.wizard-dot.active { background: var(--fg); }
.wizard-title { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
.wizard-desc { font-size: 13px; color: var(--fg-muted); margin-bottom: 24px; line-height: 1.5; }
.wizard-status { display: flex; align-items: center; gap: 8px; padding: 12px; background: var(--surface-raised); border-radius: 6px; margin-bottom: 12px; font-size: 13px; }
.wizard-model-option {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 12px; margin-bottom: 4px; background: var(--surface-raised);
  border: 1px solid var(--border); border-radius: 6px; cursor: pointer; transition: all .2s;
}
.wizard-model-option:hover { border-color: var(--teal-dim); }
.wizard-model-option.selected { border-color: var(--teal); background: var(--teal-dim); }
.wizard-model-name { font-size: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 500; }
.wizard-model-meta { font-size: 11px; color: var(--fg-muted); }
.wizard-progress { width: 100%; height: 4px; background: var(--surface-raised); border-radius: 2px; margin: 12px 0; overflow: hidden; }
.wizard-progress-fill { height: 100%; background: var(--teal); width: 0; transition: width .3s; }
.wizard-btn { display: inline-block; padding: 10px 32px; font-size: 14px; font-weight: 500; border: none; border-radius: 6px; cursor: pointer; transition: all .2s; margin-top: 16px; }
.wizard-btn-primary { background: linear-gradient(135deg, var(--teal), #6366f1); color: #fff; box-shadow: 0 2px 8px var(--teal-dim); }
.wizard-btn-primary:hover { box-shadow: 0 4px 16px var(--teal-glow); }
.wizard-btn-primary:disabled { opacity: .3; cursor: not-allowed; }
.wizard-btn-secondary { background: var(--surface); color: var(--fg-muted); border: 1px solid var(--border); margin-left: 8px; }
.wizard-test-output { padding: 12px; background: var(--surface-raised); border-radius: 6px; font-size: 13px; line-height: 1.6; white-space: pre-wrap; min-height: 60px; margin: 12px 0; }
.wizard-input {
  width: 100%; padding: 10px 12px; font-size: 13px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--surface-raised); color: var(--fg);
  border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px;
}
.wizard-input:focus { outline: none; border-color: var(--teal); }
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: var(--bg); color: var(--fg); height: 100vh; overflow: hidden;
}
#app { display: grid; grid-template-columns: 240px 1fr; height: 100vh; }
aside {
  background: var(--surface); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow-y: auto;
}
.brand {
  display: flex; align-items: center; gap: 10px;
  padding: 18px 16px 14px; border-bottom: 1px solid var(--border);
}
.brand-icon {
  width: 32px; height: 32px; border-radius: 8px;
  background: linear-gradient(135deg, var(--teal), #0284c7);
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; color: #fff; flex-shrink: 0;
}
.brand-text { font-size: 13px; font-weight: 600; color: var(--fg); }
.brand-sub { font-size: 10px; color: var(--fg-muted); margin-top: 1px; }
.panel-section {
  padding: 12px 0; border-bottom: 1px solid var(--border);
}
.panel-label {
  padding: 0 16px 6px; font-size: 10px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .08em; color: var(--fg-muted);
}
.model-card {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px; cursor: default;
}
.model-name { font-size: 12px; font-weight: 500; color: var(--fg); }
.model-size { font-size: 11px; color: var(--fg-muted); margin-top: 2px; }
.model-change {
  font-size: 11px; color: var(--teal); cursor: pointer; flex-shrink: 0;
  padding: 3px 8px; border-radius: 4px; border: 1px solid var(--teal-dim);
  transition: background .15s;
}
.model-change:hover { background: var(--teal-dim); }
.model-list { display: flex; flex-direction: column; }
.model-option {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 16px; font-size: 12px; cursor: pointer;
  border-bottom: 1px solid var(--border); transition: background .15s;
}
.model-option:hover { background: var(--surface-raised); }
.model-option:last-child { border-bottom: none; }
.folder-item {
  display: flex; align-items: center; padding: 5px 16px; font-size: 11px;
  color: var(--fg-secondary); gap: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.folder-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--teal); flex-shrink: 0;
}
.folder-remove {
  margin-left: auto; cursor: pointer; color: var(--fg-muted);
  font-size: 14px; padding: 0 4px; transition: color .15s; flex-shrink: 0;
}
.folder-remove:hover { color: var(--red); }
.add-folder {
  display: block; width: calc(100% - 32px); margin: 6px 16px 4px;
  padding: 5px 10px; font-size: 11px; cursor: pointer;
  background: transparent; color: var(--fg-muted);
  border: 1px dashed var(--border-hover); border-radius: 5px;
  transition: color .15s, border-color .15s;
}
.add-folder:hover { color: var(--teal); border-color: var(--teal-dim); }
.folder-input {
  width: calc(100% - 32px); display: block; padding: 5px 8px; font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--surface-raised); color: var(--fg);
  border: 1px solid var(--border); border-radius: 4px;
  margin: 4px 16px 0; outline: none;
}
.folder-input:focus { border-color: var(--teal); }
.thread {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 8px 16px; cursor: pointer; transition: background .15s;
}
.thread:hover { background: var(--surface-raised); }
.thread.active { background: var(--teal-dim); }
.thread-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--fg-muted); flex-shrink: 0; margin-top: 4px;
}
.thread.active .thread-dot { background: var(--teal); }
.thread-info { overflow: hidden; }
.thread-title {
  font-size: 12px; color: var(--fg); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.thread-time { font-size: 10px; color: var(--fg-muted); margin-top: 2px; }
.thread-empty { padding: 8px 16px; font-size: 11px; color: var(--fg-muted); font-style: italic; }
.status-bar {
  padding: 10px 16px; font-size: 11px; color: var(--fg-muted);
  border-top: 1px solid var(--border); display: flex; align-items: center; gap: 6px;
  margin-top: auto;
}
.status-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--fg-muted); flex-shrink: 0;
}
main {
  display: flex; flex-direction: column; padding: 28px 32px 24px;
  overflow: hidden; background: var(--bg);
}
.main-header { margin-bottom: 20px; }
.main-header h1 { font-size: 20px; font-weight: 600; color: var(--fg); }
.main-header p { font-size: 13px; color: var(--fg-muted); margin-top: 4px; }
textarea {
  width: 100%; padding: 14px; font-size: 14px; font-family: inherit;
  background: var(--surface); color: var(--fg); border: 1px solid var(--border);
  border-radius: var(--radius); resize: vertical; min-height: 80px; line-height: 1.6;
  transition: border-color .15s;
}
textarea:focus { outline: none; border-color: var(--teal); }
textarea::placeholder { color: var(--fg-muted); }
.actions { display: flex; gap: 8px; margin: 10px 0 18px; }
.btn-run {
  padding: 9px 28px; font-size: 13px; font-weight: 600;
  border-radius: var(--radius); border: none; cursor: pointer;
  background: var(--teal); color: #fff; transition: opacity .15s;
}
.btn-run:hover:not(:disabled) { opacity: .88; }
.btn-run:disabled { opacity: .35; cursor: not-allowed; }
.btn-stop {
  padding: 9px 20px; font-size: 13px; font-weight: 500;
  border-radius: var(--radius); cursor: pointer;
  background: var(--surface); color: var(--fg-muted);
  border: 1px solid var(--border); transition: color .15s, border-color .15s;
}
.btn-stop:disabled { opacity: .35; cursor: not-allowed; }
.btn-stop:not(:disabled):hover { color: var(--red); border-color: var(--red); }
#output {
  flex: 1; overflow-y: auto; padding: 18px;
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  font-size: 14px; line-height: 1.7; white-space: pre-wrap; word-wrap: break-word;
  color: var(--fg);
}
#output:empty::before { content: "Output will appear here..."; color: var(--fg-muted); font-style: italic; }
.done-line {
  display: block; color: var(--fg-muted); padding: 12px 0 0; font-size: 12px;
  border-top: 1px solid var(--border); margin-top: 12px;
}
.tool-badge {
  display: inline-block; padding: 2px 8px; margin: 2px 4px 2px 0;
  font-size: 11px; font-weight: 500;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--teal-dim); color: var(--teal); border-radius: 3px;
}
.err-line { display: block; color: var(--red); padding: 2px 0; font-size: 13px; }
.err-block {
  display: block; color: var(--red); padding: 8px 12px; margin: 8px 0;
  border-left: 3px solid var(--red); font-size: 13px;
}
@media (max-width: 640px) { #app { grid-template-columns: 1fr; } aside { display: none; } }
</style>
</head>
<body>
<div id="app">
<aside>
  <div class="brand">
    <div class="brand-icon">LS</div>
    <div><div class="brand-text">Local Smartz</div><div class="brand-sub" id="version"></div></div>
  </div>
  <div class="panel-section" id="model-section">
    <div class="panel-label">Model</div>
    <div id="model-content"></div>
  </div>
  <div class="panel-section">
    <div class="panel-label">Folders</div>
    <div id="folder-list"></div>
    <button class="add-folder" id="add-folder-btn">+ Add folder</button>
  </div>
  <div class="panel-section" style="flex:1;border-bottom:none">
    <div class="panel-label">Threads</div>
    <div id="thread-list"><div class="thread-empty">No threads yet</div></div>
  </div>
  <div class="status-bar" id="status-bar">
    <span class="status-dot"></span> Loading...
  </div>
</aside>
<main>
  <div id="wizard" style="display:none">
    <div class="wizard-container">
      <div class="wizard-steps" id="wizard-steps"></div>
      <div id="wizard-content"></div>
    </div>
  </div>
  <div class="main-header"><h1>Research</h1><p>Ask anything. Local models, no cloud dependency.</p></div>
  <textarea id="prompt" placeholder="What would you like to research?" rows="3" autofocus></textarea>
  <div class="actions">
    <button id="run-btn" class="btn-run">Run</button>
    <button id="stop-btn" class="btn-stop" disabled>Stop</button>
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

  function timeAgo(iso) {
    if (!iso) return 'unknown';
    const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

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
  pr.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); if (!runBtn.disabled) runBtn.click(); }
  });

  let currentModel = '', currentProfile = '';
  async function fetchModels() {
    try {
      const r = await fetch('/api/models'), d = await r.json();
      currentModel = d.current || '';
      currentProfile = d.profile || '';
      renderModelCard(d);
    } catch(e) {}
  }

  function renderModelCard(d) {
    const mc = $('model-content');
    const model = d.models.find(m => m.name === d.current) || d.models[0] || {};
    mc.innerHTML = '<div class="model-card" id="model-card-inner">' +
      '<div><div class="model-name">' + (d.current || 'No model') + '</div>' +
      '<div class="model-size">' + (model.size_gb ? model.size_gb.toFixed(1) + ' GB' : '') +
      (d.profile ? ' \u00b7 ' + d.profile + ' profile' : '') + '</div></div>' +
      '<span class="model-change">Change</span></div>';
    mc.querySelector('.model-change').onclick = () => renderModelList(d.models);
  }

  function renderModelList(models) {
    const mc = $('model-content');
    let html = '<div class="model-list">';
    models.forEach(m => {
      const active = m.name === currentModel ? ' style="color:var(--teal)"' : '';
      html += '<div class="model-option"' + active + ' data-model="' + m.name + '">' +
        '<span class="model-name">' + m.name + '</span>' +
        '<span class="model-size">' + m.size_gb.toFixed(1) + ' GB</span></div>';
    });
    html += '<div class="model-option" style="color:var(--fg-muted);border:none" id="model-cancel">Cancel</div></div>';
    mc.innerHTML = html;
    mc.querySelectorAll('.model-option[data-model]').forEach(el => {
      el.onclick = () => selectModel(el.dataset.model);
    });
    $('model-cancel').onclick = () => fetchModels();
  }

  async function selectModel(name) {
    try {
      const r = await fetch('/api/models/select', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: name })
      });
      if (r.ok) { fetchModels(); fetchStatus(); }
    } catch(e) {}
  }

  async function fetchFolders() {
    try {
      const r = await fetch('/api/folders'), d = await r.json();
      renderFolders(d);
    } catch(e) {}
  }

  function renderFolders(d) {
    const fl = $('folder-list');
    fl.innerHTML = '';
    const ws = makeEl('div', 'folder-item', '');
    ws.innerHTML = '<span class="folder-dot"></span> ' + shortenPath(d.workspace);
    fl.appendChild(ws);
    (d.folders || []).forEach(f => {
      const item = makeEl('div', 'folder-item', '');
      item.innerHTML = '<span class="folder-dot" style="background:var(--amber)"></span> ' +
        shortenPath(f) + '<span class="folder-remove" data-path="' + f + '">\u00d7</span>';
      item.querySelector('.folder-remove').onclick = () => removeFolder(f);
      fl.appendChild(item);
    });
  }

  function shortenPath(p) {
    return p.replace(/^\/Users\/[^/]+/, '~');
  }

  $('add-folder-btn').addEventListener('click', function() {
    const existing = $('folder-input-field');
    if (existing) { existing.remove(); return; }
    const inp = document.createElement('input');
    inp.type = 'text'; inp.id = 'folder-input-field';
    inp.className = 'folder-input';
    inp.placeholder = 'Enter folder path...';
    inp.addEventListener('keydown', async e => {
      if (e.key === 'Enter') {
        const path = inp.value.trim();
        if (!path) return;
        const r = await fetch('/api/folders', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: path })
        });
        if (r.ok) { inp.remove(); fetchFolders(); }
        else {
          const err = await r.json();
          inp.style.borderColor = 'var(--red)';
          inp.placeholder = err.error || 'Invalid path';
          inp.value = '';
        }
      } else if (e.key === 'Escape') { inp.remove(); }
    });
    this.parentNode.insertBefore(inp, this);
    inp.focus();
  });

  async function removeFolder(path) {
    await fetch('/api/folders', {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: path })
    });
    fetchFolders();
  }

  async function fetchThreads() {
    try {
      const r = await fetch('/api/threads'), threads = await r.json(), el = $('thread-list');
      if (!Array.isArray(threads) || !threads.length) {
        el.innerHTML = '<div class="thread-empty">No threads yet</div>'; return;
      }
      el.innerHTML = '';
      threads.forEach(t => {
        const id = t.id || '', title = t.title || id;
        const d = makeEl('div', 'thread' + (id === activeThread ? ' active' : ''), '');
        d.innerHTML = '<span class="thread-dot"></span><div class="thread-info">' +
          '<div class="thread-title">' + title + '</div>' +
          '<div class="thread-time">' + (t.entry_count || 0) + ' entr' +
          ((t.entry_count === 1) ? 'y' : 'ies') + ' \u00b7 ' + timeAgo(t.last_updated) + '</div></div>';
        d.onclick = () => { activeThread = (activeThread === id) ? null : id; fetchThreads(); };
        el.appendChild(d);
      });
    } catch(e) {}
  }

  async function fetchStatus() {
    try {
      const r = await fetch('/api/status'), d = await r.json();
      const online = d.ollama && d.ollama.running;
      const modelCount = d.ollama && d.ollama.models ? d.ollama.models.length : 0;
      const sb = $('status-bar');
      sb.innerHTML = '<span class="status-dot" style="background:var(--' + (online ? 'green' : 'red') +
        ');box-shadow:0 0 4px var(--' + (online ? 'green-dim' : '') + ')"></span> ' +
        (online ? 'Ollama running \u00b7 ' + modelCount + ' model' + (modelCount !== 1 ? 's' : '') : 'Ollama offline');
      const v = $('version');
      if (v && d.version) v.textContent = 'v' + d.version;
      // Wizard trigger
      if (!d.ready && !wizardDismissed) showWizard();
    } catch(e) {
      $('status-bar').innerHTML = '<span class="status-dot" style="background:var(--red)"></span> Offline';
    }
  }

  // \u2500\u2500 Setup Wizard \u2500\u2500
  let wizardStep = 0, wizardDismissed = false, wizardStatus = null;
  const wizardEl = $('wizard');
  const SUGGESTED_MODELS = [
    { name: 'qwen3:8b-q4_K_M', size: '5 GB', desc: 'Fast, good for quick questions', minRam: 8 },
    { name: 'qwen2.5-coder:32b-instruct-q5_K_M', size: '23 GB', desc: 'Strong coding and analysis', minRam: 32 },
    { name: 'llama3.1:70b-instruct-q5_K_M', size: '40 GB', desc: 'Most capable, best quality', minRam: 64 },
  ];

  function showWizard() {
    wizardEl.style.display = '';
    document.querySelectorAll('main > :not(#wizard)').forEach(el => el.style.display = 'none');
    document.querySelector('aside').style.opacity = '0.4';
    document.querySelector('aside').style.pointerEvents = 'none';
    renderWizardStep();
  }

  function hideWizard() {
    wizardDismissed = true;
    wizardEl.style.display = 'none';
    document.querySelectorAll('main > :not(#wizard)').forEach(el => el.style.display = '');
    document.querySelector('aside').style.opacity = '';
    document.querySelector('aside').style.pointerEvents = '';
    fetchModels(); fetchFolders(); fetchThreads(); fetchStatus();
  }

  function renderWizardDots() {
    const dots = $('wizard-steps');
    dots.innerHTML = '';
    for (let i = 0; i < 4; i++) {
      dots.appendChild(makeEl('span', 'wizard-dot' + (i < wizardStep ? ' done' : '') + (i === wizardStep ? ' active' : '')));
    }
  }

  function renderWizardStep() {
    renderWizardDots();
    const c = $('wizard-content');
    c.innerHTML = '';
    if (wizardStep === 0) renderWizStep1(c);
    else if (wizardStep === 1) renderWizStep2(c);
    else if (wizardStep === 2) renderWizStep3(c);
    else if (wizardStep === 3) renderWizStep4(c);
  }

  // Step 1: Ollama Status Check
  function renderWizStep1(c) {
    c.innerHTML = '<div class="wizard-title">Welcome to Local Smartz</div>' +
      '<div class="wizard-desc">Let\u2019s get you set up. This takes about a minute.</div>' +
      '<div id="wiz-status">Checking Ollama...</div>';
    fetch('/api/status').then(r => r.json()).then(d => {
      wizardStatus = d;
      const el = $('wiz-status');
      if (d.ollama && d.ollama.running) {
        let info = '<div class="wizard-status"><span style="color:var(--green)">\u2713</span> Ollama running' +
          (d.ollama.version ? ' (v' + d.ollama.version + ')' : '') + '</div>';
        if (d.ram_gb) info += '<div class="wizard-status"><span style="color:var(--green)">\u2713</span> ' +
          d.ram_gb + ' GB RAM</div>';
        el.innerHTML = info;
        setTimeout(() => { wizardStep = 1; renderWizardStep(); }, 1500);
      } else {
        let instruct = '';
        if (d.platform === 'darwin') {
          instruct = 'Download Ollama from <a href="https://ollama.com/download" target="_blank" style="color:var(--teal)">ollama.com/download</a>, open the app, then click Check Again.';
        } else {
          instruct = 'Run: <code style="background:var(--surface-raised);padding:2px 6px;border-radius:3px">curl -fsSL https://ollama.ai/install.sh | sh && ollama serve</code>';
        }
        el.innerHTML = '<div class="wizard-status"><span style="color:var(--red)">\u2717</span> Ollama is not running</div>' +
          '<div class="wizard-desc">' + instruct + '</div>' +
          '<button class="wizard-btn wizard-btn-secondary" onclick="renderWizStep1($(\'wizard-content\'))">Check Again</button>';
      }
    }).catch(() => {
      $('wiz-status').innerHTML = '<div class="wizard-status"><span style="color:var(--red)">\u2717</span> Cannot reach server</div>';
    });
  }

  // Step 2: Model Selection
  function renderWizStep2(c) {
    c.innerHTML = '<div class="wizard-title">Choose a model</div>' +
      '<div class="wizard-desc">Pick an AI model to power your research. Larger models give better results but need more RAM and disk space.</div>' +
      '<div id="wiz-models">Loading models...</div>';
    fetch('/api/models').then(r => r.json()).then(d => {
      const el = $('wiz-models');
      let html = '';
      const existing = d.models || [];
      const ramGb = (wizardStatus && wizardStatus.ram_gb) || 8;

      if (existing.length) {
        html += '<div style="font-size:11px;color:var(--fg-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Already downloaded</div>';
        existing.forEach(m => {
          html += '<div class="wizard-model-option" data-model="' + m.name + '">' +
            '<div><div class="wizard-model-name">' + m.name + '</div>' +
            '<div class="wizard-model-meta">' + m.size_gb.toFixed(1) + ' GB</div></div></div>';
        });
      }

      // Suggested models not yet downloaded
      const existingNames = existing.map(m => m.name);
      const suggestions = SUGGESTED_MODELS.filter(s => !existingNames.includes(s.name) && s.minRam <= ramGb);
      if (suggestions.length) {
        html += '<div style="font-size:11px;color:var(--fg-muted);text-transform:uppercase;letter-spacing:.06em;margin:16px 0 8px">Download a new model</div>';
        suggestions.forEach(s => {
          html += '<div class="wizard-model-option" data-download="' + s.name + '">' +
            '<div><div class="wizard-model-name">' + s.name + '</div>' +
            '<div class="wizard-model-meta">' + s.size + ' \u2014 ' + s.desc + '</div></div>' +
            '<span style="color:var(--teal);font-size:11px">Download</span></div>';
        });
      }

      html += '<div id="wiz-model-actions" style="margin-top:16px"></div>';
      el.innerHTML = html;

      // Click handlers for existing models
      el.querySelectorAll('[data-model]').forEach(opt => {
        opt.onclick = () => {
          el.querySelectorAll('.wizard-model-option').forEach(o => o.classList.remove('selected'));
          opt.classList.add('selected');
          $('wiz-model-actions').innerHTML = '<button class="wizard-btn wizard-btn-primary" id="wiz-select-btn">Select</button>';
          $('wiz-select-btn').onclick = () => selectWizModel(opt.dataset.model);
        };
      });

      // Click handlers for download options
      el.querySelectorAll('[data-download]').forEach(opt => {
        opt.onclick = () => downloadWizModel(opt.dataset.download, el);
      });
    });
  }

  function selectWizModel(name) {
    fetch('/api/models/select', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({model: name})
    }).then(r => { if (r.ok) { wizardStep = 2; renderWizardStep(); } });
  }

  function downloadWizModel(name, container) {
    const actions = $('wiz-model-actions');
    actions.innerHTML = '<div class="wizard-desc">Downloading ' + name + '...</div>' +
      '<div class="wizard-progress"><div class="wizard-progress-fill" id="wiz-dl-progress"></div></div>' +
      '<div id="wiz-dl-status" style="font-size:11px;color:var(--fg-muted)">Starting...</div>';

    fetch('/api/setup', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({model: name})
    }).then(res => {
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '';
      function read() {
        reader.read().then(({done, value}) => {
          if (done) return;
          buf += dec.decode(value, {stream:true});
          const lines = buf.split('\n'); buf = lines.pop();
          lines.forEach(line => {
            if (line.indexOf('data: ') === 0) {
              try {
                const ev = JSON.parse(line.slice(6));
                if (ev.type === 'text') $('wiz-dl-status').textContent = ev.content;
                if (ev.type === 'done') {
                  actions.innerHTML = '<div class="wizard-status"><span style="color:var(--green)">\u2713</span> Downloaded ' + name + '</div>' +
                    '<button class="wizard-btn wizard-btn-primary" id="wiz-post-dl-select">Select ' + name + '</button>';
                  $('wiz-post-dl-select').onclick = () => selectWizModel(name);
                }
                if (ev.type === 'error') {
                  actions.innerHTML = '<div class="wizard-status"><span style="color:var(--red)">\u2717</span> ' + ev.message + '</div>' +
                    '<button class="wizard-btn wizard-btn-secondary" onclick="renderWizStep2($(\'wizard-content\'))">Try Again</button>';
                }
              } catch(e) {}
            }
          });
          read();
        });
      }
      read();
    });
  }

  // Step 3: Workspace
  function renderWizStep3(c) {
    fetch('/api/folders').then(r => r.json()).then(d => {
      c.innerHTML = '<div class="wizard-title">Set your workspace</div>' +
        '<div class="wizard-desc">Where are the files you want to research? Local Smartz can read PDFs, spreadsheets, and text files from folders you choose.</div>' +
        '<div class="wizard-status" style="margin-bottom:16px"><span style="color:var(--teal)">\u25cf</span> ' + d.workspace + '</div>' +
        '<input class="wizard-input" id="wiz-folder-input" placeholder="Add another folder path (optional)">' +
        '<div id="wiz-folder-list"></div>' +
        '<div id="wiz-folder-error" style="font-size:12px;color:var(--red);margin-top:4px"></div>' +
        '<button class="wizard-btn wizard-btn-primary" id="wiz-ws-continue">Continue</button>';

      $('wiz-folder-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          const path = e.target.value.trim();
          if (!path) return;
          fetch('/api/folders', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({path: path})
          }).then(r => {
            if (r.ok) { e.target.value = ''; $('wiz-folder-error').textContent = ''; renderWizFolders(); }
            else r.json().then(d => { $('wiz-folder-error').textContent = d.error || 'Invalid path'; });
          });
        }
      });

      $('wiz-ws-continue').onclick = () => { wizardStep = 3; renderWizardStep(); };
      renderWizFolders();
    });
  }

  function renderWizFolders() {
    fetch('/api/folders').then(r => r.json()).then(d => {
      const el = $('wiz-folder-list');
      if (!el) return;
      el.innerHTML = '';
      (d.folders || []).forEach(f => {
        const item = makeEl('div', 'wizard-status', '');
        item.innerHTML = '<span style="color:var(--amber)">\u25cf</span> ' + f +
          ' <span style="cursor:pointer;color:var(--fg-muted);margin-left:auto" data-rmfolder="' + f + '">\u00d7</span>';
        item.querySelector('[data-rmfolder]').onclick = () => {
          fetch('/api/folders', {method:'DELETE',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({path:f})}).then(() => renderWizFolders());
        };
        el.appendChild(item);
      });
    });
  }

  // Step 4: Test Run
  function renderWizStep4(c) {
    c.innerHTML = '<div class="wizard-title">Testing your setup</div>' +
      '<div class="wizard-desc">Running a quick query to make sure everything works...</div>' +
      '<div class="wizard-test-output" id="wiz-test-out">Starting...</div>' +
      '<div id="wiz-test-actions"></div>';

    const testOut = $('wiz-test-out');
    testOut.textContent = '';

    fetch('/api/research', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({prompt: 'What is artificial intelligence? Answer in one sentence.'})
    }).then(res => {
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '';
      function read() {
        reader.read().then(({done, value}) => {
          if (done) return;
          buf += dec.decode(value, {stream:true});
          const lines = buf.split('\n'); buf = lines.pop();
          lines.forEach(line => {
            if (line.indexOf('data: ') === 0) {
              try {
                const ev = JSON.parse(line.slice(6));
                if (ev.type === 'text') testOut.textContent += ev.content;
                if (ev.type === 'tool') testOut.textContent += '[' + ev.name + '] ';
                if (ev.type === 'done') {
                  $('wiz-test-actions').innerHTML =
                    '<div class="wizard-status" style="margin-top:12px"><span style="color:var(--green)">\u2713</span> Everything is working!</div>' +
                    '<button class="wizard-btn wizard-btn-primary" id="wiz-done-btn">Start Researching</button>';
                  $('wiz-done-btn').onclick = hideWizard;
                }
                if (ev.type === 'error') {
                  testOut.textContent += '\nError: ' + ev.message;
                  $('wiz-test-actions').innerHTML =
                    '<div class="wizard-status" style="margin-top:12px"><span style="color:var(--red)">\u2717</span> Test failed</div>' +
                    '<button class="wizard-btn wizard-btn-secondary" onclick="renderWizStep4($(\'wizard-content\'))">Try Again</button>' +
                    '<button class="wizard-btn wizard-btn-primary" onclick="hideWizard()">Skip</button>';
                }
              } catch(e) {}
            }
          });
          read();
        });
      }
      read();
    }).catch(e => {
      testOut.textContent = 'Error: ' + e.message;
      $('wiz-test-actions').innerHTML =
        '<button class="wizard-btn wizard-btn-secondary" onclick="renderWizStep4($(\'wizard-content\'))">Try Again</button>';
    });
  }

  fetchStatus(); fetchModels(); fetchFolders(); fetchThreads();
  setInterval(fetchStatus, 30000);
}();
</script>
</body>
</html>"""


def _json_body(handler):
    """Decorator: parse JSON body into a ``body`` kwarg, emit 400 on failure.

    Replaces the repeated ``try: body = self._read_json_body() except
    ValueError as e: self._json_response({"error": str(e)}, 400); return``
    boilerplate in every POST handler.
    """
    @functools.wraps(handler)
    def wrapped(self, *args, **kwargs):
        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._json_response({"error": str(exc)}, 400)
            return None
        return handler(self, *args, body=body, **kwargs)

    return wrapped


class LocalSmartzHandler(BaseHTTPRequestHandler):
    """HTTP request handler with SSE support."""

    # Set by start_server() — profile override from CLI (None = auto-detect)
    _default_profile: str | None = None
    _model_override: str | None = None

    def setup(self):
        # Pre-create the per-request SSE write lock here instead of
        # lazy-initializing inside ``_send_event``. The old lazy pattern had a
        # TOCTOU race: the main handler thread and the heartbeat pulse thread
        # could both observe ``_sse_lock is None`` concurrently, each create
        # their own ``threading.Lock``, and end up interleaving bytes on the
        # SSE wire. Creating it here — before any method can see the
        # instance — is race-free.
        super().setup()
        self._sse_lock = threading.Lock()

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
        elif path == "/api/models":
            self._handle_models()
        elif path == "/api/models/catalog":
            self._handle_models_catalog()
        elif path == "/api/models/warmup":
            self._handle_warmup_status()
        elif path == "/api/agents":
            self._handle_agents()
        elif path == "/api/agents/models":
            self._handle_agents_models()
        elif path == "/api/skills":
            self._handle_list_skills()
        elif path == "/api/ollama/info":
            self._handle_ollama_info()
        elif path == "/api/observability/info":
            self._handle_observability_info()
        elif path == "/api/folders":
            self._handle_folders()
        elif path == "/api/secrets":
            self._handle_secrets_list()
        elif path == "/api/logs":
            self._handle_logs_list(parsed)
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
        elif path == "/api/models/select":
            self._handle_model_select()
        elif path == "/api/models/pull":
            self._handle_model_pull()
        elif path == "/api/models/warmup":
            self._handle_warmup_start()
        elif path == "/api/skills/refactor":
            self._handle_skill_refactor()
        elif path == "/api/skills/new":
            self._handle_skill_new()
        elif path == "/api/plugins/save":
            self._handle_plugin_save()
        elif path == "/api/folders":
            self._handle_folder_add()
        elif path == "/api/secrets":
            self._handle_secrets_set()
        elif path == "/api/issues/report":
            self._handle_issues_report()
        elif path.startswith("/api/agents/") and path.endswith("/model"):
            # POST /api/agents/<name>/model — persist per-agent model override.
            agent_name = path[len("/api/agents/"):-len("/model")]
            self._handle_agent_model_set(agent_name)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/folders":
            self._handle_folder_delete()
        elif path == "/api/models":
            self._handle_model_delete()
        elif path == "/api/secrets":
            self._handle_secrets_delete(parsed)
        elif path == "/api/logs":
            self._handle_logs_clear()
        else:
            self._json_response({"error": "Not found"}, 404)

    # ── Helpers ──

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
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
        """Serialized SSE write. The per-request lock is created in
        ``setup()`` so the main stream loop and the heartbeat pulse can race
        for it without a TOCTOU double-initialization.
        """
        with self._sse_lock:
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
        from localsmartz.ollama import (
            check_server, get_version, list_models, model_available,
            resolve_available_model,
        )
        from localsmartz.utils.hardware import get_ram_gb
        from localsmartz import __version__
        import platform as _platform

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

        # Fallback: if the planning model is missing but a usable substitute is
        # installed, the user is functionally ready — surface that.
        effective_model = profile["planning_model"]
        fallback_warning: str | None = None
        if ollama_ok and profile["planning_model"] in missing_models:
            chosen, warning = resolve_available_model(profile["planning_model"])
            if chosen and chosen != profile["planning_model"]:
                effective_model = chosen
                fallback_warning = warning
                missing_models = [m for m in missing_models if m != profile["planning_model"]]

        self._json_response({
            "profile": profile["name"],
            "planning_model": profile["planning_model"],
            "execution_model": profile["execution_model"],
            "effective_model": effective_model,
            "fallback_warning": fallback_warning,
            "ready": ollama_ok and not missing_models,
            "missing_models": missing_models,
            "ollama": {
                "running": ollama_ok,
                "version": version,
                "models": models,
            },
            "version": __version__,
            "ram_gb": get_ram_gb(),
            "platform": _platform.system().lower(),
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
        agent = params.get("agent", [None])[0]
        self._handle_research_request(prompt, thread_id, profile_name, agent)

    @_json_body
    def _handle_research_post(self, *, body):
        """Accept research requests as JSON for app clients."""
        prompt = body.get("prompt")
        thread_id = body.get("thread_id")
        profile_name = body.get("profile") or self._default_profile
        agent = body.get("agent")  # Optional: pin to a single agent
        self._handle_research_request(prompt, thread_id, profile_name, agent)

    def _handle_research_request(
        self,
        prompt: str | None,
        thread_id: str | None,
        profile_name: str | None,
        agent: str | None = None,
    ):
        if not isinstance(prompt, str) or not prompt.strip():
            self._json_response({"error": "No prompt provided"}, 400)
            return

        if thread_id is not None and not isinstance(thread_id, str):
            self._json_response({"error": "thread_id must be a string"}, 400)
            return

        self._start_sse()

        try:
            self._stream_research(prompt.strip(), profile_name, thread_id, agent)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            detail = traceback.format_exc()
            try:
                self._send_event({"type": "error", "message": f"{e}\n\n{detail}"})
            except Exception:
                pass

    def _preflight_model(
        self,
        profile_name: str | None,
        model_override: str | None,
    ):
        """Validate Ollama is up and the configured model is available.

        Returns ``(profile, model, model_override, cwd)`` on success, or
        ``None`` if an error event was emitted and the caller must stop.
        Emits [note] + [warmup] status events as a side effect.
        """
        from localsmartz.profiles import get_profile
        from localsmartz.ollama import (
            check_server,
            list_models,
            model_available,
            resolve_available_model,
            warmup_model,
        )

        if not check_server():
            self._send_event({
                "type": "error",
                "message": "Ollama not running \u2192 Start it with: ollama serve",
            })
            return None

        cwd = Path.cwd()

        profile = get_profile(profile_name, model_override=model_override)
        model = profile["planning_model"]
        if not model_available(model):
            chosen, msg = resolve_available_model(model)
            if chosen is None:
                available = ", ".join(list_models()) or "none"
                self._send_event({
                    "type": "error",
                    "message": (
                        f"Model '{model}' not found \u2192 Pull it with: ollama pull {model}\n"
                        f"Available models: {available}"
                    ),
                })
                return None
            # Substitute found — switch to it for this run, surface the warning
            # as an info-style text event so the user sees what happened.
            model_override = chosen
            model = chosen
            if msg:
                self._send_event({"type": "text", "content": f"[note] {msg}\n\n"})

        # Warm the model into Ollama VRAM before we spin up the agent graph.
        # Ollama short-circuits when the model is already resident, so this
        # is cheap on the hot path and eliminates the "~30s silent Thinking…"
        # window on the first query after app launch.
        self._send_event({
            "type": "status",
            "stage": "loading_model",
            "model": model,
        })
        warm_ok, warm_ms, warm_err = warmup_model(model, keep_alive="30m")
        if not warm_ok:
            # Non-fatal: the subsequent stream call will also try to load
            # the model. Surface the warning so the user knows if something
            # is off (e.g. Ollama restarted mid-request).
            self._send_event({
                "type": "text",
                "content": f"[warmup] {warm_err}\n\n",
            })
        self._send_event({
            "type": "status",
            "stage": "ready",
            "model": model,
            "warmup_ms": warm_ms,
        })

        return profile, model, model_override, cwd

    def _run_fast_path(
        self,
        prompt: str,
        profile: dict,
        model: str,
        model_override: str | None,
        thread_id: str | None,
        focus_agent: str | None,
        cwd: Path,
    ) -> None:
        """Fast-path streaming: direct ChatOllama, no DeepAgents graph.

        Owns its heartbeat pulse + OTel span. Exits cleanly on
        BrokenPipeError without draining remaining tokens.
        """
        from localsmartz.agent import fast_path_stream
        from localsmartz.observability import get_tracer
        from localsmartz.threads import append_entry

        start_time = time.time()
        first_text = ""
        pulse = _HeartbeatPulse(self._send_event, interval_s=15.0)
        pulse.start()
        tracer = get_tracer("local-smartz.research")
        fast_path_span_cm = tracer.start_as_current_span("research.fast_path")
        fast_path_span = fast_path_span_cm.__enter__()
        fast_path_span.set_attribute("routing.path", "fast_path")
        fast_path_span.set_attribute("routing.reason", "trivial_prompt")
        fast_path_span.set_attribute("agent.focus", focus_agent or "none")
        fast_path_span.set_attribute("model.name", model)
        try:
            for event in fast_path_stream(prompt, profile, model_override=model_override):
                pulse.touch()
                try:
                    if event.get("type") == "done":
                        # Rewrite thread_id + duration so the client sees our wall time.
                        self._send_event({
                            "type": "done",
                            "duration_ms": int((time.time() - start_time) * 1000),
                            "thread_id": thread_id or "",
                        })
                        continue
                    if event.get("type") == "text":
                        content = event.get("content", "")
                        if isinstance(content, str):
                            first_text += content
                    self._send_event(event)
                except (BrokenPipeError, ConnectionResetError):
                    # Client disconnected — stop pulling tokens immediately.
                    break
        finally:
            pulse.stop()
            fast_path_span_cm.__exit__(None, None, None)

        # Best-effort thread append — mirrors the full-path behavior.
        if thread_id:
            try:
                append_entry(
                    thread_id=thread_id,
                    cwd=str(cwd),
                    query=prompt,
                    summary=first_text[:500],
                    artifacts=[],
                    turns=1,
                )
            except Exception:
                pass

    def _run_full_agent(
        self,
        prompt: str,
        profile_name: str | None,
        model: str,
        model_override: str | None,
        thread_id: str | None,
        focus_agent: str | None,
        cwd: Path,
    ) -> None:
        """Full DeepAgents graph streaming.

        Owns its heartbeat pulse + OTel span, drift + loop detectors,
        turn-count enforcement, tool-name validator, and MCP client
        cleanup. Exits cleanly on BrokenPipeError and skips the
        post-stream ``invoke`` + ``done`` event when the client is gone.
        """
        from localsmartz.agent import create_agent, extract_final_response
        from localsmartz.observability import get_tracer
        from localsmartz.threads import append_entry
        from localsmartz.validation import LoopDetector
        from localsmartz.drift import create_drift_detector

        # Focus mode: create_agent now scopes the main agent's tools + swaps
        # system prompt (see agent.create_agent focus-mode branch). The
        # banner stays — it's what the user sees in the UI.
        if focus_agent:
            self._send_event({"type": "text", "content": f"[focus] running as `{focus_agent}` agent only\n\n"})

        agent, profile, checkpointer, mcp_clients = create_agent(
            profile_name=profile_name,
            thread_id=thread_id,
            cwd=cwd,
            model_override=model_override,
            focus_agent=focus_agent,
        )

        config = {"configurable": {"thread_id": thread_id or "default"}}
        input_msg = {"messages": [{"role": "user", "content": prompt}]}

        start_time = time.time()
        tools_used: set[str] = set()

        # Lite profile: loop detection and turn limits
        is_lite = profile["name"] == "lite"
        max_turns = profile.get("max_turns", 20)
        loop_detector = LoopDetector(max_repeats=3)
        drift_detector = create_drift_detector(profile)
        turn_count = 0
        loop_broken = False
        client_disconnected = False

        # Build the set of valid tool names exposed to the model so we can
        # reject hallucinated namespaced ones (e.g. ``repo_browser.write_todos``)
        # surfaced by small Ollama models.
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

        # Start the heartbeat pulse. The agent.stream loop blocks during tool
        # execution and model reasoning; without this the client sees long
        # silent gaps.
        pulse = _HeartbeatPulse(self._send_event, interval_s=15.0)
        pulse.start()

        # OTel span covering the whole full-path agent run so Phoenix can
        # distinguish it from the fast-path and show which agent (if any)
        # was pinned.
        tracer = get_tracer("local-smartz.research")
        full_span_cm = tracer.start_as_current_span("research.full_agent")
        full_span = full_span_cm.__enter__()
        full_span.set_attribute("routing.path", "full_agent")
        full_span.set_attribute("agent.focus", focus_agent or "none")
        full_span.set_attribute("model.name", model)
        full_span.set_attribute("profile.name", profile.get("name", "unknown"))

        try:
            # Multi-mode stream: "updates" emits node-boundary state (tool calls
            # + tool results), "messages" emits per-token deltas from the LLM.
            # Without messages mode, the UI shows "Thinking…" until the AI
            # message completes — a 10–60 s silent gap on long answers. With
            # both, users see tokens as they're produced (fixes AP-4 in the
            # deepagents skill's anti-patterns doc).
            #
            # To avoid double-emission we only emit AI text content from the
            # messages branch; the updates branch handles tool events only.
            for mode, payload in agent.stream(
                input_msg, config=config, stream_mode=["updates", "messages"]
            ):
                pulse.touch()

                if mode == "messages":
                    # payload is (AIMessageChunk, metadata_dict) from LangGraph.
                    # Only emit text deltas from AI chunks; tool-call deltas are
                    # handled by the updates branch at the node boundary.
                    try:
                        msg_chunk, _meta = payload  # type: ignore[misc]
                    except (TypeError, ValueError):
                        continue
                    # Skip non-AI chunks (tool output messages flow through
                    # updates; re-emitting here would duplicate).
                    if getattr(msg_chunk, "type", None) not in ("ai", "AIMessageChunk"):
                        # AIMessageChunk reports type via .type attribute which
                        # may be "AIMessageChunk" depending on LangChain version.
                        type_attr = getattr(msg_chunk, "__class__", type(msg_chunk)).__name__
                        if type_attr != "AIMessageChunk":
                            continue
                    content = getattr(msg_chunk, "content", None)
                    if isinstance(content, str) and content:
                        self._send_event({"type": "text", "content": content})
                    elif isinstance(content, list):
                        # Some adapters emit content as a list of segments.
                        for seg in content:
                            text = seg.get("text") if isinstance(seg, dict) else None
                            if isinstance(text, str) and text:
                                self._send_event({"type": "text", "content": text})
                    continue

                # mode == "updates"
                chunk = payload
                if not isinstance(chunk, dict):
                    continue
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
                                # Guard against hallucinated namespace prefixes
                                # (``repo_browser.write_todos`` etc).
                                if not _is_valid_tool_name(
                                    name, valid_names=_valid_tool_names or None
                                ):
                                    self._send_event({
                                        "type": "tool_error",
                                        "name": name,
                                        "message": (
                                            f"Invalid tool name '{name}' — "
                                            "tool names do not include dots or slashes. "
                                            "The model likely hallucinated a namespace."
                                        ),
                                    })
                                    continue
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

                        # NOTE: AI text content is no longer emitted here —
                        # the "messages" branch above streams tokens as they
                        # arrive. Re-enabling this would cause the full reply
                        # to be appended a second time after the token stream.

                # Enforce turn limit and loop break
                if turn_count >= max_turns or loop_broken:
                    if turn_count >= max_turns:
                        self._send_event({
                            "type": "tool_error",
                            "name": "system",
                            "message": f"Turn limit ({max_turns}) reached. Returning partial results.",
                        })
                    break
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected mid-stream. Flag it so the post-stream
            # invoke()/done-event writes are skipped, but still run
            # pulse.stop() + span close + mcp cleanup below.
            client_disconnected = True
        finally:
            pulse.stop()
            full_span.set_attribute("turn_count", turn_count)
            full_span.set_attribute("tools_used", ",".join(sorted(tools_used)))
            full_span_cm.__exit__(None, None, None)

        try:
            if not client_disconnected:
                # Get final result
                full_result = agent.invoke(None, config=config)
                response = extract_final_response(full_result) if full_result else "No response generated."

                # Log to thread BEFORE sending done event (prevents race condition)
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

                duration_ms = int((time.time() - start_time) * 1000)
                self._send_event({"type": "done", "duration_ms": duration_ms, "thread_id": thread_id or ""})
        finally:
            if mcp_clients:
                from localsmartz.plugins.agent_integration import close_mcp_clients
                close_mcp_clients(mcp_clients)

    def _stream_research(
        self,
        prompt: str,
        profile_name: str | None,
        thread_id: str | None,
        focus_agent: str | None = None,
    ):
        """Run research agent and emit SSE events.

        Orchestrates three phases:
        1. ``_preflight_model`` — Ollama + model availability + warmup.
        2. ``_run_fast_path`` — trivial prompts, direct ChatOllama stream.
        3. ``_run_full_agent`` — DeepAgents graph with multi-mode streaming.
        """
        from localsmartz.profiles import is_fast_path
        from localsmartz.threads import create_thread

        model_override = LocalSmartzHandler._model_override or _saved_model_override(Path.cwd())

        preflight = self._preflight_model(profile_name, model_override)
        if preflight is None:
            return
        profile, model, model_override, cwd = preflight

        # Ensure storage
        storage = cwd / ".localsmartz"
        for subdir in ["threads", "artifacts", "memory", "scripts", "reports"]:
            (storage / subdir).mkdir(parents=True, exist_ok=True)

        # Create thread if specified
        if thread_id:
            create_thread(thread_id, str(cwd), title=prompt[:60])

        # ── Fast path ─────────────────────────────────────────────────────
        # Trivial prompts (short, no research verbs, <=2 sentences) skip the
        # DeepAgents graph entirely — one direct ChatOllama stream.
        # Also taken when the user pinned the Planner agent for a trivial
        # prompt: Planner's whole job is decomposition, and forcing a small
        # model to emit write_todos for a one-liner triggers tool-call
        # hallucinations like `repo_browser.write_todos`. Answer directly.
        allow_fast_path = focus_agent is None or focus_agent == "planner"
        if allow_fast_path and is_fast_path(prompt):
            self._run_fast_path(
                prompt=prompt,
                profile=profile,
                model=model,
                model_override=model_override,
                thread_id=thread_id,
                focus_agent=focus_agent,
                cwd=cwd,
            )
            return

        self._run_full_agent(
            prompt=prompt,
            profile_name=profile_name,
            model=model,
            model_override=model_override,
            thread_id=thread_id,
            focus_agent=focus_agent,
            cwd=cwd,
        )

    def _handle_models(self):
        """Return available Ollama models with current selection."""
        from localsmartz.ollama import list_models_with_size
        from localsmartz.profiles import get_profile
        from localsmartz.config import load_config

        models = [{"name": n, "size_gb": round(s, 1)} for n, s in list_models_with_size()]
        cwd = Path.cwd()
        config = load_config(cwd) or {}
        current = LocalSmartzHandler._model_override or config.get("planning_model", "")
        profile = get_profile(self._default_profile, model_override=current or None)
        self._json_response({
            "models": models,
            "current": current or (profile["planning_model"] if profile else ""),
            "profile": profile["name"] if profile else "unknown",
        })

    @_json_body
    def _handle_model_select(self, *, body):
        """Switch the active model.

        Frees the old model from Ollama VRAM (``evict_model``) before
        kicking off a background warmup for the new one with
        ``keep_alive="-1"`` so it stays resident. The UI polls
        ``/api/models/warmup`` to block input until the new model is ready.
        """
        from localsmartz.ollama import evict_model, model_available
        from localsmartz.config import load_config, save_config
        from localsmartz.profiles import get_profile

        if not body.get("model"):
            self._json_response({"error": "No model specified"}, 400)
            return
        model = body["model"]
        if not model_available(model):
            self._json_response({"error": f"Model '{model}' not available in Ollama"}, 400)
            return
        cwd = Path.cwd()

        # Determine the currently-resident model so we can evict before
        # pinning the new one. Priority: in-memory override > saved config.
        old_model = LocalSmartzHandler._model_override
        if not old_model:
            saved = load_config(cwd) or {}
            old_model = saved.get("planning_model")

        save_config(cwd, {"planning_model": model})
        LocalSmartzHandler._model_override = model

        # Best-effort evict. No-op when old == new (model_select on same
        # model is a cheap refresh) and silent on failure — the swap
        # succeeded even if VRAM reclaim didn't.
        if old_model and old_model != model:
            evict_model(old_model)

        # Pin the new model with keep_alive=-1 so it survives idle
        # stretches — the UI stays responsive without waiting on a cold
        # reload when the user comes back minutes later. Background thread
        # so the POST returns immediately and the UI can show the loading
        # overlay via warmup polling.
        _warmup_in_background(model, keep_alive="-1")

        profile = get_profile(self._default_profile, model_override=model)
        self._json_response({
            "ok": True,
            "model": model,
            "profile": profile["name"],
            "previous_model": old_model or "",
        })

    def _handle_models_catalog(self):
        """Return the curated catalog with installed/not-installed flags.

        Installed models NOT in the catalog are appended as extra rows so the
        user sees everything they have.
        """
        from localsmartz.ollama import (
            SUGGESTED_MODELS,
            list_models_with_size,
            model_available,
        )
        from localsmartz.config import load_config
        from localsmartz.profiles import get_profile

        installed = {name: size for name, size in list_models_with_size()}
        cwd = Path.cwd()
        config = load_config(cwd) or {}
        current = LocalSmartzHandler._model_override or config.get("planning_model", "")
        profile = get_profile(self._default_profile, model_override=current or None)

        rows: list[dict] = []
        catalog_names: set[str] = set()
        for item in SUGGESTED_MODELS:
            name = item["name"]
            catalog_names.add(name)
            is_installed = model_available(name)
            actual_size = None
            if name in installed:
                actual_size = round(installed[name], 1)
            rows.append({
                **item,
                "installed": is_installed,
                "installed_size_gb": actual_size,
                "current": name == current,
            })
        # Append extras (user has it pulled but it's not in the curated list)
        for name, size in installed.items():
            if name not in catalog_names:
                rows.append({
                    "name": name,
                    "size_gb_estimate": round(size, 1),
                    "ram_class": "custom",
                    "note": "Pulled manually",
                    "installed": True,
                    "installed_size_gb": round(size, 1),
                    "current": name == current,
                })
        self._json_response({
            "catalog": rows,
            "current": current or (profile["planning_model"] if profile else ""),
            "profile": profile["name"] if profile else "unknown",
        })

    @_json_body
    def _handle_warmup_start(self, *, body):
        """POST /api/models/warmup {"model": "...", "keep_alive"?: "30m"}.

        Preloads a model into Ollama VRAM. Fires a background warmup so the
        UI can show a loading screen and poll GET /api/models/warmup.
        Idempotent — if the model is already resident, the background call
        returns fast and state flips to 'ready'.
        """
        model = body.get("model", "").strip() if isinstance(body.get("model"), str) else ""
        if not model:
            self._json_response({"error": "No model specified"}, 400)
            return

        keep_alive = body.get("keep_alive")
        if not isinstance(keep_alive, str) or not keep_alive.strip():
            keep_alive = "30m"

        _warmup_in_background(model, keep_alive=keep_alive)
        with _WARMUP_STATE_LOCK:
            state = dict(_WARMUP_STATE.get(model, {"stage": "loading"}))
        self._json_response({"model": model, **state})

    def _handle_warmup_status(self):
        """GET /api/models/warmup?model=... — return current warmup state.

        Omit `model` to get the full map.
        """
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        model = (qs.get("model") or [""])[0].strip()
        with _WARMUP_STATE_LOCK:
            if model:
                state = dict(_WARMUP_STATE.get(model, {"stage": "idle"}))
                self._json_response({"model": model, **state})
                return
            snapshot = {k: dict(v) for k, v in _WARMUP_STATE.items()}
        self._json_response({"warmup": snapshot})

    @_json_body
    def _handle_model_pull(self, *, body):
        """Stream SSE progress for `ollama pull <model>`."""
        model = body.get("model", "").strip() if isinstance(body.get("model"), str) else ""
        if not model:
            self._json_response({"error": "No model specified"}, 400)
            return

        self._start_sse()
        self._send_event({"type": "step", "message": f"Pulling {model}..."})
        try:
            proc = subprocess.Popen(
                ["ollama", "pull", model],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            self._send_event({"type": "error", "message": "`ollama` not on PATH"})
            return
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                self._send_event({"type": "progress", "model": model, "line": line})
            proc.wait()
            if proc.returncode == 0:
                self._send_event({"type": "done", "model": model})
            else:
                self._send_event({
                    "type": "error",
                    "message": f"ollama pull exited {proc.returncode}",
                })
        except (BrokenPipeError, ConnectionResetError):
            proc.terminate()

    def _handle_model_delete(self):
        """DELETE /api/models?name=<model> — remove a model from Ollama."""
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        model = (qs.get("name") or [""])[0].strip()
        if not model:
            self._json_response({"error": "No model specified (use ?name=...)"}, 400)
            return
        try:
            result = subprocess.run(
                ["ollama", "rm", model],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            self._json_response({"error": "`ollama` not on PATH"}, 500)
            return
        except subprocess.TimeoutExpired:
            self._json_response({"error": "`ollama rm` timed out"}, 504)
            return
        if result.returncode != 0:
            self._json_response({
                "error": f"ollama rm failed: {result.stderr.strip() or result.stdout.strip()}"
            }, 500)
            return
        self._json_response({"ok": True, "removed": model})

    # ── Plugin/skill authoring ───────────────────────────────────────────

    def _llm_complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        """Call the active local LLM with a single user turn.
        Used by the authoring endpoints — synchronous, returns text only."""
        from langchain_ollama import ChatOllama
        from localsmartz.profiles import get_profile
        from localsmartz.config import load_config

        cwd = Path.cwd()
        config = load_config(cwd) or {}
        model = LocalSmartzHandler._model_override or config.get("planning_model")
        if not model:
            profile = get_profile(self._default_profile)
            model = profile["planning_model"]

        from localsmartz.ollama import resolve_available_model
        chosen, _ = resolve_available_model(model)
        if chosen:
            model = chosen

        llm = ChatOllama(model=model, temperature=0, num_ctx=8192, num_predict=max_tokens)
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        resp = llm.invoke(msgs)
        return getattr(resp, "content", str(resp))

    _SKILL_REFACTOR_SYSTEM = """You are a skill refactoring assistant. You receive an existing SKILL.md (with YAML frontmatter `name` and `description`) plus authoring guidance (often AGENTS.md / CLAUDE.md style notes). Produce a single replacement SKILL.md.

Rules:
- KEEP the existing `name` field unchanged unless the guidance explicitly renames the skill.
- Update `description` to reflect any new triggering criteria.
- Body: rewrite to be clear, actionable, sectioned. Keep useful original content.
- Return ONLY the SKILL.md file content (frontmatter + body). No prose, no code fences, no explanation."""

    _SKILL_NEW_SYSTEM = """You are a skill authoring assistant. Given a name and a free-text description, produce a SKILL.md file with valid YAML frontmatter and a useful markdown body.

Frontmatter required:
  name: <kebab-case-name>
  description: Use when ... (80-200 chars, lists trigger phrases)

Body sections to include:
  ## When to activate
  ## What it does
  ## Notes / pitfalls

Return ONLY the SKILL.md file content. No prose, no code fences, no explanation."""

    _PLUGIN_JSON_SYSTEM = """You are generating a plugin.json file for a local-smartz plugin. Given a plugin name and one-line description, return JSON with these fields:
  { "name": "<kebab>", "version": "0.1.0", "description": "<>", "author": {"name": "Local Smartz user"} }
Return ONLY the JSON, no prose, no code fences."""

    @_json_body
    def _handle_skill_refactor(self, *, body):
        skill_name = (body.get("name") or "").strip()
        guidance = (body.get("guidance") or "").strip()
        if not skill_name or not guidance:
            self._json_response({"error": "name and guidance are required"}, 400)
            return

        from localsmartz.plugins import Registry
        reg = Registry.from_default_root()
        match = next((s for s in reg.list_skills() if s.name == skill_name), None)
        if match is None:
            self._json_response({"error": f"Skill not found: {skill_name}"}, 404)
            return

        existing = match.source_path.read_text(errors="ignore") if match.source_path.exists() else ""
        user = (
            f"Existing SKILL.md:\n\n```\n{existing}\n```\n\n"
            f"Authoring guidance:\n\n{guidance}\n\n"
            "Produce the full replacement SKILL.md."
        )
        try:
            content = self._llm_complete(self._SKILL_REFACTOR_SYSTEM, user, max_tokens=3072)
        except Exception as e:  # noqa: BLE001
            self._json_response({"error": f"LLM call failed: {e}"}, 500)
            return
        self._json_response({
            "name": skill_name,
            "original": existing,
            "proposed": content.strip(),
        })

    @_json_body
    def _handle_skill_new(self, *, body):
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip()
        if not name or not description:
            self._json_response({"error": "name and description are required"}, 400)
            return

        try:
            skill_md = self._llm_complete(
                self._SKILL_NEW_SYSTEM,
                f"name: {name}\n\ndescription / context:\n{description}",
                max_tokens=2048,
            )
            plugin_json = self._llm_complete(
                self._PLUGIN_JSON_SYSTEM,
                f"name: {name}\ndescription: {description[:200]}",
                max_tokens=512,
            )
        except Exception as e:  # noqa: BLE001
            self._json_response({"error": f"LLM call failed: {e}"}, 500)
            return
        self._json_response({
            "name": name,
            "skill_md": skill_md.strip(),
            "plugin_json": plugin_json.strip(),
        })

    @_json_body
    def _handle_plugin_save(self, *, body):
        target = body.get("target_path")
        plugin_name = (body.get("plugin_name") or "").strip()
        files = body.get("files") or {}
        if not target or not plugin_name or not isinstance(files, dict):
            self._json_response({"error": "target_path, plugin_name, files required"}, 400)
            return

        target_path = Path(target).expanduser().resolve()
        if not target_path.is_dir():
            self._json_response({"error": f"target_path is not a directory: {target_path}"}, 400)
            return

        plugin_dir = target_path / plugin_name
        if plugin_dir.exists():
            self._json_response({
                "error": f"Plugin already exists at {plugin_dir} — pick a different name or remove it first"
            }, 409)
            return

        try:
            plugin_dir.mkdir(parents=True)
            # Standard layout
            (plugin_dir / ".claude-plugin").mkdir()
            skills_dir = plugin_dir / "skills" / plugin_name
            skills_dir.mkdir(parents=True)

            for rel_path, content in files.items():
                if not isinstance(content, str):
                    continue
                # Whitelist of allowed targets to keep things sane
                if rel_path == "plugin.json":
                    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(content)
                elif rel_path == "SKILL.md":
                    (skills_dir / "SKILL.md").write_text(content)
                elif rel_path in ("CLAUDE.md", "AGENTS.md", "README.md"):
                    (plugin_dir / rel_path).write_text(content)
        except OSError as e:
            self._json_response({"error": f"Filesystem error: {e}"}, 500)
            return

        self._json_response({
            "ok": True,
            "plugin_name": plugin_name,
            "plugin_dir": str(plugin_dir),
        })

    def _handle_agents(self):
        """Surface the conceptual agents in the active profile.

        Agents are returned in the new dict shape with a ``model`` field —
        the effective per-agent model (profile default merged with any
        per-user override from global_config["agent_models"]). Fields
        ``name``, ``title``, ``summary`` are kept for backward compatibility.
        """
        from localsmartz.profiles import get_profile, list_agents

        profile = get_profile(self._default_profile)
        self._json_response({
            "profile": profile["name"],
            "agents": list_agents(profile),
        })

    def _handle_agents_models(self):
        """Return the effective per-agent model map for the active profile."""
        from localsmartz.profiles import effective_agent_models, get_profile

        profile = get_profile(self._default_profile)
        self._json_response({
            "profile": profile["name"],
            "models": effective_agent_models(profile),
        })

    @_json_body
    def _handle_agent_model_set(self, agent_name: str, *, body):
        """POST /api/agents/<name>/model body {"model": "..."} — persist override."""
        from localsmartz import global_config
        from localsmartz.profiles import get_profile

        agent_name = (agent_name or "").strip()
        if not agent_name:
            self._json_response({"error": "agent name required"}, 400)
            return

        profile = get_profile(self._default_profile)
        agents = profile.get("agents", {})
        if not isinstance(agents, dict) or agent_name not in agents:
            self._json_response({
                "error": f"Unknown agent '{agent_name}' for profile '{profile['name']}'",
            }, 404)
            return

        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            self._json_response({"error": "Request body must include string 'model'"}, 400)
            return
        model = model.strip()

        # Read-modify-write the per-agent overrides dict.
        current = global_config.get("agent_models")
        if not isinstance(current, dict):
            current = {}
        current = dict(current)
        current[agent_name] = model
        try:
            global_config.set("agent_models", current)
        except ValueError as exc:
            self._json_response({"error": f"Invalid value: {exc}"}, 400)
            return
        self._json_response({"ok": True, "agent": agent_name, "model": model})

    def _handle_list_skills(self):
        """Return installed skills (name + description + plugin)."""
        from localsmartz.plugins import Registry
        try:
            reg = Registry.from_default_root()
            skills = reg.list_skills()
        except Exception as e:  # noqa: BLE001
            self._json_response({"error": str(e)}, 500)
            return
        rows = [
            {
                "name": s.name,
                "description": s.description,
                "plugin": s.plugin_name or "",
                "source_path": str(s.source_path),
            }
            for s in skills
        ]
        # Use a list (not dict) so the Swift client can decode directly into [Skill].
        body = json.dumps(rows).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _handle_observability_info(self):
        from localsmartz import observability
        self._json_response(observability.status())

    def _handle_ollama_info(self):
        """Surface Ollama's model storage path + disk usage."""
        from localsmartz.ollama import (
            check_server,
            get_version,
            list_models_with_size,
            ollama_disk_usage_bytes,
            ollama_models_path,
        )
        path = ollama_models_path()
        models = list_models_with_size()
        self._json_response({
            "running": check_server(),
            "version": get_version(),
            "models_path": str(path),
            "path_exists": path.exists(),
            "source": "OLLAMA_MODELS" if os.environ.get("OLLAMA_MODELS") else "default",
            "model_count": len(models),
            "total_size_bytes": ollama_disk_usage_bytes(),
        })

    def _handle_folders(self):
        """Return workspace and configured research folders."""
        from localsmartz.config import get_folders
        cwd = Path.cwd()
        self._json_response({
            "workspace": str(cwd),
            "folders": get_folders(cwd),
        })

    @_json_body
    def _handle_folder_add(self, *, body):
        """Add a research folder."""
        from localsmartz.config import add_folder
        if not body.get("path"):
            self._json_response({"error": "No path specified"}, 400)
            return
        folder = Path(body["path"]).expanduser()
        if not folder.is_dir():
            self._json_response({"error": f"Path is not a directory: {body['path']}"}, 400)
            return
        cwd = Path.cwd()
        folders = add_folder(cwd, body["path"])
        self._json_response({"ok": True, "folders": folders})

    def _handle_folder_delete(self):
        """Remove a research folder."""
        from localsmartz.config import remove_folder
        try:
            body = self._read_json_body()
        except ValueError:
            self._json_response({"error": "Invalid request body"}, 400)
            return
        if not body.get("path"):
            self._json_response({"error": "No path specified"}, 400)
            return
        cwd = Path.cwd()
        folders = remove_folder(cwd, body["path"])
        self._json_response({"ok": True, "folders": folders})

    def _handle_setup(self):
        """Stream model setup progress as SSE events."""
        from localsmartz.profiles import get_profile
        from localsmartz.ollama import (
            check_server, is_installed, model_available, pull_model,
        )

        # Parse optional model parameter — empty body is valid
        target_model = None
        content_length = self.headers.get("Content-Length", "0")
        try:
            length = int(content_length)
        except ValueError:
            length = 0
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if isinstance(body, dict):
                    target_model = body.get("model")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

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
            if target_model:
                models = [target_model]
            else:
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

    # ── Secrets ──

    def _handle_secrets_list(self):
        from localsmartz import secrets as _secrets
        try:
            self._json_response(_secrets.masked_all())
        except Exception as e:  # noqa: BLE001
            self._json_response({"error": str(e)}, 500)

    @_json_body
    def _handle_secrets_set(self, *, body):
        from localsmartz import secrets as _secrets
        from localsmartz import log_buffer
        provider = body.get("provider")
        value = body.get("value")
        if not provider or not isinstance(provider, str):
            self._json_response({"error": "provider is required"}, 400)
            return
        if not value or not isinstance(value, str):
            self._json_response({"error": "value is required"}, 400)
            return
        try:
            source = _secrets.set(provider, value)
        except Exception as e:  # noqa: BLE001
            self._json_response({"error": str(e)}, 500)
            return
        env_name = _secrets.PRESET_BY_NAME.get(provider)
        if env_name:
            os.environ[env_name] = value
        log_buffer.info("secrets", f"set {provider} ({source})")
        self._json_response({"ok": True, "source": source})

    def _handle_secrets_delete(self, parsed):
        from localsmartz import secrets as _secrets
        from localsmartz import log_buffer
        query = parse_qs(parsed.query)
        provider_vals = query.get("provider", [])
        provider = provider_vals[0] if provider_vals else None
        if not provider:
            self._json_response({"error": "provider is required"}, 400)
            return
        try:
            _secrets.delete(provider)
        except Exception as e:  # noqa: BLE001
            self._json_response({"error": str(e)}, 500)
            return
        env_name = _secrets.PRESET_BY_NAME.get(provider)
        if env_name:
            os.environ.pop(env_name, None)
        log_buffer.info("secrets", f"deleted {provider}")
        self._json_response({"ok": True})

    # ── Logs ──

    def _handle_logs_list(self, parsed):
        from localsmartz import log_buffer
        query = parse_qs(parsed.query)
        since_raw = query.get("since", ["0"])[0]
        try:
            since = int(since_raw)
        except (TypeError, ValueError):
            since = 0
        entries = log_buffer.since(since)
        body = json.dumps(entries).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _handle_logs_clear(self):
        from localsmartz import log_buffer
        log_buffer.clear()
        self._json_response({"ok": True})

    # ── Issue report ──

    @_json_body
    def _handle_issues_report(self, *, body):
        from localsmartz import log_buffer
        title = (body.get("title") or "").strip()
        description = (body.get("description") or "").strip()
        include_logs = bool(body.get("include_logs"))
        if not title:
            self._json_response({"error": "title is required"}, 400)
            return

        ts = int(time.time())
        slug_chars = []
        for ch in title.lower():
            if ch.isalnum():
                slug_chars.append(ch)
            elif ch in (" ", "-", "_"):
                slug_chars.append("-")
        slug = "".join(slug_chars).strip("-")[:40] or "issue"
        issues_dir = Path.home() / ".localsmartz" / "issues"
        try:
            issues_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._json_response({"error": f"failed to create issues dir: {e}"}, 500)
            return
        path = issues_dir / f"{ts}_{slug}.md"

        lines = [
            f"# {title}",
            "",
            f"**Timestamp:** {_iso_timestamp(ts)}",
            f"**Unix:** {ts}",
            "",
            "## Description",
            "",
            description or "_(no description provided)_",
            "",
        ]
        if include_logs:
            snap = log_buffer.snapshot()
            lines.append("## Logs")
            lines.append("")
            lines.append("```")
            for entry in snap:
                lines.append(
                    f"[{entry.get('seq')}] {_iso_timestamp(entry.get('ts'))} "
                    f"{entry.get('level','').upper():<5} "
                    f"{entry.get('source','')}: {entry.get('message','')}"
                )
            lines.append("```")
            lines.append("")

        try:
            path.write_text("\n".join(lines), encoding="utf-8")
        except OSError as e:
            self._json_response({"error": f"failed to write issue: {e}"}, 500)
            return
        self._json_response({"ok": True, "path": str(path)})


def _install_crash_diagnostics() -> None:
    """Make silent child-process deaths impossible.

    Three problems we address here, in order of severity:

    1. **SIGPIPE=SIG_DFL kills the process.** Python inherits the default
       SIGPIPE handler on macOS which *terminates* the process when a write
       lands on a closed pipe. SSE clients (the Swift app) routinely close
       connections mid-stream — on cancel, on window close, on navigation.
       Every one of those writes was a chance to silently kill the backend.
       We ignore the signal so writes just raise ``BrokenPipeError`` (which
       ``_send_event`` already catches and re-raises cleanly).

    2. **Unhandled exceptions in daemon threads are invisible.** Our
       warmup + heartbeat workers run as ``daemon=True``. If one raises,
       Python prints to ``sys.stderr`` and the thread dies — but because
       the process stays alive, no one notices until a later request
       deadlocks on state that worker was supposed to update. Install a
       ``threading.excepthook`` that routes to our ``log_buffer`` so the
       trace shows up in `/api/logs` and in the spawned-process log file.

    3. **Top-level crashes must flush before the process exits.** Install a
       ``sys.excepthook`` that logs and flushes before the default hook runs.
    """
    from localsmartz import log_buffer as _log_buffer

    try:
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    except (AttributeError, ValueError):
        # Not POSIX or called from non-main thread — harmless.
        pass

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        # Mirror stderr output so it lands in the app's spawned log file
        # in addition to the in-memory log buffer.
        msg = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        try:
            _log_buffer.error("thread", f"[{args.thread.name if args.thread else 'unknown'}] {msg}")
        except Exception:
            pass
        print(f"[thread {args.thread.name if args.thread else '?'}] {msg}", file=sys.stderr, flush=True)

    threading.excepthook = _thread_hook

    _prev_hook = sys.excepthook

    def _sys_hook(exc_type, exc, tb) -> None:
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            _log_buffer.error("unhandled", msg)
        except Exception:
            pass
        print(f"[unhandled] {msg}", file=sys.stderr, flush=True)
        _prev_hook(exc_type, exc, tb)

    sys.excepthook = _sys_hook


def start_server(port: int = 11435, profile_name: str | None = None):
    """Start the Local Smartz HTTP server.

    Args:
        port: Port to listen on
        profile_name: Profile override ("full" or "lite"). Auto-detect if None.
    """
    _install_crash_diagnostics()

    # Store profile_name on handler class so all requests use consistent profile
    LocalSmartzHandler._default_profile = profile_name
    from localsmartz import __version__ as _version
    from localsmartz import log_buffer as _log_buffer
    _log_buffer.info("startup", f"local-smartz {_version} starting on port {port}")

    # Preload the default planning model into Ollama VRAM as soon as the
    # server starts, so the first user query doesn't pay the 10–60s cold
    # load cost silently. The Swift app polls /api/models/warmup and keeps
    # the query input disabled until stage == "ready".
    try:
        from localsmartz.config import load_config
        from localsmartz.ollama import check_server
        from localsmartz.profiles import get_profile

        _cwd = Path.cwd()
        _config = load_config(_cwd) or {}
        _saved = _config.get("planning_model")
        _profile = get_profile(profile_name, model_override=_saved or None)
        _boot_model = _profile.get("planning_model") if isinstance(_profile, dict) else None
        if _boot_model and check_server():
            print(f"  Warming {_boot_model} (background, keep_alive=-1)...", file=sys.stderr)
            # Pin the active planning model resident indefinitely. Idle-time
            # eviction is handled explicitly at model-switch — see
            # ``_handle_model_select`` which calls evict_model on the old
            # model before warming the new one.
            _warmup_in_background(_boot_model, keep_alive="-1")
    except Exception as exc:  # noqa: BLE001 — don't let warmup crash startup
        _log_buffer.info("startup", f"boot warmup skipped: {exc}")

    server = ThreadingHTTPServer(("127.0.0.1", port), LocalSmartzHandler)
    server.daemon_threads = True
    print(f"\n  Local Smartz running at http://localhost:{port}", file=sys.stderr, flush=True)
    print(f"  Press Ctrl+C to stop.\n", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr, flush=True)
        server.shutdown()
    except Exception as exc:  # noqa: BLE001 — want the trace in the log
        tb = traceback.format_exc()
        try:
            _log_buffer.error("fatal", f"serve_forever crashed: {exc}\n{tb}")
        except Exception:
            pass
        print(f"[fatal] serve_forever crashed: {exc}\n{tb}", file=sys.stderr, flush=True)
        raise
