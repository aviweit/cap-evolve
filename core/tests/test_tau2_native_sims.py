"""tau2's own trajectories were never written, so `tau2 view` had nothing to show.

Both ``run_tasks`` call sites in the tau2 adapter passed ``save_path=None``: tau2 built
``SimulationResults`` in memory, ``_sim_to_rollout`` converted each sim, and the native
object was dropped. A user optimizing tau2 airline saw only cap-evolve's own per-rollout
JSON — complete traces, but in cap-evolve's schema, which `tau2 view` cannot read. tau2
still closed every run with "To review the simulations, run: tau2 view" (it prints that
unconditionally), pointing at a directory that was never written.

The adapter now saves to ``<run_ts>/native_sims/<tag>/results.json``, tying each set of
native traces to the phase that produced it (``seed`` = baseline, ``candidate_NNN`` = an
iteration) via the candidate dir the harness already passes as ``ctx``.

The path must be FRESH on every call. tau2 reads an existing results file (or its
``simulations/`` sibling) as a run to resume: it prompts on stdin and raises
FileExistsError when the answer is not "y" (tau2/runner/checkpoint.py), and ``auto_resume``
is off by default. An eval has no stdin, so a collision would hang or kill the run — and a
silent resume would be worse, returning a previous split's sims as if they were this
one's. The same tag IS evaluated twice (seed on val at baseline, seed on test at finalize).
"""

import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / "templates" / "adapters" / "tau2_bench" / "adapter.py"


def _load_adapter_module():
    """Import the tau2 adapter with `tau2` stubbed (CI has no tau2-bench install)."""
    for p in (REPO / "core", ADAPTER.parent, ADAPTER.parent.parent):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    llm_utils = types.ModuleType("tau2.utils.llm_utils")
    llm_utils.get_token_usage = lambda messages: {"completion_tokens": 0, "prompt_tokens": 0}
    for name, mod in (
        ("tau2", types.ModuleType("tau2")),
        ("tau2.utils", types.ModuleType("tau2.utils")),
        ("tau2.utils.llm_utils", llm_utils),
    ):
        sys.modules.setdefault(name, mod)

    spec = importlib.util.spec_from_file_location("_tau2_adapter_sims", ADAPTER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _adapter():
    return _load_adapter_module().Adapter.__new__(_load_adapter_module().Adapter)


def _candidate_dir(tmp_path, tag="seed"):
    """The ctx the harness passes: <run_ts>/candidates/<tag>."""
    d = tmp_path / "run_20260101_000000" / "candidates" / tag
    d.mkdir(parents=True)
    return d


def test_path_is_under_the_run_dir_and_named_for_the_phase(tmp_path):
    a = _adapter()
    run = tmp_path / "run_20260101_000000"

    seed = a._sim_save_path(_candidate_dir(tmp_path, "seed"))
    assert seed == run / "native_sims" / "seed" / "results.json"

    cand = a._sim_save_path(_candidate_dir(tmp_path, "candidate_003"))
    assert cand == run / "native_sims" / "candidate_003" / "results.json", \
        "an iteration's traces must be distinguishable from the baseline's"


def test_second_eval_of_the_same_tag_gets_a_fresh_path(tmp_path):
    """seed is evaluated on val at baseline and on test at finalize — same tag, twice."""
    a = _adapter()
    ctx = _candidate_dir(tmp_path, "seed")
    first = a._sim_save_path(ctx)
    first.parent.mkdir(parents=True)          # tau2 would have written it

    second = a._sim_save_path(ctx)
    assert second != first, "reusing the path makes tau2 prompt on stdin, then raise"
    assert second.parent.name == "seed-2"

    second.parent.mkdir(parents=True)
    assert a._sim_save_path(ctx).parent.name == "seed-3", "must keep walking, not wrap"


def test_a_bare_simulations_sibling_also_counts_as_taken(tmp_path):
    """tau2's has_existing is `save_path.exists() or (parent/'simulations').exists()`, so a
    directory holding only the per-sim files still trips the resume prompt."""
    a = _adapter()
    ctx = _candidate_dir(tmp_path, "seed")
    (a._sim_save_path(ctx).parent / "simulations").mkdir(parents=True)
    assert a._sim_save_path(ctx).parent.name == "seed-2"


def test_unexpected_layout_disables_saving_instead_of_raising(tmp_path):
    """Saving native traces is a convenience; it must never be what breaks an eval."""
    a = _adapter()
    loose = tmp_path / "not_a_candidate_dir"
    loose.mkdir()
    assert a._sim_save_path(loose) is None
    assert a._sim_save_path(None) is None


def test_both_call_sites_pass_the_save_path(monkeypatch, tmp_path):
    """The actual bug was two `save_path=None` literals; a helper alone does not fix it."""
    mod = _load_adapter_module()
    seen = []

    class _Sims:
        simulations = []

    runner = types.ModuleType("tau2.runner")
    runner.run_tasks = lambda config, tasks, **kw: (seen.append(kw.get("save_path")), _Sims())[1]
    sim_model = types.ModuleType("tau2.data_model.simulation")
    sim_model.TextRunConfig = lambda **kw: types.SimpleNamespace(**kw)
    sim_model.TerminationReason = types.SimpleNamespace()
    gateway = types.ModuleType("gateway")
    gateway.agent_model = gateway.user_model = lambda: "aws/claude-haiku-4-5"
    gateway.llm_args_for = lambda m: {}
    for name, m in (("tau2.runner", runner),
                    ("tau2.data_model", types.ModuleType("tau2.data_model")),
                    ("tau2.data_model.simulation", sim_model),
                    ("gateway", gateway)):
        monkeypatch.setitem(sys.modules, name, m)

    a = mod.Adapter.__new__(mod.Adapter)
    # Must MAP the task: both methods return early (no run_tasks call) when nothing maps.
    monkeypatch.setattr(type(a), "_tau2_tasks_by_id",
                        lambda self: {"9": types.SimpleNamespace(id="9")}, raising=False)
    task = types.SimpleNamespace(id="9")
    ctx = _candidate_dir(tmp_path, "seed")

    a.run_batch([task], ctx, seed=0)
    a.run_trials([task], ctx, n_trials=2, base_seed=0)

    assert len(seen) == 2, "run_batch and run_trials must both reach run_tasks"
    for p in seen:
        assert p is not None, "save_path=None means tau2 view still has nothing to read"
        assert Path(p).name == "results.json"
        assert Path(p).parent.parent.name == "native_sims"
