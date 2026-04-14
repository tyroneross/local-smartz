"""Main agent setup — creates and runs the Local Smartz research agent.

Architecture aligned with DeepAgents best practices:
- Single agent with all tools (not separate agent classes)
- Built-in write_todos for planning (no separate planner)
- Built-in task tool for subagent spawning with context isolation
- Built-in filesystem tools for context offloading
- SummarizationMiddleware keeps context window manageable
"""

import sys
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver

from localsmartz.profiles import (
    AGENT_ROLES,
    agent_tool_names,
    get_agent_model,
    get_model,
    get_profile,
    is_fast_path,
)
from localsmartz.threads import load_context
from localsmartz.tools.web import web_search, scrape_url
from localsmartz.tools.documents import parse_pdf, read_spreadsheet, read_text_file
from localsmartz.tools.reports import create_report, create_spreadsheet
from localsmartz.tools.compute import python_exec
from localsmartz.plugins.agent_integration import (
    build_mcp_tools,
    build_plugin_tools,
    build_skill_prompt_section,
    close_mcp_clients,
    get_active_skills,
)
from localsmartz.plugins.mcp_client import MCPClient


# All custom tools — DeepAgents adds write_todos, task, ls, read_file,
# write_file, edit_file automatically
ALL_TOOLS = [
    web_search,
    scrape_url,
    parse_pdf,
    read_spreadsheet,
    read_text_file,
    python_exec,
    create_report,
    create_spreadsheet,
]

# Lite profile: curated subset — fewer tools = fewer hallucinated tool names.
# Excludes parse_pdf, read_spreadsheet, create_spreadsheet (complex schemas).
# DeepAgents still adds write_todos, write_file, read_file automatically.
# task (subagent) tool is NOT added for lite — 8B models can't delegate effectively.
LITE_TOOLS = [
    web_search,
    scrape_url,
    read_text_file,
    python_exec,
    create_report,
]


SYSTEM_PROMPT_FULL = """\
You are Local Smartz, a local-first research assistant running entirely on local hardware via Ollama. You have access to web search, document processing, computation, and report generation tools — plus built-in planning, filesystem, and subagent capabilities.

## How to Approach Research

1. **Plan first**: Use write_todos to decompose the question into concrete steps. Each todo should be a specific, completable action (not vague like "research topic").

2. **Gather information**: Use web_search to find relevant sources, then scrape_url to extract detailed content from the best results. For local files, use parse_pdf, read_spreadsheet, or read_text_file.

3. **Analyze data**: Use python_exec for ALL calculations, statistics, and data processing. Never estimate numbers — compute them. Save intermediate results to files with write_file when useful.

4. **Produce output**: Use create_report to save the final deliverable. Default: markdown to .localsmartz/reports/.

## When to Use Subagents

Use the task tool to spawn a subagent when:
- A subtask needs deep, focused work that would pollute your main context
- You need to research multiple independent topics in parallel
- A task involves processing large documents that would overwhelm context

When delegating, give the subagent a clear, self-contained instruction. It has the same tools you do.

## Context Management

- Use write_file to save intermediate findings, research notes, or data extracts
- Use read_file to retrieve previously saved work
- This prevents context window overflow on long research sessions
- Save structured data (JSON, CSV) for later analysis

## Calculation Policy

CRITICAL: All math, statistics, percentages, growth rates, and financial figures MUST be computed via python_exec. Your text may describe trends directionally ("revenue grew significantly") but actual numbers come from Python output. This is non-negotiable — local models hallucinate math.

## Report Structure

When producing a final report:
- Title: Concise, descriptive
- Executive summary: 2-3 sentences answering the original question
- Key findings: Bulleted, most important first
- Detailed analysis: Sections with evidence
- Source attribution: [Source Name] for all claims
- Headings as statements ("AI market reaches $52B") not labels ("Market Overview")
- Numbers with context: $1.2B, +15% YoY, 3x industry average

## Tool Reference

| Tool | Purpose |
|------|---------|
| web_search | DuckDuckGo search — use specific queries |
| scrape_url | Fetch URL content as markdown |
| parse_pdf | Extract text from PDF files |
| read_spreadsheet | Read Excel files as markdown tables |
| read_text_file | Read local text files |
| python_exec | Execute Python for calculations (30s timeout) |
| create_report | Generate markdown/html/docx report |
| create_spreadsheet | Create Excel from structured data |
| write_todos | Plan and track subtasks (built-in) |
| task | Delegate to focused subagent (built-in) |
| write_file | Save content to filesystem (built-in) |
| read_file | Read from filesystem (built-in) |

## Guidelines

- Always plan before executing (write_todos)
- Search broadly, then scrape deeply
- Cross-reference claims across multiple sources
- If a tool fails, report the error and try an alternative
- Cite sources for every factual claim
- Save reports to .localsmartz/reports/ unless user specifies otherwise
"""


