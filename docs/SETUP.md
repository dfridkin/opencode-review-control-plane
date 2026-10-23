# Setup

## 1. Secrets and variables

| Name | Kind | Purpose |
|---|---|---|
| `REVIEW_MODEL` | variable | `provider/model`, e.g. `openweight/<model id>`. Used by agent-review, the security job, and evals. |
| `REVIEW_RUNS_ON` | variable, optional | JSON runner spec for the review jobs. Default `"ubuntu-latest"`. Use e.g. `["self-hosted","gpu"]` when the model endpoint is only reachable from your network. |
| `REVIEW_BLOCK_ON` | variable, optional | `none` (default), `blocker`, or `major`. Controls whether `agent-review` fails its check. |
| `LLM_BASE_URL` | variable | The `openweight` provider's OpenAI-compatible endpoint, e.g. `http://gpu-box:8000/v1`. |
| `LLM_API_KEY` | secret, optional | Only if that endpoint requires a key. |
| `LLM_CONTEXT` | variable, optional | Context window of the model in tokens (default 131072). Match your server's setting. |

The review jobs post comments with the workflow's own `GITHUB_TOKEN`; no GitHub App is needed.

### Serving an open-weight model

Any server with an OpenAI-compatible `/v1/chat/completions` endpoint and tool calling works:
vLLM, llama.cpp's `llama-server`, Ollama, or LM Studio. Checks before you point CI at it:

- `curl $LLM_BASE_URL/models` returns the model ID you put in `REVIEW_MODEL` (after `openweight/`).
- Tool calling is enabled on the server (vLLM needs its tool-call parser flags for your model family).
- The context window is at least 64K tokens. Ollama defaults much lower; raise `num_ctx`.
- The runner can reach the endpoint. A self-hosted runner on the same network is the usual setup;
  set `REVIEW_RUNS_ON` to its labels.
- Run `evals/run_evals.py --live --model openweight/<id>` against your incidents before trusting it in CI.

The same endpoint works for local development: export `LLM_BASE_URL` and pick the model in opencode
with `/models`. The provider is defined in `opencode.json`; replace the example model entry with yours.

## 2. Labels

The workflows create labels as needed. Two conventions you maintain by hand:

- `incident-cause`: put it on any merged PR that caused an incident. Metrics count it, and it's your
  queue of new eval cases.
- `risk:high`: any maintainer can add it to force a PR into the `critical` tier.

## 3. Branch protection (or a ruleset) on `main`

- Require a pull request before merging
- Required approvals: **0**. The `review-gate` status enforces approvals by tier. A blanket
  "1 approval" rule defeats the point.
- Require review from Code Owners: **on**. CODEOWNERS only lists `critical` paths, so this
  only bites where it should.
- Dismiss stale approvals when new commits are pushed: **on**
- Required status checks: `verify`, `control-plane`, `review-gate`
- Require merge queue: recommended (see below)
- Do not allow bypassing for agents' identities. Admin bypass for humans is your call; it shows in the audit log.

## 4. Merge queue

`ci.yml` and `review-pipeline.yml` already listen for `merge_group`. The pipeline posts
`review-gate: success` on merge-group commits because only PRs that already satisfied protection
can enter the queue.

## 5. Release layer (your deploy tooling)

The template stops at merge. The rest of the stack is what makes it safe to merge with less reading:

- Feature flags for anything user-facing from a `low` or `elevated` PR. Default off.
- Staged rollout: internal users or a canary slice first, with automatic halt on error-rate or latency SLOs.
- One-command rollback that is exercised regularly, not just documented. If rollback takes an hour,
  your review bar should be higher.
- Link the deploy ID back to the PR so `incident-cause` labeling takes seconds.

## 6. Bootstrapping

The PR that first adds this template can't be judged by it: the base branch has no policy or
scripts yet, so `review-gate` stays pending. Merge that one PR with an admin override after a
normal human review, or land it in two steps (scripts and policy first, workflows second).

## 7. Local developer setup

Nothing to install beyond opencode, git, python3, and make. `opencode.json`, `AGENTS.md`, and the
files in `.opencode/` load automatically. Useful env vars for the verify plugin:

- `AGENT_VERIFY_CMD`: override `make verify` (e.g. a faster subset during exploration)
- `AGENT_VERIFY_MAX_BLOCKS`: attempts before the plugin lets the agent stop (default 3)
- `AGENT_VERIFY_SKIP=1`: bypass for a session. Humans only.
