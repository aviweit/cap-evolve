# appworld blackbox: the system prompt and REPL tools, delivered by the Skillberry Store + Proxy-Agent

The same AppWorld agent and data as the direct example in [`..`](../README.md), but the capability under
optimization never reaches AppWorld's side. It lives in the Skillberry Store as a skill, and the
Skillberry Proxy-Agent (SPA) injects it into every LLM call.

| | direct (`..`) | blackbox (this folder) |
|---|---|---|
| Agent under test | AppWorld's simplified ReAct code agent, subclassed (`CapReActAgent`) | AppWorld's simplified ReAct code agent, **stock** |
| Prompt the client sends | the candidate's `policy/instructions.txt` | AppWorld's own stock `instructions.txt`, unchanged |
| Capabilities | `[system-prompt, tools]` (prompt, REPL helpers, API notes) | `[system-prompt, tools]`: the store skill's `SKILL.md`, `repl/helpers.py`, `repl/api_notes.json` |
| Where the candidate goes | rendered by the harness, in-process | the Skillberry Store; the proxy injects `SKILL.md` as the system prompt and offers the store tool `init_repl`, which loads `repl/` into the trial's REPL |
| The REPL | in the episode's process | the trial's own AppWorld environment server (`AW_REMOTE_REPL`, default on) |
| Data, splits, scoring, lints for task-specific hints, optimizer isolation | shared | shared (reused from `..`) |

There is no PROMPT.md: the approach is built from the direct one, not generated.

## Layout

```
adapters/adapter.py              the cap-evolve adapter: data/seal/score as in the direct example (..); run_trials lints, deploys, runs episodes
adapters/appworld_tailoring.py   context headers, the history-start marker, SPA's upstream model from AW_*, deploy(),
                                 the env-server ports, and repl/ -> the init_repl tool (build_init_repl)
adapters/aw_env_server.py        one AppWorld environment server (the remote REPL) for one trial
adapters/aw_bb_agent.py          one episode: AppWorld's stock agent, LLM calls to the proxy with the headers
adapters/bb_lints.py             SKILL.md and repl/ lints (reuses ../adapters/lints.py)
optimizer/INSTRUCTIONS.template.md   the optimizer's instructions (SKILL.md + repl/ edit surface)
seed_capability/my_skill/        the committed seed skill: SKILL.md (app list a {{ app_descriptions }} placeholder),
                                 repl/helpers.py + repl/api_notes.json (empty), scripts/init_repl.py (FROZEN: the store
                                 tool's template, in protected_paths; deploy fills in the candidate's repl/ code)
scripts/make_seed_skill.py       renders the committed seed into the project (fills the app list, which comes from
                                 AppWorld's installed app code and is never committed); --build rewrites the seed
scripts/provision_stack.py       store + proxy at the pinned commits in the shared vendor/ (never deletes; a stale clone is an error)
scripts/setup_project.sh         scaffold the project, generate the seed, provision, run cap-evolve check
scripts/launch.sh                run (or resume) with the optimizer isolated; AW_SPEC=capevolve.smoke.yaml for a smoke
capevolve.yaml, capevolve.smoke.yaml, smoke_split.json   the run spec (search settings = the direct example, ..) and a 1-task smoke
tests/test_blackbox.py           offline tests (no AppWorld, store, proxy or model)
```

The project also gets `../adapters/aw_agent.py` and `lints.py` (the harness: hardened code
guard, gold-free records, error redaction, worker-environment allowlist, the 8-gram and
supervisor-contact checks) and uses `../sandbox` and `../scripts` for the
optimizer's isolation, splits and roots.

## How a call flows

