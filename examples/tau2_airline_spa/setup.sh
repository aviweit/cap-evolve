#!/usr/bin/env bash
# Onboard tau2-bench airline (SPA variant) as a NEW benchmark and prepare it
# for optimization.
#
# This sets up the full SPA stack: Skillberry Store + Skillberry Proxy-Agent +
# tau2 Environment Manager, imports primitive tools, and verifies the adapter.
#
#   bash examples/tau2_airline_spa/setup.sh
#
set -uo pipefail

EX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$EX_DIR/../.." && pwd)"
VENV="$REPO/.venv"
PY="$VENV/bin/python"
PIP_INDEX="${PIP_INDEX:-https://pypi.org/simple}"

# Vendor directories for services
VENDOR="$REPO/vendor"
STORE_DIR="${SKILLBERRY_STORE_DIR:-$VENDOR/skillberry-store}"
AGENT_DIR="${SKILLBERRY_AGENT_DIR:-$VENDOR/skillberry-agent}"
BENCHMARKS_DIR="${SKILLBERRY_BENCHMARKS_DIR:-$VENDOR/skillberry-benchmarks}"
TAU2_DIR="$BENCHMARKS_DIR/tau2/tau2-bench"

# Ports
STORE_PORT="${SKILLBERRY_STORE_PORT:-8000}"
SPA_PORT="${SKILLBERRY_AGENT_PORT:-7000}"
ENV_MGR_PORT="${TAU2_ENV_MANAGER_PORT:-8004}"

# Pinned versions (reproducibility)
STORE_TAG="${SKILLBERRY_STORE_TAG:-0.2.1}"
BENCHMARKS_COMMIT="${SKILLBERRY_BENCHMARKS_COMMIT:-a3a83266008275e9d800fd709927fa3dc4f23ec5}"
AGENT_COMMIT="${SKILLBERRY_AGENT_COMMIT:-e359494f18267e339f9561acbd7a930e3b51189e}"

# SPA configuration defaults (can be overridden by .env or exported vars)
SPA_PROVIDER_NAME="${SPA_PROVIDER_NAME:-litellm}"
SPA_MODEL_NAME="${SPA_MODEL_NAME:-openai/aws/gpt-oss-120b}"

