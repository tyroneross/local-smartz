# Web UI Redesign (Alt C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Local Smartz embedded web UI with Alt C visual language, adding model selector, folder management, and thread metadata.

**Architecture:** Backend-first: extend config.py with folder/merge support, add API endpoints to serve.py, fix the thread race condition, then replace the `_UI_HTML` constant with the Alt C design. All changes in 4 files.

**Tech Stack:** Python 3.12+ (stdlib http.server), embedded HTML/CSS/JS (no build tools), pytest

**Spec:** `docs/superpowers/specs/2026-03-22-web-ui-redesign-design.md`
**Mockup:** `/tmp/localsmartz-alt-c-creative.html`

---

### Task 1: Make save_config() read-modify-write

**Files:**
- Modify: `src/localsmartz/config.py:56-82`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py — add at end

def test_save_config_merges_existing_keys(tmp_path):
    """save_config merges with existing config, doesn't overwrite."""
    save_config(tmp_path, {"planning_model": "model-a", "profile": "full"})
    save_config(tmp_path, {"folders": ["~/docs"]})
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded["planning_model"] == "model-a"
    assert loaded["folders"] == ["~/docs"]


def test_save_config_overwrites_same_key(tmp_path):
    """save_config overwrites keys that are explicitly passed."""
    save_config(tmp_path, {"planning_model": "model-a", "profile": "full"})
    save_config(tmp_path, {"planning_model": "model-b"})
    loaded = load_config(tmp_path)
    assert loaded["planning_model"] == "model-b"
    assert loaded["profile"] == "full"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_config.py::test_save_config_merges_existing_keys tests/test_config.py::test_save_config_overwrites_same_key -v`
Expected: FAIL — `folders` key gets overwritten

- [ ] **Step 3: Update save_config to read-modify-write**

In `src/localsmartz/config.py`, replace `save_config` (lines 56-82):

```python
def save_config(cwd: Path, config: dict) -> None:
    """Save config to .localsmartz/config.json.

    Read-modify-write: merges new keys with existing config.
    Atomic write: writes to temp file in same directory, then renames.
    Creates .localsmartz/ directory if it doesn't exist.
    """
    config_dir = cwd / ".localsmartz"
    config_dir.mkdir(parents=True, exist_ok=True)

    path = _config_path(cwd)

    # Read existing config to merge
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Merge: new keys overwrite existing, existing keys preserved
    merged = {**existing, **config}

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(
        dir=str(config_dir), suffix=".tmp", prefix="config_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(merged, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: ALL PASS (including all existing tests)

- [ ] **Step 5: Commit**

```bash
git add src/localsmartz/config.py tests/test_config.py
git commit -m "feat: make save_config read-modify-write to prevent key overwrites"
```

---

### Task 2: Relax load_config validation + add get_folders helper

**Files:**
- Modify: `src/localsmartz/config.py:29-53`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py — add at end

def test_load_config_folders_only(tmp_path):
    """Config with only folders (no planning_model) is valid."""
    save_config(tmp_path, {"folders": ["~/docs"]})
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded["folders"] == ["~/docs"]


def test_get_folders_returns_list(tmp_path):
    """get_folders returns folder list from config."""
    from localsmartz.config import get_folders
    save_config(tmp_path, {"planning_model": "x", "folders": ["/a", "/b"]})
    assert get_folders(tmp_path) == ["/a", "/b"]


def test_get_folders_empty_when_missing(tmp_path):
    """get_folders returns [] when no folders key or no config."""
    from localsmartz.config import get_folders
    assert get_folders(tmp_path) == []
    save_config(tmp_path, {"planning_model": "x"})
    assert get_folders(tmp_path) == []


def test_get_folders_corrupted_config(tmp_path):
    """get_folders returns [] for corrupted config."""
    from localsmartz.config import get_folders
    config_dir = tmp_path / ".localsmartz"
    config_dir.mkdir()
    (config_dir / "config.json").write_text("not json")
    assert get_folders(tmp_path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_config.py::test_load_config_folders_only tests/test_config.py::test_get_folders_returns_list -v`
Expected: FAIL

- [ ] **Step 3: Relax load_config and add get_folders**

In `src/localsmartz/config.py`:

Replace `load_config` validation (lines 49-51):
```python
    # Old: require planning_model
    # model = data.get("planning_model")
    # if not model or not isinstance(model, str):
    #     return None

    # New: valid if it has planning_model OR folders
    has_model = isinstance(data.get("planning_model"), str) and data["planning_model"]
    has_folders = isinstance(data.get("folders"), list)
    if not has_model and not has_folders:
        return None
```

Add `get_folders` function after `save_config`:
```python
def get_folders(cwd: Path) -> list[str]:
    """Get configured research folders. Returns [] if none configured.

    Reads config JSON directly — does not depend on load_config validation.
    """
    path = _config_path(cwd)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        folders = data.get("folders", [])
        return folders if isinstance(folders, list) else []
    except (json.JSONDecodeError, OSError):
        return []
```

- [ ] **Step 4: Run ALL config tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: ALL PASS

**Important:** Verify `test_load_missing_planning_model` still passes — it tests that `{"profile": "lite"}` (no model, no folders) returns `None`. With the relaxed validation this should still be `None` since there's neither `planning_model` nor `folders`.

- [ ] **Step 4b: Guard resolve_model against folders-only config**

`resolve_model()` at line ~204 does `config["planning_model"]` which will `KeyError` on a folders-only config. Add a guard:

In `src/localsmartz/config.py`, in `resolve_model` (around line 204), change:
```python
    # Old:
    # config = load_config(cwd)
    # if config:
    #     model = config["planning_model"]

    # New:
    config = load_config(cwd)
    if config and config.get("planning_model"):
        model = config["planning_model"]
```

Add test:
```python
def test_resolve_model_folders_only_config_runs_picker(tmp_path):
    """Config with only folders (no planning_model) falls through to picker."""
    save_config(tmp_path, {"folders": ["/docs"]})
    with patch("localsmartz.config.check_server", return_value=True), \
         patch("localsmartz.config.list_models_with_size", return_value=[("m:8b", 5.0)]), \
         patch("localsmartz.config.get_version", return_value="0.15.2"), \
         patch("localsmartz.config.detect_profile", return_value="lite"), \
         patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        result = resolve_model(tmp_path, cli_model=None, profile_name="lite")
        assert result == "m:8b"
```

- [ ] **Step 5: Commit**

```bash
git add src/localsmartz/config.py tests/test_config.py
git commit -m "feat: relax config validation for folders-only configs, add get_folders helper"
```

---

### Task 3: Add folder management functions to config.py

**Files:**
- Modify: `src/localsmartz/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py — add at end

def test_add_folder(tmp_path):
    """add_folder adds a path to the folders list."""
    from localsmartz.config import add_folder, get_folders
    add_folder(tmp_path, "/Users/test/docs")
    assert get_folders(tmp_path) == ["/Users/test/docs"]


def test_add_folder_no_duplicates(tmp_path):
    """add_folder doesn't add the same path twice."""
    from localsmartz.config import add_folder, get_folders
    add_folder(tmp_path, "/Users/test/docs")
    add_folder(tmp_path, "/Users/test/docs")
    assert get_folders(tmp_path) == ["/Users/test/docs"]


def test_add_folder_expands_tilde(tmp_path):
    """add_folder expands ~ to absolute path."""
    from localsmartz.config import add_folder, get_folders
    import os
    add_folder(tmp_path, "~/test-folder-xyz")
    folders = get_folders(tmp_path)
    assert len(folders) == 1
    assert folders[0] == os.path.expanduser("~/test-folder-xyz")


def test_remove_folder(tmp_path):
    """remove_folder removes a path from the list."""
    from localsmartz.config import add_folder, remove_folder, get_folders
    add_folder(tmp_path, "/a")
    add_folder(tmp_path, "/b")
    remove_folder(tmp_path, "/a")
    assert get_folders(tmp_path) == ["/b"]


def test_remove_folder_nonexistent(tmp_path):
    """remove_folder is a no-op for paths not in the list."""
    from localsmartz.config import add_folder, remove_folder, get_folders
    add_folder(tmp_path, "/a")
    remove_folder(tmp_path, "/nonexistent")
    assert get_folders(tmp_path) == ["/a"]


def test_add_folder_preserves_model(tmp_path):
    """add_folder doesn't overwrite planning_model."""
    from localsmartz.config import add_folder
    save_config(tmp_path, {"planning_model": "my-model", "profile": "full"})
    add_folder(tmp_path, "/docs")
    loaded = load_config(tmp_path)
    assert loaded["planning_model"] == "my-model"
    assert loaded["folders"] == ["/docs"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_config.py::test_add_folder tests/test_config.py::test_remove_folder -v`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Implement add_folder and remove_folder**

In `src/localsmartz/config.py`, add after `get_folders`:

```python
def add_folder(cwd: Path, folder_path: str) -> list[str]:
    """Add a research folder to config. Returns updated list.

    Expands ~, normalizes path. No-op if already present.
    """
    expanded = str(Path(folder_path).expanduser())
    folders = get_folders(cwd)
    if expanded not in folders:
        folders.append(expanded)
    save_config(cwd, {"folders": folders})
    return folders


def remove_folder(cwd: Path, folder_path: str) -> list[str]:
    """Remove a research folder from config. Returns updated list.

    Expands ~, normalizes path. No-op if not present.
    """
    expanded = str(Path(folder_path).expanduser())
    folders = get_folders(cwd)
    folders = [f for f in folders if f != expanded]
    save_config(cwd, {"folders": folders})
    return folders
```

- [ ] **Step 4: Run all config tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/localsmartz/config.py tests/test_config.py
git commit -m "feat: add folder management functions (add_folder, remove_folder)"
```

---

### Task 4: Add API endpoints for models, folders, and fix thread race

**Files:**
- Modify: `src/localsmartz/serve.py:287-344` (handler class routing)
- Modify: `src/localsmartz/serve.py:628-643` (done event + append_entry ordering)
- Test: `tests/test_serve.py`

- [ ] **Step 1: Write failing tests for new endpoints**

```python
# tests/test_serve.py — add at end
# IMPORTANT: Use existing test patterns. The test file uses a `server` fixture
# that returns a port number, plus `_get(port, path)` and `_post(port, path, payload)`
# helpers using HTTPConnection. Add a `_delete` helper:

def _delete(port: int, path: str, payload: dict) -> tuple[int, dict]:
    """Send a DELETE request with JSON body. Returns (status, response_dict)."""
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(payload).encode("utf-8")
    conn.request("DELETE", path, body=body,
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    status = resp.status
    data = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return status, data


def test_get_models(server):
    """GET /api/models returns model list with current and profile."""
    status, data = _get(server, "/api/models")
    assert status == 200
    assert "models" in data
    assert "current" in data
    assert "profile" in data


def test_post_model_select_invalid(server):
    """POST /api/models/select rejects unavailable models."""
    status, data = _post(server, "/api/models/select", {"model": "nonexistent-model-xyz"})
    assert status == 400


def test_get_folders(server):
    """GET /api/folders returns workspace and configured folders."""
    status, data = _get(server, "/api/folders")
    assert status == 200
    assert "workspace" in data
    assert isinstance(data["folders"], list)


def test_post_folder_add_nonexistent(server):
    """POST /api/folders rejects nonexistent paths."""
    status, data = _post(server, "/api/folders", {"path": "/nonexistent/path/xyz"})
    assert status == 400


def test_cors_includes_delete(server):
    """CORS OPTIONS response includes DELETE in allowed methods."""
    conn = HTTPConnection("127.0.0.1", server, timeout=5)
    conn.request("OPTIONS", "/api/folders")
    resp = conn.getresponse()
    methods = resp.getheader("Access-Control-Allow-Methods", "")
    conn.close()
    assert resp.status == 204
    assert "DELETE" in methods
```

**IMPORTANT:** Check the existing `test_serve.py` for the exact `server` fixture, `_get()`, and `_post()` helper signatures before writing tests. Adapt the test code to match. The snippets above follow the expected pattern but must be verified.

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL — endpoints don't exist yet

- [ ] **Step 3: Add handler methods and routing**

In `src/localsmartz/serve.py`, add to the handler class:

1. Add class variable: `_model_override: str | None = None`

2. Update `do_GET` routing — add before the `else`:
```python
        elif path == "/api/models":
            self._handle_models()
        elif path == "/api/folders":
            self._handle_folders()
```

3. Update `do_POST` routing — add before the `else`:
```python
        elif path == "/api/models/select":
            self._handle_model_select()
        elif path == "/api/folders":
            self._handle_folder_add()
```

4. Add `do_DELETE` method:
```python
    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/folders":
            self._handle_folder_delete()
        else:
            self._json_response({"error": "Not found"}, 404)
```

5. Update `_cors_headers` — change `"GET, POST, OPTIONS"` to `"GET, POST, DELETE, OPTIONS"`

6. Add handler methods:
```python
    def _handle_models(self):
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
        from localsmartz.ollama import model_available
        from localsmartz.config import save_config
        from localsmartz.profiles import get_profile

        body = self._read_json_body()
        if not body or not body.get("model"):
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
        from localsmartz.config import get_folders
        cwd = Path.cwd()
        self._json_response({
            "workspace": str(cwd),
            "folders": get_folders(cwd),
        })

    def _handle_folder_add(self):
        from localsmartz.config import add_folder
        body = self._read_json_body()
        if not body or not body.get("path"):
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
        from localsmartz.config import remove_folder
        body = self._read_json_body()
        if not body or not body.get("path"):
            self._json_response({"error": "No path specified"}, 400)
            return
        cwd = Path.cwd()
        folders = remove_folder(cwd, body["path"])
        self._json_response({"ok": True, "folders": folders})
```

7. **DO NOT replace `_read_json_body`** — it already exists at serve.py:361-380 and raises `ValueError` on invalid input. Existing callers (`_handle_research_post`) depend on this. New handler methods must wrap calls in `try/except ValueError`:
```python
        try:
            body = self._read_json_body()
        except ValueError:
            body = None
```
Update `_handle_model_select`, `_handle_folder_add`, and `_handle_folder_delete` to use this pattern instead of the bare `body = self._read_json_body()` shown in step 6.

8. Update `_stream_research` to check `_model_override` — at line ~510 where model is resolved, add:
```python
        # Check class-level model override (set by /api/models/select)
        model_override = LocalSmartzHandler._model_override or _saved_model_override(cwd)
```

9. Fix thread race condition — in `_stream_research` (around line 628-643), swap the order so `append_entry` runs BEFORE the done event:
```python
        # Log to thread BEFORE sending done event
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
```

- [ ] **Step 4: Run all tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_serve.py tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/localsmartz/serve.py tests/test_serve.py
git commit -m "feat: add /api/models, /api/folders endpoints and fix thread race condition"
```

---

### Task 5: Replace _UI_HTML with Alt C design

**Files:**
- Modify: `src/localsmartz/serve.py:56-284` (replace entire `_UI_HTML` constant)

This is the largest task — replacing the embedded HTML/CSS/JS with the Alt C "Creative" design. The reference mockup is at `/tmp/localsmartz-alt-c-creative.html`.

- [ ] **Step 1: Replace `_UI_HTML`**

Replace the entire `_UI_HTML` string (lines 56-284) with the Alt C design. This includes:

**CSS changes:**
- Dark-first color scheme (tokens from spec)
- 240px sidebar with sections (brand, model, folders, threads, status)
- Gradient Run button
- 1px grid gap for section borders
- Monospace for data elements

**HTML structure:**
- Brand: gradient LS icon + name + version placeholder
- Model section: card with name/size + "Change" link
- Folders section: folder list + add button
- Threads section: dot indicators + metadata
- Status bar: Ollama status
- Main: "Research" heading + textarea + buttons + output

**JavaScript changes:**
- Keep existing: `streamSSE()`, `handleEvent()`, `setRunning()`, prompt keyboard shortcut
- Add: `fetchModels()` — loads models, populates model card, handles "Change" overlay
- Add: `selectModel(name)` — POST to `/api/models/select`, update card
- Add: `fetchFolders()` — loads folders, renders folder list
- Add: `addFolder()` — shows input, POST to `/api/folders`
- Add: `removeFolder(path)` — DELETE to `/api/folders`
- Update: `fetchStatus()` — populate brand version, status bar model count
- Update: `fetchThreads()` — render entry count + relative time
- Update: `handleEvent` for `done` — thread_id from event
- Add: `timeAgo(isoDate)` helper for relative timestamps

Use the mockup at `/tmp/localsmartz-alt-c-creative.html` as the visual reference, but wire up all the JavaScript to the real API endpoints.

**Critical constraints:**
- The element `id="output"` MUST be preserved — existing test `test_ui_serves_html` checks for it
- `handleEvent` for `done` must extract `thread_id` from event data:
```javascript
else if (d.type === 'done') {
    append(makeEl('span', 'done-line', 'Done (' + (d.duration_ms / 1000).toFixed(1) + 's)'));
    setRunning(false); fetchThreads();
}
```
- `timeAgo(isoDate)` edge cases: return `"unknown"` for null/undefined, `"just now"` for <60s, then `"Nm ago"`, `"Nh ago"`, `"Nd ago"`
- `addFolder()` error handling: if POST returns 400, show the error message from response body in the folder section (not alert())
- New handler methods must wrap `_read_json_body()` in `try/except ValueError` (see Task 4 step 7)

- [ ] **Step 2: Start server and visually verify**

```bash
cd ~/Desktop/git-folder/local-smartz
pkill -f "localsmartz --serve" 2>/dev/null; sleep 1
.venv/bin/python -m localsmartz --serve --port 11435 &
sleep 2
curl -s http://localhost:11435/ | head -5
```

Open `http://localhost:11435` in browser. Verify:
- Brand shows "Local Smartz" + version
- Model card shows current model + "Change" works
- Folders section shows workspace
- Threads list with entry count + relative time
- Status bar shows Ollama status
- Run/Stop buttons work
- Research query completes and thread sidebar updates

- [ ] **Step 3: Run all existing tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS (153+ tests)

- [ ] **Step 4: Commit**

```bash
git add src/localsmartz/serve.py
git commit -m "feat: redesign web UI with Alt C visual language

Model selector, folder management, thread metadata, and dark-first
design with gradient accents. All features wired to new API endpoints."
```

---

### Task 6: E2E verification

**Files:** None (read-only verification)

- [ ] **Step 1: Run full test suite**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short
```
Expected: ALL PASS

- [ ] **Step 2: API smoke test**

```bash
curl -s http://localhost:11435/api/health
curl -s http://localhost:11435/api/models
curl -s http://localhost:11435/api/folders
curl -s http://localhost:11435/api/status
```

Verify all return valid JSON with expected shapes.

- [ ] **Step 3: Model selector E2E**

```bash
# Select a different model via API
curl -s -X POST http://localhost:11435/api/models/select \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b-q4_K_M"}'
# Verify config updated
cat ~/Desktop/git-folder/local-smartz/.localsmartz/config.json
# Verify /api/models reflects change
curl -s http://localhost:11435/api/models | python3 -c "import sys,json; print(json.load(sys.stdin)['current'])"
```

- [ ] **Step 4: Folder management E2E**

```bash
# Add a folder
curl -s -X POST http://localhost:11435/api/folders \
  -H "Content-Type: application/json" \
  -d '{"path":"~/Documents"}'
# Verify
curl -s http://localhost:11435/api/folders
# Remove it
curl -s -X DELETE http://localhost:11435/api/folders \
  -H "Content-Type: application/json" \
  -d '{"path":"~/Documents"}'
# Verify removed
curl -s http://localhost:11435/api/folders
```

- [ ] **Step 5: IBR visual scan**

Navigate Playwright to `http://localhost:11435` and take a screenshot. Verify:
- Alt C layout renders correctly
- Model card shows model name + size
- Threads show entry count + timestamps
- Status bar shows Ollama status

- [ ] **Step 6: Commit any test fixes**

If any issues found during verification, fix and commit.

---

## Chunk Boundaries

| Chunk | Tasks | Review After |
|-------|-------|-------------|
| **Chunk 1** | Tasks 1-3 (config layer) | Yes — verify all config tests pass, no regressions |
| **Chunk 2** | Task 4 (API endpoints + thread fix) | Yes — verify new endpoints work, existing tests pass |
| **Chunk 3** | Tasks 5-6 (UI replacement + E2E) | Yes — full visual + functional verification |
