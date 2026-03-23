# Playwright Scraping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Playwright-based scraping with auto-fallback for JS-rendered pages

**Architecture:** Persistent event loop on daemon thread, lazy browser init, profile-gated fallback when httpx content is too short

**Tech Stack:** Python, playwright, pytest

**Spec:** `docs/superpowers/specs/2026-03-23-playwright-scraping-design.md`

---

### Task 1: Add playwright dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add playwright to pyproject.toml**

In `pyproject.toml`, in the `dependencies` list, add:
```toml
    "playwright>=1.51.0",
```

- [ ] **Step 2: Install and get browser**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/pip install -e . --quiet
.venv/bin/playwright install chromium
```

- [ ] **Step 3: Verify**

```bash
.venv/bin/python -c "from playwright.async_api import async_playwright; print('playwright OK')"
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add playwright dependency for browser-based scraping"
```

---

### Task 2: Create browser.py module

**Files:**
- Create: `src/localsmartz/tools/browser.py`
- Create: `tests/test_browser.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_browser.py
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio


def test_browser_available():
    """_browser_available returns True when playwright is installed."""
    from localsmartz.tools.browser import _browser_available
    assert _browser_available() is True


def test_scrape_with_browser_returns_string():
    """scrape_with_browser returns a string result."""
    from localsmartz.tools.browser import scrape_with_browser
    # Test against a known static page (data: URI)
    result = scrape_with_browser("data:text/html,<html><body><h1>Test</h1><p>Hello world</p></body></html>")
    assert isinstance(result, str)
    assert "Hello world" in result or "Test" in result


def test_scrape_with_browser_error_handling():
    """scrape_with_browser returns error string on failure."""
    from localsmartz.tools.browser import scrape_with_browser
    result = scrape_with_browser("http://localhost:99999/nonexistent", timeout=3000)
    assert result.startswith("Error:")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_browser.py -v
```

- [ ] **Step 3: Create browser.py**

```python
# src/localsmartz/tools/browser.py
"""Playwright-based browser scraping for JS-rendered pages.

Uses a persistent event loop on a daemon thread to avoid asyncio.run()
deadlocks. Browser is lazy-initialized and reused across calls.
"""

import asyncio
import atexit
import threading

_browser = None
_playwright_instance = None
_loop = None
_loop_thread = None


def _get_loop():
    """Get or create a persistent event loop on a daemon thread."""
    global _loop, _loop_thread
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
        _loop_thread.start()
    return _loop


def _run_async(coro):
    """Run an async coroutine from sync code. Thread-safe."""
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result(timeout=60)


async def _ensure_browser():
    """Lazy-init Playwright browser. Reused across calls."""
    global _browser, _playwright_instance
    if _browser and _browser.is_connected():
        return _browser

    from playwright.async_api import async_playwright
    _playwright_instance = await async_playwright().start()
    _browser = await _playwright_instance.chromium.launch(headless=True)
    atexit.register(_cleanup_browser)
    return _browser


def _cleanup_browser():
    """Close browser on exit. Uses persistent loop (still alive on daemon thread)."""
    global _browser, _playwright_instance
    try:
        if _browser:
            _run_async(_browser.close())
        if _playwright_instance:
            _run_async(_playwright_instance.stop())
    except Exception:
        pass
    _browser = None
    _playwright_instance = None


def _browser_available() -> bool:
    """Check if Playwright is installed."""
    try:
        import playwright
        return True
    except ImportError:
        return False


def scrape_with_browser(
    url: str,
    selector: str | None = None,
    wait_for: str | None = None,
    timeout: int = 15000,
) -> str:
    """Scrape a URL using a real browser (Playwright).

    Args:
        url: The URL to scrape
        selector: Optional CSS selector — returns ALL matches joined with newlines
        wait_for: Optional selector to wait for before extracting
        timeout: Page load timeout in milliseconds

    Returns:
        Extracted text content
    """
    async def _scrape():
        browser = await _ensure_browser()
        page = await browser.new_page()
        try:
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")

            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=timeout)
                except Exception:
                    pass  # Continue with what loaded

            if selector:
                elements = await page.query_selector_all(selector)
                texts = []
                for el in elements:
                    text = await el.inner_text()
                    if text.strip():
                        texts.append(text.strip())
                return "\n\n".join(texts) if texts else ""
            else:
                # Get main content — prefer article/main, fallback to body
                for sel in ["article", "main", "[role='main']", "body"]:
                    el = await page.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        if len(text.strip()) > 100:
                            title = await page.title()
                            return f"# {title}\n\n{text.strip()}"

                text = await page.inner_text("body")
                title = await page.title()
                return f"# {title}\n\n{text.strip()}"
        finally:
            await page.close()

    try:
        return _run_async(_scrape())
    except Exception as e:
        return f"Error: Browser scrape failed: {e}"
