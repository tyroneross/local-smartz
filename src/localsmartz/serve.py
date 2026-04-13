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
import os
import subprocess
import sys
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
        elif path == "/api/models/catalog":
            self._handle_models_catalog()
        elif path == "/api/agents":
            self._handle_agents()
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
        agent = payload.get("agent")  # Optional: pin to a single agent
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

    def _stream_research(
        self,
        prompt: str,
        profile_name: str | None,
        thread_id: str | None,
        focus_agent: str | None = None,
    ):
        """Run research agent and emit SSE events."""
        from localsmartz.agent import create_agent, extract_final_response
        from localsmartz.profiles import get_profile, agent_focus_prompt
        from localsmartz.ollama import (
            check_server, model_available, list_models, resolve_available_model,
        )
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

        # Preflight: required model must be available — fall back to the largest
        # installed substitute if the configured model isn't pulled. Mirrors the
        # behavior of the CLI _preflight() and /api/status. If no substitute
        # exists either, surface the original error.
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
                return
            # Substitute found — switch to it for this run, surface the warning
            # as an info-style text event so the user sees what happened.
            model_override = chosen
            if msg:
                self._send_event({"type": "text", "content": f"[note] {msg}\n\n"})

        # Ensure storage
        storage = cwd / ".localsmartz"
        for subdir in ["threads", "artifacts", "memory", "scripts", "reports"]:
            (storage / subdir).mkdir(parents=True, exist_ok=True)

        # Create thread if specified
        if thread_id:
            create_thread(thread_id, str(cwd), title=prompt[:60])

        focus_suffix = agent_focus_prompt(focus_agent)
        if focus_suffix:
            self._send_event({"type": "text", "content": f"[focus] running as `{focus_agent}` agent only\n\n"})

        agent, profile, checkpointer, mcp_clients = create_agent(
            profile_name=profile_name,
            thread_id=thread_id,
            cwd=cwd,
            model_override=model_override,
            extra_system_prompt=focus_suffix,
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

        if mcp_clients:
            from localsmartz.plugins.agent_integration import close_mcp_clients
            close_mcp_clients(mcp_clients)

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

    def _handle_model_pull(self):
        """Stream SSE progress for `ollama pull <model>`."""
        try:
            body = self._read_json_body()
        except ValueError:
            self._json_response({"error": "Invalid request body"}, 400)
            return
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

    def _handle_skill_refactor(self):
        try:
            body = self._read_json_body()
        except ValueError:
            self._json_response({"error": "Invalid JSON body"}, 400)
            return
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

    def _handle_skill_new(self):
        try:
            body = self._read_json_body()
        except ValueError:
            self._json_response({"error": "Invalid JSON body"}, 400)
            return
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

    def _handle_plugin_save(self):
        try:
            body = self._read_json_body()
        except ValueError:
            self._json_response({"error": "Invalid JSON body"}, 400)
            return
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
        """Surface the conceptual agents in the active profile."""
        from localsmartz.profiles import get_profile, list_agents

        profile = get_profile(self._default_profile)
        self._json_response({
            "profile": profile["name"],
            "agents": list_agents(profile),
        })

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

    # ── Secrets ──

    def _handle_secrets_list(self):
        from localsmartz import secrets as _secrets
        try:
            self._json_response(_secrets.masked_all())
        except Exception as e:  # noqa: BLE001
            self._json_response({"error": str(e)}, 500)

    def _handle_secrets_set(self):
        from localsmartz import secrets as _secrets
        from localsmartz import log_buffer
        try:
            body = self._read_json_body()
        except ValueError as e:
            self._json_response({"error": str(e)}, 400)
            return
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

    def _handle_issues_report(self):
        from localsmartz import log_buffer
        try:
            body = self._read_json_body()
        except ValueError as e:
            self._json_response({"error": str(e)}, 400)
            return
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


def start_server(port: int = 11435, profile_name: str | None = None):
    """Start the Local Smartz HTTP server.

    Args:
        port: Port to listen on
        profile_name: Profile override ("full" or "lite"). Auto-detect if None.
    """
    # Store profile_name on handler class so all requests use consistent profile
    LocalSmartzHandler._default_profile = profile_name
    from localsmartz import __version__ as _version
    from localsmartz import log_buffer as _log_buffer
    _log_buffer.info("startup", f"local-smartz {_version} starting on port {port}")
    server = ThreadingHTTPServer(("127.0.0.1", port), LocalSmartzHandler)
    server.daemon_threads = True
    print(f"\n  Local Smartz running at http://localhost:{port}", file=sys.stderr)
    print(f"  Press Ctrl+C to stop.\n", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.shutdown()
