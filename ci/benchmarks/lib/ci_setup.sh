#!/usr/bin/env bash
# ci_setup.sh — idempotently prepare the self-hosted runner for ONE benchmark.
# Creates a cached py3.12 venv + benchmark deps/clones OUTSIDE the checkout (so they
# survive between jobs), ensures the claude-code optimizer CLI is installed, preflights
# the model-gateway budget (fail fast on 429 budget_exceeded rather than score all-0.000),
# and exports CAPEVOLVE_PY / SKILLSBENCH_SRC / PATH to $GITHUB_ENV.
#
#   ci_setup.sh <bench>
set -euo pipefail
BENCH="${1:?bench}"
CACHE="${CAPEVOLVE_CI_CACHE:-$HOME/.cache/capevolve-ci}"
VENV="$CACHE/venv"
CAPEVOLVE_PY="$VENV/bin/python"
IDX="--index-url https://pypi.org/simple"
mkdir -p "$CACHE"

command -v uv >/dev/null || { echo "::error:: uv is required on the runner"; exit 1; }
[ -x "$CAPEVOLVE_PY" ] || uv venv --python 3.12 "$VENV"

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$LIB_DIR/../../.." && pwd)"
uv pip install -p "$CAPEVOLVE_PY" -q $IDX "$REPO/core" litellm

case "$BENCH" in
  swebench)
    # PINNED. These were unpinned, so the grading harness could change under us between
    # runs with nothing in the log to say it had. Pinned to what skillberry-1 was actually
    # running on 2026-08-07, verified ON the runner to resolve all 5 smoke ids from
    # princeton-nlp/SWE-bench_Lite (300-row test split, 0 missing). datasets 5.0.1 was
    # verified equivalent off-runner; 5.0.0 is pinned because it is the observed-good state
    # here and the point of a pin is reproducibility, not an untested upgrade.
    uv pip install -p "$CAPEVOLVE_PY" -q $IDX "swebench==4.1.0" "datasets==5.0.0"
    command -v harbor >/dev/null 2>&1 || uv tool install $IDX harbor >/dev/null 2>&1 || true

    # Dataset preflight — WARM ONCE, THEN GO OFFLINE.
    #
    # `run_evaluation` re-resolves the dataset over the HF Hub on EVERY call, and
    # `princeton-nlp/*` is a 307 redirect to `SWE-bench/*`, so each resolution is live Hub
    # traffic. HF rate-limits unauthenticated requests per source IP and this runner has no
    # HF_TOKEN (confirmed on skillberry-1). When resolution degrades mid-run the harness
    # raises
    #
    #     ValueError: Some instance IDs not found in dataset!
    #
    # AFTER every patch has already been generated. Run 31161200250 lost 11 minutes and
    # $3.44 of optimizer budget that way, then published `reward 0.000` as a real result.
    # Its timings show the shape: iteration 1's eval took 10m22s, then 29s, then 6s —
    # slow-and-retrying, then fast-failing.
    #
    # A warning is not enough, because the failure is transient: re-checked later on the
    # runner, all 5 ids resolve fine, so a preflight that merely verifies would have passed
    # and the run would still have died. So: resolve every dataset the run needs ONCE here
    # (warming datasets' cache), fail loudly and cheaply if that can't be done, and then pin
    # the rest of the job to HF_HUB_OFFLINE so no later call can touch the Hub at all.
    # Verified on skillberry-1 that both datasets load from the warm cache with offline mode
    # enabled. This also makes the dataset immutable for the run, which is what a benchmark
    # wants anyway, and is consistent with pinning the harness above.
    if [ -n "${HF_TOKEN:-}" ]; then
      echo "HF Hub: authenticated (HF_TOKEN present)"
    else
      echo "HF Hub: unauthenticated (no HF_TOKEN) — warming the cache now and running offline"
    fi
    SWE_TASKS="$REPO/ci/benchmarks/swebench/${TIER:-smoke}/tasks.json"
    if [ -f "$SWE_TASKS" ]; then
      SWEBENCH_TASKS_JSON="$SWE_TASKS" "$CAPEVOLVE_PY" - <<'PY' || exit 1
