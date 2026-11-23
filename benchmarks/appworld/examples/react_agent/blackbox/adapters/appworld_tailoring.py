"""AppWorld blackbox tailoring: route AppWorld's stock ReAct agent through the Skillberry Proxy-Agent.

Nothing in AppWorld is modified. The agent under test is AppWorld's own SimplifiedReActCodeAgent with
its own stock prompt file. What this module adds, from outside the benchmark:

- The client's model is the proxy's sentinel (``openai/ibm/skillberry-local`` at the proxy's
  ``/v1``), so every LLM call goes to the Skillberry Proxy-Agent (SPA).
- Every call carries ``skillberry-context-*`` headers (README: How a call flows):
  ``env_id`` / ``task_id`` / ``plugin``, ``history_start_regex`` (the proxy drops the client's
  22 stock prompt messages up to the real task turn) and ``template-main_user-*`` (the proxy
  renders the candidate SKILL.md with the task's supervisor).
- The candidate (a skill folder: SKILL.md, plus the tools capability in ``repl/helpers.py`` and
  ``repl/api_notes.json``) is deployed to the Skillberry Store, and SPA is restarted bound to it
  (``deploy``). The ``repl/`` files reach the store packaged into one tool, ``scripts/init_repl.py``
  (``build_init_repl``), which the model calls to load them into the trial's own REPL; the
  ``skillberry-context-appworld-repl_port`` header tells that tool which REPL is the trial's.
- SPA's upstream model comes from the same ``AW_*`` variables the direct approach uses
  (``spa_upstream_env``).
"""

from __future__ import annotations

import hashlib
import os
import sys
import shutil
import socket
import subprocess
import tempfile
import time
import threading
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SKILL_NAME = "my_skill"
PLUGIN = "appworld"
SPA_MODEL_SENTINEL = "openai/ibm/skillberry-local"
# The client's real task turn: the last message of AppWorld's stock prompt. It is the only message
# that starts with this phrase (the demo's look-alike reads "Using these APIs, you can now ...").
HISTORY_START_REGEX = r"^\s*Using these APIs, now generate code to solve the actual task:"
SUPERVISOR_FIELDS = ("first_name", "last_name", "email", "phone_number")

# What the direct approach uses for its model (adapters/aw_agent.py MODEL_CONFIG).
DEFAULT_MODEL_NAME = "hosted_vllm/openai/gpt-oss-120b-a100"
DEFAULT_MODEL_URL = "{{MODEL_SERVER_URL}}/gpt-oss-120b-a100/v1"
URL_KEYS = ("RITS_URL", "MODEL_SERVER_URL")
# litellm provider prefixes the direct approach may use that are OpenAI-compatible endpoints;
# SPA reaches them through litellm's openai provider (OPENAI_API_BASE / OPENAI_API_KEY).
OPENAI_COMPATIBLE_PREFIXES = ("hosted_vllm/", "litellm_proxy/", "openai/")


class BlackboxConfigError(RuntimeError):
    """The AW_* model settings cannot be served through the proxy."""


def _blackbox_scripts() -> Path:
    skills = os.environ.get("CAPEVOLVE_SKILLS_DIR")
    if skills:
        return Path(skills) / "interventions" / "llm-proxies" / "blackbox" / "scripts"
    for p in _HERE.parents:
        cand = p / "skills" / "interventions" / "llm-proxies" / "blackbox" / "scripts"
        if cand.is_dir():
            return cand
    raise RuntimeError("cannot locate skills/interventions/llm-proxies/blackbox/scripts "
                       "(set CAPEVOLVE_SKILLS_DIR)")


def blackbox_env():
    scripts = str(_blackbox_scripts())
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import blackbox_env as bb  # noqa: PLC0415
    return bb


