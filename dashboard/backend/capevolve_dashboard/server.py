"""Idempotent launcher for the dashboard server + CLI entrypoint."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
#: Bind addresses a browser cannot dial as-is; the URL we hand a human uses loopback.
_WILDCARD_HOSTS = ("0.0.0.0", "::", "*", "")


def resolve_host(cli_arg: str | None = None) -> str:
    """Address to bind. CLI flag > ``CAPEVOLVE_DASHBOARD_HOST`` > loopback.

    Loopback-only by default (the dashboard serves a project's run artifacts). Use
    ``0.0.0.0`` to reach it from another machine — a remote box, a container, a VM —
    none of which can reach a 127.0.0.1-only listener.
    """
    host = (cli_arg or os.environ.get("CAPEVOLVE_DASHBOARD_HOST") or "").strip()
    return host or DEFAULT_HOST


def browsable_host(host: str) -> str:
    """Host a browser on this machine should dial for a server bound to ``host``."""
    return DEFAULT_HOST if host.strip() in _WILDCARD_HOSTS else host.strip()


def url_for(port: int, host: str = DEFAULT_HOST) -> str:
    return f"http://{browsable_host(host)}:{port}"


def is_up(port: int, host: str = DEFAULT_HOST) -> bool:
    try:
        with urllib.request.urlopen(f"{url_for(port, host)}/api/health", timeout=0.5) as r:
            return r.status == 200
    except Exception:
        return False


def resolve_static_dir() -> Path | None:
    # server.py(file) -> capevolve_dashboard[0] -> backend[1] -> dashboard[2];
    # the built SPA lives at dashboard/frontend/dist.
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    return dist if dist.is_dir() else None


def ensure_up(base_dir, port: int = 7878, open_browser: bool = True,
              host: str | None = None) -> str:
    host = resolve_host(host)
    url = url_for(port, host)
    if is_up(port, browsable_host(host)):
        if open_browser:
            webbrowser.open(url)
        return url
    env = dict(os.environ, CAPEVOLVE_BASE_DIR=str(base_dir))
    static = resolve_static_dir()
    if static:
        env["CAPEVOLVE_STATIC_DIR"] = str(static)
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "capevolve_dashboard.asgi:app",
         "--host", host, "--port", str(port), "--log-level", "warning"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if open_browser:
        webbrowser.open(url)
    return url


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cap-evolve-dashboard")
    p.add_argument("--base", default=".capevolve")
    p.add_argument("--port", type=int, default=7878)
    p.add_argument("--host", default=None,
                   help="bind address (default 127.0.0.1, or $CAPEVOLVE_DASHBOARD_HOST); "
                        "use 0.0.0.0 to reach the dashboard from another machine")
    p.add_argument("--no-open", action="store_true")
    args = p.parse_args(argv)
    url = ensure_up(args.base, port=args.port, open_browser=not args.no_open,
                    host=args.host)
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
