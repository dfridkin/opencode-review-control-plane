#!/usr/bin/env bash
# Create or update a single PR comment identified by a hidden marker.
# Usage: upsert_comment.sh <marker> <body-file>
# Env: GH_TOKEN, REPO, PR_NUMBER
set -euo pipefail
marker="<!-- $1 -->"
body_file="$2"
: "${REPO:?}" "${PR_NUMBER:?}"

body="$(printf '%s\n%s\n' "$marker" "$(cat "$body_file")")"
existing="$(gh api --paginate "repos/${REPO}/issues/${PR_NUMBER}/comments" \
  --jq ".[] | select(.body | startswith(\"${marker}\")) | .id" | tail -n1)"

if [[ -n "$existing" ]]; then
  gh api -X PATCH "repos/${REPO}/issues/comments/${existing}" -f body="$body" > /dev/null
else
  gh api -X POST "repos/${REPO}/issues/${PR_NUMBER}/comments" -f body="$body" > /dev/null
fi
