# The prompt — onboard tau2 airline (SPA variant) and optimize composite skills

Paste this to your coding agent (Claude Code) at the cap-evolve repo root and say
**"follow RUN.md."** Intake treats this as a brand-new benchmark: the integration
step **clones tau2-bench + skillberry-store + skillberry-agent**, installs all
dependencies, starts the SPA stack (store → env manager → SPA), imports primitive
tools, writes the adapter, runs the `cap-evolve check` gate, then the full
baseline → optimize → gate → report loop. Everything below is the input intake needs.

```text
Follow RUN.md to run a cap-evolve optimization. Onboard this as a brand-new
benchmark — the intake/integration step should CLONE + INSTALL all dependencies.

# 1. CAPABILITY TO OPTIMIZE

- type:         [skill-package]
- what:         NEW composite skills that SPA translates into system prompt
                enrichment. These guide the agent to orchestrate the 14 frozen
                primitive tools (book_reservation, calculate, cancel_reservation,
                get_reservation_details, get_user_details, list_all_airports,
                search_direct_flight, search_onestop_flight, send_certificate,
                update_reservation_baggages, update_reservation_flights,
                update_reservation_passengers, get_flight_status,
                transfer_to_human_agents).
- seed:         seed_capability/primitive_skill/ is a READ-ONLY reference. The
                optimizer MUST NOT edit or delete it or any file inside it.
                It may only CREATE new sibling skill packages.
- capability_path:   seed_capability
- actions:      [create]   (create new skills ONLY — never edit primitive_skill)
- capability_sources:  []

## CONSTRAINTS ON NEW COMPOSITE SKILLS

1. Each new skill is a directory with SKILL.md + scripts/.
2. The SKILL.md snippet is what SPA injects as system prompt enrichment.
3. **scripts/ = the COMPLETE tool set sent to the LLM.** Each .py file with a
   public function becomes a callable tool. The LLM sees ONLY what is in scripts/.
4. scripts/ must contain ALL relevant tools: unchanged primitives (copied) +
   new composite tools. Composite tools call primitives via
   `_make_api_call(tool_name="<primitive>", ...)`.
5. The optimizer tests ONE composite skill at a time: adapter.apply() uploads it
   to the store and restarts SPA with SKILL_NAME=<that_skill>.

## TWO COMPOSITE TOOL PATTERNS

### Pattern 1: WRAPPER (replace a primitive with an enhanced version)

When a primitive tool needs additional logic (guards, validation, computation):
- scripts/ contains: 13 primitives (EXCLUDE the wrapped one) + the composite.
- The composite calls the primitive via `_make_api_call(tool_name="...", ...)`.
- The agent sees ONLY the composite — cannot bypass the guard.

### Pattern 2: AGGREGATION (new tool combining multiple primitives)

When the agent needs a multi-step operation it does incorrectly or not at all:
- scripts/ contains: all 14 primitives + the new composite (ADDED, none removed).
- The composite calls multiple primitives via `_make_api_call(tool_name="...", ...)`.
- The agent gains a new capability without losing any primitive.

Both patterns: NEVER touch `seed_capability/primitive_skill/` — only CREATE new
sibling skill directories.

# 2. BENCHMARK / DATASET

## 2a. tau2-bench (the task suite + runner)
- benchmark:    tau2-bench airline domain (airline_skillberry variant)
- repo:         https://github.com/skillberry-ai/skillberry-benchmarks.git (subdir tau2/tau2-bench)
- commit:       a3a83266008275e9d800fd709927fa3dc4f23ec5
- install:      git clone; git checkout a3a8326; pip install -e tau2/tau2-bench
- domain:       airline_skillberry
- agent type:   llm_agent (with LLM calls routed through SPA)

## 2b. Skillberry Store (holds primitive tools + composite skills)
- repo:         https://github.com/skillberry-ai/skillberry-store.git
- tag:          0.2.1
- install:      git clone --branch 0.2.1; python3.11 -m venv .venv; make install-requirements
- run:          EXECUTE_PYTHON_LOCALLY=True make run  (port 8000)
- health:       curl http://localhost:8000/health

## 2c. Skillberry Proxy-Agent (SPA — enriches LLM calls with skill prompts)
- repo:         https://github.com/skillberry-ai/skillberry-agent.git
- commit:       e359494f18267e339f9561acbd7a930e3b51189e
- install:      git clone; python3.11 -m venv .venv; make install-requirements
- run:          make run  (port 7000)
- health:       curl http://localhost:7000/health
- DEPENDS ON:   store (port 8000) must be running first
- env config:
    SKILL_NAME=<skill_to_evaluate>
    USE_AGENT_TOOLS=false
    USE_AGENT_PROMPTS=true
    MCP_PROMPTS_POSITION=postfix
    SPA_PROVIDER_NAME=litellm.ibm
    SPA_MODEL_NAME=aws/gpt-oss-120b

## 2d. tau2 Environment Manager (the HTTP API primitive tools call)
- port:         8004
- start:        cd tau2-bench && python scripts/start_tau2_environment_manager.py

## 2e. Tasks
- all 50 airline tasks (IDs "0" through "49")
- configurable via split_ids.json (or split_ids_task9.json for single-task runs)

# 3. RUNNER + MODELS + CREDENTIALS

## Architecture
  tau2 run_tasks (host)
    ├── agent LLM → SPA (host:7000, SKILL_NAME=X) → Store (host:8000) + upstream LLM
    │                 ↓
    │         injects skill X's SKILL.md as system prompt enrichment
    │         primitive tools available as callable tools
    └── user simulator LLM → OPENAI_BASE_URL directly (no SPA)

  tau2 Env Manager (host:8004) ← primitive tool HTTP calls

## Models + credentials
- agent model:      ibm/skillberry-local  (litellm alias → SPA on localhost:7000)
- user sim model:   openai/aws/gpt-oss-120b  (direct via OPENAI_BASE_URL)
- OPENAI_BASE_URL:  upstream LLM endpoint (e.g. IBM litellm gateway)
- OPENAI_API_KEY:   API key for upstream
- IBM_LITELLM_API_BASE: (= OPENAI_BASE_URL, exported for SPA's litellm.ibm provider)
- IBM_THIRD_PARTY_API_KEY: (= OPENAI_API_KEY, exported for SPA)

## Critical: SPA restart per candidate
Before each evaluation, adapter.apply() does:
  1. Upload composite skill to store: POST /skills/import-anthropic
  2. Stop SPA (make stop / kill port 7000)
  3. Export SKILL_NAME=<candidate_skill_name>
  4. Start SPA (make run)
  5. Wait for health check

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
  5. Purge store, import 14 primitive tools individually:
     POST /tools/add?selected_func=<name>&update=true (file upload)
  6. Start SPA (port 7000) with SKILL_NAME=primitive_skill — wait for health
  7. cap-evolve check

# 6. OPTIMIZER
- optimizer:    claude-code
- model:        claude-opus-4-8
- instructions: scope to skill-package CREATE only. Encode:
    * READ the primitive_skill signatures FIRST (seed_capability/primitive_skill/)
    * The optimizer may only CREATE new skill dirs alongside primitive_skill
    * Each new skill's SKILL.md is the prompt enrichment SPA will inject
    * New skills' scripts must delegate to primitive tools (never bypass them)

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
> (`seed_capability/primitive_skill/`), and `setup.sh` are what the intake /
> implement-and-check flow produced.
