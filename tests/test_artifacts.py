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


def test_register_emits_artifact_span():
    """S2 (Phase 3): artifacts.register emits ls.artifact.register span.

    Attaches a SimpleSpanProcessor with an in-memory exporter to whatever
    TracerProvider is already installed (or installs a fresh one if none
    is set). OTel forbids *overriding* an existing provider at the global
    level, so we attach rather than replace.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    current = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()

    # If the current provider is the no-op default, install a real one.
    # Else attach an extra processor so we observe spans without stomping.
    if not isinstance(current, TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    else:
        current.add_span_processor(SimpleSpanProcessor(exporter))

    with tempfile.TemporaryDirectory() as tmp:
        register(
            path="/tmp/worker-output.md",
            format="markdown",
            title="Worker A output",
            cwd=tmp,
            thread_id="thread-42",
        )

    spans = exporter.get_finished_spans()
    artifact_spans = [s for s in spans if s.name == "ls.artifact.register"]
    assert artifact_spans, (
        f"expected at least one ls.artifact.register span; got {[s.name for s in spans]}"
    )
    span = artifact_spans[0]
    attrs = dict(span.attributes)
    assert attrs.get("ls.artifact.format") == "markdown"
    assert attrs.get("ls.artifact.thread_id") == "thread-42"
    assert attrs.get("ls.artifact.path_basename") == "worker-output.md"
    assert attrs.get("ls.artifact.id"), (
        f"ls.artifact.id attr missing; got {attrs!r}"
    )
