# Research packet — UI audit + plugin/skill compliance + gemma4 + CLI fix queue

Date: 2026-04-26
Repo: `/Users/tyroneross/dev/git-folder/local-smartz`
Last commit: `61b2736` (Phase 3 patterns + CLI nouns, just pushed)
Trigger: User asked for `/build-loop:research` then `/ibr:ibr` iteration covering four linked workstreams.

## Bottom line

All four workstreams are **shippable in one ibr-led loop** because they share one ground truth — the macOS app and a single FastAPI backend. The risk concentrates in two places: (a) the two **net-new untested SwiftUI tabs** (`PatternTab` 236 LOC, `EvalTab` 184 LOC), which Phase 3 added without visual verification, and (b) the **plugin validator's missing agent.md handler**, which means LocalSmartz claims plugin compatibility but silently ignores sub-agent definitions in the standard `agents/` directory. Everything else (gemma4 catalog presence, CLI fix queue) is concrete, low-ambiguity follow-on work. Expected cost: 6–9 atomic IBR iterations, ~3–5 hours of model time.

## What I found

### Workstream 1 — macOS UI audit (verified)

| View | LOC | Status | Defect risk | Why |
|---|---|---|---|---|
| `PatternTab.swift` | 236 | ⚠️ Untested visually | **HIGH** | New surface. Fetches `/api/patterns`, drives provider/pattern persist via `/api/patterns/active` and `/api/patterns/current`. Backend handlers are new too. Source comment at line 13 admits "persistence is achieved by writing the values to a local file via /api/secrets keyed store is not appropriate" — i.e. plumbing was improvised. |
| `EvalTab.swift` | 184 | ⚠️ Untested visually | **HIGH** | New surface. POSTs `/api/evals/run` with 600s timeout, decodes provider/model/pass/fail/results. Substring grader ≠ benchmark per its own header comment. |
| `ModelsTab.swift` | 706 | Refactored (+114) | MEDIUM | Recently touched, large surface. Likely affected by the new noun-CLI / model-registry plumbing. |
| `AgentsTab.swift` | 512 | Refactored (+263) | MEDIUM | Significant churn this cycle. Holds per-role model assignments referenced by PatternTab. |
| `ResearchView.swift` | 1516 | Refactored (+191) | LOW–MEDIUM | Largest surface, stable abstraction (research thread + artifacts) but +191 LOC adds Phoenix/eval surface — worth a smoke pass. |
| `SettingsView.swift` | 436 | +4 LOC | LOW | Minor; defer unless tabs underneath fail. |
| `SetupView.swift` | 753 | Untouched this cycle | LOW | Skip first pass. |

**IBR macOS path:** `ibr:scan_macos` and `ibr:native_scan` walk the AX (Accessibility) tree, capture screenshots, and audit hit-target sizes / contrast / focus order. They DO NOT need Chrome — different from web `ibr:scan`.

**Recommended audit order:** PatternTab → EvalTab → ModelsTab → AgentsTab → ResearchView → SettingsView → SetupView (deferred).

**Severity threshold:** **Strong-checkpoint+ first**, queue Guidance findings as a follow-on batch. New surfaces with no prior IBR signal — keep the loop tight. (Per the global feedback rule "Strong checkpoint" / "Guidance" terminology.)

**"Done" criteria:** all 6 tabs scan without AX errors, no hit targets <24px desktop, no contrast <4.5:1, focus order matches visual order, all interactive controls have accessibility labels, no unhandled HTTP errors surface as raw "HTTP 500" text in the UI.

### Workstream 2 — Plugin/skill compliance (verified gap)

`src/localsmartz/plugins/validator.py` (460 LOC) implements:

