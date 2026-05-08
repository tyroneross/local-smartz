# Streaming + pricing follow-ups (run 3, 2026-05-08)

Closes the open items from the prior two build-loop runs (10 + 5 commits, all on
`main`, none pushed). Five commits, sequential, single-writer git contract
(orchestrator commits — implementers do not).

## Commits

### A. `chore(cost): verify Anthropic cache multipliers + Groq pricing rows`

**Status: shipped (132d022).**

Pure verification + documentation pass against authoritative sources:

- Anthropic cache multipliers (1.25× / 0.10×) — verified against
  `https://docs.claude.com/en/docs/build-with-claude/prompt-caching.md`
  pricing section (5-min ephemeral cache tier, which is what the runner
  pins via `cache_control={"type":"ephemeral"}`). Comments now cite the
  canonical `.md` URL so future refreshes have a stable source.
- Groq pricing rows — verified 2x against
  `https://console.groq.com/docs/models.md` AND the live
  `GET /openai/v1/models` API:
  - `llama-3.3-70b-versatile`  $0.59 / $0.79 — confirmed (production)
  - `llama-3.1-8b-instant`     $0.05 / $0.08 — confirmed (production)
  - `llama-4-scout-17b-16e`    $0.11 / $0.34 — confirmed (preview tier)
  - `llama-4-maverick-17b-128e` — DEPRECATED on Groq (no longer in either
    docs table; not returned by /v1/models). Row preserved with
    deprecation note; `CLOUD_TIER_TABLE['groq']['strong']` still references
    it. Repointing tier table is a separate scope (see "follow-ups" below).
  - `groq/compound` — Groq does not publish per-token pricing for the
    compound systems. Existing flat rate ($0.50 / $0.85) is a SWAG;
    note field tells the UI "Rate approximate".

`TAG:UNVERIFIED` markers removed from cost.py. All 40 cost tests green.

### B. `feat(serve): route cloud providers through streaming pattern in _stream_research`

**Goal: end-to-end SSE for cloud runs.**

Today every path in `_stream_research` (`_run_fast_path` /
`_run_graph_pipeline` / `_run_full_agent`) is Ollama-bound. The pattern-layer
streaming primitive (`patterns/single.py` + `cloud_*runner.stream_turn`)
ships token deltas, but no caller ever runs them when the user picks
`provider=anthropic|openai|groq`. SSE consumers see a stuck "Thinking…"
during the cloud call and then the full reply at the end.

Wire a new `_run_pattern_cloud(prompt, profile, model, thread_id, cwd, *, provider, pattern)` method that:

