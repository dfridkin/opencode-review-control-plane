#!/usr/bin/env python3
"""Count consecutive AI review rounds that ended with a verified blocker or major finding.

  REVIEW_JSON="$(cat findings.json)" python3 scripts/ci/review_rounds.py \
      --previous previous-comment.md --head-sha "$HEAD_SHA" --cap 3

A round is one review of one head commit. The count rises when a new commit still has a
verified finding at `major` or above, resets to 0 on a clean round, and does not change when
the same commit is reviewed again (a re-run). The count is carried between runs in a hidden
marker at the end of the review summary comment.

Prints JSON: {"rounds": N, "stuck": bool, "marker": "<!-- ... -->", "open": [...]}.
`stuck` is true once rounds >= cap. A cap of 0 disables it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

RANK = {"nit": 0, "minor": 1, "major": 2, "blocker": 3}
MARKER = re.compile(r"<!-- review-rounds: (\d+) head=([0-9a-fA-F]*) -->")


def parse_previous(text: str) -> tuple[int, str]:
    matches = MARKER.findall(text or "")
    if not matches:
        return 0, ""
    rounds, head = matches[-1]
    return int(rounds), head


def open_findings(data: dict | None) -> list[dict]:
    if not data:
        return []
    return [f for f in data.get("findings", []) if RANK.get(f.get("severity", "nit"), 0) >= RANK["major"]]


def count(previous: str, head_sha: str, data: dict | None, cap: int) -> dict:
    prev_rounds, prev_head = parse_previous(previous)
    found = open_findings(data)
    if data is None:
        rounds = prev_rounds              # no usable output: neither count nor reset
    elif prev_head and prev_head == head_sha:
        rounds = prev_rounds              # same commit reviewed again
    elif found:
        rounds = prev_rounds + 1
    else:
        rounds = 0
    return {
        "rounds": rounds,
        "stuck": cap > 0 and rounds >= cap,
        "marker": f"<!-- review-rounds: {rounds} head={head_sha} -->",
        "open": [{"severity": f.get("severity"), "file": f.get("file"), "line": f.get("line"),
                  "title": f.get("title", "")} for f in found],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--previous", help="file holding the previous summary comment (may be missing)")
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--cap", type=int, default=3)
    args = ap.parse_args(argv)

    previous = ""
    if args.previous and os.path.exists(args.previous):
        with open(args.previous) as fh:
            previous = fh.read()
    raw = os.environ.get("REVIEW_JSON", "").strip()
    try:
        data = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        data = None
    json.dump(count(previous, args.head_sha, data, args.cap), sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
