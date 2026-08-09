import json
import shutil
from pathlib import Path

import pytest

import sync_models as sm  # same dir; run pytest from ci/benchmarks/lib

REPO = Path(__file__).resolve().parents[3]

WF = """\
name: Benchmarks
on:
  workflow_dispatch:
    inputs:
      trials:
        description: "Trials"
        type: string
        default: "10"
      agent_model:
        description: "Evaluation model"
        type: choice
        default: "aws/gpt-oss-120b"
        options:
          - "aws/gpt-oss-120b"
          - "claude-opus-4-8"
      optimizer_model:
        description: "Optimization model"
        type: choice
        default: "claude-opus-4-8"
        options:
          - "aws/gpt-oss-120b"
          - "claude-opus-4-8"
      gate_k_se:
        description: "Gate"
        type: string
        default: "1.0"
"""

RS = 'AGENT_MODEL="${AGENT_MODEL:-aws/gpt-oss-120b}"\nOPTIMIZER_MODEL="${OPTIMIZER_MODEL:-claude-opus-4-8}"\n'


def _repo(tmp_path, wf=WF, rs=RS, tasks=None):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "benchmarks.yml").write_text(wf)
    (tmp_path / "ci" / "benchmarks" / "lib").mkdir(parents=True)
    (tmp_path / "ci" / "benchmarks" / "lib" / "run_suite.sh").write_text(rs)
    for tier, rows in (tasks or {}).items():
        d = tmp_path / "ci" / "benchmarks" / tier
        d.mkdir(parents=True, exist_ok=True)
        (d / "tasks.json").write_text(json.dumps(rows))
    return tmp_path


def _models(*ids):
    return [{"id": i} for i in ids]


# ---- served_ids ------------------------------------------------------------

def test_served_ids_dedupes_and_sorts_case_insensitively():
    body = json.dumps({"data": _models("azure/gpt-5.5", "Azure/o4-mini", "azure/gpt-5.4", "Azure/o4-mini")})
    assert sm.served_ids(body) == ["azure/gpt-5.4", "azure/gpt-5.5", "Azure/o4-mini"]


def test_served_ids_tolerates_bare_list_and_plain_strings():
    assert sm.served_ids(json.dumps(["b", "a"])) == ["a", "b"]
    assert sm.served_ids(json.dumps([{"id": "a"}, "b", {}, {"id": None}])) == ["a", "b"]


# ---- parsing the workflow --------------------------------------------------

def test_reads_current_options_and_defaults_per_picker():
    assert sm.current_options(WF, "agent_model") == ["aws/gpt-oss-120b", "claude-opus-4-8"]
    assert sm.current_default(WF, "agent_model") == "aws/gpt-oss-120b"
    assert sm.current_default(WF, "optimizer_model") == "claude-opus-4-8"


def test_rewrite_touches_only_the_named_picker():
    out = sm.rewrite_options(WF, "agent_model", ["x/one", "x/two", "x/three"])
    assert sm.current_options(out, "agent_model") == ["x/one", "x/two", "x/three"]
    # the OTHER picker must be untouched — the span must not run past its own block
    assert sm.current_options(out, "optimizer_model") == ["aws/gpt-oss-120b", "claude-opus-4-8"]
    # and neighbouring inputs must survive intact
    assert 'default: "1.0"' in out and 'gate_k_se:' in out and 'trials:' in out


def test_unknown_picker_raises_rather_than_silently_doing_nothing():
    with pytest.raises(ValueError):
        sm.current_options(WF, "nope_model")


# ---- sync ------------------------------------------------------------------

def test_check_reports_drift_without_writing(tmp_path):
    r = _repo(tmp_path)
    before = (r / sm.WORKFLOW).read_text()
    code, rep = sm.sync(r, ["aws/gpt-oss-120b", "claude-opus-4-8", "new/model"], write=False)
    assert code == sm.EXIT_DRIFT
    assert (r / sm.WORKFLOW).read_text() == before, "check mode must not write"
    assert any("+ new/model" in l for l in rep)


def test_write_applies_to_both_pickers(tmp_path):
    r = _repo(tmp_path)
    models = ["aws/gpt-oss-120b", "claude-opus-4-8", "new/model"]
    code, _ = sm.sync(r, models, write=True)
    assert code == sm.EXIT_OK
    text = (r / sm.WORKFLOW).read_text()
    assert sm.current_options(text, "agent_model") == models
    assert sm.current_options(text, "optimizer_model") == models


def test_idempotent(tmp_path):
    r = _repo(tmp_path)
    models = ["aws/gpt-oss-120b", "claude-opus-4-8"]
    sm.sync(r, models, write=True)
    first = (r / sm.WORKFLOW).read_text()
    code, rep = sm.sync(r, models, write=True)
    assert code == sm.EXIT_OK
    assert (r / sm.WORKFLOW).read_text() == first
    assert any("already in sync" in l for l in rep)


