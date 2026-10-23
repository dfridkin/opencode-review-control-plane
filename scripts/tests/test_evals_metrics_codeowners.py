import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import review_metrics  # noqa: E402
import review_summary  # noqa: E402
import run_evals  # noqa: E402
import sync_codeowners  # noqa: E402
from policy import load_policy  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class EvalTests(unittest.TestCase):
    def setUp(self):
        self.cases = run_evals.load_cases()

    def results(self, replay_dir):
        return run_evals.run(self.cases, lambda c: run_evals.replay_response(c, replay_dir))

    def test_seed_cases_load(self):
        kinds = sorted(c.kind for c in self.cases)
        self.assertEqual(kinds, ["control", "incident", "incident"])

    def test_good_replay_meets_baseline(self):
        s = run_evals.summarize(self.results(ROOT / "evals" / "fixtures" / "replay"))
        self.assertEqual(s["recall"], 1.0)
        self.assertEqual(s["false_positive_rate"], 0.0)
        baseline = json.loads((ROOT / "evals" / "baselines" / "replay.json").read_text())
        self.assertEqual(run_evals.compare(s, baseline), [])

    def test_regressed_reviewer_is_caught(self):
        s = run_evals.summarize(self.results(FIXTURES / "replay-regressed"))
        problems = run_evals.compare(s, {"recall": 1.0, "false_positive_rate": 0.0, "incident_cases": 2})
        self.assertEqual(len(problems), 2)

    def test_removing_incident_cases_is_flagged(self):
        s = {"recall": 1.0, "false_positive_rate": 0.0, "incident_cases": 1}
        problems = run_evals.compare(s, {"recall": 1.0, "false_positive_rate": 0.0, "incident_cases": 2})
        self.assertTrue(any("removed" in p for p in problems))

    def test_keyword_match_counts_when_line_is_off(self):
        exp = run_evals.Expected(file="src/billing/invoices.py", line=9, keywords=["tenant"])
        finding = {"file": "./src/billing/invoices.py", "line": 40, "title": "Missing tenant filter",
                   "evidence": "", "severity": "blocker"}
        self.assertTrue(run_evals.finding_matches(finding, exp, 3))

    def test_wrong_file_never_matches(self):
        exp = run_evals.Expected(file="src/billing/invoices.py", line=9, keywords=["tenant"])
        finding = {"file": "src/billing/other_invoices.py", "line": 9, "title": "tenant", "evidence": ""}
        self.assertFalse(run_evals.finding_matches(finding, exp, 3))

    def test_majority_vote_over_runs(self):
        case = next(c for c in self.cases if c.kind == "incident")
        hit = {"findings": [{"file": case.expected[0].file, "line": case.expected[0].line,
                             "severity": "major", "title": "x", "evidence": ""}]}
        miss = {"findings": []}
        seq = iter([hit, miss, miss])
        res = run_evals.run([case], lambda c: next(seq), runs=3)
        self.assertFalse(res[0].passed)

    def test_baseline_path_is_per_model(self):
        class A:
            live, model = True, "openweight/qwen3-coder:30b"
        self.assertEqual(run_evals.default_baseline(A()).name, "openweight_qwen3-coder_30b.json")
        A.live = False
        self.assertEqual(run_evals.default_baseline(A()).name, "replay.json")

    def test_cli_exit_code_on_regression(self):
        r = subprocess.run([sys.executable, str(ROOT / "evals" / "run_evals.py"), "--replay",
                            str(FIXTURES / "replay-regressed")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


class MetricsTests(unittest.TestCase):
    PRS = [
        {"number": 1, "title": "Add export", "author": {"login": "agent"},
         "createdAt": "2026-10-01T10:00:00Z", "mergedAt": "2026-10-01T12:00:00Z",
         "additions": 40, "deletions": 10, "labels": [{"name": "tier:low"}],
         "reviews": []},
        {"number": 2, "title": "Fix auth", "author": {"login": "agent"},
         "createdAt": "2026-10-02T10:00:00Z", "mergedAt": "2026-10-03T10:00:00Z",
         "additions": 100, "deletions": 20, "labels": [{"name": "tier:critical"}, {"name": "incident-cause"}],
         "reviews": [{"author": {"login": "bob"}, "state": "CHANGES_REQUESTED"},
                     {"author": {"login": "bob"}, "state": "APPROVED"}]},
        {"number": 3, "title": 'Revert "Fix auth"', "author": {"login": "bob"},
         "createdAt": "2026-10-04T10:00:00Z", "mergedAt": "2026-10-04T10:30:00Z",
         "additions": 20, "deletions": 100, "labels": [], "reviews": [
             {"author": {"login": "claude", "is_bot": True}, "state": "APPROVED"}]},
        {"number": 4, "title": "Open PR", "author": {"login": "x"}, "createdAt": "2026-10-05T10:00:00Z",
         "mergedAt": None, "additions": 1, "deletions": 1, "labels": [], "reviews": []},
    ]

    def test_compute(self):
        m = review_metrics.compute(self.PRS)
        self.assertEqual(m["merged_prs"], 3)
        self.assertEqual(m["revert_prs"], 1)
        self.assertEqual(m["incident_linked_prs"], 1)
        self.assertEqual(m["human_approved_share_pct"], 33.3)  # bot approval doesn't count
        self.assertEqual(m["one_round_share_pct"], 66.7)
        self.assertEqual(m["by_tier"]["critical"]["merged"], 1)
        self.assertIn("unlabeled", m["by_tier"])
        self.assertIn("| Revert share |", review_metrics.to_markdown(m, "t"))


class CodeownersTests(unittest.TestCase):
    def test_render_contains_critical_paths_only(self):
        block = sync_codeowners.render_block(load_policy(ROOT / "review-policy.toml"))
        self.assertIn("/src/auth/** @your-org/platform-leads", block)
        self.assertIn("*.sql @your-org/platform-leads", block)
        self.assertNotIn("package.json", block)  # elevated tier has no owners

    def test_splice_is_idempotent_and_preserves_manual_lines(self):
        policy = load_policy(ROOT / "review-policy.toml")
        manual = "# my rules\n/docs/ @docs-team\n"
        once = sync_codeowners.splice(manual, sync_codeowners.render_block(policy))
        twice = sync_codeowners.splice(once, sync_codeowners.render_block(policy))
        self.assertEqual(once, twice)
        self.assertTrue(once.startswith(manual))

    def test_repo_codeowners_in_sync(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_codeowners.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class ReviewSummaryTests(unittest.TestCase):
    def run_summary(self, payload, block_on):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, REVIEW_JSON=payload)
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "ci" / "review_summary.py"),
                                "--block-on", block_on, "--out", f"{d}/c.md"],
                               env=env, capture_output=True, text=True)
            return r.returncode, Path(f"{d}/c.md").read_text()

    def test_advisory_never_fails(self):
        payload = json.dumps({"findings": [{"severity": "blocker", "file": "a", "line": 1, "title": "t"}],
                              "summary": "s"})
        self.assertEqual(self.run_summary(payload, "none")[0], 0)

    def test_blocker_blocks_when_enabled(self):
        payload = json.dumps({"findings": [{"severity": "blocker", "file": "a", "line": 1, "title": "t"}],
                              "summary": "s"})
        rc, md = self.run_summary(payload, "blocker")
        self.assertEqual(rc, 1)
        self.assertIn("`a:1`", md)

    def test_major_does_not_block_at_blocker_threshold(self):
        payload = json.dumps({"findings": [{"severity": "major", "file": "a", "line": 1, "title": "t"}],
                              "summary": "s"})
        self.assertEqual(self.run_summary(payload, "blocker")[0], 0)

    def test_missing_output_fails_closed_when_blocking(self):
        self.assertEqual(self.run_summary("", "blocker")[0], 1)
        self.assertEqual(self.run_summary("not json", "none")[0], 0)

    def test_render_escapes_pipes(self):
        md = review_summary.render({"findings": [{"severity": "nit", "file": "a", "line": 1,
                                                  "title": "a | b"}]})
        self.assertIn("a \\| b", md)


if __name__ == "__main__":
    unittest.main()
