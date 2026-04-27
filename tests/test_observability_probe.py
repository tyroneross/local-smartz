"""Phoenix probe tests — exercises probe_collector without requiring Phoenix."""
from __future__ import annotations

from localsmartz import observability


def test_probe_collector_returns_false_on_no_daemon(monkeypatch) -> None:
    """No Phoenix running anywhere → probe must return False, not raise."""
    # httpx is imported inside probe_collector; monkeypatching the import is
    # fragile, so we short-circuit via env by pointing at a black-hole URL.
    monkeypatch.setattr(
        observability,
        "DEFAULT_OTLP_ENDPOINT",
        "http://127.0.0.1:59999/v1/traces",
    )
    assert observability.probe_collector() is False


def test_auto_setup_respects_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("LOCALSMARTZ_OBSERVE", "0")
    # probe_collector would say False anyway, but opt-out must win regardless.
    assert observability.auto_setup_if_reachable() is False


def test_status_includes_collector_reachable_field() -> None:
    s = observability.status()
    assert "collector_reachable" in s
    assert isinstance(s["collector_reachable"], bool)
    assert isinstance(s["pii_redaction"], bool)


def test_redact_pii_attributes_redacts_payloads_and_patterns() -> None:
    attrs = observability.redact_pii_attributes({
        "llm.input_messages": "email me at person@example.com",
        "http.url": "https://example.test/users/person@example.com",
        "api_key": "sk_test_12345678901234567890",
        "model.name": "gpt-oss:20b",
    })
    assert attrs["llm.input_messages"] == "[REDACTED]"
    assert "[REDACTED_EMAIL]" in attrs["http.url"]
    assert attrs["api_key"] == "[REDACTED]"
    assert attrs["model.name"] == "gpt-oss:20b"


def test_redacting_span_exporter_sanitizes_attributes() -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        SimpleSpanProcessor(observability.PIIRedactingSpanExporter(exporter))
    )
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("redaction-test") as span:
        span.set_attribute("user.email", "person@example.com")
        span.set_attribute("model.name", "gpt-oss:20b")
        span.add_event("payload", {"prompt": "My SSN is 123-45-6789"})

    spans = exporter.get_finished_spans()
    assert spans[0].attributes["user.email"] == "[REDACTED_EMAIL]"
    assert spans[0].attributes["model.name"] == "gpt-oss:20b"
    assert spans[0].events[0].attributes["prompt"] == "[REDACTED]"
