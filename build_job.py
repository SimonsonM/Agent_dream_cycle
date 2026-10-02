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
import hashlib
import re
import subprocess
from datetime import datetime
from pathlib import Path

import policy

BASE_DIR = Path.home() / "dream-cycle"
LOGS_DIR = Path.home() / "dream-logs"

ALLOWED_WRITE_DIRS = [BASE_DIR, LOGS_DIR]


def _safe_target(file_path: str) -> Path | None:
    """Resolve file_path iff allowed and not protected (see policy.safe_target)."""
    t = policy.safe_target(file_path, ALLOWED_WRITE_DIRS)
    if t is None:
        log(f"    BLOCKED: '{file_path}' is protected or outside allowed directories")
    return t


def discover_agent_names() -> list[str]:
    """Return agent names by scanning BASE_DIR for subdirs that have a staging dir.

    This replaces the old hardcoded AGENT_NAMES list and automatically picks up
    any manifest-registered agents whose first run has already created directories.
    """
    if not BASE_DIR.exists():
        return []
    return sorted(
        d.name for d in BASE_DIR.iterdir()
        if d.is_dir() and (d / "staging").is_dir()
    )


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _write_applied_record(agent_name: str, event: str, title: str,
                           action_type: str, file_path: str, rollback_path: str,
                           sha256: str = ""):
    """Append one line to <agent>/logs/applied_changes.jsonl for reflection feedback."""
    import json
    log_path = BASE_DIR / agent_name / "logs" / "applied_changes.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp":    datetime.now().isoformat(),
        "event":        event,          # "applied" | "reverted"
        "title":        title,
        "action_type":  action_type,
        "file_path":    file_path,
        "rollback_path": rollback_path,
        "sha256":       sha256,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")


def _write_rollback(path: Path, title: str, record: dict, bak: Path | None,
                    target: Path | None, applied_log: str) -> None:
    """Rollback script built from structured data only; no LLM-supplied shell."""
    one_line = re.sub(r"[^\x20-\x7e]", " ", title)[:80]
    lines = ["#!/bin/bash", "set -u", f"# Rollback: {one_line}",
             f"# Applied: {record['timestamp']}", ""]
    if target is not None and bak is not None:
        lines.append(f"mv -f -- {shlex.quote(str(bak))} {shlex.quote(str(target))}")
    elif target is not None:
        lines.append(f"rm -f -- {shlex.quote(str(target))}")
    else:
        lines.append(f"echo {shlex.quote('Manual rollback required for: ' + one_line)}")
    if applied_log:
        rec = dict(record, event="reverted")
        lines.append(f"printf '%s\\n' {shlex.quote(json.dumps(rec))} >> {shlex.quote(applied_log)}")
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o755)


def apply_action(action: dict, staged_file: Path, applied_dir: Path,
                 agent_name: str = "") -> bool:
    action_type = action.get("action_type", "")
    content     = action.get("content", "") or ""
    file_path   = action.get("file_path", "")
    title       = action.get("title", "unknown")

    # Defense in depth: re-derive risk here; never trust the manifest or the LLM.
    risk, why = policy.effective_risk(action, ALLOWED_WRITE_DIRS)
    if risk != "low":
        log(f"    REFUSED ({risk}): {title} — {'; '.join(why)}")
        return False

    log(f"  Applying: {title}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target: Path | None = None
    bak: Path | None = None
    try:
        if action_type == "model_pull":
            model = shlex.split(content)[-1]
            result = subprocess.run(["ollama", "pull", model],
                                    capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                log(f"    Model pull failed: {result.stderr}")
                return False
            log(f"    Pulled model: {model}")

        elif action_type in ("documentation", "config"):
            target = _safe_target(file_path)
            if target is None:
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                bak = target.with_name(f"{target.name}.{ts}.bak")
                shutil.copy2(target, bak)
            tmp = target.with_name(target.name + ".tmp")
            with open(tmp, "w") as f:
                f.write(content)
            os.replace(tmp, target)
            log(f"    Written: {target}")
        else:
            log(f"    '{action_type}' noted but not auto-executed (safe skip)")
            return False

        safe_title    = re.sub(r"[^A-Za-z0-9_.-]", "_", title)[:30]
        rollback_path = applied_dir / f"rollback_{ts}_{safe_title}.sh"
        applied_log   = str(BASE_DIR / agent_name / "logs" / "applied_changes.jsonl") if agent_name else ""
        sha = hashlib.sha256(content.encode()).hexdigest()
        record = {"timestamp": datetime.now().isoformat(), "event": "applied",
                  "title": title, "action_type": action_type,
                  "file_path": str(target) if target else file_path,
                  "rollback_path": str(rollback_path), "sha256": sha}
        _write_rollback(rollback_path, title, record, bak, target, applied_log)
        log(f"    Rollback: {rollback_path.name}")

        if agent_name:
            _write_applied_record(agent_name, "applied", title, action_type,
                                  record["file_path"], str(rollback_path), sha)

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

        risk = policy.max_risk(risk, policy.effective_risk(action, ALLOWED_WRITE_DIRS)[0])
        if risk == "low":
            if apply_action(action, staged_file, applied_dir, agent_name=agent_name):
                applied.append(entry["title"])
        else:
            review_needed.append({"risk": risk, "title": entry["title"],
                                   "file": str(staged_file)})
            log(f"[{agent_name}] [{risk.upper()}] Flagged for review: {entry['title']}")

    return applied, review_needed


def main():
    parser = argparse.ArgumentParser(description="Dream Cycle Build Job")
    parser.add_argument("--agent",
                        help="Run build for a specific agent (default: all with staging dirs)")
    args = parser.parse_args()

    date_str = datetime.now().strftime("%Y-%m-%d")
    log(f"=== Build Job Starting — {date_str} ===")

    if args.agent:
        agents_to_run = [args.agent]
    else:
        agents_to_run = discover_agent_names()
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
