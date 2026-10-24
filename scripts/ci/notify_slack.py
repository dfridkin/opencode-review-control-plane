#!/usr/bin/env python3
"""Post a review event for a PR to the Slack channel for that kind of event.

Each kind goes to its own incoming webhook, so each lands in its own channel and the people
watching it see only that context:

  kind          when                                           webhook secret            mention variable
  needs-human   the agent is stuck (Layers 1 and 2)            SLACK_WEBHOOK_NEEDS_HUMAN SLACK_MENTION_NEEDS_HUMAN
  security      security verdict is escalate, fail, or         SLACK_WEBHOOK_SECURITY    SLACK_MENTION_SECURITY
                unavailable (Layer 3)
  approval      the risk gate needs a person to approve        SLACK_WEBHOOK_APPROVALS   SLACK_MENTION_APPROVALS
                (Layer 5)

  SLACK_WEBHOOK_SECURITY=... python3 scripts/ci/notify_slack.py --kind security \
      --pr-url https://github.com/org/repo/pull/42 --title "Fix pagination" --author agent-bot \
      --headline "Security review: escalate" --reason "Touches tenant scoping" \
      [--tier elevated] [--findings findings.json] [--details reasons.json] [--dry-run]

A kind whose webhook is unset is skipped with a notice. A failed post is a warning, never a
failed job: notification must not decide whether a PR can merge. The gate does that.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

MAX_ITEMS = 5
KINDS = {
    "needs-human": ("SLACK_WEBHOOK_NEEDS_HUMAN", "SLACK_MENTION_NEEDS_HUMAN", "PR needs a human"),
    "security": ("SLACK_WEBHOOK_SECURITY", "SLACK_MENTION_SECURITY", "Security review"),
    "approval": ("SLACK_WEBHOOK_APPROVALS", "SLACK_MENTION_APPROVALS", "PR waiting on approval"),
}


def esc(text: object) -> str:
    """Escape Slack mrkdwn control characters so PR text can't mention or link anything."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _capped(items: list[str]) -> list[str]:
    out = items[:MAX_ITEMS]
    if len(items) > MAX_ITEMS:
        out.append(f"…and {len(items) - MAX_ITEMS} more on the PR")
    return out


def build_payload(kind: str, pr_url: str, title: str, author: str, reason: str,
                  tier: str = "", findings: list[dict] | None = None,
                  details: list[str] | None = None, headline: str = "", mention: str = "") -> dict:
    head = esc(headline or KINDS[kind][2])
    lines = [f"*{head}: <{pr_url}|{esc(title)}>*",
             f"Reason: {esc(reason)}",
             f"Author: {esc(author)}" + (f" · Tier: `{esc(tier)}`" if tier else "")]
    if details:
        lines += _capped([f"• {esc(d)}" for d in details])
    if findings:
        lines.append("Open findings:")
        lines += _capped([f"• [{esc(f.get('severity'))}] `{esc(f.get('file'))}:{esc(f.get('line'))}` "
                          f"{esc(f.get('title', ''))}" for f in findings])
    if mention:
        lines.insert(0, mention)  # set by the repo admin, not by the PR: not escaped
    return {"text": f"{head}: {esc(title)} {pr_url}",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}]}


def _load(path: str | None, key: str) -> list:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict):
        return list(data.get(key, []))
    return list(data) if isinstance(data, list) else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", required=True, choices=sorted(KINDS))
    ap.add_argument("--pr-url", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", default="")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--headline", default="")
    ap.add_argument("--tier", default="")
    ap.add_argument("--findings", help="JSON: a list of findings, or an object with 'open' or 'findings'")
    ap.add_argument("--details", help="JSON: a list of strings, or an object with 'reasons'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    webhook_env, mention_env, _ = KINDS[args.kind]
    findings = _load(args.findings, "open") or _load(args.findings, "findings")
    details = [str(d) for d in _load(args.details, "reasons")]
    payload = build_payload(args.kind, args.pr_url, args.title, args.author, args.reason,
                            args.tier, findings, details, args.headline,
                            os.environ.get(mention_env, ""))
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    url = os.environ.get(webhook_env, "")
    if not url:
        print(f"::notice::{webhook_env} is not set; skipping the '{args.kind}' Slack notice")
        return 0
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"slack {args.kind}: {resp.status}")
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::Slack '{args.kind}' notice failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