| Component | Validator? | Schema covered |
|---|---|---|
| `.claude-plugin/plugin.json` | ✅ | name (kebab-case), version (semver), description (>10 chars), author.name, keywords (optional list[str]) |
| `skills/<name>/SKILL.md` | ✅ | YAML frontmatter (name, description), version (optional), canonical→source-plugin/source-repo, body >100 bytes |
| `commands/*.md` | ✅ | frontmatter description, allowed-tools (CSV) |
| `hooks/hooks.json` | ✅ | events whitelist (Stop, PreCompact, SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, SubagentStop, Notification), command type, timeout int |
| `.mcp.json` | ✅ | mcpServers/servers, command, args list[str] |
| **`agents/*.md` (sub-agents)** | ❌ | **Not supported. `discovery.py` has no `find_agent_files`. `validator.py` has no `validate_agent_md`. `validate_plugin()` does not walk `agents/`.** |

**Confirmed via grep:**
- `grep -n "find_skill\|find_command\|find_hooks\|find_mcp\|find_agent" src/localsmartz/plugins/discovery.py` returns 4 finders, no agent finder.
- `grep -rn "agents/\|agent.md" src/localsmartz/plugins/` returns 0 hits.

**Anthropic Claude Code agent format (TAG:INFERRED — verify against current docs in iteration 1 via Context7):**
- `agents/<name>.md` with YAML frontmatter:
  - `name` (required, kebab-case)
  - `description` (required, when-to-use phrasing for autonomous dispatch)
  - `model` (optional: `inherit` | `opus` | `sonnet` | `haiku` | full model id)
  - `tools` (optional, comma-separated allowlist)
  - body: agent's system prompt
- File can be at plugin root OR under skills' subdirs.

**Other SKILL.md / plugin.json gaps spotted (TAG:INFERRED — verify against spec):**
- SKILL.md frontmatter `model` field (per-skill model override) not in validator.
- SKILL.md frontmatter `allowed-tools` not in validator (some skill formats use it).
- `plugin.json` may have `chainTo` or `dependsOn` fields per Anthropic spec extensions — validator doesn't check them.
- Validator hard-rejects `description` <10 chars on plugin.json — current spec is "non-empty" with no length floor; could be over-strict.

### Workstream 3 — gemma4:26b integration (verified, partial)

**Status now:**
- `localsmartz model list` shows `gemma4:26b 18.0 GB` ✅ (just pulled).
- Catalog entry exists: `src/localsmartz/models/catalog.py:84-97` — tier `standard`, roles `[strong, vision]`, `tool_reliability: strong`, `reasoning_mode: native`, `default_for: ["vision.*"]`.
- `recs_for_tier("full")` includes gemma4:26b ✅ (full inherits standard).
- **GAP:** `recommended_for_tier(tier)` (catalog.py:281) returns hardcoded names — gemma4 is in NO tier's recommended set:
  - mini → `qwen3.5:9b` only
  - standard → `qwen3.5:9b`, `qwen3.5:27b`
  - full → `qwen3.5:9b`, `qwen3.5:122b`, `qwen3-next:80b`
- **GAP:** No automatic role assignment — a freshly pulled `gemma4:26b` doesn't fill any role until user runs `localsmartz model assign`.

**Where it fits given existing slate:**
- qwen3.5:9b = fast, qwen3.5:122b = strong, qwen3-next:80b = critic ⇒ gemma4:26b is best as **vision specialist** + **alternate strong** (faster, smaller than 122b, native reasoning, strong tool calling). Recommend roles: `vision` (primary on full tier given qwen3-vl:32b is also there), `strong` (fallback when qwen3.5:122b is unloaded).
- Position relative to gpt-oss:20b: gemma4:26b is bigger, native reasoning, stronger tool calls. Prefer for tool-heavy strong work. gpt-oss:20b stays for harmony-format-aware workflows once parser lands.

### Workstream 4 — CLI fix queue (verified)

