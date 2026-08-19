#!/usr/bin/env bash
# Tear down the tau2_airline_spa environment: kill services, remove vendor dirs + venvs.
#
#   bash examples/tau2_airline_spa/teardown.sh
#
set -uo pipefail

EX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$EX_DIR/../.." && pwd)"
VENDOR="$REPO/vendor"

STORE_PORT="${SKILLBERRY_STORE_PORT:-8000}"
ENV_MGR_PORT="${TAU2_ENV_MANAGER_PORT:-8004}"
SPA_PORT="7000"

# Sentinels written by skillberry-common/scripts/start-service.sh. They hold the
# PID of the process the service itself started, which is a far safer handle than
# "whoever owns the port".
SPA_PID_FILE="/tmp/skillberry-agent-service.pid"
STORE_PID_FILE="/tmp/skillberry-store-service.pid"

say(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

# Terminate a PID: SIGTERM, then SIGKILL only if it does not go away.
term_pid(){
  local pid="$1" name="$2"
  kill "$pid" 2>/dev/null || return 1
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || { echo "  ✓ stopped $name (PID $pid)"; return 0; }
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null || true
  echo "  ✓ killed $name (PID $pid)"
}

# Stop a service via the PID its own sentinel recorded, falling back to the port
# ONLY for a process that matches "$3" — never a blind `kill -9` on whoever holds
# the port. On macOS, port 7000 belongs to ControlCenter (AirPlay Receiver), and
# SIGKILLing a system process because it squats our port is not acceptable.
stop_service(){
  local name="$1" port="$2" pattern="$3" pidfile="${4:-}"
  local pid pids stopped=0

  if [ -n "$pidfile" ] && [ -f "$pidfile" ]; then
    pid=$(head -1 "$pidfile" 2>/dev/null | tr -dc '0-9')
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      term_pid "$pid" "$name" && stopped=1
    fi
    rm -f "$pidfile"
  fi

  if [ "$stopped" -eq 0 ]; then
    # -sTCP:LISTEN: without it lsof also returns CLIENTS of the port (cap-evolve's
    # own runner talks to SPA), which must never be considered the service.
    pids=$(lsof -ti :"$port" -sTCP:LISTEN 2>/dev/null || true)
    for pid in $pids; do
      if ps -p "$pid" -o args= 2>/dev/null | grep -q -- "$pattern"; then
        term_pid "$pid" "$name" && stopped=1
      else
        printf '  ! port %s held by PID %s (%s) — not %s, leaving it alone\n' \
          "$port" "$pid" "$(ps -p "$pid" -o args= 2>/dev/null | head -1)" "$name"
      fi
    done
  fi

  [ "$stopped" -eq 1 ] || echo "  - $name not running (port $port)"
}

say "1/4  Stop services"
stop_service "skillberry-proxy-agent" "$SPA_PORT"     "-m main"              "$SPA_PID_FILE"
stop_service "skillberry-store"       "$STORE_PORT"   "skillberry_store.main" "$STORE_PID_FILE"
stop_service "tau2-env-manager"       "$ENV_MGR_PORT" "EnvironmentManager"

# Sentinels are removed by stop_service; clear them unconditionally in case a
# service died without cleaning up (a stale sentinel blocks the next `make run`).
rm -f "$STORE_PID_FILE" "$SPA_PID_FILE"

say "2/4  Remove vendor directory ($VENDOR)"
if [ -d "$VENDOR" ]; then
  rm -rf "$VENDOR"
  echo "  ✓ removed $VENDOR"
else
  echo "  - $VENDOR not found"
fi

say "3/4  Remove cap-evolve venv ($REPO/.venv)"
if [ -d "$REPO/.venv" ]; then
  rm -rf "$REPO/.venv"
  echo "  ✓ removed .venv"
else
  echo "  - .venv not found"
fi

say "4/4  Remove scaffolded project ($REPO/.capevolve)"
if [ -d "$REPO/.capevolve" ]; then
  rm -rf "$REPO/.capevolve"
  echo "  ✓ removed .capevolve"
else
  echo "  - .capevolve not found"
fi

# Clean up logs
rm -f "$REPO/env_manager.log"

printf '\n\033[1;32mTEARDOWN COMPLETE.\033[0m\n'
