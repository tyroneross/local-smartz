---
name: SSE event taxonomy in serve.py is a server-client contract
description: Every new SSE event type emitted from serve.py must add a UI handleEvent arm + CSS + preserve the broken-pipe break contract. Live curl smoke is the gate.
type: gotcha
---

# serve.py SSE event taxonomy is a server-client contract

**Rule:** Any change in local-smartz that touches `src/localsmartz/serve.py` (especially the embedded HTML/JS `handleEvent` switch around line 552), `src/localsmartz/tools/web.py`, `src/localsmartz/runners/cloud_*.py`, or `src/localsmartz/patterns/*.py` must:

1. Pass `uv run pytest` (the existing baseline gate).
2. Pass a **live 5-second curl POST smoke** against `/api/research` with the new code running.
3. Confirm every observed SSE event type has a matching arm in the UI's `handleEvent`.
4. Preserve `tests/test_sse_cancellation.py`'s broken-pipe break contract — any new emit point added before the streaming loop must let `BrokenPipeError` propagate to the existing `try/finally` so `pulse.stop()` runs.

**Why:** Pytest with mocked SDKs greenlit 27 commits over a multi-session run on 2026-05-08 yet shipped two real user-facing bugs:

1. `tools/web.py:_extract_text` raised `'NavigableString' object has no attribute 'children'` when `soup.select_one('[role="main"]')` returned a text node. The graph_pipeline failed silently because pytest never iterated a real DOM. Fixed in commit `7d26e70`; regression test `tests/test_web_extract.py`.
2. The embedded UI's `handleEvent` had no cases for `stage` / `status` / `heartbeat` SSE event types. The server emitted them; the browser ignored them; the user saw `"Output will appear here..."` forever between Run and first text. Pytest never rendered the embedded HTML. Fixed in commit `7d26e70`; broken-pipe contract preserved in `8be7c96`.

**How to apply:**

```bash
# Restart server in background
pkill -f "localsmartz --serve" 2>/dev/null
uv run localsmartz --serve --port 11435 > /tmp/localsmartz-serve.log 2>&1 &

# Wait for ready
for i in {1..15}; do curl -fsS -m 1 http://localhost:11435/api/status >/dev/null 2>&1 && break; sleep 1; done

# Smoke: collect observed event types in 5 seconds
curl -sN -X POST http://localhost:11435/api/research \
  -H "Content-Type: application/json" \
  -d '{"prompt":"OK","focus_agent":"writer"}' \
  --max-time 5 | grep -oE '"type":\s*"[^"]+"' | sort -u
```

Cross-reference output against `handleEvent` arms in `serve.py` near line 552. Any observed type without a renderer is a silent-server, ignored-client failure.

**SSE event taxonomy as of 2026-05-08 (commit 8be7c96):**

| Event type | Server emits at | UI renders as |
|---|---|---|
| `status` (stage:ready/loading) | `_send_ready_event` and warmup paths | `▸ Model Ready: <model> (resident\|warmed in Ns)` |
| `stage` (stage:starting/<role>) | Top of `_run_*` handlers + graph node sinks | `▸ <Stage Name>` muted line |
| `text` | Streaming token deltas + final answer | Buffered streaming text |
| `tool` | Tool-call surfacing | Teal pill badge |
| `tool_error` | `_run_graph_pipeline` except branch + per-tool errors | Red `[<name>] <message>` line |
| `heartbeat` (elapsed_s:N) | `_HeartbeatPulse` ticks every 15s | Single coalesced `⋯ still working (Ns)` line that updates in place |
| `done` | End of fast_path / graph / full agent | `Done (Ns)` line + clears running state |
| `error` | Top-level handler failures | Red error block + clears running state |

Adding a new event type? Add the `handleEvent` arm + CSS class IN THE SAME COMMIT, and re-run the live smoke before claiming green.

**Adjacent risks:**

- `tools/web.py` BeautifulSoup edge cases — `soup.select_one(...)` and `soup.find(...)` can return `NavigableString` (text node) rather than `Tag`. Always guard with `if not hasattr(element, "children"): return str(element).strip()` before iterating.
- DeepAgents `RunnableRetry` — never wrap a chat model with `.with_retry()` before `create_deep_agent` (unhashable + strips bind_tools). Retry at the SDK call layer instead. See `~/.claude/projects/-Users-tyroneross/memory/reference_deepagents_runnable_retry.md`.
- Provider abstraction lives in `src/localsmartz/runners/`. `_create_model` (DeepAgents) and `create_langchain_model` (patterns) are deliberately parallel; do not collapse.
