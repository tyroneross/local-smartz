# Handoff for Codex — local-smartz session 2026-04-26

**Branch:** `main` · **HEAD:** `35be23a` · **Origin:** synced.
**Repo:** `/Users/tyroneross/dev/git-folder/local-smartz`
**Predecessor:** Claude Opus 4.7 (10 commits, `61b2736 → dda4027`) + Codex continuation (`50701ec`, `89ebaad`, `35be23a`).

---

## What was done

### Codex continuation after this handoff
- **macOS packaging / model display:** committed `50701ec` (`Polish model routing and macOS packaging`).
- **Query DOE optimization:** committed `89ebaad` (`Optimize query routing DOE`). Added `src/localsmartz/query_doe.py`, tightened fast-path routing for current-data, recommendations/model selection, citations, and debugging prompts, archived 10 optimization loops. DOE score improved `1280.684 → 2996.875`; 8 kept, 2 discarded.
- **Local model eval matrix:** committed `35be23a` (`Add local model eval matrix`). Added `src/localsmartz/model_eval.py` and `localsmartz-model-eval`; live matrix showed both `gpt-oss:20b` and `qwen3:8b-q4_K_M` pass 6/6, with `gpt-oss:20b` faster in that run.
- **Latest validation:** Python full suite **662 passed, 4 skipped**. Branch pushed and synced to `origin/main`.

### Phase 3 multi-agent (already shipped before this session, ref: `61b2736`)
Reflection / Orchestrator-Workers / Parallelization patterns + S1 runner factory + S2 artifact spans + S3 threads `kind` param. 31 tests.

### `/build-loop:research` + `/ibr:ibr` loop (Iter 0–8, this session)
- **Iter 1 — backend:** `runners/harmony.py` (gpt-oss `<|channel|>` token parser), `validation.TurnCallDeduplicator` + `IntentAnchor` (per-turn dedup + original-prompt re-injection), `local-smartz` console-script alias, PowerPoint→markdown policy in agent system prompts, eval default-model fallback chain. +55 tests (562→617).
- **Iter 2 — gemma4:** `recommended_for_tier` includes `gemma4:26b` on standard + full; `localsmartz model add` prints role suggestions for catalog models. +4 tests.
- **Iter 3 — plugin validator (Anthropic plugin spec compliance):** `discovery.find_agent_files`, `validator.validate_agent_md` (frontmatter `name` / `description` / `model` / `effort` / `maxTurns` / `disallowedTools`); spec-driven downgrades MISSING_AUTHOR / MISSING_NAME (skill) / MISSING_ALLOWED_TOOLS from errors → warnings. Spec snapshot at `.build-loop/research/2026-04-26-anthropic-plugin-spec-snapshot.md`. +16 tests.
- **Iter 4–7 — SwiftUI AX:** 10 Strong-checkpoint accessibilityLabel fixes across SetupHelp / SetupView / PatternTab / ModelsTab / EvalTab / ResearchView. +4 tests.
- **Iter 8 — touch targets:** Research/Author tabs, New Research, Send button now ≥24px. Stale `app/build/` removed (incremental xcodebuild was returning false-success against a March-16 binary).

### User-driven fixes after the loop
- **Composer:** drag handle, multiline `TextField(axis: .vertical)` + `lineLimit(1...10)`, RoundedRectangle container, height persisted to `UserDefaults["composerHeight"]`.
- **Fast-path perf:** `fast_path_stream` now picks `fast` role > planning fallback; `ChatOllama` gains `keep_alive="30m"`.
- **Sidebar:** `AgentRow` shows the per-agent effective model.
- **Queue:** `queuedPrompts` state — pressing Enter while streaming queues; on stream end auto-runs next; badge above input opens popover.
- **Trace hint:** `→ next: <model>` above input when prompt non-empty + not streaming.
- **Fonts:** global bump (10→12, 11→13, 12→14, 13→15, 14→16, 16→18) across 11 view files.
- **Toolbar model name:** `BackendStatusResponse` decodes `effective_model`; `refreshStatus` populates `currentModel` on launch from /api/status (the 30s poll runs immediately).
- **CRITICAL perf unblock — backend reinstall:** macOS `BackendManager` spawns from `~/.local/bin/localsmartz` (priority 2 in `resolveBackendSpawn` at `app/LocalSmartz/Services/BackendManager.swift:307`). That uv-tool install was stale all session — predating every Python fix above. Reinstalled with `uv tool install --reinstall --from /Users/tyroneross/dev/git-folder/local-smartz localsmartz`. Now serves repo source.

### Claude handoff test state before Codex continuation
- Python: **648 passed**, 4 skipped, 0 failed.
- macOS Debug: BUILD SUCCEEDED (clean, not incremental).

---

## ⚠️ Unverified / needs visual confirmation

These shipped in code + clean rebuild + relaunch, but the IBR scan caught the SetupView (post-defaults-reset) so the ResearchView surfaces weren't visually re-confirmed:

