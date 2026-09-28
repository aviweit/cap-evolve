# The prompt — onboard tau2-bench airline and optimize its TOOL SURFACE, delivered through the proxy

Paste this to your coding agent (Claude Code) at the cap-evolve repo root and say
**"follow RUN.md."** This is the **blackbox** arm: the candidate becomes a skill in the Skillberry
Store and the Skillberry Proxy-Agent injects it into the agent's LLM calls, so the benchmark never
sees skill files. The sibling `../PROMPT.md` is the **direct** arm of the same benchmark — same
tasks, same capability, same scorer; only the delivery differs.

The intervention skill owns everything about how `blackbox` *works*. This prompt owns the
benchmark, its environment service, and — critically — how the benchmark is **tailored from
outside** so that it is never forked or edited (§2c).

```text
Use only information in this current cap-evolve repo root folder on the current Git branch.
Do not use external documentation, web searches, prior knowledge, other repositories, branches, tags, or commits.
Treat this repository as the sole source of truth. If required information is missing, state that it is missing rather than guessing.
Start with RUN.md and follow it exactly.


Follow RUN.md to run a cap-evolve optimization. Onboard this as a brand-new
benchmark — the intake/integration step should CLONE + INSTALL it (not assume it
exists). Here is everything intake needs:

# 1. CAPABILITY TO OPTIMIZE  (a copy is edited each iteration; the original is never touched)
- type:         [tools]                     # the agent's TOOL SURFACE only
- delivered as: ONE skill package in the Skillberry Store (my_skill/), whose scripts/*.py are the
                agent's tools. Every public top-level function in a script becomes its own tool;
                helpers must be nested and `_`-prefixed.
- tools means:  edit tool docstrings/descriptions; edit tool behavior/code; and ADD/REMOVE tools,
                including composite tools that call existing tools
- the POLICY is NOT part of the capability and is NEVER edited. It is the benchmark's own
                specification of the task — the rules the agent is GRADED against — so a candidate
                that could rewrite it would be editing the exam: soften an inconvenient rule and
                the reward rises, because the same text is what judges the agent. Under this arm
                the policy reaches the agent UNCHANGED (the proxy keeps the request's system
                messages, USE_AGENT_PROMPTS=true) and is not an artifact the optimizer can see.
- the FROZEN substrate is part of the SEED but NOT the capability: primitive_tools/functions.py
                holds tau2's own primitives as standalone store tools plus the ONE bridge to the
                benchmark's environment service. Those are what make the measurement comparable,
                so seal them: protected_paths: ["primitive_tools/*", "my_skill/SKILL.md"].
                Editing a sealed path makes the candidate INDECISIVE (the measurement is void)
                rather than scored 0.0.
- SKILL.md is sealed too, for this run: an empty SKILL.md is a NEUTRAL seed, because the tool
                surface — not prose — is what is being optimized here. (Optimizing the prose
                instead is a different capability; do not quietly widen this one.)
- capability_sources: []  — the wrappers call primitives BY NAME through the store, so no shared
                types module is imported by the editable code.

# 1b. INTERVENTION  (how the capability reaches the model — a spec key)
- the spec gets the top-level line:  intervention: blackbox
                (`capabilities:` says WHAT is edited; `intervention:` says HOW it reaches the
                model. capevolve.yaml-only — no CLI override.)
- skill_name: my_skill  — REQUIRED. The proxy serves exactly ONE skill, resolved
                SKILL_UUID > SKILL_NAME > a search of the chat history. That last fallback is
                silent and looks like success EVEN AGAINST AN EMPTY STORE, so the name is not
                optional.
- FOLLOW THE INTERVENTION SKILL — skills/interventions/llm-proxies/blackbox/SKILL.md and the
                seeding reference it points to. It owns provisioning, service lifecycle, the seed
                shape, the wrapper authoring rules, store import order, and the per-candidate
                deploy. Do NOT re-derive any of that here, and do not hand-roll a copy of
                blackbox_env. This prompt supplies only what the skill cannot know: the benchmark,
                its environment service, and the TAILORING in §2c.
- VERIFY:       `cap-evolve check .capevolve/project` green, and `intervention: sap` (a typo)
                REJECTED BY NAME, not silently defaulted to direct.

# 2. BENCHMARK / DATASET  (the eval) — INSTALL IT DURING INTAKE
- benchmark:    tau2-bench, airline domain
- repo:         https://github.com/sierra-research/tau2-bench   (latest main; record the resolved commit)
- install:      git clone into vendor/tau2-bench, then
                  pip install -e vendor/tau2-bench "websockets>=13.0"
                WHY the extra package: tau2's data_model imports its voice stack
                UNCONDITIONALLY, but `websockets` ships only in the [voice] extra — so a base
                install cannot even `import tau2`. `websockets` alone is enough; do NOT install
                [voice], which drags in livekit, boto3 and google-cloud-aiplatform.
- UNMODIFIED:   install the benchmark AS PUBLISHED. Do NOT fork it, do not edit the checkout, do
                not install a tailored build of it. If you believe the benchmark must be changed
                to make this arm work, STOP and say so — §2c is how that need is met instead. A
                benchmark edited to suit the intervention stops being comparable to anyone else's
                numbers, including our own earlier ones.
- RECORD the resolved commit in PROJECT.md. It is part of the MEASUREMENT, not bookkeeping: the
                benchmark owns the policy text the agent reads, the task set, and the reward
                checks, so two numbers are comparable only if both name the commit behind them.
- domain:       "airline_skillberry" — registered by OUR tailoring module at apply() time, NOT by
                the benchmark. Its environment is a PLAIN vanilla tau2 airline environment (see
                §2c for why that matters).
- tasks:        "adapter" — all 50 airline tasks from tau2.domains.airline.environment.get_tasks;
                no network in tasks()
- splits:       all 50 as train = val = test (no-holdout fit metric; the engine logs a
                splits_warning and the report flags the test number as a fit metric). Pin them in
                split_ids.json.

# 2b. THE BENCHMARK'S ENVIRONMENT SERVICE  (required by this arm)
- WHY it is needed: store-hosted tools execute in the STORE's process, not where the benchmark's
                state lives, so the skill's tools can only reach that state through a service the
                benchmark fronts over HTTP. The intervention health-checks this and aborts
                without it.
- tau2 HAS one, and it is UPSTREAM code — tau2.orchestrator.environment_manager.EnvironmentManager
                is vanilla tau2, not something we add. Do NOT reimplement it; only its launcher is
                ours (scripts/start_tau2_env_manager.py).
- start it:     port 8004, from cap-evolve's venv. ~10s to start (importing tau2 pulls in
                litellm), so POLL the port rather than sleeping — and probe a route it actually
                serves (/docs or /). It serves NO /health, so probing that reports a live service
                as dead and a readiness loop burns every attempt.
                LITELLM_LOCAL_MODEL_COST_MAP=True skips litellm's doomed remote cost-map fetch,
                which otherwise stalls startup until it times out.
- its CALL SHAPE (what the frozen primitives speak):
                  base URL:     http://127.0.0.1:8004   (also set SPA_REMOTE_ENV_URL in .env)
                  URL:          {base}/{env_id}/tools/{tool_name}
                  request:      POST {"name": <tool>, "arguments": {...}}
                  success:      result["content"] is a JSON *string* — json.loads it
                  failure:      result["content"] is a plain message; non-200 → raise
- per-rollout identity: the store's executor injects `env_id` into the tool module, so the
                primitives need no session plumbing of their own. Getting the RIGHT env_id there
                is what §2c's context header is for.

# 2c. TAILORING THE BENCHMARK — FROM OUTSIDE, NEVER INSIDE
This arm needs five things stock tau2 does not do. They are supplied by a module BESIDE the
adapter (adapters/tau2_tailoring.py, deployed into the project's adapters/), installed from
apply(). They are NEVER obtained by editing the benchmark.
- the five:     (1) Skillberry context headers on the AGENT's LLM calls (never the user
                simulator's, never a judge's); (2) routing the agent's model to the proxy;
                (3) a domain + agent for the arm; (4) retrieving the proxy-side trajectory and
                merging it into the runner's own; (5) a vMCP disconnect at session end.
- PREFER THE RUNNER'S PUBLIC EXTENSION POINTS. For tau2 all of (1)-(3) need no patch at all:
                  * registry.register_domain(...) and registry.register_agent_factory(...) are
                    public, and a late registration is honoured because domains resolve lazily.
                  * `llm_args` is forwarded from run config -> agent -> generate() -> the LLM
                    client UNFILTERED, so extra_headers / base_url / api_key /
                    custom_llm_provider all travel that way. The AGENT FACTORY is the right home
                    for it: it runs exactly ONCE per rollout and receives `task`, so it can start
                    this rollout's remote environment, keep the env_id on the agent, and bake the
                    header in.
- Where NO public seam exists, a NARROW, IDEMPOTENT wrapper installed at apply() time is allowed
                — never an edit to the checkout, never a fork, never sys.modules surgery. Two are
                needed here, one per merge window below.
- DO NOT put the remote session on the ENVIRONMENT. It is the obvious place and it is wrong: the
                evaluator re-invokes the registered domain constructor 1-3 MORE times per rollout,
                so an environment that starts a session in __init__ orphans remote environments
                and makes env_id ambiguous. The arm's domain must be a PLAIN environment,
                side-effect-free and identical to tau2's own airline, so the evaluator may build
                it freely AND its real tools let the evaluator's state replay rebuild a local DB.
- THE TWO MERGES HAVE DIFFERENT WINDOWS. This is the subtlest requirement here and the easiest to
                get wrong, so it is stated outright:
                  * the ENV-SERVICE trajectory (the PRIMITIVE calls, port 8004) must be merged
                    BEFORE evaluation. Under this arm every real mutation happens in the store
                    against the remote environment, so tau2's own trajectory never saw those
                    calls; without them the evaluator's state replay reconstructs nothing, the DB
                    check goes false and reward collapses to 0. Its messages are properly paired
                    (a call, then its result), which is what makes them legal replay input.
                  * the PROXY trajectory (the COMPOUND/skill calls, port 7000) must be merged
                    AFTER evaluation, and before the runner persists the result. It is filtered
                    to drop primitives (they already arrived via the env service), and it is NOT
                    legal replay input — merging it early both changes the reward and can hard-
                    fail the rollout into a retry storm.
                  Getting either window wrong produces a plausible-looking trace and a WRONG
                  score, with no error anywhere.
- ASSERT EVERY SEAM, at install time, in an offline verify() the check gate runs. A SEAM YOU
                CANNOT ASSERT IS A SEAM YOU MAY NOT USE. This is not optional hardening: the
                benchmark tracks latest main, and the two highest-risk seams fail SILENTLY —
                if `llm_args` stops reaching the client the context header vanishes and the proxy
                falls back to a shared default session; if the pre-evaluation merge stops firing
                the DB check goes false. Both read as a worse capability rather than broken
                wiring. PROVE the llm_args passthrough by stubbing the client call and asserting
                the header arrived — do not merely inspect a signature. Every failure message
                must name the seam, the tau2 version+commit it ran against, what it costs, and
                that validating a changed commit is the operator's responsibility.

# 3. RUNNER  (the agent under test) + MODELS + CREDENTIALS
- how to run:   tau2's own batch runner (adapter.run_batch -> tau2.runner.run_tasks with a
                TextRunConfig; the 1.0.x API, not the deprecated flat-kwargs form)
- fast eval:    ALSO implement the optional adapter method
                run_trials(tasks, ctx, *, n_trials, base_seed) -> {task_id: [Rollout, ...]}.
                Run ALL num_trials in ONE run_tasks call with num_trials=N (grouped by sim.trial)
                and return {task_id: [trial0, trial1, ...]} (len n_trials, trial-ordered). When
                present, cap-evolve calls it ONCE per candidate instead of looping run_batch per
                trial; per-trial persistence is UNCHANGED so pass^k / SE / resume keep working.
- apply():      deploy the candidate as THE store skill (primitives FIRST so a skill redeploy
                cannot cascade into the frozen substrate, then rebind the proxy) AND install the
                §2c tailoring. Guard on the CANDIDATE'S SHAPE (is a skill package present?)
                rather than on the spec, so a spec/seed mismatch fails loudly instead of
                delivering the wrong way silently. apply() must NEVER raise: record the deploy
                failure and let the rollouts come back errored, so the harness EXCLUDES the
                candidate instead of scoring it 0.0 — a failed deployment is infrastructure
                noise, not a verdict on the capability.
- AGENT model:  the proxy-routed sentinel (default `ibm/skillberry-local`). It is a MODEL NAME,
                not a URL; the route is decided by exact string match, so do not normalize it.
- USER SIMULATOR model: a real gateway model, NEVER the sentinel. That boundary is a CORRECTNESS
                rule, not a preference — routing the simulator through the proxy injects the
                capability into the very thing measuring the agent.
- gateway wiring: OpenAI-compatible endpoint, standard bearer auth, catalog ids normalized once
                to `openai/<alias>`  (NO litellm monkeypatch, NO tau2 fork)
- credentials:  OPENAI_BASE_URL (or OPENAI_API_BASE) + OPENAI_API_KEY in the repo-root .env
- NEVER put an API key in the llm_args you hand the runner: tau2 records llm_args VERBATIM into
                its results file, which trajectories() exposes, cap-evolve copies to the
                optimizer, and `store: git` COMMITS. A key passed that way becomes a committed
                secret that was also shipped to the optimizer.
- concurrency:  start LOW (e.g. TAU2_MAX_CONCURRENCY=4) — every agent call funnels through ONE
                proxy and ONE store process.
- cost:         the proxy reports no token usage, so rollout cost_usd/tokens are 0 and any max_usd
                ceiling is INERT — only optimizer budgets bind. A 0 in the cost panel means NOT
                MEASURED, not free. Say so rather than presenting it as free.

# 4. SCORER  (what to optimize against) — and WHERE the metric comes from
- metric:       tau2's own task reward in [0,1]
- metric source: tau2 computes it per simulation as `sim.reward_info.reward`; the per-check
                breakdown is in `sim.reward_info` (db_check / action_checks / communicate_checks /
                nl_assertions / env_assertions). Implement adapter.score() to read the reward +
                reward_info the run stashes from each simulation, and verify score() is
                deterministic on a fixed rollout (the `cap-evolve check` gate enforces this).
                NOTE for airline: `reward_basis` is [DB, COMMUNICATE] — action_checks are reported
                but do NOT enter the reward. Do not chase action matches to explain a 0.0.
- feedback:     gold-AWARE but gold-SAFE, and ARGUMENT-LEVEL — this IS the learning signal, so a
                tool-name-only message ("action X was wrong") is too coarse. For EACH failing
                check, localize the defect at the argument level:
                  * for each mismatched write/action, name the differing ARGUMENT key + the
                    AGENT'S OWN wrong value (e.g. "book_reservation: payment_id='credit_card_9'
                    is not on the user's profile; available=[credit_card_4421, gift_card_8]");
                  * for communicate misses, name the un-stated value when derivable from the
                    agent's own state (e.g. "did not state the computed total cost ($150 from
                    your own observed amounts)").
                Gold-SAFE: NEVER read or print the gold/expected value — derive everything from
                the agent's OWN messages/tool-calls and the user's OWN profile/db state (parsed
                from the agent's own tool results in the trace). Use reward_info only to know
                WHICH action/argument failed (the gold action's arg KEYS are safe; its VALUES are
                not). Fall back to the tool-name message when a piece isn't safely derivable.
- objective:    maximize mean reward on the VAL split

# 4b. TRAJECTORIES  (the FULL traces the optimizer reads) — PATH IS AN INPUT
- where:        persist tau2's native per-task simulation results (full transcript + reward_info)
                via run_tasks(save_path=...), at the ONE path format every tau2 adapter in this
                repo shares:
                <run_dir>/native_sims/<tag>/<split>/results_<YYYYmmdd_HHMMSS>_<pid>.json
                (<tag> is the candidate dir from ctx; <split> stands in for the phase, which no
                adapter is told. The timestamp+pid matters: tau2 reads an existing results file
                as a run to RESUME and prompts on stdin, which an eval does not have.)
- expose:       implement adapter.trajectories(split) to return that directory. cap-evolve copies
                it VERBATIM into the optimizer's working dir as ./trajectories/ each iteration.
- these native files are ALSO the record that §2c's two merges worked: the persisted simulation
                must contain BOTH the primitive calls and the compound skill calls. A trace with
                only one of them is a WIRING BUG, not a bad candidate — say so rather than
                letting the optimizer chase a phantom capability problem.

# 5. OPTIMIZER  (proposes the edits) + MODEL + CREDENTIALS + CONTEXT
- optimizer:    claude-code
- model:        claude-opus-4-6
- credentials:  a logged-in Claude Code session (or ANTHROPIC_API_KEY)
- runner_repo_path:  ../../vendor/tau2-bench  (the cloned checkout — surfaced to the optimizer as
                read-only context so it can consult tau2's tools/scoring/task structure.
                READ-ONLY is literal: it must never be edited, per §2's UNMODIFIED rule.)
- optimizer instructions: author .capevolve/project/optimizer/INSTRUCTIONS.md from the scaffolded
                template (keep its {{...}} placeholders intact). Scope it to the SELECTED
                capability: tools ONLY ⇒ no prompt-editing guidance, no system-prompt skill, and
                the policy is NOT presented as editable. Keep it short on meta-narration but
                DEMANDING on iteration depth: each iteration diagnoses EVERY failure cluster in
                the trajectories and ships a fix for as many as pass REAL/SAFE/VERIFIED in ONE
                candidate. A one- or two-edit iteration is under-used.
- what to edit, per ./guidance/tools/SKILL.md: prefer CODE-BEARING changes to the skill's
                scripts — a validation wrapper that enforces a rule in code before calling the
                primitive; a workflow tool that collapses a recurring sequence; a composite WRITE
                tool that performs a stalled multi-step action in code so the agent can't
                analyze, confirm, then fail to execute. Improve tool docs AND RETURN VALUES
                (actionable errors + next steps) — the docstring and the return are what the
                agent sees. Never bare-remove a tool; add a replacement that calls it, verify,
                then swap.
- NON-OVERFITTING guardrail: every edit must be a GENERAL rule/validation that generalizes across
                the class of inputs. NEVER hardcode a task-specific id/value/date/name/answer (a
                guard fires on the general condition, e.g. "payment_id not on the user's
                profile", NOT `if reservation_id == "ABC123"`). A literal special-case overfits,
                fails the held-out gate, and hurts other tasks.
- EXPLOIT GROUND TRUTH for diagnosis: the native trajectories include reward_info with the
                per-check breakdown; USE it to localize the exact defect, but keep the resulting
                edit GENERAL and never copy a gold value into tool code.
- cross-iteration contract: READ ./LEDGER.md + the whole ./JOURNAL.md + ./RUNMAP.md (and the
                ./prior_iterations/ entries for clusters you'll touch) FIRST; each iteration FILL
                ./PROCESS.md and APPEND to ./JOURNAL.md.

# 6. BUDGET / GATE
- algorithm:        hill-climb  (--focus all)
- max_iterations:   10          num_trials: 10
- per-iteration optimizer $ cap:  optimizer_usd_per_iter 40
- optimizer_max_turns: 400      (generous; the $ cap is the real per-iteration ceiling)
- max_usd: 400      max_optimizer_usd: 400   (NOTE: the runner side is unmetered here — see §3)
- gate:             paired (per-task paired SE — banks real 1-task gains), k_se 0.2
- store:            git          (every iteration committed for an inspectable process)
- ALSO author a cheap SMOKE spec beside the full one (capevolve.smoke.yaml + its own pinned
                split): 2 tasks, num_trials 1, max_iterations 1, max_usd ~10. It exists to prove
                the WHOLE loop end to end over the SAME stack — provision, deploy the skill,
                rebind the proxy, rollout, score, gate, sealed test, report — for a couple of
                dollars, before anyone spends the full budget. Keep every other key identical to
                the full spec (same intervention, same skill_name, same protected_paths) so the
                smoke exercises the same delivery; only the SCALE differs. setup.sh must DEPLOY
                both specs and both splits, and run.sh's smoke flag must resolve to the smoke
                spec — VERIFY that, because a flag that silently falls back to the full spec turns
                a $10 check into a $400 run and nothing in the output says so.
- ONE ARM PER RUN. The spec, the seed and the record must agree about how candidates were
                delivered, or the number cannot be attributed.
```

> The bundled `examples/tau2_airline/blackbox/` is the **result** of following this prompt: the
> adapter (`adapters/adapter.py`), the gateway shim (`adapters/gateway.py`), the outside-in
> tailoring (`adapters/tau2_tailoring.py`, §2c), the seed capability (`seed_capability/` —
> `my_skill/` plus the frozen `primitive_tools/`), and the optimizer instructions
> (`optimizer/INSTRUCTIONS.md`). Every asset name mirrors the direct arm one level up, so
> `diff ../capevolve.yaml capevolve.yaml` shows exactly the delivery delta and nothing else.
> `setup.sh` is the executable transcript of that onboarding; `run.sh` starts the stack and runs
> the optimization.
