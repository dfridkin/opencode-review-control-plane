import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from notify_human_review import build_payload  # noqa: E402
from policy import load_policy  # noqa: E402
from review_rounds import count  # noqa: E402
from risk_classify import ChangedFile, classify  # noqa: E402

BLOCKER = {"findings": [{"file": "a.py", "line": 3, "severity": "blocker", "title": "t", "evidence": "e"}]}
MINOR = {"findings": [{"file": "a.py", "line": 3, "severity": "minor", "title": "t", "evidence": "e"}]}


def marker(rounds, head):
    return f"### Automated review\n...\n<!-- review-rounds: {rounds} head={head} -->\n"


class RoundTests(unittest.TestCase):
    def test_first_round_with_blocker_counts_one(self):
        r = count("", "aaa", BLOCKER, cap=3)
        self.assertEqual((r["rounds"], r["stuck"]), (1, False))
        self.assertIn("review-rounds: 1 head=aaa", r["marker"])

    def test_new_commit_still_blocked_increments_and_hits_cap(self):
        r = count(marker(2, "aaa"), "bbb", BLOCKER, cap=3)
        self.assertEqual((r["rounds"], r["stuck"]), (3, True))
        self.assertEqual(r["open"][0]["severity"], "blocker")

    def test_clean_round_resets(self):
        self.assertEqual(count(marker(2, "aaa"), "bbb", MINOR, cap=3)["rounds"], 0)

    def test_rerun_of_same_commit_does_not_increment(self):
        self.assertEqual(count(marker(2, "aaa"), "aaa", BLOCKER, cap=3)["rounds"], 2)

    def test_missing_output_neither_counts_nor_resets(self):
        self.assertEqual(count(marker(2, "aaa"), "bbb", None, cap=3)["rounds"], 2)

    def test_cap_zero_disables(self):
        self.assertFalse(count(marker(9, "aaa"), "bbb", BLOCKER, cap=0)["stuck"])


class LabelTests(unittest.TestCase):
    def test_needs_human_label_pulls_docs_change_into_human_review(self):
        policy = load_policy(ROOT / "review-policy.toml")
        files = [ChangedFile("docs/guide.md", 1, 0)]
        self.assertEqual(classify(policy, files, [], "pass").min_approvals, 0)
        c = classify(policy, files, ["needs-human"], "pass")
        self.assertGreaterEqual(c.min_approvals, 1)


class SlackPayloadTests(unittest.TestCase):
    def test_pr_text_cannot_mention_or_link(self):
        p = build_payload("https://github.com/o/r/pull/1", "<!channel> fix <http://x|y>", "bot",
                          "low", "stuck", [{"severity": "blocker", "file": "a.py", "line": 1,
                                            "title": "<!here>"}])
        text = p["blocks"][0]["text"]["text"]
        self.assertNotIn("<!channel>", text)
        self.assertNotIn("<!here>", text)
        self.assertIn("&lt;!channel&gt;", text)
        self.assertIn("<https://github.com/o/r/pull/1|", text)

    def test_admin_mention_is_kept(self):
        p = build_payload("u", "t", "a", "", "r", [], mention="<!subteam^S1>")
        self.assertTrue(p["blocks"][0]["text"]["text"].startswith("<!subteam^S1>"))

    def test_findings_are_capped(self):
        many = [{"severity": "major", "file": "f", "line": i, "title": "x"} for i in range(8)]
        text = build_payload("u", "t", "a", "", "r", many)["blocks"][0]["text"]["text"]
        self.assertIn("and 3 more", text)


if __name__ == "__main__":
    unittest.main()
