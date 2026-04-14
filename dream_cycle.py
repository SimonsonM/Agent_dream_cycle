#!/usr/bin/env python3
"""
Dream Cycle Orchestrator — Multi-Agent Edition
Runs nightly via cron.

Usage:
  dream_cycle.py                          # run default agent (ai_research)
  dream_cycle.py --agent security         # run specific agent
  dream_cycle.py --agent security --reconfigure
  dream_cycle.py --list-agents
"""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import requests
import math
import uuid
import xml.etree.ElementTree as ET
try:
    import chromadb
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path.home() / "dream-cycle"
LOGS_DIR    = Path.home() / "dream-logs"
CONFIG_FILE = BASE_DIR / "config.json"

BASE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR = BASE_DIR / "chroma_db"

# ── Models ────────────────────────────────────────────────────────────────────
LOCAL_MODEL    = ""
CLAUDE_MODEL   = "claude-sonnet-4-6"
client         = anthropic.Anthropic()
UCB1_C_DEFAULT = 1.4

# ── Agent Profiles ────────────────────────────────────────────────────────────
AGENT_PROFILES = {
    "security": {
        "name": "Security Research Agent",
        "tracks": [
            "CVE/Vulnerability Research", "Threat Intelligence",
            "Malware Analysis", "Zero-Day Exploits", "Security Tooling",
        ],
        "arxiv_queries": [
            ("cybersecurity vulnerability detection exploit adversarial", 8),
            ("malware detection machine learning intrusion", 5),
            ("network security anomaly detection zero-day", 5),
        ],
        "default_github_repos": [
            "projectdiscovery/nuclei",
            "aquasecurity/trivy",
            "rapid7/metasploit-framework",
            "nmap/nmap",
            "OWASP/owasp-mstg",
        ],
        "fetch_cves": True,
        "fetch_github_trending": False,
        "context": (
            "Focus on exploitability, CVE severity, defensive tooling, "
            "and threat actor TTPs. Cross-reference MITRE ATT&CK where applicable."
        ),
    },
    "marketing": {
        "name": "Marketing Intelligence Agent",
        "tracks": [
            "Growth Hacking", "SEO/SEM", "Content Strategy",
            "MarTech Stack", "Analytics & Attribution",
        ],
        "arxiv_queries": [
            ("recommendation systems user engagement personalization", 5),
            ("natural language generation content marketing automation", 5),
            ("causal inference A/B testing conversion rate optimization", 4),
        ],
        "default_github_repos": [
            "apache/superset",
            "metabase/metabase",
            "PostHog/posthog",
            "plausible/analytics",
            "matomo-org/matomo",
        ],
        "fetch_cves": False,
        "fetch_github_trending": False,
        "context": (
            "Focus on conversion rate impact, SEO ranking signals, "
            "content distribution leverage, and MarTech integrations "
            "that reduce manual work."
        ),
    },
    "programming": {
        "name": "Programming Intelligence Agent",
        "tracks": [
            "AI/ML Engineering", "Web Development", "DevOps/Infrastructure",
            "Language Updates", "Developer Tooling",
        ],
        "arxiv_queries": [
            ("machine learning engineering MLOps deployment optimization", 8),
            ("large language model fine-tuning efficient inference", 6),
            ("distributed systems reliability fault tolerance", 5),
        ],
        "default_github_repos": [
            "astral-sh/uv",
            "astral-sh/ruff",
            "microsoft/TypeScript",
            "vercel/next.js",
            "docker/compose",
        ],
        "fetch_cves": False,
        "fetch_github_trending": True,
        "context": (
            "Focus on language runtime changes, breaking API changes, "
            "new tooling that replaces existing workflow steps, and "
            "infrastructure patterns that reduce operational overhead."
        ),
    },
    "ai_research": {
        "name": "AI Research Agent",
        "tracks": [
            "LLM Research", "Computer Vision", "Robotics",
            "AI Safety & Alignment", "Multimodal Models",
        ],
        "arxiv_queries": [
            ("large language model reasoning chain-of-thought agents tool use", 10),
            ("computer vision object detection transformer", 7),
            ("robotics reinforcement learning manipulation", 6),
            ("AI safety alignment value learning interpretability", 6),
            ("multimodal vision language foundation model", 5),
        ],
        "default_github_repos": [
            "huggingface/transformers",
            "ollama/ollama",
            "anthropics/anthropic-sdk-python",
            "facebookresearch/segment-anything",
            "ggerganov/llama.cpp",
        ],
        "fetch_cves": False,
        "fetch_github_trending": True,
        "context": (
            "Focus on benchmark improvements, architectural innovations, "
            "training efficiency breakthroughs, and safety implications. "
            "Note when a paper challenges current best practices."
        ),
    },
}

# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                raw = json.load(f)
            # Migrate old flat format
            if "local_model" in raw and "global" not in raw:
                migrated = {"global": {"local_model": raw["local_model"]}, "agents": {}}
                save_config(migrated)
                return migrated
            return raw
        except Exception:
            pass
    return {}

def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

def get_agent_config(config: dict, agent_name: str) -> dict:
    return config.get("agents", {}).get(agent_name, {})

def set_agent_config(config: dict, agent_name: str, agent_cfg: dict) -> dict:
    config.setdefault("agents", {})[agent_name] = agent_cfg
    return config

# ── Per-agent directories ─────────────────────────────────────────────────────

def get_agent_dirs(agent_name: str) -> dict:
    agent_dir   = BASE_DIR / agent_name
    staging_dir = agent_dir / "staging"
    applied_dir = staging_dir / "applied"
    logs_dir    = agent_dir / "logs"
    for d in [staging_dir, applied_dir, logs_dir]:
        d.mkdir(parents=True, exist_ok=True)
    return {
        "agent_dir":   agent_dir,
        "staging_dir": staging_dir,
        "applied_dir": applied_dir,
        "logs_dir":    logs_dir,
        "perf_log":    agent_dir / "performance.jsonl",
        "seen_cache":  agent_dir / "seen_cache.json",
    }

# ── Model selection ───────────────────────────────────────────────────────────

def list_ollama_models() -> list[str]:
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
    print("\n── Local Model Selection ──────────────────────────────────────")
    available = list_ollama_models()
    options   = available + ["Pull a different model"]
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    print()
    while True:
        try:
            idx = int(input(f"Select [1-{len(options)}]: ").strip()) - 1
            if 0 <= idx < len(available):
                return available[idx]
            elif idx == len(available):
                break
            print(f"  Enter a number between 1 and {len(options)}")
        except (ValueError, EOFError):
            print("  Enter a number")
    name = input("Model name to pull (e.g. qwen2.5:7b): ").strip()
    if name:
        pull_ollama_model(name)
        return name
    return "qwen2.5:7b"

# ── Agent setup ───────────────────────────────────────────────────────────────

def configure_agent(agent_name: str, profile: dict) -> dict:
    print(f"\n── Configuring {profile['name']} ─────────────────────────────")
    print(f"Tracks: {', '.join(profile['tracks'])}\n")
    print("Default GitHub repos to watch for releases:")
    for i, repo in enumerate(profile["default_github_repos"], 1):
        print(f"  {i}. {repo}")
    raw = input("\nKeep all, or enter comma-separated list [all]: ").strip()
    if raw.lower() in ("", "all"):
        github_repos = list(profile["default_github_repos"])
    else:
        github_repos = [r.strip() for r in raw.split(",") if r.strip()]
    while True:
        extra = input("Add another repo (owner/name, Enter to skip): ").strip()
        if not extra:
            break
        github_repos.append(extra)
    return {"github_repos": github_repos}

# ── Seen-items cache ──────────────────────────────────────────────────────────

CACHE_TTL_DAYS = 7

def item_hash(title: str, link: str) -> str:
    return hashlib.sha256(f"{title.strip()}{link.strip()}".encode()).hexdigest()[:16]

def load_seen_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path) as f:
            return json.load(f)
    except Exception:
        return {}

def save_seen_cache(cache_path: Path, cache: dict):
    cutoff = (datetime.now() - timedelta(days=CACHE_TTL_DAYS * 2)).isoformat()
    pruned = {k: v for k, v in cache.items() if v >= cutoff}
    with open(cache_path, "w") as f:
        json.dump(pruned, f)

def filter_seen(items: list[dict], cache: dict) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=CACHE_TTL_DAYS)).isoformat()
    now    = datetime.now().isoformat()
    fresh  = []
    for item in items:
        h  = item_hash(item.get("title", ""), item.get("link", ""))
        ts = cache.get(h)
        if ts is None or ts < cutoff:
            fresh.append(item)
        cache.setdefault(h, now)
    skipped = len(items) - len(fresh)
    if skipped:
        log(f"  Dedup cache: skipped {skipped} already-seen items")
    return fresh

