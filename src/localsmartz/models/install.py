"""Model install — shared function for HTTP SSE + CLI.

Wraps ``ollama.stream_pull`` and emits both:
- progress events yielded to the caller (for SSE or CLI progress bars)
- an OTel span ``ls.model.install`` with size/duration attrs

Progress event schema (stable):
    {"type": "status", "text": "pulling manifest"}
    {"type": "progress", "digest": "...", "completed": int, "total": int}
    {"type": "done", "duration_ms": int, "bytes": int}
    {"type": "error", "message": str}
"""
from __future__ import annotations

import time
from typing import Iterator

from localsmartz.observability import get_tracer


def install(model_name: str, *, insecure: bool = False, timeout: float = 7200.0) -> Iterator[dict]:
    """Install an Ollama model; yield normalized progress events.

    Use from CLI or HTTP handler. Emits an OTel span even when the collector
    isn't running (the tracer is a no-op in that case).
    """
    from localsmartz.ollama import stream_pull  # lazy — avoids import-time Ollama dep

    tracer = get_tracer("localsmartz.models.install")
    start = time.time()
    total_bytes = 0
    with tracer.start_as_current_span("ls.model.install") as span:
        span.set_attribute("ls.model.name", model_name)
        span.set_attribute("ls.model.insecure", insecure)
        try:
            for chunk in stream_pull(model_name, insecure=insecure, timeout=timeout):
                if not isinstance(chunk, dict):
                    continue
                err = chunk.get("error")
                if err:
                    span.set_attribute("ls.model.error", str(err)[:500])
                    yield {"type": "error", "message": str(err)}
                    return
                status = chunk.get("status", "")
                completed = chunk.get("completed")
                total = chunk.get("total")
                digest = chunk.get("digest", "")
                if completed is not None and total is not None:
                    total_bytes = max(total_bytes, int(total or 0))
                    yield {
                        "type": "progress",
                        "digest": digest,
                        "completed": int(completed or 0),
                        "total": int(total or 0),
                    }
                elif status:
                    yield {"type": "status", "text": status}
                if status == "success":
                    break
        except Exception as exc:  # noqa: BLE001 — we surface via event
            span.record_exception(exc)
            yield {"type": "error", "message": str(exc)}
            return

        duration_ms = int((time.time() - start) * 1000)
        span.set_attribute("ls.model.bytes", total_bytes)
        span.set_attribute("ls.model.duration_ms", duration_ms)
        yield {"type": "done", "duration_ms": duration_ms, "bytes": total_bytes}
