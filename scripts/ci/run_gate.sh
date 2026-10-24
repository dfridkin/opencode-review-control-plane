#!/usr/bin/env bash
# Compute the risk tier and post the `review-gate` commit status.
# Shared by review-pipeline.yml (after security runs) and review-gate-refresh.yml
# (when a review is submitted or a label changes).
#
# Env (all required unless noted):
#   GH_TOKEN, REPO, PR_NUMBER, HEAD_SHA, PR_AUTHOR
#   SECURITY_VERDICT   pass | escalate | fail | unavailable | pending
#   RUN_URL            optional, linked from the status
#   PR_URL, PR_TITLE   optional; with SLACK_WEBHOOK_APPROVALS set, a Layer 5 notice goes to the
#                      approvals channel when the PR first needs a person at its current tier
#
# Run from a checkout of the BASE branch so the policy and scripts can't be
# modified by the PR they are judging.

set -euo pipefail

: "${REPO:?}" "${PR_NUMBER:?}" "${HEAD_SHA:?}" "${PR_AUTHOR:?}" "${SECURITY_VERDICT:?}"
work="${RUNNER_TEMP:-$(mktemp -d)}/gate"
mkdir -p "$work"

post_status() {  # state description
  gh api "repos/${REPO}/statuses/${HEAD_SHA}" \
    -f state="$1" -f context="review-gate" \
    -f description="${2:0:140}" \
    ${RUN_URL:+-f target_url="$RUN_URL"} > /dev/null
  echo "review-gate -> $1: $2"
}

if [[ "$SECURITY_VERDICT" == "pending" ]]; then
  post_status pending "Waiting for the security review on this commit"
  exit 0
fi

gh api --paginate --slurp "repos/${REPO}/pulls/${PR_NUMBER}/files" > "$work/files.json"
gh api --paginate --slurp "repos/${REPO}/pulls/${PR_NUMBER}/reviews" > "$work/reviews.json"
labels="$(gh api "repos/${REPO}/issues/${PR_NUMBER}/labels" --jq '[.[].name] | join(",")')"

python3 scripts/risk_classify.py \
  --files "$work/files.json" \
  --labels "$labels" \
  --security-verdict "$SECURITY_VERDICT" > "$work/classification.json"

python3 scripts/review_gate.py \
  --classification "$work/classification.json" \
  --reviews "$work/reviews.json" \
  --head-sha "$HEAD_SHA" \
  --author "$PR_AUTHOR" > "$work/gate.json"

state="$(jq -r .state "$work/gate.json")"
desc="$(jq -r .description "$work/gate.json")"
tier="$(jq -r .tier "$work/classification.json")"
post_status "$state" "$desc"

# Keep exactly one tier:<name> label on the PR. Used by scripts/review_metrics.py.
gh label create "tier:${tier}" --repo "$REPO" --color BFD4F2 \
  --description "Risk tier from review-policy.toml" --force > /dev/null 2>&1 || true
for old in $(echo "$labels" | tr ',' '\n' | grep '^tier:' || true); do
  [[ "$old" == "tier:${tier}" ]] || gh pr edit "$PR_NUMBER" --repo "$REPO" --remove-label "$old" > /dev/null || true
done
gh pr edit "$PR_NUMBER" --repo "$REPO" --add-label "tier:${tier}" > /dev/null || true

# Layer 5 Slack notice: once per tier, when this run first puts the PR in a tier that needs a
# person and approvals are still missing. A push that keeps the tier does not post again.
had_tier=0
echo "$labels" | tr ',' '\n' | grep -qx "tier:${tier}" && had_tier=1
need="$(jq -r .min_approvals "$work/classification.json")"
if [[ "$state" == "pending" && "$need" != "0" && "$had_tier" == "0" && -n "${PR_URL:-}" ]]; then
  jq '{reasons: .reasons}' "$work/classification.json" > "$work/reasons.json"
  python3 scripts/ci/notify_slack.py --kind approval --pr-url "$PR_URL" --title "${PR_TITLE:-PR #$PR_NUMBER}" \
    --author "$PR_AUTHOR" --tier "$tier" --reason "$desc" --details "$work/reasons.json" || true
fi
