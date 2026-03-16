"""CLI entry point: python -m localsmartz"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


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
        start_server(port=args.port)
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

    profile = get_profile(args.profile)
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
    """Interactive setup — install Ollama and pull models."""
    from localsmartz.profiles import get_profile
    from localsmartz.ollama import setup

    profile = get_profile(args.profile)
    ok = setup(profile)
    sys.exit(0 if ok else 1)


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


def _interactive(args, cwd: Path):
    """Interactive REPL — type queries, Ctrl+C or 'exit' to quit."""
    from localsmartz.profiles import get_profile
    from localsmartz.threads import get_thread, load_context

    profile = get_profile(args.profile)

    if not _preflight(profile):
        sys.exit(1)

    thread_id = args.thread or f"cli_{datetime.now():%Y%m%d_%H%M%S}"
    args.thread = thread_id

    print(f"Local Smartz v0.1.0 — local-first research [{profile['name']}]")
    print(f"Model: {profile['planning_model']}")
    print(f"Thread: {thread_id}")

    # Show thread history if resuming
    existing = get_thread(thread_id, str(cwd))
    if existing and existing.get("entry_count", 0) > 0:
        print(f"Resuming thread ({existing['entry_count']} previous entries)")
        context = load_context(thread_id, str(cwd))
        if context:
            # Show a brief preview of previous context
            preview_lines = context.strip().split("\n")[:5]
            for line in preview_lines:
                print(f"  {line}")
            if len(context.strip().split("\n")) > 5:
                print("  ...")

    print("Type your research question. Ctrl+C or 'exit' to quit.\n")

    while True:
        try:
            prompt = input("\033[1mlocalsmartz>\033[0m ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not prompt:
            continue
        if prompt.lower() in ("exit", "quit", "q"):
            print("Bye.")
            break

        try:
            _run(prompt, args, cwd)
        except KeyboardInterrupt:
            print("\n\nInterrupted. Ready for next query.\n")
        except Exception as e:
            print(f"\nError: {e}\n", file=sys.stderr)

        print()


def _run(prompt: str, args, cwd: Path):
    """Execute a single research query."""
    from localsmartz.agent import run_research, extract_final_response, review_output
    from localsmartz.threads import create_thread, append_entry
    from localsmartz.profiles import get_profile

    verbose = not args.quiet
    thread_id = args.thread

    # Preflight check
    profile = get_profile(args.profile)
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
