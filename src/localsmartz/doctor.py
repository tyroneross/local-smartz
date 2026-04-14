"""Diagnostic health-check matrix for `localsmartz doctor`.

Single public entry point: ``run_doctor()`` returns a list of
``(name, status, hint)`` tuples where ``status`` is one of
``"ok"`` / ``"fail"`` / ``"skip"``. Kept intentionally small and
stdlib-only — no httpx, no external logging.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from localsmartz.profiles import is_fast_path

_OK = "ok"
_FAIL = "fail"
_SKIP = "skip"

_MODEL_PREFIXES = ("qwen3:8b-", "qwen2.5-coder:32b-", "llama3.2:3b", "gpt-oss:")
_BACKEND_PORTS = (11435, 11436)
_CONNECT_TIMEOUT = 2.0
_READ_TIMEOUT = 8.0


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
    """Return (status_code, body) — or (0, b"") on any error."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode() or 0, resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except Exception:
            return e.code, b""
    except Exception:
        return 0, b""


def _check_ollama_reachable() -> tuple[str, str, str]:
    code, _body = _http_get("http://localhost:11434/api/tags", timeout=_CONNECT_TIMEOUT)
    if code == 200:
        return ("ollama_reachable", _OK, "http://localhost:11434 responded 200")
    return (
        "ollama_reachable",
        _FAIL,
        "Start Ollama: `brew services start ollama` or `ollama serve`",
    )


def _check_models_present() -> tuple[str, str, str]:
    code, body = _http_get("http://localhost:11434/api/tags", timeout=_CONNECT_TIMEOUT)
    if code != 200:
        return ("models_present", _SKIP, "ollama not reachable")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        names = [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return ("models_present", _FAIL, "could not parse /api/tags response")
    for n in names:
        if any(n.startswith(p) for p in _MODEL_PREFIXES):
            return ("models_present", _OK, f"found {n}")
    return (
        "models_present",
        _FAIL,
        "Install a model: `ollama pull qwen3:8b-q4_K_M`",
    )


def _find_backend() -> int | None:
    for port in _BACKEND_PORTS:
        for path in ("/api/health", "/health"):
            code, _ = _http_get(f"http://localhost:{port}{path}", timeout=_CONNECT_TIMEOUT)
            if code == 200:
                return port
    return None


def _check_backend_up(port: int | None) -> tuple[str, str, str]:
    if port is not None:
        return ("backend_up", _OK, f"backend responding on :{port}")
    return (
        "backend_up",
        _SKIP,
        "Backend not running (start: `localsmartz --serve` or launch app)",
    )


def _check_sse_healthy(port: int | None) -> tuple[str, str, str]:
    if port is None:
        return ("sse_healthy", _SKIP, "backend not up")
    payload = json.dumps({"prompt": "what is 2 plus 2"}).encode("utf-8")
    req = urllib.request.Request(
        f"http://localhost:{port}/api/research",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_READ_TIMEOUT) as resp:
            if resp.getcode() != 200:
                return ("sse_healthy", _FAIL, f"HTTP {resp.getcode()} from /api/research")
            # Scan stream for at least one "event: text" or "event: done" line.
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith("event:"):
                    kind = line.split(":", 1)[1].strip()
                    if kind in ("text", "done"):
                        return ("sse_healthy", _OK, f"received event: {kind}")
    except Exception as e:
        return (
            "sse_healthy",
            _FAIL,
            f"Backend responded but stream errored ({e.__class__.__name__}) — check `localsmartz logs`",
        )
    return (
        "sse_healthy",
        _FAIL,
        "Backend responded but stream errored — check `localsmartz logs`",
    )


def _check_fast_path_classifier() -> tuple[str, str, str]:
    try:
        positive = is_fast_path("what is 2+2") is True
        negative = is_fast_path("research the economy") is False
    except Exception as e:
        return ("fast_path_classifier", _FAIL, f"is_fast_path raised: {e}")
    if positive and negative:
        return ("fast_path_classifier", _OK, "positive + negative cases classified correctly")
    return (
        "fast_path_classifier",
        _FAIL,
        "profiles.is_fast_path regression — run pytest",
    )


def run_doctor() -> list[tuple[str, str, str]]:
    """Run all health checks in order and return their results."""
    results: list[tuple[str, str, str]] = []
    results.append(_check_ollama_reachable())
    results.append(_check_models_present())
    port = _find_backend()
    results.append(_check_backend_up(port))
    results.append(_check_sse_healthy(port))
    results.append(_check_fast_path_classifier())
    return results
