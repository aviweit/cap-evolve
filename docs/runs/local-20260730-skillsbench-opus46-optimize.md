<!-- Local benchmark run detail page. Linked from https://skillberry-ai.github.io/cap-evolve/benchmarks.html -->

> **Local optimization run** (recorded on the benchmark-history branch).
> Ran outside the CI workflow (source: `bcarmeli`); optimizer proposed 3 candidates over the shared office-document skills (docx/pptx/xlsx/pdf), 1 accepted by paired gate.
> Benchmark: SkillsBench (all 87 tasks, fit metric — train==val==test); agent + optimizer: `claude-opus-4-6`.

> **Splice note:** the finalize steps baseline-test re-evaluation was broken by an overnight VPN drop; `test_baseline_reward` here is spliced from a properly-scored prior baseline run (fit-metric: test == val by construction). See PATCH_NOTE.md in the run dir for details.


# Run summary — `run_opus_optimize3`

- **Benchmark:** `skillsbench`
- **Agent under test:** `claude-opus-4-6`
- **Optimizer:** `claude-opus-4-6`
- **Tasks / trials:** 87 tasks · 1 trials
- **Iterations (actual / cap):** 3 / 3  ·  1 accepted
- **Split discipline:** fit-metric (train==val==test, no holdout)
- **Best candidate:** `cand_0001`

## Headline

| | baseline (seed) | best (`cand_0001`) |
|---|---|---|
| val_reward (mean) | 0.2809 ± 0.0475 | **0.3574 ± 0.0502** (35.7%) |
| pass_at_1 (fully passing) | — | **28/87** (—) |
| test_reward | 0.2809 | **0.3574** |
| test_delta (best − baseline) |  | **0.0765** |
| val wall-clock |  | 188m 52s |
| test wall-clock |  | 301m 5s |
| total wall-clock |  | 1272m 2s |

## Iterations

| iter | candidate | parent | val | Δ vs parent | accepted? |
|---|---|---|---|---|---|
| 1 | `cand_0001` | `seed` | 0.3574 | +0.0765 | ✓ |
| 2 | `cand_0002` | `cand_0001` | 0.3247 | -0.0327 | ✗ |
| 3 | `cand_0003` | `cand_0001` | 0.1703 | -0.1871 | ✗ |

## Passing tasks

- ✓ `3d-scan-calc`
- ✓ `adaptive-cruise-control`
- ✓ `bike-rebalance`
- ✓ `court-form-filling`
- ✓ `econ-detrending-correlation`
- ✓ `energy-ac-optimal-power-flow`
- ✓ `energy-market-pricing`
- ✓ `exam-block-sequencing`
- ✓ `exceltable-in-ppt`
- ✓ `fix-erlang-ssh-cve`
- ✓ `glm-lake-mendota`
- ✓ `gravitational-wave-detection`
- ✓ `grid-dispatch-operator`
- ✓ `hvac-control`
- ✓ `lean4-proof`
- ✓ `mars-clouds-clustering`
- ✓ `offer-letter-generator`
- ✓ `paper-anonymizer`
- ✓ `parallel-tfidf-search`
- ✓ `paratransit-routing`
- ✓ `pddl-tpp-planning`
- ✓ `pdf-excel-diff`
- ✓ `powerlifting-coef-calc`
- ✓ `protein-expression-analysis`
- ✓ `spring-boot-jakarta-migration`
- ✓ `threejs-to-obj`
- ✓ `tictoc-unnecessary-abort-detection`
- ✓ `weighted-gdp-calc`

## Partial credit

| task | reward |
|---|---|
| `dialogue-parser` | 0.833 |
| `lab-unit-harmonization` | 0.646 |
| `debug-trl-grpo` | 0.600 |
| `drone-planning-control` | 0.567 |
| `crystallographic-wyckoff-position-analysis` | 0.450 |

## Errored tasks (infra, not skill defect)

- ⚠ `earthquake-phase-association`
- ⚠ `fix-build-google-auto`
- ⚠ `fix-druid-loophole-cve`
- ⚠ `multilingual-video-dubbing`
- ⚠ `python-scala-translation`
- ⚠ `quantum-numerical-simulation`
- ⚠ `seismic-phase-picking`
- ⚠ `shock-analysis-demand`

## Artifacts

Local run — artifacts live on the recording host under `.capevolve/run_opus_optimize3/`
(gitignored, per-run):

- `baseline.json` — full per-task rewards
- `final.json` — headline numbers
- `events.jsonl` — event timeline (including manual splice records)
- `PATCH_NOTE.md` — human-readable splice documentation
- `rollouts/val/*.json` — 348 per-rollout JSONs across the 4 candidates (agent transcript + CTRF)
- `rollouts/test/*.json` — 174 per-rollout JSONs for the finalize step
- `dashboard.html` — static snapshot of the run's dashboard