# ── Keyword pre-filter ────────────────────────────────────────────────────────

def keyword_score(item: dict, tracks: list[str]) -> int:
    keywords = set()
    for t in tracks:
        for w in t.lower().replace("/", " ").replace("&", " ").split():
            if len(w) > 3:
                keywords.add(w)
    text = " ".join([
        item.get("title", ""), item.get("summary", ""),
        item.get("track", ""), item.get("source", ""),
    ]).lower()
    return sum(1 for kw in keywords if kw in text)

def top_k_items(items: list[dict], tracks: list[str], k: int = 40) -> list[dict]:
    if len(items) <= k:
        return items
    scored = sorted(items, key=lambda x: keyword_score(x, tracks), reverse=True)
    log(f"  Pre-filter: kept {k}/{len(items)} most relevant items")
    return scored[:k]

# ── Data fetchers ─────────────────────────────────────────────────────────────

def fetch_arxiv(query: str, max_results: int = 10) -> list[dict]:
    try:
        r    = requests.get("http://export.arxiv.org/api/query",
                            params={"search_query": query, "max_results": max_results,
                                    "sortBy": "submittedDate"}, timeout=30)
        root = ET.fromstring(r.text)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        return [{
            "title":   entry.find("atom:title",   ns).text.strip(),
            "summary": entry.find("atom:summary", ns).text.strip()[:500],
            "link":    entry.find("atom:id",      ns).text.strip(),
            "source":  "arxiv",
        } for entry in root.findall("atom:entry", ns)]
    except Exception as e:
        log(f"arXiv error: {e}")
        return []

def fetch_github_trending() -> list[dict]:
    try:
        r = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": "ai agent machine-learning", "sort": "stars",
                    "order": "desc", "per_page": 10},
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=30,
        )
        return [{
            "title":   x["full_name"],
            "summary": x.get("description", ""),
            "link":    x.get("html_url", ""),
            "source":  "github_trending",
            "stars":   x["stargazers_count"],
        } for x in r.json().get("items", [])]
    except Exception as e:
        log(f"GitHub trending error: {e}")
        return []

def fetch_github_releases(repos: list[str]) -> list[dict]:
    results = []
    for repo in repos:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{repo}/releases",
                params={"per_page": 2},
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=15,
            )
            if r.status_code == 200:
                for rel in r.json():
                    results.append({
                        "title":   f"{repo} {rel.get('tag_name', '')}",
                        "summary": (rel.get("body") or "")[:300],
                        "link":    rel.get("html_url", ""),
                        "source":  "github_release",
                    })
        except Exception as e:
            log(f"GitHub release error ({repo}): {e}")
    return results

def fetch_cve_recent() -> list[dict]:
    try:
        r = requests.get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params={"resultsPerPage": 10, "startIndex": 0},
            timeout=30,
        )
        return [{
            "title":   x["cve"]["id"],
            "summary": x["cve"]["descriptions"][0]["value"][:300] if x["cve"]["descriptions"] else "",
            "link":    f"https://nvd.nist.gov/vuln/detail/{x['cve']['id']}",
            "source":  "nvd_cve",
        } for x in r.json().get("vulnerabilities", [])]
    except Exception as e:
        log(f"CVE error: {e}")
        return []

def load_perf_log(perf_log_path: Path) -> list[dict]:
    if not perf_log_path.exists():
        return []
    entries = []
    with open(perf_log_path) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries[-50:]

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def extract_json(text: str) -> dict:
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text, i)
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("No JSON object found in response")

def ollama_chat(prompt: str, system: str = "") -> str:
    payload = {"model": LOCAL_MODEL,
               "messages": [{"role": "user", "content": prompt}], "stream": False}
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
    kwargs = {"model": CLAUDE_MODEL, "max_tokens": 4096,
              "messages": [{"role": "user", "content": prompt}]}
    if system:
        kwargs["system"] = system
    try:
        return client.messages.create(**kwargs).content[0].text
    except Exception as e:
        log(f"Claude error: {e}")
        return ""

# ── Ollama helpers (token savers) ─────────────────────────────────────────────

def ollama_enrich_findings(findings: list[dict], profile: dict) -> list[dict]:
    """Add a brief local context note to each finding before Claude sees them."""
    log("Phase 3a: Ollama pre-enrichment...")
    tracks_str = ", ".join(profile["tracks"])
    for finding in findings:
        prompt = (
            f"In 1-2 sentences, add technical context for a researcher focused on: {tracks_str}.\n"
            f"Title: {finding.get('title', '')}\n"
            f"Summary: {finding.get('summary', '')}\n"
            f"Response (plain text, 1-2 sentences only):"
        )
        ctx = ollama_chat(prompt)
        finding["ollama_context"] = ctx.strip()[:300] if ctx else ""
    return findings

