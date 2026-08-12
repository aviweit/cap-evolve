# Optimize the capability — CREATE new composite skills (never edit the seed)

{{TARGET_READER}}

{{FOCUS_SUMMARY}}

{{EMPTY_SEED}}

## CRITICAL CONSTRAINT: CREATE-ONLY (read this before anything else)

You are optimizing a **skill-package** capability. The seed skill at
`./primitive_skill/` (and everything inside it) is **READ-ONLY**. You MUST NOT
edit, rename, delete, or overwrite any file inside `./primitive_skill/`.

Your ONLY allowed action is to **CREATE new sibling skill directories** alongside
`primitive_skill/`. Each new skill you create is a new directory at the same level:

```
seed_capability/
├── primitive_skill/       ← READ-ONLY, never touch
├── my_new_skill/          ← you CREATE this
│   ├── SKILL.md           ← the prompt enrichment SPA will inject
│   └── scripts/           ← optional: composite tool functions
│       └── ...
└── another_skill/         ← you can create multiple
    ├── SKILL.md
    └── scripts/
        └── ...
```

**What gets evaluated:** `adapter.apply()` uploads your new skill to the store and
restarts SPA with `SKILL_NAME=<your_skill_name>`. The agent then sees your skill's
SKILL.md as system prompt enrichment + any scripts as callable tools.

**What the SKILL.md does:** It is injected as system prompt enrichment by SPA. It
should guide the agent to orchestrate the 14 frozen primitive tools correctly.

**Composite-wrapper pattern (extending primitive tool logic):**
If a primitive tool needs additional logic (e.g. eligibility checking before
`cancel_reservation`), do NOT edit the primitive. Instead:
1. Create a new composite tool function in your skill's `scripts/` (e.g.
   `cancel_reservation_with_eligibility_check.py`) that implements the extra logic
   and delegates to the primitive tool internally.
2. In your SKILL.md, list ALL 14 primitive tools EXCEPT the one being wrapped, PLUS
   the new composite tool in its place.
3. The composite function is the only path — the agent sees it instead of the raw
   primitive.

**The 14 frozen primitive tools** (available via the store, called by scripts):
book_reservation, calculate, cancel_reservation, get_reservation_details,
get_user_details, list_all_airports, search_direct_flight, search_onestop_flight,
send_certificate, update_reservation_baggages, update_reservation_flights,
update_reservation_passengers, get_flight_status, transfer_to_human_agents.

## GOAL

