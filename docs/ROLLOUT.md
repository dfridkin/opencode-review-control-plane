# Rollout

Work backwards from the target: most PRs merge on automated gates alone, humans spend their time
on the five decisions, and revert share and incident-linked PRs stay flat or fall.

Each phase has an exit criterion. Don't advance on a calendar; advance on the numbers.

## Phase 0: Measure (1 to 2 weeks)

- Run `scripts/review_metrics.py` over the last 60 to 90 days. Save it as the baseline.
- Start labeling `incident-cause` and harvesting reverts.
- Build 5 to 10 eval cases from real incidents plus 3 to 5 clean controls.

**Exit:** baseline numbers recorded; `evals/run_evals.py --live --runs 3` produces a stable score for
each model you are considering. Pick the one that clears your bar on your own incidents.

## Phase 1: Shadow (2 to 4 weeks)

- Turn on `agent-review.yml` (advisory, `REVIEW_BLOCK_ON=none`) and `review-pipeline.yml`.
- Keep your current human review rules unchanged. Make `review-gate` visible but NOT required.
- Every week: read the findings humans dismissed. Each confirmed false positive becomes a control
  case; each bug the agents missed and a human caught becomes an incident case.

**Exit:** reviewer false-positive rate on your control set is low enough that people stopped
ignoring the comments; the tier the gate computes matches what your senior engineers would pick.

## Phase 2: Gate low-risk paths

- Make `review-gate` a required check. Set required approvals to 0 in branch protection.
- `low` tier PRs (docs, copy, the explicit list in `review-policy.toml`) now merge on automated
  gates. Everything else still needs a human.
- Set `REVIEW_BLOCK_ON=blocker` and add the `review` job from `agent-review.yml` to the required
  checks. Without the second step, a failing review does not stop a merge.

**Exit:** four weeks with revert share and incident-linked share at or below baseline.
If either rises, revert to Phase 1 and look at which tier the bad PRs came from.

## Phase 3: Tune the tiers

- Move paths between tiers based on the `by_tier` metrics. Widen the `low` list one path at a time. A tier with zero reverts and 100%
  human approval may be over-classified. A `low` tier with reverts is under-classified.
- Raise `human_review_above_lines` only if small PRs in `low` are clean and large ones are too.
- Consider `REVIEW_BLOCK_ON=major` once the verifier's precision holds up.

## Ongoing

- Weekly: review metrics, add new incidents to `evals/`.
- On every model or prompt change: `review-evals.yml` gates it.
- Quarterly: re-read `review-policy.toml` with the people who carry the pager.

## Regulated environments

Everything here produces an audit trail: commit statuses with reasons, labels for tier and security
verdict, review records tied to commit SHAs, and eval results tied to each reviewer change.
If your process requires named human sign-off on every change (GxP, SOX, and similar), keep
`min_approvals = 1` on every tier, including `low`, and use this stack to make that sign-off a
two-minute evidence check instead of a line-by-line read. That alone recovers most of the time.