def ollama_compress(scan: dict, reflect: dict) -> dict:
    """Compress scan+reflect with Ollama before sending to Claude in Phase 4."""
    prompt = (
        "Summarize these two research outputs in under 600 words total. "
        "Keep: priority track, top finding titles+scores, key observations, "
        "suggested improvement.\n\n"
        f"SCAN: {json.dumps(scan)[:2000]}\n"
        f"REFLECT: {json.dumps(reflect)[:800]}\n\n"
        'Return JSON: {"scan_summary": "...", "reflect_summary": "..."}'
    )
    result = ollama_chat(prompt)
    try:
        return extract_json(result)
    except Exception:
        return {"scan_summary":    json.dumps(scan)[:600],
                "reflect_summary": json.dumps(reflect)[:400]}

# ── ChromaDB helpers ──────────────────────────────────────────────────────────

def get_chroma_client():
    """Return a persistent ChromaDB client, or None if chromadb is not installed."""
    if not CHROMA_AVAILABLE:
        return None
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def _lesson_corpus(chroma):
    return chroma.get_or_create_collection(
        name="lesson_corpus",
        metadata={"hnsw:space": "cosine"},
    )


def _run_nodes(chroma):
    return chroma.get_or_create_collection(
        name="run_nodes",
        metadata={"hnsw:space": "cosine"},
    )


# ── UCB1 scoring ──────────────────────────────────────────────────────────────

def ucb1_score(times_selected: int, cumulative_value: float,
               total_runs: int, C: float) -> float:
    """UCB1 formula: mean_value + C * sqrt(ln(total_runs) / times_selected).
    Nodes never selected before return +inf so they are always tried first."""
    if times_selected == 0:
        return float("inf")
    mean = cumulative_value / times_selected
    return mean + C * math.sqrt(math.log(total_runs) / times_selected)


def select_context_parents(agent_name: str, k: int = 3,
                           disable_ucb1: bool = False,
                           C: float = UCB1_C_DEFAULT) -> list[dict]:
    """Score all past run nodes for *agent_name* via UCB1 and return the top-k.

    Each returned dict has: run_id, date_str, priority_track, tonight_score, summary.
    Falls back to chronological (most-recent-first) when --disable-ucb1 is set or
    when ChromaDB is unavailable.  Increments times_selected for chosen nodes.
    """
    chroma = get_chroma_client()
    if chroma is None:
        return []
    coll = _run_nodes(chroma)
    try:
        results = coll.get(where={"agent_name": agent_name},
                           include=["documents", "metadatas"])
    except Exception as exc:
        log(f"UCB1: ChromaDB query failed — {exc}")
        return []

    ids   = results.get("ids", [])
    docs  = results.get("documents", [])
    metas = results.get("metadatas", [])
    if not ids:
        return []

    node_map = {rid: (doc, meta) for rid, doc, meta in zip(ids, docs, metas)}
    total    = len(ids)

    if disable_ucb1:
        ordered = sorted(node_map.items(),
                         key=lambda kv: kv[1][1].get("date_str", ""),
                         reverse=True)
        selected_ids = [rid for rid, _ in ordered[:k]]
    else:
        scored = [
            (
                ucb1_score(int(meta.get("times_selected", 0)),
                           float(meta.get("cumulative_value", 0.0)),
                           total, C),
                rid,
            )
            for rid, (_, meta) in node_map.items()
        ]
        scored.sort(reverse=True, key=lambda x: x[0])
        selected_ids = [rid for _, rid in scored[:k]]

    parents = []
    for rid in selected_ids:
        doc, meta = node_map[rid]
        parents.append({
            "run_id":         rid,
            "date_str":       meta.get("date_str", ""),
            "priority_track": meta.get("priority_track", ""),
            "tonight_score":  int(meta.get("tonight_score", 0)),
            "summary":        doc,
        })
        # Increment times_selected immediately so concurrent runs see updated counts
        coll.update(
            ids=[rid],
            metadatas=[{**meta,
                        "times_selected": int(meta.get("times_selected", 0)) + 1}],
        )

    if parents:
        mode = "sequential" if disable_ucb1 else "UCB1"
        log(f"Context parents ({mode}): {[p['run_id'] for p in parents]}")
    return parents