# ---- per-call headers --------------------------------------------------------------------------
def context_headers(env_id: str, task_id: str, supervisor: dict,
                    repl_port: int | None = None) -> dict[str, str]:
    """The skillberry-context-* headers for one episode. Values must be single-line ASCII.

    ``repl_port`` is the trial's AppWorld environment server (the remote REPL). The store hands every
    header to its tools as ``skillberry_context``; ``init_repl`` reads this one as
    ``skillberry_context["appworld"]["repl_port"]``.
    """
    headers = {
        "skillberry-context-plugin": PLUGIN,
        "skillberry-context-env_id": str(env_id),
        "skillberry-context-task_id": str(task_id),
        "skillberry-context-history_start_regex": HISTORY_START_REGEX,
    }
    if repl_port is not None:
        headers[f"skillberry-context-{PLUGIN}-repl_port"] = str(int(repl_port))
    for field in SUPERVISOR_FIELDS:
        value = str(supervisor.get(field, ""))
        if "\n" in value or "\r" in value or not value.isascii():
            raise ValueError(f"supervisor {field!r} is not a single-line ASCII header value")
        headers[f"skillberry-context-template-main_user-{field}"] = value
    return headers


# ---- SPA's upstream model, from the AW_* variables --------------------------------------------
def _read_kv(path: str | None, keys: tuple[str, ...]) -> dict[str, str]:
    """Only ``keys`` from a KEY=VALUE file (the secrets file); every other line is ignored."""
    out: dict[str, str] = {}
    if not path or not Path(path).is_file():
        return out
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() in keys:
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def spa_model_name(aw_model_name: str) -> str:
    """The litellm model SPA should call for the direct approach's ``AW_MODEL_NAME``.

    ``AW_SPA_MODEL`` overrides. Otherwise an OpenAI-compatible provider prefix is replaced by
    ``openai/`` so SPA's litellm uses OPENAI_API_BASE / OPENAI_API_KEY, keeping the served model id
    (``hosted_vllm/openai/gpt-oss-120b-a100`` -> ``openai/openai/gpt-oss-120b-a100``).
    """
    override = os.environ.get("AW_SPA_MODEL")
    if override:
        return override
    for prefix in OPENAI_COMPATIBLE_PREFIXES:
        if aw_model_name.startswith(prefix):
            return "openai/" + aw_model_name[len(prefix):]
    return "openai/" + aw_model_name


def spa_upstream_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """SPA env overrides (model, endpoint, key) derived from the direct approach's AW_* variables.

    The proxy sends a standard ``Authorization: Bearer`` key, so the AW endpoint must accept one:
    ``AW_KEY_HEADER=bearer`` (a LiteLLM gateway, as CI uses). A custom-header endpoint such as RITS
    with ``RITS_API_KEY: <key>`` cannot be served through the proxy.
    """
    env = dict(os.environ if environ is None else environ)
    key_header = env.get("AW_KEY_HEADER", "RITS_API_KEY")
    if key_header.lower() != "bearer":
        raise BlackboxConfigError(
            f"AW_KEY_HEADER={key_header!r}: the proxy authenticates with a standard bearer key, so the "
            "blackbox approach needs AW_KEY_HEADER=bearer and a gateway that accepts it (README: Models)")
    key_env = env.get("AW_KEY_ENV", "RITS_API_KEY")
    secrets = _read_kv(env.get("AW_SECRETS_FILE"), (key_env,) + URL_KEYS)
    key = env.get(key_env) or secrets.get(key_env)
    if not key:
        raise BlackboxConfigError(f"no value for {key_env} (AW_KEY_ENV) in the environment or AW_SECRETS_FILE")
    url = env.get("AW_MODEL_URL", DEFAULT_MODEL_URL)
    if "{{MODEL_SERVER_URL}}" in url:
        server = (env.get("MODEL_SERVER_URL") or env.get("RITS_URL")
                  or secrets.get("MODEL_SERVER_URL") or secrets.get("RITS_URL"))
        if not server:
            raise BlackboxConfigError("AW_MODEL_URL uses {{MODEL_SERVER_URL}} but neither MODEL_SERVER_URL "
                                      "nor RITS_URL is set")
        url = url.replace("{{MODEL_SERVER_URL}}", server.rstrip("/"))
    return {
        "SPA_MODEL_NAME": spa_model_name(env.get("AW_MODEL_NAME", DEFAULT_MODEL_NAME)),
        "OPENAI_API_BASE": url,
        "OPENAI_BASE_URL": url,
        "OPENAI_API_KEY": key,
    }