```
AppWorld's stock ReAct agent (one process per episode, aw_bb_agent.py)
  sends: its 23 stock prompt messages + the episode so far, model openai/ibm/skillberry-local
  headers: skillberry-context-env_id (per episode), -task_id, -plugin: appworld,
           -history_start_regex: ^\s*Using these APIs, now generate code to solve the actual task:
           -template-main_user-{first_name,last_name,email,phone_number} (the task's supervisor)
           -appworld-repl_port (the trial's environment server)
        │
        ▼
Skillberry Proxy-Agent (pinned in skills/interventions/llm-proxies/blackbox/scripts/blackbox_env.py)
  1. drops the client's messages before the real task turn (the marker)
  2. fetches my_skill's prompt (SKILL.md) from the store and renders it with the main_user values
  3. sends to SPA_MODEL_NAME: [system: rendered SKILL.md] + the real task turn + the episode so far,
     with the skill's tools bound: init_repl, when the candidate's repl/ loads anything
  4. if the model calls init_repl, the store runs it with every skillberry-context-* header as the
     global skillberry_context: it POSTs the repl/ code to that port's /execute (only /execute)
     for the task, then the model writes its code; only that final reply reaches the client
        │
        ▼
AppWorld's stock agent runs the reply's code in its world, on the same environment server, so the
code can call the helpers. The tool call itself never reaches the client's history.
```

