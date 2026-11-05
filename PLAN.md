# Blog rewrite plan (draft for review)

Nothing in `site/index.html` changes until this plan is agreed.

**Runs used:** direct `run_20261013_094928`, blackbox `run_20261101_125955`. τ²-bench airline at
`4ce7c03`, 7 tasks × 5 trials, agent `claude-haiku-4-5`, user simulator `claude-sonnet-5`,
optimizer Claude Code `claude-opus-5-5`, hill-climb, 3 iterations.

**Not in the post:** the Store quote issue, the earlier blackbox run, any defect story.

---

## Structure

| ¶ | Role | Status |
|---|---|---|
| 1 | What exists now: most optimizers reach inside the agent; cap-evolve's direct approach | **kept** |
| 2 | Motivation: real agents are closed (enterprises) | **kept** |
| 3 | Blackbox to the rescue: high level + first diagram | **kept** |
| 4 | τ² results: direct and blackbox, very similar, incl. runtime and budget | new |
| 5 | How it's done on τ²: PROMPT.md → provisioning → tailoring → run, detailed diagram | new |
| 6 | One tool, end to end: a new tool added to the skill, and how it changes the agent | new |
| 7 | Taking the winner home: patching vanilla τ²-bench, with numbers | new |
| — | Limits, try it (short close) | new |

¶1–3 stay exactly as they are now, up to and including the first diagram and its caption.
Everything after it ("So here is the question…" to the end) is replaced.

---

## ¶4 — τ²-bench: direct and blackbox, side by side

Hero card first: **gain over the seed +0.600 (direct) vs +0.629 (blackbox)**.

| Sealed test, 7 tasks × 5 trials | Direct | Blackbox |
|---|---|---|
| Seed → best | 0.343 → 0.943 | 0.257 → 0.886 |
| Gain over the seed | +0.600 | +0.629 |
| ± standard error | 0.067 | 0.097 |
| Best candidate | `cand_0003` | `cand_0003` |
| Iterations accepted | 3 of 3 | 2 of 3 |

Then, briefly:
- the final gap, 0.057, is about half of one combined standard error;
- 5 of 7 tasks land on the same score; tasks 35 and 44 differ by one trial in five;
- the same kinds of errors: 50 of 140 val rollouts fail on both, same top categories;
- figures: climb per round, per-task bars (regenerated from these two runs).

**Runtime and budget.** Honest numbers, from the run records:

| | Direct | Blackbox |
|---|---|---|
| Wall clock | 130 min | 160 min |
| Benchmark time, 3 iterations | 42 min | 23 min |
| Baseline + sealed test | 27 min | 20 min |
| Optimizer time, 3 iterations | 24 min | 117 min |
| Optimizer spend | $6.07 (9.2M tokens) | $21.13 (29.3M tokens) |

⚠ **These are not "very similar".** The benchmark side is comparable (blackbox is even faster
here), but the optimizer spent about 3.5× more and took about 5× longer on blackbox. See
decision 1.

---

## ¶5 — How it's done on τ²-bench

**5.1 Onboarding with one prompt.** Wording from the Blackbox tab: a coding agent follows
`examples/tau2_sierra_airline/PROMPT.md`. Asked *how does the candidate reach the model?*, the
user answers **`blackbox`**. The agent then:
- installs τ²-bench unmodified, pinned to one commit;
- provisions the Skillberry Store and Proxy-Agent (clones both, at pinned commits, own venvs);
- seeds the capability from τ²'s own source: 14 frozen **primitives** (τ²'s real tool code,
  tagged and protected) and `store_skill`, one script per tool;
- writes the adapter and the tailoring module, from outside τ²;
- passes the `cap-evolve check` gate, then runs the loop.

**5.2 What the tailoring adds to τ²** (all from outside, in `adapters/tau2_tailoring.py`):

| Hook | What it does |
|---|---|
| a registered domain, `airline_skillberry` | τ²'s own registration API; τ²'s source is untouched |
| agent routing | τ²'s stock `llm_agent`, its model pointed at the Proxy-Agent |
| context headers | `skillberry-context-plugin`, `-env_id`, `-task_id` on every agent call |
| one environment per trial | started and stopped on τ²'s environment service (:8004) |
| trajectory merge, before scoring | the environment's record, so τ² scores the real DB writes |
| trajectory merge, after scoring | the Proxy-Agent's record, so the optimizer sees the skill's calls |
| session close | `disconnect` in a `finally`; the Proxy-Agent removes the trial's vMCP server |

