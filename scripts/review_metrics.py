#!/usr/bin/env python3
"""Baseline and track the numbers that tell you whether lighter review is safe.

Capture these BEFORE you turn on any automation, then re-run after each phase.
If revert share or incident-linked PRs go up, roll the last change back.

  # live (needs gh auth):
  python scripts/review_metrics.py --since 2026-07-01 --markdown metrics.md

  # offline, from a saved `gh pr list --json ...` dump:
  python scripts/review_metrics.py --from-file prs.json

Conventions this script relies on (set them up in your repo):
  - Reverts: GitHub's default "Revert \"...\"" title, or a `revert` label
  - Incidents: label the PR that caused an incident `incident-cause`
  - Tiers: the review pipeline labels each PR `tier:<name>`
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

GH_FIELDS = "number,title,author,createdAt,mergedAt,additions,deletions,labels,reviews"
REVERT_TITLE = re.compile(r'^\s*Revert\s+"', re.IGNORECASE)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _labels(pr: dict) -> set[str]:
    return {(l.get("name") if isinstance(l, dict) else l) for l in pr.get("labels", [])}


def _is_bot(login: str, author: dict | None = None) -> bool:
    return login.endswith("[bot]") or bool(author and author.get("is_bot"))


def review_rounds(pr: dict) -> int:
    """1 + number of CHANGES_REQUESTED reviews. One round = merged on first pass."""
    return 1 + sum(1 for r in pr.get("reviews", []) if (r.get("state") or "").upper() == "CHANGES_REQUESTED")


def human_approved(pr: dict) -> bool:
    author = (pr.get("author") or {}).get("login", "")
    for r in pr.get("reviews", []):
        login = (r.get("author") or {}).get("login", "")
        if (r.get("state") or "").upper() == "APPROVED" and login and login != author \
                and not _is_bot(login, r.get("author")):
            return True
    return False


def pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def compute(prs: list[dict]) -> dict:
    merged = [p for p in prs if p.get("mergedAt")]
    n = len(merged)
    reverts = [p for p in merged if REVERT_TITLE.match(p.get("title", "")) or "revert" in _labels(p)]
    incidents = [p for p in merged if "incident-cause" in _labels(p)]
    hours = [(_ts(p["mergedAt"]) - _ts(p["createdAt"])).total_seconds() / 3600 for p in merged]
    sizes = [int(p.get("additions", 0)) + int(p.get("deletions", 0)) for p in merged]
    rounds = [review_rounds(p) for p in merged]

    by_tier: dict[str, list[dict]] = defaultdict(list)
    for p in merged:
        tiers = [l.split(":", 1)[1] for l in _labels(p) if l.startswith("tier:")]
        by_tier[tiers[0] if tiers else "unlabeled"].append(p)

    def q(vals: list[float], k: float) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        return round(s[min(len(s) - 1, int(k * len(s)))], 1)

    return {
        "merged_prs": n,
        "revert_prs": len(reverts),
        "revert_share_pct": pct(len(reverts), n),
        "incident_linked_prs": len(incidents),
        "incident_share_pct": pct(len(incidents), n),
        "median_hours_to_merge": round(statistics.median(hours), 1) if hours else 0.0,
        "p90_hours_to_merge": q(hours, 0.9),
        "median_changed_lines": int(statistics.median(sizes)) if sizes else 0,
        "one_round_share_pct": pct(sum(1 for r in rounds if r == 1), n),
        "five_plus_rounds_share_pct": pct(sum(1 for r in rounds if r >= 5), n),
        "human_approved_share_pct": pct(sum(1 for p in merged if human_approved(p)), n),
        "by_tier": {
            t: {
                "merged": len(ps),
                "reverts": sum(1 for p in ps if p in reverts),
                "human_approved_share_pct": pct(sum(1 for p in ps if human_approved(p)), len(ps)),
            }
            for t, ps in sorted(by_tier.items())
        },
    }


def to_markdown(m: dict, label: str) -> str:
    rows = [
        ("Merged PRs", m["merged_prs"]),
        ("Revert share", f'{m["revert_share_pct"]}% ({m["revert_prs"]})'),
        ("Incident-linked share", f'{m["incident_share_pct"]}% ({m["incident_linked_prs"]})'),
        ("Median hours to merge", m["median_hours_to_merge"]),
        ("P90 hours to merge", m["p90_hours_to_merge"]),
        ("Median changed lines", m["median_changed_lines"]),
        ("One-round passes", f'{m["one_round_share_pct"]}%'),
        ("5+ review rounds", f'{m["five_plus_rounds_share_pct"]}%'),
        ("Human-approved", f'{m["human_approved_share_pct"]}%'),
    ]
    out = [f"## Review metrics: {label}", "", "| Metric | Value |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in rows]
    out += ["", "| Tier | Merged | Reverts | Human-approved |", "|---|---|---|---|"]
    for t, v in m["by_tier"].items():
        out.append(f'| {t} | {v["merged"]} | {v["reverts"]} | {v["human_approved_share_pct"]}% |')
    return "\n".join(out) + "\n"


def fetch_live(since: str, limit: int) -> list[dict]:
    res = subprocess.run(
        ["gh", "pr", "list", "--state", "merged", "--search", f"merged:>={since}",
         "--limit", str(limit), "--json", GH_FIELDS],
        capture_output=True, text=True, check=True,
    )
    return json.loads(res.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--since", help="YYYY-MM-DD, fetch merged PRs live via gh")
    src.add_argument("--from-file", help="JSON dump of `gh pr list --json " + GH_FIELDS + "`")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--markdown", help="also write a markdown table here")
    args = ap.parse_args(argv)

    if args.from_file:
        with open(args.from_file) as fh:
            prs = json.load(fh)
        label = args.from_file
    else:
        prs = fetch_live(args.since, args.limit)
        label = f"merged since {args.since}"

    m = compute(prs)
    print(json.dumps(m, indent=2))
    if args.markdown:
        with open(args.markdown, "w") as fh:
            fh.write(to_markdown(m, label))
    return 0


if __name__ == "__main__":
    sys.exit(main())
