# The blackbox arm

An **arm** is how a candidate reaches the model under test.

- **Direct** (the default): the benchmark's runner loads the candidate's files in its own process.
- **Blackbox** (`intervention: blackbox`): the candidate is deployed to the **Skillberry Store**,
  and the **Skillberry Proxy-Agent** (SPA) injects it into the agent's LLM calls.

  The benchmark never sees a skill file, a mounted directory, or an edited prompt. That is the
  shape in which Skillberry ships capabilities to a real agent.

These pages walk through optimizing a benchmark on the blackbox arm, step by step. The worked
example is τ²-bench airline on vanilla upstream tau2:
[`examples/tau2_sierra_airline/blackbox`](../../examples/tau2_sierra_airline/blackbox/).

Read them in order:

1. [Concepts](01-concepts.md): intervention vs capability, direct vs blackbox, and what a
   benchmark must provide.
2. [Architecture](02-architecture.md): the services, ports and headers, and one trial's
   session.
3. [Quickstart](03-quickstart.md): setup, smoke run, full run, stop.
4. [The candidate](04-the-candidate.md): what the optimizer edits and the delivery rules.
5. [Reading the results](05-results.md): dashboard, run directory, the blackbox checks.
6. [Troubleshooting](06-troubleshooting.md): symptom → cause → fix.

**Primary sources:**
- [`skills/interventions/llm-proxies/blackbox/SKILL.md`](../../skills/interventions/llm-proxies/blackbox/SKILL.md):
  the intervention;
- [`core/cap_evolve/intervention.py`](../../core/cap_evolve/intervention.py): the preflight;
- [`examples/tau2_sierra_airline/PROMPT.md`](../../examples/tau2_sierra_airline/PROMPT.md): the
  onboarding prompt that produced the example.
