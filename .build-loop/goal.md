# Goal — LLM Provider Optimizations

## Concrete Goal
Land 10 commits in dependency-correct order in `/Users/tyroneross/dev/git-folder/local-smartz/`, each passing `uv run pytest`. End state: cloud providers (Anthropic, OpenAI, Groq) are first-class fallbacks with prompt caching, real usage metrics, retry/backoff, parallel workers, streaming, tier-aware routing, budget warnings, pricing freshness, and hygiene parity with Ollama.

## Scoring Criteria
1. **Tests pass at every commit** — code grader: `uv run pytest -x` exits 0 after each commit. Pass = green; Fail = any commit's test run fails.
2. **Provider-absent = no crash** — code grader: integration test that imports each cloud runner with no API key, calls `run_turn`, expects a clear `RuntimeError` with provider name, NOT a stack trace from the SDK. Pass = clear RuntimeError; Fail = SDK leak.
3. **Type/lint clean** — code grader: `uv run python -m py_compile <changed-files>` exits 0 on every changed file.
4. **Multi-provider scorecard runs end-to-end** — code grader: `uv run python -m localsmartz.model_eval --multi-provider --json` produces TSV + markdown at `.build-loop/evals/2026-05-08-multi-provider-parity.{tsv,md}` and exits 0 with at least Ollama provider scored (cloud providers skip-with-message when keys missing).
5. **MECE file ownership** — review-time check: every commit's `files_owned` is disjoint, every changed file has exactly one owner.
6. **Constraint adherence** — review-time: Ollama runner not modified except where strictly required; `_create_model`/`create_langchain_model` not collapsed; deepagents memory referenced in code touching either path.

Pass = all 6 green at final Review-F.
