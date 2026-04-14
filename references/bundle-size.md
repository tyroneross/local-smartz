# DMG bundle size: dependency analysis

Audit for trimming provider SDKs we don't use. Local Smartz is Ollama-only —
no Anthropic, no Google GenAI — but both packages ride in via
`deepagents`' hard requires.

## Why they're present

`deepagents` 0.4.11 declares:

```
langchain-anthropic >= 1.3.4
langchain-google-genai >= 4.2.0
```

And `deepagents/graph.py:10-11` *eagerly imports*:

```python
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
```

Confirmed with:

```bash
uv tree --package langchain-google-genai --invert
# → langchain-google-genai → deepagents → localsmartz
```

`langchain-google-genai` is not imported by the deepagents source we read
(no grep hits) but is still a resolved dependency, so it lands in
`site-packages` anyway.

## Disk footprint in `.venv`

```
468K  langchain_anthropic/
5.9M  anthropic/                 ← largest single unused package
568K  langchain_google_genai/
 14M  google/                    ← includes genai + unused google-auth deps
```

Total: ~21 MB in site-packages, plus whatever falls out of pruning
transitive deps (`jiter`, `google-genai`, parts of `google-auth`).

## What we can do without forking deepagents

**Option A — keep (current state).**
No source changes. Bundle includes both provider SDKs. ~21 MB overhead.
No risk of eager-import breakage.

**Option B — post-install strip in `build-dmg.sh`.**
After `pip install`, delete `langchain_anthropic/`, `anthropic/`,
`langchain_google_genai/`, and the non-auth parts of `google/`.
Then patch `deepagents/graph.py:10-11` to guard the import:

```python
try:
    from langchain_anthropic import ChatAnthropic
    from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
except ImportError:
    ChatAnthropic = None
    AnthropicPromptCachingMiddleware = None
```

This runs on the shipped `.app` only — the dev venv keeps the full deps
for compatibility. Savings: ~18 MB off the DMG.

**Risk:** every time `deepagents` publishes a new release we have to
re-verify the patch target. The line numbers and exact import surface
will drift. Future middleware work in upstream could add more
`langchain_anthropic` references. Worth tracking against the CHANGELOG
before each bundle bump.

**Option C — upstream.**
File an issue on `langchain-ai/deepagents` asking for optional provider
extras (`deepagents[anthropic]`, `deepagents[google]`). Lazy-import in
`graph.py` so the base package can install without the adapters. This
is the clean fix but external timeline.

## Decision

Ship Option A for now. Keep a watch item: if the DMG crosses ~120 MB
compressed, revisit Option B with the graph.py patch committed to our
build script for easy maintenance.

Re-audit:
- on every `deepagents` version bump
- when packaging for platforms with tight size budgets (Mac App Store)

## Reproducing

```bash
cd /Users/tyroneross/Desktop/git-folder/local-smartz
uv tree --no-dev | grep -E "anthropic|google" | head
du -sh .venv/lib/python3.*/site-packages/{langchain_anthropic,anthropic,langchain_google_genai,google}
```

*Last audit: 2026-04-13 against deepagents v0.4.11.*
