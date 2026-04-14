"""Ollama health check, model validation, and setup helpers."""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import httpx


OLLAMA_BASE = "http://localhost:11434"


# Load-bearing models that the product directly references (see
# ``profiles.PROFILES`` for planning + execution defaults). This short
# list is the **fallback** the UI shows when the Ollama library scrape
# is unreachable — the live "Popular on Ollama" list is fetched from
# ``ollama.com/search`` by ``ollama_library.get_popular`` and cached to
# ``.localsmartz/library-cache.json``.
#
# Why not a big hand-curated list? It ages. Gemma 4 launched, gemma3n
# launched, qwen3.5 launched — none showed up in the old 19-model list
# because no one remembered to edit it. The scraped list catches new
# releases automatically, ranks by pull count, and de-dupes by family
# so newer versions replace older ones.
SUGGESTED_MODELS: list[dict] = [
    {"name": "qwen3:8b-q4_K_M",                   "size_gb_estimate": 5.2,  "ram_class": "lite",  "note": "Lite/full planning default"},
    {"name": "qwen2.5-coder:32b-instruct-q5_K_M", "size_gb_estimate": 23.0, "ram_class": "full",  "note": "Full execution default"},
    {"name": "llama3.3:70b-instruct-q5_K_M",      "size_gb_estimate": 48.0, "ram_class": "heavy", "note": "Heavy-tier alternative"},
    {"name": "gpt-oss:120b",                      "size_gb_estimate": 65.0, "ram_class": "heavy", "note": "Largest OSS model supported"},
]


def ollama_models_path() -> Path:
    """Return the path Ollama uses to store models.

    Respects the OLLAMA_MODELS env var (user override), else falls back to
    the platform default (~/.ollama/models on macOS and Linux).
    """
    override = os.environ.get("OLLAMA_MODELS")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ollama" / "models"


def ollama_disk_usage_bytes() -> int:
    """Total bytes on disk used by Ollama's models directory (best-effort)."""
    root = ollama_models_path()
    if not root.exists():
        return 0
    total = 0
    for entry in root.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def check_server() -> bool:
    """Check if Ollama is running. Returns True if healthy."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/version", timeout=3.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def get_version() -> str | None:
    """Get Ollama version string, or None if not available."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/version", timeout=3.0)
        return resp.json().get("version")
    except Exception:
        return None


def is_installed() -> bool:
    """Check if ollama binary is on PATH."""
    return shutil.which("ollama") is not None