SYSTEM_PROMPT_LITE = """\
You are Local Smartz, a local-first research assistant. You answer questions using tools.

## RULES — Follow exactly

1. Call ONE tool per turn. Wait for the result before calling another tool.
2. Follow these steps IN ORDER for every question:
   - Step 1: Plan — use write_todos to list 2-4 specific actions
   - Step 2: Search — use web_search with a simple, specific query
   - Step 3: Read — use scrape_url on the best result URL
   - Step 4: Calculate — use python_exec if any math is needed
   - Step 5: Answer — write your final answer directly, or use create_report for long answers
3. Never guess numbers. Use python_exec for ALL math.
4. If a tool fails, try once more with simpler input. If it fails again, skip it and use what you have.

## Available Tools

| Tool | What it does | Example input |
|------|-------------|---------------|
| web_search | Search the web | query="renewable energy trends 2026" |
| scrape_url | Get content from a URL | url="https://example.com/article" |
| read_text_file | Read a local file | file_path="/path/to/file.txt" |
| python_exec | Run Python code | code="print(15 / 100 * 2400)" |
| create_report | Save a report | title="My Report", sections=[{"heading": "Summary", "content": "..."}], output_path=".localsmartz/reports/report.md" |
| write_todos | Plan your steps | (built-in, no special args) |
| write_file | Save text to a file | (built-in) |
| read_file | Read a saved file | (built-in) |

## Examples

**User asks:** "What is 15% of $2,400?"
**You do:** Call python_exec with code="result = 15 / 100 * 2400\\nprint(f'15% of $2,400 = ${result:,.2f}')"
**Then:** Report the answer from the output.

**User asks:** "What are the latest AI trends?"
**You do:**
1. Call write_todos to plan: ["Search for AI trends 2026", "Read top result", "Summarize findings"]
2. Call web_search with query="AI trends 2026"
3. Call scrape_url on the best URL from search results
4. Write your answer using the scraped content.

**User asks:** "Compare Python and Rust for web servers"
**You do:**
1. Call write_todos to plan: ["Search Python web server benchmarks", "Search Rust web server benchmarks", "Compare results"]
2. Call web_search with query="Python web server performance benchmarks 2026"
3. Call scrape_url on the best result
4. Call web_search with query="Rust web server performance benchmarks 2026"
5. Call scrape_url on the best result
6. Write a comparison using both sources.

## Answering

- Start with a direct answer to the question
- Use bullet points for key findings
- Cite sources: [Source Name] for claims
- Keep answers focused and concise
"""


def _create_model(profile: dict, role: str, *, model_name: str | None = None) -> ChatOllama:
    """Create a ChatOllama instance for the given profile and role.

    Improvements over the default ``ChatOllama()`` constructor:

    - **Explicit client timeout** — Ollama's default httpx client has no read
      timeout. A hung model would block the stream indefinitely. We cap
      read at 600 s (aligned with long report-generation SSE) and keep
      connect/write short so transient network issues fail fast instead
      of silently stalling the UI.
    - **Transient-error retry** — ChatOllama is wrapped in LangChain's
      ``with_retry`` so a ``httpx.ReadError`` from a hot-unloaded model
      (common during model switches) re-dispatches once with a jittered
      backoff instead of killing the turn. Still bubbles up after the
      second failure so the SSE error path fires.

    If ``model_name`` is passed, it overrides the role lookup — used when a
    per-agent model has been resolved upstream.
    """
    import httpx  # local import: heavy, only needed for the timeout struct
    name = model_name or get_model(profile, role)
    llm = ChatOllama(
        model=name,
        temperature=0,  # Deterministic for reliable tool calling
        num_ctx=4096,  # Conservative context window for memory
        client_kwargs={
            "timeout": httpx.Timeout(
                connect=5.0,
                read=600.0,
                write=30.0,
                pool=5.0,
            ),
        },
    )
    return llm.with_retry(
        stop_after_attempt=2,
        wait_exponential_jitter=True,
        retry_if_exception_type=(
            httpx.TransportError,
            httpx.TimeoutException,
        ),
    )


