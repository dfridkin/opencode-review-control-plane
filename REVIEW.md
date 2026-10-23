# Review instructions

Read by the self-hosted review (`scripts/review_orchestrator.py`) as part of every hunter's instructions.
Keep review-only rules here. Project-wide rules belong in `AGENTS.md`.

Changes to this file are scored against `evals/` before they merge.

## What each severity means here

- Blocker: data loss, security exposure, wrong results for normal inputs, a migration that is not
  backward compatible, or a query that is not scoped to the caller's tenant.
- Major: wrong results for a realistic edge case, or a broken API contract.
- Minor: wrong only for unlikely inputs, with a workaround.
- Nit: real but harmless. Style, naming, and refactoring suggestions are Nit at most.

## Cap the noise

- Report at most three Nits per review.
- Every finding needs a file:line citation and a concrete triggering input. Inference from a
  function's name is not evidence.

## Do not report

- Anything CI already enforces: lint, formatting, types, coverage thresholds
- Generated files, lockfiles, vendored code
- "Consider adding tests" without naming the specific failure path that is untested

## Always check

- New or changed API routes have an integration test
- Database queries are scoped to the caller's tenant
- Log lines do not include emails, user IDs, tokens, or request bodies
- Retries only wrap idempotent operations
- Tests added in this PR can fail: they assert behavior, not mock calls
