import json
from pathlib import Path

import record  # same dir; run pytest from ci/benchmarks/lib

# Real suite_report() task rows carry reward info ONLY — cost/latency are never
# per-task (every task in a suite run is scored in the same eval call).
TASK_OK = {
    "bench": "tau2", "task": "35",
    "reward_baseline": 0.0, "reward_opt": 1.0, "reward_delta": 1.0,
    "opt_infra": False, "run_dir": "/work/run_suite",
}
TASK2 = {**TASK_OK, "task": "37", "reward_baseline": 0.0, "reward_opt": 0.0}

# Real steps rows: one per suite-run iteration (baseline / each hill-climb step / finalize).
STEPS = [
    {"phase": "baseline", "iter": None, "candidate": "seed", "accepted": None,
     "reward": 0.0, "optimizer_usd": 0.0, "optimizer_seconds": 0.0,
     "eval_usd": 0.05, "eval_seconds": 40.0},
    {"phase": "iterate", "iter": 1, "candidate": "cand_0001", "accepted": True,
     "reward": 0.5, "optimizer_usd": 0.05, "optimizer_seconds": 12.0,
     "eval_usd": 0.05, "eval_seconds": 40.0},
    {"phase": "finalize", "iter": None, "candidate": None, "accepted": None,
     "reward": 0.5, "optimizer_usd": 0.0, "optimizer_seconds": 0.0,
     "eval_usd": 0.05, "eval_seconds": 40.0},
]


def _write_jsonl(p: Path, rows):
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_rollup_math():
    r = record.rollup([TASK_OK, TASK2], STEPS)
    assert r == {"reward_base": 0.0, "reward_opt": 0.5, "n": 2, "n_scored": 2,
                 "eval_usd": 0.15, "optimizer_usd": 0.05,
                 "eval_seconds": 120.0, "optimizer_seconds": 12.0}


# ---- infra-errored tasks are missing data, not zeros ------------------------
#
# Pilot run 31331458168 published `0.680 -> 0.620` while its own report and per-run UI said
# `0.680 -> 0.705`. Six of fifty tasks had failed on infrastructure and were averaged in as
# 0.0: 0.7045 * 44/50 = 0.62. The dashboard understated the optimized result by 8.5 points
# because containers died, and inverted the sign of the result — a regression instead of an
# improvement.

INFRA = {**TASK_OK, "task": "99", "reward_baseline": 1.0, "reward_opt": 0.0, "opt_infra": True}


def test_rollup_excludes_infra_errored_tasks_from_both_means():
    r = record.rollup([TASK_OK, TASK2, INFRA], STEPS)
    # TASK_OK 1.0 + TASK2 0.0 over 2 scored tasks; the infra task contributes to neither mean
    assert r["reward_opt"] == 0.5, "an ungradeable task must not be averaged in as 0.0"
    assert r["reward_base"] == 0.0
    assert r["n"] == 3 and r["n_scored"] == 2, "coverage must be published, not hidden"


def test_rollup_filters_both_sides_with_the_same_flag():
    """opt_infra is set when EITHER side was ungradeable (metrics.py), so a task that lost one
    side is not a valid paired sample. Filtering only the opt side would compare a 3-task
    baseline against a 2-task optimized figure."""
    r = record.rollup([TASK_OK, TASK2, INFRA], STEPS)
    # INFRA's baseline is 1.0; had it leaked into reward_base the mean would be 0.333
    assert r["reward_base"] == 0.0, "the infra task's baseline leaked into reward_base"


def test_rollup_returns_none_when_every_task_is_infra_errored():
    """No paired measurement at all: publish nothing rather than a fabricated 0.0."""
    assert record.rollup([INFRA], STEPS) is None


def test_rollup_sums_steps_not_tasks():
    # Regression: cost/latency must come from `steps`, never from summing a per-task
    # field — task rows carry no cost/latency at all in the real schema, and even if
    # they did, every row would share the same run-level value (summing would inflate
    # by the task count instead of reporting the true total).
    r = record.rollup([TASK_OK, TASK2, {**TASK_OK, "task": "40"}], STEPS)
    assert r is not None
    assert r["optimizer_usd"] == 0.05  # sum over the 2 real optimizer-costing steps, not x3 tasks
    assert r["eval_usd"] == 0.15