# ---- the tools capability: repl/ -> the init_repl store tool -----------------------------------
REPL_DIR = "repl"
REPL_FILES = ("helpers.py", "api_notes.json")
INIT_REPL = "init_repl"
INIT_REPL_REL = f"scripts/{INIT_REPL}.py"   # in the skill; frozen (protected_paths in capevolve.yaml)


def _init_repl_template() -> Path:
    """The benchmark's own frozen copy of scripts/init_repl.py, never the candidate's: the project's
    (setup_project.sh copies it next to this file), else the committed seed's (in the repo)."""
    project = _HERE / "init_repl_template.py"
    return project if project.is_file() else _HERE.parent / "seed_capability" / SKILL_NAME / INIT_REPL_REL


INIT_REPL_TEMPLATE = _init_repl_template()


def read_repl(skill_dir: Path) -> tuple[str, dict[str, str]]:
    """``(helpers.py source, api notes)`` of a candidate; missing files are empty."""
    import json  # noqa: PLC0415
    d = Path(skill_dir) / REPL_DIR
    helpers = (d / "helpers.py").read_text(encoding="utf-8") if (d / "helpers.py").is_file() else ""
    raw = (d / "api_notes.json").read_text(encoding="utf-8") if (d / "api_notes.json").is_file() else ""
    notes = json.loads(raw) if raw.strip() else {}
    return helpers, notes


def helper_names(helpers_src: str) -> list[str]:
    """The public top-level function names of ``helpers.py`` (what the model can call)."""
    import ast  # noqa: PLC0415
    try:
        tree = ast.parse(helpers_src or "")
    except SyntaxError:
        return []
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]


# Run before the direct approach's notes code when it is loaded again: put the two original doc
# lookups back, so the notes wrap them once, never a wrapper. A name test, not globals(): the REPL's
# code guard refuses globals().
_RESTORE_DOC_LOOKUPS = '''try:
    _cap_orig_show_api_doc
except NameError:
    pass
else:
    apis.api_docs.show_api_doc = _cap_orig_show_api_doc
    apis.api_docs.show_api_descriptions = _cap_orig_show_api_descriptions
'''

# Counts the loads in this world and prints the count, so init_repl can tell the model that a repeat
# call changed nothing (a weak model calls the tool again and again otherwise). Same name test as above.
LOADS_MARKER = "aw-repl-loads:"   # init_repl.py's _LOADS matches it
_COUNT_LOADS = f'''try:
    _cap_repl_loads
except NameError:
    _cap_repl_loads = 0
_cap_repl_loads = _cap_repl_loads + 1
print("{LOADS_MARKER}", _cap_repl_loads)
'''


def repl_code(helpers_src: str, notes: dict[str, str]) -> str:
    """The REPL code for a candidate's ``repl/`` files: the direct approach's own setup code
    (``aw_agent.setup_snippet``, so both approaches load helpers and notes identically), made safe to
    run more than once, plus a load counter. Empty when there is nothing to load (the seed)."""
    import aw_agent  # noqa: PLC0415  (the direct approach's harness, copied next to this file)
    code = aw_agent.setup_snippet(helpers_src, notes)
    if not code:
        return ""
    if notes:
        code = _RESTORE_DOC_LOOKUPS + "\n" + code
    return code + "\n" + _COUNT_LOADS


def build_init_repl(skill_dir: Path) -> str:
    """The ``scripts/init_repl.py`` source for a candidate, or "" when its ``repl/`` loads nothing."""
    helpers, notes = read_repl(skill_dir)
    code = repl_code(helpers, notes)
    if not code:
        return ""
    src = INIT_REPL_TEMPLATE.read_text(encoding="utf-8")
    for slot, value in (("__AW_REPL_CODE__", code), ("__AW_REPL_HELPERS__", helper_names(helpers)),
                        ("__AW_REPL_HAS_NOTES__", bool(notes))):
        quoted = f'"{slot}"'
        if src.count(quoted) != 1:
            raise RuntimeError(f"{INIT_REPL_TEMPLATE.name}: slot {slot} must appear exactly once")
        src = src.replace(quoted, repr(value))
    return src


