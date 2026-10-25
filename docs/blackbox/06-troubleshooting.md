# Troubleshooting

Check `rollout.error` in `rollouts/val/*__seed__t0.json` first. The service logs are
`/tmp/env_manager.log`, `/tmp/skillberry-store.log`, `/tmp/skillberry-agent.log` and
`/tmp/tools-agent.log`.

| Symptom | Cause | Fix |
|---|---|---|
| `intervention preflight failed: … is not provisioned` | the store or proxy was never cloned and installed | run `setup.sh`; a run never provisions |
| `intervention preflight failed: … needs skill_name` | the proxy is stopped and the spec has no `skill_name:` | set `skill_name: my_skill` |
| `port 7000 is held by …, which is not SPA` | another process holds the port (on macOS, AirPlay holds 7000) | free the port |
| every rollout errors with `deploy failed: …` | the adapter's `apply()` could not deploy the candidate | read the message; it names the cause |
| `SeamError: tau2 is at …, but this snapshot was created from TAU2_REFERENCE_SHA …` | `vendor/tau2-bench` is not at the commit the adapter was built from | run `setup.sh`, which checks that commit out |
| every reward is 0 within seconds, with `rollout.error` set | the environment service is down, or a wiring error | read `rollout.error` and `/tmp/env_manager.log` |
| tool calls fail with `no env_id in the execution context` | the context headers are missing | the runner must send `skillberry-context-*` headers |
| the agent behaves like a different model | `SPA_MODEL_NAME` is not set, so the proxy uses its default | set `SPA_MODEL_NAME` in `.env` before the store starts |
| a tool's schema has no field names; the model guesses them | a delivery rule was broken: class in another file, multi-line `def`, or a self-referencing model | see [the delivery rules](04-the-candidate.md#what-the-optimizer-edits-and-the-delivery-rules) |
| more `env_id`s than trial attempts, or some still open | a non-trial environment construction was armed, or the end-of-trial stop did not run | see [one trial's session](02-architecture.md#one-trials-session) |
| the project behaves like an older version of a skill | `CAPEVOLVE_SKILLS_DIR` is not set, so the skills come from elsewhere | `run.sh` exports it; set it yourself when calling `cap-evolve` directly |

## db_match false on every trial

The tools' writes returned success, but the scorer saw none of them. The environment service's
trajectory was not merged before evaluation. See
[the two merge windows](02-architecture.md#one-trials-session).

## Services already running from an earlier run

The preflight checks that the store and proxy are **healthy**. It does not check what they
serve, or whether the benchmark's adapter uses them.

- If they are still up from an earlier run, preflight passes and prints
  `intervention: blackbox`, even for a benchmark that cannot do blackbox.
- The proxy then keeps serving the previous run's skill.

Stop the stack between benchmarks: `blackbox_env.stop_all()` and
`pkill -f tau2.orchestrator.environment_manager`.