The repl/ code is the direct approach's own (`aw_agent.setup_snippet`), so both approaches load
helpers and notes identically. Before re-wrapping the doc lookups it puts the originals back, so
calling `init_repl` again, however often, changes nothing; the model cannot see its earlier tool
calls, so a weak one may call it on every step. The code also counts its loads, and every answer of
the tool ends the tool step ("Do not call init_repl again in this task. Reply now with your one
```python code block."), a repeat saying "Already loaded, nothing changed": without that, gpt-oss
called it again and again inside one request until the proxy's 20-step recursion limit, and the
client got the proxy's fallback apology instead of code (fix10, 2026-11-23).

The model is told about the helpers as in the direct approach: the SKILL.md deployed to the store
ends with direct's generated sentence (`aw_agent.helper_notice`: "Pre-defined helper functions are
available in the REPL (call them directly; see their docstrings with help(name)): name(a, b), …")
followed by "Call the init_repl tool once, at the very start of the task, to load them; never call
it again after that." (`appworld_tailoring.deployed_skill_md`). Direct puts its sentence just
before the final task block; the deployed SKILL.md ends where the client's final task turn
begins, so it is the same place, inside the system prompt. The seed loads nothing and gets no
sentence.

The proxy's `history_start_regex` and `template` hooks are opt-in per request: a client that sends
neither header gets the proxy's usual behaviour.

## Seed

The seed is committed in `seed_capability/my_skill/`, as tau2's seeds are; the spec's
`capability_path` is `seed_capability`, so a candidate is the folder holding `my_skill/`.

`SKILL.md` is AppWorld's stock prompt from its first line through the `**Key instructions**` turn,
byte for byte, plus frontmatter (`name: my_skill`) and a short header saying the USER/ASSISTANT
turns are a worked few-shot example. The final turn ("Using these APIs, now generate code to solve
the actual task: … Task: {{ instruction }}") is not in it: the client sends it, and the proxy keeps
it. `{{ app_descriptions }}` stays a placeholder in the committed file, as in the direct seed,
and is rendered into the project's copy at setup, exactly as AppWorld's agent renders it
(identical for every task); the `{{ main_user.* }}` slots stay for the proxy.

`repl/helpers.py` (a docstring) and `repl/api_notes.json` (`{}`) are empty, as the direct seed's
`tools/` is: they load nothing, so no store tool is deployed and the seed is the stock agent.

## Models

The proxy's upstream model comes from the same variables the direct approach uses
(`../adapters/aw_agent.py`):

| Variable | Meaning in this approach |
|---|---|
| `AW_MODEL_NAME` | the model; an OpenAI-compatible provider prefix (`hosted_vllm/`, `litellm_proxy/`) becomes `openai/` for SPA's litellm. `AW_SPA_MODEL` overrides. |
| `AW_MODEL_URL` | the endpoint (`{{MODEL_SERVER_URL}}` filled from `MODEL_SERVER_URL` / `RITS_URL`) |
| `AW_KEY_HEADER` | must be `bearer`: the proxy sends a standard `Authorization: Bearer` key, so RITS's custom `RITS_API_KEY` header cannot be served |
| `AW_KEY_ENV`, `AW_SECRETS_FILE` | the key's variable name, read from the secrets file (only that line); handed to the proxy, never to the episode worker |

## Lints

`adapters/bb_lints.py` rejects a candidate before any episode runs:
- the skill folder holds `SKILL.md`, `repl/helpers.py`, `repl/api_notes.json` and the frozen
  `scripts/init_repl.py` only (any other file would be injected or become a tool), with frontmatter
  `name: my_skill`;
- `scripts/init_repl.py` is byte-identical to the benchmark's copy (it is also in `protected_paths`,
  so cap-evolve flags an edit as tampering; the deployed tool is built from the benchmark's copy);
- `repl/helpers.py` and `repl/api_notes.json` follow the direct approach's rules for `tools/`;
- the only placeholders are the four `{{ main_user.* }}` fields, each kept at least once; no other
  `{{ }}`, no `{% %}` block, no filter, no `{{ instruction }}`;
- the rendered app list stays verbatim (in the direct approach the optimizer only sees its
  placeholder);
- the demo keeps its `USER:` / `ASSISTANT:` turns; at most 8,500 tokens;
- no 8-gram from a train/dev instruction and no supervisor contact (the direct approach's check).

## Setup

**One command:** `bash ../setup.sh --blackbox` does both steps below, and skips what is already in
place, so it is safe to re-run.

1. The direct approach's Setup steps 1-2 (`../README.md`): the patched AppWorld and
   `$AW_EVAL/.venv` (or `AW_VENV`), `root_full`, `root_opt`, the lint cache and the reference docs.
2. `bash scripts/setup_project.sh` (base folder `$AW_EVAL/.capevolve-blackbox` by default): scaffolds the project, writes the
   seed `SKILL.md` and `$AW_EVAL/app_descriptions.json`, provisions the store and proxy at the
   pinned commits in the repo's shared `vendor/`, and runs
   `cap-evolve check`. A clone there at another commit stops setup: delete it
   (e.g. `rm -rf vendor/skillberry-agent`) and run setup again to clone the pin.

## Running

```
export AW_EVAL=... AW_MODEL_NAME=... AW_MODEL_URL=... AW_KEY_HEADER=bearer AW_KEY_ENV=...   # README: Models
bash ../scripts/check_optimizer_isolation.sh sandbox       # must PASS (as in the direct approach)
AW_SPEC=capevolve.smoke.yaml bash scripts/launch.sh   # 1 task, 1 trial, 1 iteration
bash scripts/launch.sh                                # the full search
AW_RUN_TS=20261118_171500 bash scripts/launch.sh "" --resume   # resume that run in place
```

One base folder (default `$AW_EVAL/.capevolve-blackbox`) holds the project and every run: `project/` and one
`run_<YYYYMMDD_HHMMSS>` per launch, named by its start time. It must be under `$AW_EVAL`, because the
optimizer's isolation only binds a working dir inside it. Pass another base folder as the first
argument to keep a separate set.

Offline tests: `python -m pytest benchmarks/appworld/examples/react_agent/blackbox/tests`.

## Known gaps

- The demo arrives as text inside one system message, not as real chat turns. The model can then
  continue it as a transcript (invented `USER: Output:` turns in one reply). The proxy drops
  `stop`, so a stop sequence cannot prevent it today; the optimizer instructions name the failure
  mode so it can be fixed in the prompt.
- No final-window script yet: `../scripts/final_window.py` runs the direct approach.
- `init_repl` reads the trial's port from `skillberry_context`, which only a store that passes the
  full context to its tools provides. With an older store the tool answers "no REPL is attached",
  and helper calls fail with `NameError`; the store pin must move to such a commit.
