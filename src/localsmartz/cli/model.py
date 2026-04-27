"""CLI ``localsmartz model ...`` subcommand family (Phase 1.5).

Shares install code with the HTTP server — both route through
``localsmartz.models.install.install`` so we have exactly one progress
stream and one telemetry span.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any


def _cmd_list(_args: argparse.Namespace) -> int:
    from localsmartz.ollama import check_server, list_models_with_size

    if not check_server():
        print("Ollama is not running. Start with: ollama serve", file=sys.stderr)
        return 1
    models = list_models_with_size()
    if not models:
        print("No models installed. Try: localsmartz model recommend --install")
        return 0
    for name, size_gb in models:
        print(f"  {name:<40s}  {size_gb:6.1f} GB")
    return 0


def _cmd_recommend(args: argparse.Namespace) -> int:
    from localsmartz.models.catalog import recommended_for_tier
    from localsmartz.profiles import detect_tier

    tier_info = detect_tier()
    tier = tier_info["tier"]
    recs = recommended_for_tier(tier)
    print(f"Detected tier: {tier} ({tier_info['ram_gb']} GB RAM)")
    print("Recommended models:")
    for rec in recs:
        print(
            f"  {rec['name']:<30s}  ~{rec['size_gb_q4']:.1f} GB  "
            f"[{','.join(rec.get('roles', []))}]"
        )
    if not args.install:
        print("\nInstall the set with: localsmartz model recommend --install")
        return 0

    rc = 0
    for rec in recs:
        rc |= _install_with_progress(rec["name"])
    return rc


def _cmd_add(args: argparse.Namespace) -> int:
    rc = _install_with_progress(args.name)
    if rc == 0 and args.role:
        _assign_role(args.role, args.name)
    return rc


def _cmd_remove(args: argparse.Namespace) -> int:
    import subprocess

    result = subprocess.run(["ollama", "rm", args.name], check=False)
    return result.returncode


def _cmd_assign(args: argparse.Namespace) -> int:
    _assign_role(args.role, args.name)
    return 0


def _cmd_doctor(_args: argparse.Namespace) -> int:
    from localsmartz.ollama import check_server, get_version, list_running_models
    from localsmartz.observability import probe_collector
    from localsmartz.profiles import detect_tier

    tier = detect_tier()
    print(f"Tier:    {tier['tier']} ({tier['ram_gb']} GB RAM)")
    if check_server():
        print(f"Ollama:  running (v{get_version() or '?'})")
        running = list_running_models()
        if running:
            print(f"Loaded:  {', '.join(m.get('name', '?') for m in running)}")
        else:
            print("Loaded:  none resident")
    else:
        print("Ollama:  NOT running — start with `ollama serve`")
    print(
        f"Phoenix: {'reachable' if probe_collector() else 'not reachable'} "
        "(observability)"
    )
    return 0


def _install_with_progress(name: str) -> int:
    """Install one model, rendering a single-line progress bar to stderr."""
    from localsmartz.models.install import install

    last_pct = -1
    try:
        for event in install(name):
            etype = event.get("type")
            if etype == "status":
                print(f"[{name}] {event['text']}", file=sys.stderr)
            elif etype == "progress":
                total = event.get("total", 0) or 0
                completed = event.get("completed", 0) or 0
                pct = int(completed * 100 / total) if total else 0
                if pct != last_pct:
                    bar_len = 30
                    filled = int(bar_len * pct / 100)
                    bar = "#" * filled + "-" * (bar_len - filled)
                    print(
                        f"\r[{name}] {bar} {pct:3d}%  ({completed / 1e9:.2f}/"
                        f"{total / 1e9:.2f} GB)",
                        end="",
                        flush=True,
                        file=sys.stderr,
                    )
                    last_pct = pct
            elif etype == "done":
                print(
                    f"\r[{name}] installed in {event['duration_ms'] / 1000:.1f}s"
                    + " " * 40,
                    file=sys.stderr,
                )
                return 0
            elif etype == "error":
                print(f"\r[{name}] ERROR: {event['message']}", file=sys.stderr)
                return 1
    except KeyboardInterrupt:
        print(f"\r[{name}] cancelled", file=sys.stderr)
        return 130
    return 0


def _assign_role(role: str, name: str) -> None:
    """Persist the role → model assignment in global_config.agent_models."""
    from localsmartz import global_config

    overrides = global_config.get("agent_models") or {}
    if not isinstance(overrides, dict):
        overrides = {}
    overrides = dict(overrides)
    overrides[role] = name
    global_config.set("agent_models", overrides)
    print(f"Assigned role={role} → {name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localsmartz model",
        description="Manage local models (install, list, assign roles).",
    )
    sub = parser.add_subparsers(dest="op", required=True)

    sub.add_parser("list", help="List installed models").set_defaults(func=_cmd_list)

    p_rec = sub.add_parser("recommend", help="Show tier-matched recommended set")
    p_rec.add_argument("--install", action="store_true", help="Install the recommended set")
    p_rec.set_defaults(func=_cmd_recommend)

    p_add = sub.add_parser("add", help="Install one model")
    p_add.add_argument("name")
    p_add.add_argument("--role", default=None, help="Assign to a role after install")
    p_add.set_defaults(func=_cmd_add)

    p_rm = sub.add_parser("remove", help="Remove an installed model")
    p_rm.add_argument("name")
    p_rm.set_defaults(func=_cmd_remove)

    p_as = sub.add_parser("assign", help="Assign a role to an installed model")
    p_as.add_argument("role")
    p_as.add_argument("name")
    p_as.set_defaults(func=_cmd_assign)

    sub.add_parser("doctor", help="Check Ollama + tier + Phoenix").set_defaults(func=_cmd_doctor)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