**Harmony token leak — exact site:**
- `src/localsmartz/serve.py:160` — `_is_valid_tool_name(name, *, valid_names)` only checks `"." in name or "/" in name` and name-list membership. No tokenizer for harmony format. Fix needs to be **before** this function: a name extractor that strips `assistant<|channel|>(commentary|analysis|final)<|message|>` wrappers and parses `functions=NAME(...)` → `NAME`. Search call sites: `serve.py:1806` is one.
- Same fix must apply to all gpt-oss-family runners. Easiest place: a new `runners/harmony.py::strip_harmony_tokens(text) -> (clean_name, args)` plus a unit test in `tests/test_runner_harmony.py` with 5–10 fixture strings (the failure case from prior session is the canonical one).

**Console-script alias:**
- `pyproject.toml:52` has `localsmartz = "localsmartz.__main__:main"`. Add `local-smartz = "localsmartz.__main__:main"` on line 53. One-line change.

**Stale `uv tool` install:**
- User's installed binary still imports `duckduckgo_search`; repo source uses `ddgs`. After `pyproject.toml` bump or version revision, instruct `uv tool upgrade localsmartz` (or reinstall from local path with `uv tool install --from /Users/tyroneross/dev/git-folder/local-smartz localsmartz`).

**Tool-call dedup + intent retention:**
- Both live in the agent loop, not the CLI. Likely in `src/localsmartz/agent.py` or `pipeline.py`. Dedup: cache `(tool_name, sorted(args.items()))` hash within a turn. Intent retention: keep `original_user_prompt` in `ThreadState` and re-inject as system message after `(tool_name not valid)` events instead of falling through to fallback.

**PowerPoint export — recommend prompt-policy, NOT new tool.** Reasoning: `python-pptx` would add a runtime dep + ~80MB; markdown→pptx via existing tools is not a real feature gap (every consumer of "draft a powerpoint" wants an outline they can paste into their slide tool). Add a system-prompt clause: "When the user asks for a PowerPoint or slide deck, produce a markdown outline with `# Slide N:` headers and bullet points; do not attempt to generate `.pptx` binary output." Implement in `agent.py` system-prompt builder. Single test asserts the clause is present in the rendered system prompt.

## Best path

**One ibr-led iteration loop** (`/ibr:ibr` consumes the iteration plan below) interleaving UI fixes with the supporting backend/CLI work, because:
1. PatternTab + EvalTab cannot be audited until the macOS app is running, which means the FastAPI backend is up, which means the harmony parser bug is in the loop.
2. Plugin/skill validator gaps are independent of UI but ride the same review/test cycle.
3. gemma4 wiring is one focused diff in `models/catalog.py` + a unit test.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **TAG:INFERRED Anthropic plugin spec drift** — agent.md format details, plugin.json optional fields | High | Iteration 1 fetches Context7 for current `claude-code/plugin-spec` and `claude-code/sub-agents` docs before writing the validator. |
| Harmony token regex breaks on edge cases (nested calls, multiple channels) | Medium | Fixture-driven tests; start narrow (single function call), expand only on observed cases. |
| `uv tool` upgrade misses pyproject re-resolve | Low | Force reinstall: `uv tool install --reinstall localsmartz`. |
| IBR macOS scan gives sparse output on SwiftUI Forms (AX names sometimes generic) | Medium | Sample one tab first; if AX coverage is poor, fall back to screenshot diff vs design tokens (`ibr:design_system`). |
| PatternTab/EvalTab need backend handlers that may not exist (`/api/patterns/current`, `/api/evals/run`) | High | Iteration 0: smoke-test endpoints with `curl` before launching IBR; create stubs if missing. |
| Recommended-set change breaks existing `/api/setup` flow | Low | Keep `recommended_for_tier` backward-compatible by only ADDING gemma4 to standard+ as an *optional* recommendation, not replacing existing entries. |
| Subagent dispatch creates context-isolation issues with the harmony fix | Low | Land harmony parser FIRST in serial mode, then audit can dispatch in parallel. |

## Confidence