def register_run_node(run_id: str, agent_name: str, date_str: str,
                      priority_track: str = ""):
    """Insert a skeleton run node before phases execute (stats start at zero)."""
    chroma = get_chroma_client()
    if chroma is None:
        return
    _run_nodes(chroma).upsert(
        ids=[run_id],
        documents=[""],
        metadatas=[{
            "agent_name":       agent_name,
            "date_str":         date_str,
            "priority_track":   priority_track,
            "times_selected":   0,
            "cumulative_value": 0.0,
            "tonight_score":    0,
        }],
    )


def update_run_node(run_id: str, tonight_score: int, summary: str):
    """After a run completes, write the final score and summary into run_nodes."""
    chroma = get_chroma_client()
    if chroma is None:
        return
    coll = _run_nodes(chroma)
    existing = coll.get(ids=[run_id], include=["metadatas"])
    if not existing["ids"]:
        return
    meta = existing["metadatas"][0]
    coll.update(
        ids=[run_id],
        documents=[summary],
        metadatas=[{
            **meta,
            "tonight_score":    tonight_score,
            "cumulative_value": float(meta.get("cumulative_value", 0.0)) + tonight_score,
        }],
    )
    log(f"Run node {run_id} updated: score={tonight_score}")


def store_lessons(lessons: list[dict], agent_name: str, run_id: str):
    """Upsert structured lessons into the lesson_corpus ChromaDB collection.

    Each lesson dict must contain at minimum a 'lesson' field.
    tags[] is stored as a comma-joined string (ChromaDB metadata is flat).
    """
    if not lessons:
        return
    chroma = get_chroma_client()
    if chroma is None:
        log("ChromaDB unavailable — lessons not persisted")
        return
    coll = _lesson_corpus(chroma)
    ids, documents, metadatas = [], [], []
    for i, lesson in enumerate(lessons):
        text = lesson.get("lesson", "").strip()
        if not text:
            continue
        tags = lesson.get("tags", [])
        ids.append(f"{agent_name}_{run_id}_{i}")
        documents.append(text)
        metadatas.append({
            "domain":        str(lesson.get("domain", "")),
            "confidence":    float(lesson.get("confidence", 0.5)),
            "source_run_id": str(lesson.get("source_run_id", run_id)),
            "tags":          ",".join(tags) if isinstance(tags, list) else str(tags),
            "agent_name":    agent_name,
        })
    if ids:
        coll.upsert(ids=ids, documents=documents, metadatas=metadatas)
        log(f"Stored {len(ids)} lesson(s) in lesson_corpus")


# ── Phase 1: Scan ─────────────────────────────────────────────────────────────

def phase_scan(profile: dict, agent_cfg: dict, seen_cache: dict) -> dict:
    log("Phase 1: Scanning sources...")

    all_items: list[dict] = []

    for query, n in profile["arxiv_queries"]:
        all_items.extend(fetch_arxiv(query, n))

    if profile.get("fetch_github_trending"):
        all_items.extend(fetch_github_trending())

    if profile.get("fetch_cves"):
        all_items.extend(fetch_cve_recent())

    watched = agent_cfg.get("github_repos", [])
    if watched:
        releases = fetch_github_releases(watched)
        log(f"  GitHub releases: {len(releases)} items from {len(watched)} repos")
        all_items.extend(releases)

    log(f"  Collected {len(all_items)} raw items")
    fresh    = filter_seen(all_items, seen_cache)
    filtered = top_k_items(fresh, profile["tracks"])

    prompt = f"""You are the scan phase of a nightly research agent.
Agent: {profile['name']}
Context: {profile['context']}
Tracks: {', '.join(profile['tracks'])}

Tonight's items ({len(filtered)} pre-scored for relevance):
{json.dumps(filtered, indent=2)[:8000]}

Tasks:
1. Score each item 1-10 for relevance to this agent's tracks
2. Pick tonight's TOP PRIORITY TRACK
3. Select the 5 most important, actionable findings
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
        log("Scan parse failed")
        return {"priority_track": profile["tracks"][0],
                "priority_reason": "parse error", "top_findings": []}

# ── Phase 2: Reflect ──────────────────────────────────────────────────────────

def phase_reflect(profile: dict, dirs: dict) -> dict:
    log("Phase 2: Reflecting on performance...")
    perf = load_perf_log(dirs["perf_log"])
    if not perf:
        return {"observations": ["No performance data yet."], "improvement_areas": []}

    prompt = f"""You are the reflection phase of a {profile['name']} nightly review.
