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
SPA_PORT="${SKILLBERRY_AGENT_PORT:-7000}"
ENV_MGR_PORT="${TAU2_ENV_MANAGER_PORT:-8004}"

say(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

kill_port(){
  local port="$1" name="$2"
  local pids
  pids=$(lsof -ti :"$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "$pids" | xargs kill -9 2>/dev/null || true
    echo "  ✓ killed $name (port $port)"
  else
    echo "  - $name not running (port $port)"
  fi
}

say "1/4  Stop services"
kill_port "$SPA_PORT" "skillberry-proxy-agent"
kill_port "$STORE_PORT" "skillberry-store"
kill_port "$ENV_MGR_PORT" "tau2-env-manager"

# Remove stale sentinel files
rm -f /tmp/skillberry-store-service.pid
rm -f /tmp/skillberry-agent-service.pid

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
