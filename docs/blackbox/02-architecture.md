# Architecture

## The data flow

```
                      sentinel model ibm/skillberry-local
  agent under test ──────────────────────────────────────► Skillberry Proxy-Agent (SPA) :7000
  (tau2 llm_agent)    + skillberry-context-* headers            │  reads the ONE skill (skill_name)
                                                                ▼
                                                     Skillberry Store :8000
                                                     my_skill: docstrings → tool schemas,
                                                     scripts/*.py → callable tools
                                                                │  a tool calls env_<tool>(...)
                                                                ▼
                                                     tau2 environment service :8004
                                                     POST /{env_id}/tools/{tool}
                                                                │
                      the real model (SPA_MODEL_NAME) ◄─────────┘ (SPA calls it upstream)

  user simulator ─────────────────────────────────────────► the gateway directly (never SPA)
```

| Component | Port | Started by |
|---|---|---|
| Skillberry Store | `8000` (`SKILLBERRY_STORE_PORT`) | `run.sh` (`start_store`) |
| Skillberry Proxy-Agent | `7000` (fixed), config `7001` | `cap-evolve run` preflight (`start_spa`) |
| tau2 environment service | `8004` (`SPA_REMOTE_ENV_URL`) | `run.sh` |

`SPA_MODEL_NAME` (for example `openai/aws/claude-haiku-4-5`) is the model the proxy calls
upstream. If it is unset, the proxy uses its own default model.

## One trial's session

1. The trial starts. The tailoring opens a remote environment:
   `POST :8004/start_environment {"domain": "airline"}` returns an `env_id`.
2. The agent calls the sentinel model with the context headers. The proxy builds the prompt
   from the skill, and the model calls the skill's tools.
3. Each tool runs in the store. It reaches the trial's environment through
   `POST :8004/{env_id}/tools/{tool}`, with the `env_id` taken from the header.
4. **Before evaluation**, the environment service's trajectory (`GET :8004/{env_id}/trajectory`)
   is merged into tau2's, so the scorer replays the real database writes.
5. tau2 evaluates the trial.
6. **After evaluation**, the proxy's trajectory (`GET :7000/trajectory`) is merged without
   tau2's own tool calls, so the optimizer also sees the skill's composite calls.
7. In a `finally`, `POST :7000/disconnect`, then `POST :8004/{env_id}/stop_environment`.

**Why two merge windows:** if both trajectories are merged after evaluation, the scorer never
sees the writes. Then `db_match` is false and the reward is 0 on every trial, which reads as a
bad candidate rather than a wiring bug.

## The tau2 version

The example's adapter and tailoring were created from one tau2 commit,
`TAU2_REFERENCE_SHA` in [`setup.sh`](../../examples/tau2_sierra_airline/blackbox/setup.sh).
`setup.sh` checks out exactly that commit, and `install()` refuses any other one with a
`SeamError`. A newer tau2 needs a new onboarding with
[`PROMPT.md`](../../examples/tau2_sierra_airline/PROMPT.md).
