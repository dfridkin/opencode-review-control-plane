# Design notes

Reference material behind the template: how it maps to the Claude Code team's process, what it
trusts, and what it leaves out. Start with the [README](../README.md) and [SETUP.md](SETUP.md).

## How it maps to the Claude Code team's process

| # | Layer | What it does | Where it lives |
|---|---|---|---|
| 0 | Baseline | Revert share, incident-linked PRs, review rounds, time to merge, human-approved share | `scripts/review_metrics.py`, `review-metrics.yml` |
| 1 | Incident evals | Past incidents replayed against the reviewer, one baseline per model | `evals/`, `review-evals.yml` |
| 2 | Pre-PR | Plugin blocks "done" while `make verify` fails; `/pre-pr` runs a fresh-context review subagent | `.opencode/plugins/`, `scripts/hooks/`, `.opencode/commands/`, `.opencode/agents/` |
| 3 | PR review | One `bug-hunter` per focus area, then a `finding-verifier` per candidate; only confirmed findings get posted | `agent-review.yml`, `scripts/review_orchestrator.py` |
| 4 | Security | Blocking review: `pass`, `escalate` (route to a human), or `fail` (block) | `review-pipeline.yml`, `.github/review-prompts/security.md` |
| 5 | CI | Lint, types, tests, coverage floor. Fails until you wire it | `ci.yml`, `Makefile` |
| 6 | Human gate | Risk tier from paths and size; model output can only escalate | `review-policy.toml`, `scripts/risk_classify.py`, `scripts/review_gate.py` |
| 7 | Release | Merge queue, flags, staged rollout, fast rollback | your deploy tooling |

### Risk tiers

| Tier | Default paths | Human approvals |
|---|---|---|
| `low` | Docs, copy, snapshots: an explicit list | None once automated checks pass |
| `elevated` | Ordinary code (the default for any unlisted path), APIs, dependencies, config | One |
| `critical` | Auth, billing, migrations, infra, and the review system itself | One, from a code owner. The Claude Code team uses two; raise `min_approvals` if you have the people. |

### Where a person still decides

| Decision | Where it happens here |
|---|---|
| Should we build it? | `.github/ISSUE_TEMPLATE/agent-task.yml`: outcome, acceptance criteria, out of scope |
| Risky code | `review-policy.toml` tiers and `CODEOWNERS` |
| Architecture | `critical` and `elevated` tiers, plus "Where a human should look" in the PR template |
| Reading the evidence | The PR body `/pre-pr` drafts: commands run, tests that prove the change, findings fixed and left |
| Owning the outcome | Approvals tied to the current commit, `incident-cause` labels feeding the eval set, weekly metrics |

## What differs from a Claude Code setup

| Concern | Here |
|---|---|
| Project instructions | `AGENTS.md`, plus `.opencode/rules/*.md` loaded through `instructions` in `opencode.json` (always loaded, not path-scoped) |
| Verify before done | `.opencode/plugins/verify-on-idle.ts` runs `scripts/hooks/verify-on-stop.sh` on `session.idle` and sends failures back into the session |
| Protected paths | `permission.edit` in `opencode.json` |
| Review fan-out | `scripts/review_orchestrator.py`, not a model coordinating its own subagents |
| Structured output | `scripts/agent_run.py` extracts JSON from the reply, validates it, and retries once with the errors |
| CI isolation | `ci/opencode.ci.json` passed through `OPENCODE_CONFIG_CONTENT`, which outranks the repo's `opencode.json` |

## Trust model

The PR under review must not be able to change the rules it's judged by.

- The gate runs from a checkout of the **base** commit: policy and scripts included.
- Review jobs restore agent files, prompts, scripts, and opencode config from the base commit.
- In CI, opencode runs with `ci/opencode.ci.json`: read, grep, and `git diff/log/show` only; no edits,
  web, or subagents; every other permission denied so nothing waits on an approval prompt.