- Workstream 1 (UI audit): **High** — IBR has the macOS native scan path, files exist, scope clear.
- Workstream 2 (Plugin compliance): **Medium-High** — gap is concretely confirmed; spec details TAG:INFERRED until iteration 1 fetches docs.
- Workstream 3 (gemma4): **High** — catalog entry is real, tier inheritance verified at runtime.
- Workstream 4 (CLI fixes): **High** — failure mode reproduced last session, exact line numbers identified.

## Concrete iteration plan (consumed by `/ibr:ibr`)

Each item is atomic, has a verification step, and is sized for a single Sonnet pass. Numbered for IBR's iteration tracker.

### Iteration 0 — pre-flight (parallel, no UI yet)
**0a.** Fetch Anthropic plugin + sub-agent specs via Context7 (`claude-code-plugins` / `claude-code-subagents`). Pin field list, save to `.build-loop/research/2026-04-26-anthropic-plugin-spec-snapshot.md`. **Verify:** snapshot file exists, lists agent.md frontmatter fields with citations.
**0b.** Smoke-test PatternTab + EvalTab backend handlers: `curl localhost:11435/api/patterns`, `curl -X POST localhost:11435/api/patterns/active`, `curl localhost:11435/api/patterns/current`, `curl -X POST localhost:11435/api/evals/run`. Note any 404s; stub if missing. **Verify:** all four endpoints return 200 (or stubs return 200 with empty payload).
**0c.** `xcodebuild -scheme LocalSmartz -configuration Debug build && xcodebuild -scheme LocalSmartz -configuration Debug test` — confirm baseline still green. **Verify:** BUILD SUCCEEDED + tests pass.

