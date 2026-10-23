#!/usr/bin/env bash
# Install opencode on a CI runner. On a self-hosted runner with opencode preinstalled, this is a no-op.
set -euo pipefail

command -v opencode >/dev/null 2>&1 || curl -fsSL https://opencode.ai/install | bash
bin_dir="$HOME/.opencode/bin"   # where the installer puts the binary
if [[ -n "${GITHUB_PATH:-}" ]]; then echo "$bin_dir" >> "$GITHUB_PATH"; fi
export PATH="$bin_dir:$PATH"
opencode --version || true
