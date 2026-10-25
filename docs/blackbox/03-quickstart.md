# Quickstart

All commands run from the cap-evolve repo root.

## 0. Before you start

- **Tools:** `git`, `uv`, `curl` and `make`. `setup.sh` creates a Python 3.12 venv.
- **A repo-root `.env`**, with these names. Values come from your gateway; the shell wins over
  `.env`.

  | Variable | What it is |
  |---|---|
  | `OPENAI_BASE_URL`, `OPENAI_API_KEY` | the OpenAI-compatible gateway the agent's model and the user simulator run on |
  | `SPA_MODEL_NAME` | the model the proxy calls upstream, as `openai/<model>`, e.g. `openai/aws/claude-haiku-4-5` |
  | `TAU2_USER_MODEL` | the user simulator's model |
  | `TAU2_AGENT_MODEL` | optional; the agent's gateway model. Without it, the adapter derives it from `SPA_MODEL_NAME` |

  Put `SPA_MODEL_NAME` in `.env` **before** the store starts: the store copies its environment
  once, when it starts.
- **The optimizer** authenticates on its own: a logged-in Claude Code session, or
  `ANTHROPIC_API_KEY`.

## 1. Setup

```bash
bash examples/tau2_sierra_airline/blackbox/setup.sh            # add --no-dashboard to skip the dashboard
```

It prints six numbered steps and ends with `READY.`:

1. tools + venv;
2. clone tau2 into `vendor/tau2-bench` **at `TAU2_REFERENCE_SHA`**;
3. install cap-evolve and tau2 into the venv; check 50 airline tasks load;
4. install the snapshot (adapters, seed, optimizer instructions, specs, splits) as the project
   in `.capevolve-blackbox/project`;
5. provision the Skillberry stack: clone and install the store and the proxy under `vendor/`.
   This only provisions; nothing is started;
6. the hard gate, `cap-evolve check`.

The script doesn't stop on every error, so check the end of the output: `READY.`, and no
`SETUP FAILED:` line.

## 2. Smoke run (task 9, 1 trial, no optimizer spend)

```bash
bash examples/tau2_sierra_airline/blackbox/run.sh --smoke
```

`run.sh` does three steps:
1. it starts tau2's environment service on `:8004`, logging to `/tmp/env_manager.log`;
2. it starts the store, imports the 14 frozen primitive tools and deploys the seed skill. It
   must print `primitives in store: 14`;
3. it runs `cap-evolve run` with `capevolve.smoke.yaml`. The preflight starts the proxy and
   prints:

```
intervention: blackbox | store :8000 | proxy :7000 | skill=my_skill | env=http://127.0.0.1:8004
```

`skill=` comes from the spec's `skill_name:`.

The smoke uses the mock optimizer, which makes no edits. A good smoke gives task-9 rollouts with
`error` null and a real reward. Task 9 is a pipeline check, not a measurement.

## 3. The full run

```bash
bash examples/tau2_sierra_airline/blackbox/run.sh
```

It uses `capevolve.yaml`:
- the 7 budget tasks of `split_ids_budget.json`, 5 trials each;
- agent `aws/claude-haiku-4-5`;
- optimizer Claude Code `claude-opus-5-5`, hill-climb, 3 iterations.

The run lands in `.capevolve-blackbox/run_<ts>/`, and the banner prints the dashboard URL.

## 4. Stop the stack

`run.sh` leaves the services running and prints the stop commands: `blackbox_env.stop_all()`
for the store and proxy, and `pkill -f tau2.orchestrator.environment_manager` for the
environment service. Stop them before you run another benchmark: see
[Troubleshooting](06-troubleshooting.md#services-already-running-from-an-earlier-run).
