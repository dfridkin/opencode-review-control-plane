import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "hooks" / "verify-on-stop.sh"


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class StopHookTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        run = lambda *a: subprocess.run(a, cwd=self.dir, check=True, capture_output=True)  # noqa: E731
        run("git", "init", "-q")
        run("git", "config", "user.email", "t@example.com")
        run("git", "config", "user.name", "t")
        Path(self.dir, "a.txt").write_text("one\n")
        run("git", "add", ".")
        run("git", "commit", "-qm", "init")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def hook(self, verify_cmd="true", session="s1", **extra_env):
        env = {k: v for k, v in os.environ.items() if k not in {"CI", "GITHUB_ACTIONS"}}
        env.update(AGENT_PROJECT_DIR=self.dir, AGENT_VERIFY_CMD=verify_cmd, **extra_env)
        payload = json.dumps({"session_id": session, "hook_event_name": "Stop", "stop_hook_active": False})
        r = subprocess.run(["bash", str(HOOK)], input=payload, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def dirty(self, text="two\n"):
        Path(self.dir, "a.txt").write_text(text)

    def test_clean_tree_is_noop(self):
        self.assertIsNone(self.hook(verify_cmd="exit 1"))

    def test_failing_verify_blocks_with_output(self):
        self.dirty()
        out = self.hook(verify_cmd="echo 'E501 line too long'; exit 1")
        self.assertEqual(out["decision"], "block")
        self.assertIn("E501 line too long", out["reason"])
        self.assertIn("attempt 1 of 3", out["reason"])

    def test_untracked_file_counts_as_change(self):
        Path(self.dir, "new.py").write_text("x = 1\n")
        self.assertEqual(self.hook(verify_cmd="exit 1")["decision"], "block")

    def test_passing_verify_is_cached(self):
        self.dirty()
        marker = Path(tempfile.mkdtemp(), "ran.log")  # outside the repo so it doesn't change the tree
        cmd = f"echo run >> {marker}"
        self.assertIsNone(self.hook(verify_cmd=cmd))
        self.assertIsNone(self.hook(verify_cmd=cmd))
        self.assertEqual(marker.read_text().count("run"), 1)  # second call hit the cache
        self.dirty("three\n")
        self.assertIsNone(self.hook(verify_cmd=cmd))
        self.assertEqual(marker.read_text().count("run"), 2)  # new tree, re-verified

    def test_gives_up_after_max_blocks(self):
        self.dirty()
        for i in range(2):
            self.assertEqual(self.hook(verify_cmd="exit 1", AGENT_VERIFY_MAX_BLOCKS="2")["decision"], "block")
        out = self.hook(verify_cmd="exit 1", AGENT_VERIFY_MAX_BLOCKS="2")
        self.assertNotIn("decision", out)
        self.assertIn("still failing", out["systemMessage"])

    def test_noop_in_ci(self):
        self.dirty()
        self.assertIsNone(self.hook(verify_cmd="exit 1", GITHUB_ACTIONS="true"))

    def test_counter_is_per_session(self):
        self.dirty()
        self.hook(verify_cmd="exit 1", session="a")
        out = self.hook(verify_cmd="exit 1", session="b")
        self.assertIn("attempt 1 of", out["reason"])


if __name__ == "__main__":
    unittest.main()