import json, os, sys
ids = [t["id"] for t in json.load(open(os.environ["SWEBENCH_TASKS_JSON"]))]
split = os.environ.get("SWEBENCH_SPLIT", "test")
base = os.environ.get("SWEBENCH_DATASET", "princeton-nlp/SWE-bench_Lite")
# Oracle context is on by default in run_suite.sh, and it is a SECOND dataset that the
# adapter resolves over the Hub. Warm it too, or offline mode below breaks tasks().
wanted = [(base, ids)]
if os.environ.get("SWEBENCH_ORACLE", "1").strip().lower() in ("1", "true", "yes", "on"):
    wanted.append((os.environ.get("SWEBENCH_ORACLE_DATASET",
                                  "princeton-nlp/SWE-bench_Lite_oracle"), None))
import datasets, swebench
print(f"swebench {getattr(swebench,'__version__','?')} / datasets {datasets.__version__}"
      f" -> {split} split, checking {len(ids)} task id(s)")
from swebench.harness.utils import load_swebench_dataset
for name, want in wanted:
    try:
        got = load_swebench_dataset(name, split, want)
    except Exception as exc:
        print(f"::error:: cannot resolve {name}/{split}: {type(exc).__name__}: {str(exc)[:400]}")
        print("::error:: scoring would fail for EVERY task AFTER the agent had already run,")
        print("::error:: and the suite would report a 0.000 it never measured.")
        print("::error:: Check: HF Hub reachability/throttling (set the HF_TOKEN secret), or")
        print("::error:: a corrupt dataset cache on the runner (~/.cache/huggingface/datasets).")
        sys.exit(1)
    print(f"  warmed {name}: {len(got)} row(s)")
print("swebench dataset preflight OK — pinning the run to the warm cache (offline)")
PY
      # Only after a successful warm: no later Hub call, so no mid-run throttling.
      SWEBENCH_OFFLINE=1
    fi ;;
  tau2)
    [ -d "$CACHE/tau2-bench/.git" ] || git clone --depth 1 https://github.com/sierra-research/tau2-bench "$CACHE/tau2-bench"
    uv pip install -p "$CAPEVOLVE_PY" -q $IDX -e "$CACHE/tau2-bench" ;;
  skillsbench)
    uv tool install $IDX benchflow >/dev/null 2>&1 || true
    [ -d "$CACHE/skillsbench-src/.git" ] || GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/benchflow-ai/skillsbench "$CACHE/skillsbench-src" ;;
  spreadsheetbench)
    uv pip install -p "$CAPEVOLVE_PY" -q $IDX pandas openpyxl docker tornado requests
    command -v docker >/dev/null && docker info >/dev/null 2>&1 || {
      echo "::error:: docker daemon not reachable — spreadsheetbench runs each task in its own container"; exit 1; }
    if ! command -v libreoffice >/dev/null 2>&1 && ! command -v soffice >/dev/null 2>&1; then
      echo "::warning:: LibreOffice not found — formula-only cells won't be recalculated before scoring"
    fi
    SB_VARIANT="sample_200"
    # pilot's tasks are drawn from full's train split, so it needs the 912-task dataset too.
    case "${TIER:-smoke}" in full|pilot) SB_VARIANT="full_912";; esac
    SPREADSHEETBENCH_DATA_DIR="$(SPREADSHEETBENCH_VARIANT="$SB_VARIANT" "$REPO/ci/benchmarks/spreadsheetbench/fetch_data.sh" "$CACHE/spreadsheetbench-data")" ;;
esac

"$CAPEVOLVE_PY" -c "import cap_evolve; print('cap_evolve OK')"

