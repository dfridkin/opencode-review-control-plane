#!/usr/bin/env python3
"""Turn the review agents' structured output into a PR comment and a pass/fail.

  REVIEW_JSON="$(cat findings.json)" python3 scripts/ci/review_summary.py \
      --block-on blocker --out comment.md

--block-on none     advisory only (default; start here)
--block-on blocker  fail the check if any VERIFIED blocker was found
--block-on major    fail on verified blocker or major

Missing or unparseable output never passes silently when blocking is on.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

RANK = {"nit": 0, "minor": 1, "major": 2, "blocker": 3}


def render(data: dict, engine_label: str = "") -> str:
    findings = sorted(data.get("findings", []), key=lambda f: -RANK.get(f.get("severity", "nit"), 0))
    counts = {k: sum(1 for f in findings if f.get("severity") == k) for k in RANK}
    lines = ["### Automated review" + (f" ({engine_label})" if engine_label else ""), ""]
    if not findings:
        lines.append("No verified findings.")
    else:
        lines.append("| Severity | Location | Finding |")
        lines.append("|---|---|---|")
        for f in findings:
            title = str(f.get("title", "")).replace("|", "\\|")
            lines.append(f"| {f.get('severity')} | `{f.get('file')}:{f.get('line')}` | {title} |")
    dropped = data.get("dropped_unverified")
    lines += ["", f"blockers {counts['blocker']} · majors {counts['major']} · "
                  f"minors {counts['minor']} · nits {counts['nit']}"
              + (f" · dropped as unverified {dropped}" if dropped is not None else "")]
    if data.get("summary"):
        lines += ["", str(data["summary"])]
    if data.get("errors"):
        lines += ["", "Agent calls that failed (the review is incomplete):"]
        lines += [f"- {e}" for e in data["errors"][:10]]
    lines += ["", "_Findings were checked by an independent verifier agent before posting. "
                  "Reply on the inline comment if one is wrong; that feeds the eval set._"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--block-on", choices=["none", "blocker", "major"], default="none")
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine-label", default="")
    args = ap.parse_args(argv)

    raw = os.environ.get("REVIEW_JSON", "").strip()
    try:
        data = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        data = None

    if data is None:
        with open(args.out, "w") as fh:
            fh.write("### Automated review\n\nThe review agent returned no usable output. "
                     "Check the workflow run.\n")
        print("no structured output", file=sys.stderr)
        return 1 if args.block_on != "none" else 0

    with open(args.out, "w") as fh:
        fh.write(render(data, args.engine_label))

    if args.block_on == "none":
        return 0
    threshold = RANK[args.block_on]
    worst = max((RANK.get(f.get("severity", "nit"), 0) for f in data.get("findings", [])), default=-1)
    if worst >= threshold:
        print(f"verified finding at or above '{args.block_on}'", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
