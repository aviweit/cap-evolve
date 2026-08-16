"""SPA (Skillberry Proxy-Agent) environment wiring for tau2 airline.

Replaces the RITS module from the original tau2_airline example. Points tau2's
litellm calls at SPA (localhost:7000) for the agent, while the user simulator
calls the upstream LLM directly via OPENAI_BASE_URL.

Provides service lifecycle helpers: restart_spa(skill_name) stops SPA, sets
SKILL_NAME, restarts, and waits for the health check — used by adapter.apply()
before each evaluation.

LAZY: no network at import time, so ``cap-evolve check`` stays offline.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

# Default model strings (env-overridable).
_DEFAULT_AGENT_MODEL = "ibm/skillberry-local"
_DEFAULT_USER_MODEL = "openai/aws/gpt-oss-120b"

# Ports
SPA_PORT = "7000"
STORE_PORT = "8000"

_SPA_DIR: Optional[str] = None
_STORE_DIR: Optional[str] = None


def _load_env() -> None:
    """Load the repo-root .env into os.environ (walk parents), without overwrite."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        env = parent / ".env"
        if env.exists():
            try:
                for raw in env.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key:
                        os.environ.setdefault(key, val)
            except Exception:
                pass
            break


def _get_spa_dir() -> str:
    """Resolve the skillberry-agent directory."""
    global _SPA_DIR
    if _SPA_DIR is None:
        _load_env()
        _SPA_DIR = os.environ.get("SKILLBERRY_AGENT_DIR", "")
    return _SPA_DIR


def _get_store_dir() -> str:
    """Resolve the skillberry-store directory."""
    global _STORE_DIR
    if _STORE_DIR is None:
        _load_env()
        _STORE_DIR = os.environ.get("SKILLBERRY_STORE_DIR", "")
    return _STORE_DIR


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def agent_model() -> str:
    """litellm model string for the agent under test (default: ibm/skillberry-local)."""
    _load_env()
    return os.environ.get("TAU2_AGENT_MODEL") or _DEFAULT_AGENT_MODEL


def user_model() -> str:
    """litellm model string for the user simulator (default: openai/aws/gpt-oss-120b)."""
    _load_env()
    return os.environ.get("TAU2_USER_MODEL") or _DEFAULT_USER_MODEL


def _is_spa_routed(model: str) -> bool:
    """True if this model routes through SPA (not directly to upstream)."""
    m = (model or "").lower()
    return "skillberry" in m or m == _DEFAULT_AGENT_MODEL.lower()


def llm_args_for(model: str) -> dict:
    """Per-model litellm args: SPA-routed models → localhost:7000; others → upstream."""
    _load_env()
    if _is_spa_routed(model):
        return _spa_llm_args()
    return _upstream_llm_args()


def _spa_llm_args() -> dict:
    """litellm args pointing at SPA on localhost.

    NOTE: Do NOT pass api_key here — tau2's llm_utils already sets api_key="EMPTY"
    for the skillberry model path and passing it in kwargs causes a duplicate error.
    """
    return {
        "temperature": 0.0,
    }


def _upstream_llm_args() -> dict:
    """litellm args for direct upstream access (user simulator)."""
    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not base_url:
        raise RuntimeError(
            "OPENAI_BASE_URL not set. Put it in the repo-root .env or export it."
        )
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not set. Put it in the repo-root .env or export it."
        )
    return {
        "api_base": base_url,
        "api_key": api_key,
        "temperature": 0.0,
    }


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------


def _wait_for_health(port: str, timeout: int = 60) -> bool:
    """Poll localhost:<port>/health until responsive or timeout."""
    import urllib.request
    import urllib.error

    deadline = time.time() + timeout
    url = f"http://localhost:{port}/health"
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            time.sleep(2)
    return False


def stop_spa() -> None:
    """Stop the SPA service."""
    spa_dir = _get_spa_dir()
    if spa_dir and Path(spa_dir).is_dir():
        try:
            subprocess.run(
                ["make", "stop"], cwd=spa_dir,
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass
    # Force-kill anything on the port
    port = os.environ.get("SKILLBERRY_AGENT_PORT", SPA_PORT)
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5,
        )
        for pid in result.stdout.strip().split():
            if pid:
                os.kill(int(pid), 9)
    except Exception:
        pass


def start_spa(skill_name: str) -> None:
    """Start SPA with the given SKILL_NAME.

    Runs inside the service's own venv (make run requires an active venv).
    """
    spa_dir = _get_spa_dir()
    if not spa_dir or not Path(spa_dir).is_dir():
        raise RuntimeError(
            f"SKILLBERRY_AGENT_DIR not set or not a directory: {spa_dir!r}"
        )

    env = os.environ.copy()
    env["SKILL_NAME"] = skill_name
    env.setdefault("USE_AGENT_TOOLS", "false")
    env.setdefault("USE_AGENT_PROMPTS", "true")
    env.setdefault("MCP_PROMPTS_POSITION", "postfix")

    # Must activate the venv — make run includes a verify-venv check.
    cmd = f"cd {spa_dir} && . .venv/bin/activate && make run"
    subprocess.Popen(
        ["bash", "-c", cmd],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    port = os.environ.get("SKILLBERRY_AGENT_PORT", SPA_PORT)
    if not _wait_for_health(port, timeout=60):
        raise RuntimeError(
            f"SPA failed to start with SKILL_NAME={skill_name} on port {port}"
        )


def restart_spa(skill_name: str) -> None:
    """Stop SPA, then restart with a new SKILL_NAME."""
    stop_spa()
    time.sleep(2)
    start_spa(skill_name)


# ---------------------------------------------------------------------------
# Store interaction
# ---------------------------------------------------------------------------


def upload_skill(skill_dir: str | Path) -> bool:
    """Upload a skill directory to the store via POST /skills/import-anthropic.

    Returns True on success, False on failure.
    """
    skill_dir = str(Path(skill_dir).resolve())
    port = os.environ.get("SKILLBERRY_STORE_PORT", STORE_PORT)
    url = f"http://localhost:{port}/skills/import-anthropic"

    try:
        import urllib.request
        import urllib.parse
        import json

        # Use subprocess + curl for multipart form upload (simpler than urllib multipart)
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST", url,
                "-F", "source_type=folder",
                "-F", f"folder_path={skill_dir}",
                "-F", "snippet_mode=file",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            try:
                resp = json.loads(result.stdout)
                return resp.get("success", False) is True
            except Exception:
                pass
    except Exception:
        pass
    return False
