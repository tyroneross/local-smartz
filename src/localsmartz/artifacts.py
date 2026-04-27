"""Artifact registration and tracking for Local Smartz.

Stores artifact metadata in `.localsmartz/artifacts/index.json`.
Each artifact has:
- id: unique identifier (hash of path + timestamp)
- path: absolute path to the artifact
- format: file type/format (e.g., "python", "markdown", "json")
- title: human-readable title
- thread_id: associated thread
- created_at: timestamp

Adapted from Stratagem but uses local storage paths.
"""

import fcntl
import hashlib
import json
import time
from pathlib import Path
from typing import Any


def _artifacts_dir(cwd: str) -> Path:
    """Return the artifacts storage directory."""
    return Path(cwd) / ".localsmartz" / "artifacts"


def _lock_file(path: Path):
    """Context manager for exclusive file locking."""
    class FileLock:
        def __init__(self, file_path: Path):
            self.file_path = file_path
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_path = file_path.parent / f".{file_path.name}.lock"
            self.lock_file = None

        def __enter__(self):
            self.lock_file = open(self.lock_path, 'w')
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX)
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if self.lock_file:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    return FileLock(path)


def _generate_id(path: str) -> str:
    """Generate a unique ID for an artifact."""
    unique_string = f"{path}_{time.time()}"
    return hashlib.sha256(unique_string.encode()).hexdigest()[:16]


def register(
    path: str,
    format: str,
    title: str,
    cwd: str,
    thread_id: str | None = None
) -> dict[str, Any]:
    """Register an artifact in the index.

    Args:
        path: Absolute path to the artifact
        format: File format/type (e.g., "python", "markdown", "json")
        title: Human-readable title
        cwd: Current working directory (project root)
        thread_id: Optional thread ID to associate with

    Returns:
        The artifact metadata dict
    """
    artifacts_path = _artifacts_dir(cwd)
    artifacts_path.mkdir(parents=True, exist_ok=True)

    index_file = artifacts_path / "index.json"

    artifact = {
        "id": _generate_id(path),
        "path": path,
        "format": format,
        "title": title,
        "thread_id": thread_id,
        "created_at": time.time()
    }

    with _lock_file(index_file):
        if index_file.exists():
            with open(index_file, 'r') as f:
                index = json.load(f)
        else:
            index = {"artifacts": []}

        # Check for duplicate path in same thread
        existing = next(
            (a for a in index["artifacts"]
             if a["path"] == path and a.get("thread_id") == thread_id),
            None
        )

        if existing:
            # Update existing artifact
            existing["format"] = format
            existing["title"] = title
            existing["created_at"] = time.time()
            artifact = existing
        else:
            # Add new artifact
            index["artifacts"].append(artifact)

        with open(index_file, 'w') as f:
            json.dump(index, f, indent=2)

    # S2 (Phase 3): emit an ``ls.artifact.register`` span for Phoenix.
    # Best-effort: if the OTel SDK isn't initialized, the default no-op
    # tracer is a silent no-op. Attrs intentionally exclude the full path
    # to avoid leaking PII; the basename is enough for trace-UI readability.
    try:
        from localsmartz.observability import get_tracer as _get_tracer

        tracer = _get_tracer("localsmartz.artifacts")
        with tracer.start_as_current_span("ls.artifact.register") as span:
            span.set_attribute("ls.artifact.id", artifact["id"])
            span.set_attribute("ls.artifact.format", format)
            span.set_attribute("ls.artifact.thread_id", thread_id or "")
            try:
                basename = Path(path).name
            except Exception:  # noqa: BLE001
                basename = ""
            span.set_attribute("ls.artifact.path_basename", basename)
    except Exception:  # noqa: BLE001 — tracing must never break registration
        pass

    return artifact


def list_artifacts(cwd: str, thread_id: str | None = None) -> list[dict[str, Any]]:
    """List all artifacts, optionally filtered by thread.

    Args:
        cwd: Current working directory
        thread_id: Optional thread ID to filter by

    Returns:
        List of artifact metadata dicts, sorted by created_at (newest first)
    """
    index_file = _artifacts_dir(cwd) / "index.json"
    if not index_file.exists():
        return []

    with open(index_file, 'r') as f:
        index = json.load(f)

    artifacts = index.get("artifacts", [])

    if thread_id is not None:
        artifacts = [a for a in artifacts if a.get("thread_id") == thread_id]

    return sorted(artifacts, key=lambda a: a.get("created_at", 0), reverse=True)