def test_unserved_default_blocks_the_whole_write(tmp_path):
    """A default outside its own options is an INVALID workflow.

    So when the default is no longer served we must not write the options either: a
    stale-but-valid workflow beats a fresh-but-broken one. actionlint rejects the latter.
    """
    r = _repo(tmp_path)
    before = (r / sm.WORKFLOW).read_text()
    code, rep = sm.sync(r, ["claude-opus-4-8"], write=True)   # gpt-oss-120b dropped
    assert code == sm.EXIT_DECISION
    assert (r / sm.WORKFLOW).read_text() == before, "must not write a workflow with an invalid default"
    blob = "\n".join(rep)
    assert "agent_model default 'aws/gpt-oss-120b' is NOT served" in blob
    assert "--agent-default" in blob, "must tell the operator how to resolve it"
    assert "candidate served defaults" in blob


def test_supplying_a_served_default_unblocks_and_moves_run_suite_too(tmp_path):
    r = _repo(tmp_path)
    code, rep = sm.sync(r, ["claude-opus-4-8"], write=True, agent_default="claude-opus-4-8")
    assert code == sm.EXIT_OK, "\n".join(rep)
    text = (r / sm.WORKFLOW).read_text()
    assert sm.current_default(text, "agent_model") == "claude-opus-4-8"
    assert sm.current_options(text, "agent_model") == ["claude-opus-4-8"]
    rs = (r / sm.RUN_SUITE).read_text()
    assert 'AGENT_MODEL="${AGENT_MODEL:-claude-opus-4-8}"' in rs, rs
    # the optimizer side must be untouched
    assert 'OPTIMIZER_MODEL="${OPTIMIZER_MODEL:-claude-opus-4-8}"' in rs


def test_requested_default_must_itself_be_served(tmp_path):
    r = _repo(tmp_path)
    before = (r / sm.WORKFLOW).read_text()
    code, rep = sm.sync(r, ["claude-opus-4-8"], write=True, agent_default="not/served")
    assert code == sm.EXIT_DECISION
    assert (r / sm.WORKFLOW).read_text() == before
    assert any("not served by this key" in l for l in rep)


def test_generated_workflow_keeps_every_default_inside_its_options(tmp_path):
    """The invariant actionlint enforces, asserted directly."""
    r = _repo(tmp_path)
    models = ["claude-opus-4-8", "gemini-2.5-pro"]
    code, _ = sm.sync(r, models, write=True, agent_default="gemini-2.5-pro")
    assert code == sm.EXIT_OK
    text = (r / sm.WORKFLOW).read_text()
    for picker in sm.PICKERS:
        assert sm.current_default(text, picker) in sm.current_options(text, picker)


def test_empty_model_list_refuses_to_blank_the_pickers(tmp_path):
    r = _repo(tmp_path)
    before = (r / sm.WORKFLOW).read_text()
    code, rep = sm.sync(r, [], write=True)
    assert code == sm.EXIT_DECISION
    assert (r / sm.WORKFLOW).read_text() == before
    assert any("refusing to blank" in l for l in rep)


def test_task_pins_are_warnings_not_failures(tmp_path):
    r = _repo(tmp_path, tasks={"swebench/smoke": [{"id": "t1", "agent": "gone/model"}]})
    code, rep = sm.sync(r, ["aws/gpt-oss-120b", "claude-opus-4-8"], write=True)
    assert code == sm.EXIT_OK, "an advisory pin must not fail the sync"
    assert any("pins unserved agent(s): gone/model" in l for l in rep)


# ---- against the REAL repository ------------------------------------------

def test_parses_the_real_workflow(tmp_path):
    """The regexes must cope with the actual file, not just the fixture."""
    text = (REPO / sm.WORKFLOW).read_text(encoding="utf-8")
    for picker in sm.PICKERS:
        opts = sm.current_options(text, picker)
        assert len(opts) > 5, f"{picker} parsed as only {opts}"
        assert all(o and '"' not in o for o in opts)
        assert sm.current_default(text, picker) in opts, f"{picker} default is not among its own options"


def test_roundtrip_on_a_copy_of_the_real_workflow_is_byte_stable(tmp_path):
    """Rewriting the real file with its own current options must change nothing."""
    dst = tmp_path / ".github" / "workflows"
    dst.mkdir(parents=True)
    shutil.copy(REPO / sm.WORKFLOW, dst / "benchmarks.yml")
    text = (dst / "benchmarks.yml").read_text()
    out = text
    for picker in sm.PICKERS:
        out = sm.rewrite_options(out, picker, sm.current_options(text, picker))
    assert out == text


# ---- --validate ------------------------------------------------------------

def test_validate_passes_on_the_real_workflow():
    code, rep = sm.validate(REPO)
    assert code == sm.EXIT_OK, "\n".join(rep)


def test_validate_catches_a_default_outside_its_options(tmp_path):
    bad = WF.replace('default: "aws/gpt-oss-120b"', 'default: "gone/model"', 1)
    r = _repo(tmp_path, wf=bad)
    code, rep = sm.validate(r)
    assert code == sm.EXIT_DECISION
    assert any("not among its own" in l for l in rep)


def test_validate_needs_no_gateway(tmp_path):
    """CLI: --validate must work without --models (no network, no key)."""
    r = _repo(tmp_path)
    assert sm.main(["--validate", "--repo", str(r)]) == sm.EXIT_OK


def test_cli_requires_models_unless_validating(tmp_path):
    assert sm.main(["--repo", str(_repo(tmp_path))]) == sm.EXIT_DECISION
