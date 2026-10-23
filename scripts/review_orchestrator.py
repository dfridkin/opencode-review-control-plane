#!/usr/bin/env python3
"""Layer 2 multi-agent PR review, orchestrated in code rather than by a model.

  hunters (parallel, one per focus area)
    -> merge + dedupe + confidence floor
    -> verifiers (parallel, one per candidate, each tries to refute it)
    -> confirmed findings only

The fan-out lives here instead of in an orchestrator prompt. Open-weight models are much
more reliable at one focused task with a JSON answer than at deciding when and how to spawn
subagents, so the code makes that decision.

  python3 scripts/review_orchestrator.py --base <sha> --diff pr.diff --out findings.json
  (model from --model or REVIEW_MODEL)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_run  # noqa: E402

ROOT = agent_run.ROOT
PROMPTS = ROOT / ".github" / "review-prompts"
AREAS = ["logic", "data integrity and scoping", "error handling and resources",
         "API contracts and tests", "concurrency"]
RANK = {"nit": 0, "minor": 1, "major": 2, "blocker": 3}
CONFIDENCE_FLOOR = 0.6
DEDUPE_LINES = 3

# Request -> Result. Swappable for tests.
Runner = Callable[[agent_run.Request], agent_run.Result]
AGENTS = ROOT / ".opencode" / "agents"


def default_runner(req: agent_run.Request) -> agent_run.Result:
    return agent_run.run(req, retries=1)


def load(path: Path) -> str:
    return agent_run.strip_frontmatter(path.read_text()) if path.exists() else ""


@dataclass
class Outcome:
    findings: list[dict]
    dropped_unverified: int
    errors: list[str] = field(default_factory=list)
    candidates: int = 0

    def to_json(self) -> dict:
        counts = {k: sum(1 for f in self.findings if f["severity"] == k) for k in RANK}
        summary = (f"{len(self.findings)} verified finding(s) from {self.candidates} candidate(s): "
                   f"{counts['blocker']} blocker, {counts['major']} major, {counts['minor']} minor, "
                   f"{counts['nit']} nit.")
        if self.errors:
            summary += f" Review incomplete: {len(self.errors)} agent call(s) failed."
        return {"findings": self.findings, "dropped_unverified": self.dropped_unverified,
                "summary": summary, "errors": self.errors}


# ---------------------------------------------------------------------------
# Pure steps
# ---------------------------------------------------------------------------

def dedupe(candidates: list[dict], floor: float = CONFIDENCE_FLOOR, cap: int = 25) -> list[dict]:
    """Same file and within DEDUPE_LINES lines = same issue; keep the strongest.
    Drop low-confidence candidates. Cap to the strongest `cap` to bound verifier cost."""
    def strength(c):
        return (RANK.get(c.get("severity"), 0), float(c.get("confidence", 0)))

    kept: list[dict] = []
    for c in sorted(candidates, key=strength, reverse=True):
        if float(c.get("confidence", 0)) < floor:
            continue
        dup = any(k["file"] == c["file"] and abs(int(k["line"]) - int(c["line"])) <= DEDUPE_LINES
                  for k in kept)
        if not dup:
            kept.append(c)
    return kept[:cap]


def apply_verdict(candidate: dict, verdict: dict) -> dict | None:
    """Keep only confirmed findings. The verifier may lower severity, never raise it."""
    if verdict.get("status") != "confirmed":
        return None
    sev = candidate["severity"]
    v_sev = verdict.get("severity", sev)
    if RANK.get(v_sev, 99) < RANK[sev]:
        sev = v_sev
    return {**candidate, "severity": sev,
            "evidence": candidate["evidence"] + (f"\n\nVerifier: {verdict['reason']}" if verdict.get("reason") else "")}


# ---------------------------------------------------------------------------

def orchestrate(model: str | None, base: str, diff_path: Path,
                runner: Runner = default_runner, areas: list[str] | None = None,
                concurrency: int = 4) -> Outcome:
    areas = areas or AREAS
    hunt_rules = "\n\n".join(filter(None, [load(AGENTS / "bug-hunter.md"),
                                           load(PROMPTS / "bug-hunt.md"),
                                           load(ROOT / "REVIEW.md")]))
    verify_rules = load(AGENTS / "finding-verifier.md")
    cand_schema = json.loads((PROMPTS / "candidates.schema.json").read_text())
    verdict_schema = json.loads((PROMPTS / "verdict.schema.json").read_text())
    errors: list[str] = []

    def hunt(area: str) -> list[dict]:
        req = agent_run.Request(
            instructions=hunt_rules, schema=cand_schema, input_path=diff_path, model=model,
            task=(f"Focus area: {area}.\nBase commit: {base}. The PR diff is provided as input. "
                  "Read surrounding files in the working tree as needed."))
        try:
            res = runner(req)
        except Exception as e:  # noqa: BLE001 - one failed agent must not sink the review
            errors.append(f"hunter[{area}]: {e}")
            return []
        if res.data is None:
            errors.append(f"hunter[{area}]: invalid output ({'; '.join(res.errors[:2])})")
            return []
        return [c for c in res.data.get("candidates", []) if c.get("severity") in RANK]

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        raw = [c for batch in pool.map(hunt, areas) for c in batch]
    candidates = dedupe(raw)

    def verify(c: dict) -> dict | None:
        req = agent_run.Request(
            instructions=verify_rules, schema=verdict_schema, input_path=diff_path, model=model,
            task=f"Base commit: {base}. Candidate finding to verify or refute:\n{json.dumps(c)}")
        try:
            res = runner(req)
        except Exception as e:  # noqa: BLE001
            errors.append(f"verifier[{c['file']}:{c['line']}]: {e}")
            return None
        if res.data is None:
            errors.append(f"verifier[{c['file']}:{c['line']}]: invalid output")
            return None
        return apply_verdict(c, res.data)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        verified = list(pool.map(verify, candidates))
    findings = [f for f in verified if f]
    findings.sort(key=lambda f: (-RANK[f["severity"]], f["file"], f["line"]))
    return Outcome(findings, len(candidates) - len(findings), errors, len(candidates))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.environ.get("REVIEW_MODEL") or None)
    ap.add_argument("--base", required=True)
    ap.add_argument("--diff", required=True)
    ap.add_argument("--concurrency", type=int, default=int(os.environ.get("REVIEW_CONCURRENCY") or 4))
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    out = orchestrate(args.model, args.base, Path(args.diff), concurrency=args.concurrency)
    Path(args.out).write_text(json.dumps(out.to_json(), indent=2) + "\n")
    print(out.to_json()["summary"])
    # Every hunter failed: report no output rather than a false "clean".
    return 2 if out.errors and out.candidates == 0 and len(out.errors) >= len(AREAS) else 0


if __name__ == "__main__":
    sys.exit(main())
