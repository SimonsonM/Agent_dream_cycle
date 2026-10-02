"""
Replay-based A/B evaluation of proposed prompt changes.

Pure logic with injected callables (no LLM or network imports), so it is
unit-testable. A candidate instruction is run against the same real inputs
the scan phase saw on past nights, and a judge compares baseline vs
candidate outputs pairwise with randomized A/B order.
"""
import json
import random
from pathlib import Path
from typing import Callable

MIN_INPUTS = 3
MIN_WIN_RATE = 0.60      # wins / (wins + losses), ties excluded
PASS_SCORE = 0.70        # matches the existing validated_changes threshold


def save_replay_input(agent_dir: Path, date_str: str, items: list[dict], keep: int = 14) -> None:
    """Persist the (sanitized) scan inputs so future nights can replay them."""
    if not items:
        return
    d = Path(agent_dir) / "replay"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date_str}_scan_inputs.json").write_text(json.dumps(items))
    for old in sorted(d.glob("*_scan_inputs.json"))[:-keep]:
        old.unlink()


def load_replay_inputs(agent_dir: Path, n: int = 4, exclude_date: str = "") -> list[list[dict]]:
    """Most recent n saved input batches (excluding tonight's)."""
    d = Path(agent_dir) / "replay"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*_scan_inputs.json"), reverse=True):
        if exclude_date and f.name.startswith(exclude_date):
            continue
        try:
            batch = json.loads(f.read_text())
            if isinstance(batch, list) and batch:
                out.append(batch)
        except Exception:
            continue
        if len(out) >= n:
            break
    return out


def run_ab(inputs: list, build_prompt: Callable[[list, str], str],
           instruction: str, generate: Callable[[str], str],
           is_valid: Callable[[str], bool],
           judge: Callable[[list, str, str], str],
           rng: random.Random | None = None) -> dict:
    """Baseline vs candidate over each input. judge returns 'A'|'B'|'tie'."""
    rng = rng or random.Random()
    wins = losses = ties = judge_fail = 0
    valid_base = valid_cand = 0
    for batch in inputs:
        base = generate(build_prompt(batch, ""))
        cand = generate(build_prompt(batch, instruction))
        vb, vc = is_valid(base), is_valid(cand)
        valid_base += vb
        valid_cand += vc
        if vb and not vc:
            losses += 1          # broke output validity: automatic loss
            continue
        if vc and not vb:
            wins += 1
            continue
        if not vb and not vc:
            ties += 1
            continue
        cand_first = rng.random() < 0.5
        a, b = (cand, base) if cand_first else (base, cand)
        try:
            verdict = judge(batch, a, b)
        except Exception:
            judge_fail += 1
            continue
        if verdict == "tie":
            ties += 1
        elif (verdict == "A") == cand_first:
            wins += 1
        else:
            losses += 1
    n = len(inputs)
    decided = wins + losses
    return {"n": n, "wins": wins, "losses": losses, "ties": ties,
            "judge_failures": judge_fail,
            "win_rate": (wins / decided) if decided else 0.0,
            "validity_base": valid_base / n if n else 0.0,
            "validity_cand": valid_cand / n if n else 0.0}


def decide(r: dict) -> tuple[float, str]:
    """Map an A/B result to (score 0-1, verdict). Passing needs enough
    evidence, a clear win rate, and no validity regression."""
    if r["n"] < MIN_INPUTS:
        return 0.5, "insufficient_data"
    if r["wins"] + r["losses"] < 2:
        return 0.5, "inconclusive"
    if r["validity_cand"] < r["validity_base"]:
        return 0.2, "validity_regression"
    score = 0.3 + 0.7 * r["win_rate"]       # win_rate 0.6 -> 0.72
    if r["win_rate"] >= MIN_WIN_RATE and score >= PASS_SCORE:
        return round(score, 3), "pass"
    return round(score, 3), "fail"
