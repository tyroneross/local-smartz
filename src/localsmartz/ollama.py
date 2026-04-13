"""Ollama health check, model validation, and setup helpers."""

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx


OLLAMA_BASE = "http://localhost:11434"


# Curated catalog of suggested models. Shown to users alongside "installed"
# so they can see what's available to pull without hunting on ollama.com.
# Size is the approximate on-disk estimate; actual will be reported for installed.
SUGGESTED_MODELS: list[dict] = [
    # Lite-class (fast, low RAM) ---------------------------------------------
    {"name": "qwen3:8b-q4_K_M",                   "size_gb_estimate": 5.2, "ram_class": "lite",  "note": "Fast general model (lite profile default)"},
    {"name": "llama3.2:3b",                       "size_gb_estimate": 2.0, "ram_class": "lite",  "note": "Small fast model, good for quick queries"},
    {"name": "phi3:mini",                         "size_gb_estimate": 2.3, "ram_class": "lite",  "note": "Microsoft Phi-3 mini"},
    {"name": "gemma2:9b",                         "size_gb_estimate": 5.4, "ram_class": "lite",  "note": "Google Gemma 2 9B"},
    # Full-class (mid range, 16-32GB+ RAM) -----------------------------------
    {"name": "qwen2.5-coder:32b-instruct-q5_K_M", "size_gb_estimate": 23.0, "ram_class": "full", "note": "Strong code/agent model (default execution)"},
    {"name": "gpt-oss:20b",                       "size_gb_estimate": 14.0, "ram_class": "full", "note": "OSS-tuned model"},
    # Heavy (64GB+ RAM) ------------------------------------------------------
    {"name": "llama3.1:70b-instruct-q5_K_M",      "size_gb_estimate": 48.0, "ram_class": "heavy","note": "Full profile default planning model"},
    {"name": "gpt-oss:120b",                      "size_gb_estimate": 65.0, "ram_class": "heavy","note": "Largest OSS model"},
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
