# Phase 4: Lite Model Hardening

## Problem

qwen3:8b is less reliable than 70B models at tool calling and structured output. During Phase 3 live testing, simple queries worked (capital of France, sqrt calculation), but complex multi-tool research chains will hit failure modes: hallucinated tool names, wrong parameter types, stringified JSON, incomplete tool calls, and overeager tool use.

The full profile can lean on model capability. The lite profile needs guardrails.

## Approach: 4 Layers

### Layer 1: Schema Simplification (Tool Definitions)

**Flatten nested parameters.** Small models fail at nesting depth >2. Convert deep structures to single-depth with underscore notation.

```python
# BEFORE (8B models hallucinate nested keys)
{"sections": [{"title": "str", "content": "str"}]}

# AFTER (flat, explicit)
{"sections_json": "string — JSON array of {title, content} objects"}
```

**Reduce tool count per invocation.** Full profile exposes all 8 tools + built-ins. Lite profile should expose a curated subset per phase:
- **Planning phase**: write_todos only
- **Research phase**: web_search, scrape_url, read_text_file
- **Computation phase**: python_exec
- **Output phase**: create_report

Implementation: `agent.py` selects tool subset based on profile. Phase detection via system prompt instruction.

**Align parameter names with pretraining patterns.** Research shows 5-17% improvement from naming params to match common API conventions the model saw during training. Audit all 8 tool schemas for clarity.

**Add enum constraints** wherever possible. `format: "markdown"` becomes `format: Literal["markdown", "html", "docx"]`.

### Layer 2: Prompt Hardening (System Prompt)

**Profile-specific system prompts.** The current `SYSTEM_PROMPT` in agent.py is one-size-fits-all. Split into:
- `SYSTEM_PROMPT_FULL` — current version (flexible, trusts model judgment)
- `SYSTEM_PROMPT_LITE` — tighter constraints:

Key differences for lite prompt:
1. **Explicit tool call format** — show exact JSON structure the model should produce
2. **2-3 few-shot examples** — concrete tool call + response patterns
3. **Numbered step enforcement** — "Step 1: Plan. Step 2: Search. Step 3: Analyze. Step 4: Report."
4. **One tool at a time** — "Call ONE tool per turn. Wait for the result before calling another."
5. **No subagent delegation** — remove `task` tool from lite profile (8B models can't effectively delegate)

**Lower temperature.** Already at 0 (good). Keep it there.

### Layer 3: Validation & Retry (Runtime)

**Tool call validation middleware.** Before executing any tool call from the agent:

```python
def validate_tool_call(tool_call: dict, available_tools: list[str]) -> tuple[bool, str]:
    """Validate tool call before execution. Returns (ok, error_message)."""
    name = tool_call.get("name", "")

    # Check tool exists
    if name not in available_tools:
        return False, f"Tool '{name}' does not exist. Available: {available_tools}"

    # Check args is a dict (not stringified JSON)
    args = tool_call.get("args", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return False, f"Invalid arguments format for {name}"

    # Type-check required params against tool schema
    # ... per-tool validation

    return True, ""
```

**Retry with reformulation.** On tool call failure:
1. First retry: inject error message into conversation, ask model to try again
2. Second retry: simplify the request ("Just use web_search with a simple query")
3. Third failure: skip tool, continue with available context

**Output validation.** After agent completes, before returning to user:
- Check response isn't empty
- Check it actually addresses the prompt (simple keyword overlap check)
- If response is just tool output without synthesis, append a nudge and re-invoke

### Layer 4: Graceful Degradation

**Profile-aware feature gates:**

| Feature | Full | Lite |
|---------|------|------|
| Subagent delegation | Yes | No |
| Quality review gate | Yes | No (too unreliable) |
| Concurrent tool calls | 2 | 1 |
| Max tools exposed | All 8 + built-ins | 5 (curated per phase) |
| Max turns | 20 | 10 |
| Context window | 4096 | 4096 |

**Fallback responses.** If the agent gets stuck in a loop (same tool call 3x), break out and return partial results with a disclaimer: "Research partially complete — the local model encountered difficulty with [specific tool]. Here's what was gathered so far."

## Implementation Order

1. **Split system prompt** (SYSTEM_PROMPT_LITE) — highest impact, lowest effort
2. **Tool validation middleware** — catches failures before they cascade
3. **Retry logic** — makes failures recoverable
4. **Schema simplification** — per-tool audit, flatten where needed
5. **Phase-based tool subsetting** — requires prompt restructuring
6. **Graceful degradation** — feature gates and fallback responses

## Testing Strategy

Run the same 5-query benchmark on both profiles:
1. Simple factual: "What is the capital of France?"
2. Calculation: "What is 15% of $2,400?"
3. Web research: "What are the latest AI trends in 2026?"
4. Multi-step: "Compare Python and Rust performance for web servers"
5. Report generation: "Write a brief report on renewable energy adoption"

Track: tool call success rate, hallucinated tools, total turns, time to completion.
Target: lite profile completes 4/5 queries correctly (vs 5/5 for full).

## References

- Qwen3 function calling docs: qwen.readthedocs.io
- PA-Tool framework (schema alignment): arxiv.org/html/2510.07248
- Reliability alignment (reduce hallucination): arxiv.org/html/2412.04141v1
- LangChain Pydantic validation with retries: langchain-ai.github.io
- Ollama structured outputs: ollama.com/blog/structured-outputs
