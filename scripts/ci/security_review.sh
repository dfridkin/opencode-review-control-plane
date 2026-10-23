#!/usr/bin/env bash
# Blocking security review (Layer 3). Runs opencode through scripts/agent_run.py.
#
# The workflow restores the prompts, schemas, agent sources, and CI config from the BASE
# commit before calling this, so the PR under review can't rewrite its own reviewer.
#
# Env:
#   BASE_SHA        required
#   REVIEW_MODEL    provider/model, e.g. openweight/qwen3-coder (required)
#   LLM_BASE_URL / LLM_API_KEY for the `openweight` provider
#   OUT_DIR         where to write security.json (default: $RUNNER_TEMP or ./)
#
# Writes $OUT_DIR/security.json and `verdict=<v>` to $GITHUB_OUTPUT.
# Any failure produces verdict "unavailable", which the gate routes to a human.

set -euo pipefail

: "${BASE_SHA:?BASE_SHA is required}"
out_dir="${OUT_DIR:-${RUNNER_TEMP:-.}}"
mkdir -p "$out_dir"
diff_file="$out_dir/pr.diff"
result_file="$out_dir/security.json"

git diff --no-color "${BASE_SHA}...HEAD" > "$diff_file"

unavailable() {
  jq -n --arg why "$1" '{verdict:"unavailable", findings:[], risk_notes:$why}' > "$result_file"
}

credentials_present() {
  [[ -n "${REVIEW_MODEL:-}" ]] && { [[ "${REVIEW_MODEL}" != openweight/* ]] || [[ -n "${LLM_BASE_URL:-}" ]]; }
}

if [[ ! -s "$diff_file" ]]; then
  jq -n '{verdict:"pass", findings:[], risk_notes:"Empty diff."}' > "$result_file"
elif ! credentials_present; then
  unavailable "No model configured or reachable (fork PR, or REVIEW_MODEL / LLM_BASE_URL not set). Routed to a human."
else
  if python3 scripts/agent_run.py \
      --agent .opencode/agents/security-reviewer.md \
      --task "Security review of this pull request. Base commit: ${BASE_SHA}. The unified diff is provided as input. Apply the rules in .github/review-prompts/security.md, quoted below, exactly.

$(cat .github/review-prompts/security.md)" \
      --schema .github/review-prompts/security.schema.json \
      --input "$diff_file" \
      --out "$result_file" 2> "$out_dir/security.stderr"; then
    :
  else
    unavailable "Security reviewer produced no valid verdict: $(tail -c 300 "$out_dir/security.stderr" | tr '\n' ' ')"
  fi
fi

verdict="$(jq -r '.verdict' "$result_file")"
echo "Security verdict: $verdict"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then echo "verdict=$verdict" >> "$GITHUB_OUTPUT"; fi
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Security review: \`$verdict\`"
    echo
    jq -r '.risk_notes' "$result_file"
    echo
    jq -r '.findings[] | "- **\(.severity)** `\(.file):\(.line)` [\(.category)] \(.title)\n  - \(.evidence)"' "$result_file"
  } >> "$GITHUB_STEP_SUMMARY"
fi