1. **Toolbar model name on launch.** Should read `Model: gpt-oss:20b ● Ready` after Get Started (not "FULL"). Backend `/api/status` confirmed returning `effective_model: gpt-oss:20b`. Code path verified by reading. Not yet seen rendered.
2. **Message queue.** Press Enter twice during streaming → expect "1 queued" badge above input + auto-send when first finishes.
3. **`→ next: <model>` hint.** Type into composer (don't send) → expect single hint line above input.
4. **Sidebar agent model lines.** Were visible in earlier scan (`Planner: qwen3:8b-q4_K_M` etc.); should still be there.
5. **Fast-path perf.** Cold first query: ~5–15s. Warm second query within 30 min: TAG:INFERRED <2s first token (keep_alive holds model in VRAM).

**Verify command sequence:**
```
pkill -9 -f "Local Smartz"
open "/Users/tyroneross/Library/Developer/Xcode/DerivedData/LocalSmartz-adskgbdiipgunggnzfecqpkzjefl/Build/Products/Debug/Local Smartz.app"
# wait ~5s, click Get Started, send "what time is it", then send a follow-up
curl -sS http://localhost:11435/api/status | python3 -m json.tool
```
Expected: `effective_model: "gpt-oss:20b"`, toolbar shows that name, fast-path marker in chat shows the same.

---

## Known sharp edges

- **xcodebuild incremental returns false-success.** Always `xcodebuild -scheme LocalSmartz -configuration Debug clean` before build. Verified twice this session — the binary timestamp is the proof.
- **Repo `app/build/` is gone (intentional).** Build outputs live in `~/Library/Developer/Xcode/DerivedData/LocalSmartz-adskgbdiipgunggnzfecqpkzjefl/Build/Products/Debug/Local Smartz.app`.
- **macOS app spawn order:** bundled python (only in DMG) → `~/.local/bin/localsmartz` shim → user pythonPath → uv-tool venv. The shim is the active path in dev.
- **SourceKit ghost errors** (`Cannot find type 'AppState'…`) are **indexer-only false positives**, not real build errors. They appear after Swift edits and clear after `xcodegen generate` re-runs. Don't try to "fix" them.
- **gpt-oss family emits OpenAI Harmony format.** Tool names arrive wrapped as `assistant<|channel|>commentary<|message|>functions=NAME(args)`. The harmony parser at `src/localsmartz/runners/harmony.py` strips wrappers before tool-name validation in `serve.py:_is_valid_tool_name`. Don't add `.with_retry()` calls before `create_deep_agent` (DeepAgents constraint — see `feedback_subagent_default.md` and `reference_deepagents_runnable_retry.md`).
- **Python backend is spawned by macOS app.** Editing `src/localsmartz/` only takes effect after `uv tool install --reinstall --from <repo>` (or the user manually killing the spawned process AND rebuilding the bundled python).
- **Plugin validator was over-strict** vs current Anthropic spec (snapshot in `.build-loop/research/2026-04-26-anthropic-plugin-spec-snapshot.md`). Three optional fields are now warnings not errors. Don't re-tighten without checking the spec.

---

## Recommended next actions for Codex

In rough priority:

1. **Visual-verify the four unverified items above.** If toolbar still shows `FULL`, the issue is likely UserDefaults caching `currentModel = ""` from a prior session — clear with `defaults delete com.localsmartz.LocalSmartz currentModel` (if that key exists) or just send one query to populate via SSE `.status` event.
2. **Path A DeepAgents subagents wiring** — `patterns/orchestrator.py::_dispatch_workers_path_a` is a placeholder that falls back to Path B. Wire `create_deep_agent(subagents=...)` + `task` tool. Integration test should construct a real DeepAgents agent.
3. **Sidebar truncation** — `qwen2.5-coder:32b-instruct-q5_K_M` truncates to `qwen2.5-coder:32b-i…` in AgentRow. Consider tooltip on hover, or compact display name like `qwen2.5-coder:32b`.
4. **`navgator scan`** — index is from 2026-04-13. Refresh so future blast-radius checks are accurate.
5. **PII-filter span processor** (research packet F13) — deferred this session; relevant before sharing telemetry.

---

## Files most-modified this session

| File | What changed |
|---|---|
| `app/LocalSmartz/Views/ResearchView.swift` | composer, queue, toolbar, fonts, hint, AX labels |
| `src/localsmartz/agent.py` | fast-path role pickup, keep_alive, ppt policy |
| `src/localsmartz/profiles.py` | `fast_model` key + `'fast'` role lookup |
| `src/localsmartz/runners/harmony.py` | NEW — gpt-oss harmony token parser |
| `src/localsmartz/plugins/validator.py` | agent.md validation + spec-driven downgrades |
| `src/localsmartz/plugins/discovery.py` | `find_agent_files` |
| `src/localsmartz/models/catalog.py` | gemma4 in recommended set |
| `src/localsmartz/serve.py` | harmony pre-parse before tool-name validation, dedup/anchor wiring |
| `app/LocalSmartz/Views/ThreadListView.swift` | per-agent model rows in sidebar, AgentInfo extended |
| `pyproject.toml` | `local-smartz` console-script alias |

---

## Memory / preferences active in this repo

- Build from scratch over packages.
- Subagent-driven execution for plan work (always).
- Attack root cause; don't ship layered defensive workarounds.
- Real data, not mocks (mock requires explicit permission).
- 24px desktop touch targets; 4.5:1 contrast.
- Status = text color only, no background badges.
- Plugin storage under `.<toolname>/` (`.ibr/`, `.build-loop/`, `.bookmark/`, `.navgator/`), never under `.claude/`.

Codex: read `CLAUDE.md` for the full version. Don't undo any of the above without checking with the user.
