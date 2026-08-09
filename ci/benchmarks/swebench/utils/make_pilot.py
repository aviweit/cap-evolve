#!/usr/bin/env python3
"""Generate the swebench pilot tier as a representative subset of the full tier.

  make_pilot.py [--size 50] [--write]

The pilot exists so the per-trial cost and runtime of a 250-task full run can be measured on
a tenth of the tasks first — every task is independent, so cost and throughput extrapolate
linearly, which a 5-task smoke cannot do.

ALLOCATION: floor of 1 per repo, then the remainder proportionally by largest remainder. The
floor means no repo's build/test toolchain can hide from the pilot; the proportional remainder
keeps the mix close enough to full that timings still predict it. django lands at ~38% against
full's ~46% — that tilt is the price of the floor and is deliberate.

SELECTION WITHIN A REPO — the bit that was wrong. The first version took `ids[:n]`, the HEAD of
each repo's slice. The full tier is sorted by instance id, and SWE-bench ids track upstream PR
numbers, so that drew almost exclusively from the oldest issues: measured mean relative position
0.08 on a 0=first / 1=last scale, with django spanning 10554-11880 out of 9296-17084. A
systematic age skew, not a sample. Now the picks are spread evenly across each repo's ordering,
which keeps the whole id range represented.

Deterministic by construction — no RNG — so the output is reproducible and reviewable in a diff.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FULL = HERE.parent / "full" / "tasks.json"
PILOT = HERE.parent / "pilot" / "tasks.json"


def allocate(counts: dict[str, int], target: int) -> dict[str, int]:
    """Floor of 1 per repo, remainder by largest fractional share."""
    alloc = {r: 1 for r in counts}
    left = target - len(alloc)
    if left < 0:
        raise SystemExit(f"target {target} < {len(counts)} repos; cannot give each a floor of 1")
    pool = sum(counts.values()) - len(alloc)
    share = {r: (counts[r] - 1) / pool * left if pool else 0 for r in counts}
    for r in counts:
        alloc[r] += int(share[r])
    rem = target - sum(alloc.values())
    for r in sorted(counts, key=lambda r: (-(share[r] - int(share[r])), r)):
        if rem <= 0:
            break
        if alloc[r] < counts[r]:
            alloc[r] += 1
            rem -= 1
    return alloc


def spread(ids: list[str], n: int) -> list[str]:
    """n ids spread evenly across `ids`, always including the first and last.

    Even spacing rather than `ids[:n]` is the whole point: it samples the full id range instead
    of clustering on the oldest instances.
    """
    if n >= len(ids):
        return list(ids)
    if n == 1:
        return [ids[len(ids) // 2]]
    step = (len(ids) - 1) / (n - 1)
    return [ids[round(i * step)] for i in range(n)]


def build(size: int) -> list[dict]:
    full = json.loads(FULL.read_text(encoding="utf-8"))
    by_repo: dict[str, list[dict]] = collections.defaultdict(list)
    for t in full:
        by_repo[t["id"].split("__")[0]].append(t)
    counts = {r: len(v) for r, v in by_repo.items()}
    alloc = allocate(counts, size)
    out: list[dict] = []
    for repo in sorted(by_repo):
        chosen = spread([t["id"] for t in by_repo[repo]], alloc[repo])
        by_id = {t["id"]: t for t in by_repo[repo]}
        out += [{"id": i, "tag": "pilot", "agent": by_id[i]["agent"]} for i in chosen]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=50)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    rows = build(a.size)
    full_ids = [t["id"] for t in json.loads(FULL.read_text(encoding="utf-8"))]
    assert len(rows) == a.size, f"got {len(rows)} not {a.size}"
    assert len({r['id'] for r in rows}) == a.size, "duplicate ids"
    assert {r["id"] for r in rows} <= set(full_ids), "pilot id not present in full"

    if a.write:
        PILOT.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {PILOT} ({len(rows)} tasks)")
    c = collections.Counter(r["id"].split("__")[0] for r in rows)
    for repo in sorted(c, key=lambda r: -c[r]):
        print(f"  {repo:<13}{c[repo]:>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
