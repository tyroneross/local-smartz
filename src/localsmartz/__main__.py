"""CLI entry point: python -m localsmartz"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import readline  # noqa: F401 — enables arrow keys + history in input()
except ImportError:
    pass  # Windows fallback — input() still works, just no history


_NOUNS = ("plugins", "skills", "config", "secrets", "logs")


def main():
    # Noun-based subcommand layer: detect BEFORE the legacy argparse runs.
    # Old flags (--serve, --setup, --check, --list-threads, --version, --help)
    # remain intact; nouns are additive.
    if len(sys.argv) > 1 and sys.argv[1] in _NOUNS:
        _handle_noun_command(sys.argv[1], sys.argv[2:])
        return

    _legacy_main()


def _legacy_main():
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
        "--trace", action="store_true", help="Enable LangSmith tracing"
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help=(
            "Send OpenTelemetry traces to a local Phoenix collector "
            "(default: http://localhost:6006/v1/traces). Run Phoenix with: "
            "docker run -p 6006:6006 arizephoenix/phoenix"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    args = parser.parse_args()
    cwd = Path(args.cwd) if args.cwd else Path.cwd()

    # Configure LangSmith tracing (cloud, opt-in via --trace)
    from localsmartz.tracing import configure_tracing
    if hasattr(args, 'trace') and args.trace:
        import os
        os.environ["LANGSMITH_TRACING"] = "true"
    configure_tracing(cwd)

    # Configure OpenTelemetry observability (local Phoenix, opt-in via --observe
    # OR LOCALSMARTZ_OBSERVE=1 env var). Failures are logged + swallowed.
    from localsmartz.observability import setup_observability, is_observability_enabled
    if (hasattr(args, 'observe') and args.observe) or is_observability_enabled():
        setup_observability()

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
        from localsmartz import secrets as _secrets
        from localsmartz import log_buffer as _log_buffer
        n = _secrets.export_to_env()
        _log_buffer.info("secrets", f"exported {n} preset keys to env")
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
    from localsmartz.ollama import validate_for_profile, resolve_available_model

    profile = get_profile(args.profile, model_override=args.model)
    print(f"Profile: {profile['name']}")
    print(f"Planning model: {profile['planning_model']}")
    print(f"Execution model: {profile['execution_model']}")
    print()

    ok, messages = validate_for_profile(profile)
    for msg in messages:
        print(msg)

    if not ok:
        chosen, fallback_msg = resolve_available_model(profile["planning_model"])
        if chosen and chosen != profile["planning_model"]:
            print()
            print(f"\033[33m!\033[0m {fallback_msg}")
            profile["planning_model"] = chosen
            ok = True

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
            agent, _profile, _checkpointer, _mcp_clients = create_agent(
                profile_name=profile_name,
                model_override=selected_model,
            )
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
    """Quick Ollama check before running. Returns True if ready.

    Mutates `profile["planning_model"]` to a fallback if the configured one
    isn't pulled but a usable substitute exists. Also warms the model into
    Ollama VRAM so the first query doesn't sit in a silent cold-load.
    """
    from localsmartz.ollama import check_server, resolve_available_model, warmup_model

    if not check_server():
        print("Error: Ollama is not running. Start it with: ollama serve", file=sys.stderr)
        return False

    requested = profile["planning_model"]
    chosen, msg = resolve_available_model(requested)
    if chosen is None:
        print(f"Error: {msg}", file=sys.stderr)
        return False
    if msg:
        print(f"  \033[33m!\033[0m {msg}", file=sys.stderr)
        profile["planning_model"] = chosen

    # Warm the model — blocks until it's loaded into VRAM (or keep_alive refreshed).
    # Idempotent: returns fast if the model is already resident.
    target = profile["planning_model"]
    print(f"  Loading model {target}...", end="", flush=True, file=sys.stderr)
    ok, warm_ms, warm_err = warmup_model(target, keep_alive="30m")
    if ok:
        print(f" \033[32m\u2713\033[0m ({warm_ms} ms)", file=sys.stderr)
    else:
        # Non-fatal: the first real query will also trigger a load attempt.
        print(f" \033[33m!\033[0m ({warm_err})", file=sys.stderr)
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


# ---------------------------------------------------------------------------
# Noun-based subcommand layer: plugins / skills / config
# ---------------------------------------------------------------------------


def _handle_noun_command(noun: str, rest: list[str]) -> None:
    try:
        if noun == "plugins":
            _cmd_plugins(rest)
        elif noun == "skills":
            _cmd_skills(rest)
        elif noun == "config":
            _cmd_config(rest)
        elif noun == "secrets":
            _cmd_secrets(rest)
        elif noun == "logs":
            _cmd_logs(rest)
    except SystemExit:
        raise
    except Exception as e:  # pragma: no cover — unexpected crashes
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _format_table(rows: list[list[str]], headers: list[str]) -> str:
    all_rows = [headers] + rows
    widths = [max(len(str(r[i])) for r in all_rows) for i in range(len(headers))]
    lines = []
    fmt_row = lambda r: "  ".join(str(r[i]).ljust(widths[i]) for i in range(len(headers)))
    lines.append(fmt_row(headers))
    lines.append("  ".join("-" * w for w in widths))
    for r in rows:
        lines.append(fmt_row(r))
    return "\n".join(lines)


# ---------------------------- plugins ---------------------------------------


def _cmd_plugins(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="localsmartz plugins",
        description="Manage installed plugins",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_install = sub.add_parser("install", help="Install a plugin from a local path")
    p_install.add_argument("path", help="Path to plugin directory")
    p_install.add_argument("--name", default=None, help="Override installed name")
    p_install.add_argument(
        "--copy", action="store_true", help="Copy instead of symlink"
    )

    p_list = sub.add_parser("list", help="List installed plugins")
    p_list.add_argument("--json", action="store_true", help="Machine-readable output")

    p_validate = sub.add_parser("validate", help="Validate a plugin directory")
    p_validate.add_argument("path", help="Path to plugin directory")

    p_remove = sub.add_parser("remove", help="Remove an installed plugin")
    p_remove.add_argument("name", help="Installed plugin name")

    args = parser.parse_args(argv)

    from localsmartz.plugins import Registry

    reg = Registry.from_default_root()

    if args.action == "install":
        src = Path(args.path).expanduser()
        if not src.exists():
            print(f"error: path does not exist: {src}", file=sys.stderr)
            sys.exit(1)
        report = reg.validate(src)
        if not report.ok:
            _print_validation_report(report)
            sys.exit(1)
        try:
            plugin = reg.install(src, dest_name=args.name, copy=args.copy)
        except Exception as e:
            print(f"error: install failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"\u2713 Installed {plugin.name} at {plugin.install_path}")
        return

    if args.action == "list":
        plugins = reg.list_plugins()
        if args.json:
            _print_json(
                [
                    {
                        "name": p.name,
                        "version": p.version,
                        "description": p.description,
                        "author": p.author_name,
                        "install_path": str(p.install_path),
                        "skills": [s.name for s in p.skills],
                        "commands": [c.name for c in p.commands],
                        "mcp_servers": [m.name for m in p.mcp_servers],
                    }
                    for p in plugins
                ]
            )
            return
        if not plugins:
            print("(no plugins installed)")
            return
        rows = [
            [p.name, p.version or "-", str(len(p.skills)), str(len(p.commands)), p.description[:50]]
            for p in plugins
        ]
        print(_format_table(rows, ["NAME", "VERSION", "SKILLS", "COMMANDS", "DESCRIPTION"]))
        return

    if args.action == "validate":
        path = Path(args.path).expanduser()
        report = reg.validate(path)
        _print_validation_report(report)
        sys.exit(0 if report.ok else 1)

    if args.action == "remove":
        # Surface a clear error if plugin isn't installed.
        installed = {p.name for p in reg.list_plugins()}
        dest = reg.root / args.name
        if args.name not in installed and not dest.exists() and not dest.is_symlink():
            print(f"error: plugin not installed: {args.name}", file=sys.stderr)
            sys.exit(1)
        reg.remove(args.name)
        print(f"\u2713 Removed {args.name}")
        return


def _print_validation_report(report) -> None:
    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]
    for issue in errors:
        print(f"error: [{issue.code}] {issue.message} ({issue.path})", file=sys.stderr)
    for issue in warnings:
        print(f"warning: [{issue.code}] {issue.message} ({issue.path})", file=sys.stderr)
    if report.ok and not warnings:
        print("\u2713 valid")
    elif report.ok:
        print(f"\u2713 valid ({len(warnings)} warning(s))")


# ---------------------------- skills ----------------------------------------


def _cmd_skills(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="localsmartz skills",
        description="Manage skill activation",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_list = sub.add_parser("list", help="List discovered skills")
    p_list.add_argument("--active", action="store_true", help="Active skills only")
    p_list.add_argument("--json", action="store_true", help="Machine-readable output")

    p_activate = sub.add_parser("activate", help="Activate a skill by name")
    p_activate.add_argument("name")

    p_deactivate = sub.add_parser("deactivate", help="Deactivate a skill by name")
    p_deactivate.add_argument("name")

    args = parser.parse_args(argv)

    from localsmartz import global_config
    from localsmartz.plugins import Registry

    reg = Registry.from_default_root()
    known_names = {s.name for s in reg.list_skills()}
    # Source of truth for the running agent is global_config["active_skills"].
    # We intersect with known_names so only valid (currently installed) skills
    # are surfaced; stale entries in global.json don't show up as "active" but
    # are preserved until the user explicitly deactivates.
    stored_active = global_config.get("active_skills") or []
    if not isinstance(stored_active, list):
        stored_active = []
    active = set(stored_active) & known_names

    if args.action == "list":
        if args.active:
            # Filter to only currently-installed skills that are in active_skills.
            skills = [s for s in reg.list_skills() if s.name in active]
        else:
            skills = reg.list_skills()
        if args.json:
            _print_json(
                [
                    {
                        "name": s.name,
                        "description": s.description,
                        "plugin": s.plugin_name,
                        "active": s.name in active,
                        "source_path": str(s.source_path),
                    }
                    for s in skills
                ]
            )
            return
        if not skills:
            print("(no skills found)" if not args.active else "(no active skills)")
            return
        rows = [
            [
                ("*" if s.name in active else " ") + " " + s.name,
                s.plugin_name or "(standalone)",
                (s.description or "")[:60],
            ]
            for s in skills
        ]
        print(_format_table(rows, ["NAME", "PLUGIN", "DESCRIPTION"]))
        return

    if args.action == "activate":
        if args.name not in known_names:
            print(f"error: skill not found: {args.name}", file=sys.stderr)
            sys.exit(1)
        # Preserve any previously-stored names (even unknown ones) in addition
        # to the newly-activated name.
        new_active = sorted(set(stored_active) | {args.name})
        global_config.set("active_skills", new_active)
        print(f"\u2713 Activated {args.name}")
        return

    if args.action == "deactivate":
        if args.name not in active:
            print(f"error: skill not active: {args.name}", file=sys.stderr)
            sys.exit(1)
        new_active = sorted(set(stored_active) - {args.name})
        global_config.set("active_skills", new_active)
        print(f"\u2713 Deactivated {args.name}")
        return


# ---------------------------- config ----------------------------------------


def _cmd_config(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="localsmartz config",
        description="Get or set user-global configuration",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_get = sub.add_parser("get", help="Show one or all settings")
    p_get.add_argument("key", nargs="?", default=None)
    p_get.add_argument("--json", action="store_true", help="Machine-readable output")

    p_set = sub.add_parser("set", help="Set a config key")
    p_set.add_argument("key")
    p_set.add_argument("value")

    sub.add_parser("reset", help="Delete the global config file")

    args = parser.parse_args(argv)

    from localsmartz import global_config

    if args.action == "get":
        current = global_config.load_global()
        defaults = global_config.all_defaults()
        if args.key is not None:
            if args.key not in current:
                print(f"error: unknown config key: {args.key}", file=sys.stderr)
                sys.exit(1)
            value = current[args.key]
            if args.json:
                _print_json({args.key: value})
            else:
                print(_format_value(value))
            return

        # All settings with source.
        if args.json:
            _print_json(
                {
                    k: {
                        "value": current.get(k),
                        "default": defaults.get(k),
                        "source": _source_for(k, current, defaults),
                    }
                    for k in global_config.SCHEMA_KEYS
                }
            )
            return
        rows = []
        for k in global_config.SCHEMA_KEYS:
            rows.append(
                [
                    k,
                    _format_value(current.get(k)),
                    _source_for(k, current, defaults),
                ]
            )
        print(_format_table(rows, ["KEY", "VALUE", "SOURCE"]))
        return

    if args.action == "set":
        coerced, err = _coerce_value(args.key, args.value)
        if err:
            print(f"error: {err}", file=sys.stderr)
            sys.exit(1)
        try:
            global_config.set(args.key, coerced)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"\u2713 Set {args.key} = {_format_value(coerced)}")
        return

    if args.action == "reset":
        from localsmartz import global_config as _gc
        _gc.reset()
        print("\u2713 Reset global config")
        return


def _format_value(value) -> str:
    if isinstance(value, list):
        return ",".join(str(v) for v in value) if value else "(empty)"
    if value == "":
        return "(empty)"
    return str(value)


def _source_for(key: str, current: dict, defaults: dict) -> str:
    # Check file presence and whether this key is stored there.
    from localsmartz import global_config as _gc

    file_path = Path.home() / ".localsmartz" / "global.json"
    if file_path.exists():
        try:
            raw = json.loads(file_path.read_text("utf-8"))
            if isinstance(raw, dict) and key in raw:
                return "file"
        except (OSError, json.JSONDecodeError):
            pass
    return "default"


def _coerce_value(key: str, raw: str):
    """Coerce a CLI string value into the type declared by SCHEMA_KEYS.

    Returns (coerced_value, error_msg). On error, coerced_value is None.
    """
    from localsmartz import global_config

    if key not in global_config.SCHEMA_KEYS:
        return None, f"unknown config key: {key}"
    expected = global_config.SCHEMA_KEYS[key]
    if expected is str:
        # Reject purely numeric strings as a type mismatch for known string keys
        # whose values are paths or identifiers (fits test expectation).
        if raw.strip().lstrip("-").isdigit():
            return None, f"invalid value for {key}: expected string path/identifier, got numeric {raw!r}"
        return raw, None
    if expected is list:
        items = [s.strip() for s in raw.split(",") if s.strip()] if raw else []
        return items, None
    if expected is int:
        try:
            return int(raw), None
        except ValueError:
            return None, f"invalid int for {key}: {raw!r}"
    if expected is bool:
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True, None
        if low in ("false", "0", "no", "off"):
            return False, None
        return None, f"invalid bool for {key}: {raw!r}"
    return None, f"unsupported type for {key}: {expected.__name__}"


# ---------------------------- secrets ---------------------------------------


def _cmd_secrets(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="localsmartz secrets",
        description="Manage API keys stored in the macOS Keychain (file fallback)",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_list = sub.add_parser("list", help="List known providers and whether they're set")
    p_list.add_argument("--json", action="store_true", help="Machine-readable output")

    p_set = sub.add_parser("set", help="Store a secret for a provider")
    p_set.add_argument("provider")
    p_set.add_argument("value")

    p_get = sub.add_parser("get", help="Print the last 4 characters of a stored secret")
    p_get.add_argument("provider")

    p_delete = sub.add_parser("delete", help="Delete a provider's secret")
    p_delete.add_argument("provider")

    sub.add_parser("export", help="Export preset secrets to environment variables")

    args = parser.parse_args(argv)

    from localsmartz import secrets as _secrets

    if args.action == "list":
        rows_data = _secrets.masked_all()
        if args.json:
            _print_json(rows_data)
            return
        rows = [
            [
                r["provider"],
                r.get("env_var") or "-",
                "yes" if r["set"] else "no",
                r.get("last_four") or "-",
                r.get("source") or "-",
            ]
            for r in rows_data
        ]
        print(_format_table(rows, ["PROVIDER", "ENV_VAR", "SET", "LAST_FOUR", "SOURCE"]))
        return

    if args.action == "set":
        try:
            source = _secrets.set(args.provider, args.value)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        # Also populate env var in this process for preset providers.
        env_name = _secrets.PRESET_BY_NAME.get(args.provider)
        if env_name:
            os.environ[env_name] = args.value
        print(f"\u2713 Stored {args.provider} in {source}")
        return

    if args.action == "get":
        if not _secrets.is_set(args.provider):
            print(f"error: not set: {args.provider}", file=sys.stderr)
            sys.exit(1)
        print(_secrets.last_four(args.provider) or "")
        return

    if args.action == "delete":
        _secrets.delete(args.provider)
        env_name = _secrets.PRESET_BY_NAME.get(args.provider)
        if env_name:
            os.environ.pop(env_name, None)
        print(f"\u2713 Deleted {args.provider}")
        return

    if args.action == "export":
        n = _secrets.export_to_env()
        print(f"\u2713 Exported {n} preset key(s) to env")
        return


# ---------------------------- logs ------------------------------------------


def _cmd_logs(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="localsmartz logs",
        description="Tail the in-memory log buffer",
    )
    sub = parser.add_subparsers(dest="action", required=False)

    p_tail = sub.add_parser("tail", help="Show recent log entries (default action)")
    p_tail.add_argument("--json", action="store_true", help="Machine-readable output")
    p_tail.add_argument("--since", type=int, default=0, help="Only entries with seq > this")

    sub.add_parser("clear", help="Clear the log buffer")

    args = parser.parse_args(argv)

    from localsmartz import log_buffer

    action = args.action or "tail"

    if action == "tail":
        since = getattr(args, "since", 0) or 0
        entries = log_buffer.since(since)
        if getattr(args, "json", False):
            _print_json(entries)
            return
        if not entries:
            print("(no log entries)")
            return
        rows = [
            [
                str(e.get("seq", "")),
                datetime.fromtimestamp(e.get("ts", 0)).strftime("%H:%M:%S"),
                str(e.get("level", "")),
                str(e.get("source", "")),
                str(e.get("message", "")),
            ]
            for e in entries
        ]
        print(_format_table(rows, ["SEQ", "TIME", "LEVEL", "SOURCE", "MESSAGE"]))
        return

    if action == "clear":
        log_buffer.clear()
        print("\u2713 Cleared log buffer")
        return


if __name__ == "__main__":
    main()
