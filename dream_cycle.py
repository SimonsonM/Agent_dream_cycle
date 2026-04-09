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
import xml.etree.ElementTree as ET

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path.home() / "dream-cycle"
STAGING_DIR = BASE_DIR / "dream-staging"
APPLIED_DIR = STAGING_DIR / "applied"
LOGS_DIR = Path.home() / "dream-logs"
PERF_LOG = BASE_DIR / "performance.jsonl"
CONFIG_FILE = BASE_DIR / "config.json"

for d in [STAGING_DIR, APPLIED_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Models ─────────────────────────────────────────────────────────────────────
LOCAL_MODEL = ""                    # set at runtime from config or interactive selection
CLAUDE_MODEL = "claude-sonnet-4-6"  # Anthropic — deep research + judge

TRACKS = ["AI/ML", "Cybersecurity", "Robotics/CV", "Data Analytics", "Project Management"]

DEFAULT_RSS_FEEDS = [
    {"name": "Hacker News",        "url": "https://news.ycombinator.com/rss",                                  "track": "AI/ML"},
    {"name": "HuggingFace Blog",   "url": "https://huggingface.co/blog/feed.xml",                              "track": "AI/ML"},
    {"name": "Anthropic Blog",     "url": "https://www.anthropic.com/rss.xml",                                 "track": "AI/ML"},
    {"name": "MIT AI News",        "url": "https://news.mit.edu/topic/artificial-intelligence2/feed",          "track": "AI/ML"},
    {"name": "Krebs on Security",  "url": "https://krebsonsecurity.com/feed/",                                 "track": "Cybersecurity"},
    {"name": "The Hacker News",    "url": "https://feeds.feedburner.com/TheHackersNews",                       "track": "Cybersecurity"},
    {"name": "Dark Reading",       "url": "https://www.darkreading.com/rss.xml",                               "track": "Cybersecurity"},
    {"name": "IEEE Spectrum AI",   "url": "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", "track": "AI/ML"},
    {"name": "IEEE Robotics",      "url": "https://spectrum.ieee.org/feeds/topic/robotics.rss",                "track": "Robotics/CV"},
    {"name": "Towards Data Sci.",  "url": "https://towardsdatascience.com/feed",                               "track": "Data Analytics"},
]

client = anthropic.Anthropic()

# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

# ── Model selection ────────────────────────────────────────────────────────────

def list_ollama_models() -> list[str]:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=10)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []

def pull_ollama_model(model_name: str) -> bool:
    print(f"Pulling {model_name} (this may take a while)...")
    try:
        result = subprocess.run(["ollama", "pull", model_name], timeout=600)
        return result.returncode == 0
    except Exception as e:
        print(f"Pull failed: {e}")
        return False

def select_local_model() -> str:
    """Interactively pick an installed Ollama model or pull a new one."""
    print("\n── Local Model Selection ──────────────────────────────────────")
    available = list_ollama_models()

    options = available + ["Pull a different model"]
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    print()

    while True:
        try:
            raw = input(f"Select [1-{len(options)}]: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(available):
                return available[idx]
            elif idx == len(available):
                break
            else:
                print(f"  Enter a number between 1 and {len(options)}")
        except (ValueError, EOFError):
            print("  Enter a number")

    # Pull path
    model_name = input("Model name to pull (e.g. qwen2.5:7b): ").strip()
    if model_name:
        pull_ollama_model(model_name)
        return model_name
    return "qwen2.5:7b"

# ── RSS feeds ─────────────────────────────────────────────────────────────────

def fetch_rss_feed(feed: dict, max_items: int = 5) -> list[dict]:
    """Fetch and parse a single RSS 2.0 or Atom feed."""
    url, name = feed["url"], feed["name"]
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "DreamCycle/1.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)

        # Detect format: Atom feeds have a namespace containing 'atom' or use <feed> root
        raw_tag = root.tag
        ns_uri = raw_tag[1:raw_tag.index("}")] if raw_tag.startswith("{") else ""
        is_atom = "atom" in ns_uri or root.tag.endswith("feed")
        p = f"{{{ns_uri}}}" if ns_uri else ""

        items = []
        if is_atom:
            for entry in root.findall(f"{p}entry")[:max_items]:
                title_el  = entry.find(f"{p}title")
                link_el   = entry.find(f"{p}link")
                summ_el   = entry.find(f"{p}summary") or entry.find(f"{p}content")
                items.append({
                    "title":   (title_el.text or "").strip(),
                    "link":    link_el.get("href", "") if link_el is not None else "",
                    "summary": (summ_el.text or "")[:400].strip() if summ_el is not None else "",
                    "source":  name,
                    "track":   feed.get("track", "AI/ML"),
                })
        else:
            channel = root.find("channel")
            if channel is None:
                return []
            for item in channel.findall("item")[:max_items]:
                title_el = item.find("title")
                link_el  = item.find("link")
                desc_el  = item.find("description")
                items.append({
                    "title":   (title_el.text or "").strip() if title_el is not None else "",
                    "link":    (link_el.text or "").strip()  if link_el  is not None else "",
                    "summary": (desc_el.text  or "")[:400].strip() if desc_el is not None else "",
                    "source":  name,
                    "track":   feed.get("track", "AI/ML"),
                })
        return items
    except Exception as e:
        log(f"RSS fetch error ({name}): {e}")
        return []

def fetch_all_rss_feeds(feeds: list[dict]) -> list[dict]:
    results = []
    for feed in feeds:
        items = fetch_rss_feed(feed)
        results.extend(items)
        if items:
            log(f"  RSS [{feed['name']}]: {len(items)} items")
    return results

def configure_rss_feeds() -> list[dict]:
    """Interactively select RSS feeds to subscribe to."""
    print("\n── RSS Feed Selection ─────────────────────────────────────────")
    print("Select feeds to follow (comma-separated numbers, 'all', or 'none'):\n")
    for i, f in enumerate(DEFAULT_RSS_FEEDS, 1):
        print(f"  {i:2}. [{f['track']:15}] {f['name']}")
    print()

    while True:
        raw = input("Selection [all]: ").strip().lower() or "all"
        if raw == "all":
            selected = list(DEFAULT_RSS_FEEDS)
            break
        if raw == "none":
            selected = []
            break
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(",")]
            if all(0 <= i < len(DEFAULT_RSS_FEEDS) for i in indices):
                selected = [DEFAULT_RSS_FEEDS[i] for i in indices]
                break
            print(f"  Numbers must be between 1 and {len(DEFAULT_RSS_FEEDS)}")
        except ValueError:
            print("  Enter numbers separated by commas, 'all', or 'none'")

    # Allow custom feed URLs
    print(f"\n{len(selected)} feed(s) selected.")
    while True:
        custom = input("Add a custom feed URL? (paste URL or press Enter to skip): ").strip()
        if not custom:
            break
        custom_name = input("  Name for this feed: ").strip() or custom
        custom_track = input(f"  Track ({'/'.join(TRACKS)}): ").strip() or "AI/ML"
        selected.append({"name": custom_name, "url": custom, "track": custom_track})
        print(f"  Added: {custom_name}")

    return selected

