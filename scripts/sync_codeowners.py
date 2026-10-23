#!/usr/bin/env python3
"""Keep CODEOWNERS in sync with the owned tiers in review-policy.toml.

  python scripts/sync_codeowners.py --write   regenerate the managed block
  python scripts/sync_codeowners.py --check   exit 1 if CODEOWNERS has drifted (CI)

Only the block between the markers is managed. Anything you write outside it
is left alone. Later CODEOWNERS lines win, so the managed block goes last.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy import DEFAULT_POLICY, REPO_ROOT, Policy, load_policy  # noqa: E402

BEGIN = "# --- BEGIN review-policy managed block (scripts/sync_codeowners.py) ---"
END = "# --- END review-policy managed block ---"
DEFAULT_CODEOWNERS = REPO_ROOT / ".github" / "CODEOWNERS"


def to_codeowners_pattern(glob: str) -> str | None:
    g = glob.strip()
    if g in {"**", "*", "**/*"}:
        return None  # catch-all tiers are not ownership rules
    return "/" + g.lstrip("/") if "/" in g else g


def render_block(policy: Policy) -> str:
    lines = [BEGIN, "# Generated from review-policy.toml. Edit the policy, then run --write."]
    for tier in sorted(policy.tiers, key=lambda t: t.rank):  # riskiest last = wins
        if not tier.owners:
            continue
        lines.append(f"# tier: {tier.name} ({tier.reason})")
        for glob in tier.paths:
            pat = to_codeowners_pattern(glob)
            if pat:
                lines.append(f"{pat} {' '.join(tier.owners)}")
    lines.append(END)
    return "\n".join(lines) + "\n"


def splice(existing: str, block: str) -> str:
    if BEGIN in existing and END in existing:
        pre = existing.split(BEGIN, 1)[0]
        post = existing.split(END, 1)[1].lstrip("\n")
        return pre + block + post
    sep = "" if not existing or existing.endswith("\n") else "\n"
    return existing + sep + ("\n" if existing else "") + block


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY))
    ap.add_argument("--codeowners", default=str(DEFAULT_CODEOWNERS))
    args = ap.parse_args(argv)

    policy = load_policy(args.policy)
    path = Path(args.codeowners)
    existing = path.read_text() if path.exists() else ""
    desired = splice(existing, render_block(policy))

    if args.check:
        if existing != desired:
            print(f"{path} is out of sync with {args.policy}. "
                  "Run: python scripts/sync_codeowners.py --write", file=sys.stderr)
            return 1
        print("CODEOWNERS in sync")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desired)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
