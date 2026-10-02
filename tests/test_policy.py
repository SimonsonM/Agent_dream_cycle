import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import policy

B = Path("/tmp/_adc_base")


class PolicyTests(unittest.TestCase):
    A = [B]

    def eff(self, **kw):
        a = {"risk": "low", "action_type": "documentation",
             "file_path": str(B / "a.md"), "content": "x"}
        a.update(kw)
        return policy.effective_risk(a, self.A)[0]

    def test_basic(self):
        self.assertEqual(self.eff(), "low")
        self.assertEqual(self.eff(action_type="script"), "high")
        self.assertEqual(self.eff(action_type="workflow"), "medium")
        self.assertEqual(self.eff(action_type="bogus"), "high")
        self.assertEqual(self.eff(risk="weird"), "high")

    def test_never_lowers(self):
        self.assertEqual(self.eff(risk="high"), "high")
        self.assertEqual(self.eff(risk="medium"), "medium")

    def test_paths(self):
        self.assertEqual(self.eff(file_path="/etc/x.md"), "high")
        self.assertEqual(self.eff(file_path=str(B / "staging" / "x.md")), "high")
        self.assertEqual(self.eff(file_path=str(B / "a.sh")), "medium")

    def test_config_must_parse(self):
        ok = self.eff(action_type="config", file_path=str(B / "c.json"), content='{"a":1}')
        bad = self.eff(action_type="config", file_path=str(B / "c.json"), content="{nope")
        self.assertEqual((ok, bad), ("low", "medium"))

    def test_model_pull_and_size(self):
        self.assertEqual(self.eff(action_type="model_pull", content="ollama pull a:b"), "low")
        self.assertEqual(self.eff(action_type="model_pull", content="ollama pull a && id"), "high")
        self.assertEqual(self.eff(content="x" * 70000), "high")
