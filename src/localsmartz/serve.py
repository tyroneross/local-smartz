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
  --bg: #0c0c0c; --surface: #161616; --surface-raised: #1c1c1c;
  --border: rgba(255,255,255,0.06); --border-hover: rgba(255,255,255,0.12);
  --fg: #e4e4e4; --fg-secondary: rgba(228,228,228,0.6); --fg-muted: rgba(228,228,228,0.3);
  --teal: #0ea5e9; --teal-dim: rgba(14,165,233,0.15); --teal-glow: rgba(14,165,233,0.25);
  --amber: #f59e0b; --amber-dim: rgba(245,158,11,0.15);
  --green: #22c55e; --green-dim: rgba(34,197,94,0.2);
  --red: #ef4444;
  --radius: 10px;
}
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
    } catch(e) {
      $('status-bar').innerHTML = '<span class="status-dot" style="background:var(--red)"></span> Offline';
    }
  }

  fetchStatus(); fetchModels(); fetchFolders(); fetchThreads();
  setInterval(fetchStatus, 30000);
}();
</script>
</body>
</html>"""


class LocalSmartzHandler(BaseHTTPRequestHandler):
    """HTTP request handler with SSE support."""

    # Set by start_server() — profile override from CLI (None = auto-detect)
    _default_profile: str | None = None
    _model_override: str | None = None

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
        elif path == "/api/folders":
            self._handle_folders()
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
        elif path == "/api/folders":
            self._handle_folder_add()
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
        model_override = LocalSmartzHandler._model_override or _saved_model_override(cwd)

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

    def _handle_model_select(self):
        """Switch the active model."""
        from localsmartz.ollama import model_available
        from localsmartz.config import save_config
        from localsmartz.profiles import get_profile

        try:
            body = self._read_json_body()
        except ValueError:
            self._json_response({"error": "Invalid request body"}, 400)
            return
        if not body.get("model"):
            self._json_response({"error": "No model specified"}, 400)
            return
        model = body["model"]
        if not model_available(model):
            self._json_response({"error": f"Model '{model}' not available in Ollama"}, 400)
            return
        cwd = Path.cwd()
        save_config(cwd, {"planning_model": model})
        LocalSmartzHandler._model_override = model
        profile = get_profile(self._default_profile, model_override=model)
        self._json_response({"ok": True, "model": model, "profile": profile["name"]})

    def _handle_folders(self):
        """Return workspace and configured research folders."""
        from localsmartz.config import get_folders
        cwd = Path.cwd()
        self._json_response({
            "workspace": str(cwd),
            "folders": get_folders(cwd),
        })

    def _handle_folder_add(self):
        """Add a research folder."""
        from localsmartz.config import add_folder
        try:
            body = self._read_json_body()
        except ValueError:
            self._json_response({"error": "Invalid request body"}, 400)
            return
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
