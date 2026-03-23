"""CLI entry point: python -m localsmartz"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

try:
    import readline  # noqa: F401 — enables arrow keys + history in input()
except ImportError:
    pass  # Windows fallback — input() still works, just no history


def main():
    parser = argparse.ArgumentParser(
        prog="localsmartz",
        description="Local-first multi-agent research system powered by Ollama",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Research question or task",
    )
    parser.add_argument(
        "--profile",
        choices=["full", "lite"],
        default=None,
        help="Hardware profile (auto-detected if omitted)",
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="Ollama model to use (overrides profile default)",
    )
    parser.add_argument(
        "--thread",
        type=str,
        default=None,
        help="Thread ID for context retention (resume or name a thread)",
    )
    parser.add_argument(
        "--cwd",
        type=str,
        default=None,
        help="Working directory for file operations",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=str,
        default=None,
        help="Directory for output artifacts",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only output the final result",
    )
    parser.add_argument(
        "--list-threads",
        action="store_true",
        help="List all research threads and exit",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check Ollama status and model availability, then exit",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Install/configure Ollama and download required models",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start HTTP server for the macOS app",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=11435,
        help="Server port (default: 11435, used with --serve)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    args = parser.parse_args()
    cwd = Path(args.cwd) if args.cwd else Path.cwd()

    # List threads mode
    if args.list_threads:
        from localsmartz.threads import list_threads
        threads = list_threads(str(cwd))
        if not threads:
            print("No threads found.")
        else:
            print(f"{'ID':<30} {'Title':<40} {'Entries':>7}")
            print("-" * 79)
            for t in threads:
                print(f"{t['id']:<30} {t.get('title', '')[:40]:<40} {t.get('entry_count', 0):>7}")
        sys.exit(0)

    # Health check mode
    if args.check:
        _check(args)
        sys.exit(0)

    # Setup mode
    if args.setup:
        _setup(args)
        sys.exit(0)

    # Server mode
    if args.serve:
        from localsmartz.serve import start_server
        start_server(port=args.port, profile_name=args.profile)
        sys.exit(0)

    # Join positional args as prompt
    prompt = " ".join(args.prompt).strip() if args.prompt else ""

    if not prompt:
        if sys.stdin.isatty():
            _interactive(args, cwd)
            sys.exit(0)
        else:
            prompt = sys.stdin.read().strip()
            if not prompt:
                print("Error: No prompt provided", file=sys.stderr)
                sys.exit(1)

    try:
        _run(prompt, args, cwd)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _check(args):
    """Run Ollama health check and report status."""
    from localsmartz.profiles import get_profile
    from localsmartz.ollama import validate_for_profile

    profile = get_profile(args.profile, model_override=args.model)
    print(f"Profile: {profile['name']}")
    print(f"Planning model: {profile['planning_model']}")
    print(f"Execution model: {profile['execution_model']}")
    print()

    ok, messages = validate_for_profile(profile)
    for msg in messages:
        print(msg)

    if ok:
        print("\nReady to go.")
    else:
        print("\nNot ready — run: localsmartz --setup")
        sys.exit(1)


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
        print("  \033[31m\u2717\033[0m Ollama is not installed.")
        print("\n  Install Ollama:")
        print("    macOS:  Download from https://ollama.com/download")
        print("    Linux:  curl -fsSL https://ollama.ai/install.sh | sh")
        sys.exit(1)

    while not check_server():
        print("  \033[31m\u2717\033[0m Ollama is not running.")
        if not interactive:
            print("  Start Ollama manually: ollama serve")
            sys.exit(1)
        input("  Start Ollama, then press Enter to check again... ")

    version = get_version()
    ram_gb = get_ram_gb()
    profile_name = detect_profile()
    v_str = f" (v{version})" if version else ""
    print(f"  \033[32m\u2713\033[0m Ollama running{v_str}")
    if ram_gb:
        print(f"  \033[32m\u2713\033[0m {ram_gb} GB RAM \u2014 {profile_name} profile")

    # Step 2: Choose model
    print("\n  [2/4] Choose a model:\n")
    models = list_models_with_size()

    if models:
        print("  Already downloaded:")
        for i, (name, size) in enumerate(models):
            rec = "  \033[94m\u2190 recommended\033[0m" if i == len(models) - 1 else ""
            print(f"    {i + 1}. {name:<30s} ({size:.1f} GB){rec}")
    else:
        print("  No models downloaded yet.")
        print("  Downloading recommended model...")
        rec_model = "qwen3:8b-q4_K_M" if ram_gb < 64 else "llama3.1:70b-instruct-q5_K_M"
        pull_model(rec_model)
        models = list_models_with_size()
        print(f"  \033[32m\u2713\033[0m Downloaded {rec_model}")

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
        print(f"  \033[32m\u2713\033[0m Model: {selected_model}")

    # Step 3: Workspace
    print(f"\n  [3/4] Workspace folder")
    if interactive:
        default_ws = str(cwd)
        print(f"  Where are the files you want to research?")
        try:
            ws_input = input(f"  Default: {default_ws}\n  > ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(130)
        if ws_input and ws_input != default_ws:
            ws_path = Path(ws_input).expanduser()
            if ws_path.is_dir():
                add_folder(cwd, ws_input)
                print(f"  \033[32m\u2713\033[0m Added: {ws_input}")
            else:
                print(f"  \033[33m!\033[0m Not a directory, skipping")
        print(f"  \033[32m\u2713\033[0m Workspace: {default_ws}")

        while True:
            try:
                extra = input("  Add another folder? (path or Enter to skip) > ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                break
            if not extra:
                break
            extra_path = Path(extra).expanduser()
            if extra_path.is_dir():
                add_folder(cwd, extra)
                print(f"  \033[32m\u2713\033[0m Added: {extra}")
            else:
                print(f"  \033[33m!\033[0m Not a directory")
    else:
        print(f"  \033[32m\u2713\033[0m Workspace: {cwd} (non-interactive, using default)")

    # Step 4: Test
    print(f"\n  [4/4] Testing...")
    if selected_model and interactive:
        print('  Query: "What is artificial intelligence?"')
        try:
            profile = get_profile(profile_name, model_override=selected_model)
            from localsmartz.agent import create_agent
            agent = create_agent(profile)
            result = agent.invoke(
                {"messages": [{"role": "user", "content": "What is artificial intelligence? Answer in one sentence."}]}
            )
            response = result.get("messages", [{}])[-1].get("content", "No response")
            if len(response) > 200:
                response = response[:200] + "..."
            print(f"  \u2192 {response}")
            print("  \033[32m\u2713\033[0m Working!")
        except Exception as e:
            print(f"  \033[33m!\033[0m Test failed: {e}")
            print("  Setup is complete, but you may need to check your model.")
    else:
        print("  \033[32m\u2713\033[0m Skipped (non-interactive)")

    print(f"\n  \033[1mSetup complete!\033[0m Run 'localsmartz' to start researching.\n")


def _preflight(profile: dict) -> bool:
    """Quick Ollama check before running. Returns True if ready."""
    from localsmartz.ollama import check_server, model_available, suggest_pull

    if not check_server():
        print("Error: Ollama is not running. Start it with: ollama serve", file=sys.stderr)
        return False

    model = profile["planning_model"]
    if not model_available(model):
        print(f"Error: Model '{model}' not found in Ollama.", file=sys.stderr)
        print(f"  → {suggest_pull(model)}", file=sys.stderr)
        return False

    return True


def _select_model(profile: dict) -> str | None:
    """Show available Ollama models and let user pick one.

    Returns selected model name, or None to use profile default.
    """
    from localsmartz.ollama import list_models

    models = list_models()
    if not models:
        return None

    default = profile["planning_model"]

    print("\n  Available models:")
    default_idx = None
    for i, m in enumerate(models, 1):
        marker = " *" if m == default else ""
        if m == default:
            default_idx = i
        print(f"    {i}. {m}{marker}")

    hint = f" [{default_idx}]" if default_idx else ""
    print()

    try:
        choice = input(f"  Select model{hint}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None

    if not choice:
        return None  # Use profile default

    try:
        idx = int(choice)
        if 1 <= idx <= len(models):
            selected = models[idx - 1]
            return selected if selected != default else None
    except ValueError:
        # Treat as model name typed directly
        if choice in models:
            return choice if choice != default else None

    print(f"  Invalid selection, using default: {default}")
    return None


def _handle_command(cmd: str, args, cwd: Path, model_override: str | None, profile: dict) -> str | None:
    """Handle slash commands. Returns 'exit' to quit, 'continue' to skip, None for unknown."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()

    if command in ("/exit", "/quit", "/q"):
        return "exit"

    if command == "/help":
        print()
        print("  Commands:")
        print("    /help             Show this help")
        print("    /model            Change model")
        print("    /thread [name]    Switch or show thread")
        print("    /exit             Quit")
        print()
        print("  Shortcuts:")
        print("    Ctrl+C            Cancel current query")
        print("    Ctrl+D            Quit")
        print()
        return "continue"

    if command == "/model":
        new_model = _select_model(profile)
        if new_model:
            args._model_override = new_model
            # Save to config for next run
            from localsmartz.config import save_config
            save_config(cwd, {"planning_model": new_model, "profile": profile["name"]})
            print(f"  Model → {new_model}  \033[32mSaved\033[0m")
        else:
            current = args._model_override if hasattr(args, '_model_override') and args._model_override else profile["planning_model"]
            print(f"  Model: {current} (unchanged)")
        return "continue"

    if command == "/thread":
        if len(parts) > 1:
            new_thread = parts[1].strip()
            args.thread = new_thread
            print(f"  Thread → {new_thread}")
        else:
            print(f"  Thread: {args.thread}")
            # Show brief thread info
            from localsmartz.threads import get_thread
            existing = get_thread(args.thread, str(cwd))
            if existing and existing.get("entry_count", 0) > 0:
                print(f"  Entries: {existing['entry_count']}")
            else:
                print("  (new thread)")
        return "continue"

    return None  # Unknown command


