#!/usr/bin/env python3
"""Assert the models this run selected are actually served by the gateway.

  check_models.py <models_json> --require ROLE=MODEL_ID [--require ROLE=MODEL_ID ...]

<models_json> is the raw body of `GET $ANTHROPIC_BASE_URL/models` (OpenAI-shaped:
`{"data": [{"id": ...}, ...]}`). Exit 0 if every required model id is served, 1 with a
diagnosis naming the offending id, the closest available spellings, and the full served
list.

Why this exists
---------------
The `agent_model` / `optimizer_model` dropdowns in .github/workflows/benchmarks.yml are a
STATIC list. The gateway's per-team entitlements are not — they drift as teams and model
deployments change. When the two disagree, every LLM call dies with

    litellm.APIError: Litellm_proxyException - team not allowed to access model.
    This team can only access models=[...]. Tried to access Azure/gpt-5-mini-2025-08-07

Each rollout then records $0.00 / 0 tokens and the suite reports a clean-looking mean
reward of 0.000. Run 31124146014 burned 11 minutes and $2.56 of optimizer spend that way:
all 5 swebench tasks infra-errored because the dispatch selected a model that was in the
dropdown but not on the key's allowlist, and 15 of that dropdown's 30 options — including
its own default `aws/gpt-oss-120b` — were in the same state.

assert_run.py catches this AFTER the fact (its `measured` check is what finally failed the
job). This catches it BEFORE any spend, in the setup step, in about a second.

Two spellings that look right and are not
-----------------------------------------
Model ids are matched literally by the gateway, so these are hard failures that a human
reads straight past:

  * prefix drift  — dropdown `gcp/gemini-2.5-flash`, gateway `gemini-2.5-flash`
  * case drift    — dropdown `Azure/o4-mini`, gateway `azure/o4-mini`

Both are reported as such rather than as a bare "not found", because the fix is a one-token
edit and the failure mode otherwise looks like a missing deployment.

NB: the CI virtual keys are scoped to `llm_api_routes`, so the richer management routes
(/key/info, /model/info) return HTTP 403 "not allowed to call this route". `/models` is an
llm_api route and is therefore the only entitlement introspection available to us here.
Listing is necessary-but-not-sufficient for entitlement, so ci_setup.sh still probes one
real completion afterwards; this check exists to turn the common, cheap-to-detect case into
a fast, self-explaining failure.
"""
from __future__ import annotations

import difflib
import json
import sys


def served_ids(body: str) -> list[str]:
    """Extract model ids from an OpenAI-shaped /models body.

    Tolerates a bare list and a `{"data": [...]}` envelope, and entries that are plain
    strings rather than objects — different LiteLLM versions have shipped each shape, and a
    preflight that crashes on an unexpected envelope would block runs it should have waved
    through.
    """
    payload = json.loads(body)
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    ids = []
    for row in rows:
        mid = row if isinstance(row, str) else (row or {}).get("id")
        if isinstance(mid, str) and mid:
            ids.append(mid)
    return ids


def suggest(wanted: str, available: list[str]) -> tuple[str, list[str]]:
    """Classify why `wanted` is absent and offer the ids a human probably meant.

    Returns (reason, candidates). `reason` distinguishes the two silent-typo cases from a
    genuine absence so the error text can say which one-token edit to make.
    """
    exact_ci = [m for m in available if m.lower() == wanted.lower()]
    if exact_ci:
        return "case mismatch", exact_ci

    # Compare the last path segment: `gcp/gemini-2.5-flash` vs `gemini-2.5-flash`.
    tail = wanted.lower().rsplit("/", 1)[-1]
    same_tail = [m for m in available if m.lower().rsplit("/", 1)[-1] == tail]
    if same_tail:
        return "prefix mismatch", same_tail

    return "not served", difflib.get_close_matches(wanted, available, n=3, cutoff=0.5)


def check(body: str, required: dict[str, str]) -> tuple[bool, list[str]]:
    """Validate every required model against the served list.

    Returns (ok, lines). `lines` is the human-facing report — GitHub `::error::` annotations
    on failure, a one-line confirmation per role on success.
    """
    available = served_ids(body)
    if not available:
        return False, [
            "::error:: gateway /models returned no usable model ids — cannot verify "
            "entitlement. Check ANTHROPIC_BASE_URL and the key's route scope.",
        ]

    lines, bad = [], []
    for role, model in required.items():
        if model in available:
            lines.append(f"gateway entitlement OK: {role} = {model}")
            continue
        bad.append(role)
        reason, cands = suggest(model, available)
        qualifier = "" if reason == "not served" else f" ({reason})"
        lines.append(f"::error:: {role} model {model!r} is NOT served by this gateway{qualifier}.")
        if cands:
            hint = ", ".join(repr(c) for c in cands)
            verb = "did you mean" if reason == "not served" else "the gateway spells it"
            lines.append(f"::error:: {verb} {hint}?")

    if bad:
        lines += [
            "::error:: every rollout would fail with `team not allowed to access model` and "
            "the suite would report a fake 0.000 — aborting before spending anything.",
            "::error:: fix the workflow input (or the .github/workflows/benchmarks.yml "
            "dropdown) to one of the models served to this key:",
        ]
        lines += [f"::error::   {m}" for m in sorted(available)]
    return not bad, lines


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__ or "", file=sys.stderr)
        return 2
    path, required = argv[0], {}
    rest = iter(argv[1:])
    for arg in rest:
        if arg != "--require":
            print(f"::error:: unexpected argument {arg!r}", file=sys.stderr)
            return 2
        spec = next(rest, "")
        role, _, model = spec.partition("=")
        if not role or not model:
            print(f"::error:: --require expects ROLE=MODEL_ID, got {spec!r}", file=sys.stderr)
            return 2
        required[role] = model

    if not required:
        print("gateway entitlement: no models to check", file=sys.stderr)
        return 0

    try:
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
    except OSError as exc:
        print(f"::error:: cannot read gateway /models response: {exc}", file=sys.stderr)
        return 1

    try:
        ok, lines = check(body, required)
    except (json.JSONDecodeError, TypeError) as exc:
        # A non-JSON body means the route was refused or something proxied an HTML error
        # page. That is not proof the models are unavailable, so stay non-blocking and let
        # the completion probe be the judge.
        print(f"gateway entitlement: unparseable /models response ({exc}) — skipping check")
        return 0

    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
