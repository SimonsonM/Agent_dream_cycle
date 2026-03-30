#!/usr/bin/env python3
"""
Dream Cycle Orchestrator
Runs nightly at 11:15 PM via cron.
Four phases: Scan → Reflect → Deep Research → Judge + Stage
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import requests

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path.home() / "dream-cycle"
STAGING_DIR = BASE_DIR / "dream-staging"
APPLIED_DIR = STAGING_DIR / "applied"
LOGS_DIR = Path.home() / "dream-logs"
PERF_LOG = BASE_DIR / "performance.jsonl"

for d in [STAGING_DIR, APPLIED_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Models ─────────────────────────────────────────────────────────────────────
LOCAL_MODEL = "qwen3.5:9b"       # Ollama — scan + reflect
CLAUDE_MODEL = "claude-sonnet-4-6"  # Anthropic — deep research + judge

TRACKS = ["AI/ML", "Cybersecurity", "Robotics/CV", "Data Analytics", "Project Management"]

client = anthropic.Anthropic()

# ── Helpers ────────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def ollama_chat(prompt: str, system: str = "") -> str:
    payload = {
        "model": LOCAL_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if system:
        payload["messages"].insert(0, {"role": "system", "content": system})
    try:
        r = requests.post("http://localhost:11434/api/chat", json=payload, timeout=120)
        r.raise_for_status()
        return r.json()["message"]["content"]
    except Exception as e:
        log(f"Ollama error: {e}")
        return ""

def claude_chat(prompt: str, system: str = "") -> str:
    kwargs = {
        "model": CLAUDE_MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    try:
        msg = client.messages.create(**kwargs)
        return msg.content[0].text
    except Exception as e:
        log(f"Claude error: {e}")
        return ""

def fetch_arxiv(query: str, max_results: int = 10) -> list[dict]:
    import xml.etree.ElementTree as ET
    url = "http://export.arxiv.org/api/query"
    params = {"search_query": query, "max_results": max_results, "sortBy": "submittedDate"}
    try:
        r = requests.get(url, params=params, timeout=30)
        root = ET.fromstring(r.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        papers = []
        for entry in root.findall("atom:entry", ns):
            papers.append({
                "title": entry.find("atom:title", ns).text.strip(),
                "summary": entry.find("atom:summary", ns).text.strip()[:500],
                "link": entry.find("atom:id", ns).text.strip(),
            })
        return papers
    except Exception as e:
        log(f"arXiv fetch error: {e}")
        return []

def fetch_github_trending() -> list[dict]:
    try:
        r = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": "ai agent machine-learning", "sort": "stars", "order": "desc", "per_page": 10},
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=30,
        )
        repos = r.json().get("items", [])
        return [{"name": x["full_name"], "description": x.get("description", ""), "stars": x["stargazers_count"]} for x in repos]
    except Exception as e:
        log(f"GitHub fetch error: {e}")
        return []

def fetch_cve_recent() -> list[dict]:
    try:
        r = requests.get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params={"resultsPerPage": 10, "startIndex": 0},
            timeout=30,
        )
        items = r.json().get("vulnerabilities", [])
        return [
            {
                "id": x["cve"]["id"],
                "description": x["cve"]["descriptions"][0]["value"][:300] if x["cve"]["descriptions"] else "",
            }
            for x in items
        ]
    except Exception as e:
        log(f"CVE fetch error: {e}")
        return []

def load_perf_log() -> list[dict]:
    if not PERF_LOG.exists():
        return []
    entries = []
    with open(PERF_LOG) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries[-50:]  # last 50 events

# ── Phase 1: Scan ──────────────────────────────────────────────────────────────

def phase_scan() -> dict:
    log("Phase 1: Scanning sources...")

    arxiv_ai = fetch_arxiv("machine learning agents LLM", 8)
    arxiv_cv = fetch_arxiv("computer vision robotics OpenCV", 5)
    arxiv_sec = fetch_arxiv("cybersecurity vulnerability detection", 5)
    github = fetch_github_trending()
    cves = fetch_cve_recent()

    raw = {
        "arxiv_ai": arxiv_ai,
        "arxiv_cv": arxiv_cv,
        "arxiv_sec": arxiv_sec,
        "github_trending": github,
        "recent_cves": cves,
    }

    prompt = f"""You are the scan phase of a nightly research agent for a technical consultant
working across: {', '.join(TRACKS)}.

Here is tonight's raw data:
{json.dumps(raw, indent=2)[:6000]}