def list_models() -> list[str]:
    """List models available in Ollama. Returns empty list on error."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def list_models_with_size() -> list[tuple[str, float]]:
    """List models with on-disk size in GB, sorted by size ascending.

    Returns:
        List of (model_name, size_gb) tuples. Empty list on error.
    """
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        models = [
            (m["name"], m.get("size", 0) / 1e9)
            for m in data.get("models", [])
        ]
        models.sort(key=lambda x: x[1])
        return models
    except Exception:
        return []


def model_available(model_name: str) -> bool:
    """Check if a specific model is pulled in Ollama.

    Handles flexible matching:
    - Exact: 'qwen3:8b-q4_K_M' matches 'qwen3:8b-q4_K_M'
    - Base family: 'qwen3:8b-q4_K_M' matches if 'qwen3:8b' is pulled
    """
    available = list_models()
    if model_name in available:
        return True
    # Check if any pulled model shares the same base
    model_base = model_name.split(":")[0] if ":" in model_name else model_name
    model_variant = model_name.split(":")[1].split("-")[0] if ":" in model_name else ""
    for m in available:
        m_base = m.split(":")[0] if ":" in m else m
        m_variant = m.split(":")[1].split("-")[0] if ":" in m else ""
        if m_base == model_base and m_variant == model_variant:
            return True
    return False


def suggest_pull(model_name: str) -> str:
    """Generate an ollama pull command."""
    return f"ollama pull {model_name}"


def warmup_model(
    model_name: str,
    keep_alive: str = "30m",
    timeout: float = 300.0,
) -> tuple[bool, int, str | None]:
    """Pre-load a model into Ollama's VRAM so the first real query is fast.

    POSTs an empty-prompt /api/generate request with ``keep_alive`` set so the
    model stays resident. Idempotent — Ollama returns immediately when the
    model is already loaded.

    ``keep_alive``:
      - ``"30m"`` (default): keep the model resident for 30 minutes idle.
      - ``"-1"``: keep resident forever — used for the active planning model
        so the first query after launch is warm and subsequent idle
        stretches never evict. Pair with ``evict_model()`` on model switch
        to free VRAM.
      - ``"0"``: immediately unload — see ``evict_model`` for the helper.

    Returns:
        (ok, duration_ms, error_message). On timeout or any HTTP/network
        error, ok is False and error_message describes the failure. Never
        raises — callers can decide whether to surface or swallow.
    """
    start = time.time()
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": model_name,
                "prompt": "",
                "keep_alive": keep_alive,
                "stream": False,
            },
            timeout=timeout,
        )
        elapsed_ms = int((time.time() - start) * 1000)
        if resp.status_code == 200:
            return True, elapsed_ms, None
        # Ollama returns 404 when a model isn't pulled. Surface the body
        # so callers can fall back cleanly.
        return False, elapsed_ms, f"Ollama returned {resp.status_code}: {resp.text[:200]}"
    except httpx.TimeoutException:
        elapsed_ms = int((time.time() - start) * 1000)
        return False, elapsed_ms, f"Warmup timed out after {timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001 — broad catch, we surface via return
        elapsed_ms = int((time.time() - start) * 1000)
        return False, elapsed_ms, f"Warmup failed: {exc}"


def evict_model(
    model_name: str,
    timeout: float = 30.0,
) -> tuple[bool, str | None]:
    """Force Ollama to unload ``model_name`` from VRAM.

    POSTs ``/api/generate`` with ``keep_alive: 0`` and an empty prompt. Ollama
    interprets zero keep-alive as "drop from memory immediately after this
    request." Used when switching the active planning model so the new
    model has VRAM to load into on smaller machines.

    Returns ``(ok, error_message)``. Never raises.
    """
    if not model_name:
        return False, "no model name provided"
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": model_name,
                "prompt": "",
                "keep_alive": 0,
                "stream": False,
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            return True, None
        # 404 usually means the model isn't pulled — treat as a soft success
        # since there's nothing resident to evict.
        if resp.status_code == 404:
            return True, None
        return False, f"Ollama returned {resp.status_code}: {resp.text[:200]}"
    except httpx.TimeoutException:
        return False, f"Evict timed out after {timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001 — surface via return
        return False, f"Evict failed: {exc}"


def resolve_available_model(
    requested: str, min_gb: float = 1.0
) -> tuple[str | None, str | None]:
    """Resolve a possibly-missing model to one that is actually pulled.

    Returns (model_name, warning_or_error):
      - (requested, None) if requested is available
      - (substitute, warning) if requested is missing but a >=min_gb substitute exists
      - (None, error) if Ollama is down or no usable model is available
    """
    if not check_server():
        return None, "Ollama is not running. Start it with: ollama serve"
    if model_available(requested):
        return requested, None
    candidates = [(n, s) for n, s in list_models_with_size() if s >= min_gb]
    if not candidates:
        return (
            None,
            f"Model '{requested}' not pulled and no other suitable model found. "
            f"Pull one with: {suggest_pull(requested)}",
        )
    chosen = candidates[-1][0]  # largest available
    warning = (
        f"Model '{requested}' not pulled — using '{chosen}' instead. "
        f"For the recommended model: {suggest_pull(requested)}"
    )
    return chosen, warning


def validate_for_profile(profile: dict) -> tuple[bool, list[str]]:
    """Validate Ollama is ready for a given profile.

    Returns:
        (ok, messages) — ok is True if ready, messages are status/error strings
    """
    messages = []

    if not is_installed():
        messages.append("Ollama is not installed.")
        messages.append("  Install: https://ollama.com/download")
        if platform.system() == "Darwin":
            messages.append("  Or: brew install ollama")
        messages.append("  Then run: localsmartz --setup")
        return False, messages

    if not check_server():
        messages.append("Ollama is installed but not running.")
        messages.append("  Start it with: ollama serve")
        messages.append("  Or open the Ollama app.")
        return False, messages

    version = get_version()
    messages.append(f"Ollama: running (v{version})" if version else "Ollama: running")

    planning_model = profile["planning_model"]
    execution_model = profile["execution_model"]
    models_to_check = [(planning_model, "Planning")]
    if execution_model != planning_model:
        models_to_check.append((execution_model, "Execution"))

    ok = True
    for model, label in models_to_check:
        if model_available(model):
            messages.append(f"  {label} model ({model}): ready")
        else:
            messages.append(f"  {label} model ({model}): not found")
            messages.append(f"    → {suggest_pull(model)}")
            ok = False

    return ok, messages


def pull_model(model_name: str) -> bool:
    """Pull a model via ollama CLI. Shows progress. Returns True on success."""
    print(f"Pulling {model_name}...", file=sys.stderr)
    try:
        result = subprocess.run(
            ["ollama", "pull", model_name],
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        print("Error: ollama command not found", file=sys.stderr)
        return False


def stream_pull(
    model_name: str,
    insecure: bool = False,
    timeout: float = 7200.0,
) -> Iterator[dict]:
    """Yield NDJSON progress dicts from Ollama's ``/api/pull`` endpoint.

    Docs: https://docs.ollama.com/api/pull

    Each yielded dict may include:
      - ``status``: "pulling manifest" / "downloading <digest>" / "verifying" /
        "success" (a trailing ``{"status": "success"}`` marks completion — the
        generator re-yields it and then exits; callers should treat it as
        the terminal event rather than wait for StopIteration alone).
      - ``digest``: layer digest (when streaming a blob).
      - ``total``: bytes expected for the current layer (if known).
      - ``completed``: bytes transferred so far (if streaming a blob).
      - ``error``: non-empty when Ollama reports a pull failure mid-stream;
        Ollama keeps HTTP 200 and encodes failures in-band, so callers must
        inspect each chunk — a non-2xx status code only happens for
        up-front failures like an unreachable daemon or malformed body.

    Raises ``RuntimeError`` on HTTP error status; httpx exceptions propagate
    (ConnectError when Ollama is down, TimeoutException on stalls). The
    default 2-hour timeout covers pulls of the largest supported models on
    slow links — callers should override for tighter bounds.

    Safe to call for arbitrary names; Ollama returns 404 for unknown ones,
    which surfaces here as ``RuntimeError``.
    """
    body = {"model": model_name, "stream": True, "insecure": insecure}
    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", f"{OLLAMA_BASE}/api/pull", json=body) as resp:
            if resp.status_code >= 400:
                # Drain enough of the body to put it in the error message
                # without blocking on a huge response.
                try:
                    detail = resp.read().decode("utf-8", errors="replace")[:500]
                except Exception:  # noqa: BLE001
                    detail = ""
                raise RuntimeError(
                    f"Ollama /api/pull returned {resp.status_code}: {detail}"
                )
            for line in resp.iter_lines():
                if not line:
                    continue
                # httpx's iter_lines() yields str by default; be defensive in
                # case a future version flips to bytes.
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed lines rather than aborting the pull —
                    # upstream has been known to emit stray whitespace.
                    continue


def list_running_models() -> list[dict]:
    """List models currently loaded in Ollama VRAM via ``/api/ps``.

    Docs: https://docs.ollama.com/api/ps

    Returns a list of dicts with keys: ``name``, ``model``, ``size``,
    ``size_vram``, ``expires_at`` (RFC3339), ``context_length`` (plus
    ``digest`` and ``details`` when Ollama includes them). Empty list on
    any error — never raises, since callers use this for opportunistic UI
    (e.g. "show what's hot") and must not crash when the daemon is down.
    """
    try:
        resp = httpx.get(f"{OLLAMA_BASE}/api/ps", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        models = data.get("models") or []
        # Defensive: the API always wraps in {"models": [...]}, but return
        # [] rather than raise if the shape is unexpected.
        return list(models) if isinstance(models, list) else []
    except Exception:  # noqa: BLE001 — soft failure is the contract
        return []


def show_model(model_name: str) -> dict | None:
    """Fetch model metadata from ``/api/show``.

    Docs: https://docs.ollama.com/api-reference/show-model-details

    Returns a dict with the full response — ``modelfile``, ``parameters``,
    ``template``, ``license``, ``modified_at``, ``details``
    (``parameter_size``, ``quantization_level``, ``format``, ``family``,
    ``families``, ``parent_model``), ``model_info``, and ``capabilities``.

    Returns ``None`` if the model isn't pulled (Ollama returns 404) or if
    Ollama errors for any other reason — this is a read-only metadata
    lookup, so soft-fail is the right posture.
    """
    if not model_name:
        return None
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/show",
            json={"model": model_name},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:  # noqa: BLE001 — soft failure is the contract
        return None


def delete_model(model_name: str) -> tuple[bool, str | None]:
    """Delete a pulled model from Ollama via HTTP ``DELETE /api/delete``.

    Docs: https://docs.ollama.com/api/delete

    Preferred over ``subprocess.run(["ollama", "rm", ...])`` because it
    works when the CLI isn't on PATH (e.g. the packaged .app on macOS) and
    gives us structured error handling.

    Returns ``(ok, error_message)``. Idempotent — a 404 is reported as a
    soft success (nothing to delete), matching ``ollama rm``'s behavior
    for already-gone models.
    """
    if not model_name:
        return False, "no model name provided"
    try:
        resp = httpx.request(
            "DELETE",
            f"{OLLAMA_BASE}/api/delete",
            json={"model": model_name},
            timeout=30.0,
        )
        if resp.status_code == 200:
            return True, None
        if resp.status_code == 404:
            # Idempotent: nothing to delete is success.
            return True, None
        return False, f"Ollama returned {resp.status_code}: {resp.text[:200]}"
    except httpx.TimeoutException:
        return False, "Delete timed out after 30s"
    except Exception as exc:  # noqa: BLE001 — surface via return
        return False, f"Delete failed: {exc}"


def setup(profile: dict) -> bool:
    """Interactive setup — check Ollama, pull missing models.

    Returns True if everything is ready after setup.
    """
    print("Local Smartz Setup")
    print("=" * 40)
    print()

    # Step 1: Check Ollama installation
    if not is_installed():
        print("Ollama is not installed.")
        print()
        if platform.system() == "Darwin":
            print("Install options:")
            print("  1. Download from https://ollama.com/download")
            print("  2. brew install ollama")
        else:
            print("Install from: https://ollama.com/download")
        print()
        print("After installing, run: localsmartz --setup")
        return False

    print("Ollama: installed")

    # Step 2: Check if server is running
    if not check_server():
        print("Ollama server is not running. Starting...")
        # Try to start ollama serve in background
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Wait for it to come up
            for _ in range(10):
                time.sleep(1)
                if check_server():
                    break
        except Exception:
            pass

        if not check_server():
            print("Could not start Ollama. Please start it manually:")
            print("  ollama serve")
            return False

    version = get_version()
    print(f"Ollama: running (v{version})" if version else "Ollama: running")
    print()

    # Step 3: Check and pull models
    print(f"Profile: {profile['name']}")
    planning_model = profile["planning_model"]
    execution_model = profile["execution_model"]

    models_needed = [planning_model]
    if execution_model != planning_model:
        models_needed.append(execution_model)

    all_ready = True
    for model in models_needed:
        if model_available(model):
            print(f"  {model}: ready")
        else:
            print(f"  {model}: not found — downloading...")
            if pull_model(model):
                print(f"  {model}: ready")
            else:
                print(f"  {model}: FAILED to download")
                all_ready = False

    print()
    if all_ready:
        print("Setup complete. Run: localsmartz \"your research question\"")
    else:
        print("Some models failed to download. Try manually:")
        for model in models_needed:
            if not model_available(model):
                print(f"  {suggest_pull(model)}")

    return all_ready
