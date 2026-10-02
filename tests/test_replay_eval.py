import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
sys.path.insert(0, str(Path(__file__).parent.parent))
import replay_eval as re_


def build(batch, extra):
    return f"{len(batch)}|{extra}"


class ReplayTests(unittest.TestCase):
    inputs = [[{"title": "a"}]] * 4

    def run_ab(self, judge, valid=lambda raw: True, seed=1):
        return re_.run_ab(self.inputs, build, "INSTR", lambda p: p, valid, judge,
                          random.Random(seed))

    def test_judge_prefers_candidate_regardless_of_position(self):
        # judge picks whichever output contains INSTR (the candidate)
        j = lambda batch, a, b: "A" if "INSTR" in a else "B"
        for seed in range(5):
            r = self.run_ab(j, seed=seed)
            self.assertEqual((r["wins"], r["losses"]), (4, 0))
        self.assertEqual(re_.decide(r)[1], "pass")

    def test_judge_prefers_baseline_fails(self):
        j = lambda batch, a, b: "B" if "INSTR" in a else "A"
        r = self.run_ab(j)
        self.assertEqual(r["wins"], 0)
        self.assertEqual(re_.decide(r)[1], "fail")

    def test_position_bias_judge_is_a_coin_flip_not_a_pass(self):
        r = self.run_ab(lambda b, a, c: "A", seed=3)   # always says A
        self.assertEqual(r["wins"] + r["losses"], 4)
        self.assertLess(r["wins"], 4)   # positional bias cannot sweep both sides

    def test_validity_regression_is_loss(self):
        valid = lambda raw: "INSTR" not in raw
        r = self.run_ab(lambda *a: "A", valid)
        self.assertEqual(r["losses"], 4)
        self.assertEqual(re_.decide(r)[1], "validity_regression")

    def test_insufficient_and_inconclusive(self):
        self.assertEqual(re_.decide({"n": 2, "wins": 2, "losses": 0, "validity_cand": 1,
                                     "validity_base": 1, "win_rate": 1})[1], "insufficient_data")
        self.assertEqual(re_.decide({"n": 4, "wins": 1, "losses": 0, "validity_cand": 1,
                                     "validity_base": 1, "win_rate": 1})[1], "inconclusive")

    def test_judge_exception_counted_not_crash(self):
        def j(*a): raise RuntimeError("x")
        r = self.run_ab(j)
        self.assertEqual(r["judge_failures"], 4)

    def test_save_load_prunes_and_excludes_today(self):
        d = Path(tempfile.mkdtemp())
        for i in range(1, 20):
            re_.save_replay_input(d, f"2026-01-{i:02d}", [{"title": str(i)}])
        self.assertEqual(len(list((d / "replay").glob("*.json"))), 14)
        got = re_.load_replay_inputs(d, n=4, exclude_date="2026-01-19")
        self.assertEqual([b[0]["title"] for b in got], ["18", "17", "16", "15"])


if __name__ == "__main__":
    unittest.main()
