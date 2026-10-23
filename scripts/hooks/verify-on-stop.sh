#!/usr/bin/env bash
# Verify-before-done hook, called by .opencode/plugins/verify-on-idle.ts when a session goes idle.
# The agent cannot declare a task finished while verification fails.
#
# Layer 1 of the review stack. Catches the cheapest class of defect (it doesn't
# build, lint, or pass its own tests) before a human or a CI minute is spent.
#
# Behavior:
#   - No-op in CI (CI runs its own gates; this hook would otherwise run inside review jobs).
#   - No-op when the working tree has no changes.
#   - Skips re-running when the exact same tree already passed (hash cache).
#   - On failure, returns {"decision":"block"} with the tail of the output so the agent keeps working.
#   - Gives up after MAX_BLOCKS attempts per session and tells the user, so it can't loop forever.
#
# Input: JSON on stdin with a session_id. Output: nothing (pass), {"decision":"block",
# "reason":...} (keep working), or {"systemMessage":...} (gave up). Exit code is always 0.
#
# Config (env):
#   AGENT_VERIFY_CMD          command to run (default: make verify)
#   AGENT_VERIFY_MAX_BLOCKS   attempts before letting the turn end (default: 3)
#   AGENT_VERIFY_SKIP=1       bypass for this session (humans only)
#   AGENT_PROJECT_DIR         repo root (defaults to $PWD)

set -uo pipefail

input="$(cat)"

if [[ -n "${CI:-}" || -n "${GITHUB_ACTIONS:-}" ]]; then exit 0; fi
if [[ "${AGENT_VERIFY_SKIP:-}" == "1" ]]; then exit 0; fi
command -v python3 >/dev/null 2>&1 || { echo "verify-on-stop: python3 not found, skipping" >&2; exit 0; }

project_dir="${AGENT_PROJECT_DIR:-$(pwd)}"
cd "$project_dir" || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Nothing changed: nothing to verify.
untracked="$(git ls-files --others --exclude-standard)"
if git diff --quiet HEAD -- 2>/dev/null && [[ -z "$untracked" ]]; then exit 0; fi

json_field() {
  python3 -c 'import json,sys; d=json.loads(sys.argv[1] or "{}"); v=d.get(sys.argv[2], ""); print(v if not isinstance(v,bool) else str(v).lower())' "$input" "$1" 2>/dev/null
}
session_id="$(json_field session_id)"
session_id="${session_id:-nosession}"

state_dir="$(git rev-parse --git-dir)/agent-verify"
mkdir -p "$state_dir"
counter_file="$state_dir/blocks-${session_id}"
cache_file="$state_dir/last-pass"

# Fingerprint of the working tree (tracked diff + untracked file contents).
tree_hash="$(
  {
    git diff HEAD --binary
    if [[ -n "$untracked" ]]; then
      while IFS= read -r f; do [[ -f "$f" ]] && { echo "== $f"; cat "$f"; }; done <<< "$untracked"
    fi
  } | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
)"
if [[ -f "$cache_file" && "$(cat "$cache_file")" == "$tree_hash" ]]; then
  rm -f "$counter_file"
  exit 0
fi

verify_cmd="${AGENT_VERIFY_CMD:-make verify}"
max_blocks="${AGENT_VERIFY_MAX_BLOCKS:-3}"
log_file="$(mktemp)"
trap 'rm -f "$log_file"' EXIT

if bash -c "$verify_cmd" >"$log_file" 2>&1; then
  echo "$tree_hash" > "$cache_file"
  rm -f "$counter_file"
  exit 0
fi

blocks=0
[[ -f "$counter_file" ]] && blocks="$(cat "$counter_file")"
blocks=$((blocks + 1))
echo "$blocks" > "$counter_file"

if (( blocks > max_blocks )); then
  rm -f "$counter_file"
  python3 - "$verify_cmd" "$max_blocks" <<'PY'
import json, sys
cmd, n = sys.argv[1], sys.argv[2]
print(json.dumps({
    "systemMessage": f"verify-on-stop: `{cmd}` still failing after {n} attempts. "
                     "Stopping so you can take a look. Do not open a PR from this state."
}))
PY
  exit 0
fi

python3 - "$verify_cmd" "$log_file" "$blocks" "$max_blocks" <<'PY'
import json, sys
cmd, log_path, n, max_n = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(log_path, errors="replace") as fh:
    tail = fh.read()[-4000:]
print(json.dumps({
    "decision": "block",
    "reason": (
        f"`{cmd}` failed (attempt {n} of {max_n}). The task is not done until it passes. "
        "Fix the cause; do not skip, delete, or weaken tests to get green.\n\n"
        f"Last output:\n```\n{tail}\n```"
    ),
}))
PY
exit 0
