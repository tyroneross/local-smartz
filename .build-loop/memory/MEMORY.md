# Project Memory — local-smartz

Project-specific gotchas, conventions, and decisions for build-loop runs against `~/dev/git-folder/local-smartz/`. Loaded after global memory; overrides on conflict.

## Gotchas

- [SSE event taxonomy is a server-client contract](gotcha_serve_sse_live_smoke_required.md) — every new SSE event type emitted by `serve.py` must have a matching arm in the embedded UI's `handleEvent` switch, plus CSS, plus the broken-pipe break contract preserved. Live curl smoke is the gate.

## Decisions

_Empty. Add architecturally-significant project decisions here as `decision_<slug>.md` and link above._

## Conventions

_Empty._

## Contracts

_Empty._

---

## How this index works

- One bullet per entry under the relevant section.
- Each bullet links to a sibling `<type>_<slug>.md` file.
- The orchestrator reads global memory at `~/.build-loop/memory/MEMORY.md` first, then this file. Project entries override global on key conflict.
- Build-loop:build-loop SKILL.md §Phase 1 step 6 defines the load order.
