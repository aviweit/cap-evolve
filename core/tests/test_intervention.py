"""The ``intervention:`` spec field — validation, resolution, and preflight refusals.

Everything here is OFFLINE: no service is started and no network is touched. The one
thing that must never regress is the *refusal* behaviour — a spec whose intervention cannot
be honoured has to stop the run up front, because an out-of-process delivery failure is
(correctly) treated as per-candidate infra noise downstream, which means a run against a
dead stack does not crash. It quietly errors every rollout and reports having measured
nothing.
"""

import json
from pathlib import Path

import pytest

from cap_evolve import intervention as itv

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "skills"
MANIFEST = json.loads((SKILLS / "_registry" / "manifest.json").read_text())["skills"]


@pytest.fixture(autouse=True)
def _forget_emitted_warnings():
    """The deprecation warning is emitted once per process, so without this the alias tests
    pass or fail depending on which ran first."""
    itv._WARNED.clear()


# --- declared() -----------------------------------------------------------


def test_absent_intervention_is_direct():
    assert itv.declared({}) == itv.DIRECT
    assert itv.declared({"intervention": None}) == itv.DIRECT
    assert itv.declared({"intervention": ""}) == itv.DIRECT


def test_known_values_are_normalised():
    assert itv.declared({"intervention": "blackbox"}) == itv.BLACKBOX
    assert itv.declared({"intervention": "  BLACKBOX "}) == itv.BLACKBOX
    assert itv.declared({"intervention": "direct"}) == itv.DIRECT


def test_unknown_value_is_refused_by_name():
    """The spec reader has no unknown-key/value rejection, so this is the only guard: a
    typo must not silently deliver candidates the direct way."""
    with pytest.raises(itv.InterventionError) as e:
        itv.declared({"intervention": "sap"})
    assert "sap" in str(e.value) and "direct" in str(e.value)


def test_the_deprecated_alias_still_resolves_and_warns(capsys):
    """A spec written before the rename — including one outside this repo — must keep running."""
    assert itv.declared({"intervention": "spa"}) == itv.BLACKBOX
    assert itv.declared({"intervention": "  SPA "}) == itv.BLACKBOX
    out = capsys.readouterr()
    assert "deprecated alias" in out.err and itv.BLACKBOX in out.err
    assert out.out == "", "a deprecation line must never reach the JSON on stdout"


def test_the_alias_warns_once_per_process():
    """``declared()`` is called from both `check` and the run path, and one command printing
    the same deprecation twice reads like two different problems."""
    itv.declared({"intervention": "spa"})
    before = len(itv._WARNED)
    itv.declared({"intervention": "spa"})
    assert len(itv._WARNED) == before


def test_the_alias_is_not_advertised_as_a_valid_value():
    """An alias offered as a choice would keep minting new specs that need the alias."""
    assert "spa" not in itv.KNOWN
    with pytest.raises(itv.InterventionError) as e:
        itv.declared({"intervention": "sap"})
    assert "spa" not in str(e.value), "the error must not teach the deprecated name"


# --- skill resolution -----------------------------------------------------


def test_blackbox_intervention_skill_is_registered():
    row = MANIFEST.get("blackbox")
    assert row, "the blackbox intervention skill is missing from the manifest"
    assert row["component"] == "intervention"
    assert (SKILLS / row["path"] / "scripts" / "blackbox_env.py").exists()


def test_skill_dir_rejects_a_component_mismatch():
    """Resolution is by name AND component: a name collision with a capability must not
    hand the intervention layer the wrong skill."""
    fake = {"blackbox": {"component": "capability", "path": "capabilities/skill-package"}}
    with pytest.raises(itv.InterventionError) as e:
        itv.skill_dir("blackbox", fake, SKILLS)
    assert "not 'intervention'" in str(e.value)


def test_skill_dir_reports_an_unregistered_intervention():
    with pytest.raises(itv.InterventionError) as e:
        itv.skill_dir("nope", {}, SKILLS)
    assert "build_manifest" in str(e.value)


# --- preflight ------------------------------------------------------------


def test_direct_preflight_is_a_noop():
    assert itv.preflight({}, {}, SKILLS) == {"intervention": itv.DIRECT}


def test_blackbox_preflight_refuses_when_not_provisioned(tmp_path, monkeypatch):
    """A run must NOT clone and install two services behind the operator's back; it must
    say which onboarding step was skipped."""
    monkeypatch.setenv("SPA_VENDOR_DIR", str(tmp_path / "empty-vendor"))
    with pytest.raises(itv.InterventionError) as e:
        itv.preflight({"intervention": "blackbox", "skill_name": "x"}, MANIFEST, SKILLS)
    msg = str(e.value)
    assert "not provisioned" in msg and "setup.sh" in msg


def test_blackbox_preflight_needs_a_skill_name(tmp_path, monkeypatch):
    """SPA serves exactly one skill; with no name it falls back to searching the store,
    which succeeds silently even when the store is empty."""
    monkeypatch.setenv("SPA_VENDOR_DIR", str(tmp_path / "empty-vendor"))
    with pytest.raises(itv.InterventionError):
        itv.preflight({"intervention": "blackbox"}, MANIFEST, SKILLS)


# --- describe() -----------------------------------------------------------


def test_describe_direct_and_blackbox():
    assert "direct" in itv.describe({"intervention": "direct"})
    line = itv.describe({"intervention": "blackbox", "store_port": "8000", "proxy_port": "7000",
                        "skill_name": "airline_skill",
                        "remote_env": "http://127.0.0.1:8004", "remote_env_healthy": False})
    assert "airline_skill" in line and "UNREACHABLE" in line