def _interactive(args, cwd: Path):
    """Interactive REPL — Claude Code-style UX."""
    from localsmartz.profiles import get_profile
    from localsmartz.threads import get_thread, load_context

    from localsmartz.config import resolve_model

    # Resolve model via CLI flag / config / picker
    model_override = resolve_model(cwd, args.model, args.profile)
    profile = get_profile(args.profile, model_override=model_override)

    # First-run auto-trigger: if no model configured, run setup wizard
    from localsmartz.config import load_config as _load_cfg
    _cfg = _load_cfg(cwd)
    if not _cfg or not _cfg.get("planning_model"):
        print("  First run detected \u2014 starting setup wizard...\n")
        _setup(args)
        # Reload profile after setup
        from localsmartz.config import resolve_model
        model_override = resolve_model(cwd, args.model, args.profile)
        profile = get_profile(args.profile, model_override=model_override)

    if not _preflight(profile):
        sys.exit(1)

    # Store on args for /model command to update
    args._model_override = model_override

    thread_id = args.thread or f"cli_{datetime.now():%Y%m%d_%H%M%S}"
    args.thread = thread_id

    active_model = profile["planning_model"]

    # Banner
    print()
    print(f"  \033[1mLocal Smartz\033[0m v0.1.0")
    print(f"  {profile['name']} · {active_model}")
    print(f"  Thread: {thread_id}")

    # Show thread history if resuming
    existing = get_thread(thread_id, str(cwd))
    if existing and existing.get("entry_count", 0) > 0:
        print(f"  Resuming ({existing['entry_count']} previous entries)")
        context = load_context(thread_id, str(cwd))
        if context:
            preview_lines = context.strip().split("\n")[:3]
            for line in preview_lines:
                print(f"    {line}")
            if len(context.strip().split("\n")) > 3:
                print("    ...")

    print()
    print("  Type /help for commands")
    print()

    while True:
        try:
            prompt = input("\033[1m> \033[0m").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not prompt:
            continue

        if prompt.lower() in ("exit", "quit"):
            print("Bye.")
            break

        # Slash commands
        if prompt.startswith("/"):
            result = _handle_command(prompt, args, cwd, args._model_override, profile)
            if result == "exit":
                print("Bye.")
                break
            if result == "continue":
                continue
            # Unknown command — treat as query
            print(f"  Unknown command: {prompt.split()[0]}")
            print("  Type /help for available commands")
            continue

        # Update model_override in case /model changed it
        current_override = args._model_override if hasattr(args, '_model_override') else model_override

        try:
            _run(prompt, args, cwd, model_override=current_override)
        except KeyboardInterrupt:
            print("\n\nInterrupted. Ready for next query.\n")
        except Exception as e:
            print(f"\nError: {e}\n", file=sys.stderr)

        print()


