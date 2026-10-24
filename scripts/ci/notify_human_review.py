#!/usr/bin/env python3
"""Post a `needs-human` notice for a PR to a Slack incoming webhook.

  SLACK_WEBHOOK_URL=... python3 scripts/ci/notify_human_review.py \
      --pr-url https://github.com/org/repo/pull/42 --title "Fix pagination" \
      --author agent-bot --tier elevated --reason "3 review rounds with verified blockers" \
      [--findings open.json] [--dry-run]

Env:
  SLACK_WEBHOOK_URL  incoming webhook (secret). Unset: prints a notice and exits 0.
  SLACK_MENTION      optional, e.g. "<!subteam^S0123ABCD>" for a user group, or "<!here>".

A failed post is a warning, never a failed job: notification must not decide whether a PR
can merge. The gate does that.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

MAX_FINDINGS = 5


def esc(text: object) -> str:
    """Escape Slack mrkdwn control characters so PR text can't mention or link anything."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_payload(pr_url: str, title: str, author: str, tier: str, reason: str,
                  findings: list[dict], mention: str = "") -> dict:
    head = f"PR needs a human: <{pr_url}|{esc(title)}>"
    lines = [f"*{head}*",
             f"Reason: {esc(reason)}",
             f"Author: {esc(author)}" + (f" · Tier: `{esc(tier)}`" if tier else "")]
    if findings:
        lines.append("Open findings:")
        for f in findings[:MAX_FINDINGS]:
            lines.append(f"• [{esc(f.get('severity'))}] `{esc(f.get('file'))}:{esc(f.get('line'))}` "
                         f"{esc(f.get('title', ''))}")
        if len(findings) > MAX_FINDINGS:
            lines.append(f"…and {len(findings) - MAX_FINDINGS} more on the PR")
    if mention:
        lines.insert(0, mention)  # set by the repo admin, not by the PR: not escaped
    text = "\n".join(lines)
    return {"text": f"PR needs a human: {esc(title)} {pr_url}",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pr-url", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--author", default="")
    ap.add_argument("--tier", default="")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--findings", help="JSON file: a list of findings, or an object with 'open'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    findings: list[dict] = []
    if args.findings and os.path.exists(args.findings):
        try:
            with open(args.findings) as fh:
                data = json.load(fh)
            findings = data.get("open", []) if isinstance(data, dict) else list(data)
        except (json.JSONDecodeError, OSError):
            findings = []

    payload = build_payload(args.pr_url, args.title, args.author, args.tier, args.reason,
                            findings, os.environ.get("SLACK_MENTION", ""))
    if args.dry_run:
        json.dump(payload, sys.stdout, indent=2)
        print()
        return 0

    url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not url:
        print("::notice::SLACK_WEBHOOK_URL is not set; skipping the Slack notice")
        return 0
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"slack: {resp.status}")
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::Slack notice failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
