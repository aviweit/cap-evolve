# Gateway key model comparison

Captured 2026-08-09 from `GET $ANTHROPIC_BASE_URL/models` against each key.
Keys are identified by prefix only — the full values are secrets.

| | outgoing key `sk-dHBjd…` | incoming key `sk-Mr84…` |
|---|---|---|
| models served | **34** | **23** |
| only in this key | 14 | 3 |
| in both | 20 | 20 |

## Lost by switching — present in the OLD key, absent from the NEW one

These are the candidates to request on the new key.

| model | notes |
|---|---|
| `aws/claude-sonnet-4-5` | superseded on the new key by `aws/claude-sonnet-4-6` |
| `aws/gpt-oss-120b` | **`agent_model` default** and `run_suite.sh` AGENT_MODEL fallback; pinned by tau2, skillsbench, spreadsheetbench and swebench/smoke tasks.json |
| `aws/us.claude-opus-4-7` | regional alias of `aws/claude-opus-4-7`, which is on both keys |
| `Azure/gpt-4.1` |  |
| `Azure/gpt-4o` |  |
| `Azure/gpt-5-2025-08-07` |  |
| `Azure/gpt-5-mini-2025-08-07` | **the agent used for every swebench run so far**; pinned by swebench pilot + full tasks.json |
| `Azure/gpt-5-nano-2025-08-07` |  |
| `Azure/gpt-5.1-codex-2025-11-13` |  |
| `azure/gpt-5.3-chat` |  |
| `azure/gpt-5.3-codex` |  |
| `claude-sonnet-4-5-20250929` | dated alias; `claude-sonnet-4-6` is on both keys |
| `GCP/gemini-2.0-flash` | older generation; `gemini-2.5-flash` is on both keys |
| `gcp/gemini-3-flash-preview` | new key has `gcp/gemini-3.6-flash` and `gcp/gemini-3.5-flash-lite` instead |

## Gained by switching — only in the NEW key

- `aws/claude-opus-4-8`
- `aws/claude-sonnet-4-6`
- `Azure/o4-mini`

## Available on both

- `aws/claude-haiku-4-5`
- `aws/claude-opus-4-7`
- `aws/claude-opus-5`
- `aws/claude-sonnet-5`
- `azure/gpt-5.4`
- `azure/gpt-5.5`
- `azure/gpt-5.6-luna`
- `azure/gpt-5.6-sol`
- `azure/gpt-5.6-terra`
- `claude-haiku-4-5-20251001`
- `claude-opus-4-6`
- `claude-opus-4-8`
- `claude-sonnet-4-6`
- `gcp/gemini-3-pro-preview`
- `gcp/gemini-3.1-pro-preview`
- `gcp/gemini-3.5-flash-lite`
- `gcp/gemini-3.6-flash`
- `gemini-2.5-flash`
- `gemini-2.5-pro`
- `rits/google/gemma-4-31B`

## Impact on the CI

| what | model | old | new |
|---|---|:--:|:--:|
| benchmarks.yml agent_model default | `aws/gpt-oss-120b` | yes | **no** |
| benchmarks.yml optimizer_model default | `claude-opus-4-8` | yes | yes |
| run_suite.sh AGENT_MODEL fallback | `aws/gpt-oss-120b` | yes | **no** |
| run_suite.sh OPTIMIZER_MODEL fallback | `claude-opus-4-8` | yes | yes |
| agent used for every swebench run so far | `Azure/gpt-5-mini-2025-08-07` | yes | **no** |
| spreadsheetbench/pilot tasks.json pin | `azure/gpt-5.5` | yes | yes |

Consequences of the switch, in order of severity:

1. **`agent_model` default breaks.** `aws/gpt-oss-120b` is unserved, so a default dispatch would
   fail. `sync_models.py` refuses to write in this situation (a `default:` outside its own
   `options:` is an invalid workflow) and requires `--agent-default <served-model>`.
2. **The swebench agent breaks.** `Azure/gpt-5-mini-2025-08-07` is unserved, so every number
   measured so far becomes non-reproducible on the new key. A different agent means a
   different measurement — earlier results are not comparable.
3. **The optimizer is unaffected.** `claude-opus-4-8` is on both keys.
4. **`tasks.json` `agent` pins go stale** for tau2, skillsbench, spreadsheetbench and swebench.
   Advisory only — `run_suite.sh` warns and the env value wins — so this is noise, not breakage.

Regenerate with `ci/benchmarks/lib/sync_models.py`; the pickers are kept in sync automatically
by `.github/workflows/sync-model-lists.yml`.