Performance events:
{json.dumps(perf, indent=2)[:3000]}

Analyze:
1. What patterns appear in failures or escalations?
2. What tasks could use a cheaper/faster model?
3. One concrete process improvement for tomorrow?

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

# ── Phase 3: Deep Research ────────────────────────────────────────────────────

def phase_deep_research(profile: dict, scan_results: dict,
                        parent_context: str = "") -> dict:
    log("Phase 3: Deep research via Claude...")
    findings = scan_results.get("top_findings", [])
    if not findings:
        log("  No findings, skipping Phase 3")
        return {"research": [], "synthesis": ""}

    enriched = ollama_enrich_findings(findings, profile)
    priority = scan_results.get("priority_track", "AI/ML")  # noqa: F841

    log("Phase 3b: Claude deep synthesis...")
    parent_block = (
        f"\nPrior-run context (UCB1-selected parents):\n{parent_context}\n"
        if parent_context else ""
    )
    prompt = f"""You are the deep research phase of a {profile['name']}.
Tracks: {', '.join(profile['tracks'])}
Context: {profile['context']}
{parent_block}
Top findings (each has a local pre-research context note):
{json.dumps(enriched, indent=2)[:5000]}

For each finding:
1. Expand on what it means and why it matters for these tracks
2. Identify direct applicability to current tools/workflows
3. Flag any suggested changes with specifics
4. Note cross-connections between findings

Return JSON:
{{
  "research": [
    {{
      "title": "...",
      "deep_summary": "...",
      "applicability": "high|medium|low",
      "applicable_to": ["..."],
      "suggests_change": true,
      "change_description": "..."
    }}
  ],
  "synthesis": "..."
}}"""

    result = claude_chat(prompt)
    try:
        return extract_json(result)
    except Exception:
        log("Deep research parse failed")
        return {"research": [], "synthesis": ""}

# ── Phase 4: Judge + Stage ────────────────────────────────────────────────────

def phase_judge_and_stage(profile: dict, scan: dict, reflect: dict, research: dict) -> dict:
    log("Phase 4: Judging and staging changes...")

    compressed = ollama_compress(scan, reflect)

    prompt = f"""You are the judgment phase of a {profile['name']}.

Scan summary: {compressed.get('scan_summary', '')}
Reflection summary: {compressed.get('reflect_summary', '')}
Deep research: {json.dumps(research, indent=2)[:3000]}

Decide what actions to stage:
- risk: low   → safe to auto-apply at 4 AM (docs, config tweaks, model pulls)
- risk: medium → stage for human review (workflow changes, integrations)
- risk: high  → stage with notes, never auto-apply (live system changes)

Provide a rollback_command for each action.

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

# ── Phase 5: Lesson Extraction ────────────────────────────────────────────────

def phase_extract_lessons(judge: dict, run_id: str, agent_name: str) -> list[dict]:
    """Prompt Claude (Sonnet) to distill structured lessons from the Judge output.

    Returns a list of dicts conforming to:
        {lesson, domain, confidence, source_run_id, tags[]}
    These are later stored in the lesson_corpus ChromaDB collection.
    """
    log("Phase 5: Extracting structured lessons via Claude...")
    summary       = judge.get("summary", "")
    staged        = judge.get("staged_actions", [])
    tonight_score = judge.get("tonight_score", 0)

    prompt = f"""You are a lesson-extraction system for a nightly AI research agent.

Run ID: {run_id}
Agent: {agent_name}
Tonight's score: {tonight_score}/10

Judge summary:
{summary}

Staged actions (title + description):
{json.dumps([{{"title": a.get("title", ""), "description": a.get("description", "")}}
             for a in staged], indent=2)}

Extract 3-7 concise, generalizable lessons from this run. Each lesson should be an
actionable insight that could improve future runs of this or similar agents.

