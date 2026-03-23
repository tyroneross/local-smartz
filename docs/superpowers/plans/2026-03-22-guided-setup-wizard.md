# Guided Setup Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guided setup wizard to the web UI (priority) and terminal that walks non-technical users through Ollama check, model selection, workspace config, and a test query.

**Architecture:** The web wizard renders in the `<main>` area when `status.ready === false`. It reuses existing API endpoints (`/api/status`, `/api/models`, `/api/setup`, `/api/folders`) and the SSE streaming infrastructure. The terminal wizard rewrites `--setup` as a 4-step interactive flow. A shared `get_ram_gb()` helper is extracted for both.

**Tech Stack:** Python 3.12+ (stdlib http.server), embedded HTML/CSS/JS, pytest

**Spec:** `docs/superpowers/specs/2026-03-22-guided-setup-wizard-design.md`

---

### Task 1: Extract shared `get_ram_gb()` helper

**Files:**
- Create: `src/localsmartz/utils/__init__.py`
- Create: `src/localsmartz/utils/hardware.py`
- Modify: `src/localsmartz/config.py:181-194` (replace inline RAM detection)
- Create: `tests/test_hardware.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_hardware.py
from localsmartz.utils.hardware import get_ram_gb


def test_get_ram_gb_returns_positive_int():
    """get_ram_gb returns a positive integer on this machine."""
    ram = get_ram_gb()
    assert isinstance(ram, int)
    assert ram > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_hardware.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create utils package and hardware module**

Create `src/localsmartz/utils/__init__.py`:
```python
```

Create `src/localsmartz/utils/hardware.py`:
```python
"""Hardware detection utilities."""

import platform
import subprocess


def get_ram_gb() -> int:
    """Detect system RAM in GB. Returns 0 on failure."""
    try:
        if platform.system() == "Darwin":
            ram_bytes = int(subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True
            ).strip())
        else:
            import os
            ram_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return int(ram_bytes / (1024 ** 3))
    except Exception:
        return 0
```

- [ ] **Step 4: Update config.py to use the shared helper**

In `src/localsmartz/config.py`, in `first_run_picker()` around lines 181-194, replace the inline RAM detection block:

```python
    # Old: inline platform detection (lines 181-194)
    # Replace with:
    from localsmartz.utils.hardware import get_ram_gb
    ram_gb = get_ram_gb()
    if ram_gb > 0:
        print(f"  Hardware: {ram_gb} GB RAM · {detected} profile")
    else:
        print(f"  Profile: {detected}")
```

Remove the `import platform` and `import subprocess` that were only used for RAM detection (check they're not used elsewhere in the function first).

- [ ] **Step 5: Run all tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short`
Expected: ALL PASS (171 + 1 new)

- [ ] **Step 6: Commit**

```bash
git add src/localsmartz/utils/ tests/test_hardware.py src/localsmartz/config.py
git commit -m "refactor: extract get_ram_gb() into shared utils/hardware module"
```

---

### Task 2: Extend `/api/status` with `version` and `ram_gb`

**Files:**
- Modify: `src/localsmartz/serve.py:648-677` (`_handle_status`)
- Modify: `src/localsmartz/serve.py` (JS `fetchStatus` in `_UI_HTML`)
- Test: `tests/test_serve.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_serve.py — add at end

def test_status_includes_version(server):
    """GET /api/status includes version field."""
    status, data = _get(server, "/api/status")
    assert status == 200
    assert "version" in data
    assert isinstance(data["version"], str)
    assert data["version"]  # non-empty


def test_status_includes_ram(server):
    """GET /api/status includes ram_gb field."""
    status, data = _get(server, "/api/status")
    assert status == 200
    assert "ram_gb" in data
    assert isinstance(data["ram_gb"], int)
    assert data["ram_gb"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_serve.py::test_status_includes_version tests/test_serve.py::test_status_includes_ram -v`
Expected: FAIL — keys missing

- [ ] **Step 3: Update `_handle_status()`**

In `src/localsmartz/serve.py`, in `_handle_status()` (line 648), add imports and fields:

After the existing imports at the top of the method, add:
```python
        from localsmartz.utils.hardware import get_ram_gb
        from localsmartz import __version__
```

In the `self._json_response({...})` call, add these three fields:
```python
            "version": __version__,
            "ram_gb": get_ram_gb(),
            "platform": platform.system().lower(),  # "darwin" or "linux"
```

Add `import platform` to the method-level imports (or at the top of the file).