def test_rollup_empty_is_none():
    assert record.rollup([]) is None


def test_rollup_no_steps_is_zero():
    r = record.rollup([TASK_OK, TASK2])
    assert r is not None
    assert r["eval_usd"] == 0 and r["optimizer_usd"] == 0
    assert r["eval_seconds"] == 0 and r["optimizer_seconds"] == 0


def test_build_success(tmp_path):
    m = tmp_path / "metrics.jsonl"; _write_jsonl(m, [TASK_OK, TASK2])
    s = tmp_path / "steps.jsonl"; _write_jsonl(s, STEPS)
    meta = {"run_id": 1, "bench": "tau2", "conclusion": "success", "date": "2026-07-23T00:00:00Z"}
    rec = record.build_record(m, meta, s)
    assert rec["schema"] == 1
    assert rec["run_id"] == 1 and rec["bench"] == "tau2"
    assert len(rec["tasks"]) == 2
    assert len(rec["steps"]) == 3
    assert rec["suite"]["n"] == 2 and rec["suite"]["eval_usd"] == 0.15
    assert rec["suite"]["optimizer_usd"] == 0.05
    assert "flips" not in rec["suite"]


def test_build_failed_run_has_null_suite(tmp_path):
    m = tmp_path / "metrics.jsonl"; _write_jsonl(m, [TASK_OK])
    meta = {"run_id": 2, "bench": "tau2", "conclusion": "failure", "date": "d"}
    rec = record.build_record(m, meta)
    assert rec["suite"] is None
    assert len(rec["tasks"]) == 1
    assert rec["steps"] == []


def test_build_missing_metrics(tmp_path):
    meta = {"run_id": 3, "bench": "swebench", "conclusion": "success", "date": "d"}
    rec = record.build_record(tmp_path / "nope.jsonl", meta, tmp_path / "nope_steps.jsonl")
    assert rec["tasks"] == [] and rec["steps"] == [] and rec["suite"] is None


def test_aggregate_sorts_and_counts(tmp_path):
    d = tmp_path / "records"; d.mkdir()
    (d / "1__tau2.json").write_text(json.dumps({"run_id": 1, "bench": "tau2", "date": "2026-07-20T00:00:00Z"}))
    (d / "2__tau2.json").write_text(json.dumps({"run_id": 2, "bench": "tau2", "date": "2026-07-22T00:00:00Z"}))
    recs, meta = record.aggregate(d, now="2026-07-23T09:00:00Z")
    assert [r["run_id"] for r in recs] == [2, 1]  # newest first
    assert meta == {"count": 2, "runs": 2, "updated": "2026-07-23T09:00:00Z"}


def test_build_preserves_tier(tmp_path):
    m = tmp_path / "metrics.jsonl"; _write_jsonl(m, [TASK_OK])
    rec = record.build_record(m, {"run_id": 9, "bench": "tau2", "tier": "smoke",
                                   "conclusion": "success", "date": "d"})
    assert rec["tier"] == "smoke"
    # tier absent -> not fabricated (the page defaults missing tier to "smoke" at render)
    rec2 = record.build_record(m, {"run_id": 9, "bench": "tau2",
                                    "conclusion": "success", "date": "d"})
    assert "tier" not in rec2


def test_build_has_ui_true(tmp_path):
    m = tmp_path / "metrics.jsonl"; _write_jsonl(m, [TASK_OK])
    meta = {"run_id": 10, "bench": "tau2", "conclusion": "success", "date": "d"}
    rec = record.build_record(m, meta, has_ui=True)
    assert rec["has_ui"] is True


def test_build_has_ui_defaults_false(tmp_path):
    m = tmp_path / "metrics.jsonl"; _write_jsonl(m, [TASK_OK])
    meta = {"run_id": 11, "bench": "tau2", "conclusion": "success", "date": "d"}
    rec = record.build_record(m, meta)
    assert rec["has_ui"] is False