INIT_REPL_LINE = ("Call the init_repl tool once, at the very start of the task, to load them; never call it "
                  "again after that.")
INIT_REPL_NOTES_LINE = ("Call the init_repl tool once, at the very start of the task: it adds API notes to "
                        "apis.api_docs.show_api_doc / show_api_descriptions; never call it again after that.")


def repl_notice(skill_dir: Path) -> str:
    """What the model is told about the candidate's repl/, or "" when it loads nothing (the seed).

    The helper sentence is the direct approach's own (``aw_agent.helper_notice``: every public helper
    with its positional parameters), generated from ``helpers.py`` exactly as direct generates it;
    only the ``init_repl`` instruction is added, since here the helpers are loaded by that tool.
    """
    import aw_agent  # noqa: PLC0415
    helpers, notes = read_repl(skill_dir)
    if not repl_code(helpers, notes):
        return ""
    notice = aw_agent.helper_notice(helpers)
    return f"{notice} {INIT_REPL_LINE}" if notice else INIT_REPL_NOTES_LINE


def deployed_skill_md(skill_dir: Path) -> str:
    """The SKILL.md the store gets: the candidate's, plus ``repl_notice`` as its last paragraph.

    Direct puts its helper sentence at the end of the prompt, just before the final task block; the
    blackbox SKILL.md ends where the client's final task turn begins, so its end is the same place.
    """
    text = (Path(skill_dir) / "SKILL.md").read_text(encoding="utf-8")
    notice = repl_notice(skill_dir)
    return f"{text.rstrip()}\n\n{notice}\n" if notice else text


# ---- deploying a candidate ---------------------------------------------------------------------
def skill_digest(skill_dir: Path) -> str:
    """A content hash of what is deployed (SKILL.md and the repl/ files): which candidate SPA serves."""
    h = hashlib.sha256((Path(skill_dir) / "SKILL.md").read_bytes())
    for name in REPL_FILES:
        f = Path(skill_dir) / REPL_DIR / name
        h.update(b"\0" + name.encode() + b"\0" + (f.read_bytes() if f.is_file() else b""))
    return h.hexdigest()


_deploy_lock = threading.Lock()
_deployed = {"digest": None}


def deploy(skill_dir: Path) -> bool:
    """Make ``skill_dir`` the store's only skill and restart SPA bound to it.

    Idempotent per candidate content: a second call with the same SKILL.md and repl/ does nothing (SPA serves
    one skill for its whole lifetime, so only a new candidate needs a restart). Returns True when it
    (re)deployed. Raises on any store or SPA failure.
    """
    digest = skill_digest(skill_dir)
    with _deploy_lock:
        if _deployed["digest"] == digest:
            return False
        bb = blackbox_env()
        upstream = spa_upstream_env()
        bb.start_store()
        # The store imports EVERY file of the folder (each non-code file becomes an injected snippet,
        # each public function a tool), and the candidate folder also holds the harness's own files -
        # so import a copy with SKILL.md and, when repl/ loads something, the init_repl tool alone.
        # That SKILL.md then ends with the generated helper sentence (deployed_skill_md).
        stage = Path(tempfile.mkdtemp(prefix="aw_bb_skill_")) / SKILL_NAME
        stage.mkdir()
        (stage / "SKILL.md").write_text(deployed_skill_md(skill_dir), encoding="utf-8")
        tool = build_init_repl(skill_dir)
        if tool:
            (stage / "scripts").mkdir()
            (stage / "scripts" / f"{INIT_REPL}.py").write_text(tool, encoding="utf-8")
        try:
            bb.reset_store_to_skill(stage, SKILL_NAME, bb.Protection())
        finally:
            shutil.rmtree(stage.parent, ignore_errors=True)
        bb.restart_spa(SKILL_NAME, **upstream)
        _deployed["digest"] = digest
        return True


