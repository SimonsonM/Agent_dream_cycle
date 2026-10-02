"""build_job: policy-gated apply, structured rollback, injection resistance."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import build_job
from build_job import apply_action, run_agent_build


class BuildJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.base = self.tmp / "dream-cycle"
        self.logs = self.tmp / "dream-logs"
        self.base.mkdir()
        self.applied = self.base / "a" / "staging" / "applied"
        self.applied.mkdir(parents=True)
        self.staged = self.base / "a" / "staging" / "x.staged"
        self.staged.write_text("{}")
        for target, val in (("BASE_DIR", self.base), ("LOGS_DIR", self.logs),
                            ("ALLOWED_WRITE_DIRS", [self.base, self.logs])):
            p = patch.object(build_job, target, val)
            p.start()
            self.addCleanup(p.stop)

    def _doc(self, **kw):
        a = {"title": "Doc", "risk": "low", "action_type": "documentation",
             "file_path": str(self.base / "a" / "notes.md"), "content": "# hi\n"}
        a.update(kw)
        return a

    def test_doc_applied_and_rollback_restores(self):
        target = self.base / "a" / "notes.md"
        target.write_text("orig")
        self.assertTrue(apply_action(self._doc(), self.staged, self.applied, "a"))
        self.assertEqual(target.read_text(), "# hi\n")
        rb = next(self.applied.glob("rollback_*.sh"))
        subprocess.run(["bash", str(rb)], check=True)
        self.assertEqual(target.read_text(), "orig")
        rec = json.loads((self.base / "a" / "logs" / "applied_changes.jsonl")
                         .read_text().splitlines()[-1])
        self.assertEqual(rec["event"], "reverted")

    def test_new_file_rollback_removes_it(self):
        self.assertTrue(apply_action(self._doc(), self.staged, self.applied, "a"))
        subprocess.run(["bash", str(next(self.applied.glob("rollback_*.sh")))], check=True)
        self.assertFalse((self.base / "a" / "notes.md").exists())

    def test_script_never_applied_even_if_declared_low(self):
        a = self._doc(action_type="script", file_path=str(self.base / "a" / "x.sh"),
                      content="#!/bin/sh\necho hi\n")
        self.assertFalse(apply_action(a, self.staged, self.applied, "a"))
        self.assertFalse((self.base / "a" / "x.sh").exists())

    def test_path_escape_and_protected_blocked(self):
        for fp in (str(self.tmp / "evil.md"), str(self.base / ".." / "evil.md"),
                   str(self.base / "a" / "staging" / "m.md"),
                   str(self.base / "a" / "config.json")):
            self.assertFalse(apply_action(self._doc(file_path=fp), self.staged,
                                          self.applied, "a"), fp)

    def test_rollback_ignores_llm_shell_and_quotes_title(self):
        marker = self.tmp / "pwned"
        a = self._doc(title=f"x'; touch {marker}; '", rollback_command=f"touch {marker}")
        self.assertTrue(apply_action(a, self.staged, self.applied, "a"))
        subprocess.run(["bash", str(next(self.applied.glob("rollback_*.sh")))], check=True)
        self.assertFalse(marker.exists())

    def test_model_pull_strict(self):
        with patch.object(build_job.subprocess, "run") as run:
            run.return_value.returncode = 0
            ok = {"title": "m", "risk": "low", "action_type": "model_pull",
                  "content": "ollama pull qwen3.5:9b"}
            self.assertTrue(apply_action(ok, self.staged, self.applied, "a"))
            run.assert_called_once_with(["ollama", "pull", "qwen3.5:9b"],
                                        capture_output=True, text=True, timeout=300)
            bad = dict(ok, content="ollama pull x; rm -rf ~")
            self.assertFalse(apply_action(bad, self.staged, self.applied, "a"))
            self.assertEqual(run.call_count, 1)

    def test_manifest_low_but_policy_high_goes_to_review(self):
        sd = self.base / "a" / "staging"
        f = sd / "s.staged"
        f.write_text(json.dumps(self._doc(action_type="script", file_path=str(self.base / "x.sh"))))
        (sd / "2026-01-01_manifest.json").write_text(
            json.dumps([{"file": str(f), "risk": "low", "title": "Doc"}]))
        applied, review = run_agent_build("a", "2026-01-01")
        self.assertEqual(applied, [])
        self.assertEqual(review[0]["risk"], "high")


if __name__ == "__main__":
    unittest.main()
