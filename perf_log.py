#!/usr/bin/env python3
"""
Performance Logger
Call this from your MCP servers, agent tasks, or shell scripts
to log events for the dream cycle reflection phase.

Usage:
  python3 perf_log.py --task "summarize file" --outcome success --model qwen3.5:9b --duration 4.2
  python3 perf_log.py --task "debug code" --outcome escalated --model qwen3.5:9b --note "too complex, routed to Claude"
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

LOG_FILE = Path.home() / "dream-cycle" / "performance.jsonl"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def log_event(task: str, outcome: str, model: str, duration: float = 0.0, note: str = ""):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "task": task,
        "outcome": outcome,        # success | failed | escalated | timeout
        "model": model,
        "duration_sec": duration,
        "note": note,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"Logged: [{outcome}] {task} via {model}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--outcome", required=True, choices=["success", "failed", "escalated", "timeout"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    log_event(args.task, args.outcome, args.model, args.duration, args.note)
