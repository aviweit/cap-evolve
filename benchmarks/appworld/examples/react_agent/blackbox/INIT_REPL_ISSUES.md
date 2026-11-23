# The tools capability through a model-called `init_repl`: issues found

This note records what went wrong when the AppWorld blackbox approach delivered the tools capability
(`repl/helpers.py` and `repl/api_notes.json`) through a store tool, `init_repl`, that the model calls.
It covers the design problems and what the runs on the 10 fixed tasks showed (2026-11-22/23). The
decision it led to is at the end.

## What was built

- **The remote REPL.** Each trial gets its own AppWorld environment server (`adapters/aw_env_server.py`,
  AppWorld's own `appworld.serve.environment.app`), and the stock agent's world attaches to it with
  `AppWorld(..., remote_environment_url=...)`. The adapter manages the ports; up to 24 run in parallel.
- **The port reaches the store.** The adapter sends `skillberry-context-appworld-repl_port` with every
  model call. The proxy forwards the whole Skillberry context to the store, and the store (branch
  `fix/vmcp-rich-schema-and-quotes-context-globals`) hands it to every tool as the global
  `skillberry_context`. Before that change the store passed `env_id` only.
- **The tool.** At deploy time, the candidate's `repl/` files become the direct approach's REPL code
  (`aw_agent.setup_snippet`: the helpers, plus two wrappers around `apis.api_docs.show_api_doc` and
  `show_api_descriptions` that add a `note` field). That code is written into the frozen template
  `seed_capability/my_skill/scripts/init_repl.py`. When the model calls `init_repl()`, the store POSTs
  the code to the trial's `/execute`.
- **The announcement.** The deployed `SKILL.md` (the system prompt) ends with the direct approach's
  generated sentence listing the helpers, followed by "Call the init_repl tool once, ...".

**What worked:**
- the port propagation: one port per episode's `env_id`, unchanged through proxy and store;
- the load itself: helpers defined and notes active in the right trial's world;
- idempotency: the code puts the original doc lookups back before re-wrapping them, so it can run any
  number of times.

## Design problems

1. **Setup depends on the model.** Loading the REPL is setup: the direct approach runs it exactly once,
   before step 1. As a model-called tool, whether and how often it runs depends on the model's choices.
   It isn't deterministic, so two candidates with the same `repl/` can see different worlds.
2. **The model can't see its earlier tool calls.** The proxy runs the tool loop inside one request and
   returns only the model's final reply. The next request carries the client's own history, without the
   tool call. So the model can't tell the REPL is already loaded, and every request looks like "the start
   of the task".
3. **A setup tool has no natural end.** `init_repl()` takes no arguments, returns the same text every
   time, and never moves the task forward; AppWorld tasks are solved by writing code. In tau2, every tool
   call has different arguments and returns new data, and the conversation ends with a message to the
   user. Here, nothing about the task tells the model to stop calling.
4. **The few-shot demo has no tool calls, so the model may get confused.** The stock prompt's demo, the
   body of `SKILL.md`, is code-only: every ASSISTANT turn is one sentence plus one ```python block, and
   every USER turn is the REPL's `Output:`. It never shows a tool call or a tool result.
   - **A second way to act:** with a tool bound (function calling, `tool_choice="auto"`), the model has
     a way of acting the demo never shows. It must decide, unaided, when to call a tool and when to
     write code.
   - **The demo can't fix that:** it arrives as text inside one system message, not as real chat turns.
     An optimizer could describe a tool call in it, but not show one in the format the model would use
     (`tool_calls`, `role: tool`).
   - **Proxy tools would make it worse:** the same holds, more strongly, for doc lookups as proxy tools
     (problem 6). They would overlap with what the demo teaches in code.
   - **The runs fit this, though only as a correlation:** once a tool was bound, the model both looped on
     it and more often returned turns with neither text nor a call (What the runs showed, 2 and 3). The
     seed, with no tool, did neither.
5. **Helpers need an announcement the model may ignore.** The sentence listing the helpers sits at the
   end of a long system prompt, after the whole demo. Nothing forces the model to use the helpers, or to
   load them first.
6. **Doc lookups as proxy tools (an alternative we rejected).** We also considered making
   `show_api_doc` / `show_api_descriptions` store tools that the model calls through the proxy, with no
   REPL change. Three reasons against it:
   - **the result never enters the conversation history** (problem 2), so a note seen once is gone at the
     next step. In the direct approach the doc output, note included, stays in the history;
   - **it depends on the model choosing the tool** over its usual in-code lookup;
   - **it needs a copy of the original docs,** or another store path into the trial's environment server.
7. **The store can reach `/evaluate`.** A store script that knows the port can call any route of the
   trial's environment server, `/evaluate` included. Today only the frozen loader calls it, and only
   `/execute`; nothing enforces that.

## What the runs showed

All runs: the blackbox approach, `capevolve.fix10.yaml` (10 fixed tasks, 5 trials), gpt-oss-120b, run
as a user would (`setup.sh --blackbox`, `run.sh --blackbox --fix10`).

### 1. The proxy rejected every call to a tool without arguments

- **Symptom:** the store showed `init_repl` offered on 1,576 requests and executed 0 times. In
  `cand_0001`, 2,350 of 2,820 agent turns (**83%**) were the proxy's fallback text, "I apologize, but
  I'm experiencing a technical difficulty...".
- **Cause:** `mcp_interceptor._extract_mcp_request` asserted `request.args`, which is falsy for `{}`.
  The proxy's catch-all (`agentic_graph.py`, "Error while streaming to the react agent") turned the
  failure into the apology, so it was silent: logged at ERROR, a normal-looking reply for the client.
- **Scope:** every zero-parameter skill tool, including tau2's `list_all_airports()`.
- **Fix:** `assert request.args is not None`, committed to aviweit/skillberry-agent as `28f5db8`, and
  `AGENT_REF` moved to it.
- **Run:** `run_20261122_175759`, stopped; its `cand_0001` score is void.

### 2. The model loops on `init_repl` inside one request

- **Symptom:** after the fix, the tool ran and returned "REPL ready", but gpt-oss called it again and
  again within one proxy request, until the proxy's recursion limit of 20 steps. That request's reply
  then became the apology. Every episode ran the tool 2–13 times.
- **Measured** (`run_20261123_094010`): `cand_0001` lost **14.4%** of its turns (75 of 521) to the
  apology, in 21 of 22 episodes. The seed, with no tool, lost 0.3%.
- **Mitigation, benchmark-side:**
  - every answer of the tool now ends with "Do not call init_repl again in this task. Reply now with
    your one ```python code block.";
  - a repeat answers "Already loaded, nothing changed" (the REPL code counts its loads);
  - the system-prompt line became "once, at the very start of the task; never call it again".
- **Result** (`run_20261123_095212`): `cand_0001` still lost **8.4%** of its turns (76 of 903), in 23 of
  47 episodes, with 1–8 calls per episode. The tool's answers were right (30 "REPL ready", 126 "Already
  loaded"), and the model kept calling anyway.
- **Score:** `cand_0001` scored **0.26** against the seed's **0.48** and was rejected. Part of that drop
  is the lost turns, so the number says little about the candidate.

### 3. Empty replies once a tool is bound

- **Symptom:** "No content or tool calls found in response": the model returns neither text nor a tool
  call, and the catch-all turns it into the apology.
- **Measured** (`run_20261123_095212`): 0 on the seed, 22 rising to 67 once `init_repl` was offered.
- This also happens without tools. tau2 SPA runs from 2026-09 show the apology in 11 of 20 and 2 of 5
  simulations, mostly right after a tool returned `[]`. It is a proxy behaviour in general, not only
  `init_repl`'s, but offering a tool here made it more frequent.

### 4. Things that were not problems

- **The port propagation:** every `env_id` kept its own port. 24 environment servers ran in parallel,
  and none were left after the runs or after a worker was killed.
- **The deployed system prompt** ended with the generated helper sentence as designed.
- **The frozen `init_repl.py`** was never edited by the optimizer: the lint and `protected_paths`
  passed.

## Decision (2026-11-23)

Work in steps, notes first. The helpers (`repl/helpers.py`) and the model-called `init_repl` are parked
on a separate branch.

**For the notes:**
- keep the two wrappers in the REPL, as the direct approach does, so a note appears in the doc lookup's
  `Output:` and stays in the history;
- load them **deterministically**: the episode worker asks the store to run its frozen loader once, with
  the trial's `skillberry-context-*` headers, before step 1;
- don't offer the loader to the model, so the model stays in the code-only mode the demo teaches.

**What the benchmark side does and doesn't do:**
- it doesn't change the benchmark: AppWorld's stock `SimplifiedReActCodeAgent`, its prompt and loop are
  untouched;
- the REPL is AppWorld's own supported remote environment;
- the worker never reads the candidate's files: it triggers the load, and the store delivers the
  candidate;
- the REPL changes exactly as in the direct approach, and the app databases, which are what gets
  scored, are not touched.

**Still open:**
- keeping store scripts away from `/evaluate`;
- the proxy's catch-all, which hides empty replies and loops behind a polite apology;
- the empty replies themselves.
