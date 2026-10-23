# Testing rules

Tests are how a reviewer who never reads your diff trusts it. Write them for that reader.

- `tests/acceptance/**` is human-owned. Do not edit it. If an acceptance test looks wrong,
  stop and say so in the PR body. An agent that writes its own pass criteria is grading itself.
- Every behavior change gets a test that fails without the change.
- Assert observable behavior (return values, persisted state, emitted events), not that a mock was called.
- Do not mock the unit under test, or the database layer in tests named "integration".
- Never delete, skip, or loosen an existing assertion to make a change pass. If a test is genuinely
  obsolete, remove it in its own commit and explain why in the PR body.
- No sleeps for synchronization. Use the project's fake clock or explicit waits on a condition.
- Name tests after the behavior: `test_second_page_starts_after_last_item_of_first_page`.
