"""spa_env rotates the service logs it captures.

Both the Store and the Proxy-Agent are launched with their stdout appended to a file that was
never truncated or rotated, in a vendor directory that is deliberately shared and long-lived.
proxy-agent.log passes 100MB routinely — the agent logs every request to stdout as well as to
its own file — and the store has no file handler at all on its serving path, so that capture is
its only log. Left alone, both grow for as long as the machine is up.

These tests drive the real function rather than grepping the source, because the failure mode is
in the generation shuffle: an off-by-one there either loses the newest log or grows without
bound, and both look fine in a diff.
"""

import importlib.util
from pathlib import Path

import pytest

SPA_ENV = (Path(__file__).resolve().parents[2]
           / "skills/interventions/llm-proxies/spa/scripts/spa_env.py")


@pytest.fixture(scope="module")
def spa_env():
    spec = importlib.util.spec_from_file_location("spa_env_under_test", SPA_ENV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_small_log_is_left_alone(spa_env, tmp_path):
    """Rotating a log that has not grown would throw away history for nothing."""
    log = tmp_path / "proxy-agent.log"
    log.write_text("recent lines\n")
    spa_env._rotate_if_large(log, max_bytes=1024, backups=3)
    assert log.read_text() == "recent lines\n"
    assert not (tmp_path / "proxy-agent.log.1").exists()


def test_an_oversized_log_is_rotated_and_the_live_path_is_freed(spa_env, tmp_path):
    log = tmp_path / "store.log"
    log.write_bytes(b"x" * 2048)
    spa_env._rotate_if_large(log, max_bytes=1024, backups=3)
    assert not log.exists(), "the live path must be free for the next append"
    assert (tmp_path / "store.log.1").stat().st_size == 2048


def test_generations_shift_and_the_oldest_falls_off(spa_env, tmp_path):
    """The bound is only real if the LAST generation is dropped rather than shifted forever."""
    log = tmp_path / "store.log"
    for gen, body in ((1, b"gen1"), (2, b"gen2"), (3, b"gen3")):
        (tmp_path / f"store.log.{gen}").write_bytes(body)
    log.write_bytes(b"y" * 2048)

    spa_env._rotate_if_large(log, max_bytes=1024, backups=3)

    assert (tmp_path / "store.log.1").read_bytes() == b"y" * 2048   # newest becomes .1
    assert (tmp_path / "store.log.2").read_bytes() == b"gen1"
    assert (tmp_path / "store.log.3").read_bytes() == b"gen2"
    assert not (tmp_path / "store.log.4").exists(), "backups=3 must not grow a 4th generation"
    # gen3 was the oldest and is gone — that is what makes the total bounded.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "store.log.1", "store.log.2", "store.log.3"]


def test_repeated_rotation_stays_bounded(spa_env, tmp_path):
    """The property that matters on a long-lived runner: files never accumulate."""
    log = tmp_path / "proxy-agent.log"
    for _ in range(12):
        log.write_bytes(b"z" * 2048)
        spa_env._rotate_if_large(log, max_bytes=1024, backups=3)
    assert len(list(tmp_path.iterdir())) == 3, sorted(p.name for p in tmp_path.iterdir())


def test_a_missing_log_is_not_an_error(spa_env, tmp_path):
    spa_env._rotate_if_large(tmp_path / "never-written.log", max_bytes=1, backups=3)


def test_rotation_failure_never_blocks_a_start(spa_env, tmp_path):
    """A full disk or read-only mount is a reason to run degraded, not to refuse to start."""
    log = tmp_path / "store.log"
    log.write_bytes(b"x" * 2048)
    (tmp_path / "store.log.1").mkdir()      # replace() onto a directory raises OSError
    spa_env._rotate_if_large(log, max_bytes=1024, backups=3)   # must not propagate
    assert log.exists(), "the live log should survive a failed rotation"


def test_start_detached_rotates_before_appending(spa_env):
    """The rotation has to be wired into the capture path, not merely defined."""
    src = SPA_ENV.read_text(encoding="utf-8")
    body = src.split("def _start_detached(", 1)[1].split("\ndef ", 1)[0]
    assert "_rotate_if_large(log)" in body
    assert body.index("_rotate_if_large(log)") < body.index('log.open("ab")'), (
        "rotate BEFORE opening the handle: rotating after would leave the process writing "
        "into the rotated-away inode")


def test_the_ceiling_is_overridable_but_bounded_by_default(spa_env):
    assert spa_env.LOG_MAX_BYTES > 0 and spa_env.LOG_BACKUPS > 0
    assert spa_env.LOG_MAX_BYTES * (1 + spa_env.LOG_BACKUPS) <= 64 * 1024 * 1024, (
        "the default per-service ceiling should stay small enough that a log is never the "
        "largest thing in the vendor dir")
