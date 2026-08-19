# The prompt — onboard tau2 airline (SPA variant) and optimize the single `airline_skill`

Paste this to your coding agent (Claude Code) at the cap-evolve repo root and say
**"follow RUN.md."** Intake treats this as a brand-new benchmark: the integration
step **clones tau2-bench + skillberry-store + skillberry-agent**, installs all
dependencies, starts the SPA stack (store → env manager → SPA), imports the frozen
primitive tools and the single `airline_skill`, writes the adapter, runs the
`cap-evolve check` gate, then the full baseline → optimize → gate → report loop.
Everything below is the input intake needs.

```text
Follow RUN.md to run a cap-evolve optimization. Onboard this as a brand-new
benchmark — the intake/integration step should CLONE + INSTALL all dependencies.

# 1. CAPABILITY TO OPTIMIZE

- type:         [skill-package]
- what:         ONE skill named `airline_skill`, modified IN PLACE. Its SKILL.md is
                what SPA injects as system prompt enrichment; its scripts/ is the
                exact tool set sent to the LLM.
- frozen:       The 14 primitive tools are STANDALONE store tools imported from
                seed_capability/primitive_tools/functions.py and tagged
                `primitive-tool` (book_reservation, calculate, cancel_reservation,
                get_reservation_details, get_user_details, list_all_airports,
                search_direct_flight, search_onestop_flight, send_certificate,
                update_reservation_baggages, update_reservation_flights,
                update_reservation_passengers, get_flight_status,
                transfer_to_human_agents). They are NOT a skill and are never
                modified. `_make_api_call` lives in that module as an internal
                helper and is NOT registered as a tool (the importer's AST filter
                skips `_`-prefixed functions).
- seed:         seed_capability/airline_skill/ holds SKILL.md + 14 baseline wrapper
                tools, one per primitive, named `<primitive>_wrapper`.
                seed_capability/primitive_tools/ is READ-ONLY reference.
- capability_path:   seed_capability
- actions:      [modify]   (modify airline_skill in place — never a sibling skill)
- skill_name:   airline_skill
- capability_sources:  []

## CONSTRAINTS ON THE SINGLE SKILL

1. There is exactly ONE skill: `airline_skill`. Never create a sibling skill.
   The `name:` in its SKILL.md frontmatter must stay `airline_skill`.
2. The SKILL.md prose is what SPA injects as system prompt enrichment.
3. **scripts/ = the COMPLETE tool set sent to the LLM.** Each .py file's single
   top-level public function becomes a callable tool. The LLM sees ONLY scripts/.
4. **Tools call primitives BY NAME** — `cancel_reservation(...)`,
   `get_user_details(...)`. The store auto-detects the dependency; nothing is
   imported or declared. NO tool in the skill may call `_make_api_call` — that is
   infrastructure used only inside the primitives themselves.
5. **Helpers are NESTED and `_`-prefixed.** Any helper logic must be a function
   defined inside the tool's body with a name starting with `_`; a module-level
   helper would be registered as its own tool and shown to the LLM.
6. Each file in scripts/ has exactly ONE top-level public function whose name
   matches the filename, with a Google-style docstring (the store parses it into
   the tool schema).
7. adapter.apply() DELETES `airline_skill` from the store (cascading to its own
   tools), re-imports the candidate's `airline_skill/`, and restarts SPA with
   SKILL_NAME=airline_skill. The standalone primitives survive the cascade.

## THREE OPTIMIZATION PATTERNS

### Pattern 1: GUARD an existing wrapper

When the agent misuses a tool (wrong args, skips a check, violates policy): edit
the wrapper in place, adding a nested `_`-helper that checks the failing condition,
returns an error when it fires, and delegates to the primitive otherwise. The agent
sees ONLY the wrapper — it cannot bypass the guard. BOUNDED.

### Pattern 2: ADD an aggregation tool

When the agent needs a multi-step operation it does incorrectly or not at all: add
a new .py file to scripts/ whose function calls several primitives by name (e.g.
get_user_details then get_reservation_details per reservation id). The agent gains
one correct tool. BOUNDED (nothing existing changes).

### Pattern 3: REMOVE a tool

When a tool's presence causes misfires (wasted turns, premature escalation): delete
its .py file. UNBOUNDED — it changes the agent's options on every task, including
passing ones. Requires strong evidence.

All patterns: NEVER touch `seed_capability/primitive_tools/`.

# 2. BENCHMARK / DATASET

## 2a. tau2-bench (the task suite + runner)
- benchmark:    tau2-bench airline domain (airline_skillberry variant)
- repo:         https://github.com/skillberry-ai/skillberry-benchmarks.git (subdir tau2/tau2-bench)
- commit:       a3a83266008275e9d800fd709927fa3dc4f23ec5
- install:      git clone; git checkout a3a8326; pip install -e tau2/tau2-bench
- domain:       airline_skillberry
- agent type:   llm_agent (with LLM calls routed through SPA)

## 2b. Skillberry Store (holds the frozen primitive tools + the single airline_skill)
- repo:         https://github.com/skillberry-ai/skillberry-store.git
- tag:          0.2.1
- install:      git clone --branch 0.2.1; python3.11 -m venv .venv; make install-requirements
- run:          EXECUTE_PYTHON_LOCALLY=True make run  (port 8000)
- health:       curl http://localhost:8000/health

## 2c. Skillberry Proxy-Agent (SPA — enriches LLM calls with skill prompts)
- repo:         https://github.com/skillberry-ai/skillberry-agent.git
- commit:       e359494f18267e339f9561acbd7a930e3b51189e
- install:      git clone; python3.11 -m venv .venv; make install-requirements
- run:          make run  (ports 7000 main + 7001 config)
- health:       curl http://localhost:7000/health
- DEPENDS ON:   store (port 8000) must be running first
- PORT IS FIXED at 7000 and is NOT configurable by env var. Three places outside
  this example hardcode it: tau2's `config.py`
  (`SKILLBERRY_AGENT_URL = "http://127.0.0.1:7000"`), two literals in `tau2/run.py`,
  and `uvicorn.run(..., port=7000)` in SPA's `main.py`. A knob here would move the
  health check without moving the routing, so there deliberately isn't one.
  * Both 7000 and 7001 must be FREE before `setup.sh` — it preflights them and
    stops with the offending PID named.
  * On macOS, port 7000 is held by `ControlCenter` (AirPlay Receiver) by DEFAULT.
    Turn it off: System Settings > General > AirDrop & Handoff > AirPlay Receiver.
  * To run SPA on another port you must patch those three locations yourself; the
    example does not support it.
  * `stop_spa()`/`teardown.sh` kill only the PID SPA recorded in
    `/tmp/skillberry-agent-service.pid`, and fall back to the port owner ONLY after
    confirming it is SPA — so a foreign process squatting 7000 is reported, never
    SIGKILLed.
- env config:
    SKILL_NAME=airline_skill
    USE_AGENT_TOOLS=false
    USE_AGENT_PROMPTS=true
    MCP_PROMPTS_POSITION=postfix
    SPA_PROVIDER_NAME=litellm
    SPA_MODEL_NAME=openai/aws/gpt-oss-120b

## 2d. tau2 Environment Manager (the HTTP API primitive tools call)
- port:         8004
- start:        cd tau2-bench && python scripts/start_tau2_environment_manager.py

## 2e. Tasks
- all 50 airline tasks (IDs "0" through "49")
- configurable via split_ids.json (or split_ids_task9.json for single-task runs)

# 3. RUNNER + MODELS + CREDENTIALS

## Architecture
  tau2 run_tasks (host)
    ├── agent LLM → SPA (host:7000, SKILL_NAME=airline_skill) → Store (host:8000) + upstream LLM
    │                 ↓
    │         injects airline_skill's SKILL.md as system prompt enrichment
    │         airline_skill's scripts/ are the callable tools; each delegates to
    │         a frozen primitive, which calls the Env Manager
    └── user simulator LLM → OPENAI_BASE_URL directly (no SPA)

  tau2 Env Manager (host:8004) ← primitive tool HTTP calls

## Models + credentials
- agent model:      ibm/skillberry-local  (litellm alias → SPA on localhost:7000)
- user sim model:   openai/aws/gpt-oss-120b  (direct via OPENAI_BASE_URL)
- OPENAI_API_KEY:   API key for upstream LLM
- OPENAI_API_BASE:  upstream LLM endpoint URL
- OPENAI_BASE_URL:  upstream LLM base URL (same value as OPENAI_API_BASE)

## Critical: skill replacement + SPA restart per candidate
Before each evaluation, adapter.apply() does:
  1. DELETE /skills/airline_skill?delete_tools=true&delete_snippets=true
     (cascade removes the skill's own wrapper tools; the standalone primitives are
     referenced by no skill manifest and survive untouched)
  2. Upload the candidate's airline_skill/: POST /skills/import-anthropic
  3. Stop SPA (make stop / kill port 7000)
  4. Export SKILL_NAME=airline_skill
  5. Start SPA (make run)
  6. Wait for health check

# 4. SCORER
- metric:       tau2's own reward in [0,1] (per-task)
- deterministic: reads reward from rollout.metadata (never re-runs)
- feedback:     gold-SAFE, argument-level: names the wrong ARGUMENT key + the
                AGENT'S OWN wrong value. Never the gold value.

# 5. STARTUP SEQUENCE

  1. Clone skillberry-benchmarks (@ a3a8326), skillberry-store (tag 0.2.1), skillberry-agent (@ e359494)
  2. Install dependencies for each
  3. Start skillberry-store (port 8000) — wait for health check
  4. Start tau2 Env Manager (port 8004)
  5. Purge store, then import the 14 primitive tools individually as STANDALONE
     tools (not a skill), tagging each `primitive-tool`:
       for each PUBLIC func in seed_capability/primitive_tools/functions.py
         (public = not starting with '_', so _make_api_call is excluded):
       POST /tools/add?selected_func=<name>&update=true   -F "tool=@functions.py"
       GET /tools/<name> -> set tags=['primitive-tool'] -> PUT /tools/<name>
  6. Import the single skill AFTER the primitives (so the store can auto-detect
     each wrapper's dependency on the primitive it calls by bare name):
       POST /skills/import-anthropic  -F source_type=folder
         -F folder_path=<abs>/seed_capability/airline_skill -F snippet_mode=file
  7. Start SPA (port 7000) with SKILL_NAME=airline_skill — wait for health
  8. cap-evolve check

# 6. OPTIMIZER
- optimizer:    claude-code
- model:        claude-opus-4-8
- instructions: scope to MODIFYING the single airline_skill. Encode:
    * READ the primitive signatures FIRST (seed_capability/primitive_tools/functions.py)
    * The optimizer edits ./airline_skill/ in place — SKILL.md, existing wrapper
      tools, new composite tools, and removal of tools it can justify
    * Never edit primitive_tools/; never create a sibling skill
    * Tools call primitives BY NAME; no tool calls _make_api_call
    * Helpers must be nested inside their tool and _-prefixed

# 7. BUDGET / GATE
- algorithm:        hill-climb (--focus all)
- max_iterations:   1           num_trials: 1
- per-iteration optimizer $ cap: optimizer_usd_per_iter 20
- optimizer_max_turns: 100
- max_optimizer_usd: 100        max_usd: 200
- gate:             paired, k_se 0.0
- store:            git

# 8. CONFIGURING TASK SCOPE
- Default: all 50 tasks (split_ids.json)
- Single task: use split_ids_task9.json
  Switch via: cap-evolve run --split-ids-file split_ids_task9.json
  Or edit capevolve.yaml: split_ids_file: split_ids_task9.json
```

> The bundled `examples/tau2_airline_spa/` is the **result** of following this prompt:
> the adapter (`adapters/adapter.py` + `adapters/spa_env.py`), the seed capability
> (`seed_capability/airline_skill/` + the frozen `seed_capability/primitive_tools/`),
> and `setup.sh` are what the intake / implement-and-check flow produced.
> See `DESIGN-optimization-rework.md` for the rationale behind the single-skill model.
