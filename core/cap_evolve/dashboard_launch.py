"""Best-effort launcher that wires the (optional) live dashboard into the pipeline.

The stdlib-only core never imports the dashboard's web stack. Instead it spawns
the optional ``capevolve-dashboard`` package as a detached subprocess
(``python -m capevolve_dashboard.server``), which is idempotent (it reuses an
already-running server on the port). If that package isn't installed, launching
is a no-op with a friendly hint — the run is never affected.
"""
from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

MODES = ("auto", "report-only", "off")
DEFAULT_PORT = 7878
DEFAULT_HOST = "127.0.0.1"
#: Bind addresses that are not reachable as-is from a browser; the URL we print for
#: a human must fall back to loopback for these.
_WILDCARD_HOSTS = ("0.0.0.0", "::", "*", "")


def resolve_host(cli_arg: str | None = None) -> str:
    """Address the dashboard server binds. CLI flag > env var > loopback.

    Loopback-only by default: the dashboard exposes a project's run artifacts, so it
    should not be reachable off-box unless asked for. Set
    ``CAPEVOLVE_DASHBOARD_HOST=0.0.0.0`` (or pass ``--host``) when the browser lives
    somewhere other than the machine running the pipeline — a remote box, a container,
    a VM — since nothing outside it can reach a 127.0.0.1-only listener.
    """
    host = (cli_arg or os.environ.get("CAPEVOLVE_DASHBOARD_HOST") or "").strip()
    return host or DEFAULT_HOST


def browsable_host(host: str) -> str:
    """The host a browser on this machine should dial for a server bound to ``host``."""
    return DEFAULT_HOST if host.strip() in _WILDCARD_HOSTS else host.strip()


def _free_port(start: int, tries: int = 25, host: str = DEFAULT_HOST) -> int:
    """First free TCP port at/above ``start`` so we never reuse a stale server.

    The dashboard server binds a port and serves whatever ``--base`` it was given;
    if another (possibly unrelated) server already holds ``start``, our spawn would
    silently fail to bind and that stale server would keep serving the WRONG run.
    Picking a free port guarantees this run gets its own dashboard on its own base.

    Raises when every port in the scanned range is taken, rather than falling back
    to ``start`` — a caller that silently reused a taken port would print a URL that
    actually serves a stale, unrelated dashboard (observed live: ~25 leaked dashboard
    processes from old sessions squatting this whole range made every run's dashboard
    silently point at someone else's project). Callers decide how to surface this.
    """
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    raise RuntimeError(
        f"no free port in [{start}, {start + tries}) — {tries} candidates all taken; "
        "leaked dashboard processes from prior runs may be squatting this range")


def resolve_mode(cli_arg: str | None, spec_value: str | None, default: str = "auto") -> str:
    """Precedence: explicit CLI flag > spec field > default. Unknown → default."""
    for candidate in (cli_arg, spec_value, default):
        if candidate in MODES:
            return candidate
    return default


def is_available() -> bool:
    """True if the optional dashboard package is importable in this interpreter."""
    return importlib.util.find_spec("capevolve_dashboard") is not None


def launch_command(base_dir, port: int = DEFAULT_PORT, open_browser: bool = True,
                   host: str = DEFAULT_HOST) -> list[str]:
    """The argv that (idempotently) ensures the dashboard server is up."""
    cmd = [sys.executable, "-m", "capevolve_dashboard.server",
           "--base", str(base_dir), "--port", str(port), "--host", host]
    if not open_browser:
        cmd.append("--no-open")
    return cmd


def url_for(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> str:
    return f"http://{browsable_host(host)}:{port}"


def maybe_launch(base_dir, *, mode: str, port: int = DEFAULT_PORT,
                 open_browser: bool = True, host: str | None = None) -> dict:
    """Spawn the dashboard server unless mode is ``off``. Never raises.

    Returns a small status dict (``{"dashboard": url}`` or ``{"dashboard":
    "skipped", "reason": ...}``) suitable for printing as part of a phase summary.
    """
    if mode == "off":
        return {"dashboard": "off"}
    if not is_available():
        return {"dashboard": "skipped",
                "reason": "capevolve-dashboard not installed "
                          "(pip install -e dashboard/backend)"}
    host = resolve_host(host)
    try:
        # Avoid reusing a stale server squatting the default port. Probed on the same
        # address we will bind: a 127.0.0.1-only listener leaves 0.0.0.0:PORT bindable
        # on some platforms, so probing loopback for a wildcard bind can pick a port
        # uvicorn then fails to take.
        port = _free_port(port, host=host)
    except RuntimeError as e:
        return {"dashboard": "error", "reason": str(e)}
    try:
        subprocess.Popen(
            launch_command(Path(base_dir), port=port, open_browser=open_browser, host=host),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:  # noqa: BLE001 — launching must never break the run
        return {"dashboard": "error", "reason": str(e)}
    return {"dashboard": url_for(port, host)}
