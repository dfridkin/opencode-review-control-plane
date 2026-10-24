# opencode-review-control-plane

A template for reviewing agent-written code without a person reading every diff, and without
raising reverts or incidents. Built for [opencode](https://opencode.ai) and open-weight models you
host yourself, on GitHub Actions.

- Based on [How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/)
  (Anthropic, Oct 2026)
- Companion article: *[title TBD]* on [dmitriyfridkin.substack.com](https://dmitriyfridkin.substack.com/)
- Using Claude Code? See [claude-review-control-plane](https://github.com/dfridkin/claude-review-control-plane).

This is my adaptation of the post for open-weight models, built from public documentation. It is
not affiliated with or endorsed by Anthropic or the opencode project.

## Why a separate version

Some teams can't send source code to a hosted model, or want review to run at a fixed hardware
cost. The process from the post carries over unchanged: automated checks on every change, people
deciding the things machines are bad at, and a risk gate that sets how much human review each
change gets. Three things change:

- **Orchestration moves into code.** Smaller open-weight models handle one focused task with a JSON
  answer far better than deciding when to start subagents, so a Python script runs the reviewers.
- **Replies are validated.** The model's JSON is checked against a schema and retried once. A reply
  that never validates sends the PR to a person instead of passing it.
- **The eval set picks the model.** Score candidate models on your own past incidents before one
  reviews your PRs.

## What's in it

1. **Pre-PR.** A plugin blocks the agent from finishing while `make verify` fails. `/pre-pr` runs a
   fresh-context review subagent and drafts an evidence-first PR description.
2. **AI review.** One bug-hunting agent per focus area, then a verifier that tries to refute each
   finding. Only confirmed findings get posted. A PR still blocked after three rounds gets a
   `needs-human` label and a Slack notice.
3. **Security review.** Can pass, route to a human, or block.
4. **Required CI.** Lint, types, tests, coverage floor.
5. **Risk gate.** Docs merge on automated checks; ordinary code needs one person; auth, billing,
   infra, and the review system itself need a code owner.
6. **Evals and metrics.** Past incidents replayed against the reviewer, and revert share tracked.

## Quick start

```bash
# 1. Copy into your repo (or click "Use this template")
cp -r .opencode .github ci evals scripts docs AGENTS.md REVIEW.md opencode.json review-policy.toml Makefile /path/to/your-repo/

# 2. Point opencode at your model server and set the model ID in opencode.json
export LLM_BASE_URL=http://gpu-box:8000/v1     # vLLM, llama.cpp, Ollama, LM Studio

# 3. Set your critical paths and owners in review-policy.toml, then:
make codeowners

# 4. Wire lint, typecheck, and test in the Makefile. `make verify` fails until you do.

# 5. Replace evals/incidents/ with your own incidents and score the model
python3 evals/run_evals.py --live --model openweight/<model id> --runs 3 --write-baseline
```

Then set the repository variables (`REVIEW_MODEL`, `LLM_BASE_URL`, and `REVIEW_RUNS_ON` for a
self-hosted runner that can reach the model), set branch protection, and roll out in phases:

- [docs/SETUP.md](docs/SETUP.md): variables, serving the model, branch protection
- [docs/ROLLOUT.md](docs/ROLLOUT.md): shadow mode first, then gate the low tier, then tune
- [docs/DESIGN.md](docs/DESIGN.md): how it maps to the post, trust model, known limits, file layout

Everything is Python 3.11+ stdlib and bash. `make control-plane-test` runs the template's own tests.

## License

[MIT](LICENSE) © Dmitriy Fridkin ([FRI Solutions](https://frisolutions.dev))
