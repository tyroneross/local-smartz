"""OpenTelemetry tracing for local-smartz.

Sends OTLP/HTTP spans to a local Arize Phoenix collector at the standard
``localhost:6006`` endpoint by default. If Phoenix isn't running, OTel just
buffers/drops spans silently — there is **no impact** on the agent.

Usage:
    1. Run Phoenix locally:
         docker run -p 6006:6006 arizephoenix/phoenix
       OR
         pip install arize-phoenix && python -m phoenix.server.main serve

    2. Start local-smartz with the ``--observe`` flag (or set
       ``LOCALSMARTZ_OBSERVE=1`` in the environment).

    3. Open http://localhost:6006 to see every Ollama call, every tool
       invocation, every prompt + response with timing.

The Python deps are part of the base install — they're light and the bundled
DMG ships with them so you can flip the flag without a separate pip step.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence

log = logging.getLogger(__name__)

# Default Phoenix collector endpoint. Phoenix accepts standard OTLP/HTTP traces.
DEFAULT_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "http://localhost:6006/v1/traces",
)
DEFAULT_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "local-smartz")

_INSTRUMENTED = False

_REDACTED = "[REDACTED]"
_PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[REDACTED_NUMBER]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\b(?:sk|pk|rk|xox[baprs]|ghp|github_pat)_[A-Za-z0-9_\-]{12,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"(?<!\w)/(?:Users|home)/[^\s'\"<>),;\]]+"), "/[REDACTED_PATH]"),
    (re.compile(r"\b[A-Za-z]:\\Users\\[^\s'\"<>),;\]]+"), "[REDACTED_PATH]"),
)
_PAYLOAD_KEY_PARTS = (
    "prompt",
    "prompts",
    "message",
    "messages",
    "input",
    "output",
    "completion",
    "response",
    "content",
    "query",
    "document",
    "documents",
    "text",
)
_SECRET_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
)


def is_observability_enabled() -> bool:
    """Honor an env-var override so the user doesn't have to pass ``--observe``."""
    val = os.environ.get("LOCALSMARTZ_OBSERVE", "").lower().strip()
    return val in ("1", "true", "yes", "on")


def is_pii_redaction_enabled() -> bool:
    """Default-on guard before any trace leaves this process."""
    val = os.environ.get("LOCALSMARTZ_TELEMETRY_REDACT", "1").lower().strip()
    return val not in ("0", "false", "no", "off")


def redact_pii_text(text: str) -> str:
    """Redact common PII/secrets from a string while preserving readability."""
    out = text
    for pattern, replacement in _PII_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _PAYLOAD_KEY_PARTS + _SECRET_KEY_PARTS)