# Short system prompt for trivial prompts — keeps the fast-path tiny so
# first-token latency is dominated by the model, not prompt ingestion.
_FAST_PATH_SYSTEM_PROMPT = (
    "You are a helpful assistant running locally. Answer concisely."
)


def fast_path_stream(
    prompt: str,
    profile: dict,
    model_override: str | None = None,
):
    """Yield SSE event dicts for a trivial prompt, bypassing the agent graph.

    Emits:
      - ``{type: "text", content: "[fast-path] ..."}`` marker (first event)
      - streamed ``{type: "text", content: "..."}`` token chunks
      - final ``{type: "done", duration_ms, thread_id}``

    Callers own thread_id bookkeeping — pass the thread_id through by wrapping
    the final ``done`` event on the serve layer if needed.
    """
    import time as _time

    # Pick model: explicit override wins, else profile planning model.
    model_name = model_override or get_model(profile, "planning")

    start = _time.time()
    # Subtle marker so the user knows a fast-path was selected.
    yield {
        "type": "text",
        "content": f"[fast-path] using {model_name} (no agent planning)\n\n",
    }

    import httpx
    llm = ChatOllama(
        model=model_name,
        temperature=0,
        num_ctx=2048,  # Tight: fast-path prompts are always short.
        # Match the main-agent timeout discipline — never let the UI hang
        # waiting on an unresponsive local model.
        client_kwargs={
            "timeout": httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0),
        },
    ).with_retry(
        stop_after_attempt=2,
        wait_exponential_jitter=True,
        retry_if_exception_type=(httpx.TransportError, httpx.TimeoutException),
    )

    messages = [
        {"role": "system", "content": _FAST_PATH_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        for chunk in llm.stream(messages):
            content = getattr(chunk, "content", None)
            if isinstance(content, str) and content:
                yield {"type": "text", "content": content}
            elif isinstance(content, list):
                # Some LangChain adapters emit content as a list of segments.
                for seg in content:
                    text = seg.get("text") if isinstance(seg, dict) else None
                    if isinstance(text, str) and text:
                        yield {"type": "text", "content": text}
    except Exception as exc:  # noqa: BLE001 — we surface, don't crash
        yield {
            "type": "tool_error",
            "name": "fast_path",
            "message": f"Fast-path LLM call failed: {exc}",
        }

    duration_ms = int((_time.time() - start) * 1000)
    yield {"type": "done", "duration_ms": duration_ms, "thread_id": ""}


# Re-export is_fast_path for callers that import from agent module (serve.py).
__all__ = [
    "ALL_TOOLS",
    "LITE_TOOLS",
    "create_agent",
    "extract_final_response",
    "fast_path_stream",
    "is_fast_path",
    "review_output",
    "run_research",
]


def _build_system_prompt(
    profile: dict,
    *,
    include_plugin_skills: bool,
    thread_id: str | None = None,
    cwd: Path | None = None,
    extra_system_prompt: str = "",
) -> str:
    """Assemble the system prompt for a given profile.

    Extracted so tests can verify prompt construction without spinning up
    the full agent (which requires a live Ollama server).
    """
    is_lite = profile["name"] == "lite"
    prompt = SYSTEM_PROMPT_LITE if is_lite else SYSTEM_PROMPT_FULL

    if thread_id and cwd is not None:
        context = load_context(thread_id, str(cwd))
        if context:
            prompt += f"\n\n## Previous Research Context\n\n{context}"

    if include_plugin_skills:
        skills = get_active_skills()
        section = build_skill_prompt_section(skills)
        if section:
            prompt += "\n" + section

    if extra_system_prompt:
        prompt += extra_system_prompt

    return prompt


def _build_tool_set(
    profile: dict,
    *,
    include_plugin_tools: bool,
    include_mcp: bool,
) -> tuple[list, list[MCPClient]]:
    """Build the flat tool list. Returns (tools, mcp_clients_to_close)."""
    is_lite = profile["name"] == "lite"
    tools: list = list(LITE_TOOLS if is_lite else ALL_TOOLS)

    if include_plugin_tools:
        tools.extend(build_plugin_tools(profile_name=profile["name"]))

    mcp_clients: list[MCPClient] = []
    if include_mcp:
        mcp_tools, mcp_clients = build_mcp_tools()
        tools.extend(mcp_tools)

    return tools, mcp_clients


def _tools_index(tools: list) -> dict:
    """Index the full tool set by ``tool.name`` so we can look up a role's
    allowed tools from the string names stored in AGENT_ROLES. Tools that
    don't expose a ``name`` attribute (rare — raw callables) are indexed by
    ``__name__``."""
    idx: dict = {}
    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "__name__", None)
        if isinstance(name, str):
            idx[name] = t
    return idx


def _scope_tools(all_tools: list, wanted: list[str]) -> list:
    """Select the subset of ``all_tools`` whose name matches one of ``wanted``.

    Silently drops unknown names — the caller can verify completeness via a
    length check. Used for both focus-mode (main agent is the role) and
    subagent specs (each role gets a narrow list).
    """
    idx = _tools_index(all_tools)
    return [idx[name] for name in wanted if name in idx]


def _build_subagent_specs(
    profile: dict,
    all_tools: list,
) -> list[dict]:
    """Build a DeepAgents ``subagents=`` list from AGENT_ROLES.

    Each entry gets its own tool set (prevents tool-name hallucinations by
    shrinking the model's surface area) and its own model (honors per-agent
    overrides via ``global_config.agent_models`` + profile defaults). The
    main agent uses ``task()`` to delegate; each subagent sees only its
    scoped tools — a cleaner replacement for the pre-migration
    prompt-injection hack that broadcast role focus via the system prompt.
    """
    specs: list[dict] = []
    for role_name, meta in AGENT_ROLES.items():
        if "tools" not in meta:
            # Role has no explicit tool allow-list at all — skip rather
            # than inherit the flat set, so we don't re-open the
            # hallucination surface.
            continue
        wanted = agent_tool_names(role_name)
        scoped = _scope_tools(all_tools, wanted)
        spec: dict = {
            "name": role_name,
            "description": meta.get("summary", role_name),
            "system_prompt": meta.get("system_focus", ""),
            # Empty list means "no custom tools" — DeepAgents middleware
            # still provides write_todos + filesystem built-ins.
            "tools": scoped,
        }
        # Per-agent model override (profile default merged with user overrides).
        role_model = get_agent_model(profile, role_name)
        if role_model:
            spec["model"] = role_model
        specs.append(spec)
    return specs


def create_agent(
    profile_name: str | None = None,
    thread_id: str | None = None,
    cwd: Path | None = None,
    model_override: str | None = None,
    *,
    include_plugin_skills: bool | None = None,
    include_plugin_tools: bool | None = None,
    include_mcp: bool = False,
    extra_system_prompt: str = "",
    focus_agent: str | None = None,
):
    """Create the Local Smartz research agent.

    Uses DeepAgents' single-agent architecture:
    - One agent with all tools
    - Built-in write_todos for planning
    - Built-in task tool for subagent delegation
    - Built-in filesystem for context offloading
    - SqliteSaver for durable checkpointing (threads survive restart)

    Args:
        profile_name: "full" or "lite", or None for auto-detect
        thread_id: Optional thread ID for context continuity
        cwd: Working directory (default: cwd)
        model_override: If set, replaces planning_model (user-selected model)
        include_plugin_skills: Inject active plugin skills into system prompt.
            Defaults to True for full profile, False for lite (small models
            can't absorb large prompts).
        include_plugin_tools: Expose plugin commands as guidance tools.
            Defaults to True for full, False for lite.
        include_mcp: Start registered MCP servers and expose their tools.
            Default False -- opt in when ready; startup can be slow.

    Returns:
        Tuple of (agent, profile, checkpointer, mcp_clients). ``mcp_clients``
        may be empty; caller should call ``close()`` on each at session end.
    """
    cwd = cwd or Path.cwd()
    profile = get_profile(profile_name, model_override=model_override)
    is_lite = profile["name"] == "lite"

    # Profile-aware defaults: lite keeps its prompt + tool budget tight.
    if include_plugin_skills is None:
        include_plugin_skills = not is_lite
    if include_plugin_tools is None:
        include_plugin_tools = not is_lite

    # Per-agent model resolution — when the caller pins focus to a named agent,
    # prefer that agent's configured model (profile default merged with any
    # global_config override). An explicit ``model_override`` passed in still
    # wins so CLI --model keeps working.
    effective_planning_model: str | None = None
    if focus_agent and not model_override:
        agent_model = get_agent_model(profile, focus_agent)
        if agent_model:
            effective_planning_model = agent_model
            profile["planning_model"] = agent_model

    # Use planning model for the main agent
    model = _create_model(profile, "planning", model_name=effective_planning_model)

    system_prompt = _build_system_prompt(
        profile,
        include_plugin_skills=include_plugin_skills,
        thread_id=thread_id,
        cwd=cwd,
        extra_system_prompt=extra_system_prompt,
    )

    tools, mcp_clients = _build_tool_set(
        profile,
        include_plugin_tools=include_plugin_tools,
        include_mcp=include_mcp,
    )

    # Filesystem backend for persistent workspace
    storage_dir = cwd / ".localsmartz"
    storage_dir.mkdir(parents=True, exist_ok=True)

    # Durable checkpointer. Threads resume across process restarts — a user
    # reopening the app can continue a conversation instead of seeing
    # context evaporate (fixes AP-3 in the deepagents anti-patterns doc).
    # Constructed directly from a sqlite3.Connection so we manage lifetime
    # ourselves; the ``SqliteSaver.from_conn_string`` helper returns a
    # context manager that would close the DB when the ``with`` block
    # exits — not what we want for a long-lived agent.
    #
    # ``check_same_thread=False`` is required because the HTTP server
    # dispatches requests on worker threads distinct from the one that
    # created the agent; the saver's read/write calls happen on whichever
    # thread invokes ``agent.stream``. SQLite itself serializes access
    # inside a single connection, so this is safe.
    import sqlite3
    checkpoint_db = storage_dir / "checkpoints.db"
    checkpoint_conn = sqlite3.connect(str(checkpoint_db), check_same_thread=False)
    checkpointer = SqliteSaver(checkpoint_conn)
    checkpointer.setup()  # Idempotent — creates tables if missing.

    # Two modes:
    # 1. Focus mode (``focus_agent`` set): scope the MAIN agent's tools to just
    #    the role's whitelist + replace the system prompt with the role's
    #    system_focus. No subagents — the role IS the main agent. This blocks
    #    tool-name hallucinations (the old prompt-injection hack left the
    #    full flat tool set accessible, which is how we got
    #    ``repo_browser.write_todos`` bugs in qwen3:8b).
    # 2. Multi-agent mode (default): main agent keeps its full tool set and
    #    ``task()`` delegation. Subagents are built from AGENT_ROLES with
    #    their own scoped tools + models via _build_subagent_specs.
    if focus_agent and focus_agent in AGENT_ROLES:
        role_meta = AGENT_ROLES[focus_agent]
        wanted = agent_tool_names(focus_agent)
        scoped_tools = _scope_tools(tools, wanted)
        role_prompt = role_meta.get("system_focus", "") or system_prompt
        agent = create_deep_agent(
            model=model,
            tools=scoped_tools,
            system_prompt=role_prompt,
            backend=FilesystemBackend(root_dir=str(storage_dir), virtual_mode=True),
            checkpointer=checkpointer,
            # No subagents — main agent is the role. Removing ``task`` delegation
            # also keeps the tool budget small for small local models.
            subagents=[],
        )
    else:
        subagent_specs = _build_subagent_specs(profile, tools)
        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            backend=FilesystemBackend(root_dir=str(storage_dir), virtual_mode=True),
            checkpointer=checkpointer,
            subagents=subagent_specs if subagent_specs else None,
        )

    return agent, profile, checkpointer, mcp_clients


