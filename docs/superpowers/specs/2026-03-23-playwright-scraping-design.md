# Local Smartz — Playwright-Powered Web Scraping

**Date:** 2026-03-23
**Scope:** Add Playwright-based scraping for JavaScript-rendered pages, falling back from httpx+bs4 when static scraping fails

## Context

Current `scrape_url()` uses httpx + BeautifulSoup — works for static HTML but fails on SPAs, AJAX-loaded content, and JS-rendered pages (common for news sites, dashboards, modern web apps). Adding Playwright gives the agent a real browser to handle these cases.

IBR (Interface Built Right) uses Playwright 1.51.0 for browser automation. Rather than depending on IBR directly, we add Playwright as a direct dependency — keeping the tools self-contained.

## Design

### Approach: Extend `scrape_url()` with auto-fallback

Not a new tool. The existing `scrape_url()` tries httpx first (fast, lightweight). If the result is suspiciously short (<100 chars of content), it automatically retries with Playwright. The agent doesn't need to know which method is used.

```
scrape_url(url) →
  1. Try httpx+bs4 (fast, <1s)
  2. If content < 100 chars → retry with Playwright (slow, 5-10s)
  3. Return best result
```

Optional `use_browser: bool = False` parameter for explicit Playwright requests.

### Profile gating

- **Full profile**: Playwright available (fallback + explicit `use_browser=True`)
- **Lite profile**: httpx only (Playwright too heavy for <16GB RAM machines)

The tool checks the profile at call time by accepting a `profile_name` parameter from the agent, rather than using a mutable module-level flag (which has thread-safety issues and can be stomped by concurrent `create_agent()` calls like the quality reviewer).

### Async handling

Playwright is async. Local Smartz tools are synchronous. Solution: a **persistent event loop on a daemon thread**. This avoids the nested `asyncio.run()` deadlock:

```python
import asyncio, threading

_loop = None
_loop_thread = None

def _get_loop():
    global _loop, _loop_thread
    if _loop is None:
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
        _loop_thread.start()
    return _loop

def _run_async(coro):
    """Run an async coroutine from sync code. Thread-safe."""
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result(timeout=30)
```

All Playwright operations go through `_run_async()`. The daemon thread keeps the loop alive for `atexit` cleanup. The daemon flag means it doesn't block process exit if cleanup fails.

**Why not `asyncio.run()`:** Calling `asyncio.run()` inside a function that's already inside `asyncio.run()` raises `RuntimeError: This event loop is already running`. The persistent loop avoids this entirely.

### Browser lifecycle

- Launch browser on first Playwright call (lazy init via `_run_async`)
- Reuse across multiple `scrape_url` calls in the same agent run
- Close via `atexit` handler using `_run_async` (loop is still alive on daemon thread)
- Headless by default

### Implementation

**1. Add dependency to `pyproject.toml`:**
```toml
"playwright>=1.51.0",
```

Note: After install, user must run `playwright install chromium` to get the browser binary. The `--setup` wizard should check for this.

**2. Create `src/localsmartz/tools/browser.py`:**