say(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
die(){ printf '\n\033[1;31mSETUP FAILED: %s\033[0m\n' "$*" >&2; exit 1; }

# Load repo-root .env into shell (no overwrite of already-exported vars)
if [ -f "$REPO/.env" ]; then
  while IFS= read -r _line || [ -n "$_line" ]; do
    _line="${_line#"${_line%%[![:space:]]*}"}"   # ltrim whitespace
    # skip blank lines and comments
    [[ -z "$_line" || "$_line" == \#* || "$_line" != *=* ]] && continue
    _key="${_line%%=*}"
    _val="${_line#*=}"
    # strip surrounding quotes
    _val="${_val%\"}" ; _val="${_val#\"}"
    _val="${_val%\'}" ; _val="${_val#\'}"
    [ -z "${!_key+x}" ] && export "$_key=$_val"
  done < "$REPO/.env"
fi

wait_for_port(){
  local port="$1" name="$2" max="${3:-20}"
  for i in $(seq 1 "$max"); do
    if curl -sf "http://localhost:$port/health" >/dev/null 2>&1 ||
       curl -sf "http://localhost:$port/docs" >/dev/null 2>&1 ||
       curl -sf "http://localhost:$port/" >/dev/null 2>&1; then
      echo "  ✓ $name responsive on port $port"
      return 0
    fi
    echo "  waiting for $name... (attempt $i/$max)"
    sleep 5
  done
  die "$name failed to start on port $port"
}

# ---------------------------------------------------------------------------
say "1/7  Install cap-evolve (Python venv + core CLI)"
[ -x "$PY" ] || python3 -m venv "$VENV" || die "could not create venv"
"$PY" -m pip install -q --index-url "$PIP_INDEX" --upgrade pip
"$PY" -m pip install -q --index-url "$PIP_INDEX" -e "$REPO/core" || die "pip install ./core failed"
"$VENV/bin/cap-evolve" version || die "cap-evolve CLI not available"
echo "  ✓ cap-evolve installed"

# ---------------------------------------------------------------------------
say "1.5/7  Check required credentials"
_require_env() {
  local var="$1" desc="$2"
  if [ -z "${!var:-}" ]; then
    die "$var is not set — $desc. Set it in $REPO/.env or export it."
  fi
}
# Skillberry Proxy-Agent (SPA) — these always have defaults set above, just echo them
echo "  SPA_PROVIDER_NAME=$SPA_PROVIDER_NAME"
echo "  SPA_MODEL_NAME=$SPA_MODEL_NAME"
# tau2-bench / upstream LLM
_require_env OPENAI_API_KEY  "needed for the upstream LLM API key"
_require_env OPENAI_API_BASE "needed for the upstream LLM endpoint URL"
_require_env OPENAI_BASE_URL "needed for the upstream LLM base URL (same value as OPENAI_API_BASE)"
echo "  ✓ all required credentials present"

# ---------------------------------------------------------------------------
say "2/7  Clone + install tau2-bench (from skillberry-benchmarks @ $BENCHMARKS_COMMIT)"
if [ ! -d "$BENCHMARKS_DIR/.git" ]; then
  echo "  cloning skillberry-benchmarks -> $BENCHMARKS_DIR"
  git clone https://github.com/skillberry-ai/skillberry-benchmarks.git "$BENCHMARKS_DIR" \
    || die "git clone skillberry-benchmarks failed"
  git -C "$BENCHMARKS_DIR" checkout "$BENCHMARKS_COMMIT" \
    || die "git checkout $BENCHMARKS_COMMIT failed"
fi
if [ ! -d "$TAU2_DIR" ]; then
  die "tau2-bench directory not found at $TAU2_DIR"
fi
"$PY" -m pip install -q --index-url "$PIP_INDEX" -e "$TAU2_DIR[skillberry]" || die "pip install tau2-bench failed"
TAU2_SHA="$(git -C "$BENCHMARKS_DIR" rev-parse HEAD)"
"$PY" -c "import tau2" >/dev/null 2>&1 || die "tau2 import failed"
echo "  ✓ tau2-bench installed @ $TAU2_SHA (from skillberry-benchmarks)"

# ---------------------------------------------------------------------------
say "3/7  Clone + start Skillberry Store (tag $STORE_TAG, port $STORE_PORT)"
mkdir -p "$VENDOR"
if [ ! -d "$STORE_DIR/.git" ]; then
  echo "  cloning skillberry-store @ $STORE_TAG -> $STORE_DIR"
  git clone --branch "$STORE_TAG" --depth 1 \
    https://github.com/skillberry-ai/skillberry-store.git "$STORE_DIR" \
    || die "git clone skillberry-store failed"
fi
cd "$STORE_DIR"
if [ ! -d ".venv" ]; then
  python3.11 -m venv .venv || python3 -m venv .venv || die "store venv creation failed"
fi
. .venv/bin/activate
if [ ! -f ".stamps/install-requirements-" ] 2>/dev/null; then
  make install-requirements || pip install -e . || die "store install failed"
fi
deactivate
# Start store if not already running
if ! curl -sf "http://localhost:$STORE_PORT/health" >/dev/null 2>&1; then
  # Remove stale sentinel that blocks startup when no process is actually running
  rm -f /tmp/skillberry-store-service.pid
  echo "  starting store..."
  nohup bash -c "cd $STORE_DIR && . .venv/bin/activate && EXECUTE_PYTHON_LOCALLY=True make run" > store.log 2>&1 &
  sleep 5
fi
cd "$REPO"
wait_for_port "$STORE_PORT" "skillberry-store" 60

# ---------------------------------------------------------------------------
say "4/7  Start tau2 Environment Manager (port $ENV_MGR_PORT)"
if lsof -Pi :"$ENV_MGR_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "  ✓ env manager already running on port $ENV_MGR_PORT"
else
  echo "  starting env manager..."
  # tau2 is installed in cap-evolve's venv; start the EnvironmentManager inline
  nohup "$PY" -c "
import asyncio
from tau2.orchestrator.environment_manager import EnvironmentManager

async def runner():
    manager = EnvironmentManager(host='127.0.0.1', port=$ENV_MGR_PORT)
    await manager.run()

asyncio.run(runner())
" > "$REPO/env_manager.log" 2>&1 &
  sleep 5
  if ! lsof -Pi :"$ENV_MGR_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "  env manager log:"
    tail -20 "$REPO/env_manager.log" 2>/dev/null || true
    die "env manager failed to start on port $ENV_MGR_PORT"
  fi
  echo "  ✓ env manager started on port $ENV_MGR_PORT"
fi

# ---------------------------------------------------------------------------
say "5/7  Purge store + import primitive tools (14 functions)"
# Purge
curl -s -X DELETE "http://localhost:$STORE_PORT/admin/purge-all" >/dev/null 2>&1 || true
echo "  store purged"

# Import primitive tools from the seed_capability
PRIM_TOOLS="$EX_DIR/seed_capability/primitive_skill/scripts/tau2_primitive_functions.py"
if [ ! -f "$PRIM_TOOLS" ]; then
  die "primitive tools file not found: $PRIM_TOOLS"
fi

# Extract public function names
FUNC_NAMES=$("$PY" -c "
import ast, sys
with open('$PRIM_TOOLS') as f:
    tree = ast.parse(f.read())
funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and not n.name.startswith('_')]
print(' '.join(funcs))
") || die "failed to parse primitive tools"

TOOL_COUNT=0
for func_name in $FUNC_NAMES; do
  RESPONSE=$(curl -s -X POST \
    "http://localhost:$STORE_PORT/tools/add?selected_func=$func_name&update=true" \
    -F "tool=@$PRIM_TOOLS" 2>&1)
  if echo "$RESPONSE" | grep -q '"uuid"'; then
    TOOL_COUNT=$((TOOL_COUNT + 1))
  else
    echo "  ⚠ failed to import $func_name"
  fi
done
echo "  ✓ imported $TOOL_COUNT primitive tools"
[ "$TOOL_COUNT" -gt 0 ] || die "no primitive tools imported"

# Import primitive_skill as a skill package so SPA can serve it
PRIM_SKILL_DIR="$EX_DIR/seed_capability/primitive_skill"
echo "  importing primitive_skill..."
SKILL_RESP=$(curl -s -X POST "http://localhost:$STORE_PORT/skills/import-anthropic" \
  -F "source_type=folder" \
  -F "folder_path=$(cd "$PRIM_SKILL_DIR" && pwd)" \
  -F "snippet_mode=file" 2>&1)
if echo "$SKILL_RESP" | grep -qE '"success"|"skill_name"'; then
  echo "  ✓ primitive_skill imported to store"
else
  echo "  ⚠ primitive_skill import response: $SKILL_RESP"
  die "failed to import primitive_skill"
fi

# ---------------------------------------------------------------------------
say "6/7  Clone + start Skillberry Proxy-Agent (@ $AGENT_COMMIT, port $SPA_PORT)"
if [ ! -d "$AGENT_DIR/.git" ]; then
  echo "  cloning skillberry-agent -> $AGENT_DIR"
  git clone https://github.com/skillberry-ai/skillberry-agent.git "$AGENT_DIR" \
    || die "git clone skillberry-agent failed"
  git -C "$AGENT_DIR" checkout "$AGENT_COMMIT" \
    || die "git checkout $AGENT_COMMIT failed"
fi
cd "$AGENT_DIR"
if [ ! -d ".venv" ]; then
  python3.11 -m venv .venv || python3 -m venv .venv || die "agent venv creation failed"
fi
. .venv/bin/activate
if [ ! -f ".stamps/install-requirements-" ] 2>/dev/null; then
  make install-requirements || pip install -e . || die "agent install failed"
fi
deactivate
# Start SPA if not already running
if ! curl -sf "http://localhost:$SPA_PORT/health" >/dev/null 2>&1; then
  echo "  starting SPA with SKILL_NAME=primitive_skill..."
  export SKILL_NAME=primitive_skill
  export USE_AGENT_TOOLS=false
  export USE_AGENT_PROMPTS=true
  export MCP_PROMPTS_POSITION=postfix
  export SPA_PROVIDER_NAME="$SPA_PROVIDER_NAME"
  export SPA_MODEL_NAME="$SPA_MODEL_NAME"
  # Ensure LLM credentials are available for SPA's provider
  if [ -z "${OPENAI_API_KEY:-}" ]; then
    die "OPENAI_API_KEY must be set (SPA's litellm provider needs it)"
  fi
  nohup bash -c "cd $AGENT_DIR && . .venv/bin/activate && make run" > proxy-agent.log 2>&1 &
  sleep 5
fi
cd "$REPO"
wait_for_port "$SPA_PORT" "skillberry-proxy-agent"

# ---------------------------------------------------------------------------
say "7/7  Scaffold cap-evolve project + wire adapter + check"
# Scaffold
"$PY" "$REPO/skills/phases/intake/scripts/run.py" --base "$REPO/.capevolve" --workdir "$REPO" --force >/dev/null 2>&1 \
  || true
PROJECT="$REPO/.capevolve/project"
mkdir -p "$PROJECT/adapters"

# Wire
cp "$EX_DIR/adapters/adapter.py" "$EX_DIR/adapters/spa_env.py" "$PROJECT/adapters/"
rm -rf "$PROJECT/seed_capability"
cp -R "$EX_DIR/seed_capability" "$PROJECT/seed_capability"
cp "$EX_DIR/capevolve.yaml" "$EX_DIR/split_ids.json" "$PROJECT/"

# Export service dirs for the adapter
export SKILLBERRY_AGENT_DIR="$AGENT_DIR"
export SKILLBERRY_STORE_DIR="$STORE_DIR"

echo "  project scaffolded at $PROJECT"

PYTHONPATH="$PROJECT/adapters" "$VENV/bin/cap-evolve" check "$PROJECT" || die "cap-evolve check did not pass"

printf '\n\033[1;32mREADY.\033[0m  Next:\n'
printf '  SKILLBERRY_AGENT_DIR=%s \\\n' "$AGENT_DIR"
printf '  SKILLBERRY_STORE_DIR=%s \\\n' "$STORE_DIR"
printf '  cap-evolve run --spec %s/capevolve.yaml\n' "$PROJECT"