Return a JSON array only — no prose before or after:
[
  {{
    "lesson": "one-sentence actionable insight",
    "domain": "category such as: tooling, workflow, research, security, llm-routing",
    "confidence": 0.85,
    "source_run_id": "{run_id}",
    "tags": ["tag1", "tag2"]
  }}
]"""

    result = claude_chat(prompt)

    # Prefer a bare JSON array; fall back to object wrapping a list
    try:
        start = result.find("[")
        end   = result.rfind("]")
        if start != -1 and end != -1:
            arr = json.loads(result[start:end + 1])
            if isinstance(arr, list):
                return arr
    except Exception:
        pass
    try:
        obj = extract_json(result)
        for v in obj.values():
            if isinstance(v, list):
                return v
    except Exception:
        pass

    log("Lesson extraction parse failed — no lessons stored")
    return []


# ── Write Staged Files ────────────────────────────────────────────────────────

def write_staging(judge: dict, date_str: str, dirs: dict) -> list:
    staging_dir = dirs["staging_dir"]
    actions     = judge.get("staged_actions", [])
    manifest    = []
    for i, action in enumerate(actions):
        risk  = action.get("risk", "high")
        fname = f"{date_str}_{i:02d}_{action.get('action_type', 'change')}_{risk}.staged"
        fpath = staging_dir / fname
        with open(fpath, "w") as f:
            json.dump(action, f, indent=2)
        manifest.append({"file": str(fpath), "risk": risk, "title": action.get("title", "")})
        log(f"  Staged [{risk}]: {action.get('title', '')}")
    with open(staging_dir / f"{date_str}_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest

# ── Write Changelog ───────────────────────────────────────────────────────────

def write_changelog(date_str: str, agent_name: str, profile: dict,
                    scan: dict, reflect: dict, research: dict,
                    judge: dict, manifest: list, dirs: dict) -> str:
    changelog = dirs["logs_dir"] / f"{date_str}-changelog.md"
    lines = [
        f"# Dream Cycle — {profile['name']} — {date_str}",
        f"\n**Priority Track:** {scan.get('priority_track', '?')}  ",
        f"**Reason:** {scan.get('priority_reason', '')}  ",
        f"**Score:** {judge.get('tonight_score', '?')}/10\n",
        "---\n", "## Phase 1 — Top Findings\n",
    ]
    for item in scan.get("top_findings", []):
        lines.append(f"- **{item.get('title', '')}** `[{item.get('track', '')}]` score:{item.get('score', '?')}  ")
        lines.append(f"  {item.get('summary', '')}\n")

    lines += ["\n## Phase 2 — Reflection\n"]
    for obs in reflect.get("observations", []):
        lines.append(f"- {obs}")
    if reflect.get("suggested_improvement"):
        lines.append(f"\n**Improvement Suggestion:** {reflect['suggested_improvement']}\n")

    lines += ["\n## Phase 3 — Deep Research\n"]
    for r in research.get("research", []):
        lines.append(f"### {r.get('title', '')}")
        lines.append(f"**Applicability:** {r.get('applicability', '?')} | **Tracks:** {', '.join(r.get('applicable_to', []))}")
        lines.append(f"\n{r.get('deep_summary', '')}\n")
        if r.get("suggests_change"):
            lines.append(f"**Suggests change:** {r.get('change_description', '')}\n")

    lines += ["\n## Phase 4 — Staged Actions\n"]
    for m in manifest:
        lines.append(f"[{m['risk'].upper()}] {m['title']}")

    lines += [f"\n---\n*Generated by dream_cycle.py ({agent_name}) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    with open(changelog, "w") as f:
        f.write("\n".join(lines))
    log(f"Changelog: {changelog}")
    return str(changelog)

# ── Send Summary ──────────────────────────────────────────────────────────────

def send_gmail_summary(changelog_path: str, judge: dict, scan: dict, agent_name: str):
    subject = (
        f"Dream Cycle [{agent_name}] {datetime.now().strftime('%Y-%m-%d')} "
        f"— {scan.get('priority_track', '?')} | Score {judge.get('tonight_score', '?')}/10"
    ).replace("\n", " ")
    body  = judge.get("summary", "No summary generated.")
    body += f"\n\nFull changelog: {changelog_path}"
    body += f"\nStaged actions: {len(judge.get('staged_actions', []))}"

    to_addr = os.getenv("DREAM_CYCLE_EMAIL", "")
    if not to_addr:
        mail_path = Path(changelog_path).parent / f"{datetime.now().strftime('%Y-%m-%d')}.mail"
        with open(mail_path, "w") as f:
            f.write(f"Subject: {subject}\n\n{body}")
        log(f"No DREAM_CYCLE_EMAIL set — saved to {mail_path}")
        return
    try:
        proc = subprocess.run(["msmtp", "-t"],
                              input=f"To: {to_addr}\nSubject: {subject}\n\n{body}",
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            log("Email sent via msmtp")
            return
    except FileNotFoundError:
        pass
    mail_path = Path(changelog_path).parent / f"{datetime.now().strftime('%Y-%m-%d')}.mail"
    with open(mail_path, "w") as f:
        f.write(f"Subject: {subject}\n\n{body}")
    log(f"msmtp not configured — saved to {mail_path}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global LOCAL_MODEL

    parser = argparse.ArgumentParser(description="Dream Cycle — nightly research agent")
    parser.add_argument("--agent",       choices=list(AGENT_PROFILES.keys()),
                        help="Agent profile to run")
    parser.add_argument("--reconfigure", action="store_true",
                        help="Re-run setup for the selected agent")
    parser.add_argument("--list-agents", action="store_true",
                        help="List available agent profiles and exit")
    parser.add_argument("--disable-ucb1", action="store_true",
                        help="Fall back to sequential (most-recent-first) context-parent selection")
    args = parser.parse_args()

    if args.list_agents:
        print("\nAvailable agents:")
        for name, p in AGENT_PROFILES.items():
            print(f"  {name:15} — {p['name']}")
            print(f"               Tracks: {', '.join(p['tracks'][:3])}...")
        return

    agent_name = args.agent or "ai_research"
    profile    = AGENT_PROFILES[agent_name]
    dirs       = get_agent_dirs(agent_name)
    date_str   = datetime.now().strftime("%Y-%m-%d")
    run_id     = f"{agent_name}_{date_str}_{datetime.now().strftime('%H%M%S')}"

    log(f"=== Dream Cycle [{profile['name']}] — {date_str} (run={run_id}) ===")

    config = load_config()

    if not config.get("global", {}).get("local_model"):
        config.setdefault("global", {})["local_model"] = select_local_model()
        save_config(config)
    LOCAL_MODEL = config["global"]["local_model"]

    agent_cfg = get_agent_config(config, agent_name)
    if not agent_cfg or args.reconfigure:
        agent_cfg = configure_agent(agent_name, profile)
        config    = set_agent_config(config, agent_name, agent_cfg)
        save_config(config)

    ucb1_c = float(config.get("global", {}).get("ucb1_c", UCB1_C_DEFAULT))
    log(f"Model: {LOCAL_MODEL} | Repos watched: {len(agent_cfg.get('github_repos', []))} "
        f"| UCB1 C={ucb1_c}{' (disabled)' if args.disable_ucb1 else ''}")

    seen_cache = load_seen_cache(dirs["seen_cache"])

    # Register this run in ChromaDB before phases start; stats updated after completion
    register_run_node(run_id, agent_name, date_str)

    # UCB1 context-parent selection (or sequential fallback with --disable-ucb1)
    parents = select_context_parents(agent_name, k=3,
                                     disable_ucb1=args.disable_ucb1, C=ucb1_c)
    parent_context = "\n".join(
        f"[{p['date_str']} score={p['tonight_score']}/10 "
        f"track={p['priority_track']}]: {p['summary']}"
        for p in parents
    )

    scan     = phase_scan(profile, agent_cfg, seen_cache)
    save_seen_cache(dirs["seen_cache"], seen_cache)

    reflect  = phase_reflect(profile, dirs)
    research = phase_deep_research(profile, scan, parent_context)
    judge    = phase_judge_and_stage(profile, scan, reflect, research)

    # Lesson extraction (Claude/Sonnet) → lesson_corpus ChromaDB collection
    lessons  = phase_extract_lessons(judge, run_id, agent_name)
    store_lessons(lessons, agent_name, run_id)

    # Persist final score and summary; cumulative_value accumulates for UCB1
    update_run_node(run_id, judge.get("tonight_score", 0), judge.get("summary", ""))

    manifest = write_staging(judge, date_str, dirs)
    changelog_path = write_changelog(date_str, agent_name, profile,
                                     scan, reflect, research, judge, manifest, dirs)
    send_gmail_summary(changelog_path, judge, scan, agent_name)

    log(f"=== Complete. {len(manifest)} actions staged. ===")
    date_str = datetime.now().strftime("%Y-%m-%d")
    log(f"=== Dream Cycle Starting — {date_str} ===")

    # Load persisted model, or prompt once and save it
    config = load_config()
    if not config.get("local_model"):
        config["local_model"] = select_local_model()
        save_config(config)
    LOCAL_MODEL = config["local_model"]
    log(f"Local model: {LOCAL_MODEL}")

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