1. Skips the Ollama preflight (cloud runs don't need it).
2. Builds the runner via `runners.get_runner(provider)`.
3. Resolves the pattern from the request (default `"single"` when None).
4. Imports the pattern module dynamically and calls its `run()`.
5. Walks the async iterator inside an asyncio loop, converting each
   `PatternEvent` to an SSE event:
   - `text_delta` → `{type: "text", content: delta}` (matches the
     existing SSE consumer schema in the macOS app + HTML UI).
   - `turn` (with role=primary or specialist) → `{type: "text", content}`
     emitted ONLY when the turn carries content the user hasn't already
     seen via text_deltas (i.e. fallback path on runners without
     `stream_turn`). When text_deltas already shipped the turn's body,
     the turn event is dropped to avoid double-emission.
   - `budget_warn` → pass through as `{type: "budget_warn", ...}`.
   - `pattern_start` / `done` → pass through verbatim (UI uses `done` to
     stop the spinner).
   - `iteration` (critic_loop / reflection) → `{type: "iteration", ...}`.
   - All other events (`tool`, `score`, etc.) — pass through.
6. Owns its own heartbeat pulse + OTel span.
7. Calls `append_entry` with the assembled response on success.

`_stream_research` selects this path BEFORE the routing decision when
`provider` is set and is not `"ollama"`. Order of precedence:
  - explicit `provider == "ollama"` → existing routing (fast / graph / full).
  - explicit `provider in {anthropic, openai, groq}` → `_run_pattern_cloud`.
  - `provider is None` → existing routing (default Ollama).

Test plan:
- Smoke: spin up the handler with a `_FakeHandler` test double, monkeypatch
  `runners.get_runner` to return a streaming fake runner (same shape as
  `tests/test_pattern_single_stream._StreamingRunner`), POST `/api/research`
  with `provider=groq`, assert SSE events contain `text_delta` deltas
  before the `done` event.
- Existing 812 tests must remain green.

Files owned: `src/localsmartz/serve.py`. Tests: new
`tests/test_serve_cloud_streaming.py`.

### C. `feat(patterns/orchestrator): instrument worker turns with budget_warn`

Extend the round-2 budget instrumentation to orchestrator workers. Today
only lead-side turns (planner, synthesizer) tick the BudgetTracker;
worker turns inside `_dispatch_workers_path_b._one_worker` and
`_dispatch_workers_path_a` do not.

Plumb `usage` through the worker return shape:

```python
return {
    "role": ..., "artifact_id": ..., "summary": ..., "duration_ms": ...,
    "status": ...,
    "usage": dict(turn.get("usage") or {}),  # NEW
}
```

After the worker fan-out completes, before the synthesize turn, iterate the
worker outputs and tick the BudgetTracker once per worker. Tracker fires
the warn at most once per session (existing `_warned` flag), so even if
ten workers cross the threshold simultaneously the user sees one event.

Path A returns one tool-output per task without per-call usage today
(DeepAgents wraps the bookkeeping); attribute usage to `{}` for Path A
workers so the tracker stays sound but doesn't overcount.

Files owned: `src/localsmartz/patterns/orchestrator.py`. Tests: extend
`tests/test_pattern_budget_warn.py` with an orchestrator-flavored case.

### D. `feat(patterns): text_delta streaming for chain + router + orchestrator`

Extend the `stream_turn` consumption pattern from `patterns/single.py` to
the three sequential patterns where token-by-token is unambiguous:

- `chain.py` — three stages emit `text_delta` per role (gather/analyze/write)
  + the existing `turn` event closes the role boundary.
- `router.py` — the router's classification turn isn't streamed (it's
  one-word output), but the specialist turn IS streamed via `text_delta`
  events with `role="specialist:<label>"`.
- `orchestrator.py` — three streamable phases:
  - `text_delta` per planning-turn token (role=`orchestrator.plan`)
  - `text_delta` per synthesize-turn token (role=`final`)
  - Worker turns are NOT streamed token-level (they fan out concurrently
    in Path B; interleaved tokens would be unreadable). The full-text
    `turn` events per worker stay as-is.

Add a small private helper `_stream_or_run(runner, ...)` in `patterns/base.py`
to factor out the "use stream_turn if present, else run_turn" idiom that
single.py already encodes — keeps the four streamable patterns from
duplicating the assemble/error/fallback logic.

Files owned: `src/localsmartz/patterns/base.py`,
`src/localsmartz/patterns/chain.py`, `src/localsmartz/patterns/router.py`,
`src/localsmartz/patterns/orchestrator.py`. Tests: new test file
`tests/test_pattern_streaming_coverage.py` exercising the three patterns
with the existing `_StreamingRunner` test double.

### E. `feat(patterns): phase-boundary events for critic_loop + parallel + reflection`

For patterns where token-level interleaving is genuinely ambiguous (multiple
roles streaming concurrently or interleaved across iterations), ship phase
markers + full-text events instead of text_delta.

- `critic_loop.py` — emit `phase_start: writer`, `phase_end: writer`,
  `phase_start: critic`, `phase_end: critic` per iteration. The full-text
  `turn` events per role stay as-is.
- `parallel.py` — emit `phase_start: sample.<n>`, `phase_end: sample.<n>`
  per sample, plus `phase_start: synthesize` / `phase_end: synthesize`.
  Concurrent samples' tokens cannot be cleanly interleaved on a single
  text stream.
- `reflection.py` — emit `phase_start: primary`, `phase_end: primary`,
  `phase_start: reflector`, `phase_end: reflector` per iteration. Same
  reasoning as critic_loop — interleaved stagnation reasoning would mix
  with the primary's output.

Document the design choice in each pattern's docstring (one paragraph
each) so a future implementer doesn't try to "fix" the apparent gap.

Phase events are the simplest possible shape:
```json
{"type": "phase_start", "phase": "writer", "iteration": 1}
{"type": "phase_end",   "phase": "writer", "iteration": 1}
```
The PatternEvent TypedDict gains two new optional fields: `phase: str`
and `iteration: int` is already there.

Files owned: `src/localsmartz/patterns/base.py` (TypedDict update),
`src/localsmartz/patterns/critic_loop.py`,
`src/localsmartz/patterns/parallel.py`,
`src/localsmartz/patterns/reflection.py`. Tests: extend
`tests/test_patterns.py` (or a new file) with phase-boundary assertions.

## Verification

After commit A: `uv run pytest tests/test_cost*.py` — green ✅.
After commit B: extend `tests/test_serve_*` with cloud-streaming assertions.
After commit C: extend `tests/test_pattern_budget_warn.py` with orchestrator case.
After commit D: new `tests/test_pattern_streaming_coverage.py`.
After commit E: extend pattern tests with phase-boundary assertions.
Final: full `uv run pytest` must remain ≥ 812 passed.

## Out of scope

- Repointing `CLOUD_TIER_TABLE['groq']['strong']` away from Maverick.
  Tracked as a follow-up; prior commit's tier table tests assume the
  current strong-tier model name.
- New patterns. Six exist; this run only extends streaming coverage.
- Token-level streaming for critic_loop / parallel / reflection. The
  phase-boundary shape is the documented terminal answer for those.
