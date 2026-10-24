"""run_gate.sh posts the Layer 5 approvals notice once per tier, using a stub `gh`."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

STUB = r"""#!/usr/bin/env bash
# Minimal gh stand-in: answers the calls run_gate.sh makes, logs the rest.
echo "gh $*" >> "$STUB_LOG"
case "$*" in
  *"/files"*)   echo "[[{\"filename\":\"$STUB_FILE\",\"status\":\"modified\",\"additions\":5,\"deletions\":1}]]" ;;
  *"/reviews"*) echo '[[]]' ;;
  *"/labels"*)  echo "$STUB_LABELS" ;;
  *) : ;;
esac
"""


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "needs bash and jq")
class GateNotifyTests(unittest.TestCase):
    def run_gate(self, labels: str, changed: str = "src/app/handler.py") -> str:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bin").mkdir()
        gh = tmp / "bin" / "gh"
        gh.write_text(STUB)
        gh.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if not k.startswith("SLACK_")}
        env.update(PATH=f"{tmp / 'bin'}:{env['PATH']}", STUB_LOG=str(tmp / "gh.log"),
                   STUB_LABELS=labels, STUB_FILE=changed, RUNNER_TEMP=str(tmp), REPO="o/r", PR_NUMBER="7",
                   HEAD_SHA="abc", PR_AUTHOR="agent-bot", SECURITY_VERDICT="pass",
                   PR_URL="https://github.com/o/r/pull/7", PR_TITLE="Change handler")
        out = subprocess.run(["bash", "scripts/ci/run_gate.sh"], cwd=ROOT, env=env,
                             capture_output=True, text=True, check=True)
        return out.stdout

    def test_first_time_in_a_human_tier_posts_an_approvals_notice(self):
        out = self.run_gate("")
        self.assertIn("review-gate -> pending", out)
        self.assertIn("SLACK_WEBHOOK_APPROVALS is not set", out)  # attempted, routed to approvals

    def test_same_tier_on_a_later_push_does_not_post_again(self):
        out = self.run_gate("tier:elevated")
        self.assertIn("review-gate -> pending", out)
        self.assertNotIn("SLACK_WEBHOOK_APPROVALS", out)

    def test_tier_that_needs_no_person_does_not_post(self):
        out = self.run_gate("", changed="docs/guide.md")
        self.assertIn("review-gate -> success", out)
        self.assertNotIn("SLACK_WEBHOOK_APPROVALS", out)


if __name__ == "__main__":
    unittest.main()
