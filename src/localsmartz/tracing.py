"""LangSmith tracing configuration.

Tracing is automatic when LANGSMITH_TRACING=true is set.
This module loads .env files and verifies config.

Local-Only privacy boundary (SEC-001): when ``global_config.local_only`` is
True — including the fail-closed degraded state where global.json exists
but can't be parsed — LangSmith tracing must never be enabled for this
process. Without this gate, a pre-set LANGSMITH_TRACING env var or --trace
flag would let LangChain's tracing callbacks ship every prompt/completion,
even from local ChatOllama runs, to smith.langchain.com regardless of the
Local-Only toggle.
"""

import os
import sys
from pathlib import Path


def _local_only_enabled() -> bool:
    """Fail-closed read of ``global_config.local_only``. Mirrors the same
    fail-closed helper duplicated in ``secrets.py``/``serve.py`` — a corrupt
    or unreadable global.json must never silently re-open cloud egress."""
    try:
        from localsmartz import global_config

        value, degraded = global_config.local_only_state()
        return True if degraded else value
    except Exception:  # noqa: BLE001
        return True


def configure_tracing(cwd: Path | None = None, force: bool = False) -> bool:
    """Load .env if present and check if tracing is enabled.

    Only loads LANGSMITH_ prefixed vars. Does not overwrite existing env vars.
    Returns True if LANGSMITH_TRACING is set to true.

    Under Local-Only (including the fail-closed degraded state), tracing is
    force-disabled: LANGSMITH_TRACING is set to "false" regardless of any
    pre-set env var or force=True request, and this function returns False —
    a pre-set env var cannot re-enable tracing for this process.
    """
    if _local_only_enabled():
        already_requested = force or os.environ.get(
            "LANGSMITH_TRACING", ""
        ).lower() == "true"
        if already_requested:
            print("Local-Only: LangSmith tracing disabled", file=sys.stderr)
        os.environ["LANGSMITH_TRACING"] = "false"
        return False

    if force:
        os.environ["LANGSMITH_TRACING"] = "true"

    cwd = cwd or Path.cwd()

    env_file = cwd / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key.startswith("LANGSMITH_"):
                os.environ.setdefault(key, value)

    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true"