Tasks:
1. Score each item 1-10 for relevance across all tracks
2. Pick tonight's TOP PRIORITY TRACK based on what's freshest and most actionable
3. Select the 5 most important findings overall
4. Return JSON only:
{{
  "priority_track": "...",
  "priority_reason": "...",
  "top_findings": [
    {{"title": "...", "source": "...", "track": "...", "score": 8, "link": "...", "summary": "..."}}
  ]
}}"""

    result = ollama_chat(prompt)
    try:
        clean = result[result.index("{"):result.rindex("}") + 1]
        return json.loads(clean)
    except Exception:
        log("Scan parse failed, using raw structure")
        return {"priority_track": "AI/ML", "priority_reason": "parse error", "top_findings": []}

# ── Phase 2: Reflect ───────────────────────────────────────────────────────────

def phase_reflect() -> dict:
    log("Phase 2: Reflecting on today's performance...")

    perf = load_perf_log()
    if not perf:
        return {"observations": ["No performance data yet."], "improvement_areas": []}

    prompt = f"""You are the reflection phase of a nightly agent review.
Here are today's agent performance events (tasks, outcomes, escalations):
{json.dumps(perf, indent=2)[:3000]}

Analyze:
1. What patterns do you see in failures or escalations?
2. What tasks could have been handled by a cheaper/faster model?
3. What's one concrete process improvement for tomorrow?

Return JSON only:
{{
  "observations": ["...", "..."],
  "improvement_areas": ["...", "..."],
  "suggested_improvement": "..."
}}"""

    result = ollama_chat(prompt)
    try:
        clean = result[result.index("{"):result.rindex("}") + 1]
        return json.loads(clean)
    except Exception:
        return {"observations": ["Reflection parse failed"], "improvement_areas": []}

# ── Phase 3: Deep Research ─────────────────────────────────────────────────────

def phase_deep_research(scan_results: dict) -> dict:
    log("Phase 3: Deep research via Claude...")

    findings = scan_results.get("top_findings", [])
    priority = scan_results.get("priority_track", "AI/ML")

    prompt = f"""You are the deep research phase of a nightly agent.
Tonight's priority track: {priority}

Top findings to research deeply:
{json.dumps(findings, indent=2)}

For each finding:
1. Explain what it actually is and why it matters
2. Assess direct applicability to: AI/ML stack (Ollama, MCP servers, Claude API),
   robotics project (Raspberry Pi, OpenCV, MediaPipe, rover chassis),
   cybersecurity consulting, or data analytics work
3. Identify if it suggests any change to current tools, workflows, or configs
4. If iterative depth applies (a finding builds on another finding), note it

Return JSON:
{{
  "research": [
    {{
      "title": "...",
      "deep_summary": "...",
      "applicability": "high|medium|low",
      "applicable_to": ["..."],
      "suggests_change": true/false,
      "change_description": "...",
      "iterative_depth": "..."
    }}
  ],
  "cross_connections": "..."
}}"""

    result = claude_chat(prompt)
    try:
        clean = result[result.index("{"):result.rindex("}") + 1]
        return json.loads(clean)
    except Exception:
        log("Deep research parse failed")
        return {"research": [], "cross_connections": ""}

# ── Phase 4: Judge + Stage ─────────────────────────────────────────────────────

def phase_judge_and_stage(scan: dict, reflect: dict, research: dict) -> dict:
    log("Phase 4: Judging and staging changes...")

    prompt = f"""You are the judgment phase of a nightly self-improving agent.

Scan results: {json.dumps(scan, indent=2)[:2000]}
Reflection: {json.dumps(reflect, indent=2)}
Deep research: {json.dumps(research, indent=2)[:3000]}

Decide what actions to stage. Rules:
- risk: low → safe to auto-apply at 4 AM (documentation updates, new model pulls, config tweaks)
- risk: medium → stage for human review (workflow changes, new tool integrations)
- risk: high → stage with detailed notes, never auto-apply (anything touching live systems)

For each staged action, provide a rollback_command.