```python
"""Playwright-based browser scraping for JS-rendered pages."""

import asyncio
import atexit
import threading

_browser = None
_playwright = None
_loop = None
_loop_thread = None


def _get_loop():
    """Get or create a persistent event loop on a daemon thread."""
    global _loop, _loop_thread
    if _loop is None:
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
        _loop_thread.start()
    return _loop


def _run_async(coro):
    """Run an async coroutine from sync code. Thread-safe."""
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result(timeout=30)


async def _ensure_browser():
    """Lazy-init Playwright browser. Reused across calls."""
    global _browser, _playwright
    if _browser:
        return _browser

    from playwright.async_api import async_playwright
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=True)
    atexit.register(_cleanup_browser)
    return _browser


def _cleanup_browser():
    """Close browser on exit. Uses the persistent loop (still alive on daemon thread)."""
    global _browser, _playwright
    try:
        if _browser:
            _run_async(_browser.close())
        if _playwright:
            _run_async(_playwright.stop())
    except Exception:
        pass
    _browser = None
    _playwright = None


def scrape_with_browser(url: str, selector: str | None = None, wait_for: str | None = None, timeout: int = 15000) -> str:
    """Scrape a URL using a real browser (Playwright).

    Args:
        url: The URL to scrape
        selector: Optional CSS selector to extract specific content
        wait_for: Optional selector to wait for before extracting
        timeout: Page load timeout in milliseconds

    Returns:
        Extracted text content as markdown
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

                # Last resort: full page text
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

**3. Update `scrape_url()` in `src/localsmartz/tools/web.py`:**

Add auto-fallback after the httpx+bs4 attempt:

```python
# After existing content extraction, before returning:
if len(content_text.strip()) < 100 and _browser_available():
    # Content too short — likely JS-rendered, retry with browser
    from localsmartz.tools.browser import scrape_with_browser
    browser_content = scrape_with_browser(url, selector=selector)
    if len(browser_content.strip()) > len(content_text.strip()):
        content_text = browser_content
```

Add helper function:
```python
def _browser_available() -> bool:
    """Check if Playwright is installed (not profile-gated here — caller decides)."""
    try:
        import playwright
        return True
    except ImportError:
        return False
```

**4. Pass profile context to scrape_url:**

The agent's tool definition for `scrape_url` doesn't need to change — profile gating happens inside the function. Read the profile from `.localsmartz/config.json` at call time:

```python
# Inside scrape_url(), in the fallback section:
from localsmartz.config import load_config
config = load_config(Path.cwd())
is_full = config and config.get("profile") == "full"
if len(content_text.strip()) < 100 and is_full and _browser_available():
    # ...fallback to Playwright
```

This avoids any mutable module-level flags.

**5. Update `--setup` wizard to check Playwright:**

In the terminal wizard Step 1, after Ollama check:
```python
    # Check Playwright browsers
    try:
        import playwright
        # Check if chromium is installed
        import subprocess
        result = subprocess.run(
            ["playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True
        )
        if "already installed" not in result.stdout.lower():
            print("  Installing browser for web scraping...")
            subprocess.run(["playwright", "install", "chromium"], check=True)
    except ImportError:
        pass  # Playwright not installed — skip
```

### Interlock with LangSmith tracing

When LangSmith is enabled (separate spec), Playwright scrape calls are traced automatically because:
- `scrape_url()` is a registered LangChain tool
- LangSmith traces all tool invocations through the agent pipeline
- Browser scrape duration, input URL, and output content all appear in traces
- No additional integration needed

## Files to modify

| File | Changes |
|------|---------|
| `pyproject.toml` | Add `playwright>=1.51.0` |
| `src/localsmartz/tools/browser.py` | **New** — Playwright scraping module |
| `src/localsmartz/tools/web.py` | Add auto-fallback, `_browser_available()`, `_playwright_enabled` flag |
| `src/localsmartz/agent.py` | Set `_playwright_enabled` flag based on profile |
| `src/localsmartz/__main__.py` | Optional: Playwright install check in `--setup` |
| `tests/test_browser.py` | **New** — test browser scraping (requires `playwright install chromium`) |

## Testing

1. Unit test: `_browser_available()` returns False when Playwright not installed
2. Unit test: `scrape_url()` with short content triggers fallback (mock Playwright)
3. Unit test: `scrape_url()` with `use_browser=True` calls Playwright directly
4. Integration: scrape a known JS-heavy site (e.g., a React SPA) and verify content extraction
5. Verify lite profile never triggers Playwright

## Success criteria

- Static sites continue to use httpx+bs4 (fast path unchanged)
- JS-rendered sites automatically get Playwright fallback
- Lite profile never incurs browser overhead
- Browser is lazy-initialized and reused across calls
- Agent doesn't need to know which scraping method is used
- LangSmith traces capture both static and browser scrape calls