# ── Helpers ────────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def extract_json(text: str) -> dict:
    """Extract the first valid JSON object from an LLM response."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == '{':
            try:
                obj, _ = decoder.raw_decode(text, i)
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("No JSON object found in response")

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

def phase_scan(rss_feeds: list[dict] = None) -> dict:
    log("Phase 1: Scanning sources...")

    arxiv_ai = fetch_arxiv("machine learning agents LLM", 8)
    arxiv_cv = fetch_arxiv("computer vision robotics OpenCV", 5)
    arxiv_sec = fetch_arxiv("cybersecurity vulnerability detection", 5)
    github = fetch_github_trending()
    cves = fetch_cve_recent()
    rss_items = fetch_all_rss_feeds(rss_feeds) if rss_feeds else []

    raw = {
        "arxiv_ai": arxiv_ai,
        "arxiv_cv": arxiv_cv,
        "arxiv_sec": arxiv_sec,
        "github_trending": github,
        "recent_cves": cves,
        "rss_feeds": rss_items,
    }

    prompt = f"""You are the scan phase of a nightly research agent for a technical consultant
working across: {', '.join(TRACKS)}.

Here is tonight's raw data (includes arXiv papers, GitHub trending, CVEs, and RSS feed items):
{json.dumps(raw, indent=2)[:8000]}

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
        return extract_json(result)
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
        return extract_json(result)
    except Exception:
        return {"observations": ["Reflection parse failed"], "improvement_areas": []}

# ── Phase 3: Deep Research ─────────────────────────────────────────────────────

def phase_deep_research(scan_results: dict) -> dict:
    log("Phase 3: Deep research via Claude...")

    findings = scan_results.get("top_findings", [])
    if not findings:
        log("No findings from Phase 1, skipping Phase 3")
        return {"research": [], "cross_connections": ""}
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
        return extract_json(result)
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
        return extract_json(result)
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
    global LOCAL_MODEL

    date_str = datetime.now().strftime("%Y-%m-%d")
    log(f"=== Dream Cycle Starting — {date_str} ===")

    # Load persisted settings, or prompt once and save
    config = load_config()
    if not config.get("local_model"):
        config["local_model"] = select_local_model()
        save_config(config)
    if "rss_feeds" not in config:
        config["rss_feeds"] = configure_rss_feeds()
        save_config(config)
    LOCAL_MODEL = config["local_model"]
    rss_feeds = config.get("rss_feeds", [])
    log(f"Local model: {LOCAL_MODEL} | RSS feeds: {len(rss_feeds)}")

    scan = phase_scan(rss_feeds)
    reflect = phase_reflect()
    research = phase_deep_research(scan)
    judge = phase_judge_and_stage(scan, reflect, research)
    manifest = write_staging(judge, date_str)
    changelog_path = write_changelog(date_str, scan, reflect, research, judge, manifest)
    send_gmail_summary(changelog_path, judge, scan)

    log(f"=== Dream Cycle Complete. {len(manifest)} actions staged. ===")

if __name__ == "__main__":
    main()
