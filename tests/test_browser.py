"""Tests for Playwright browser scraping module."""

from localsmartz.tools.browser import _get_loop, _run_async, _browser_available, scrape_with_browser
import asyncio


def test_browser_available():
    """Returns True when playwright is installed."""
    assert _browser_available() is True


def test_get_loop_creates_running_loop():
    """_get_loop returns a running event loop on a daemon thread."""
    loop = _get_loop()
    assert loop.is_running()


def test_get_loop_is_idempotent():
    """_get_loop returns the same loop each time."""
    loop1 = _get_loop()
    loop2 = _get_loop()
    assert loop1 is loop2


def test_run_async_executes_coroutine():
    """_run_async can run a simple coroutine from sync code."""
    async def add(a, b):
        return a + b
    result = _run_async(add(3, 4))
    assert result == 7


def test_scrape_with_browser_data_uri():
    """scrape_with_browser works with a data: URI."""
    result = scrape_with_browser(
        "data:text/html,<html><body><article>Hello from Playwright</article></body></html>",
        timeout=5000,
    )
    assert isinstance(result, str)
    assert "Hello from Playwright" in result


def test_scrape_with_browser_error_returns_string():
    """scrape_with_browser returns error string on failure, never raises."""
    result = scrape_with_browser("http://localhost:1/nonexistent", timeout=3000)
    assert result.startswith("Error:")