# Ensure the claude-code optimizer CLI (the EDIT PROPOSER) is present. If a runner is
# reprovisioned/rebooted the global npm install can vanish; without `claude` the benchmark
# SILENTLY degrades — the optimizer fails every iteration with `cli_present:false`, no edit
# is proposed, and every task reports best=seed / reward 0.000 as if it had "optimized".
# Install idempotently into a user-writable prefix ($HOME/.local/bin is already on PATH and
# exported below), then HARD-FAIL if it is still unavailable so a broken runner is loud.
if ! command -v claude >/dev/null 2>&1; then
  command -v npm >/dev/null || { echo "::error:: npm required to install the claude-code optimizer"; exit 1; }
  echo "claude CLI missing — installing @anthropic-ai/claude-code into $HOME/.local"
  npm install -g --prefix "$HOME/.local" @anthropic-ai/claude-code
fi
export PATH="$HOME/.local/bin:$PATH"
command -v claude >/dev/null || {
  echo "::error:: claude-code optimizer CLI still unavailable after install — aborting."
  echo "::error:: (running anyway would silently yield best=seed / reward 0.000 on every task.)"
  exit 1
}
echo "claude-code optimizer: $(command -v claude) ($(claude --version 2>/dev/null | head -1))"

# Gateway budget preflight. The agent (gpt-oss) AND the optimizer (opus) share one
# LiteLLM gateway; when it hits its spend cap it returns 429 budget_exceeded and every
# rollout dies with INFRASTRUCTURE_ERROR → the whole suite silently scores 0.000 (looks
# identical to a real regression). Probe once and FAIL FAST rather than burn hours. Only
# hard-fails on the budget case; other transient errors are non-blocking.
if [ -n "${ANTHROPIC_BASE_URL:-}" ] && [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ] && command -v curl >/dev/null; then
  probe=/tmp/capevolve_budget_probe.$$.json
  code="$(curl -sS -m 30 -o "$probe" -w '%{http_code}' \
    "$ANTHROPIC_BASE_URL/chat/completions" \
    -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" -H 'Content-Type: application/json' \
    -d '{"model":"aws/gpt-oss-120b","messages":[{"role":"user","content":"ping"}],"max_tokens":1}' 2>/dev/null || echo 000)"
  if [ "$code" = "429" ] && grep -qi 'budget' "$probe" 2>/dev/null; then
    echo "::error:: model gateway is OVER BUDGET (HTTP 429 budget_exceeded) — aborting."
    echo "::error:: every rollout would score 0.000 as INFRASTRUCTURE_ERROR. Raise/reset the gateway budget."
    head -c 300 "$probe" 2>/dev/null; echo; rm -f "$probe"; exit 1
  fi
  rm -f "$probe"
  echo "gateway budget preflight: HTTP $code (not budget-blocked)"
fi

# Export for later workflow steps (no-op locally).
if [ -n "${GITHUB_ENV:-}" ]; then
  {
    echo "CAPEVOLVE_PY=$CAPEVOLVE_PY"
    echo "SKILLSBENCH_SRC=$CACHE/skillsbench-src"
    # Set only when the swebench dataset preflight above warmed the cache successfully.
    # Both names are exported because `datasets` moved the flag between versions and the
    # older one is still honoured; setting both is version-proof.
    if [ -n "${SWEBENCH_OFFLINE:-}" ]; then
      echo "HF_HUB_OFFLINE=1"
      echo "HF_DATASETS_OFFLINE=1"
    fi
    if [ -n "${SPREADSHEETBENCH_DATA_DIR:-}" ]; then echo "SPREADSHEETBENCH_DATA_DIR=$SPREADSHEETBENCH_DATA_DIR"; fi
    echo "PATH=$HOME/.local/bin:$PATH"
  } >> "$GITHUB_ENV"
fi
echo "ci_setup done for $BENCH (venv: $VENV)"
