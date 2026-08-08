#!/usr/bin/env bash
# ci_setup.sh — idempotently prepare the self-hosted runner for ONE benchmark.
# Creates a cached py3.12 venv + benchmark deps/clones OUTSIDE the checkout (so they
# survive between jobs), ensures the claude-code optimizer CLI is installed, preflights the
# model gateway (fail fast when the SELECTED models are not entitled, or the gateway is over
# budget, rather than score all-0.000), and exports CAPEVOLVE_PY / SKILLSBENCH_SRC / PATH to
# $GITHUB_ENV.
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
    # Harbor is the DEFAULT swebench adapter. It runs a real coding agent (claude-code)
    # inside its own sandboxed containers and manages its own dataset, so NONE of the
    # litellm path's machinery applies: no HuggingFace dataset, no oracle context, no
    # swebench/sweb.eval.* images, no per-instance patch application. The litellm branch
    # below is kept only until the harbor path is confirmed green, then removed.
    #
    # Why the switch: the full tier's 250 task ids come from SWE-bench_Verified, but oracle
    # context only exists for Lite (46 of the 250), and single-shot blind patching is not a
    # meaningful target for a mid-tier model. Harbor needs no oracle because the agent
    # explores the repo itself.
    SWE_ADAPTER="${SWEBENCH_ADAPTER:-harbor}"
    echo "swebench adapter: $SWE_ADAPTER"
    if [ "$SWE_ADAPTER" = "harbor" ]; then
      # The adapter does `from capevolve_harbor import ...`; that package lives in this repo
      # and was never installed into the CI venv, so the harbor path would have died on
      # import. Install it explicitly.
      uv pip install -p "$CAPEVOLVE_PY" -q $IDX "$REPO/capevolve_harbor"
      "$CAPEVOLVE_PY" -c "import capevolve_harbor; print('capevolve_harbor OK')"
      command -v harbor >/dev/null 2>&1 || uv tool install $IDX harbor >/dev/null 2>&1 || true
      command -v harbor >/dev/null || {
        echo "::error:: harbor CLI unavailable — the harbor adapter cannot run a single task."
        exit 1; }
      echo "harbor: $(command -v harbor)"
      command -v docker >/dev/null && docker info >/dev/null 2>&1 || {
        echo "::error:: docker daemon not reachable — harbor runs every task in a container"
        exit 1; }
    else
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
    fi

    # Pre-pull the tier's eval images. swebench pulls
    # swebench/sweb.eval.x86_64.<instance> lazily, DURING grading, once per instance
    # (SWEBENCH_NAMESPACE=swebench). When a pull fails the instance never gets a container,
    # swebench files it under `error_ids`, and the harness still exits 0 — so a Docker Hub
    # hiccup or rate-limit becomes a silent per-instance hole in the measurement rather than
    # a visible failure. Observed across runs 31173670507 and 31179047624: the two instances
    # with no local image (django__django-11179, pytest-dev__pytest-7432) were the ones that
    # kept failing, while their manifests are present on Docker Hub (HTTP 200) — i.e. they
    # are pullable, just not reliably at eval time.
    #
    # Pull them up front instead: the images are then warm for every iteration, and a Hub
    # problem surfaces here, in seconds, naming the instances it will cost us.
    #
    # Instance -> image name: swebench replaces "__" with "_1776_"
    # (django__django-11179 -> sweb.eval.x86_64.django_1776_django-11179).
    if [ -f "$SWE_TASKS" ] && command -v docker >/dev/null 2>&1; then
      # Optional Docker Hub auth. Unauthenticated pulls are capped at 100 manifests/hour per
      # source IP, which this shared runner can exhaust; with creds the cap is far higher.
      if [ -n "${DOCKERHUB_USER:-}" ] && [ -n "${DOCKERHUB_TOKEN:-}" ]; then
        printf '%s' "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USER" --password-stdin >/dev/null 2>&1 \
          && echo "Docker Hub: authenticated as $DOCKERHUB_USER" \
          || echo "::warning:: Docker Hub login failed — continuing unauthenticated"
      else
        echo "Docker Hub: unauthenticated (no DOCKERHUB_USER/DOCKERHUB_TOKEN) — 100 pulls/hour per IP"
      fi
      # Reap orphaned eval containers before starting. Cancelling a benchmarks run kills the
      # workflow process but NOT the Docker containers swebench started, so each cancellation
      # leaves `sweb.eval.*` containers running indefinitely — one was observed Up 3 hours,
      # competing for Docker throughput with the run that replaced it. The bench job is
      # serialized on this runner (single self-hosted runner, one leg at a time), so any
      # sweb.eval container alive at setup time can only be a leftover.
      # Scoped to sweb.eval.* on purpose: harbor's `*__env-main` containers belong to a
      # different adapter and are not ours to kill.
      swe_orphans=$(docker ps -aq --filter "name=sweb.eval" 2>/dev/null | tr '\n' ' ')
      if [ -n "$(printf '%s' "$swe_orphans" | tr -d ' ')" ]; then
        # shellcheck disable=SC2086 -- intentional word splitting over container ids
        docker rm -f $swe_orphans >/dev/null 2>&1 || true
        echo "reaped $(printf '%s' "$swe_orphans" | wc -w | tr -d ' ') orphaned sweb.eval container(s)"
      fi

      swe_ids=$("$CAPEVOLVE_PY" -c "
import json,sys
print(' '.join(t['id'] for t in json.load(open(sys.argv[1]))))" "$SWE_TASKS")
      # Build the to-pull list first, skipping images already on the runner, then pull the
      # remainder with BOUNDED CONCURRENCY. swebench pulls in parallel during grading
      # (--max_workers, 10 here), so a sequential pre-pull would be slower than the behaviour
      # it replaces — fine for smoke's 5 instances, actively harmful for full's 250. Six is
      # chosen to stay well under Docker Hub's unauthenticated rate limiting while still
      # overlapping the (large, ~1GB+) layer downloads.
      swe_todo="$(mktemp)"; swe_fails="$(mktemp)"
      swe_total=0
      for iid in $swe_ids; do
        swe_total=$((swe_total+1))
        img="swebench/sweb.eval.x86_64.$(printf '%s' "$iid" | sed 's/__/_1776_/g'):latest"
        docker image inspect "$img" >/dev/null 2>&1 || printf '%s %s\n' "$iid" "$img" >> "$swe_todo"
      done
      swe_need=$(wc -l < "$swe_todo" | tr -d ' ')
      if [ "$swe_need" -gt 0 ]; then
        echo "pre-pulling $swe_need of $swe_total eval image(s), 6 at a time"
        # instance ids contain no spaces, so a space-separated pair is safe for xargs -n 2.
        # Each worker echoes the instance id on failure; the parent counts those.
        xargs -P 6 -n 2 sh -c 'docker pull -q "$2" >/dev/null 2>&1 || echo "$1"' _ \
          < "$swe_todo" > "$swe_fails" 2>/dev/null || true
      fi
      swe_failed=$(wc -l < "$swe_fails" | tr -d ' ')
      swe_missing=$(tr '\n' ' ' < "$swe_fails")
      for iid in $swe_missing; do
        echo "::warning:: could not pull the eval image for $iid — it will infra-error during scoring"
      done
      rm -f "$swe_todo" "$swe_fails"
      if [ "$swe_failed" -gt 0 ] && [ "$swe_failed" -eq "$swe_total" ]; then
        echo "::error:: NONE of the $swe_total eval images could be pulled — Docker Hub is"
        echo "::error:: unreachable or rate-limited. Every task would infra-error; aborting"
        echo "::error:: rather than spending the optimizer budget on an unmeasurable run."
        exit 1
      fi
      [ "$swe_failed" -gt 0 ] \
        && echo "::warning:: $swe_failed/$swe_total eval images unavailable:$swe_missing" \
        || echo "swebench eval images ready: $swe_total/$swe_total"
      fi
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

# Gateway preflight — ENTITLEMENT first, then BUDGET. The agent AND the optimizer share one
# LiteLLM gateway, and both of these faults present identically: every rollout dies with
# INFRASTRUCTURE_ERROR and the suite reports a clean-looking 0.000 that is indistinguishable
# from a real regression. Detect both up front rather than burn hours and dollars.
#
# ENTITLEMENT is the one that actually bites. The model dropdowns in benchmarks.yml are a
# STATIC list; the gateway's per-team allowlist is not, so they drift apart. Run 31124146014
# selected `Azure/gpt-5-mini-2025-08-07` — in the dropdown, absent from the key's allowlist —
# and spent 11 minutes plus $2.56 of optimizer budget before assert_run.py noticed that all
# 5 tasks had infra-errored. 15 of that dropdown's 30 agent options were in the same state,
# including its own default `aws/gpt-oss-120b`.
#
# The previous probe could not have caught that: it asked about a HARDCODED
# `aws/gpt-oss-120b` instead of the models the run actually selected, and only hard-failed on
# HTTP 429 budget_exceeded — so a `team not allowed to access model` rejection printed
# "(not budget-blocked)" and sailed straight through.
if [ -n "${ANTHROPIC_BASE_URL:-}" ] && [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ] && command -v curl >/dev/null; then
  PF_AGENT="${AGENT_MODEL:-aws/gpt-oss-120b}"
  PF_OPTIMIZER="${OPTIMIZER_MODEL:-claude-opus-4-8}"

  # 1. ENTITLEMENT — is each SELECTED model served to this key at all?
  # /models is an `llm_api_routes` call. The richer /model/info and /key/info are NOT: these
  # virtual keys are route-scoped and answer both with 403 "not allowed to call this route",
  # which is also the real reason the old preflight logged a mystery HTTP 403.
  models=/tmp/capevolve_models.$$.json
  mcode="$(curl -sS -m 30 -o "$models" -w '%{http_code}' \
    "$ANTHROPIC_BASE_URL/models" \
    -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" 2>/dev/null || echo 000)"
  if [ "$mcode" = "200" ]; then
    if ! "$CAPEVOLVE_PY" "$LIB_DIR/check_models.py" "$models" \
        --require agent="$PF_AGENT" --require optimizer="$PF_OPTIMIZER"; then
      rm -f "$models"; exit 1
    fi
  else
    echo "::warning:: gateway /models returned HTTP $mcode — cannot verify model entitlement"
  fi
  rm -f "$models"

  # 2. BUDGET + call-time entitlement — one real completion with the SELECTED agent model.
  # Listing a model is necessary but not sufficient: the team check happens at call time.
  # `max_completion_tokens` (not `max_tokens`) is used because the Azure reasoning
  # deployments reject the latter outright, and a probe that 400s on its own parameters
  # would be a false alarm.
  probe=/tmp/capevolve_budget_probe.$$.json
  code="$(curl -sS -m 60 -o "$probe" -w '%{http_code}' \
    "$ANTHROPIC_BASE_URL/chat/completions" \
    -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$PF_AGENT\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_completion_tokens\":16}" \
    2>/dev/null || echo 000)"
  if [ "$code" = "429" ] && grep -qi 'budget' "$probe" 2>/dev/null; then
    echo "::error:: model gateway is OVER BUDGET (HTTP 429 budget_exceeded) — aborting."
    echo "::error:: every rollout would score 0.000 as INFRASTRUCTURE_ERROR. Raise/reset the gateway budget."
    head -c 300 "$probe" 2>/dev/null; echo; rm -f "$probe"; exit 1
  fi
  if grep -qi 'not allowed to access model' "$probe" 2>/dev/null; then
    echo "::error:: gateway REFUSED agent model '$PF_AGENT' at call time (HTTP $code):"
    echo "::error:: the key's team is not entitled to it, so every rollout would fail and the"
    echo "::error:: suite would publish a fake 0.000. Pick a model this key can call."
    head -c 600 "$probe" 2>/dev/null; echo; rm -f "$probe"; exit 1
  fi
  rm -f "$probe"
  echo "gateway preflight: agent='$PF_AGENT' optimizer='$PF_OPTIMIZER' entitled; completion probe HTTP $code (not budget-blocked)"
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
