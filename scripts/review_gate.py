#!/usr/bin/env python3
"""Risk-routed human approval gate.

Combines the classifier output with the PR's reviews and decides the
`review-gate` commit status: success | pending | failure.

  success  tier needs no human, or enough fresh approvals exist
  pending  waiting on a human (shows up yellow, blocks merge)
  failure  blocked: too large, security FAIL, or changes requested

Approvals count only when they are:
  - from someone other than the PR author
  - from a human account (bots never count, including the review agents)
  - from an allowed author_association (OWNER/MEMBER/COLLABORATOR by default)
  - on the current head commit (when require_fresh_approval = true)

Critical-path *ownership* is enforced natively by CODEOWNERS + branch
protection. This gate enforces *that a human looked at all*, for every tier
that needs one, including PRs escalated by size, labels, or the security job.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy import DEFAULT_POLICY, Policy, load_policy  # noqa: E402

STATUS_DESCRIPTION_MAX = 140  # GitHub commit status limit


@dataclass
class GateResult:
    state: str
    description: str
    tier: str
    min_approvals: int
    approvals: list[str] = field(default_factory=list)
    stale_approvals: list[str] = field(default_factory=list)
    changes_requested_by: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def latest_review_state_by_user(reviews: list[dict]) -> dict[str, dict]:
    """GitHub semantics: COMMENTED reviews don't change a user's verdict;
    DISMISSED clears it. Latest non-comment review wins."""
    ordered = sorted(reviews, key=lambda r: r.get("submitted_at") or "")
    latest: dict[str, dict] = {}
    for r in ordered:
        user = (r.get("user") or {}).get("login")
        if not user:
            continue
        state = (r.get("state") or "").upper()
        if state == "COMMENTED" or state == "PENDING":
            continue
        if state == "DISMISSED":
            latest.pop(user, None)
            continue
        latest[user] = r
    return latest


def evaluate(
    policy: Policy,
    classification: dict,
    reviews: list[dict],
    head_sha: str,
    author: str,
) -> GateResult:
    tier = classification["tier"]
    min_approvals = int(classification["min_approvals"])
    reasons = list(classification.get("reasons", []))

    if classification.get("blocked"):
        return GateResult("failure", classification.get("block_reason") or "blocked",
                          tier, min_approvals, reasons=reasons)

    approvals, stale, changes = [], [], []
    for user, r in latest_review_state_by_user(reviews).items():
        u = r.get("user") or {}
        if user == author or u.get("type") == "Bot" or user.endswith("[bot]"):
            continue
        if r.get("author_association") not in policy.approver_associations:
            continue
        state = r["state"].upper()
        if state == "CHANGES_REQUESTED":
            changes.append(user)
        elif state == "APPROVED":
            if policy.require_fresh_approval and r.get("commit_id") != head_sha:
                stale.append(user)
            else:
                approvals.append(user)

    base = dict(tier=tier, min_approvals=min_approvals, approvals=sorted(approvals),
                stale_approvals=sorted(stale), changes_requested_by=sorted(changes),
                reasons=reasons)

    if changes:
        return GateResult("failure", f"Changes requested by {', '.join(sorted(changes))}", **base)

    if min_approvals == 0:
        return GateResult("success", f"Tier '{tier}': automated gates only", **base)

    if len(approvals) >= min_approvals:
        return GateResult("success",
                          f"Tier '{tier}': approved by {', '.join(sorted(approvals))}", **base)

    need = min_approvals - len(approvals)
    desc = f"Tier '{tier}': needs {need} more human approval(s)"
    if stale:
        desc += f" ({len(stale)} stale, re-approve latest commit)"
    return GateResult("pending", desc, **base)


def to_markdown(res: GateResult, classification: dict) -> str:
    icon = {"success": "PASS", "pending": "WAITING", "failure": "BLOCKED"}[res.state]
    lines = [
        f"### review-gate: {icon}",
        "",
        f"**{res.description}**",
        "",
        f"- Tier: `{res.tier}` (min approvals: {res.min_approvals})",
        f"- Changed lines (counted): {classification.get('changed_lines', '?')}",
    ]
    if res.approvals:
        lines.append(f"- Fresh approvals: {', '.join(res.approvals)}")
    if res.stale_approvals:
        lines.append(f"- Stale approvals (older commit): {', '.join(res.stale_approvals)}")
    if res.changes_requested_by:
        lines.append(f"- Changes requested: {', '.join(res.changes_requested_by)}")
    lines.append("")
    lines.append("Why this tier:")
    lines += [f"- {r}" for r in res.reasons]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--classification", required=True)
    ap.add_argument("--reviews", required=True, help="JSON from GET /pulls/{n}/reviews")
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--policy", default=str(DEFAULT_POLICY))
    args = ap.parse_args(argv)

    policy = load_policy(args.policy)
    with open(args.classification) as fh:
        classification = json.load(fh)
    with open(args.reviews) as fh:
        reviews = json.load(fh)
    # `gh api --paginate` can emit concatenated arrays; tolerate a list of lists.
    if reviews and isinstance(reviews[0], list):
        reviews = [r for page in reviews for r in page]

    res = evaluate(policy, classification, reviews, args.head_sha, args.author)
    res.description = res.description[:STATUS_DESCRIPTION_MAX]

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(to_markdown(res, classification))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"state={res.state}\n")
            fh.write(f"description={res.description}\n")

    print(json.dumps(asdict(res), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
