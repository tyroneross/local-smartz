"""Tests for artifact registration and tracking."""

import tempfile
from pathlib import Path

from localsmartz.artifacts import register, list_artifacts


def test_register_artifact():
    with tempfile.TemporaryDirectory() as tmp:
        artifact = register(
            path="/tmp/report.md",
            format="markdown",
            title="Test Report",
            cwd=tmp,
        )
        assert artifact["path"] == "/tmp/report.md"
        assert artifact["format"] == "markdown"
        assert artifact["title"] == "Test Report"
        assert artifact["id"]
        assert artifact["created_at"]


def test_register_with_thread():
    with tempfile.TemporaryDirectory() as tmp:
        artifact = register(
            path="/tmp/report.md",
            format="markdown",
            title="Test Report",
            cwd=tmp,
            thread_id="thread-1",
        )
        assert artifact["thread_id"] == "thread-1"


def test_list_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        register(path="/tmp/a.md", format="markdown", title="A", cwd=tmp)
        register(path="/tmp/b.xlsx", format="xlsx", title="B", cwd=tmp)

        artifacts = list_artifacts(tmp)
        assert len(artifacts) == 2


def test_list_artifacts_by_thread():
    with tempfile.TemporaryDirectory() as tmp:
        register(path="/tmp/a.md", format="markdown", title="A", cwd=tmp, thread_id="t1")
        register(path="/tmp/b.md", format="markdown", title="B", cwd=tmp, thread_id="t2")
        register(path="/tmp/c.md", format="markdown", title="C", cwd=tmp, thread_id="t1")

        t1_artifacts = list_artifacts(tmp, thread_id="t1")
        assert len(t1_artifacts) == 2

        t2_artifacts = list_artifacts(tmp, thread_id="t2")
        assert len(t2_artifacts) == 1


def test_list_artifacts_empty():
    with tempfile.TemporaryDirectory() as tmp:
        artifacts = list_artifacts(tmp)
        assert artifacts == []


def test_register_duplicate_updates():
    with tempfile.TemporaryDirectory() as tmp:
        register(path="/tmp/report.md", format="markdown", title="V1", cwd=tmp, thread_id="t1")
        register(path="/tmp/report.md", format="markdown", title="V2", cwd=tmp, thread_id="t1")

        artifacts = list_artifacts(tmp)
        assert len(artifacts) == 1
        assert artifacts[0]["title"] == "V2"