**5.3 Detailed diagram (new).** An expanded version of the ¶3 diagram, in three lanes:

```
 ONBOARDING (once)                      ONE ITERATION                         ONE TRIAL (× tasks × trials)
 PROMPT.md ─→ answer "blackbox"         optimizer edits store_skill              τ² llm_agent ── sentinel model + headers ─→ Proxy-Agent :7000
   ─→ install τ² @ pinned SHA             ─→ deploy: reset Store to            Proxy-Agent ─→ vMCP server for this env_id (Store)
   ─→ provision Store + Proxy-Agent          store_skill, restart Proxy-Agent     Proxy-Agent ─→ real model (skill + tool schemas)
   ─→ seed 14 primitives + store_skill      ─→ τ² runs tasks × trials             tool call ─→ Store runs script ─→ primitives ─→ env :8004
   ─→ tailoring (hooks above)            ─→ score, gate keeps or drops         end: merge env traj → score → merge proxy traj
   ─→ cap-evolve check                   ─→ next edit                          finally: disconnect → vMCP server removed
```

Light theme, 1400+ px, same style as `fig0`.

---

## ¶6 — One tool, end to end

The tool: **`book_split_reservations`**, new in the winner `cand_0003`. Only task 23 uses it:
**0 of 5 trials at the seed, 5 of 5 with the winner**, on both test sessions.

Shown as a visual sequence (a figure, plus short excerpts under it):
1. **The failure.** Task 23 at the seed: the agent books several reservations and gets the
   order or the passengers wrong, so τ²'s database doesn't match.
2. **The edit.** The optimizer adds `store_skill/scripts/book_split_reservations.py`: its one-line
   signature with Pydantic models, its docstring, its calls to `env_book_reservation`.
3. **Into the singleton skill.** cap-evolve deploys `store_skill`; the Store registers the script
   as a tool and derives its JSON schema from the annotations.
4. **What the model sees.** The Proxy-Agent sends the model the tool's schema: name,
   description, nested properties, required fields.
5. **The call.** In a task-23 trial the model calls `book_split_reservations` once; the script
   runs in the Store and calls the primitive per reservation, in order.
6. **The effect.** τ²'s final database matches the expected one; the trial scores 1.0.

---

## ¶7 — Taking the winner home: patching vanilla τ²-bench

A procedure for the agent owner, with commands:
1. take the winner's skill: `run_…/candidates/cand_0003/store_skill/scripts/`;
2. clone τ²-bench at the same commit;
3. patch one file, `src/tau2/domains/airline/tools.py`, by four mechanical rules: τ²'s
   `AirlineTools` → `_AirlinePrimitives`, unchanged; each script → a method of a new
   `AirlineTools`, word for word; `env_<tool>(…)` → `self._prim("<tool>", …)`; keep the
   candidate's argument models, so the agent sees the same schemas;
4. check: the tools' schemas match the candidate's, and replaying each task's gold actions gives
   the same final database;
5. run the same `tau2 run` on the unpatched and the patched clone;
6. **numbers:** unpatched vs patched, per task, plus DB matches.

---

## Close — limits and try it

- no held-out split (train = val = test, 7 tasks): fit numbers;
- one benchmark, one run per approach;
- the agent's own token cost isn't measured through the proxy;
- try it: the example folders, `PROMPT.md`, the Blackbox tab, a star.

---

## Figures

| Figure | ¶ | Status |
|---|---|---|
| `fig0-blackbox-flow.png` | 3 | keep |
| climb per round, per-task bars | 4 | regenerate from these two runs |
| detailed end-to-end diagram | 5 | **new** |
| one tool, end to end | 6 | **new** |
| unpatched vs patched τ² | 7 | regenerate after the new export (decision 2) |

## Decisions needed

1. **Budget in ¶4.** Runtime on the benchmark side is similar, but optimizer spend is $21.13 vs
   $6.07. Options: (a) show both rows and say plainly that the optimizer cost more on this run;
   (b) show only benchmark time and the score. (a) is the honest one.
2. **The export in ¶7.** The earlier export patched a different candidate, from the earlier
   blackbox run. For honest numbers on *this* winner: one new `tau2 run` of 7 × 5 on the
   patched clone (gateway spend), plus recreating the patch generator, whose scripts are gone.
3. **¶6 figure:** a drawn sequence diagram (recommended), or a table of the six steps?
