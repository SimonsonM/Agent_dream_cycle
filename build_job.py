#!/usr/bin/env python3
"""
Dream Cycle Build Job — Multi-Agent Edition
Runs at 4 AM via cron. Auto-applies low-risk staged changes.

Usage:
  build_job.py                   # run for all agents with staging dirs
  build_job.py --agent security  # run for one agent only
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR    = Path.home() / "dream-cycle"
LOGS_DIR    = Path.home() / "dream-logs"
AGENT_NAMES = ["security", "marketing", "programming", "ai_research"]


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def apply_action(action: dict, staged_file: Path, applied_dir: Path) -> bool:
    action_type  = action.get("action_type", "")
    content      = action.get("content", "")
    file_path    = action.get("file_path", "")
    rollback_cmd = action.get("rollback_command", "")
    title        = action.get("title", "unknown")

    log(f"  Applying: {title}")

    try:
        if action_type == "model_pull" and content.startswith("ollama pull"):
            model_name = shlex.split(content)[-1]
            result = subprocess.run(shlex.split(content),
                                    capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                log(f"    Model pull failed: {result.stderr}")
                return False
            log(f"    Pulled model: {model_name}")

        elif action_type in ("documentation", "config") and file_path and content:
            target = Path(file_path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.copy2(target, str(target) + ".bak")
            with open(target, "w") as f:
                f.write(content)
            log(f"    Written: {target}")

        elif action_type == "script" and file_path and content:
            target = Path(file_path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w") as f:
                f.write(content)
            os.chmod(target, 0o755)
            log(f"    Script written: {target}")

        else:
            log(f"    '{action_type}' noted but not auto-executed (safe skip)")

        # Write rollback script
        ts            = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title    = title[:30].replace(" ", "_")
        rollback_path = applied_dir / f"rollback_{ts}_{safe_title}.sh"
        with open(rollback_path, "w") as f:
            f.write("#!/bin/bash\n")
            f.write(f"# Rollback: {title}\n")
            f.write(f"# Applied: {datetime.now().isoformat()}\n\n")
            if rollback_cmd:
                f.write(rollback_cmd + "\n")
            elif file_path:
                expanded = Path(file_path).expanduser()
                bak      = Path(str(expanded) + ".bak")
                if bak.exists():
                    f.write(f'mv "{bak}" "{expanded}"\n')
                else:
                    f.write(f"echo 'Manual rollback required for: {title}'\n")
            else:
                f.write(f"echo 'Manual rollback required for: {title}'\n")
        os.chmod(rollback_path, 0o755)
        log(f"    Rollback: {rollback_path.name}")

        shutil.move(str(staged_file), str(applied_dir / staged_file.name))
        return True

    except Exception as e:
        log(f"    Apply failed: {e}")
        return False


def run_agent_build(agent_name: str, date_str: str) -> tuple[list, list]:
    staging_dir = BASE_DIR / agent_name / "staging"
    applied_dir = staging_dir / "applied"
    applied_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = staging_dir / f"{date_str}_manifest.json"
    if not manifest_path.exists():
        manifests = sorted(staging_dir.glob("*_manifest.json"))
        if not manifests:
            log(f"[{agent_name}] No manifests found, skipping")
            return [], []
        manifest_path = manifests[-1]
        log(f"[{agent_name}] Using most recent manifest: {manifest_path.name}")

    with open(manifest_path) as f:
        manifest = json.load(f)

    applied       = []
    review_needed = []

    for entry in manifest:
        risk        = entry.get("risk", "high")
        staged_file = Path(entry["file"])

        if not staged_file.exists():
            log(f"[{agent_name}] Staged file not found: {staged_file.name}")
            continue

        with open(staged_file) as f:
            action = json.load(f)

        if risk == "low":
            if apply_action(action, staged_file, applied_dir):
                applied.append(entry["title"])
        else:
            review_needed.append({"risk": risk, "title": entry["title"],
                                   "file": str(staged_file)})
            log(f"[{agent_name}] [{risk.upper()}] Flagged for review: {entry['title']}")

    return applied, review_needed


def main():
    parser = argparse.ArgumentParser(description="Dream Cycle Build Job")
    parser.add_argument("--agent", choices=AGENT_NAMES,
                        help="Run build for a specific agent (default: all)")
    args = parser.parse_args()

    date_str = datetime.now().strftime("%Y-%m-%d")
    log(f"=== Build Job Starting — {date_str} ===")

    if args.agent:
        agents_to_run = [args.agent]
    else:
        agents_to_run = [
            name for name in AGENT_NAMES
            if (BASE_DIR / name / "staging").exists()
        ]
        if not agents_to_run:
            log("No agent staging dirs found. Nothing to do.")
            return

    all_applied = {}
    all_review  = {}

    for agent_name in agents_to_run:
        log(f"--- {agent_name} ---")
        applied, review = run_agent_build(agent_name, date_str)
        all_applied[agent_name] = applied
        all_review[agent_name]  = review

    # Combined build report
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report_path   = LOGS_DIR / f"{date_str}-build-report.md"
    total_applied = sum(len(v) for v in all_applied.values())
    total_review  = sum(len(v) for v in all_review.values())

    with open(report_path, "w") as f:
        f.write(f"# Build Report — {date_str}\n\n")
        f.write(f"**Total auto-applied:** {total_applied}  \n")
        f.write(f"**Total needs review:** {total_review}\n\n---\n\n")
        for name in agents_to_run:
            applied = all_applied.get(name, [])
            review  = all_review.get(name, [])
            f.write(f"## Agent: {name}\n\n")
            f.write(f"### Auto-Applied ({len(applied)})\n")
            for a in applied:
                f.write(f"- {a}\n")
            f.write(f"\n### Needs Review ({len(review)})\n")
            for r in review:
                label = "MEDIUM" if r["risk"] == "medium" else "HIGH"
                f.write(f"- [{label}] {r['title']}\n")
                f.write(f"  File: `{r['file']}`\n")
            f.write("\n")
        f.write(f"---\n*Build job ran at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")
        f.write("## Rollback\n")
        f.write("Scripts: `ls ~/dream-cycle/AGENT/staging/applied/rollback_*.sh`\n")

    log(f"Build report: {report_path}")
    log(f"Applied: {total_applied} | Needs review: {total_review}")
    log("=== Build Job Complete ===")


if __name__ == "__main__":
    main()