### Iteration 1 — CLI/Backend fixes (must land before UI audit so app is reliable)
**1a.** Add `runners/harmony.py::strip_harmony_tokens(text) -> str | None` returning the cleaned tool name, plus `extract_harmony_tool_call(text) -> (name, args_str) | None`. Wire into `serve.py:160` callers. **Verify:** new `tests/test_harmony.py` with ≥5 fixtures (incl. the prior session's failure case), all green.
**1b.** Add tool-call dedup within a turn in `agent.py` / `pipeline.py`. **Verify:** unit test asserts duplicate `(name, args)` calls within one turn cache the result.
**1c.** Pin user intent across tool errors: persist `original_user_prompt` in thread state, re-inject on tool-not-found errors. **Verify:** new test scripts a tool-name failure mid-turn and asserts the agent retries with the original task pinned (not "What would you like me to help you with?").
**1d.** Add `local-smartz` console-script alias in `pyproject.toml:52-53`. **Verify:** `local-smartz --check` works after `uv tool install --reinstall .`.
**1e.** PowerPoint prompt-policy clause in agent.py system-prompt builder. **Verify:** new test asserts clause present.

### Iteration 2 — gemma4 first-class wiring
**2a.** Update `models/catalog.py::recommended_for_tier`: add `gemma4:26b` to `standard` and `full` recommended sets (keep existing entries; just append). Document role intent in `notes`. **Verify:** `localsmartz model recommend` on a full-tier machine lists gemma4. Update `tests/test_models_registry.py` to assert gemma4 in standard + full recommendations.
**2b.** Add an auto-assignment helper or doc note: post-`add` of a known catalog model, suggest role assignments via stdout (don't auto-assign — keep user in control). **Verify:** running `localsmartz model add gemma4:26b` (already-installed case) prints suggested `model assign vision gemma4:26b` line.
**2c.** Optionally elevate gemma4 to a `default_for` slot for full-tier vision pipelines (since qwen3-vl:32b is also there, this is a TAG:ASSUMED choice — surface trade-off in commit msg).

### Iteration 3 — Plugin/skill validator gap closure (sub-agent support)
**3a.** Add `discovery.find_agent_files(plugin_root) -> list[Path]` walking `agents/*.md` and `skills/*/agents/*.md`. **Verify:** unit test with fixture plugin dir.
**3b.** Add `validator.validate_agent_md(path)` covering frontmatter `name` (kebab-case), `description` (non-empty), optional `model` (inherit | opus | sonnet | haiku | full id), optional `tools` (CSV or list). Body must be non-empty. **Verify:** new `tests/test_plugin_validator.py::test_agent_md_*` cases (valid + each error code).
**3c.** Wire `validate_plugin()` to walk agents and merge their reports.
**3d.** Update `_PLUGIN_JSON_SYSTEM` / `_SKILL_NEW_SYSTEM` prompts in `serve.py:2358-2400` to mention agents directory option.
**3e.** Update SKILL.md validator: accept optional `model` and `allowed-tools` frontmatter fields without erroring; only error on unknown REQUIRED fields. **Verify:** test with skill that has `model: opus` passes.
**3f.** Loosen plugin.json description min-length from 10 to 1 (TAG:ASSUMED — confirm against spec snapshot from 0a). **Verify:** plugin.json with 5-char description warns instead of erroring.

### Iteration 4 — UI audit, tab-by-tab (PatternTab first)
**4a.** Launch app with backend on `:11435`. `ibr:native_scan` against window for **PatternTab**. Capture report. **Verify:** report file at `.ibr/scans/2026-04-26-pattern-tab.json`.
**4b.** Triage findings to Strong-checkpoint+ list. Apply fixes in `PatternTab.swift`. Common candidates: AX label on refresh button (line 137 — currently icon-only), description text not announced, segmented control state on `selectedProvider`. **Verify:** rescan, all checkpoints clear.

### Iteration 5 — UI audit EvalTab
**5a.** `ibr:native_scan` against EvalTab. **5b.** Fix Strong-checkpoint+ findings. Likely candidates: Provider segmented control AX, model TextField placeholder behavior, results list AX semantics (icons need labels), error text contrast. **Verify:** rescan clean.

### Iteration 6 — ModelsTab + AgentsTab (refactored, second priority)
**6a.** Scan ModelsTab; fix. **6b.** Scan AgentsTab; fix. **Verify:** rescan both clean.

### Iteration 7 — ResearchView smoke pass
**7a.** Single-pass `ibr:native_scan` on ResearchView focusing on the +191 LOC Phoenix/eval surface. Defer non-new findings. **Verify:** new surface scans clean.

### Iteration 8 — Sweep + sign-off
**8a.** `ibr:scan_macos` whole-app pass. **8b.** Run `pytest -q` (expect ≥570 passing after iterations 1–3 add tests). **8c.** `xcodebuild` Debug + Release. **8d.** Update memory file `local-smartz-session-2026-04-26.md` with delta. **8e.** Commit + push.

## Success metrics for the loop

- **Tests:** ≥570 passing (was 562; iterations add ~12 tests).
- **macOS Debug + Release:** BUILD SUCCEEDED.
- **IBR:** zero Strong-checkpoint findings remaining on PatternTab, EvalTab, ModelsTab, AgentsTab; ResearchView/SettingsView/SetupView baseline tracked but not blocked.
- **Plugin validator:** validates a fixture plugin containing `agents/researcher.md` without false errors and rejects an invalid one with `MISSING_AGENT_NAME` or similar coded issue.
- **CLI:** `local-smartz --check` works (alias). `localsmartz model recommend` (full tier) lists gemma4. Repro of last session's harmony failure (gpt-oss:20b emitting `assistant<|channel|>functions=web_search`) succeeds end-to-end.

## Out of scope this loop

- Path A DeepAgents subagents wiring (Phase 3 follow-up, separate session).
- PII-filter span processor (research packet F13 deferred).
- Catalog gap entries `gpt-oss:{20b,120b}` full-tier registry tagging (already pulled, working).
- New iOS companion build.
- Optimization run on the new patterns.

---

**Run ID:** local-smartz-2026-04-26-ui-plus-plugin-research
**Next action:** invoke `/ibr:ibr` with this packet path as input. The packet's Iteration 0 → Iteration 8 plan is structured for IBR's iteration tracker — each iteration has atomic deliverables and verifiable success criteria.
