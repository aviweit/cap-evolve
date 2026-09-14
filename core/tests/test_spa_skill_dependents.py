"""Deleting a skill requires deleting whatever depends on it first.

``skills_service.delete`` refuses outright while anything depends on the skill::

    dependents = self.handler.dependency_manager.get_dependents(uuid)
    if dependents:
        raise ObjectInUseError("skill", uuid, dependents)      # -> HTTP 409

vMCP and vNFS servers register exactly that dependency when created against a skill, and they live
in the store rather than in the run — so an interrupted run leaves them behind in a store that keeps
running. The next run then failed EVERY rollout with "could not remove existing skill my_skill from
the store" (observed 2026-09-14: 50 tasks x 10 trials, coverage 0/50, and a baseline of 0.0 still
reported as a "floor").

These tests drive the real delete_skill against a fake store that enforces the store's own 409 rule,
because the bug was never in the HTTP calls themselves but in their ORDER, and in what a bare False
hides from the caller.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SPA_ENV = (Path(__file__).resolve().parents[2]
           / "skills/interventions/llm-proxies/spa/scripts/spa_env.py")

SKILL_UUID = "skill-uuid-1111"


@pytest.fixture(scope="module")
def spa_env():
    spec = importlib.util.spec_from_file_location("spa_env_under_test", SPA_ENV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeStore:
    """The subset of the store's HTTP surface delete_skill touches, with the 409 rule enforced.

    Models the store's actual precondition rather than trusting a mock to be called: DELETE
    /skills/<x> returns 409 for as long as any row still references it via ``skill_uuid``.
    """

    def __init__(self, *, skill=True, vmcp=(), vnfs=(), tools=()):
        self.skill = {"uuid": SKILL_UUID, "name": "my_skill",
                      "tool_uuids": list(tools), "snippet_uuids": []} if skill else None
        self.vmcp = [dict(r) for r in vmcp]
        self.vnfs = [dict(r) for r in vnfs]
        self.tools = {t: {"uuid": t, "name": "tool-" + t, "tags": []} for t in tools}
        self.calls = []

    def _blocking(self):
        return ([("vmcp", r) for r in self.vmcp if r.get("skill_uuid") == SKILL_UUID]
                + [("vnfs", r) for r in self.vnfs if r.get("skill_uuid") == SKILL_UUID])

    def curl(self, args, timeout=60):
        verb = args[args.index("-X") + 1]
        url = args[-1]
        path = "/" + url.split("/", 3)[3] if url.startswith("http") else url
        self.calls.append(verb + " " + path.split("?")[0])

        if verb == "GET" and path.startswith("/skills/"):
            return (200, json.dumps(self.skill)) if self.skill else (404, "not found")
        if verb == "GET" and path.startswith("/vmcp_servers/"):
            return 200, json.dumps(self.vmcp)
        if verb == "GET" and path.startswith("/vnfs_servers/"):
            return 200, json.dumps(self.vnfs)
        if verb == "GET" and path.startswith("/tools/"):
            uuid = path.split("/tools/")[1].split("?")[0]
            return (200, json.dumps(self.tools[uuid])) if uuid in self.tools else (404, "gone")
        if verb == "DELETE" and path.startswith("/vmcp_servers/"):
            uuid = path.rsplit("/", 1)[-1]
            self.vmcp = [r for r in self.vmcp if r.get("uuid") != uuid]
            return 204, ""
        if verb == "DELETE" and path.startswith("/vnfs_servers/"):
            uuid = path.rsplit("/", 1)[-1]
            self.vnfs = [r for r in self.vnfs if r.get("uuid") != uuid]
            return 204, ""
        if verb == "DELETE" and path.startswith("/skills/"):
            blocking = self._blocking()
            if blocking:
                named = ", ".join(k + ":" + r["uuid"] for k, r in blocking)
                return 409, json.dumps(
                    {"detail": "skill " + SKILL_UUID + " in use by [" + named + "]"})
            self.skill = None
            return 204, ""
        if verb == "DELETE" and path.startswith("/tools/"):
            self.tools.pop(path.rsplit("/", 1)[-1], None)
            return 204, ""
        return 404, "unhandled"

    def install(self, spa_env, monkeypatch):
        monkeypatch.setattr(spa_env, "_curl", self.curl)
        monkeypatch.setattr(spa_env, "store_port", lambda: "8000")
        monkeypatch.setattr(spa_env, "load_env", lambda: None)
        return self


# ---------------------------------------------------------------------------
# The three entry paths run.sh and the optimisation loop actually take
# ---------------------------------------------------------------------------

def test_1_clean_store_with_a_properly_imported_skill(spa_env, monkeypatch):
    """Nothing depends on the skill: the delete succeeds and no dependent is deleted."""
    store = FakeStore().install(spa_env, monkeypatch)

    assert spa_env.delete_skill("my_skill") is True
    assert store.skill is None
    assert not [c for c in store.calls
                if c.startswith("DELETE /vmcp") or c.startswith("DELETE /vnfs")]


def test_2_store_left_holding_a_previous_skill_with_a_live_vmcp(spa_env, monkeypatch):
    """The interrupted-run case: a vMCP server survives in the store and blocked every rollout."""
    store = FakeStore(vmcp=[{"uuid": "vmcp-1", "skill_uuid": SKILL_UUID}]).install(
        spa_env, monkeypatch)

    assert spa_env.delete_skill("my_skill") is True
    assert store.skill is None, "the skill must actually be gone"
    assert store.vmcp == [], "the blocking dependent must be gone too"
    assert store.calls.index("DELETE /vmcp_servers/vmcp-1") < \
        store.calls.index("DELETE /skills/my_skill"), "the order IS the fix"


def test_3_candidate_switch_clears_dependents_of_both_kinds(spa_env, monkeypatch):
    """Mid-run reimport, with both dependent kinds present at once."""
    store = FakeStore(vmcp=[{"uuid": "vmcp-1", "skill_uuid": SKILL_UUID},
                            {"uuid": "vmcp-2", "skill_uuid": SKILL_UUID}],
                      vnfs=[{"uuid": "vnfs-1", "skill_uuid": SKILL_UUID}]).install(
        spa_env, monkeypatch)

    assert spa_env.delete_skill("my_skill") is True
    assert (store.vmcp, store.vnfs, store.skill) == ([], [], None)


# ---------------------------------------------------------------------------
# Not over-reaching, and not hiding failures
# ---------------------------------------------------------------------------

def test_a_dependent_of_another_skill_is_left_alone(spa_env, monkeypatch):
    """It cannot be what blocks this skill, and deleting it would break the other one."""
    other = {"uuid": "vmcp-other", "skill_uuid": "skill-uuid-9999"}
    store = FakeStore(vmcp=[{"uuid": "vmcp-1", "skill_uuid": SKILL_UUID}, dict(other)]).install(
        spa_env, monkeypatch)

    assert spa_env.delete_skill("my_skill") is True
    assert store.vmcp == [other], "only the blocking dependent may be removed"


def test_a_missing_skill_is_success_and_touches_nothing(spa_env, monkeypatch):
    """Callers always delete-then-import, so 404 is the clean-slate path."""
    store = FakeStore(skill=False, vmcp=[{"uuid": "vmcp-1", "skill_uuid": SKILL_UUID}]).install(
        spa_env, monkeypatch)

    assert spa_env.delete_skill("my_skill") is True
    assert store.vmcp == [{"uuid": "vmcp-1", "skill_uuid": SKILL_UUID}]


def test_a_surviving_409_names_the_dependent_instead_of_returning_false(spa_env, monkeypatch):
    """An unknown dependent kind must be diagnosable in one read.

    "could not remove existing skill my_skill from the store" — no status code, no named dependent —
    is what made this take two days. If a third kind appears, the store names it in the 409 body and
    that must reach the operator.
    """
    FakeStore(vmcp=[{"uuid": "vmcp-ghost", "skill_uuid": SKILL_UUID}]).install(spa_env, monkeypatch)
    monkeypatch.setattr(spa_env, "delete_skill_dependents", lambda _uuid: True)   # unknown kind

    with pytest.raises(RuntimeError) as err:
        spa_env.delete_skill("my_skill")

    msg = str(err.value)
    assert "409" in msg and "vmcp-ghost" in msg
    assert "_SKILL_DEPENDENT_KINDS" in msg, "the message must say how to fix it"


def test_an_unexpected_status_is_raised_with_its_body(spa_env, monkeypatch):
    store = FakeStore().install(spa_env, monkeypatch)
    real = store.curl

    def broken(args, timeout=60):
        if args[args.index("-X") + 1] == "DELETE" and "/skills/" in args[-1]:
            return 500, "Error deleting skill: disk on fire"
        return real(args, timeout)

    monkeypatch.setattr(spa_env, "_curl", broken)

    with pytest.raises(RuntimeError) as err:
        spa_env.delete_skill("my_skill")
    assert "500" in str(err.value) and "disk on fire" in str(err.value)


def test_dependents_are_matched_on_skill_uuid_not_name(spa_env, monkeypatch):
    """add_dependent registers the skill's UUID, so a row without skill_uuid is not a dependent."""
    store = FakeStore(vmcp=[{"uuid": "vmcp-nolink"}]).install(spa_env, monkeypatch)

    assert spa_env.delete_skill_dependents(SKILL_UUID) is True
    assert store.vmcp == [{"uuid": "vmcp-nolink"}]


def test_a_failed_dependent_delete_is_reported(spa_env, monkeypatch):
    store = FakeStore(vmcp=[{"uuid": "vmcp-1", "skill_uuid": SKILL_UUID}]).install(
        spa_env, monkeypatch)
    real = store.curl

    def stubborn(args, timeout=60):
        if args[args.index("-X") + 1] == "DELETE" and "/vmcp_servers/" in args[-1]:
            return 500, "nope"
        return real(args, timeout)

    monkeypatch.setattr(spa_env, "_curl", stubborn)

    assert spa_env.delete_skill_dependents(SKILL_UUID) is False
