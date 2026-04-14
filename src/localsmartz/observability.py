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

log = logging.getLogger(__name__)

# Default Phoenix collector endpoint. Phoenix accepts standard OTLP/HTTP traces.
DEFAULT_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "http://localhost:6006/v1/traces",
)
DEFAULT_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "local-smartz")

_INSTRUMENTED = False


def is_observability_enabled() -> bool:
    """Honor an env-var override so the user doesn't have to pass ``--observe``."""
    val = os.environ.get("LOCALSMARTZ_OBSERVE", "").lower().strip()
    return val in ("1", "true", "yes", "on")


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
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
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
        "phoenix_install_hint": (
            "Run Phoenix locally:\n"
            "  docker run -p 6006:6006 arizephoenix/phoenix\n"
            "OR\n"
            "  pip install arize-phoenix && python -m phoenix.server.main serve\n"
            "Then open http://localhost:6006"
        ),
    }
