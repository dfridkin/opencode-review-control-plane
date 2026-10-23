"""Shared domain model for the review control plane.

Kept dependency-free (stdlib only, Python 3.11+) so it runs on any CI runner
without a pip install step.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = REPO_ROOT / "review-policy.toml"


# ---------------------------------------------------------------------------
# Globs
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """gitignore-like glob -> regex.

    **  any characters including "/"
    *   any characters except "/"
    ?   one character except "/"
    A pattern with no "/" matches the basename at any depth.
    """
    pattern = pattern.strip().lstrip("/")
    anchored = "/" in pattern
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                # "**/" can also match zero directories
                if pattern[i : i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    body = "".join(out)
    if anchored:
        return re.compile(rf"^{body}$")
    return re.compile(rf"^(?:.*/)?{body}$")


def path_matches(path: str, pattern: str) -> bool:
    if path.startswith("./"):
        path = path[2:]
    return bool(_glob_to_regex(pattern).match(path))


def matches_any(path: str, patterns: list[str]) -> str | None:
    for p in patterns:
        if path_matches(path, p):
            return p
    return None


# ---------------------------------------------------------------------------
# Policy model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tier:
    name: str
    rank: int  # higher = riskier
    min_approvals: int
    owners: tuple[str, ...]
    reason: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Policy:
    tiers: tuple[Tier, ...]  # ordered riskiest first
    human_review_above_lines: int
    hard_max_lines: int
    exclude_from_size: tuple[str, ...]
    require_fresh_approval: bool
    approver_associations: tuple[str, ...]
    label_escalations: dict[str, str] = field(default_factory=dict)
    verdict_escalations: dict[str, str] = field(default_factory=dict)
    default_tier: str = ""

    def tier(self, name: str) -> Tier:
        for t in self.tiers:
            if t.name == name:
                return t
        raise KeyError(f"unknown tier {name!r}")

    @property
    def lowest(self) -> Tier:
        return self.tiers[-1]

    @property
    def default(self) -> Tier:
        """Tier for a file that matches no tier's paths."""
        return self.tier(self.default_tier) if self.default_tier else self.lowest

    def first_human_tier(self) -> Tier:
        """Lowest-ranked tier that still requires a human approval."""
        candidates = [t for t in self.tiers if t.min_approvals > 0]
        if not candidates:
            raise ValueError("policy has no tier that requires human approval")
        return min(candidates, key=lambda t: t.rank)


def load_policy(path: Path | str = DEFAULT_POLICY) -> Policy:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    raw_tiers = raw.get("tier", [])
    if not raw_tiers:
        raise ValueError("policy must define at least one [[tier]]")
    n = len(raw_tiers)
    tiers = tuple(
        Tier(
            name=t["name"],
            rank=n - idx,
            min_approvals=int(t.get("min_approvals", 0)),
            owners=tuple(t.get("owners", [])),
            reason=t.get("reason", ""),
            paths=tuple(t.get("paths", [])),
        )
        for idx, t in enumerate(raw_tiers)
    )
    names = [t.name for t in tiers]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate tier names: {names}")

    size = raw.get("size", {})
    approvals = raw.get("approvals", {})
    esc = raw.get("escalation", {})
    policy = Policy(
        tiers=tiers,
        human_review_above_lines=int(size.get("human_review_above_lines", 400)),
        hard_max_lines=int(size.get("hard_max_lines", 1500)),
        exclude_from_size=tuple(size.get("exclude_from_size", [])),
        require_fresh_approval=bool(approvals.get("require_fresh_approval", True)),
        approver_associations=tuple(
            approvals.get("approver_associations", ["OWNER", "MEMBER", "COLLABORATOR"])
        ),
        label_escalations=dict(esc.get("labels", {})),
        verdict_escalations=dict(esc.get("security_verdicts", {})),
        default_tier=str(raw.get("default_tier", "")),
    )
    policy.default  # raises on typo
    for target in [*policy.label_escalations.values(), *policy.verdict_escalations.values()]:
        policy.tier(target)  # raises on typo
    return policy