def spa_base_url() -> str:
    return os.environ.get("SPA_BASE_URL", "http://127.0.0.1:7000").rstrip("/")


def disconnect(headers: dict, spa_url: str | None = None, timeout: float = 60) -> bool:
    """Release the proxy's per-episode state (its vMCP server and trajectory) for headers' env_id.

    A session is one episode (one trial). The worker calls this when its episode ends, and the adapter calls it again for any episode that did
    not confirm it (killed at the wall cap, crashed): without that, the proxy keeps that env_id's
    server for the rest of its life. Idempotent: the proxy answers 200 for an env_id it no longer holds.
    Returns whether the proxy confirmed it. AppWorld needs no trajectory tailoring: its agent writes
    code instead of calling proxy tools, so the proxy's trajectory has nothing to merge.
    """
    req = urllib.request.Request((spa_url or spa_base_url()) + "/disconnect", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 — cleanup only: report it, never raise
        return False


# --- the remote REPL: one AppWorld environment server per trial -------------------------------
# An AppWorld environment server holds ONE world, so parallel trials need one server each. The
# adapter owns their ports and their lifetime: start at trial start, stop when the trial ends (the
# server process holds the world's databases in memory), and sweep any leftover at batch end.
# Ports come from a reserved range (AW_ENV_PORTS, default 9100-9399) that must not overlap the
# Skillberry Store (8000), the proxy (7000/7001) or the dashboards.

_ports_lock = threading.Lock()
_ports_in_use: set[int] = set()
_live: dict[int, subprocess.Popen] = {}


def remote_repl_enabled(environ: dict[str, str] | None = None) -> bool:
    """Run each trial's world on its own environment server (default), or in-process (AW_REMOTE_REPL=0)."""
    env = os.environ if environ is None else environ
    return str(env.get("AW_REMOTE_REPL", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _port_range(environ: dict[str, str] | None = None) -> range:
    env = os.environ if environ is None else environ
    lo, _, hi = str(env.get("AW_ENV_PORTS", "9100-9399")).partition("-")
    return range(int(lo), int(hi or lo) + 1)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def reserve_port(environ: dict[str, str] | None = None) -> int:
    """A port from the reserved range that no live trial holds and nothing else is bound to."""
    with _ports_lock:
        for port in _port_range(environ):
            if port not in _ports_in_use and _port_free(port):
                _ports_in_use.add(port)
                return port
    raise RuntimeError("no free port left for an AppWorld environment server (AW_ENV_PORTS)")


def release_port(port: int) -> None:
    with _ports_lock:
        _ports_in_use.discard(port)


def env_server_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _server_up(port: int) -> bool:
    try:
        with urllib.request.urlopen(env_server_url(port) + "/", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def start_env_server(appworld_root: str, env: dict[str, str], timeout_s: float = 120.0) -> int:
    """Start one environment server for one trial; return its port once it answers.

    ``env`` is the episode worker's allowlisted environment (no model key). Raises RuntimeError
    when the server does not come up in time; then its port is released and its process stopped.
    """
    port = reserve_port()
    proc = subprocess.Popen([sys.executable, str(_HERE / "aw_env_server.py"), str(port), appworld_root],
                            env=dict(env, APPWORLD_ROOT=appworld_root),
                            # its own output can carry task data: never kept
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    _live[port] = proc
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if proc.poll() is not None:
            break
        if _server_up(port):
            return port
        time.sleep(0.5)
    stop_env_server(port)
    raise RuntimeError(f"AppWorld environment server on port {port} did not come up in {timeout_s:.0f}s")


def stop_env_server(port: int, timeout_s: float = 15.0) -> bool:
    """Stop the trial's server and free its memory and port. Idempotent; True if one was running."""
    proc = _live.pop(port, None)
    try:
        if proc is None:
            return False
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout_s)
        return True
    finally:
        release_port(port)


def stop_all_env_servers() -> list[int]:
    """The batch-end sweep: stop every server this process still holds. Returns their ports."""
    ports = list(_live)
    for port in ports:
        stop_env_server(port)
    return ports