Raise the eval score as much as you can THIS iteration by creating new composite
skills, then STOP (the harness re-scores you — don't run evaluation yourself).

Diagnose EVERY failure cluster in `./trajectories/` and create a skill that fixes
as many as possible. The more distinct failing clusters your new skill addresses,
the larger the gain.

The ONLY brake is regression: every design choice in the new skill must pass the
three tests below. A single speculative choice that breaks a passing task can sink
the whole candidate.

## The THREE TESTS every design choice must pass
Before you keep any decision in your new skill, confirm all three:
1. **REAL** — it targets a cluster that is FAILING in THIS iteration's
   `./trajectories/` (reward 0, partial-credit, or communication/omission). Never
   design for a hypothetical problem.
2. **SAFE (bounded blast radius)** — would this change what the agent DOES on ANY
   currently-passing task? A composite tool that wraps a primitive is BOUNDED if it
   only adds checks/logic for the failing condition and delegates normally otherwise.
   A broad prompt rule that alters behavior across ALL tasks is UNBOUNDED — avoid it.
3. **VERIFIED** — you have shown it actually fixes its target (see VERIFY-THE-FIX).

## Read these first (everything is in this working directory)
- **`./guidance/<cap>/SKILL.md` for EACH selected capability — READ IT IN FULL.**
- `./guidance/diagnose/SKILL.md` — the failure-clustering method. Use it.
- `./trajectories/` — the FULL traces of the current best candidate. The
  `{{FAILURES}}` block below summarizes them — read the actual traces for clusters.
- `./primitive_skill/` — the seed skill (READ-ONLY reference). Read its SKILL.md
  and scripts to understand the current tool signatures and behavior.
- `./LEDGER.md` — FACTS (read-only): every prior iteration's outcome + exact tasks
  it broke/fixed. Never re-introduce a design that broke a task.
- `./JOURNAL.md` — the accumulating handover. Read all RESULT lines before
  proposing. APPEND your new entry below the marker; never edit earlier entries.
- `./RUNMAP.md` + `./prior_iterations/<id>/` — prior iteration PROCESS + diffs.
- `./PROCESS.md` — your REQUIRED explainability file for THIS iteration.
- `./guidance/optimizer/<name>.md` — your agent's subagent/parallelism features.
{{BENCH_REPO}}

## Process (do this, then STOP)
**Parallelism:** {{PARALLEL_NOTE}}
1. Read the primitive_skill (signatures, behavior), capability SKILL(s), diagnose
   method, and cross-iteration files (LEDGER, JOURNAL, RUNMAP).
2. Diagnose THIS iteration's `./trajectories/` ONLY. Cluster ALL failures by shared
   root cause. RANK by LEVERAGE = (# failing tasks × trials × score recoverable).
3. Design your new composite skill:
   - Name the new skill directory (e.g. `airline_policy_skill/`)
   - Write a SKILL.md that provides the prompt enrichment to fix the clusters
   - For behavioral/rule-violation clusters: create composite tool scripts that
     enforce the rules in code (wrapping the primitives)
   - For knowledge gaps: add the missing facts/formats to the SKILL.md prose
4. Run each design choice through the THREE TESTS; drop any that fails.
5. Write the new skill directory with all its files.
6. Fill `PROCESS.md` and APPEND your entry to `JOURNAL.md`. STOP.

## Choosing your approach by FAILURE TYPE

- **RULE VIOLATION** — the agent breaks a rule it could follow. **Best lever: a
  composite tool** that wraps the primitive, enforces the rule in code (validation /
  guard / computation), and delegates to the primitive only when the check passes.
  The agent sees ONLY the composite tool — it cannot bypass the guard. Complement
  with a SKILL.md note stating the rule (knowledge reinforcement).
- **CAPABILITY GAP / ACTION STALL** — the agent has no reliable way to do the
  thing. **Create a composite tool** that performs the whole multi-step action via
  primitives in its body (e.g. check-then-cancel, loop-search-then-book). The agent
  calls ONE tool that does it all correctly.
- **KNOWLEDGE GAP** — a format/criterion/fact the agent cannot derive. Add it to
  the SKILL.md prose (explicit format, worked example, decision rule).
- **DECISION / PERMISSION** — the agent acts when it should refuse (or vice versa).
  Encode the discriminating CONDITION in a composite tool guard — refuse/raise ONLY
  when the predicate fails. Never loosen a global rule in prose.

## VERIFY-THE-FIX
- **Composite tool guard:** mentally trace the tool body on the EXACT args from the
  failing trace — confirm it fires/returns correctly. Then trace it on args from 1–2
  PASSING tasks — confirm it does NOT fire (bounded).
- **New composite tool:** construct the inputs the agent SHOULD pass (from the
  trace's observed state) and confirm the body completes the action end-to-end.
- **SKILL.md prose:** confirm the missing fact is stated, general, and unambiguous.

Record one line per choice in PROCESS.md.

## NON-OVERFITTING
Every choice encodes a GENERAL rule — NEVER a literal that special-cases one task
(its id, target, name, or expected answer). A composite tool guard fires on the
general condition, NOT `if reservation_id == "SPECIFIC_ID"`. ALLOWED: constants the
domain defines (policy dates, fixed fees, domain enums).

## Handover (REQUIRED before you STOP)
- **PROCESS.md**: ranked clusters, every design choice + its lever, VERIFY line per
  choice, what you skipped and why.
- **JOURNAL.md** (append ONE entry): the skill you created · expected effect + why
  safe · prior RESULTS you built on · refuted ideas you avoided · focus next iter.

{{FAILURES}}
{{PASSING}}
{{ALGO_BRIEF}}

## Self-check before STOP
- You did NOT edit or delete anything inside `./primitive_skill/`. All your work is
  in a NEW sibling directory you created.
- Your new skill directory has a valid SKILL.md (with frontmatter: name, description).
- Every design choice passes the THREE TESTS (REAL, SAFE, VERIFIED) with a verify
  line in PROCESS.md.
- For rule-violation / behavioral clusters you created composite tool scripts (code
  enforcement), not just prose in SKILL.md.
- No choice hardcodes a task-specific id/value/date/answer.
- PROCESS.md + JOURNAL.md are filled.
