#!/usr/bin/env python3
"""
Dream Cycle Build Job
Runs at 4 AM via cron.
Auto-applies low-risk staged changes. Flags medium/high for morning review.
"""

import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path.home() / "dream-cycle"
STAGING_DIR = BASE_DIR / "dream-staging"
APPLIED_DIR = STAGING_DIR / "applied"
LOGS_DIR = Path.home() / "dream-logs"

for d in [APPLIED_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def apply_action(action: dict, staged_file: Path) -> bool:
    """Apply a staged action and write its rollback script."""
    action_type = action.get("action_type", "")
    content = action.get("content", "")
    file_path = action.get("file_path", "")
    rollback_cmd = action.get("rollback_command", "")
    title = action.get("title", "unknown")

    log(f"Applying: {title}")

    try:
        if action_type == "model_pull" and content.startswith("ollama pull"):
            model_name = shlex.split(content)[-1]
            result = subprocess.run(shlex.split(content), capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                log(f"  Model pull failed: {result.stderr}")
                return False
            log(f"  Pulled model: {model_name}")

        elif action_type == "documentation" and file_path and content:
            target = Path(file_path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            # Backup if exists
            if target.exists():
                shutil.copy2(target, str(target) + ".bak")
            with open(target, "w") as f:
                f.write(content)
            log(f"  Written: {target}")

        elif action_type == "config" and file_path and content:
            target = Path(file_path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.copy2(target, str(target) + ".bak")
            with open(target, "w") as f:
                f.write(content)
            log(f"  Config updated: {target}")

        elif action_type == "script" and file_path and content:
            target = Path(file_path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w") as f:
                f.write(content)
            os.chmod(target, 0o755)
            log(f"  Script written: {target}")

        else:
            log(f"  Action type '{action_type}' noted but not auto-executed (safe skip)")

        # Write rollback script
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        rollback_path = APPLIED_DIR / f"rollback_{ts}_{title[:30].replace(' ', '_')}.sh"
        with open(rollback_path, "w") as f:
            f.write("#!/bin/bash\n")
            f.write(f"# Rollback: {title}\n")
            f.write(f"# Applied: {datetime.now().isoformat()}\n\n")
            if rollback_cmd:
                f.write(rollback_cmd + "\n")
            elif file_path:
                expanded = Path(file_path).expanduser()
                bak = Path(str(expanded) + ".bak")
                if bak.exists():
                    f.write(f'mv "{bak}" "{expanded}"\n')
                else:
                    f.write(f"echo 'Manual rollback required for: {title}'\n")
            else:
                f.write(f"echo 'Manual rollback required for: {title}'\n")
        os.chmod(rollback_path, 0o755)
        log(f"  Rollback script: {rollback_path}")

        # Move staged file to applied
        shutil.move(str(staged_file), str(APPLIED_DIR / staged_file.name))
        return True

    except Exception as e:
        log(f"  Apply failed: {e}")
        return False


def main():
    date_str = datetime.now().strftime("%Y-%m-%d")
    log(f"=== Build Job Starting — {date_str} ===")

    # Find today's manifest
    manifest_path = STAGING_DIR / f"{date_str}_manifest.json"

    # Fall back to most recent manifest if today's not ready
    if not manifest_path.exists():
        manifests = sorted(STAGING_DIR.glob("*_manifest.json"))
        if not manifests:
            log("No manifests found. Nothing to do.")
            return
        manifest_path = manifests[-1]
        log(f"Using most recent manifest: {manifest_path.name}")

    with open(manifest_path) as f:
        manifest = json.load(f)

    applied = []
    review_needed = []

    for entry in manifest:
        risk = entry.get("risk", "high")
        staged_file = Path(entry["file"])

        if not staged_file.exists():
            log(f"Staged file not found: {staged_file}")
            continue

        with open(staged_file) as f:
            action = json.load(f)

        if risk == "low":
            success = apply_action(action, staged_file)
            if success:
                applied.append(entry["title"])
        else:
            review_needed.append({"risk": risk, "title": entry["title"], "file": str(staged_file)})
            log(f"  [{risk.upper()}] Flagged for review: {entry['title']}")

    # Write build report
    report_path = LOGS_DIR / f"{date_str}-build-report.md"
    with open(report_path, "w") as f:
        f.write(f"# Build Report — {date_str}\n\n")
        f.write(f"## Auto-Applied ({len(applied)})\n")
        for a in applied:
            f.write(f"- ✅ {a}\n")
        f.write(f"\n## Needs Your Review ({len(review_needed)})\n")
        for r in review_needed:
            f.write(f"- {'🟡' if r['risk'] == 'medium' else '🔴'} `{r['risk'].upper()}` — {r['title']}\n")
            f.write(f"  File: `{r['file']}`\n")
        f.write(f"\n---\n*Build job ran at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")
        f.write(f"\n## Rollback\n")
        f.write(f"To undo tonight's changes: `ls ~/dream-cycle/dream-staging/applied/rollback_*.sh`\n")

    log(f"Build report: {report_path}")
    log(f"Applied: {len(applied)} | Needs review: {len(review_needed)}")
    log("=== Build Job Complete ===")


if __name__ == "__main__":
    main()