- Any change to a control-plane file lands in the `critical` tier and needs an owner's approval.
- Model verdicts can raise a tier but never lower it, so a prompt-injected diff can't remove itself
  from human review.
- A missing, errored, or invalid verdict routes to a human.
- Workflows use `pull_request`, never `pull_request_target` with a PR checkout.

## Anti-patterns this is built to prevent

- **"Just review harder."** Reviewer hours don't scale with agent output. Systems do.
- **Approving agent PRs you didn't read** because a bot said LGTM. Bot approvals never count.
- **Agents writing their own acceptance tests.** `tests/acceptance/**` is denied to the agent and owned by people.
- **2,000-line agent PRs.** Over the size budget routes to a human; over the hard max blocks.
- **Tuning the reviewer on vibes.** Every reviewer change is scored against real incidents.
- **Choosing a model on public benchmarks.** Score it on your own incidents first.
- **A gate that passes vacuously.** `make verify` fails until it verifies something.

## Not included (yet)

- **Converging review loops:** a round cap, diff-only re-review after the first passes, and no
  repeated findings. The post reports these cut PRs needing five or more rounds from 8.9% to 1.5%.
  `/pre-pr` caps itself at one re-review; CI review re-reviews the full diff on every push.
- **Verifier voting:** the post uses several verifiers per finding; this template uses one.
- **Daily eval runs.** `review-evals.yml` runs weekly and on reviewer changes.
- **Feature flags and staged rollout.** Use your own deploy tooling.

## Known limits

- Fork PRs get no secrets, so the security verdict is `unavailable` and they always route to a human.
- The eval harness reviews diffs without full repository context. Treat it as a regression floor,
  not a quality score.
- Permission denies cover the agent's edit tools, not arbitrary shell commands. The `critical` tier
  is the actual enforcement for protected paths.
- The verify plugin relies on the `session.idle` event and `session.promptAsync`. It was type-checked
  against `@opencode-ai/plugin`; recheck it when you upgrade opencode.
- The PR that first adds this template can't be judged by it. See "Bootstrapping" in `SETUP.md`.
- The seed `evals/baselines/replay.json` comes from canned fixtures. Generate baselines on your own incidents.

## Layout

```
AGENTS.md                  project instructions (opencode reads it every session)
opencode.json              permissions, rules, open-weight provider example
.opencode/
  agents/                  diff-reviewer, bug-hunter, finding-verifier, security-reviewer
  commands/pre-pr.md       /pre-pr: verify, classify, fresh review, evidence-first PR body
  plugins/verify-on-idle.ts
  rules/                   testing, PR scope, security
ci/opencode.ci.json        locked-down config for CI review jobs
.github/
  workflows/               ci, agent-review, review-pipeline, review-gate-refresh, review-evals, review-metrics
  review-prompts/          bug-hunt.md, security.md + JSON schemas
  CODEOWNERS               generated from review-policy.toml
  pull_request_template.md
  ISSUE_TEMPLATE/agent-task.yml
evals/                     incidents/, baselines/, run_evals.py
scripts/
  agent_run.py             opencode run -> validated JSON
  review_orchestrator.py   hunters -> dedupe -> verifiers
  policy.py, risk_classify.py, review_gate.py, sync_codeowners.py, review_metrics.py
  hooks/verify-on-stop.sh  called by the verify plugin
  ci/                      workflow glue
  tests/                   unit tests (stdlib only)
REVIEW.md  review-policy.toml  Makefile
```

## References

- [How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/), Anthropic, Oct 2026
- opencode: [agents](https://opencode.ai/docs/agents/), [rules](https://opencode.ai/docs/rules/), [permissions](https://opencode.ai/docs/permissions/), [plugins](https://opencode.ai/docs/plugins/), [providers](https://opencode.ai/docs/providers/), [config](https://opencode.ai/docs/config/), [CLI](https://opencode.ai/docs/cli/)