Return JSON:
{{
  "staged_actions": [
    {{
      "title": "...",
      "description": "...",
      "risk": "low|medium|high",
      "action_type": "config|script|documentation|model_pull|workflow",
      "file_path": "...",
      "content": "...",
      "rollback_command": "..."
    }}
  ],
  "summary": "...",
  "tonight_score": 8
}}"""

    result = claude_chat(prompt)
    try:
        clean = result[result.index("{"):result.rindex("}") + 1]
        return json.loads(clean)
    except Exception:
        log("Judge parse failed")
        return {"staged_actions": [], "summary": "Parse failed", "tonight_score": 0}

# ── Write Staged Files ─────────────────────────────────────────────────────────

def write_staging(judge: dict, date_str: str):
    actions = judge.get("staged_actions", [])
    manifest = []

    for i, action in enumerate(actions):
        risk = action.get("risk", "high")
        fname = f"{date_str}_{i:02d}_{action.get('action_type', 'change')}_{risk}.staged"
        fpath = STAGING_DIR / fname

        with open(fpath, "w") as f:
            json.dump(action, f, indent=2)

        manifest.append({"file": str(fpath), "risk": risk, "title": action.get("title", "")})
        log(f"  Staged [{risk}]: {action.get('title', '')}")

    manifest_path = STAGING_DIR / f"{date_str}_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return manifest

# ── Write Changelog ────────────────────────────────────────────────────────────

def write_changelog(date_str: str, scan: dict, reflect: dict, research: dict, judge: dict, manifest: list):
    changelog = LOGS_DIR / f"{date_str}-changelog.md"

    lines = [
        f"# Dream Cycle — {date_str}",
        f"\n**Priority Track Tonight:** {scan.get('priority_track', '?')}  ",
        f"**Reason:** {scan.get('priority_reason', '')}  ",
        f"**Score:** {judge.get('tonight_score', '?')}/10\n",
        "---\n",
        "## Phase 1 — Top Findings\n",
    ]

    for f in scan.get("top_findings", []):
        lines.append(f"- **{f.get('title', '')}** `[{f.get('track', '')}]` score:{f.get('score', '?')}  ")
        lines.append(f"  {f.get('summary', '')}\n")

    lines += ["\n## Phase 2 — Reflection\n"]
    for obs in reflect.get("observations", []):
        lines.append(f"- {obs}")
    if reflect.get("suggested_improvement"):
        lines.append(f"\n**Tonight's Improvement Suggestion:** {reflect['suggested_improvement']}\n")

    lines += ["\n## Phase 3 — Deep Research\n"]
    for r in research.get("research", []):
        lines.append(f"### {r.get('title', '')}")
        lines.append(f"**Applicability:** {r.get('applicability', '?')} | **Tracks:** {', '.join(r.get('applicable_to', []))}")
        lines.append(f"\n{r.get('deep_summary', '')}\n")
        if r.get("suggests_change"):
            lines.append(f"⚡ **Suggests change:** {r.get('change_description', '')}\n")

    lines += ["\n## Phase 4 — Staged Actions\n"]
    for m in manifest:
        risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(m["risk"], "⚪")
        lines.append(f"{risk_emoji} `{m['risk'].upper()}` — {m['title']}")

    lines += [f"\n---\n*Generated by dream_cycle.py at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]

    with open(changelog, "w") as f:
        f.write("\n".join(lines))

    log(f"Changelog written: {changelog}")
    return str(changelog)

# ── Send Gmail Summary ─────────────────────────────────────────────────────────

def send_gmail_summary(changelog_path: str, judge: dict, scan: dict):
    """
    Sends summary via msmtp or your MCP Gmail server.
    Requires msmtp configured, or replace with your MCP Gmail call.
    """
    subject = f"🌙 Dream Cycle {datetime.now().strftime('%Y-%m-%d')} — {scan.get('priority_track', '?')} | Score {judge.get('tonight_score', '?')}/10"
    body = judge.get("summary", "No summary generated.")
    body += f"\n\nFull changelog: {changelog_path}"
    body += f"\nStaged actions: {len(judge.get('staged_actions', []))}"

    # Try msmtp first
    try:
        proc = subprocess.run(
            ["msmtp", "-t"],
            input=f"To: m.simonson01@gmail.com\nSubject: {subject}\n\n{body}",
            capture_output=True, text=True, timeout=30
        )
        if proc.returncode == 0:
            log("Gmail sent via msmtp")
            return
    except FileNotFoundError:
        pass

    # Fallback: write to a .mail file for manual pickup
    mail_path = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.mail"
    with open(mail_path, "w") as f:
        f.write(f"Subject: {subject}\n\n{body}")
    log(f"Gmail not configured — mail saved to {mail_path}")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    date_str = datetime.now().strftime("%Y-%m-%d")
    log(f"=== Dream Cycle Starting — {date_str} ===")

    scan = phase_scan()
    reflect = phase_reflect()
    research = phase_deep_research(scan)
    judge = phase_judge_and_stage(scan, reflect, research)
    manifest = write_staging(judge, date_str)
    changelog_path = write_changelog(date_str, scan, reflect, research, judge, manifest)
    send_gmail_summary(changelog_path, judge, scan)

    log(f"=== Dream Cycle Complete. {len(manifest)} actions staged. ===")

if __name__ == "__main__":
    main()
