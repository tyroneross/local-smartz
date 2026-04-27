"""Tests for thread context management."""

import json
import tempfile
from pathlib import Path

from localsmartz.threads import (
    create_thread,
    load_context,
    append_entry,
    rebuild_context,
    list_threads,
    get_thread,
)


def test_create_thread():
    with tempfile.TemporaryDirectory() as tmp:
        path = create_thread("test-1", tmp, "Test Thread")
        assert path.exists()
        assert (path / "messages.jsonl").exists()

        # Verify index
        index_file = Path(tmp) / ".localsmartz" / "threads" / "index.json"
        assert index_file.exists()
        index = json.loads(index_file.read_text())
        assert len(index["threads"]) == 1
        assert index["threads"][0]["id"] == "test-1"
        assert index["threads"][0]["title"] == "Test Thread"


def test_create_thread_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        create_thread("test-1", tmp, "First")
        create_thread("test-1", tmp, "Updated")

        threads = list_threads(tmp)
        assert len(threads) == 1
        assert threads[0]["title"] == "Updated"


def test_append_entry():
    with tempfile.TemporaryDirectory() as tmp:
        create_thread("test-1", tmp, "Test")
        append_entry(
            thread_id="test-1",
            cwd=tmp,
            query="What is Python?",
            summary="Python is a programming language",
            artifacts=[],
            turns=3,
            tools_used=["web_search"],
        )

        # Check messages.jsonl
        msg_file = Path(tmp) / ".localsmartz" / "threads" / "test-1" / "messages.jsonl"
        lines = msg_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["query"] == "What is Python?"
        assert entry["tools_used"] == ["web_search"]

        # Check index updated
        thread = get_thread("test-1", tmp)
        assert thread["entry_count"] == 1


def test_rebuild_context():
    with tempfile.TemporaryDirectory() as tmp:
        create_thread("test-1", tmp, "Test")

        for i in range(3):
            append_entry(
                thread_id="test-1",
                cwd=tmp,
                query=f"Question {i}",
                summary=f"Answer {i}",
                artifacts=[],
                turns=1,
            )

        context = load_context("test-1", tmp)
        assert context is not None
        assert "Question 0" in context
        assert "Question 2" in context


def test_load_context_no_thread():
    with tempfile.TemporaryDirectory() as tmp:
        result = load_context("nonexistent", tmp)
        assert result is None


def test_list_threads():
    with tempfile.TemporaryDirectory() as tmp:
        create_thread("a", tmp, "Thread A")
        create_thread("b", tmp, "Thread B")

        threads = list_threads(tmp)
        assert len(threads) == 2
        ids = {t["id"] for t in threads}
        assert ids == {"a", "b"}


def test_list_threads_empty():
    with tempfile.TemporaryDirectory() as tmp:
        threads = list_threads(tmp)
        assert threads == []


def test_get_thread():
    with tempfile.TemporaryDirectory() as tmp:
        create_thread("test-1", tmp, "My Thread")
        thread = get_thread("test-1", tmp)
        assert thread is not None
        assert thread["id"] == "test-1"
        assert thread["title"] == "My Thread"


def test_get_thread_not_found():
    with tempfile.TemporaryDirectory() as tmp:
        thread = get_thread("nonexistent", tmp)
        assert thread is None


def test_append_entry_default_kind_entry():
    """S3 (Phase 3): unspecified ``kind`` defaults to ``'entry'`` — back-compat."""
    with tempfile.TemporaryDirectory() as tmp:
        create_thread("t-1", tmp, "Test")
        append_entry("t-1", tmp, "q", "s", [], 1)

        messages = Path(tmp) / ".localsmartz" / "threads" / "t-1" / "messages.jsonl"
        lines = messages.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record.get("kind") == "entry"


def test_append_entry_reflection_kind_roundtrips():
    """S3 (Phase 3): kind='reflection' persists in JSONL and is readable."""
    with tempfile.TemporaryDirectory() as tmp:
        create_thread("t-refl", tmp, "Reflection test")
        append_entry(
            "t-refl",
            tmp,
            query="what is 15% of 2400?",
            summary="primary answered; reflector scored 0.8",
            artifacts=[],
            turns=2,
            kind="reflection",
        )

        messages = Path(tmp) / ".localsmartz" / "threads" / "t-refl" / "messages.jsonl"
        lines = messages.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record.get("kind") == "reflection"
        assert record.get("query") == "what is 15% of 2400?"
        assert record.get("turns") == 2
