# DMG bundle size: dependency analysis

Audit for trimming provider SDKs we don't use. Local Smartz is Ollama-only —
no Anthropic, no Google GenAI — but both packages ride in via
`deepagents`' hard requires.

## Why they're present

`deepagents` 0.5.2 declares:

```
langchain-anthropic >= 1.3.4
langchain-google-genai >= 4.2.0
```

And `deepagents/graph.py:16-17` *eagerly imports*:

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

## Disk footprint in the clean bundled Python (pycache-stripped)

```
232K  langchain_anthropic/
4.1M  anthropic/
296K  langchain_google_genai/
8.0M  google/        (of which: 4.4M genai, 728K auth, 596K api, 236K oauth2,
                      plus cloud/gapic/logging/longrunning/rpc/type)
708K  jiter/         (transitive of anthropic; streaming-only)
```

Keep: `google/protobuf/` (996K) and `google/_upb/_message.abi3.so` (~648K) —
required by `opentelemetry-proto` (traces/metrics/logs collector protobufs).

## Approach shipped (Option B from prior audit)

Post-install strip in `app/build-dmg.sh` plus a patch to `deepagents/graph.py`
that lazy-imports `ChatAnthropic` and installs a no-op `AnthropicPromptCachingMiddleware`
stub when `langchain_anthropic` is absent. The stub is a real `AgentMiddleware`
subclass so `graph.py`'s unconditional `middleware.append(...)` calls still
succeed — they just become no-ops at runtime.

### Patch (`app/scripts/deepagents-slim.patch`)

Applied by an inline Python script in `build-dmg.sh`. Anchor-based
replacement; idempotent; skips if the anchor text is missing (e.g. after a
deepagents upgrade reshapes the imports).

```python
# Before:
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

# After:
try:
    from langchain_anthropic import ChatAnthropic
    from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
except ImportError:
    ChatAnthropic = None
    from langchain.agents.middleware.types import AgentMiddleware

    class AnthropicPromptCachingMiddleware(AgentMiddleware):
        """Stub: no-op when langchain_anthropic is not installed (Ollama-only build)."""
        def __init__(self, *args, **kwargs):
            super().__init__()
```

### Directories removed from the bundled site-packages

```
langchain_anthropic/
langchain_anthropic-*.dist-info/
anthropic/
anthropic-*.dist-info/
langchain_google_genai/
langchain_google_genai-*.dist-info/
google_genai-*.dist-info/
google_auth-*.dist-info/
googleapis_common_protos-*.dist-info/
jiter/
jiter-*.dist-info/

google/genai/
google/auth/
google/oauth2/
google/api/
google/cloud/
google/gapic/
google/logging/
google/longrunning/
google/rpc/
google/type/
```

## Measured impact

Measured against the currently-installed `/Applications/Local Smartz.app`
(deepagents 0.5.2) on 2026-04-13, after `__pycache__` strip both before and
after the slim, to isolate the delta from bytecode noise.

| Location            | Baseline | After slim | Delta |
|---------------------|----------|------------|-------|
| `site-packages/`    | 139 MB   | 127 MB     | -12 MB (-8.6%) |
| `Contents/Resources/python/` total | 723 MB | 711 MB | -12 MB |

DMG (UDZO zlib-9) delta was not re-measured in this session — the
`.app` would need a full xcodebuild rebuild to ship the slimmed Python.
Expect the compressed DMG savings to be smaller than the raw 12 MB
because zlib already compresses a lot of duplicate provider-SDK code.

## Validation

1. `"$PY/bin/python3" -c "from deepagents import create_deep_agent; print('ok')"`
   → `ok` ✅
2. `pytest tests -q` against the slimmed bundled Python → **379 passed** ✅
3. `from opentelemetry.proto.trace.v1 import trace_pb2` still imports ✅
   (confirms we kept the right subtree of `google/`).

## Upstream issue to track

Open on `langchain-ai/deepagents`: request optional provider extras
(`deepagents[anthropic]`, `deepagents[google]`) with lazy imports in
`graph.py`. Eliminates the need for our patch. Track against each
`deepagents` version bump.

## Re-verify checklist on deepagents upgrade

- [ ] `grep -n "from langchain_anthropic" site-packages/deepagents/graph.py` —
  confirm the two anchor lines in the patch still match byte-for-byte.
- [ ] Search for any new `from langchain_anthropic` / `from langchain_google_genai` /
  `from google.genai` imports anywhere in the deepagents tree.
- [ ] Confirm `AnthropicPromptCachingMiddleware` is still a subclass of
  `AgentMiddleware` in upstream (our stub matches that parent).
- [ ] Re-run the smoke test and the full pytest suite against the slimmed bundle.
- [ ] Update this file with new before/after numbers.

## Reproducing

```bash
cd /Users/tyroneross/Desktop/git-folder/local-smartz
uv tree --no-dev | grep -E "anthropic|google" | head
du -sh .venv/lib/python3.*/site-packages/{langchain_anthropic,anthropic,langchain_google_genai,google}
```

*Last audit: 2026-04-13 against deepagents 0.5.2, langchain-anthropic 1.4.0,
langchain-google-genai 4.2.1, google-genai 1.73.0.*
