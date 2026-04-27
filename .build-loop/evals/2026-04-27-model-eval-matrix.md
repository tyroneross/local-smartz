# Model eval matrix - 2026-04-27

## Command

```bash
./.venv/bin/python -m localsmartz.model_eval --models gpt-oss:20b,qwen3:8b-q4_K_M --json
```

## Result

| Model | Weighted score | Pass | Fail | Median latency |
| --- | ---: | ---: | ---: | ---: |
| gpt-oss:20b | 1.000 | 6 | 0 | 1487.5 ms |
| qwen3:8b-q4_K_M | 1.000 | 6 | 0 | 5774.0 ms |

## Interpretation

Both local candidates passed the representative answer-quality smoke. For this
task set and machine state, `gpt-oss:20b` is the better default candidate
because it tied quality and was materially faster. Keep `qwen3:8b-q4_K_M` as a
smaller fallback or compatibility comparison target.

This eval is intentionally small. It should be treated as a quick model-selection
guard, not as a broad benchmark for long research, tool-heavy workflows, or
large local-file analysis.