def redact_pii_value(value, *, key: str = ""):
    """Return an OpenTelemetry-safe value with PII removed.

    Prompt/response-shaped attributes are replaced wholesale because LangChain
    instrumentation can place full user text there. Other strings are pattern
    redacted so operational labels remain useful.
    """
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if _sensitive_key(key):
            return _REDACTED
        return redact_pii_text(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        redacted = [redact_pii_value(item, key=key) for item in value]
        if all(isinstance(item, str) for item in redacted):
            return redacted
        if all(isinstance(item, bool) for item in redacted):
            return redacted
        if all(isinstance(item, int) and not isinstance(item, bool) for item in redacted):
            return redacted
        if all(isinstance(item, float) for item in redacted):
            return redacted
        return [_REDACTED if item is not None else "" for item in redacted]
    return _REDACTED if _sensitive_key(key) else redact_pii_text(str(value))


def redact_pii_attributes(attributes: Mapping | None) -> dict:
    """Redact a span/event attribute mapping."""
    if not attributes:
        return {}
    return {
        str(key): redact_pii_value(value, key=str(key))
        for key, value in attributes.items()
    }


class _RedactedSpan:
    """ReadableSpan proxy that sanitizes attributes and events at export time."""

    def __init__(self, span):
        self._span = span

    def __getattr__(self, name):
        return getattr(self._span, name)

    @property
    def attributes(self):
        return redact_pii_attributes(getattr(self._span, "attributes", None))

    @property
    def events(self):
        try:
            from opentelemetry.sdk.trace import Event
        except Exception:  # pragma: no cover - defensive fallback
            return getattr(self._span, "events", ())
        return tuple(
            Event(
                event.name,
                redact_pii_attributes(getattr(event, "attributes", None)),
                getattr(event, "timestamp", None),
            )
            for event in getattr(self._span, "events", ())
        )


class PIIRedactingSpanExporter:
    """Exporter wrapper that redacts spans before forwarding them."""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def export(self, spans):
        return self._wrapped.export(tuple(_RedactedSpan(span) for span in spans))

    def shutdown(self):
        return self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        force_flush = getattr(self._wrapped, "force_flush", None)
        if callable(force_flush):
            return force_flush(timeout_millis)
        return True


class PIIRedactingSpanProcessor:
    """Batch span processor with a redacting exporter in front of the collector."""

    def __init__(self, exporter):
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        self._delegate = BatchSpanProcessor(PIIRedactingSpanExporter(exporter))

    def on_start(self, span, parent_context=None):
        return self._delegate.on_start(span, parent_context=parent_context)

    def on_end(self, span):
        return self._delegate.on_end(span)

    def shutdown(self):
        return self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self._delegate.force_flush(timeout_millis)


def probe_collector(endpoint: str | None = None, timeout: float = 1.0) -> bool:
    """Return True if a Phoenix-style OTLP collector is reachable.

    Used at startup to enable observability automatically without a CLI flag.
    Probes the base URL (``/v1/traces`` trimmed to root) with a 1-second HEAD
    so we don't block the main process on a long DNS lookup.
    """
    try:
        resolved = endpoint or DEFAULT_OTLP_ENDPOINT
        # Strip ``/v1/traces`` suffix to hit the Phoenix health root.
        base = resolved.rstrip("/")
        if base.endswith("/v1/traces"):
            base = base[: -len("/v1/traces")]

        import httpx  # local import: heavy

        with httpx.Client(timeout=timeout) as client:
            # Prefer HEAD; fall back to GET on 405.
            try:
                resp = client.head(base)
            except httpx.HTTPError:
                return False
            if resp.status_code == 405:
                try:
                    resp = client.get(base)
                except httpx.HTTPError:
                    return False
            return 200 <= resp.status_code < 500
    except Exception:  # noqa: BLE001 — probe must never crash
        return False


def auto_setup_if_reachable(
    *,
    endpoint: str | None = None,
    service_name: str | None = None,
) -> bool:
    """Turn observability on whenever a collector is reachable.

    Called by ``__main__.py`` at startup so Phoenix users get traces out of
    the box without passing ``--observe``. Explicit opt-out is still honored
    via ``LOCALSMARTZ_OBSERVE=0``.
    """
    val = os.environ.get("LOCALSMARTZ_OBSERVE", "").lower().strip()
    if val in ("0", "false", "no", "off"):
        return False  # User said no — respect it.
    if not probe_collector(endpoint):
        return False
    return setup_observability(endpoint=endpoint, service_name=service_name)


def setup_observability(
    *,
    endpoint: str | None = None,
    service_name: str | None = None,
) -> bool:
    """Idempotently initialize the OTel SDK + LangChain auto-instrumentation.

    Returns True on success, False if any required dependency is missing or
    the SDK fails to start. Safe to call multiple times — the second call is
    a no-op once instrumentation is registered.
    """
    global _INSTRUMENTED
    if _INSTRUMENTED:
        return True

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from openinference.instrumentation.langchain import LangChainInstrumentor
    except ImportError as e:  # pragma: no cover — defensive, deps are pinned
        log.warning(
            "Observability deps missing (%s). Install with: "
            "pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http "
            "openinference-instrumentation-langchain",
            e,
        )
        return False

    resolved_endpoint = endpoint or DEFAULT_OTLP_ENDPOINT
    resolved_service = service_name or DEFAULT_SERVICE_NAME

    try:
        provider = TracerProvider(
            resource=Resource.create({
                "service.name": resolved_service,
                "service.version": "0.1.0",
            })
        )
        exporter = OTLPSpanExporter(endpoint=resolved_endpoint)
        if is_pii_redaction_enabled():
            provider.add_span_processor(PIIRedactingSpanProcessor(exporter))
        else:
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        LangChainInstrumentor().instrument(tracer_provider=provider)
    except Exception as e:  # noqa: BLE001 — never crash the agent for observability
        log.warning("Could not initialize observability: %s", e)
        return False

    _INSTRUMENTED = True
    log.info(
        "Observability enabled — exporting OTel spans to %s as service '%s'",
        resolved_endpoint,
        resolved_service,
    )
    return True


def get_tracer(name: str = "local-smartz"):
    """Return an OTel tracer, or a no-op tracer if instrumentation isn't set up.

    Lets call sites emit spans unconditionally without first checking
    ``_INSTRUMENTED`` — if OTel deps aren't loaded, the default global
    tracer is a no-op that drops spans silently.
    """
    try:
        from opentelemetry import trace  # type: ignore
        return trace.get_tracer(name)
    except Exception:
        # Return a no-op tracer shim so callers can still use `with ...`.
        class _NoopSpan:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def set_attribute(self, *a, **k): pass
            def record_exception(self, *a, **k): pass

        class _NoopTracer:
            def start_as_current_span(self, *a, **k):  # noqa: D401
                return _NoopSpan()

        return _NoopTracer()


def status() -> dict:
    """Snapshot for the /api/observability/info endpoint."""
    return {
        "enabled": _INSTRUMENTED,
        "endpoint": DEFAULT_OTLP_ENDPOINT,
        "service_name": DEFAULT_SERVICE_NAME,
        "env_override": is_observability_enabled(),
        "pii_redaction": is_pii_redaction_enabled(),
        "collector_reachable": probe_collector(),
        "phoenix_install_hint": (
            "Run Phoenix locally:\n"
            "  docker run -p 6006:6006 arizephoenix/phoenix\n"
            "OR\n"
            "  pip install arize-phoenix && python -m phoenix.server.main serve\n"
            "Then open http://localhost:6006"
        ),
    }
