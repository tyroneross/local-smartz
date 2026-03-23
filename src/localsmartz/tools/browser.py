"""Playwright-based browser scraping for JS-rendered pages.

Uses a persistent event loop on a daemon thread to avoid asyncio.run() deadlocks.
Browser is lazy-initialized and reused across calls.
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
                    pass

            if selector:
                elements = await page.query_selector_all(selector)
                texts = []
                for el in elements:
                    text = await el.inner_text()
                    if text.strip():
                        texts.append(text.strip())
                return "\n\n".join(texts) if texts else ""
            else:
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
