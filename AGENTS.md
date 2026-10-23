# Project instructions

<!-- Read by opencode at the start of every session. Keep it under ~150 lines.
     Topic rules live in .opencode/rules/ and load through `instructions` in opencode.json. -->

<!-- Template. Replace the bracketed sections with your project's real commands. -->

## Commands

- Verify everything (the idle plugin runs this): `make verify`
- Lint: `[make lint]`
- Typecheck: `[make typecheck]`
- Unit tests: `[make test]`
- Single test: `[pytest path/to/test.py::test_name]`
- Risk tier for your branch: `python3 scripts/risk_classify.py --local origin/main`

## Definition of done

A change is done when ALL of these hold. "It compiles" is not done.

1. `make verify` passes locally. A plugin enforces this when you try to finish; do not work around it.
2. New behavior has a test that fails without the change. Run it once against the old code if unsure.
3. The diff is one concern and under the size budget in `review-policy.toml` (`human_review_above_lines`).
4. You ran `/pre-pr`, addressed every blocker and major finding, and listed the rest in the PR body.
5. The PR body says what a human should look at, if the tier requires one.

## How review works here

Most PRs are not read line by line by a person. Machines check; people decide.

| Layer | What runs | Who owns it |
|---|---|---|
| Pre-PR | Plugin runs `make verify` before you can finish; `/pre-pr` runs a fresh-context review subagent | You (the agent) |
| PR review | Parallel bug-hunter agents, then a verifier agent that tries to refute each finding | CI |
| Security | Blocking security review: pass, escalate, or fail | CI |
| CI | Lint, types, tests, coverage | CI |
| Human gate | Risk tier from `review-policy.toml` decides if a person must approve | People |
| Release | Merge queue, flags, staged rollout, one-command rollback | People + CI |

Because of this, your PR description is evidence for a human who may only read it, not the diff.
Write it so a reviewer can decide in two minutes.

## Protected paths (do not edit)

These are owned by people. Editing them is denied in the agent permission config, and any PR
touching them is routed to a human regardless:

- `tests/acceptance/**`: humans define pass criteria. If one looks wrong, say so in the PR body.
- `evals/**`, `review-policy.toml`, `REVIEW.md`, `.github/**`, `ci/**`
- Agent configuration: `.opencode/`, `opencode.json`, this file

An agent that can edit its own acceptance tests or review rules is grading itself.

## Working style

- For anything bigger than a bug fix, write the plan first: the end state, the interfaces that change,
  and the steps to get there. Work backwards from the end state.
- Model the domain before writing handlers: types and invariants first, then behavior.
- Prefer one PR per concern. If a refactor is needed first, ship it as its own PR.
- Do not add dependencies without saying why in the PR body. New dependencies raise the risk tier.

## Project context

- [Architecture overview: 5 to 10 lines, or a link to docs/architecture.md]
- [Domain terms an agent would get wrong]
- [Known sharp edges: e.g. "all DB queries must be tenant-scoped via `scoped_query()`"]
