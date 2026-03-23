# Local Smartz — LangSmith Tracing

**Date:** 2026-03-23
**Scope:** Add LangSmith observability to the agent pipeline — traces for all LLM calls, tool invocations, and agent loops

## Context

Local Smartz uses LangChain (`ChatOllama`) + DeepAgents for agent orchestration. LangSmith is LangChain's native tracing platform. Stratagem (sister project) already has LangSmith working. This adds the same tracing to Local Smartz so every research query is observable: model calls, tool invocations, durations, errors.

## Design

### How LangSmith tracing works with LangChain

LangChain has built-in LangSmith support. When these environment variables are set, **all LangChain/LangGraph operations are traced automatically** — no code changes to tool functions or agent logic needed:

```bash
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=LocalSmartz
```

This is different from Stratagem (which uses Claude Agent SDK and needs `configure_claude_agent_sdk()`). LangChain's tracing is env-var driven — if the vars are set, traces flow. If not, nothing happens. Zero overhead when disabled.

### What gets traced (automatic)

- `ChatOllama` model calls (prompt, response, tokens, latency)
- DeepAgents agent loop iterations
- Tool invocations (name, args, result, duration)
- `agent.stream()` and `agent.invoke()` calls
- Thread/checkpoint state transitions

### Implementation

**1. Add `langsmith` dependency to `pyproject.toml`:**
```toml
"langsmith>=0.3.0",
```

**2. Add `.env` support for env var loading (optional but recommended):**

Create `src/localsmartz/tracing.py`:
```python
"""LangSmith tracing configuration.

Tracing is automatic when LANGSMITH_TRACING=true is set.
This module provides a helper to load .env and verify config.
"""

import os
from pathlib import Path


def configure_tracing(cwd: Path | None = None) -> bool:
    """Load .env if present and check if tracing is enabled.

    Returns True if LANGSMITH_TRACING is set to true.
    """
    cwd = cwd or Path.cwd()

    # Load .env from project root if it exists
    env_file = cwd / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key.startswith("LANGSMITH_"):
                    os.environ.setdefault(key, value)

    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true"
```

**3. Call `configure_tracing()` at startup in `__main__.py`:**

At the top of `main()`, before any agent work:
```python
    from localsmartz.tracing import configure_tracing
    tracing = configure_tracing(cwd)
    if tracing and verbose:
        print(f"  LangSmith: tracing to {os.environ.get('LANGSMITH_PROJECT', 'default')}", file=sys.stderr)
```

**4. Add `--trace` flag (optional):**

Add to argparse:
```python
parser.add_argument("--trace", action="store_true", help="Enable LangSmith tracing")
```

When `--trace` is passed, set `LANGSMITH_TRACING=true` in env before agent runs.

**5. Add `.env` to `.gitignore`:**
```
.env
```

### Setup flow for users

```bash
# One-time: create .env in project root
cat > .env << 'EOF'
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_your_key_here
LANGSMITH_PROJECT=LocalSmartz
EOF

# Run with tracing
localsmartz "Research the EV market"
# → Traces appear at smith.langchain.com/project/LocalSmartz

# Or use --trace flag without .env
LANGSMITH_API_KEY=lsv2_pt_... localsmartz --trace "Research the EV market"
```

### Interlock with IBR/Playwright scraping

When Playwright-based scraping is added (separate spec), LangSmith will automatically trace those tool calls since they flow through the same `agent.stream()` pipeline. No additional integration work needed — LangChain traces all registered tools.

## Files to modify

| File | Changes |
|------|---------|
| `pyproject.toml` | Add `langsmith>=0.3.0` dependency |
| `src/localsmartz/tracing.py` | **New** — .env loader + tracing check |
| `src/localsmartz/__main__.py` | Call `configure_tracing()` at startup, add `--trace` flag |
| `.gitignore` | Add `.env` |
| `tests/test_tracing.py` | **New** — test .env loading, tracing config |

## Testing

1. Unit test: `configure_tracing()` with and without .env file
2. Unit test: `--trace` flag sets env var
3. Integration: run a query with `LANGSMITH_TRACING=true` and mock API key — verify no crashes
4. Manual: verify traces appear at smith.langchain.com

## Success criteria

- Tracing is zero-config when env vars not set (no impact on existing users)
- When enabled, all LLM calls + tool invocations appear in LangSmith dashboard
- `.env` file approach matches Stratagem's pattern
- `--trace` flag provides a quick enable path
