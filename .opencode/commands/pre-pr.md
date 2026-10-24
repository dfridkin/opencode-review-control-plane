---
description: "Run the pre-PR checklist on the current branch - verify, classify risk, fresh-context review, fix blockers, and draft an evidence-first PR description. Use before opening or updating any pull request."
agent: build
---

# /pre-pr

Goal: the PR that comes out of this is something a human can decide on in two minutes,
or that needs no human at all because the evidence says so.

Base ref: use `$ARGUMENTS` if given, otherwise `origin/main`. Run `git fetch origin` first.

## 1. Verify

Run `make verify`. If it fails, fix the cause and re-run. Never skip, delete, or weaken a test
to get green. If a failure is in a protected path (see AGENTS.md), stop and report it instead.

## 2. Classify

```bash
python3 scripts/risk_classify.py --local <base>
```

- If `blocked` is true because of size: STOP. Propose how to split the branch into
  independently mergeable PRs (each one concern, each under `human_review_above_lines`).
  Do not continue until the user agrees on the split.
- Note the `tier` and `reasons`. They go in the PR body.

## 3. Fresh-context review

Delegate to the `diff-reviewer` subagent with the base ref. Do not review your own diff
in this conversation; you wrote it and will read what you meant, not what you wrote.

Also delegate to the `security-reviewer` subagent if the tier is not `low`, or the diff touches
auth, data access, dependencies, config, or CI.

Run both in parallel when both apply.

## 4. Address findings

- blocker / major: fix, re-run `make verify`, then run `diff-reviewer` ONE more time.
  If new blockers appear on the second pass, stop and report them. Do not loop.
  If the user still wants the PR opened, open it with the `needs-human` label
  (`gh pr create --label needs-human`) and list the open blockers under "Review findings".
  The label routes the PR to a person and notifies the team.
- security `fail`: fix or stop. `escalate`: keep going, but say so prominently in the PR body.
- minor / nit: do not fix unless trivial. List them under "Known, not addressed".
- If you disagree with a finding, keep it in the list with one line on why.

## 5. Draft the PR description

Fill `.github/pull_request_template.md`. Write it for a reviewer who may read only this, not the diff.

- **Intent**: one or two sentences. Link the issue or spec.
- **Risk tier**: tier and reasons from step 2, verbatim.
- **Evidence**: the exact commands you ran and their results. Name the new tests and what each one
  proves would fail without the change.
- **Review findings**: counts, what you fixed, what you left and why.
- **Where a human should look**: only for tiers above `low`. Point to the 1 to 3 places where
  judgment is needed (a design choice, a tradeoff, an assumption you could not verify).
  Do not say "review everything".

Print the description. Do not push, open, or update the PR unless the user asked you to.
