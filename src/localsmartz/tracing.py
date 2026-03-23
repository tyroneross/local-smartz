"""LangSmith tracing configuration.

Tracing is automatic when LANGSMITH_TRACING=true is set.
This module loads .env files and verifies config.
"""

import os
from pathlib import Path


def configure_tracing(cwd: Path | None = None, force: bool = False) -> bool:
    """Load .env if present and check if tracing is enabled.

    Only loads LANGSMITH_ prefixed vars. Does not overwrite existing env vars.
    Returns True if LANGSMITH_TRACING is set to true.
    """
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