def run_research(
    prompt: str,
    *,
    profile_name: str | None = None,
    thread_id: str | None = None,
    cwd: Path | None = None,
    verbose: bool = True,
    model_override: str | None = None,
) -> dict:
    """Run a research query through the Local Smartz agent with streaming.

    Args:
        prompt: The research question
        profile_name: "full" or "lite", or None for auto-detect
        thread_id: Optional thread for context continuity
        cwd: Working directory
        verbose: Print progress to stderr

    Returns:
        Final agent state dict with messages
    """
    cwd = cwd or Path.cwd()
    agent, profile, checkpointer, mcp_clients = create_agent(
        profile_name=profile_name,
        thread_id=thread_id,
        cwd=cwd,
        model_override=model_override,
        include_mcp=True,
    )

    if verbose:
        model = get_model(profile, "planning")
        print(f"Profile: {profile['name']} | Model: {model}", file=sys.stderr)
        print(f"Query: {prompt[:80]}{'...' if len(prompt) > 80 else ''}", file=sys.stderr)
        print("---", file=sys.stderr)

    config = {"configurable": {"thread_id": thread_id or "default"}}
    input_msg = {"messages": [{"role": "user", "content": prompt}]}

    if not verbose:
        # Silent mode — invoke directly, still close MCP clients on exit.
        try:
            return agent.invoke(input_msg, config=config)
        finally:
            close_mcp_clients(mcp_clients)

    # Import validation for lite profile monitoring
    from localsmartz.validation import LoopDetector
    from localsmartz.drift import create_drift_detector

    max_turns = profile.get("max_turns", 20)
    is_lite = profile["name"] == "lite"
    loop_detector = LoopDetector(max_repeats=3)
    drift_detector = create_drift_detector(profile)
    turn_count = 0
    loop_broken = False

    # Streaming mode — show tool calls and progress as they happen
    final_state = None
    tools_used = set()
    showed_thinking = False

    # Immediate feedback — show "Thinking..." before LLM responds
    print("  Thinking...", end="", flush=True, file=sys.stderr)

    # Multi-mode streaming: "updates" (tool events) + "messages" (token stream).
    # Without the messages mode, the CLI shows "Thinking..." through a 10–60s
    # silent gap while the model generates. We print tokens to stdout so users
    # see the answer forming in real time (AP-4 in the deepagents skill).
    for mode, payload in agent.stream(
        input_msg, config=config, stream_mode=["updates", "messages"]
    ):
        if mode == "messages":
            # payload is (AIMessageChunk, metadata) from LangGraph.
            try:
                msg_chunk, _meta = payload  # type: ignore[misc]
            except (TypeError, ValueError):
                continue
            # Only stream chunks from the top-level AI node; ignore tool chunks.
            chunk_type = getattr(msg_chunk, "__class__", type(msg_chunk)).__name__
            if chunk_type != "AIMessageChunk" and getattr(msg_chunk, "type", None) != "ai":
                continue
            content = getattr(msg_chunk, "content", None)
            if isinstance(content, str) and content:
                if not showed_thinking:
                    print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
                    showed_thinking = True
                # Stream tokens to stdout so the answer scrolls in real time.
                print(content, end="", flush=True)
            elif isinstance(content, list):
                for seg in content:
                    text = seg.get("text") if isinstance(seg, dict) else None
                    if isinstance(text, str) and text:
                        if not showed_thinking:
                            print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
                            showed_thinking = True
                        print(text, end="", flush=True)
            continue

        # mode == "updates" — payload is the usual dict of node -> state_update.
        chunk = payload
        if not isinstance(chunk, dict):
            continue
        for node_name, state_update in chunk.items():
            if state_update is None:
                continue
            messages = state_update.get("messages", [])
            # LangGraph wraps state in Overwrite objects
            if hasattr(messages, "value"):
                messages = messages.value
            if not isinstance(messages, list):
                continue
            for msg in messages:
                # Tool calls from the AI
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        name = tc.get("name", "unknown")
                        tools_used.add(name)

                        # Clear "Thinking..." on first tool call
                        if not showed_thinking:
                            print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
                            showed_thinking = True
                        turn_count += 1
                        args_preview = _preview_args(tc.get("args", {}))
                        print(f"  ▸ {name}({args_preview})", file=sys.stderr)

                        # Loop detection (lite only)
                        if is_lite and loop_detector.record(name, tc.get("args")):
                            print(f"  ⚠ Loop detected: {name} called {loop_detector.max_repeats}x with same args — breaking", file=sys.stderr)
                            loop_broken = True

                        # Drift detection
                        for de in drift_detector.record_tool_call(name, tc.get("args"), turn_count):
                            print(f"  ⚡ drift:{de.signal.value} [{de.severity.value}] {de.message}", file=sys.stderr)

                # Tool results
                if hasattr(msg, "type") and msg.type == "tool":
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    is_error = content.startswith("Error")
                    if is_error:
                        print(f"  ✗ {content[:120]}", file=sys.stderr)
                    for de in drift_detector.record_tool_result(getattr(msg, "name", "unknown"), content, is_error, turn_count):
                        print(f"  ⚡ drift:{de.signal.value} [{de.severity.value}] {de.message}", file=sys.stderr)

        final_state = state_update

        # Enforce turn limit and loop break
        if turn_count >= max_turns:
            print(f"  ⚠ Turn limit ({max_turns}) reached — stopping", file=sys.stderr)
            break
        if loop_broken:
            break

    # Clear "Thinking..." if no tools were used (simple answer)
    if not showed_thinking:
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr)

    if tools_used:
        print(f"---\nTools used: {', '.join(sorted(tools_used))}", file=sys.stderr)
    drift_events = drift_detector.get_events()
    if drift_events:
        print(f"Drift events: {len(drift_events)}", file=sys.stderr)
        for de in drift_events:
            print(f"  ⚡ {de.signal.value} [{de.severity.value}] {de.tool}: {de.message}", file=sys.stderr)

    # Reconstruct result from final state
    # stream() returns incremental updates; get full state from checkpointer
    try:
        full_result = agent.invoke(None, config=config)
        return full_result if full_result else (final_state or {})
    finally:
        # Ensure spawned MCP server processes exit when the session ends.
        close_mcp_clients(mcp_clients)


