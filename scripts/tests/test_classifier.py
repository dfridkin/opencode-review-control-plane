import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from policy import load_policy, path_matches  # noqa: E402
from risk_classify import ChangedFile, classify, files_from_git, files_from_github_json  # noqa: E402

POLICY = load_policy(ROOT / "review-policy.toml")


def f(path, add=5, dele=0, prev=None):
    return ChangedFile(path, add, dele, prev)


class GlobTests(unittest.TestCase):
    def test_double_star_directory(self):
        self.assertTrue(path_matches("src/auth/a/b.py", "src/auth/**"))
        self.assertFalse(path_matches("src/authz.py", "src/auth/**"))

    def test_basename_pattern_matches_at_any_depth(self):
        self.assertTrue(path_matches("db/migrations/0001.sql", "*.sql"))
        self.assertTrue(path_matches("schema.sql", "*.sql"))

    def test_dotdirs(self):
        self.assertTrue(path_matches(".github/workflows/ci.yml", ".github/**"))
        self.assertTrue(path_matches("./.github/CODEOWNERS", ".github/**"))

    def test_leading_double_star_matches_zero_dirs(self):
        self.assertTrue(path_matches("gen/x.py", "**/gen/**"))
        self.assertTrue(path_matches("a/b/gen/x.py", "**/gen/**"))

    def test_single_star_does_not_cross_slash(self):
        self.assertFalse(path_matches("src/a/b.py", "src/*.py"))
        self.assertTrue(path_matches("src/b.py", "src/*.py"))


class ClassifierTests(unittest.TestCase):
    def test_docs_only_change_needs_no_human(self):
        c = classify(POLICY, [f("docs/usage.md"), f("README.md")])
        self.assertEqual(c.tier, "low")
        self.assertFalse(c.needs_human)
        self.assertFalse(c.blocked)

    def test_ordinary_code_defaults_to_one_human(self):
        c = classify(POLICY, [f("src/orders/export.py"), f("docs/usage.md")])
        self.assertEqual(c.tier, "elevated")
        self.assertTrue(c.needs_human)
        self.assertEqual(c.matched["src/orders/export.py"], "elevated")

    def test_auth_change_is_critical_with_owners(self):
        c = classify(POLICY, [f("src/orders/export.py"), f("src/auth/session.py")])
        self.assertEqual(c.tier, "critical")
        self.assertEqual(c.min_approvals, 1)
        self.assertIn("@your-org/platform-leads", c.owners)

    def test_moving_a_file_out_of_auth_is_still_critical(self):
        c = classify(POLICY, [f("src/util/session.py", prev="src/auth/session.py")])
        self.assertEqual(c.tier, "critical")

    def test_control_plane_changes_are_critical(self):
        for p in [".opencode/agents/bug-hunter.md", "review-policy.toml", "evals/baselines/replay.json",
                  ".opencode/plugins/verify-on-idle.ts", "opencode.json", "AGENTS.md",
                  "ci/opencode.ci.json", "scripts/agent_run.py", "scripts/hooks/verify-on-stop.sh",
                  "tests/acceptance/test_checkout.py", "scripts/ci/run_gate.sh"]:
            with self.subTest(p=p):
                self.assertEqual(classify(POLICY, [f(p)]).tier, "critical")

    def test_dependency_change_is_elevated(self):
        self.assertEqual(classify(POLICY, [f("package.json")]).tier, "elevated")

    def test_large_low_risk_change_routes_to_human(self):
        c = classify(POLICY, [f("docs/guide.md", add=450)])
        self.assertEqual(c.tier, "elevated")
        self.assertTrue(any("changed lines" in r for r in c.reasons))

    def test_lockfiles_do_not_count_toward_size(self):
        c = classify(POLICY, [f("docs/a.md", add=10), f("poetry.lock", add=5000)])
        self.assertEqual(c.changed_lines, 10)
        self.assertFalse(c.blocked)

    def test_hard_max_blocks(self):
        c = classify(POLICY, [f("src/orders/big.py", add=2000)])
        self.assertTrue(c.blocked)
        self.assertIn("Split", c.block_reason)

    def test_security_escalate_raises_tier(self):
        c = classify(POLICY, [f("docs/a.md")], security_verdict="escalate")
        self.assertEqual(c.tier, "elevated")

    def test_security_fail_blocks(self):
        c = classify(POLICY, [f("docs/a.md")], security_verdict="fail")
        self.assertTrue(c.blocked)

    def test_security_pass_never_lowers_a_path_tier(self):
        c = classify(POLICY, [f("src/billing/invoices.py")], security_verdict="pass")
        self.assertEqual(c.tier, "critical")

    def test_unknown_verdict_fails_closed_to_human(self):
        for v in ["unavailable", "error", "PASS?"]:
            with self.subTest(v=v):
                c = classify(POLICY, [f("docs/a.md")], security_verdict=v)
                self.assertTrue(c.needs_human)

    def test_label_escalation(self):
        c = classify(POLICY, [f("docs/a.md")], labels=["risk:high"])
        self.assertEqual(c.tier, "critical")

    def test_unrelated_labels_ignored(self):
        c = classify(POLICY, [f("docs/a.md")], labels=["tier:low", "security:pass", "enhancement"])
        self.assertEqual(c.tier, "low")

    def test_github_json_flattens_slurped_pages(self):
        pages = [[{"filename": "a.py", "additions": 1, "deletions": 2}],
                 [{"filename": "b.py", "additions": 3, "deletions": 0, "previous_filename": "c.py"}]]
        files = files_from_github_json(pages)
        self.assertEqual([x.path for x in files], ["a.py", "b.py"])
        self.assertEqual(files[1].previous_path, "c.py")


class PolicyValidationTests(unittest.TestCase):
    def test_default_tier_typo_is_rejected(self):
        bad = (ROOT / "review-policy.toml").read_text().replace(
            'default_tier = "elevated"', 'default_tier = "elevatd"')
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write(bad)
        try:
            with self.assertRaises(KeyError):
                load_policy(fh.name)
        finally:
            os.unlink(fh.name)

    def test_escalation_target_typo_is_rejected(self):
        bad = (ROOT / "review-policy.toml").read_text().replace(
            '"risk:high" = "critical"', '"risk:high" = "critcal"')
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write(bad)
        try:
            with self.assertRaises(KeyError):
                load_policy(fh.name)
        finally:
            os.unlink(fh.name)


class LocalGitTests(unittest.TestCase):
    def test_rename_out_of_auth_detected_from_git(self):
        with tempfile.TemporaryDirectory() as d:
            run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True)  # noqa: E731
            run("git", "init", "-q", "-b", "main")
            run("git", "config", "user.email", "t@example.com")
            run("git", "config", "user.name", "t")
            os.makedirs(f"{d}/src/auth")
            Path(f"{d}/src/auth/session.py").write_text("x = 1\n" * 20)
            run("git", "add", ".")
            run("git", "commit", "-qm", "init")
            run("git", "checkout", "-qb", "feature")
            os.makedirs(f"{d}/src/util")
            run("git", "mv", "src/auth/session.py", "src/util/session.py")
            run("git", "commit", "-qm", "move")
            files = files_from_git("main", cwd=d)
            self.assertEqual(files[0].path, "src/util/session.py")
            self.assertEqual(files[0].previous_path, "src/auth/session.py")
            self.assertEqual(classify(POLICY, files).tier, "critical")


if __name__ == "__main__":
    unittest.main()
