---
name: spa
description: The Skillberry proxy runtime — put the optimized capability in the Skillberry Store and let the Skillberry Proxy-Agent (SPA) inject it into the agent's LLM calls, so the benchmark never sees skill files. Use when a capevolve.yaml sets `runtime: spa`, or when you need to provision, start, deploy to, stop or clean that stack.
component: runtime
argument-hint: "up | status | deploy | down | clean"
allowed-tools: Read, Write, Edit, Bash
provides: []
needs: []
---

# Runtime: SPA (the Skillberry proxy)

A **runtime** answers *how a candidate reaches the model under test* — as opposed to a
**capability**, which is *what gets edited*. The two are independent: the same
`skill-package` capability can be delivered by writing files where a runner reads them
(`runtime: direct`) or by this runtime.

In SPA mode:

```
benchmark runner
  └── the agent's LLM call ──► SPA (host:7000) ──► Skillberry Store (host:8000)
                                    │                  (resolves ONE skill:
                                    │                   prose -> prompt enrichment,
                                    │                   scripts/ -> callable tools)
                                    └──────────────► upstream LLM
  other LLM calls (user simulator, judge, verifier) ──► upstream LLM, NEVER SPA
```

The agent gets no skill files, no mounted directory, no visible prompt edit — which is
the shape Skillberry actually ships, so optimizing here optimizes the real thing.

## The commands

```bash
python skills/runtimes/spa/scripts/run.py up --skill-name <name> [--skill-dir DIR]
python skills/runtimes/spa/scripts/run.py status [--json]
python skills/runtimes/spa/scripts/run.py deploy --skill-dir DIR --skill-name <name>
python skills/runtimes/spa/scripts/run.py down
python skills/runtimes/spa/scripts/run.py clean [--keep-clones]
```

`up` is idempotent: a healthy service is reported, never restarted — restarting SPA
mid-evaluation would swap the skill under a running rollout.

## Using it from an adapter

```python
from spa_env import Protection, reset_store_to_skill, restart_spa

PROTECT = Protection(tags=("primitive-tool",))     # the benchmark's frozen substrate

def apply(self, candidate_dir, edits=None):
    if edits:
        self.materialize(candidate_dir, edits)
    self._deploy_error = None                       # never inherit the last candidate's
    skill_dir = Path(candidate_dir) / SKILL_NAME
    if not (skill_dir / "SKILL.md").exists():
        self._deploy_error = f"{SKILL_NAME}/SKILL.md missing under {candidate_dir}"
        return
    try:
        reset_store_to_skill(skill_dir, SKILL_NAME, PROTECT)
        restart_spa(SKILL_NAME)
    except RuntimeError as e:
        self._deploy_error = str(e)                  # MUST NOT raise — see below
```

**`apply()` must never raise.** cap-evolve enters `live()` inline, so an exception aborts
the whole run with the budget half spent over one flaky restart. Record the failure and
let `run_batch`/`run_trials` return errored rollouts: the harness then *excludes* the
candidate instead of scoring it 0.0, which is correct, because a failed deployment is
infrastructure noise and not a verdict on the capability.

## What this runtime does not do (yet)

* **Tailor a benchmark.** A runner needs three things installed in it for SPA mode to
  produce correct data — Skillberry context headers on the agent's LLM calls, a merge of
  the proxy-side trajectory into its own, and a `disconnect` at session end. This phase
  assumes the benchmark **already has them** (as the `skillberry-benchmarks` fork of
  tau2 does). Onboarding a runner that does not is the next phase.
* **Host the environment.** Store-hosted tools execute in the store's process, not where
  the benchmark's state lives, so a benchmark needs an HTTP service fronting its
  environment. This phase expects the benchmark to provide one (tau2 ships an environment
  manager) and only health-checks it via `SPA_REMOTE_ENV_URL`.

## Facts that cost someone a debugging session

* **SPA serves exactly ONE skill**, resolved `SKILL_UUID` > `SKILL_NAME` > *a search of
  the chat history*. That last fallback is silent and looks like success **even against an
  empty store**, so `start_spa()` requires a name and clears `SKILL_UUID`.
* **Ports 7000/7001 are fixed** in SPA and hardcoded by its consumers. On macOS,
  ControlCenter holds 7000 for AirPlay Receiver by default (System Settings > General >
  AirDrop & Handoff).
* **A stale PID sentinel** makes `make run` print "service is already running" and exit 0
  without starting anything, after which the health check can only time out. `make stop`
  does not remove it; we always do.
* **`lsof -ti :PORT` needs `-sTCP:LISTEN`** — without it, lsof also lists *clients* of the
  port, and cap-evolve's own runner is a client of SPA.
* **SPA must bind `0.0.0.0`** to be reachable from a container, which reaches the host at
  the Docker bridge gateway (Linux/WSL2, usually `172.17.0.1`) or `host.docker.internal`.
* **The store's delete cascade silently leaves tools behind.** `delete_skill` deletes the
  manifest first and the tools second, for that reason.
* **Every public top-level function** in a skill's `scripts/*.py` becomes its own tool;
  helpers must be nested and `_`-prefixed.
* **SPA reports no token usage.** Rollout `cost_usd`/`tokens` are 0 and any `max_usd`
  ceiling is inert — only optimizer budgets bind. A 0 in the cost panel means *not
  measured*, not free.

## Pinned versions

`scripts/spa_env.py` holds the pins (store tag `0.2.1`, agent commit `e359494`), each
env-overridable (`SKILLBERRY_STORE_REF`, `SKILLBERRY_AGENT_REF`) for a bisect. Both
services need their own Python 3.11 venv, created with `uv`.
