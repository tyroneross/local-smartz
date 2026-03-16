"""Thread management for Local Smartz.

Stores conversation threads in `.localsmartz/threads/` with:
- index.json: metadata for all threads
- {thread_id}/messages.jsonl: append-only log of entries
- {thread_id}/context.md: auto-generated summary for agent context

Adapted from Stratagem but uses local storage paths and removes cost tracking.
"""

import fcntl
import hashlib
import json
import time
from pathlib import Path
from typing import Any


def _thread_dir(cwd: str) -> Path:
    """Return the threads storage directory."""
    return Path(cwd) / ".localsmartz" / "threads"


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


def create_thread(thread_id: str, cwd: str, title: str) -> Path:
    """Create a new thread with the given ID and title.

    Args:
        thread_id: Unique identifier for the thread
        cwd: Current working directory (project root)
        title: Human-readable title for the thread

    Returns:
        Path to the thread directory
    """
    thread_path = _thread_dir(cwd) / thread_id
    thread_path.mkdir(parents=True, exist_ok=True)

    # Create messages.jsonl if it doesn't exist
    messages_file = thread_path / "messages.jsonl"
    if not messages_file.exists():
        messages_file.touch()

    # Update index.json
    index_file = _thread_dir(cwd) / "index.json"
    with _lock_file(index_file):
        if index_file.exists():
            with open(index_file, 'r') as f:
                index = json.load(f)
        else:
            index = {"threads": []}

        # Check if thread already exists
        existing = next((t for t in index["threads"] if t["id"] == thread_id), None)
        if not existing:
            index["threads"].append({
                "id": thread_id,
                "title": title,
                "created_at": time.time(),
                "updated_at": time.time(),
                "entry_count": 0
            })
        else:
            existing["title"] = title
            existing["updated_at"] = time.time()

        with open(index_file, 'w') as f:
            json.dump(index, f, indent=2)

    return thread_path


def load_context(thread_id: str, cwd: str) -> str | None:
    """Load the context.md file for a thread.

    Args:
        thread_id: Thread identifier
        cwd: Current working directory

    Returns:
        Contents of context.md, or None if not found
    """
    context_file = _thread_dir(cwd) / thread_id / "context.md"
    if context_file.exists():
        return context_file.read_text()
    return None


def append_entry(
    thread_id: str,
    cwd: str,
    query: str,
    summary: str,
    artifacts: list[dict[str, Any]],
    turns: int,
    rationale: str | None = None,
    tools_used: list[str] | None = None,
    scripts: list[str] | None = None
) -> None:
    """Append an entry to the thread's messages.jsonl.

    Args:
        thread_id: Thread identifier
        cwd: Current working directory
        query: The user's query
        summary: Summary of the interaction
        artifacts: List of artifacts created (from artifacts.register)
        turns: Number of conversation turns
        rationale: Optional reasoning/approach description
        tools_used: Optional list of tool names used
        scripts: Optional list of script paths executed
    """
    thread_path = _thread_dir(cwd) / thread_id
    if not thread_path.exists():
        raise ValueError(f"Thread {thread_id} does not exist")

    messages_file = thread_path / "messages.jsonl"

    entry = {
        "timestamp": time.time(),
        "query": query,
        "summary": summary,
        "artifacts": artifacts,
        "turns": turns,
        "rationale": rationale,
        "tools_used": tools_used or [],
        "scripts": scripts or []
    }

    with _lock_file(messages_file):
        with open(messages_file, 'a') as f:
            f.write(json.dumps(entry) + '\n')

    # Update index
    index_file = _thread_dir(cwd) / "index.json"
    with _lock_file(index_file):
        with open(index_file, 'r') as f:
            index = json.load(f)

        for thread in index["threads"]:
            if thread["id"] == thread_id:
                thread["updated_at"] = time.time()
                thread["entry_count"] = thread.get("entry_count", 0) + 1
                break

        with open(index_file, 'w') as f:
            json.dump(index, f, indent=2)

    # Rebuild context
    rebuild_context(thread_id, cwd)


def rebuild_context(thread_id: str, cwd: str) -> None:
    """Rebuild the context.md file from messages.jsonl.

    Recent entries (last 5) get full detail.
    Older entries get one-line summaries.

    Args:
        thread_id: Thread identifier
        cwd: Current working directory
    """
    thread_path = _thread_dir(cwd) / thread_id
    messages_file = thread_path / "messages.jsonl"
    context_file = thread_path / "context.md"

    if not messages_file.exists():
        return

    # Load all entries
    entries = []
    with open(messages_file, 'r') as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))

    if not entries:
        return

    # Build context markdown
    lines = [f"# Thread: {thread_id}\n"]

    # Recent entries (last 5) get full detail
    recent_count = min(5, len(entries))
    older_entries = entries[:-recent_count] if len(entries) > recent_count else []
    recent_entries = entries[-recent_count:]

    if older_entries:
        lines.append("## Earlier Entries\n")
        for entry in older_entries:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["timestamp"]))
            lines.append(f"- [{ts}] {entry['summary']}")
        lines.append("")

    lines.append("## Recent Entries\n")
    for entry in recent_entries:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["timestamp"]))
        lines.append(f"### {ts}\n")
        lines.append(f"**Query:** {entry['query']}\n")
        lines.append(f"**Summary:** {entry['summary']}\n")

        if entry.get("rationale"):
            lines.append(f"**Rationale:** {entry['rationale']}\n")

        if entry.get("artifacts"):
            lines.append("**Artifacts:**")
            for art in entry["artifacts"]:
                lines.append(f"- {art.get('title', 'Untitled')} ({art.get('format', 'unknown')}): `{art.get('path', 'unknown')}`")
            lines.append("")

        if entry.get("tools_used"):
            lines.append(f"**Tools:** {', '.join(entry['tools_used'])}\n")

        if entry.get("scripts"):
            lines.append("**Scripts:**")
            for script in entry["scripts"]:
                lines.append(f"- `{script}`")
            lines.append("")

        lines.append(f"**Turns:** {entry['turns']}\n")
        lines.append("---\n")

    with open(context_file, 'w') as f:
        f.write('\n'.join(lines))


def list_threads(cwd: str) -> list[dict[str, Any]]:
    """List all threads in the workspace.

    Args:
        cwd: Current working directory

    Returns:
        List of thread metadata dicts, sorted by updated_at (newest first)
    """
    index_file = _thread_dir(cwd) / "index.json"
    if not index_file.exists():
        return []

    with open(index_file, 'r') as f:
        index = json.load(f)

    threads = index.get("threads", [])
    return sorted(threads, key=lambda t: t.get("updated_at", 0), reverse=True)


def get_thread(thread_id: str, cwd: str) -> dict[str, Any] | None:
    """Get metadata for a specific thread.

    Args:
        thread_id: Thread identifier
        cwd: Current working directory

    Returns:
        Thread metadata dict, or None if not found
    """
    threads = list_threads(cwd)
    return next((t for t in threads if t["id"] == thread_id), None)
