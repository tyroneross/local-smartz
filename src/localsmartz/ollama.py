"""Ollama health check, model validation, and setup helpers."""

import platform
import shutil
import subprocess
import sys
import time

import httpx


OLLAMA_BASE = "http://localhost:11434"


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
