#!/usr/bin/env bash
# Onboard VANILLA tau2-bench (sierra-research, latest main) for the direct arm.
#
# HAND-WRITTEN REPLAY, not generated output. The files beside this script (adapters/,
# seed_capability/, optimizer/, capevolve*.yaml, split_ids*.json, PROJECT.md) were produced by
# a coding agent following ../PROMPT.md alone. This script replays the setup steps that agent
# ran, in the prompt's R1 order, and then installs THOSE files instead of regenerating them:
#
#   bash examples/tau2/direct/setup.sh
#   bash examples/tau2/direct/run.sh --smoke     # then:  bash examples/tau2/direct/run.sh
#
# The direct arm needs no services. The clone is NEVER edited: the candidate's tools reach tau2
# through adapters/tau2_tailoring.py (a public `airline_candidate` domain + pristine gold).
set -uo pipefail

EX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$EX_DIR/../../.." && pwd)"
TAU2_REPO="https://github.com/sierra-research/tau2-bench"
TAU2_DIR="$REPO/vendor/tau2-bench"
# The commit the snapshot's tailoring was generated and verified against. NOT a pin: the clone
# is latest main. A different SHA means the seams must be re-located (PROMPT.md #2c).
TAU2_REFERENCE_SHA="4ce7c0397c1eb65c9bbe59aeacfe1ca44a1cd699"
VENV="${VENV:-$REPO/.venv}"
case "$VENV" in /*) ;; *) VENV="$REPO/$VENV" ;; esac
PY="$VENV/bin/python"
BASE="${BASE:-$REPO/.capevolve}"
PROJECT="$BASE/project"
say(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
die(){ printf '\n\033[1;31mSETUP FAILED: %s\033[0m\n' "$*" >&2; exit 1; }

say "1/5  Tools + venv (tau2 needs Python >=3.12,<3.14)"
for t in git uv curl; do command -v "$t" >/dev/null || die "'$t' not found on PATH"; done
[ -x "$PY" ] || uv venv -q -p 3.12 "$VENV" || die "uv venv -p 3.12 failed"
"$PY" --version

say "2/5  Clone tau2 — always compare with upstream main first"
UPSTREAM_SHA="$(git ls-remote "$TAU2_REPO" main | cut -f1)"
[ -n "$UPSTREAM_SHA" ] || die "git ls-remote $TAU2_REPO main returned nothing"
if [ ! -d "$TAU2_DIR/.git" ]; then
  git clone -q "$TAU2_REPO" "$TAU2_DIR" && git -C "$TAU2_DIR" checkout -q "$UPSTREAM_SHA" \
    || die "clone of $TAU2_REPO failed"
else
  HEAD_SHA="$(git -C "$TAU2_DIR" rev-parse HEAD)"
  DIRTY="$(git -C "$TAU2_DIR" status --porcelain)"
  if [ "$HEAD_SHA" != "$UPSTREAM_SHA" ] || [ -n "$DIRTY" ]; then
    # The prompt asks before re-cloning: a re-clone means re-tailoring. A script cannot ask,
    # so it refuses and says how to opt in.
    [ "${RECLONE:-0}" = "1" ] || die "vendor/tau2-bench is at ${HEAD_SHA:0:12}${DIRTY:+ (dirty)}, upstream main is ${UPSTREAM_SHA:0:12}. Re-run with RECLONE=1 to delete and re-clone (then re-tailor: PROMPT.md #2c)."
    rm -rf "$TAU2_DIR"
    git clone -q "$TAU2_REPO" "$TAU2_DIR" && git -C "$TAU2_DIR" checkout -q "$UPSTREAM_SHA" \
      || die "re-clone of $TAU2_REPO failed"
  fi
fi
SHA="$(git -C "$TAU2_DIR" rev-parse HEAD)"
echo "$SHA" > "$REPO/vendor/tau2-bench.COMMIT"
if [ "$SHA" = "$TAU2_REFERENCE_SHA" ]; then echo "  tau2 @ $SHA (= TAU2_REFERENCE_SHA)"
else echo "  WARNING: tau2 @ $SHA != TAU2_REFERENCE_SHA — the snapshot's tailoring may need re-locating (PROMPT.md #2c)"; fi

say "3/5  Install cap-evolve + tau2 into the venv"
VIRTUAL_ENV="$VENV" uv pip install -q -e "$REPO/core" -e "$TAU2_DIR" || die "uv pip install failed"
N="$("$PY" -c "from tau2.domains.airline.environment import get_tasks; print(len(get_tasks('base')))")" \
  || die "tau2 import failed"
[ "$N" = "50" ] || die "expected 50 airline tasks, got $N"
echo "  50 airline tasks"

say "4/5  Install the snapshot as the project"
"$PY" "$REPO/skills/phases/intake/scripts/run.py" --base "$BASE" --workdir "$REPO" --force >/dev/null \
  || die "intake scaffold failed"
rm -rf "$PROJECT/seed_capability"
cp -R "$EX_DIR/adapters" "$EX_DIR/seed_capability" "$EX_DIR/optimizer" "$PROJECT/"
cp "$EX_DIR/capevolve.yaml" "$EX_DIR/capevolve.smoke.yaml" "$EX_DIR"/split_ids*.json \
   "$EX_DIR/PROJECT.md" "$PROJECT/"
echo "  project at $PROJECT"

say "5/5  Hard gate — implement-and-check (cap-evolve check + instructions render self-test)"
for v in OPENAI_BASE_URL OPENAI_API_KEY; do
  [ -n "${!v:-}" ] || grep -q "^$v=" "$REPO/.env" 2>/dev/null \
    || echo "  WARNING: $v not set and not in $REPO/.env — every rollout needs it."
done
CAPEVOLVE_SKILLS_DIR="$REPO/skills" LITELLM_LOCAL_MODEL_COST_MAP=True \
  "$PY" "$REPO/skills/phases/implement-and-check/scripts/run.py" --project "$PROJECT" \
  || die "the gate did not pass"

printf '\n\033[1;32mREADY.\033[0m  Next:\n  bash %s/run.sh --smoke   # task 9, 1 trial, mock optimizer\n  bash %s/run.sh           # budget run (tasks 8 10 20 23 35 42 44)\n' "$EX_DIR" "$EX_DIR"
