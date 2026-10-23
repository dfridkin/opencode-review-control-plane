import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from policy import load_policy  # noqa: E402
from review_gate import evaluate  # noqa: E402

POLICY = load_policy(ROOT / "review-policy.toml")
HEAD, OLD, AUTHOR = "head123", "old456", "agent-dev"


def cls(tier="elevated", min_approvals=1, blocked=False, reason=""):
    return {"tier": tier, "min_approvals": min_approvals, "blocked": blocked,
            "block_reason": reason, "reasons": ["test"], "changed_lines": 10}


def review(user, state, commit=HEAD, at="2026-10-01T10:00:00Z", assoc="MEMBER", utype="User"):
    return {"user": {"login": user, "type": utype}, "state": state, "commit_id": commit,
            "submitted_at": at, "author_association": assoc}


class GateTests(unittest.TestCase):
    def ev(self, c, reviews):
        return evaluate(POLICY, c, reviews, HEAD, AUTHOR)

    def test_low_tier_passes_without_humans(self):
        self.assertEqual(self.ev(cls("low", 0), []).state, "success")

    def test_elevated_without_review_is_pending(self):
        r = self.ev(cls(), [])
        self.assertEqual(r.state, "pending")
        self.assertIn("needs 1 more", r.description)

    def test_fresh_human_approval_passes(self):
        r = self.ev(cls(), [review("alice", "APPROVED")])
        self.assertEqual(r.state, "success")
        self.assertEqual(r.approvals, ["alice"])

    def test_self_approval_does_not_count(self):
        self.assertEqual(self.ev(cls(), [review(AUTHOR, "APPROVED")]).state, "pending")

    def test_bot_approval_does_not_count(self):
        reviews = [review("claude[bot]", "APPROVED"), review("ci", "APPROVED", utype="Bot")]
        self.assertEqual(self.ev(cls(), reviews).state, "pending")

    def test_outside_contributor_approval_does_not_count(self):
        self.assertEqual(self.ev(cls(), [review("rando", "APPROVED", assoc="CONTRIBUTOR")]).state,
                         "pending")

    def test_stale_approval_requires_reapproval(self):
        r = self.ev(cls(), [review("alice", "APPROVED", commit=OLD)])
        self.assertEqual(r.state, "pending")
        self.assertEqual(r.stale_approvals, ["alice"])
        self.assertIn("stale", r.description)

    def test_changes_requested_blocks(self):
        reviews = [review("alice", "APPROVED"), review("bob", "CHANGES_REQUESTED")]
        r = self.ev(cls(), reviews)
        self.assertEqual(r.state, "failure")
        self.assertIn("bob", r.description)

    def test_changes_requested_then_approved_by_same_user(self):
        reviews = [review("bob", "CHANGES_REQUESTED", at="2026-10-01T09:00:00Z"),
                   review("bob", "APPROVED", at="2026-10-01T11:00:00Z")]
        self.assertEqual(self.ev(cls(), reviews).state, "success")

    def test_comment_after_approval_keeps_approval(self):
        reviews = [review("alice", "APPROVED", at="2026-10-01T09:00:00Z"),
                   review("alice", "COMMENTED", at="2026-10-01T12:00:00Z")]
        self.assertEqual(self.ev(cls(), reviews).state, "success")

    def test_dismissed_review_is_cleared(self):
        reviews = [review("alice", "APPROVED", at="2026-10-01T09:00:00Z"),
                   review("alice", "DISMISSED", at="2026-10-01T12:00:00Z")]
        self.assertEqual(self.ev(cls(), reviews).state, "pending")

    def test_blocked_classification_fails_even_with_approval(self):
        r = self.ev(cls(blocked=True, reason="too big"), [review("alice", "APPROVED")])
        self.assertEqual(r.state, "failure")
        self.assertEqual(r.description, "too big")

    def test_changes_requested_on_low_tier_still_blocks(self):
        self.assertEqual(self.ev(cls("low", 0), [review("bob", "CHANGES_REQUESTED")]).state, "failure")


if __name__ == "__main__":
    unittest.main()
