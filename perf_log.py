#!/usr/bin/env python3
"""
Performance Logger — agent-aware
Log agent task events for the dream cycle reflection phase.

Usage:
  perf_log.py --agent security --task "vuln scan" --outcome success --model qwen2.5:7b
  perf_log.py --task "summarize" --outcome escalated --model qwen2.5:7b --note "too complex"
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

BASE_DIR    = Path.home() / "dream-cycle"
AGENT_NAMES = ["security", "marketing", "programming", "ai_research"]


def get_log_file(agent: str | None) -> Path:
    if agent and agent in AGENT_NAMES:
        log_dir = BASE_DIR / agent
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "performance.jsonl"
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    return BASE_DIR / "performance.jsonl"


def log_event(task: str, outcome: str, model: str,
              duration: float = 0.0, note: str = "", agent: str | None = None):
    entry = {
        "timestamp":    datetime.now().isoformat(),
        "agent":        agent or "global",
        "task":         task,
        "outcome":      outcome,
        "model":        model,
        "duration_sec": duration,
        "note":         note,
    }
    log_file = get_log_file(agent)
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"Logged [{entry['agent']}]: [{outcome}] {task} via {model}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Log agent performance events")
    parser.add_argument("--task",     required=True)
    parser.add_argument("--outcome",  required=True,
                        choices=["success", "failed", "escalated", "timeout"])
    parser.add_argument("--model",    required=True)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--note",     default="")
    parser.add_argument("--agent",    choices=AGENT_NAMES, default=None,
                        help="Which agent this event belongs to")
    args = parser.parse_args()
    log_event(args.task, args.outcome, args.model, args.duration, args.note, args.agent)