- [ ] **Step 4: Update `fetchStatus()` JS in `_UI_HTML`**

Find `fetchStatus()` in the embedded JS. Replace the hardcoded version line:
```javascript
      // Old: if (v) v.textContent = 'v0.1.0';
      // New:
      const v = $('version');
      if (v && d.version) v.textContent = 'v' + d.version;
```

- [ ] **Step 5: Run all tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/localsmartz/serve.py tests/test_serve.py
git commit -m "feat: add version and ram_gb to /api/status response"
```

---

### Task 3: Extend `POST /api/setup` to accept optional model parameter

**Files:**
- Modify: `src/localsmartz/serve.py:969+` (`_handle_setup`)
- Test: `tests/test_serve.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_serve.py — add at end

def test_setup_accepts_empty_body(server):
    """POST /api/setup with no body doesn't 400."""
    conn = HTTPConnection("127.0.0.1", server, timeout=10)
    conn.request("POST", "/api/setup")
    resp = conn.getresponse()
    # Should start SSE stream (200) or error about Ollama, NOT 400
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    conn.close()
    # Should have SSE data lines
    assert "data:" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_serve.py::test_setup_accepts_empty_body -v`
Expected: May pass or fail depending on current body handling. If it 400s, the test catches it.

- [ ] **Step 3: Update `_handle_setup()` to handle optional model parameter**

In `src/localsmartz/serve.py`, at the top of `_handle_setup()` (line 969), add body parsing that tolerates empty bodies:

At the TOP of `_handle_setup()` (serve.py:969), BEFORE the existing `self._start_sse()` call, insert the body parsing block:

```python
        # Parse optional model parameter — empty body is valid (line 969+)
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

        # Existing self._start_sse() follows here unchanged
```

Then at the model selection block (serve.py:1018-1021), replace:
```python
            # OLD (lines 1018-1021):
            models = [profile["planning_model"]]
            if profile["execution_model"] != profile["planning_model"]:
                models.append(profile["execution_model"])

            # NEW:
            if target_model:
                models = [target_model]
            else:
                models = [profile["planning_model"]]
                if profile["execution_model"] != profile["planning_model"]:
                    models.append(profile["execution_model"])
```

- [ ] **Step 4: Run all tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/localsmartz/serve.py tests/test_serve.py
git commit -m "feat: POST /api/setup accepts optional model parameter for targeted pulls"
```

---

### Task 4: Add setup wizard to web UI

**Files:**
- Modify: `src/localsmartz/serve.py` (`_UI_HTML` — HTML + CSS + JS)

This is the main wizard implementation. The wizard renders when `status.ready === false` and hides when setup completes.

- [ ] **Step 1: Add wizard HTML structure to `_UI_HTML`**

Inside the `<main>` element, add a wizard container before the existing research UI:

```html
<div id="wizard" style="display:none">
  <div class="wizard-container">
    <div class="wizard-steps" id="wizard-steps"></div>
    <div id="wizard-content"></div>
  </div>
</div>
```

Add wizard CSS to the `<style>` block:
```css
.wizard-container { max-width: 480px; margin: 0 auto; padding-top: 40px; }
.wizard-steps { display: flex; gap: 8px; justify-content: center; margin-bottom: 32px; }
.wizard-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--fg-muted); transition: background .3s; }
.wizard-dot.done { background: var(--teal); }
.wizard-dot.active { background: var(--fg); }
.wizard-title { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
.wizard-desc { font-size: 13px; color: var(--fg-muted); margin-bottom: 24px; line-height: 1.5; }
.wizard-status { display: flex; align-items: center; gap: 8px; padding: 12px; background: var(--surface-raised); border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
.wizard-status .ok { color: var(--green); }
.wizard-status .err { color: var(--red); }
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
.wizard-btn { padding: 10px 32px; font-size: 14px; font-weight: 500; border: none; border-radius: 6px; cursor: pointer; transition: all .2s; }
.wizard-btn-primary { background: linear-gradient(135deg, var(--teal), #6366f1); color: #fff; box-shadow: 0 2px 8px var(--teal-dim); }
.wizard-btn-primary:hover { box-shadow: 0 4px 16px var(--teal-glow); }
.wizard-btn-primary:disabled { opacity: .3; cursor: not-allowed; }
.wizard-btn-secondary { background: var(--surface); color: var(--fg-muted); border: 1px solid var(--border); }
.wizard-test-output { padding: 12px; background: var(--surface-raised); border-radius: 6px; font-size: 13px; line-height: 1.6; white-space: pre-wrap; min-height: 60px; margin: 12px 0; }
```

