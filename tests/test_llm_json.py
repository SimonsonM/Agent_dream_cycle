import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
sys.path.insert(0, str(Path(__file__).parent.parent))
import dream_cycle as dc

SCHEMA = {"type": "object", "required": ["n"], "properties": {"n": {"type": "number"}}}


class LLMJsonTests(unittest.TestCase):
    def setUp(self):
        dc.HEALTH.__init__()
        p = patch.object(dc, "LOCAL_ONLY", False)
        p.start()
        self.addCleanup(p.stop)

    def test_repair_retry_succeeds(self):
        with patch.object(dc, "ollama_chat", side_effect=['not json', '{"n": 2}']):
            self.assertEqual(dc.llm_json("p", SCHEMA, tier="local"), {"n": 2})

    def test_local_invalid_raises(self):
        with patch.object(dc, "ollama_chat", return_value='{"n": "x"}'):
            with self.assertRaises(dc.LLMError):
                dc.llm_json("p", SCHEMA, tier="local")

    def test_auto_escalates_and_marks_degraded(self):
        with patch.object(dc, "ollama_chat", return_value=""), \
             patch.object(dc, "claude_json", return_value={"n": 1}):
            self.assertEqual(dc.llm_json("p", SCHEMA, tier="auto"), {"n": 1})
        dc.HEALTH.begin("x")
        self.assertEqual(dc.HEALTH.overall(), "degraded")

    def test_frontier_failure_raises(self):
        with patch.object(dc, "ollama_chat", return_value=""), \
             patch.object(dc, "claude_json", side_effect=RuntimeError("boom")):
            with self.assertRaises(dc.LLMError):
                dc.llm_json("p", SCHEMA, tier="auto")

    def test_health_overall(self):
        h = dc.RunHealth()
        h.begin("a"); h.note("degraded", "x"); h.begin("b"); h.note("failed", "y")
        self.assertEqual(h.overall(), "failed")


if __name__ == "__main__":
    unittest.main()