def _preview_args(args: dict, max_len: int = 60) -> str:
    """Create a short preview of tool call arguments."""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        val = str(v)
        if len(val) > 30:
            val = val[:27] + "..."
        parts.append(f"{k}={val}")
    result = ", ".join(parts)
    if len(result) > max_len:
        result = result[:max_len - 3] + "..."
    return result


def review_output(
    original_query: str,
    output: str,
    profile: dict,
    cwd: Path | None = None,
) -> str | None:
    """Run quality gate on agent output (full profile only).

    Creates a separate reviewer agent that evaluates the research output.
    Returns the review text, or None if review not applicable.
    """
    if profile["name"] != "full":
        return None

    from localsmartz.agents.definitions import load_prompt

    cwd = cwd or Path.cwd()
    reviewer_prompt = load_prompt("reviewer")
    model = _create_model(profile, "planning")

    reviewer = create_deep_agent(
        model=model,
        tools=[],  # Reviewer is pure evaluation
        system_prompt=reviewer_prompt,
    )

    review_input = (
        f"## Original Question\n{original_query}\n\n"
        f"## Research Output\n{output}"
    )

    result = reviewer.invoke(
        {"messages": [{"role": "user", "content": review_input}]},
        config={"configurable": {"thread_id": "reviewer"}},
    )

    return extract_final_response(result)


def extract_final_response(result: dict) -> str:
    """Extract the final text response from an agent result."""
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "ai":
            if hasattr(msg, "content") and isinstance(msg.content, str) and msg.content.strip():
                return msg.content
    return "No response generated."
