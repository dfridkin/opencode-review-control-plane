#!/usr/bin/env python3
"""Post verified findings as one PR review with inline comments.

Engine-neutral: works on the JSON from scripts/review_orchestrator.py whatever model
produced it. GitHub rejects a whole review if any inline comment points at a line
outside the diff, so findings on lines GitHub can't anchor go in the review body.

  python3 scripts/ci/post_review.py --findings findings.json --diff pr.diff \
      --repo owner/repo --pr 12 --head-sha <sha> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
INLINE_MIN = {"blocker", "major", "minor"}  # nits stay in the summary comment
MARKER = "<!-- agent-review-inline -->"


def commentable_lines(diff: str) -> dict[str, set[int]]:
    """Lines on the RIGHT (new) side that GitHub accepts for review comments:
    added and context lines inside hunks."""
    out: dict[str, set[int]] = {}
    path, line = None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = None if target == "/dev/null" else re.sub(r"^b/", "", target)
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        m = HUNK.match(raw)
        if m:
            line = int(m.group(1))
            continue
        if path is None or not raw:
            if path is not None and raw == "":
                out.setdefault(path, set()).add(line)
                line += 1
            continue
        tag = raw[0]
        if tag in "+ ":
            out.setdefault(path, set()).add(line)
            line += 1
        # "-" lines don't advance the new-file counter; "\" is "No newline at end of file"
    return out


def build_review(findings: list[dict], lines: dict[str, set[int]], head_sha: str) -> dict:
    comments, unanchored = [], []
    for f in findings:
        if f.get("severity") not in INLINE_MIN:
            continue
        body = (f"**[{f['severity']}]** {f['title']}\n\n{f['evidence']}\n\n"
                "_Found by a hunter agent and confirmed by an independent verifier agent._")
        path = str(f.get("file", "")).lstrip("./")
        if f.get("line") in lines.get(path, set()):
            comments.append({"path": path, "line": f["line"], "side": "RIGHT", "body": body})
        else:
            unanchored.append(f"- **[{f['severity']}]** `{path}:{f.get('line')}` {f['title']}")
    text = MARKER + "\nVerified findings from the automated review."
    if unanchored:
        text += "\n\nOn lines outside the diff:\n" + "\n".join(unanchored)
    return {"commit_id": head_sha, "event": "COMMENT", "body": text, "comments": comments}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--diff", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.findings).read_text())
    review = build_review(data.get("findings", []), commentable_lines(Path(args.diff).read_text()),
                          args.head_sha)
    if not review["comments"] and "outside the diff" not in review["body"]:
        print("no inline findings to post")
        return 0
    if args.dry_run:
        print(json.dumps(review, indent=2))
        return 0
    res = subprocess.run(["gh", "api", "-X", "POST", f"repos/{args.repo}/pulls/{args.pr}/reviews",
                          "--input", "-"], input=json.dumps(review), capture_output=True, text=True)
    if res.returncode != 0:
        print(f"posting review failed: {res.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"posted {len(review['comments'])} inline comment(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