- [ ] **Step 2: Add wizard JavaScript**

Add the wizard JS functions inside the existing IIFE. The key functions:

```javascript
  // Wizard state
  let wizardStep = 0;
  const wizardEl = $('wizard');

  function showWizard() {
    wizardEl.style.display = '';
    // Hide all main children except the wizard
    document.querySelectorAll('main > :not(#wizard)').forEach(el => el.style.display = 'none');
    // Dim sidebar during wizard
    document.querySelector('aside').style.opacity = '0.4';
    document.querySelector('aside').style.pointerEvents = 'none';
    renderWizardStep();
  }

  function hideWizard() {
    wizardEl.style.display = 'none';
    document.querySelectorAll('main > :not(#wizard)').forEach(el => el.style.display = '');
    // Restore sidebar
    document.querySelector('aside').style.opacity = '';
    document.querySelector('aside').style.pointerEvents = '';
    // Refresh sidebar data
    fetchModels(); fetchFolders(); fetchThreads();
  }

  function renderWizardDots() {
    const dots = $('wizard-steps');
    dots.innerHTML = '';
    for (let i = 0; i < 4; i++) {
      const d = makeEl('span', 'wizard-dot' + (i < wizardStep ? ' done' : '') + (i === wizardStep ? ' active' : ''));
      dots.appendChild(d);
    }
  }

  function renderWizardStep() {
    renderWizardDots();
    const content = $('wizard-content');
    if (wizardStep === 0) renderStep1_Status(content);
    else if (wizardStep === 1) renderStep2_Model(content);
    else if (wizardStep === 2) renderStep3_Workspace(content);
    else if (wizardStep === 3) renderStep4_Test(content);
  }
```

Each step function (`renderStep1_Status`, `renderStep2_Model`, `renderStep3_Workspace`, `renderStep4_Test`) renders its content into the `wizard-content` div and sets up event handlers for the buttons.

**Step 1 (Status Check):** Fetches `/api/status`, shows Ollama status + RAM. If Ollama not running, shows install instructions + "Check Again" button. If running, auto-advances after 1s.

**Step 2 (Model Select):** Fetches `/api/models`. Shows available models as selectable cards. If user wants to download a new model, calls `POST /api/setup` with `{model: name}` and shows progress. The "Select" button must be disabled until the `done` SSE event fires. After selection, calls `POST /api/models/select`.

Curated download suggestions (hardcoded JS array, gated by `ram_gb` from status):
```javascript
const SUGGESTED_MODELS = [
  { name: 'qwen3:8b-q4_K_M', size: '5 GB', desc: 'Fast, good for quick questions', minRam: 8 },
  { name: 'qwen2.5-coder:32b-instruct-q5_K_M', size: '23 GB', desc: 'Strong coding and analysis', minRam: 32 },
  { name: 'llama3.1:70b-instruct-q5_K_M', size: '40 GB', desc: 'Most capable, best quality', minRam: 64 },
];
// Filter by ram_gb from /api/status, exclude already-downloaded models
```

**Step 3 (Workspace):** Shows current workspace from `/api/folders`. Pre-filled input. "+ Add folder" button. "Continue" saves via `POST /api/folders`.

**Step 4 (Test Run):** Auto-submits test query. Uses a **separate** `streamWizardSSE()` function that targets `#wizard-test-output` instead of `#output`:

```javascript
async function streamWizardSSE(url, options, targetEl) {
  // Same as streamSSE but appends to targetEl instead of out
  // On 'done': show "Start Researching" button
  // On 'error': show error + "Try Again" button that re-runs Step 4
}
```

This avoids polluting the main `#output` div with test query results. The "Try Again" button re-calls `renderStep4_Test(content)`. The "Start Researching" button calls `hideWizard()` and refreshes all sidebar data.

- [ ] **Step 3: Update `fetchStatus()` to trigger wizard**

In the existing `fetchStatus()`, after getting the status response, add wizard trigger logic:

```javascript
  async function fetchStatus() {
    try {
      const r = await fetch('/api/status'), d = await r.json();
      // ... existing status bar update ...

      // Wizard trigger: show wizard if not ready
      if (!d.ready && !wizardDismissed) {
        showWizard();
      }
    } catch(e) {
      // ... existing error handling ...
    }
  }
```

Add `let wizardDismissed = false;` at the top of the IIFE.

