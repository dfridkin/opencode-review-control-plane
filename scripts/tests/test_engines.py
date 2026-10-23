"""Tests for the opencode adapter, the review orchestrator, and the comment poster."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import agent_run  # noqa: E402
import post_review  # noqa: E402
import review_orchestrator as orch  # noqa: E402

FINDINGS_SCHEMA = json.loads((ROOT / ".github/review-prompts/findings.schema.json").read_text())
SECURITY_SCHEMA = json.loads((ROOT / ".github/review-prompts/security.schema.json").read_text())


class ExtractJsonTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(agent_run.extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_with_prose(self):
        text = "Here is my review.\n```json\n{\"findings\": [], \"summary\": \"ok\"}\n```\nDone."
        self.assertEqual(agent_run.extract_json(text)["summary"], "ok")

    def test_last_object_wins_after_reasoning(self):
        text = 'I considered {"draft": true} but the answer is {"verdict": "pass"}'
        self.assertEqual(agent_run.extract_json(text), {"verdict": "pass"})

    def test_braces_inside_strings(self):
        text = 'noise {"title": "uses {curly} braces", "n": 1} tail'
        self.assertEqual(agent_run.extract_json(text)["title"], "uses {curly} braces")

    def test_nothing(self):
        self.assertIsNone(agent_run.extract_json("I could not complete the review."))
        self.assertIsNone(agent_run.extract_json("[1, 2]"))


class ValidateTests(unittest.TestCase):
    def test_valid_findings(self):
        data = {"findings": [{"file": "a.py", "line": 3, "severity": "major", "title": "t",
                              "evidence": "e", "confidence": 0.8}], "summary": "s"}
        self.assertEqual(agent_run.validate(data, FINDINGS_SCHEMA), [])

    def test_common_open_weight_mistakes(self):
        data = {"findings": [{"file": "a.py", "line": "3", "severity": "critical", "title": "t",
                              "evidence": "e", "confidence": 1.4}]}
        errs = agent_run.validate(data, FINDINGS_SCHEMA)
        joined = " ".join(errs)
        self.assertIn("missing required 'summary'", joined)
        self.assertIn("line: expected integer", joined)
        self.assertIn("'critical' not in", joined)
        self.assertIn("above maximum", joined)

    def test_bool_is_not_a_number(self):
        self.assertTrue(agent_run.validate(True, {"type": "integer"}))


class RunLoopTests(unittest.TestCase):
    def req(self):
        return agent_run.Request(instructions="i", task="t", schema=SECURITY_SCHEMA)

    def test_retry_with_feedback_then_success(self):
        calls = []

        def fake(req, feedback, env):
            calls.append(feedback)
            if len(calls) == 1:
                return 'Sure! {"verdict": "maybe", "findings": [], "risk_notes": "x"}'
            return '{"verdict": "escalate", "findings": [], "risk_notes": "x"}'

        res = agent_run.run(self.req(), retries=1, env={}, runner=fake)
        self.assertEqual(res.data["verdict"], "escalate")
        self.assertEqual(res.attempts, 2)
        self.assertIsNone(calls[0])
        self.assertIn("'maybe' not in", calls[1])

    def test_gives_up(self):
        res = agent_run.run(self.req(), retries=1, env={}, runner=lambda r, f, e: "no json")
        self.assertIsNone(res.data)
        self.assertEqual(res.attempts, 2)


class OpencodeAdapterTests(unittest.TestCase):
    def test_parse_events(self):
        lines = [
            {"type": "step_start", "sessionID": "ses_1", "part": {"type": "step-start"}},
            {"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool", "tool": "read"}},
            {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "Thinking."}},
            {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": '{"verdict":"pass"}'}},
            {"type": "step_finish", "sessionID": "ses_1", "part": {"type": "step-finish", "reason": "stop"}},
        ]
        out = "\n".join(json.dumps(x) for x in lines) + "\nnot json\n"
        text = agent_run.opencode_parse_events(out)
        self.assertEqual(agent_run.extract_json(text), {"verdict": "pass"})

    def test_error_event_raises(self):
        out = json.dumps({"type": "error", "error": {"name": "APIError", "data": {"message": "ECONNREFUSED"}}})
        with self.assertRaises(agent_run.EngineError):
            agent_run.opencode_parse_events(out)

    def test_ci_config_is_locked_down_and_injects_model(self):
        cfg = json.loads(agent_run.opencode_config("openweight/qwen3-coder:30b", {"LLM_CONTEXT": "65536"}))
        self.assertEqual(cfg["model"], "openweight/qwen3-coder:30b")
        self.assertEqual(cfg["provider"]["openweight"]["models"]["qwen3-coder:30b"]["limit"]["context"], 65536)
        perm = cfg["permission"]
        for k in ("edit", "webfetch", "websearch", "task", "external_directory", "doom_loop", "question"):
            self.assertEqual(perm[k], "deny", k)
        self.assertEqual(perm["shell"]["*"], "deny")
        self.assertEqual(cfg["agent"]["ci-reviewer"]["mode"], "primary")

    def test_hosted_provider_model_not_injected(self):
        cfg = json.loads(agent_run.opencode_config("anthropic/claude-sonnet-5", {}))
        self.assertEqual(cfg["provider"]["openweight"]["models"], {})

    def test_opencode_requires_provider_model(self):
        with self.assertRaises(agent_run.EngineError):
            agent_run.run_opencode(agent_run.Request("i", "t", {}, model="qwen"), None, {})


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class FakeCliTests(unittest.TestCase):
    """End to end through the real subprocess path with a fake `opencode` binary."""

    def setUp(self):
        self.bin = Path(tempfile.mkdtemp())
        self.log = self.bin / "argv.log"

    def tearDown(self):
        shutil.rmtree(self.bin)

    def make(self, name, script):
        p = self.bin / name
        p.write_text("#!/usr/bin/env bash\n" + script)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def env(self):
        return dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", RUNNER_TEMP=str(self.bin))

    def test_opencode_cli(self):
        self.make("opencode", f'''printf '%s\\n' "$@" > {self.log}
echo "$OPENCODE_CONFIG_CONTENT" > {self.bin}/cfg.json
echo '{{"type":"text","sessionID":"s","part":{{"type":"text","text":"```json\\n{{\\"verdict\\":\\"pass\\",\\"findings\\":[],\\"risk_notes\\":\\"ok\\"}}\\n```"}}}}'
''')
        diff = self.bin / "pr.diff"
        diff.write_text("diff --git a/x b/x\n")
        req = agent_run.Request("Be careful.", "Review it.", SECURITY_SCHEMA, input_path=diff,
                                model="openweight/qwen3-coder")
        res = agent_run.run(req, env=self.env())
        self.assertEqual(res.data["verdict"], "pass")
        argv_text = self.log.read_text()
        argv = argv_text.splitlines()
        for flag in ("run", "--format", "json", "--agent", "ci-reviewer", "--file", str(diff)):
            self.assertIn(flag, argv)
        prompt = argv_text.split(str(diff) + "\n", 1)[1]
        self.assertTrue(prompt.startswith("<instructions>"))
        self.assertIn("JSON Schema", prompt)
        self.assertEqual(json.loads((self.bin / "cfg.json").read_text())["model"], "openweight/qwen3-coder")

    def test_security_script_with_opencode(self):
        self.make("opencode", '''echo '{"type":"text","sessionID":"s","part":{"type":"text","text":"{\\"verdict\\":\\"escalate\\",\\"findings\\":[],\\"risk_notes\\":\\"touches billing\\"}"}}'
''')
        repo = Path(tempfile.mkdtemp())
        try:
            run = lambda *a: subprocess.run(a, cwd=repo, check=True, capture_output=True)  # noqa: E731
            run("git", "init", "-q", "-b", "main")
            run("git", "config", "user.email", "t@e")
            run("git", "config", "user.name", "t")
            for d in ("scripts", ".opencode", "ci", ".github"):
                shutil.copytree(ROOT / d, repo / d)
            (repo / "billing.py").write_text("x = 1\n")
            run("git", "add", ".")
            run("git", "commit", "-qm", "base")
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                                  text=True).stdout.strip()
            (repo / "billing.py").write_text("x = 2\n")
            run("git", "commit", "-qam", "change")
            out = repo / "out"
            env = dict(self.env(), BASE_SHA=base, REVIEW_MODEL="openweight/qwen3-coder", LLM_BASE_URL="http://gpu:8000/v1",
                       OUT_DIR=str(out), GITHUB_OUTPUT=str(out / "gho"))
            r = subprocess.run(["bash", "scripts/ci/security_review.sh"], cwd=repo, env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("verdict=escalate", (out / "gho").read_text())

            # No endpoint configured: fails closed.
            env.pop("LLM_BASE_URL")
            r = subprocess.run(["bash", "scripts/ci/security_review.sh"], cwd=repo, env=env,
                               capture_output=True, text=True)
            self.assertIn("verdict=unavailable", (out / "gho").read_text().splitlines()[-1])
        finally:
            shutil.rmtree(repo)


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.diff = Path(tempfile.mkstemp(suffix=".diff")[1])

    def tearDown(self):
        self.diff.unlink()

    def fake(self, hunts: dict, verdicts: dict, fail_areas=()):
        def runner(req):
            if "Focus area:" in req.task:
                area = req.task.split("Focus area: ", 1)[1].split(".\n", 1)[0]
                if area in fail_areas:
                    raise agent_run.EngineError("connection refused")
                return agent_run.Result({"candidates": hunts.get(area, [])}, "")
            cand = json.loads(req.task.split("verify or refute:\n", 1)[1])
            return agent_run.Result(verdicts[(cand["file"], cand["line"])], "")
        return runner

    def cand(self, file, line, sev="major", conf=0.8, title="t"):
        return {"file": file, "line": line, "severity": sev, "title": title, "evidence": "e", "confidence": conf}

    def test_pipeline(self):
        hunts = {
            "logic": [self.cand("a.py", 10, "blocker", 0.9), self.cand("b.py", 5, "minor", 0.7)],
            "data integrity and scoping": [self.cand("a.py", 12, "major", 0.8),   # dup of a.py:10
                                           self.cand("c.py", 1, "major", 0.4)],  # below floor
            "concurrency": [self.cand("d.py", 7, "major", 0.9)],
        }
        verdicts = {
            ("a.py", 10): {"status": "confirmed", "severity": "major", "reason": "traced"},   # lowered
            ("b.py", 5): {"status": "refuted", "severity": "minor", "reason": "guarded at b.py:2"},
            ("d.py", 7): {"status": "confirmed", "severity": "blocker", "reason": "x"},       # can't raise
        }
        out = orch.orchestrate("openweight/m", "abc", self.diff, runner=self.fake(hunts, verdicts))
        self.assertEqual(out.candidates, 3)
        self.assertEqual([(f["file"], f["severity"]) for f in out.findings], [("a.py", "major"), ("d.py", "major")])
        self.assertEqual(out.dropped_unverified, 1)
        self.assertIn("Verifier: traced", out.findings[0]["evidence"])
        self.assertEqual(orch.agent_run.validate(out.to_json(), FINDINGS_SCHEMA), [])

    def test_failed_hunter_is_reported_not_silent(self):
        out = orch.orchestrate("m", "abc", self.diff,
                               runner=self.fake({}, {}, fail_areas=("logic",)))
        self.assertEqual(len(out.errors), 1)
        self.assertIn("incomplete", out.to_json()["summary"])

    def test_dedupe_cap(self):
        cands = [self.cand("f.py", i * 10, conf=0.9) for i in range(40)]
        self.assertEqual(len(orch.dedupe(cands, cap=25)), 25)


class PostReviewTests(unittest.TestCase):
    DIFF = """diff --git a/src/a.py b/src/a.py
