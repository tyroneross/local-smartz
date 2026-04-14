# Goal: Wire tool execution into pipeline specialists + flip graph to default

Date: 2026-04-13
Branch: main

## Problem

`src/localsmartz/pipeline.py` has specialist nodes (researcher, analyzer, fact_checker, writer) that each call `ChatOllama.invoke(messages)` with no tools bound. Researcher describes what it would search; analyzer describes Python. This is not a real agent pipeline. Fix it, then make graph the default backend.

Reference: deepagents wraps `langchain.agents.create_agent` (see `deepagents/graph.py` line 12). We use it directly per specialist.

## Deliverables

### A. Pipeline tool execution (Python)

**File**: `src/localsmartz/pipeline.py`

1. Build full tool registry once at `build_graph()` time using helpers from `src/localsmartz/agent.py`:
   - `_build_tool_set(profile, include_plugin_tools=False, include_mcp=False)` → returns `(tools, mcp_clients)`
   - `_scope_tools(all_tools, wanted_names)` → filter to role's subset
   - `agent_tool_names(role)` → allow-list from `AGENT_ROLES[role]["tools"]`
2. Per-role ReAct agent: `langchain.agents.create_agent(model, tools=scoped, system_prompt=role_focus)`. Compile once per `build_graph`, reuse.
3. Add `_run_role_agent(role, user_content, agent_exec)` that invokes the executor and extracts the final AI message content. Keep `_invoke_role` as a back-compat alias used only when `agent_exec` is None (tests).
4. Preserve ChatOllama config: 600s read timeout, retry on TransportError/TimeoutException, num_ctx=4096, temperature=0. Upgrade `_role_llm` to match `agent._create_model`.
5. Fact-checker still runs `_parse_fact_verdict` on final message content.

**File**: `tests/test_pipeline.py`
- Add `test_researcher_actually_calls_web_search`: stub `web_search` tool, build graph with fake LLM that emits a `web_search` tool call, assert stub invoked.
- Add `test_fact_checker_can_use_web_search`: same pattern.
- Existing 8 tests: keep green by continuing to stub `_invoke_role`; add a lightweight fallback so the old stubbing still works when tests patch `pipeline._invoke_role` directly.

### B. Flip graph to default + serve dispatch (Python)

**File**: `src/localsmartz/pipeline.py`
- `is_enabled()` semantics:
  - unset, `""`, `"graph"`, `"1"`, `"true"`, `"yes"`, `"on"` → True (NEW DEFAULT)
  - `"orchestrator"`, `"deepagents"`, `"0"`, `"false"`, `"off"`, `"no"` → False
- Update feature-flag tests accordingly.

**File**: `src/localsmartz/serve.py`
- New `_run_graph_pipeline(prompt, profile, model, thread_id, cwd)` emits the same SSE event taxonomy (`status`, `stage`, `tool`, `text`, `heartbeat`, `done`).
- `_stream_research` dispatches to `_run_graph_pipeline` when `pipeline.is_enabled()` is True (after fast-path check); falls back to `_run_full_agent` otherwise.
- Node-entry `stage` emission: pass an event sink callable into node factories; first action of each node is `sink({"type": "stage", "stage": role})`.

### C. Swift default + migration

**File**: `app/LocalSmartz/Models/GlobalSettings.swift`
- Change default `pipelineBackend` from `"orchestrator"` to `"graph"` in: property init, init parameter default, decoder default, `defaults` struct, encoder default.
- `mergedOverDefaults` already preserves non-empty stored values — explicit `"orchestrator"` survives migration. Verified OK.

### D. BackendManager env wiring

**File**: `app/LocalSmartz/Services/BackendManager.swift`
- After `LOCALSMARTZ_OBSERVE` block: read `GlobalSettings.load().pipelineBackend`:
  - `"orchestrator"` → `env["LOCALSMARTZ_PIPELINE"] = "orchestrator"`
  - Graph or empty → `env.removeValue(forKey: "LOCALSMARTZ_PIPELINE")`

### E. Settings UI picker

**File**: `app/LocalSmartz/Views/SettingsView.swift`
- Add `LabeledRow("Research pipeline")` in `GeneralTab` with Picker bound to `$vm.settings.pipelineBackend`.
- Tags: `"graph"` → "Deterministic graph (default)", `"orchestrator"` → "Prompt-driven orchestrator".
- Help text: "Graph mode is more reliable on small models (qwen3:8b) — enforces a fact-check loop structurally. Orchestrator mode is simpler and slightly faster on trivial queries."

## Scoring Criteria

| # | Criterion | Method | Pass | Evidence |
|---|-----------|--------|------|----------|
| 1 | Graph specialists actually run tools | code: pytest new tests | `test_researcher_actually_calls_web_search` + `test_fact_checker_can_use_web_search` green | test output |
| 2 | No test regressions | code: pytest full | all 424+ tests green | test output |
| 3 | Graph is default | code: is_enabled unset→True | unit tests assert semantics | test output |
| 4 | SSE stage events fire from graph | code+manual: serve dispatch | `_run_graph_pipeline` emits stage for each node | serve diff + IBR |
| 5 | Swift app flips default + migrates | diff: defaults `"graph"` | `git diff` shows 5 sites flipped | diff |
| 6 | BackendManager sets env var | diff: env block | `env["LOCALSMARTZ_PIPELINE"]` logic present and correct | diff |
| 7 | Settings UI picker | diff: GeneralTab | new LabeledRow with Picker present | diff |
| 8 | E2E: researcher actually calls web_search | IBR scan + backend log | log shows `tool: web_search`, actual numbers in final answer | IBR screenshot + log snippet |

## Out of Scope

- Plugin tool / MCP wiring into the graph (defer — only custom tools from `_build_tool_set(include_plugin_tools=False, include_mcp=False)`)
- Reviewer/post-hoc review changes
- Fast-path changes (preserved)

## Risks

- `langchain.agents.create_agent` + `ChatOllama.with_retry()`-wrapped model: may need raw ChatOllama (validate during execution).
- Small models (qwen3:8b) may still fail ReAct loops — inherited risk, graph structure limits damage to single role.
- LangGraph stage emission from within nodes — use direct SSE sink captured in closure.

## Commit plan

- commit 1: pipeline tool execution (A) + tests
- commit 2: graph as default + serve dispatch (B)
- commit 3: Swift flip + BackendManager env + Settings UI (C+D+E)
