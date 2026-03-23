# LangSmith Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LangSmith observability to Local Smartz — env-var driven, zero overhead when disabled

**Architecture:** LangChain auto-traces when LANGSMITH_TRACING=true is set. We add a .env loader, a --trace flag, and tests.

**Tech Stack:** Python, langsmith, pytest

**Spec:** `docs/superpowers/specs/2026-03-23-langsmith-tracing-design.md`

---

### Task 1: Add langsmith dependency + .gitignore

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Add langsmith to pyproject.toml**

In `pyproject.toml`, in the `dependencies` list, add:
```toml
    "langsmith>=0.3.0",
```

- [ ] **Step 2: Add .env to .gitignore**

Append to `.gitignore`:
```
.env
```

- [ ] **Step 3: Install and verify**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/pip install -e . --quiet
.venv/bin/python -c "import langsmith; print('langsmith OK')"
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .gitignore
git commit -m "chore: add langsmith dependency, add .env to gitignore"
```

---

### Task 2: Create tracing.py module

**Files:**
- Create: `src/localsmartz/tracing.py`
- Create: `tests/test_tracing.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_tracing.py
import os
from pathlib import Path
from localsmartz.tracing import configure_tracing


def test_configure_tracing_no_env(tmp_path, monkeypatch):
    """Returns False when no LANGSMITH vars set."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert configure_tracing(tmp_path) is False


def test_configure_tracing_with_env(tmp_path, monkeypatch):
    """Returns True when LANGSMITH_TRACING=true."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert configure_tracing(tmp_path) is True


def test_configure_tracing_loads_dotenv(tmp_path, monkeypatch):
    """Loads LANGSMITH_ vars from .env file."""
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    (tmp_path / ".env").write_text('LANGSMITH_TRACING=true\nLANGSMITH_PROJECT=TestProject\n')
    result = configure_tracing(tmp_path)
    assert result is True
    assert os.environ.get("LANGSMITH_PROJECT") == "TestProject"


def test_configure_tracing_env_does_not_overwrite(tmp_path, monkeypatch):
    """Existing env vars take precedence over .env file."""
    monkeypatch.setenv("LANGSMITH_PROJECT", "ExistingProject")
    (tmp_path / ".env").write_text('LANGSMITH_TRACING=true\nLANGSMITH_PROJECT=FileProject\n')
    configure_tracing(tmp_path)
    assert os.environ.get("LANGSMITH_PROJECT") == "ExistingProject"


def test_configure_tracing_ignores_non_langsmith(tmp_path, monkeypatch):
    """Only loads LANGSMITH_ prefixed vars from .env."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text('SECRET_KEY=should_not_load\nLANGSMITH_TRACING=true\n')
    configure_tracing(tmp_path)
    assert os.environ.get("SECRET_KEY") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_tracing.py -v
```

- [ ] **Step 3: Create tracing.py**

```python
# src/localsmartz/tracing.py
"""LangSmith tracing configuration.

Tracing is automatic when LANGSMITH_TRACING=true is set.
This module loads .env files and verifies config.
"""

import os
from pathlib import Path


def configure_tracing(cwd: Path | None = None) -> bool:
    """Load .env if present and check if tracing is enabled.

    Only loads LANGSMITH_ prefixed vars. Does not overwrite existing env vars.
    Returns True if LANGSMITH_TRACING is set to true.
    """
    cwd = cwd or Path.cwd()

    env_file = cwd / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key.startswith("LANGSMITH_"):
                os.environ.setdefault(key, value)

    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true"
```

- [ ] **Step 4: Run tests**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_tracing.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/localsmartz/tracing.py tests/test_tracing.py
git commit -m "feat: add tracing module with .env loader for LangSmith"
```

---

### Task 3: Wire tracing into CLI

**Files:**
- Modify: `src/localsmartz/__main__.py`

- [ ] **Step 1: Add --trace flag to argparse**

Find the argparse setup in `main()`. Add after the existing flags:
```python
    parser.add_argument("--trace", action="store_true", help="Enable LangSmith tracing")
```

- [ ] **Step 2: Add configure_tracing() call at startup**

Near the top of `main()`, after `cwd` is resolved but before any agent work:
```python
    # Configure tracing
    from localsmartz.tracing import configure_tracing
    import os
    if args.trace:
        os.environ["LANGSMITH_TRACING"] = "true"
    tracing = configure_tracing(cwd)
```

- [ ] **Step 3: Run all tests**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add src/localsmartz/__main__.py
git commit -m "feat: add --trace flag and configure_tracing() at CLI startup"
```

---

## Chunk Boundaries

| Chunk | Tasks | Review After |
|-------|-------|-------------|
| **Chunk 1** | Tasks 1-3 (all) | Yes — run full suite + verify tracing doesn't break without env vars |