index 1..2 100644
--- a/src/a.py
+++ b/src/a.py
@@ -10,4 +10,5 @@ def f():
     x = 1
-    y = 2
+    y = 3
+    z = 4
     return x
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,1 +0,0 @@
-gone
"""

    def test_commentable_lines(self):
        lines = post_review.commentable_lines(self.DIFF)
        self.assertEqual(lines["src/a.py"], {10, 11, 12, 13})
        self.assertNotIn("old.py", lines)

    def test_build_review_routes_unanchored_and_skips_nits(self):
        findings = [
            {"file": "src/a.py", "line": 11, "severity": "major", "title": "inline", "evidence": "e"},
            {"file": "src/a.py", "line": 99, "severity": "blocker", "title": "elsewhere", "evidence": "e"},
            {"file": "src/a.py", "line": 12, "severity": "nit", "title": "nit", "evidence": "e"},
        ]
        r = post_review.build_review(findings, post_review.commentable_lines(self.DIFF), "sha1")
        self.assertEqual(len(r["comments"]), 1)
        self.assertEqual(r["comments"][0]["line"], 11)
        self.assertIn("elsewhere", r["body"])
        self.assertNotIn("nit", r["body"].split("outside the diff")[-1])
        self.assertEqual(r["event"], "COMMENT")


if __name__ == "__main__":
    unittest.main()
