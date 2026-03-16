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
from langgraph.checkpoint.memory import MemorySaver

from localsmartz.profiles import get_profile, get_model
from localsmartz.threads import load_context
from localsmartz.tools.web import web_search, scrape_url
from localsmartz.tools.documents import parse_pdf, read_spreadsheet, read_text_file
from localsmartz.tools.reports import create_report, create_spreadsheet
from localsmartz.tools.compute import python_exec


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


def _create_model(profile: dict, role: str) -> ChatOllama:
    """Create a ChatOllama instance for the given profile and role."""
    model_name = get_model(profile, role)
    return ChatOllama(
        model=model_name,
        temperature=0,  # Deterministic for reliable tool calling
        num_ctx=4096,  # Conservative context window for memory
    )


def create_agent(
    profile_name: str | None = None,
    thread_id: str | None = None,
    cwd: Path | None = None,
):
    """Create the Local Smartz research agent.

    Uses DeepAgents' single-agent architecture:
    - One agent with all tools
    - Built-in write_todos for planning
    - Built-in task tool for subagent delegation
    - Built-in filesystem for context offloading
    - MemorySaver for checkpointing/resume

    Args:
        profile_name: "full" or "lite", or None for auto-detect
        thread_id: Optional thread ID for context continuity
        cwd: Working directory (default: cwd)

    Returns:
        Tuple of (agent, profile, checkpointer)
    """
    cwd = cwd or Path.cwd()
    profile = get_profile(profile_name)
    is_lite = profile["name"] == "lite"

    # Use planning model for the main agent
    model = _create_model(profile, "planning")

    # Profile-specific system prompt
    system_prompt = SYSTEM_PROMPT_LITE if is_lite else SYSTEM_PROMPT_FULL
    if thread_id:
        context = load_context(thread_id, str(cwd))
        if context:
            system_prompt += f"\n\n## Previous Research Context\n\n{context}"

    # Profile-specific tool set
    tools = LITE_TOOLS if is_lite else ALL_TOOLS

    checkpointer = MemorySaver()

    # Filesystem backend for persistent workspace
    storage_dir = cwd / ".localsmartz"
    storage_dir.mkdir(parents=True, exist_ok=True)

    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        backend=FilesystemBackend(root_dir=str(storage_dir), virtual_mode=True),
        checkpointer=checkpointer,
    )

    return agent, profile, checkpointer


def run_research(
    prompt: str,
    *,
    profile_name: str | None = None,
    thread_id: str | None = None,
    cwd: Path | None = None,
    verbose: bool = True,
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
    agent, profile, checkpointer = create_agent(
        profile_name=profile_name,
        thread_id=thread_id,
        cwd=cwd,
    )

    if verbose:
        model = get_model(profile, "planning")
        print(f"Profile: {profile['name']} | Model: {model}", file=sys.stderr)
        print(f"Query: {prompt[:80]}{'...' if len(prompt) > 80 else ''}", file=sys.stderr)
        print("---", file=sys.stderr)

    config = {"configurable": {"thread_id": thread_id or "default"}}
    input_msg = {"messages": [{"role": "user", "content": prompt}]}

    if not verbose:
        # Silent mode — invoke directly
        return agent.invoke(input_msg, config=config)

    # Import validation for lite profile monitoring
    from localsmartz.validation import LoopDetector

    max_turns = profile.get("max_turns", 20)
    is_lite = profile["name"] == "lite"
    loop_detector = LoopDetector(max_repeats=3)
    turn_count = 0
    loop_broken = False

    # Streaming mode — show tool calls and progress as they happen
    final_state = None
    tools_used = set()

    for chunk in agent.stream(input_msg, config=config, stream_mode="updates"):
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
                        turn_count += 1
                        args_preview = _preview_args(tc.get("args", {}))
                        print(f"  ▸ {name}({args_preview})", file=sys.stderr)

                        # Loop detection (lite only)
                        if is_lite and loop_detector.record(name):
                            print(f"  ⚠ Loop detected: {name} called {loop_detector.max_repeats}x — breaking", file=sys.stderr)
                            loop_broken = True

                # Tool results
                if hasattr(msg, "type") and msg.type == "tool":
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    if content.startswith("Error"):
                        print(f"  ✗ {content[:120]}", file=sys.stderr)

        final_state = state_update

        # Enforce turn limit and loop break
        if turn_count >= max_turns:
            print(f"  ⚠ Turn limit ({max_turns}) reached — stopping", file=sys.stderr)
            break
        if loop_broken:
            break

    if tools_used:
        print(f"---\nTools used: {', '.join(sorted(tools_used))}", file=sys.stderr)

    # Reconstruct result from final state
    # stream() returns incremental updates; get full state from checkpointer
    full_result = agent.invoke(None, config=config)
    return full_result if full_result else (final_state or {})


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
