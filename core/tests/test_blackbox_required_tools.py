"""The blackbox stack must fail by NAME when an external command it needs is missing.

The bug this file guards: the store is installed with
`make install-requirements || pip install -e .`, so a machine without `make` falls THROUGH to plain
pip — which cannot resolve the store's pinned `torch==2.10.0+cpu`, because `+cpu` is a local version
served only by download.pytorch.org and declared via `[tool.uv.sources]` that pip does not read.

The operator then sees `No matching distribution found for torch==2.10.0+cpu` and goes hunting a
dependency problem that does not exist. Observed on a fresh runner VM: the raised error surfaced a
truncated pip line (`one-any.whl.metadata (1.7 kB)`) and the actual cause,
`/bin/sh: line 1: make: command not found`, was buried in the middle of the output.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MOD = REPO / "skills" / "interventions" / "llm-proxies" / "blackbox" / "scripts" / "blackbox_env.py"


def _env():
    if str(MOD.parent) not in sys.path:
        sys.path.insert(0, str(MOD.parent))
    spec = importlib.util.spec_from_file_location("_bbenv_tools_probe", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


E = _env()
SRC = MOD.read_text(encoding="utf-8")


# --- what is declared -------------------------------------------------------------------

def test_make_is_declared_required_with_an_install_hint():
    """`make` must be in the table, and there must be a hint telling the operator how to get it —
    a name alone leaves them guessing on an unfamiliar image."""
    assert "make" in E._REQUIRED_TOOLS
    assert E._REQUIRED_TOOLS["make"], "no reason recorded"
    assert "install" in E._install_hint("make")


def test_the_macos_hint_does_not_send_the_reader_to_brew_install_make():
    """`brew install make` installs GNU make as `gmake` and leaves `make` unsatisfied, so following
    that advice would not satisfy the very check that printed it."""
    import sys as _sys

    real = _sys.platform
    try:
        _sys.platform = "darwin"
        E.sys.platform = "darwin"
        hint = E._install_hint("make")
    finally:
        _sys.platform = real
        E.sys.platform = real
    assert "xcode-select" in hint
    assert "brew install make" in hint and "NOT" in hint, \
        "the trap must be named, not merely avoided"


def test_auto_install_is_linux_only_and_lists_no_brew():
    """An auto-install that appears to succeed and changes nothing the check can see is worse than
    none, which is why Homebrew is deliberately absent from the manager list."""
    names = [n for n, _ in E._PKG_MANAGERS]
    assert "brew" not in names
    assert "apt-get" in names
    src_i = SRC.index("def ensure_tools(")
    body = SRC[src_i:SRC.index("\ndef ", src_i + 10)]
    assert 'sys.platform not in ("linux", "linux2")' in body


@pytest.mark.parametrize("tool", ["uv", "make", "git"])
def test_every_command_the_stack_shells_out_to_is_declared(tool):
    assert tool in E._REQUIRED_TOOLS
    assert E._install_hint(tool), "every declared tool needs a platform hint"


def test_the_declaration_matches_what_the_code_actually_shells_out_to():
    """A table that drifts from the commands is worse than no table."""
    for tool in ("make", "uv", "git"):
        assert f"{tool} " in SRC or f'"{tool}"' in SRC, f"{tool} declared but never invoked"


# --- when it fires ----------------------------------------------------------------------

def test_require_tools_passes_when_everything_is_present():
    missing = [t for t in E._REQUIRED_TOOLS if not shutil.which(t)]
    if missing:
        pytest.skip(f"this machine is missing {missing}; the negative cases below still run")
    E.require_tools()          # must not raise


def test_a_missing_make_is_reported_by_name_with_its_reason(monkeypatch):
    """The whole point: the error names `make`, not a torch version."""
    real = shutil.which

    def without_make(cmd, *a, **kw):
        return None if cmd == "make" else real(cmd, *a, **kw)

    monkeypatch.setattr(E.shutil, "which", without_make)
    with pytest.raises(RuntimeError) as ei:
        E.require_tools()
    msg = str(ei.value)
    assert "make" in msg
    assert "install:" in msg, "the message must say how to fix it, not only what is wrong"
    assert "torch" not in msg, "must not send the reader after the downstream symptom"


def test_several_missing_commands_are_all_listed_not_just_the_first(monkeypatch):
    """One-at-a-time failures mean N round trips on a fresh image."""
    monkeypatch.setattr(E.shutil, "which", lambda *a, **kw: None)
    with pytest.raises(RuntimeError) as ei:
        E.require_tools()
    msg = str(ei.value)
    for tool in E._REQUIRED_TOOLS:
        assert tool in msg, f"{tool} missing from the report"


# --- where it is called -----------------------------------------------------------------

def test_provision_checks_before_it_clones():
    """Failing after two checkouts wastes minutes for a one-line problem."""
    i = SRC.index("def provision(")
    body = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "require_tools()" in body
    assert body.index("require_tools()") < body.index("store_dir()"), \
        "the check must precede any cloning work"


def test_the_start_path_is_guarded_too():
    """`make run` has NO fallback, so a missing make surfaces as a health-check timeout on a
    service that was never launched."""
    i = SRC.index("def _start_detached(")
    body = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "require_tools()" in body
    # Anchor on the COMMAND CONSTRUCTION, not on any occurrence of "make run": the docstring
    # mentions it first, so a naive search compares the guard against prose and always fails.
    cmd_at = body.index("{extra}make run")
    assert body.index("require_tools()") < cmd_at, body[:300]


# --- the masked cause -------------------------------------------------------------------

def test_the_install_failure_names_make_when_that_is_the_cause():
    """Observed: the raised error showed a truncated pip line and hid
    `make: command not found`. The handler must recognise it and say what it implies."""
    i = SRC.index("def _install_service(")
    body = SRC[i:SRC.index("\ndef ", i + 10)]
    assert "make: command not found" in body, "the masking case must be detected explicitly"
    assert "torch" in body, "and the message must explain why pip then fails on torch"
    assert "out[-" in body, "show a useful tail, not an arbitrary slice of the middle"