```

- [ ] **Step 4: Run tests**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/test_browser.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/localsmartz/tools/browser.py tests/test_browser.py
git commit -m "feat: add Playwright browser scraping module with persistent event loop"
```

---

### Task 3: Add auto-fallback to scrape_url

**Files:**
- Modify: `src/localsmartz/tools/web.py`

- [ ] **Step 1: Add `use_browser` parameter and fallback logic**

In `src/localsmartz/tools/web.py`, update the `scrape_url` function signature:

```python
def scrape_url(url: str, extract_tables: bool = False, selector: str | None = None, use_browser: bool = False) -> str:
```

At the TOP of the function, add explicit browser path:
```python
    # Explicit browser request
    if use_browser:
        from localsmartz.tools.browser import scrape_with_browser, _browser_available
        if _browser_available():
            return scrape_with_browser(url, selector=selector)
        return "Error: Playwright not installed. Run: pip install playwright && playwright install chromium"
```

Before the final `return` at the end of the function, add the auto-fallback:
```python
    # Auto-fallback: if content is suspiciously short, retry with browser (full profile only)
    if len(content_text.strip()) < 500:
        from localsmartz.tools.browser import _browser_available
        if _browser_available():
            from localsmartz.config import load_config
            config = load_config(Path.cwd())
            is_full = config and config.get("profile") == "full"
            if is_full:
                from localsmartz.tools.browser import scrape_with_browser
                browser_content = scrape_with_browser(url, selector=selector)
                if len(browser_content.strip()) > len(content_text.strip()):
                    content_text = browser_content
```

- [ ] **Step 2: Run all tests**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add src/localsmartz/tools/web.py
git commit -m "feat: add auto-fallback to Playwright when httpx content is too short"
```

---

### Task 4: Update setup wizard for Playwright check

**Files:**
- Modify: `src/localsmartz/__main__.py`

- [ ] **Step 1: Add Playwright browser check to terminal wizard Step 1**

In `_setup()`, after the Ollama status check (after "Ollama running" print), add:

```python
    # Check Playwright browser
    try:
        import playwright
        from pathlib import Path as _P
        import os as _os
        cache_dir = _P(_os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH",
            _P.home() / ".cache" / "ms-playwright"
        ))
        chromium_dirs = list(cache_dir.glob("chromium-*")) if cache_dir.exists() else []
        if not chromium_dirs:
            print("  Installing browser for web scraping...")
            import subprocess
            subprocess.run(["playwright", "install", "chromium"], check=True)
            print(f"  \033[32m\u2713\033[0m Browser installed")
        else:
            print(f"  \033[32m\u2713\033[0m Browser: ready")
    except ImportError:
        pass  # Playwright not installed — skip
```

- [ ] **Step 2: Run all tests**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add src/localsmartz/__main__.py
git commit -m "feat: add Playwright browser check to setup wizard"
```

---

### Task 5: E2E verification

- [ ] **Step 1: Run full test suite**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -m pytest tests/ -v --tb=short
```

- [ ] **Step 2: Test browser scraping directly**

```bash
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -c "
from localsmartz.tools.browser import scrape_with_browser
result = scrape_with_browser('https://example.com')
print(f'Length: {len(result)} chars')
print(result[:200])
"
```

- [ ] **Step 3: Test auto-fallback**

```bash
# Test with a JS-heavy site (would fail with httpx, succeed with Playwright)
cd ~/Desktop/git-folder/local-smartz && .venv/bin/python -c "
from localsmartz.tools.web import scrape_url
result = scrape_url('https://example.com')
print(f'Length: {len(result)} chars')
print(result[:200])
"
```

- [ ] **Step 4: Commit any fixes**

---

## Chunk Boundaries

| Chunk | Tasks | Review After |
|-------|-------|-------------|
| **Chunk 1** | Tasks 1-2 (dependency + browser module) | Yes — verify browser tests pass |
| **Chunk 2** | Tasks 3-5 (fallback + setup + E2E) | Yes — full verification |

## Interlock with LangSmith

Both features add to `pyproject.toml`. If implementing in parallel, merge the dependency additions. The LangSmith plan should be executed first (simpler, no async complexity) — its dependency addition to `pyproject.toml` will already be committed when the Playwright plan runs.
