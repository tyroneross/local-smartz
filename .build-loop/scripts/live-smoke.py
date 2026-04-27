#!/usr/bin/env python
"""Live smoke test for any shipped pattern against a local Ollama model.

Captures the span tree via the in-memory OTel exporter and pretty-prints
it. If Phoenix is reachable at localhost:6006, spans are ALSO exported
via OTLP so they show up in the Phoenix UI for visual verification.

Usage:
    .venv/bin/python .build-loop/scripts/live-smoke.py \
        [--pattern critic_loop|reflection|orchestrator|parallel] \
        [--model MODEL] [--prompt PROMPT]

Defaults:
    --pattern critic_loop  (back-compat with Phase 2 smoke)
    --model   qwen3:8b-q4_K_M
    --prompt  pattern-dependent

Exit code 0 on successful pattern run; 1 if the pattern raised.
Evidence file written to .build-loop/evals/2026-04-23-phase-3-{pattern}-smoke.md
(or 2026-04-23-live-smoke.md for back-compat on critic_loop).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_MODEL = "qwen3:8b-q4_K_M"

PATTERN_DEFAULT_PROMPTS = {
    "critic_loop": (
        "Name one concrete way a local-first LLM agent differs from a "
        "cloud-hosted one. One sentence."
    ),
    "reflection": (
        "Explain in one paragraph why a 24GB M4 Mac is a reasonable "
        "floor for running 8B-parameter local LLMs."
    ),
    "orchestrator": (
        "Compare two local LLM inference engines (Ollama vs llama.cpp) on "
        "three dimensions: model-format support, throughput tuning, and "
        "observability. Return a short comparison."
    ),
    "parallel": (
        "Give one sentence on what a 'reflexion' loop is in LLM agents."
    ),
}


def _phoenix_reachable() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:6006/", timeout=1) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def _setup_tracing(phoenix_up: bool):
    """Install OTel providers: always in-memory, optionally OTLP→Phoenix."""
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider = TracerProvider(
        resource=Resource.create({"service.name": "localsmartz-smoke"})
    )
    memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))

    if phoenix_up:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        otlp = OTLPSpanExporter(endpoint="http://localhost:6006/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(otlp))

    # OTel forbids overriding a set provider. Handle that gracefully.
    current = trace.get_tracer_provider()
    from opentelemetry.sdk.trace import TracerProvider as _RealProvider
    if isinstance(current, _RealProvider):
        # Attach our exporters to the existing provider.
        current.add_span_processor(SimpleSpanProcessor(memory_exporter))
        if phoenix_up:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as _OTLPExporter,
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor as _BSP

            current.add_span_processor(
                _BSP(_OTLPExporter(endpoint="http://localhost:6006/v1/traces"))
            )
        return current, memory_exporter

    trace.set_tracer_provider(provider)
    return provider, memory_exporter


async def _run_pattern(pattern: str, model: str, prompt: str):
    from localsmartz.patterns import REGISTRY
    from localsmartz.runners import get_runner

    runner = get_runner("ollama")
    thread_id = f"smoke-{pattern}-001"

    if pattern == "critic_loop":
        agents = {
            "writer": {"model_ref": {"provider": "ollama", "name": model}},
            "critic": {"model_ref": {"provider": "ollama", "name": model}},
        }
        profile = {
            "tier": "mini",
            "provider": "ollama",
            "budget": {"max_iterations": 2, "max_turns": 6, "threshold": 0.75},
        }
    elif pattern == "reflection":
        agents = {
            "primary": {"model_ref": {"provider": "ollama", "name": model}},
            "reflector": {"model_ref": {"provider": "ollama", "name": model}},
        }
        profile = {
            "tier": "mini",
            "provider": "ollama",
            "budget": {"max_iterations": 2, "max_turns": 6},
        }
    elif pattern == "orchestrator":
        agents = {
            "orchestrator": {"model_ref": {"provider": "ollama", "name": model}},
            "worker": {"model_ref": {"provider": "ollama", "name": model}},
        }
        profile = {
            "tier": "mini",
            "provider": "ollama",
            "override_mini_orchestrator": True,  # smoke runs on 24GB floor
            "use_deepagents_subagents": False,
            "budget": {},
        }
    elif pattern == "parallel":
        agents = {
            "sampler": {"model_ref": {"provider": "ollama", "name": model}},
        }
        profile = {
            "tier": "mini",
            "provider": "ollama",
            "budget": {"parallel_n": 2},  # 2 samples on mini for speed
        }
    else:
        raise ValueError(f"smoke: unknown pattern {pattern!r}")

    run_fn = REGISTRY[pattern]["run"]
    events = []
    async for ev in run_fn(
        prompt,
        agents=agents,
        profile=profile,
        runner=runner,
        ctx={"thread_id": thread_id, "cwd": str(ROOT)},
    ):
        events.append(ev)
    return events


def _format_tree(spans) -> str:
    by_id = {s.context.span_id: s for s in spans}
    children: dict[int, list] = {}
    roots = []
    for s in spans:
        parent = s.parent.span_id if s.parent else None
        if parent and parent in by_id:
            children.setdefault(parent, []).append(s)
        else:
            roots.append(s)

    def render(span, indent=0) -> list[str]:
        pad = "  " * indent
        attrs = dict(span.attributes)
        ls_attrs = {
            k: v for k, v in attrs.items() if k.startswith(("ls.", "pattern"))
        }
        line = f"{pad}- {span.name}"
        if ls_attrs:
            line += "  " + " ".join(
                f"{k}={v}" for k, v in sorted(ls_attrs.items())
            )
        out = [line]
        for c in sorted(
            children.get(span.context.span_id, []),
            key=lambda x: x.start_time,
        ):
            out.extend(render(c, indent + 1))
        return out

    lines = []
    for r in sorted(roots, key=lambda s: s.start_time):
        lines.extend(render(r))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--pattern",
        default="critic_loop",
        choices=sorted(PATTERN_DEFAULT_PROMPTS.keys()),
    )
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--prompt", default=None)
    # Back-compat: positional args still supported for the critic_loop path.
    ap.add_argument("positional", nargs="*", help=argparse.SUPPRESS)
    args = ap.parse_args()

    pattern = args.pattern
    model = args.model
    prompt = args.prompt or PATTERN_DEFAULT_PROMPTS[pattern]

    # Back-compat: if user passed `script.py model prompt` positionally and
    # didn't set --pattern, assume critic_loop.
    if args.positional and not args.prompt:
        if len(args.positional) >= 1:
            model = args.positional[0]
        if len(args.positional) >= 2:
            prompt = args.positional[1]

    phoenix_up = _phoenix_reachable()
    provider, memory_exporter = _setup_tracing(phoenix_up)

    started = time.perf_counter()
    error = None
    events = []
    try:
        events = asyncio.run(_run_pattern(pattern, model, prompt))
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

    provider.force_flush(timeout_millis=5000)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    spans = memory_exporter.get_finished_spans()
    tree = _format_tree(spans)

    # Evidence file path:
    #   Phase 3 patterns → 2026-04-23-phase-3-{pattern}-smoke.md
    #   critic_loop      → 2026-04-23-live-smoke.md (back-compat)
    if pattern == "critic_loop":
        evidence_name = "2026-04-23-live-smoke.md"
    else:
        evidence_name = f"2026-04-23-phase-3-{pattern}-smoke.md"
    evidence = ROOT / ".build-loop" / "evals" / evidence_name
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        f"""# Live smoke — {pattern} on {model}

- **date**: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
- **pattern**: {pattern}
- **model**: {model}
- **prompt**: {prompt!r}
- **elapsed**: {elapsed_ms} ms
- **phoenix_reachable**: {phoenix_up}
- **error**: {error or 'none'}
- **event count**: {len(events)}
- **event types**: {', '.join(e.get('type', '?') for e in events)}
- **span count**: {len(spans)}

## Span tree

```
{tree}
```

## Final event payload (truncated)

```json
{json.dumps(events[-2] if len(events) >= 2 else {}, indent=2)[:2000]}
```
"""
    )
    print(f"\n=== LIVE SMOKE RESULT ({pattern}) ===")
    print(
        f"model={model} elapsed={elapsed_ms}ms phoenix={phoenix_up} "
        f"spans={len(spans)} events={len(events)}"
    )
    print(f"evidence written to: {evidence}")
    print(f"\nspan tree:\n{tree}")

    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
