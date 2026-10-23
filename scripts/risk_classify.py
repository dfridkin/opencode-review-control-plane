#!/usr/bin/env python3
"""Deterministic risk classifier for a pull request.

Decides which tier a change lands in and therefore whether a human must approve.

Inputs (pick one source of changed files):
  --files FILE     JSON from `gh api repos/{o}/{r}/pulls/{n}/files --paginate`
                   (list of {filename, previous_filename?, additions, deletions})
  --local BASE     compute from `git diff --numstat BASE...HEAD` (used by /pre-pr)

Escalation inputs (can only RAISE the tier):
  --labels "a,b"           PR labels
  --security-verdict V     pass | escalate | fail (from the security review job)

Output: classification JSON on stdout. With --github-output, also writes
key outputs to $GITHUB_OUTPUT.

Design rule: paths set the floor. A model verdict or label can push a PR up,
never down. Otherwise a prompt-injected diff could talk its way out of review.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy import DEFAULT_POLICY, Policy, Tier, load_policy, matches_any  # noqa: E402


@dataclass
class ChangedFile:
    path: str
    additions: int = 0
    deletions: int = 0
    previous_path: str | None = None

    @property
    def lines(self) -> int:
        return self.additions + self.deletions


@dataclass
class Classification:
    tier: str
    rank: int
    min_approvals: int
    owners: list[str]
    changed_lines: int
    counted_files: int
    blocked: bool = False
    block_reason: str = ""
    reasons: list[str] = field(default_factory=list)
    matched: dict[str, str] = field(default_factory=dict)

    @property
    def needs_human(self) -> bool:
        return self.min_approvals > 0


# ---------------------------------------------------------------------------
# Core logic (pure)
# ---------------------------------------------------------------------------

def tier_for_path(policy: Policy, path: str) -> tuple[Tier, str]:
    for tier in policy.tiers:  # riskiest first
        hit = matches_any(path, list(tier.paths))
        if hit:
            return tier, hit
    return policy.default, "(default)"


def classify(
    policy: Policy,
    files: list[ChangedFile],
    labels: list[str] | None = None,
    security_verdict: str | None = None,
) -> Classification:
    labels = labels or []
    current = policy.lowest
    reasons: list[str] = []
    matched: dict[str, str] = {}

    # 1. Paths set the floor. Renames count on both sides: moving a file OUT of
    #    src/auth/ is as risky as editing it.
    for f in files:
        for p in filter(None, [f.path, f.previous_path]):
            t, pattern = tier_for_path(policy, p)
            matched[p] = t.name
            if t.rank > current.rank:
                current = t
    if current is not policy.lowest:
        hits = sorted(p for p, n in matched.items() if n == current.name)
        reasons.append(f"paths in tier '{current.name}': {', '.join(hits[:8])}"
                       + (" ..." if len(hits) > 8 else ""))

    # 2. Size.
    counted = [f for f in files if not matches_any(f.path, list(policy.exclude_from_size))]
    changed_lines = sum(f.lines for f in counted)
    blocked, block_reason = False, ""
    if changed_lines > policy.hard_max_lines:
        blocked = True
        block_reason = (f"{changed_lines} changed lines exceeds hard max "
                        f"{policy.hard_max_lines}. Split the PR.")
    elif changed_lines > policy.human_review_above_lines and current.min_approvals == 0:
        current = policy.first_human_tier()
        reasons.append(f"{changed_lines} changed lines > {policy.human_review_above_lines}: "
                       f"routed to '{current.name}'")

    # 3. Labels (escalate only).
    for label in labels:
        target = policy.label_escalations.get(label)
        if target:
            t = policy.tier(target)
            if t.rank > current.rank:
                current = t
                reasons.append(f"label '{label}' escalated to '{t.name}'")

    # 4. Security verdict (escalate or block only).
    if security_verdict:
        v = security_verdict.strip().lower()
        if v == "fail":
            blocked = True
            block_reason = (block_reason + " " if block_reason else "") + \
                "Security review returned FAIL."
        target = policy.verdict_escalations.get(v)
        if target:
            t = policy.tier(target)
            if t.rank > current.rank:
                current = t
                reasons.append(f"security verdict '{v}' escalated to '{t.name}'")
        elif v not in {"pass", "fail"}:
            # Unknown or missing verdict: fail closed into a human tier.
            t = policy.first_human_tier()
            if t.rank > current.rank:
                current = t
            reasons.append(f"unrecognized security verdict '{v}': routed to '{current.name}'")

    if not reasons:
        reasons.append(current.reason or f"tier '{current.name}'")

    return Classification(
        tier=current.name,
        rank=current.rank,
        min_approvals=current.min_approvals,
        owners=list(current.owners),
        changed_lines=changed_lines,
        counted_files=len(counted),
        blocked=blocked,
        block_reason=block_reason,
        reasons=reasons,
        matched=matched,
    )


# ---------------------------------------------------------------------------
# I/O adapters
# ---------------------------------------------------------------------------

def files_from_github_json(data: list) -> list[ChangedFile]:
    # `gh api --paginate --slurp` returns a list of pages; flatten it.
    if data and isinstance(data[0], list):
        data = [d for page in data for d in page]
    out = []
    for d in data:
        out.append(ChangedFile(
            path=d.get("filename") or d["path"],
            additions=int(d.get("additions", 0)),
            deletions=int(d.get("deletions", 0)),
            previous_path=d.get("previous_filename"),
        ))
    return out


def files_from_git(base: str, cwd: str | None = None) -> list[ChangedFile]:
    res = subprocess.run(
        ["git", "diff", "--numstat", "-M", f"{base}...HEAD"],
        capture_output=True, text=True, check=True, cwd=cwd,
    )
    out = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, dele, path = parts
        prev = None
        # rename forms: "old => new" or "dir/{old => new}/f"
        if " => " in path:
            if "{" in path:
                pre, rest = path.split("{", 1)
                mid, post = rest.split("}", 1)
                old, new = mid.split(" => ")
                prev = (pre + old + post).replace("//", "/")
                path = (pre + new + post).replace("//", "/")
            else:
                prev, path = path.split(" => ")
        out.append(ChangedFile(
            path=path,
            additions=0 if add == "-" else int(add),
            deletions=0 if dele == "-" else int(dele),
            previous_path=prev,
        ))
    return out


def write_github_output(c: Classification) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a") as fh:
        fh.write(f"tier={c.tier}\n")
        fh.write(f"needs_human={'true' if c.needs_human else 'false'}\n")
        fh.write(f"blocked={'true' if c.blocked else 'false'}\n")
        fh.write(f"changed_lines={c.changed_lines}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--files", help="JSON file of PR files from the GitHub API")
    src.add_argument("--local", metavar="BASE", help="classify local branch against BASE")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY))
    ap.add_argument("--labels", default="", help="comma-separated PR labels")
    ap.add_argument("--security-verdict", default=None)
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args(argv)

    policy = load_policy(args.policy)
    if args.files:
        with open(args.files) as fh:
            files = files_from_github_json(json.load(fh))
    else:
        files = files_from_git(args.local)

    labels = [l.strip() for l in args.labels.split(",") if l.strip()]
    c = classify(policy, files, labels, args.security_verdict)
    out = asdict(c)
    out["needs_human"] = c.needs_human
    print(json.dumps(out, indent=2))
    if args.github_output:
        write_github_output(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