- [ ] **Step 4: Verify existing test still passes**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_serve.py::test_ui_serves_html -v`
Expected: PASS — `id="output"` and `<title>Local Smartz</title>` still present

- [ ] **Step 5: Start server and visually verify**

```bash
cd ~/Desktop/git-folder/local-smartz
pkill -f "localsmartz --serve" 2>/dev/null; sleep 1
.venv/bin/python -m localsmartz --serve --port 11435 &
sleep 3
```

Open `http://localhost:11435` in browser. If system is already configured (ready=true), the research view shows. To test the wizard, temporarily rename `.localsmartz/config.json` to trigger the wizard.

- [ ] **Step 6: Run all tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/localsmartz/serve.py
git commit -m "feat: add guided setup wizard to web UI

4-step wizard: Ollama check, model selection with download,
workspace config, and test query. Shows when status.ready is false,
hides after setup completes."
```

---

### Task 5: Rewrite terminal `--setup` as interactive wizard

**Files:**
- Modify: `src/localsmartz/__main__.py:168-175` (`_setup` function)

- [ ] **Step 1: Rewrite `_setup()` as 4-step wizard**

Replace the existing `_setup()` function:

```python
def _setup(args):
    """Interactive 4-step setup wizard."""
    from localsmartz.profiles import get_profile, detect_profile
    from localsmartz.ollama import (
        check_server, is_installed, list_models_with_size,
        model_available, pull_model, get_version,
    )
    from localsmartz.config import save_config, get_folders, add_folder
    from localsmartz.utils.hardware import get_ram_gb

    interactive = sys.stdin.isatty()
    cwd = Path(args.cwd) if args.cwd else Path.cwd()

    print("\n  \033[1mLocal Smartz Setup\033[0m")
    print("  " + "=" * 20)

    # Step 1: Check Ollama
    print("\n  [1/4] Checking Ollama...")
    if not is_installed():
        print("  \033[31m✗\033[0m Ollama is not installed.")
        print("\n  Install Ollama:")
        print("    macOS:  Download from https://ollama.com/download")
        print("    Linux:  curl -fsSL https://ollama.ai/install.sh | sh")
        sys.exit(1)

    while not check_server():
        print("  \033[31m✗\033[0m Ollama is not running.")
        if not interactive:
            print("  Start Ollama manually: ollama serve")
            sys.exit(1)
        input("  Start Ollama, then press Enter to check again... ")

    version = get_version()
    ram_gb = get_ram_gb()
    profile_name = detect_profile()
    v_str = f" (v{version})" if version else ""
    print(f"  \033[32m✓\033[0m Ollama running{v_str}")
    if ram_gb:
        print(f"  \033[32m✓\033[0m {ram_gb} GB RAM — {profile_name} profile")

    # Step 2: Choose model
    print("\n  [2/4] Choose a model:\n")
    models = list_models_with_size()

    if models:
        print("  Already downloaded:")
        for i, (name, size) in enumerate(models):
            rec = "  \033[94m← recommended\033[0m" if i == len(models) - 1 else ""
            print(f"    {i + 1}. {name:<30s} ({size:.1f} GB){rec}")
    else:
        print("  No models downloaded yet.")
        print("  Downloading recommended model...")
        rec_model = "qwen3:8b-q4_K_M" if ram_gb < 64 else "llama3.1:70b-instruct-q5_K_M"
        pull_model(rec_model)
        models = list_models_with_size()
        print(f"  \033[32m✓\033[0m Downloaded {rec_model}")

    if interactive and models:
        default = len(models)
        try:
            choice = input(f"\n  Select [{default}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(130)
        if not choice:
            idx = default - 1
        else:
            try:
                idx = int(choice) - 1
                if not (0 <= idx < len(models)):
                    idx = default - 1
            except ValueError:
                idx = default - 1
    else:
        idx = len(models) - 1 if models else 0

    selected_model = models[idx][0] if models else None
    if selected_model:
        save_config(cwd, {"planning_model": selected_model, "profile": profile_name})
        print(f"  \033[32m✓\033[0m Model: {selected_model}")

    # Step 3: Workspace
    print(f"\n  [3/4] Workspace folder")
    default_ws = str(cwd)
    if interactive:
        print(f"  Where are the files you want to research?")
        ws_input = input(f"  Default: {default_ws}\n  > ").strip()
        ws = ws_input if ws_input else default_ws
        if ws != default_ws:
            ws_path = Path(ws).expanduser()
            if ws_path.is_dir():
                add_folder(cwd, ws)
                print(f"  \033[32m✓\033[0m Added: {ws}")
            else:
                print(f"  \033[33m!\033[0m Not a directory, skipping")
        print(f"  \033[32m✓\033[0m Workspace: {default_ws}")

        # Extra folders
        while True:
            extra = input("  Add another folder? (path or Enter to skip) > ").strip()
            if not extra:
                break
            extra_path = Path(extra).expanduser()
            if extra_path.is_dir():
                add_folder(cwd, extra)
                print(f"  \033[32m✓\033[0m Added: {extra}")
            else:
                print(f"  \033[33m!\033[0m Not a directory")
    else:
        print(f"  \033[32m✓\033[0m Workspace: {default_ws} (non-interactive, using default)")

    # Step 4: Test
    print(f"\n  [4/4] Testing...")
    if selected_model and interactive:
        profile = get_profile(profile_name, model_override=selected_model)
        print('  Query: "What is artificial intelligence?"')
        try:
            from localsmartz.agent import create_agent
            agent = create_agent(profile)
            result = agent.invoke(
                {"messages": [{"role": "user", "content": "What is artificial intelligence? Answer in one sentence."}]}
            )
            response = result.get("messages", [{}])[-1].get("content", "No response")
            if len(response) > 200:
                response = response[:200] + "..."
            print(f"  → {response}")
            print("  \033[32m✓\033[0m Working!")
        except Exception as e:
            print(f"  \033[33m!\033[0m Test query failed: {e}")
            print("  Setup is complete, but you may need to check your model.")
    else:
        print("  \033[32m✓\033[0m Skipped (non-interactive)")

    print(f"\n  \033[1mSetup complete!\033[0m Run 'localsmartz' to start researching.\n")
```

- [ ] **Step 2: Add first-run auto-trigger to REPL**

In `src/localsmartz/__main__.py`, in the `_interactive()` function (around line 296), before the REPL loop starts, add a check: if no config exists (no `planning_model`), run `_setup(args)` first:

```python
    # At the start of _interactive(), after profile resolution:
    from localsmartz.config import load_config
    config = load_config(cwd)
    if not config or not config.get("planning_model"):
        print("  First run detected — starting setup wizard...\n")
        _setup(args)
        # Reload profile after setup
        from localsmartz.config import resolve_model
        model_override = resolve_model(cwd, args.model, args.profile)
        profile = get_profile(args.profile, model_override=model_override)
```

This ensures `localsmartz` (no flags) auto-triggers the wizard on first run.

- [ ] **Step 3: Run all tests**

Run: `cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 4: Test manually**

```bash
cd ~/Desktop/git-folder/local-smartz
.venv/bin/python -m localsmartz --setup
```

Verify:
- Step 1 shows Ollama status + RAM
- Step 2 lists models, accepts selection
- Step 3 asks for workspace folder
- Step 4 runs test query

- [ ] **Step 4: Commit**

```bash
git add src/localsmartz/__main__.py
git commit -m "feat: rewrite --setup as interactive 4-step wizard

Steps: Ollama check, model selection, workspace config, test query.
Guards input() calls for non-interactive mode."
```

---

### Task 6: E2E verification

**Files:** None (read-only verification)

- [ ] **Step 1: Run full test suite**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short
```
Expected: ALL PASS

- [ ] **Step 2: Test web wizard trigger**

Temporarily rename config to trigger wizard:
```bash
cd ~/Desktop/git-folder/local-smartz
mv .localsmartz/config.json .localsmartz/config.json.bak
pkill -f "localsmartz --serve"; sleep 1
.venv/bin/python -m localsmartz --serve --port 11435 &
sleep 3
```

Open `http://localhost:11435` — wizard should appear. Walk through all 4 steps. After completion, restore config:
```bash
mv .localsmartz/config.json.bak .localsmartz/config.json
```

- [ ] **Step 3: Verify research view after wizard**

After completing the wizard, the research view should show with the model card populated. Run a query to confirm everything works.

- [ ] **Step 4: Test terminal wizard**

```bash
.venv/bin/python -m localsmartz --setup
```

Walk through all 4 steps. Verify each step works as expected.

- [ ] **Step 5: Commit any fixes**

---

## Chunk Boundaries

| Chunk | Tasks | Review After |
|-------|-------|-------------|
| **Chunk 1** | Tasks 1-3 (backend: RAM helper, status extension, setup param) | Yes — verify API responses correct |
| **Chunk 2** | Task 4 (web wizard — largest task) | Yes — visual verification required |
| **Chunk 3** | Tasks 5-6 (terminal wizard + E2E) | Yes — full verification |
