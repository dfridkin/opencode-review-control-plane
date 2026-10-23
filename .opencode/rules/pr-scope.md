# PR scope rules

Small, single-purpose PRs are what make automated review trustworthy and human review fast.

- One concern per PR. A refactor needed for a feature ships first, as its own PR.
- Stay under `human_review_above_lines` in `review-policy.toml` (counted lines exclude lockfiles and
  generated code). Over it, the PR is routed to a human no matter what it touches.
  Over `hard_max_lines`, the gate fails.
- No drive-by changes: formatting, renames, or "while I was here" fixes in unrelated files.
- Regenerated files (codegen, lockfiles, snapshots) go in a separate commit with the command that
  produced them in the message.
- Dependency additions or upgrades: separate PR, with the reason and the changelog link.