def _run(prompt: str, args, cwd: Path, model_override: str | None = None):
    """Execute a single research query."""
    from localsmartz.agent import run_research, extract_final_response, review_output
    from localsmartz.threads import create_thread, append_entry
    from localsmartz.profiles import get_profile

    verbose = not args.quiet
    thread_id = args.thread

    # Use explicit model_override (from REPL), or resolve via config/picker
    if model_override is not None:
        effective_override = model_override
    else:
        from localsmartz.config import resolve_model
        effective_override = resolve_model(cwd, args.model, args.profile)

    # Preflight check
    profile = get_profile(args.profile, model_override=effective_override)
    if not _preflight(profile):
        sys.exit(1)

    # Ensure storage directories exist
    storage = cwd / ".localsmartz"
    for subdir in ["threads", "artifacts", "memory", "scripts", "reports"]:
        (storage / subdir).mkdir(parents=True, exist_ok=True)

    # Create/resume thread
    if thread_id:
        create_thread(thread_id, str(cwd), title=prompt[:60])

    # Run the agent with streaming
    result = run_research(
        prompt=prompt,
        profile_name=args.profile,
        thread_id=thread_id,
        cwd=cwd,
        verbose=verbose,
        model_override=effective_override,
    )

    # Extract and print final response
    response = extract_final_response(result)
    print(response)

    # Quality gate (full profile only)
    if verbose and profile["name"] == "full":
        print("\n--- Quality Review ---", file=sys.stderr)
        try:
            review = review_output(prompt, response, profile, cwd)
            if review:
                print(review, file=sys.stderr)
        except Exception as e:
            print(f"Review skipped: {e}", file=sys.stderr)

    # Log to thread if active
    if thread_id:
        try:
            append_entry(
                thread_id=thread_id,
                cwd=str(cwd),
                query=prompt,
                summary=response[:500],
                artifacts=[],
                turns=len(result.get("messages", [])),
            )
        except Exception:
            pass  # Thread logging is best-effort


if __name__ == "__main__":
    main()
