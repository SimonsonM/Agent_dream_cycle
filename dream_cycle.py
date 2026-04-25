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
from pathlib import Path as _P
from dotenv import load_dotenv as _ld
_ld(_P(__file__).parent / '.env')

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
import platform

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

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

try:
    from plugin_system import PluginManager
    PLUGINS_AVAILABLE = True
except ImportError:
    PLUGINS_AVAILABLE = False

try:
    import jsonschema
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False

import random

# ── Injection-defense ─────────────────────────────────────────────────────────

_INJECTION_PATTERNS: list[re.Pattern] = [
    # XML/SGML role tags
    re.compile(r"<\s*/?\s*(system|user|assistant|human|ai|inst|s)\s*>", re.IGNORECASE),
    re.compile(r"<\s*!\s*--.*?--\s*>", re.DOTALL),
    # LLaMA / Mistral / Qwen chat-template tokens
    re.compile(r"\[INST\]|\[/INST\]|\[SYS\]|\[/SYS\]", re.IGNORECASE),
    re.compile(r"<<SYS>>|<</SYS>>", re.IGNORECASE),
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.IGNORECASE),
    re.compile(r"<\|system\|>|<\|user\|>|<\|assistant\|>", re.IGNORECASE),
    re.compile(r"<\|.*?\|>"),
    # Bracket / brace role markers
    re.compile(r"\[\s*(system|user|assistant|human|ai|prompt|context)\s*\]", re.IGNORECASE),
    re.compile(r"\{\s*(system|user|assistant|human|ai|prompt|context)\s*\}", re.IGNORECASE),
    # Jailbreak preambles
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an|the)\s+\w+", re.IGNORECASE),
    re.compile(r"act\s+as\s+(a|an|the)\s+\w+", re.IGNORECASE),
    re.compile(r"pretend\s+(you\s+are|to\s+be)\s+(a|an|the)", re.IGNORECASE),
    re.compile(r"\bdo\s+anything\s+now\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"\bdan\s+mode\b", re.IGNORECASE),
    re.compile(r"\bdeveloper\s+mode\b", re.IGNORECASE),
    # Prompt-section separators
    re.compile(r"###\s*(system|user|assistant|instruction)", re.IGNORECASE),
    re.compile(r"---\s*(system|user|assistant|instruction)", re.IGNORECASE),
    re.compile(r"===\s*(system|user|assistant|instruction)", re.IGNORECASE),
    # Override / injection tags
    re.compile(r"<\/?(prompt|context|instruction|override)>", re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    re.compile(r"\boverride\s+(instructions?|system)\b", re.IGNORECASE),
    # Template injection
    re.compile(r"\{\{.*?\}\}"),
    re.compile(r"%\{.*?\}%"),
]


def sanitize_llm_input(text: str, token_budget: int = 2000) -> str:
    """Strip prompt-injection patterns from external text before it enters a prompt.

    Applied to all externally-sourced content (arXiv abstracts, GitHub descriptions,
    CVE text) before embedding in any Qwen or Claude prompt.  Each call is capped at
    token_budget * 4 characters (default 8 000).
    """
    if not isinstance(text, str):
        return str(text)
    char_budget = min(token_budget * 4, 8000)
    cleaned = text
    hit = False
    for pattern in _INJECTION_PATTERNS:
        new = pattern.sub("", cleaned)
        if new != cleaned:
            hit = True
            cleaned = new
    if hit:
        print(f"[injection-defense] Stripped injection pattern(s) from input", flush=True)
    return cleaned[:char_budget].strip()


def sanitize_item(item: dict, token_budget: int = 2000) -> dict:
    """Sanitize all string fields in a data-item dict in-place."""
    return {
        k: (sanitize_llm_input(v, token_budget) if isinstance(v, str)
            else [sanitize_llm_input(e, token_budget) if isinstance(e, str) else e
                  for e in v] if isinstance(v, list)
            else v)
        for k, v in item.items()
    }


# ── Namespace isolation ────────────────────────────────────────────────────────

_SAFE_NS_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _enforce_namespace(namespace: str) -> str:
    """Validate *namespace*; raise ValueError if it fails the safe-identifier check.

    Must be called before every ChromaDB write that tags an entry with a namespace.
    """
    if not isinstance(namespace, str) or not _SAFE_NS_RE.match(namespace):
        raise ValueError(
            f"[namespace-isolation] Invalid namespace '{namespace}'. "
            r"Must match ^[a-z][a-z0-9_]{0,62}$"
        )
    return namespace


def _check_namespace_entry_integrity(
    entry_id: str,
    metadata: dict,
    expected_namespace: str,
) -> bool:
    """Return True only if a ChromaDB entry passes all integrity checks.

    Checks:
      1. Stored namespace tag matches the queried namespace.
      2. run_id / source_run_id contains no path-traversal characters.
      3. tonight_score, when present, is numeric and in [0, 10].

    Flagged entries are logged with the [namespace-isolation] FLAGGED prefix and
    excluded from retrieval — never silently merged.
    """
    stored_ns = metadata.get("namespace") or metadata.get("agent_name", "")
    if stored_ns and stored_ns != expected_namespace:
        print(
            f"[namespace-isolation] FLAGGED: entry '{entry_id}' has namespace "
            f"'{stored_ns}' but expected '{expected_namespace}' — excluded",
            flush=True,
        )
        return False

    run_id = str(metadata.get("source_run_id") or metadata.get("run_id") or entry_id)
    if any(c in run_id for c in ("/", "\\", "..", "\x00")):
        print(
            f"[namespace-isolation] FLAGGED: entry '{entry_id}' has unsafe "
            f"run_id '{run_id}' — excluded",
            flush=True,
        )
        return False

    score = metadata.get("tonight_score")
    if score is not None:
        try:
            if not (0.0 <= float(score) <= 10.0):
                raise ValueError
        except (TypeError, ValueError):
            print(
                f"[namespace-isolation] FLAGGED: entry '{entry_id}' has invalid "
                f"tonight_score '{score}' — excluded",
                flush=True,
            )
            return False

    return True


# ── Manifest validation ────────────────────────────────────────────────────────

MANIFEST_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "id":               {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,62}$"},
        "name":             {"type": "string", "minLength": 1, "maxLength": 100},
        "version":          {"type": "string"},
        "type":             {"type": "string",
                             "enum": ["research", "security", "marketing",
                                      "programming", "mcp"]},
        "memory_namespace": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,62}$"},
        "scan_targets":     {
            "type": "array",
            "items": {"type": "string",
                      "enum": ["arxiv", "github_trending", "cves", "github_releases"]},
            "minItems": 1,
        },
        "active":           {"type": "boolean"},
        "mcp_endpoint":     {"type": "string"},
        "checksum":         {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "token_budget":     {"type": "integer", "minimum": 100, "maximum": 8000},
    },
    "required": ["id", "name", "version", "type", "memory_namespace",
                 "scan_targets", "active"],
    "additionalProperties": False,
}


def _validate_mcp_endpoint(endpoint: str) -> bool:
    """Return True only if endpoint is empty or an http(s) URL with a non-empty host.

    Rejects: file://, javascript:, data:, bare paths, empty hosts.
    """
    if not endpoint:
        return True
    try:
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https"):
            print(
                f"[manifest-validation] Rejected mcp_endpoint scheme "
                f"'{parsed.scheme}': {endpoint}",
                flush=True,
            )
            return False
        if not parsed.hostname:
            print(
                f"[manifest-validation] Rejected mcp_endpoint with empty host: {endpoint}",
                flush=True,
            )
            return False
        return True
    except Exception as exc:
        print(f"[manifest-validation] mcp_endpoint parse error: {exc}", flush=True)
        return False


def _verify_manifest_checksum(manifest_file: Path, manifest: dict) -> bool:
    """Return True if no checksum field is present, or if SHA-256 matches the file."""
    checksum = manifest.get("checksum", "")
    if not checksum:
        return True
    actual = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
    if actual != checksum:
        print(
            f"[manifest-validation] Checksum mismatch for {manifest_file.name}: "
            f"expected {checksum}, got {actual}",
            flush=True,
        )
        return False
    return True


# ── Agent Manifest Discovery ────────────────────────────────────────────────
def get_agent_manifest_dirs() -> list[Path]:
    """Return all valid manifest directories for the current platform."""
    home = Path.home()
    system = platform.system()
    
    dirs = []
    
    if system == "Linux":
        dirs.append(home / ".dream_cycle" / "agents")
    elif system == "Darwin":  # macOS
        dirs.append(home / ".dream_cycle" / "agents")
        dirs.append(home / "Library" / "Application Support" / "dream_cycle" / "agents")
    elif system == "Windows":
        dirs.append(Path(os.getenv("APPDATA", "")) / "dream_cycle" / "agents")
        # Registry scanning would go here for a full implementation
    
    # Also check local project agents directory
    dirs.append(Path(__file__).parent / "agents")
    
    # Filter to only existing directories
    return [d for d in dirs if d.exists() and d.is_dir()]

def load_agent_manifests() -> dict:
    """Load and validate all agent manifests through the six-stage pipeline.

    Stage 1 — JSON parse
    Stage 2 — JSON Schema (additionalProperties: false, closed enum for scan_targets)
    Stage 3 — SHA-256 checksum (verified when 'checksum' field is present)
    Stage 4 — mcp_endpoint allow-list (http/https with non-empty host only)
    Stage 5 — active flag (inactive manifests skipped silently)
    Stage 6 — duplicate ID guard (first ID wins; collisions logged)

    All failures are non-fatal: bad manifests are logged and skipped.
    """
    agents: dict = {}
    manifest_dirs = get_agent_manifest_dirs()

    for manifest_dir in manifest_dirs:
        for manifest_file in manifest_dir.glob("*.json"):
            # Stage 1 — JSON parse
            try:
                with open(manifest_file) as f:
                    manifest = json.load(f)
            except (json.JSONDecodeError, IOError) as exc:
                print(f"[manifest-validation] {manifest_file.name}: JSON parse failed — {exc}",
                      flush=True)
                continue

            # Stage 2 — JSON Schema
            if JSONSCHEMA_AVAILABLE:
                try:
                    jsonschema.validate(instance=manifest, schema=MANIFEST_JSON_SCHEMA)
                except jsonschema.ValidationError as exc:
                    print(f"[manifest-validation] {manifest_file.name}: schema error — {exc.message}",
                          flush=True)
                    continue
            else:
                # Fallback: check required fields manually
                required = ["id", "name", "version", "type", "memory_namespace",
                            "scan_targets", "active"]
                missing = [f for f in required if f not in manifest]
                if missing:
                    print(f"[manifest-validation] {manifest_file.name}: missing fields {missing}",
                          flush=True)
                    continue

            # Stage 3 — Checksum
            if not _verify_manifest_checksum(manifest_file, manifest):
                continue

            # Stage 4 — mcp_endpoint allow-list
            if not _validate_mcp_endpoint(manifest.get("mcp_endpoint", "")):
                continue

            # Stage 5 — active flag
            if not manifest.get("active", False):
                continue

            # Stage 6 — duplicate ID guard
            agent_id = manifest["id"]
            if agent_id in agents:
                print(f"[manifest-validation] Duplicate agent id '{agent_id}' in "
                      f"{manifest_file.name} — first registration wins, skipping",
                      flush=True)
                continue

            agents[agent_id] = manifest

    return agents

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path.home() / "dream-cycle"
LOGS_DIR    = Path.home() / "dream-logs"
CONFIG_FILE = BASE_DIR / "config.json"

BASE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR = BASE_DIR / "chroma_db"

# ── Models ────────────────────────────────────────────────────────────────────
LOCAL_MODEL    = ""
CLAUDE_MODEL   = os.getenv("DREAM_CYCLE_FRONTIER_MODEL", "claude-opus-4-7")
client         = anthropic.Anthropic()
UCB1_C_DEFAULT = 1.4

# ── Routing config ────────────────────────────────────────────────────────────
# Escalate to frontier (Opus) only when local confidence is below this threshold
# OR an explicit force_frontier flag is set. Drives PULSE-style cost discipline.
LOCAL_CONFIDENCE_THRESHOLD = float(os.getenv("DREAM_CYCLE_LOCAL_CONF_THRESHOLD", "0.55"))
# When DREAM_CYCLE_LOCAL_ONLY=1, NO frontier calls are made even on escalation.
LOCAL_ONLY = os.getenv("DREAM_CYCLE_LOCAL_ONLY", "0") == "1"

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
        "github_trending_query": "security vulnerability exploit CVE detection",
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
        "github_trending_query": "marketing analytics growth SEO conversion",
        "context": (
            "Focus on conversion rate impact, SEO ranking signals, "
            "content distribution leverage, and MarTech integrations "
            "that reduce manual work."
        ),
    },
    "mcp": {
        "name": "MCP Server Intelligence Agent",
        "tracks": [
            "MCP Server Development", "Agent Memory Systems", 
            "Context Protocols", "Tool Integration", "LLM Agent Frameworks"
        ],
        "arxiv_queries": [
            ("model context protocol mcp llm agent", 8),
            ("agent memory storage retrieval augmentation", 6),
            ("context engineering prompt optimization", 5),
            ("tool use llm function calling", 5),
            ("multi-agent communication protocols", 4)
        ],
        "default_github_repos": [
            "modelcontextprotocol/python-sdk",
            "modelcontextprotocol/servers",
            "anthropics/anthropic-quickstarts",
            "mempalace/mempalace",
            "block/agents"
        ],
        "fetch_cves": False,
        "fetch_github_trending": True,
        "github_trending_query": "model context protocol MCP agent memory tool",
        "context": (
            "Focus on MCP specification implementations, agent memory systems, "
            "context protocol innovations, and tool integration patterns. "
            "Prioritize references with working examples and clear documentation."
        )
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
        "github_trending_query": "developer tooling language runtime devops infrastructure",
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
        "github_trending_query": "AI machine learning LLM agent transformer",
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

# config.yaml lives next to this script and carries defaults for new features.
YAML_CONFIG_FILE = Path(__file__).parent / "config.yaml"

def load_yaml_config() -> dict:
    """Load extended config from config.yaml; returns {} if pyyaml is absent."""
    if not YAML_AVAILABLE or not YAML_CONFIG_FILE.exists():
        return {}
    try:
        with open(YAML_CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        log(f"config.yaml load error: {e}")
        return {}

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
            cache[h] = now   # stamp/refresh so it won't be re-fetched next night
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

def _github_headers() -> dict:
    """Return GitHub API headers, including auth token if GITHUB_TOKEN is set."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def fetch_arxiv(query: str, max_results: int = 10) -> list[dict]:
    try:
        r    = requests.get("http://export.arxiv.org/api/query",
                            params={"search_query": query, "max_results": max_results,
                                    "sortBy": "submittedDate"}, timeout=30)
        root = ET.fromstring(r.text)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("atom:entry", ns):
            id_text  = entry.find("atom:id", ns).text.strip()
            arxiv_id = id_text.split("/abs/")[-1] if "/abs/" in id_text else id_text
            authors  = [
                a.find("atom:name", ns).text.strip()
                for a in entry.findall("atom:author", ns)
            ]
            results.append({
                "title":    entry.find("atom:title",   ns).text.strip(),
                "summary":  entry.find("atom:summary", ns).text.strip()[:500],
                "link":     id_text,
                "arxiv_id": arxiv_id,
                "authors":  authors[:5],   # cap at 5 to keep payload lean
                "source":   "arxiv",
            })
        return results
    except Exception as e:
        log(f"arXiv error: {e}")
        return []


def _arxiv_cache_path(agent_dir: Path, date_str: str, tag: str) -> Path:
    cache_dir = agent_dir / "arxiv_cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir / f"{date_str}_{tag.replace('.', '_')}.json"


def fetch_arxiv_by_categories(
    tags: list[str],
    results_per_tag: int,
    agent_dir: Path,
    date_str: str,
) -> list[dict]:
    """Fetch top papers per arXiv category tag with date-scoped caching.

    Called from phase_scan() (Qwen / local model phase).  Results are cached
    in <agent_dir>/arxiv_cache/<date>_<tag>.json so repeated runs on the same
    calendar day don't re-hit the API.
    """
    all_items: list[dict] = []
    for tag in tags:
        cache_path = _arxiv_cache_path(agent_dir, date_str, tag)
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    items = json.load(f)
                log(f"  arXiv cache hit: {tag} ({len(items)} papers)")
                all_items.extend(items)
                continue
            except Exception:
                pass
        items = fetch_arxiv(f"cat:{tag}", results_per_tag)
        for item in items:
            item["category_tag"] = tag
        try:
            with open(cache_path, "w") as f:
                json.dump(items, f)
        except Exception:
            pass
        log(f"  arXiv fetched: {tag} → {len(items)} papers")
        all_items.extend(items)
    return all_items

def fetch_github_trending(query: str = "ai agent machine-learning") -> list[dict]:
    try:
        r = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "stars", "order": "desc", "per_page": 10},
            headers=_github_headers(),
            timeout=30,
        )
        r.raise_for_status()
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
                headers=_github_headers(),
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


def load_applied_log(dirs: dict) -> list[dict]:
    """Load the applied_changes.jsonl written by build_job.py for this agent."""
    log_path = dirs["logs_dir"] / "applied_changes.jsonl"
    if not log_path.exists():
        return []
    entries = []
    with open(log_path) as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries[-20:]

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

def ollama_chat(prompt: str, system: str = "",
                _retries: int = 3, _retry_delay: float = 10.0) -> str:
    payload = {"model": LOCAL_MODEL,
               "messages": [{"role": "user", "content": prompt}], "stream": False}
    if system:
        payload["messages"].insert(0, {"role": "system", "content": system})
    last_err: Exception | None = None
    for attempt in range(_retries):
        try:
            r = requests.post("http://localhost:11434/api/chat", json=payload, timeout=120)
            r.raise_for_status()
            return r.json()["message"]["content"]
        except Exception as e:
            last_err = e
            if attempt < _retries - 1:
                log(f"Ollama error (attempt {attempt + 1}/{_retries}): {e} — retrying in {_retry_delay}s")
                time.sleep(_retry_delay)
    log(f"Ollama failed after {_retries} attempts: {last_err}")
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

# ── Reasoning router ──────────────────────────────────────────────────────────
# PULSE-style routing: local-first, frontier on escalation only.
# Every non-trivial reasoning call should go through reason() rather than
# claude_chat() directly. claude_chat() remains as the raw frontier client.

def _local_self_score(prompt: str, system: str = "") -> tuple[str, float]:
    """
    Run the prompt against Qwen and ask it to self-rate confidence.
    Returns (response_text, confidence in [0,1]).
    The confidence is parsed from a trailing '<<CONFIDENCE: 0.NN>>' marker
    that we ask the model to emit; if missing, default to 0.5 (neutral).
    """
    instrumented_system = (system + "\n\n" if system else "") + (
        "After your full response, on a NEW LINE, emit exactly:\n"
        "<<CONFIDENCE: X.YY>>\n"
        "where X.YY is your self-assessed confidence (0.00 = guessing, "
        "1.00 = certain) that your response is correct, complete, and "
        "well-formed for the task. Be honest; lower scores trigger expert review."
    )
    raw = ollama_chat(prompt, system=instrumented_system)
    if not raw:
        return "", 0.0
    m = re.search(r"<<\s*CONFIDENCE\s*:\s*([0-9]*\.?[0-9]+)\s*>>", raw)
    conf = 0.5
    if m:
        try:
            conf = max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            pass
    cleaned = re.sub(r"<<\s*CONFIDENCE\s*:.*?>>", "", raw).strip()
    return cleaned, conf

def reason(prompt: str, system: str = "", *,
           force_frontier: bool = False,
           force_local: bool = False,
           threshold: float | None = None) -> tuple[str, str]:
    """
    Route a reasoning task to local-first, frontier on escalation.

    Returns (response_text, tier_used) where tier_used is one of:
      "local" | "frontier" | "local_fallback"

    - force_frontier=True: skip local entirely (use for genuinely hard reasoning
      where you've already decided local can't do it)
    - force_local=True: never escalate (use for cheap summarization, lesson
      extraction, etc.)
    - threshold: override LOCAL_CONFIDENCE_THRESHOLD per-call
    """
    thr = threshold if threshold is not None else LOCAL_CONFIDENCE_THRESHOLD

    # Hard local-only mode (for offline / no-credit operation)
    if LOCAL_ONLY and not force_frontier:
        text, _ = _local_self_score(prompt, system)
        return text, "local"

    if force_frontier:
        result = claude_chat(prompt, system=system)
        if not result and not LOCAL_ONLY:
            log("Frontier failed; falling back to local")
            text, _ = _local_self_score(prompt, system)
            return text, "local_fallback"
        return result, "frontier"

    if force_local:
        text, _ = _local_self_score(prompt, system)
        return text, "local"

    # Standard path: local first, escalate on low confidence
    text, conf = _local_self_score(prompt, system)
    if conf >= thr and text:
        log(f"  reason: local (confidence={conf:.2f}, threshold={thr:.2f})")
        return text, "local"

    if LOCAL_ONLY:
        log(f"  reason: local-only mode, accepting low-confidence local "
            f"output (confidence={conf:.2f})")
        return text, "local"

    log(f"  reason: escalating to frontier (local confidence={conf:.2f} "
        f"< threshold={thr:.2f})")
    frontier_result = claude_chat(prompt, system=system)
    if frontier_result:
        return frontier_result, "frontier"
    log("  reason: frontier failed, returning low-confidence local output")
    return text, "local_fallback"

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


def _bridge_parent(
    agent_name: str,
    C: float,
    exclude_ids: set[str],
) -> dict | None:
    """Return the highest-UCB1 run node from a *different* agent (cross-domain bridge).

    Returns None when no cross-domain nodes exist or ChromaDB is unavailable.
    The returned dict has an extra 'bridge_domain' key with the source agent name.
    """
    chroma = get_chroma_client()
    if chroma is None:
        return None
    coll = _run_nodes(chroma)
    try:
        all_nodes = coll.get(include=["documents", "metadatas"])
    except Exception:
        return None

    ids   = all_nodes.get("ids", [])
    docs  = all_nodes.get("documents", [])
    metas = all_nodes.get("metadatas", [])
    if not ids:
        return None

    total      = len(ids)
    best_score = -float("inf")
    best_entry = None

    for rid, doc, meta in zip(ids, docs, metas):
        if meta.get("agent_name") == agent_name:
            continue          # same domain — skip
        if rid in exclude_ids:
            continue          # already selected
        score = ucb1_score(
            int(meta.get("times_selected", 0)),
            float(meta.get("cumulative_value", 0.0)),
            total, C,
        )
        if score > best_score:
            best_score = score
            best_entry = (rid, doc, meta)

    if best_entry is None:
        return None

    rid, doc, meta = best_entry
    return {
        "run_id":         rid,
        "date_str":       meta.get("date_str", ""),
        "priority_track": meta.get("priority_track", ""),
        "tonight_score":  int(meta.get("tonight_score", 0)),
        "summary":        doc,
        "bridge_domain":  meta.get("agent_name", ""),
    }


def select_context_parents(agent_name: str, k: int = 3,
                           disable_ucb1: bool = False,
                           C: float = UCB1_C_DEFAULT,
                           bridge_probability: float = 0.0) -> list[dict]:
    """Score all past run nodes for *agent_name* via UCB1 and return the top-k.

    Each returned dict has: run_id, date_str, priority_track, tonight_score, summary.
    Falls back to chronological (most-recent-first) when --disable-ucb1 is set or
    when ChromaDB is unavailable.  Increments times_selected for chosen nodes.

    When bridge_probability > 0 (and UCB1 is active), one parent slot is
    replaced with the highest-UCB1 node from a different agent domain.
    Bridge events are logged with the domain pair (Feature 4).
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

    # Integrity check — exclude entries that fail namespace or score validation
    node_map = {
        rid: (doc, meta)
        for rid, doc, meta in zip(ids, docs, metas)
        if _check_namespace_entry_integrity(rid, meta, agent_name)
    }
    if not node_map:
        return []
    total    = len(node_map)

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

    # ── Cross-domain bridge (Feature 4) ──────────────────────────────────────
    if (not disable_ucb1 and bridge_probability > 0.0
            and random.random() < bridge_probability and parents):
        bridge = _bridge_parent(agent_name, C, set(selected_ids))
        if bridge:
            replaced = parents.pop()   # drop lowest-scored same-domain parent
            parents.append(bridge)
            log(f"Bridge: {agent_name} ← {bridge['bridge_domain']}"
                f"  [run={bridge['run_id']}, track={bridge['priority_track']}]"
                f"  (replaced {replaced['run_id']})")
            # Track selection for bridge node
            b_chroma = get_chroma_client()
            if b_chroma:
                b_coll = _run_nodes(b_chroma)
                b_ex   = b_coll.get(ids=[bridge["run_id"]], include=["metadatas"])
                if b_ex["ids"]:
                    bm = b_ex["metadatas"][0]
                    b_coll.update(
                        ids=[bridge["run_id"]],
                        metadatas=[{**bm,
                                    "times_selected": int(bm.get("times_selected", 0)) + 1}],
                    )

    return parents


def register_run_node(run_id: str, agent_name: str, date_str: str,
                      priority_track: str = ""):
    """Insert a skeleton run node before phases execute (stats start at zero)."""
    try:
        _enforce_namespace(agent_name)
    except ValueError as exc:
        log(str(exc))
        return
    chroma = get_chroma_client()
    if chroma is None:
        return
    _run_nodes(chroma).upsert(
        ids=[run_id],
        documents=[""],
        metadatas=[{
            "agent_name":       agent_name,
            "namespace_owner":  agent_name,
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
    try:
        _enforce_namespace(agent_name)
    except ValueError as exc:
        log(str(exc))
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
        confidence = float(lesson.get("confidence", 0.5))
        metadatas.append({
            "domain":                str(lesson.get("domain", "")),
            "confidence":            confidence,
            "source_run_id":         str(lesson.get("source_run_id", run_id)),
            "tags":                  ",".join(tags) if isinstance(tags, list) else str(tags),
            "agent_name":            agent_name,
            "namespace_owner":       agent_name,   # provenance — enforced at write
            # ── contradiction metadata (Feature 2) ────────────────────────
            "contradiction":         bool(lesson.get("contradiction", False)),
            "conflicting_lesson_id": str(lesson.get("conflicting_lesson_id") or ""),
            "recommendation":        str(lesson.get("recommendation", "coexist")),
            # ── decay fields (Feature 3) ──────────────────────────────────
            "confidence_decay":      confidence,
            "selection_count":       0,
            "last_selected_run":     "",
            "dormant":               False,
            "superseded":            False,
        })
    if ids:
        coll.upsert(ids=ids, documents=documents, metadatas=metadatas)
        log(f"Stored {len(ids)} lesson(s) in lesson_corpus")


# ── Contradiction Detection (Feature 2) ──────────────────────────────────────

def detect_contradictions(
    new_lessons: list[dict],
    agent_name: str,
    priority_track: str,
) -> list[dict]:
    """Check new candidate lessons against the existing corpus for contradictions.

    Uses Claude Sonnet (per spec: Sonnet handles contradiction detection).
    Attaches contradiction metadata to each lesson dict in-place and returns
    the annotated list.  Safe to call when ChromaDB is unavailable — returns
    the list unchanged.
    """
    if not new_lessons:
        return new_lessons
    chroma = get_chroma_client()
    if chroma is None:
        return new_lessons
    coll = _lesson_corpus(chroma)
    try:
        existing = coll.get(
            where={"agent_name": agent_name},
            include=["documents", "metadatas"],
        )
    except Exception as exc:
        log(f"Contradiction check: corpus query failed — {exc}")
        return new_lessons

    existing_ids  = existing.get("ids", [])
    existing_docs = existing.get("documents", [])
    existing_meta = existing.get("metadatas", [])

    # Only compare against active (non-dormant, non-superseded) lessons
    active = [
        {"id": eid, "lesson": doc,
         "domain": meta.get("domain", ""),
         "confidence": meta.get("confidence", 0.5)}
        for eid, doc, meta in zip(existing_ids, existing_docs, existing_meta)
        if not meta.get("dormant") and not meta.get("superseded")
    ]
    if not active:
        return new_lessons

    candidates = [
        {"index": i, "lesson": l.get("lesson", ""), "domain": l.get("domain", "")}
        for i, l in enumerate(new_lessons)
    ]

    prompt = f"""You are a knowledge-consistency checker for a nightly AI research agent.

Current run domain context: {priority_track}
Agent: {agent_name}

Existing active lessons (from prior runs):
{json.dumps(active[:60], indent=2)[:3000]}

New candidate lessons to evaluate:
{json.dumps(candidates, indent=2)}

For each candidate, check whether it directly contradicts any existing lesson.
A contradiction means the two lessons give incompatible guidance on the same topic.

Return a JSON array — one entry per candidate, preserving index order:
[
  {{
    "candidate_index": 0,
    "contradiction": false,
    "conflicting_lesson_id": null,
    "description": "",
    "recommendation": "coexist"
  }}
]
recommendation must be exactly one of:
  "supersede" — new lesson is a better/updated version; mark old lesson superseded
  "coexist"   — no real conflict; keep both
  "discard"   — existing lesson is stronger; lower confidence on the new one
"""

    raw, _tier = reason(prompt, force_frontier=True)  # contradictions = genuine reasoning
    try:
        start = raw.find("[")
        end   = raw.rfind("]")
        flags = json.loads(raw[start:end + 1]) if start != -1 and end != -1 else []
    except Exception:
        log("Contradiction detection parse failed — skipping")
        return new_lessons

    flag_map = {f.get("candidate_index", i): f for i, f in enumerate(flags)}
    for i, lesson in enumerate(new_lessons):
        flag = flag_map.get(i, {})
        if flag.get("contradiction"):
            rec = flag.get("recommendation", "coexist")
            lesson["contradiction"]         = True
            lesson["conflicting_lesson_id"] = flag.get("conflicting_lesson_id")
            lesson["contradiction_desc"]    = flag.get("description", "")
            lesson["recommendation"]        = rec
            if rec == "discard":
                lesson["confidence"] = round(float(lesson.get("confidence", 0.5)) * 0.5, 4)
            log(f"  Contradiction [{rec}]: {lesson.get('lesson', '')[:70]}…")
        else:
            lesson.setdefault("contradiction", False)
            lesson.setdefault("recommendation", "coexist")
    return new_lessons


def mark_lesson_superseded(lesson_id: str):
    """Flag an existing lesson as superseded.

    The lesson is kept in the corpus but excluded from active retrieval.
    Called when contradiction detection returns recommendation='supersede'.
    """
    chroma = get_chroma_client()
    if chroma is None:
        return
    coll = _lesson_corpus(chroma)
    try:
        existing = coll.get(ids=[lesson_id], include=["metadatas"])
        if not existing["ids"]:
            log(f"  mark_lesson_superseded: id '{lesson_id}' not found")
            return
        meta = existing["metadatas"][0]
        coll.update(ids=[lesson_id], metadatas=[{**meta, "superseded": True}])
        log(f"  Lesson {lesson_id} marked superseded")
    except Exception as exc:
        log(f"  mark_lesson_superseded failed: {exc}")


# ── Lesson Decay (Feature 3) ──────────────────────────────────────────────────

def apply_lesson_decay(
    agent_name: str,
    selected_run_ids: list[str],
    current_run_id: str,
    decay_factor: float = 0.98,
    dormant_threshold: float = 0.15,
):
    """Post-run cleanup: decay confidence for lessons whose parent run was NOT selected.

    Called after the run completes — not inline with phases.
    Lessons whose decayed confidence drops below *dormant_threshold* are flagged
    dormant (excluded from active retrieval, retained in corpus).
    Lessons whose parent run WAS selected get their selection_count incremented.
    """
    chroma = get_chroma_client()
    if chroma is None:
        return
    coll = _lesson_corpus(chroma)
    try:
        results = coll.get(
            where={"agent_name": agent_name},
            include=["documents", "metadatas"],
        )
    except Exception as exc:
        log(f"Lesson decay: corpus query failed — {exc}")
        return

    ids   = results.get("ids", [])
    metas = results.get("metadatas", [])
    if not ids:
        return

    selected_set  = set(selected_run_ids) | {current_run_id}
    decayed_ids   = []
    decayed_metas = []
    dormant_count = 0

    for lid, meta in zip(ids, metas):
        if meta.get("superseded") or meta.get("dormant"):
            continue  # already inactive — skip
        src = meta.get("source_run_id", "")
        if src in selected_set:
            # Parent run was selected → refresh selection tracking
            coll.update(
                ids=[lid],
                metadatas=[{**meta,
                            "selection_count":   int(meta.get("selection_count", 0)) + 1,
                            "last_selected_run": current_run_id}],
            )
            continue
        # Parent run not selected → apply decay
        cur_conf = float(meta.get("confidence_decay", meta.get("confidence", 0.5)))
        new_conf = round(cur_conf * decay_factor, 6)
        new_meta = {**meta, "confidence_decay": new_conf}
        if new_conf < dormant_threshold:
            new_meta["dormant"] = True
            dormant_count += 1
        decayed_ids.append(lid)
        decayed_metas.append(new_meta)

    if decayed_ids:
        # Batch ChromaDB updates in chunks of 100
        for i in range(0, len(decayed_ids), 100):
            coll.update(
                ids=decayed_ids[i:i + 100],
                metadatas=decayed_metas[i:i + 100],
            )
        msg = f"Lesson decay: {len(decayed_ids)} lesson(s) decayed"
        if dormant_count:
            msg += f", {dormant_count} newly dormant"
        log(msg)


def audit_dormant_lessons(agent_name: str):
    """Print a review table of dormant lessons for *agent_name*.

    Invoked via: dream_cycle.py --audit-dormant [--agent NAME]
    """
    chroma = get_chroma_client()
    if chroma is None:
        print("ChromaDB unavailable — cannot audit dormant lessons")
        return
    coll = _lesson_corpus(chroma)
    try:
        results = coll.get(
            where={"agent_name": agent_name, "dormant": True},
            include=["documents", "metadatas"],
        )
    except Exception as exc:
        print(f"Audit failed: {exc}")
        return

    ids   = results.get("ids", [])
    docs  = results.get("documents", [])
    metas = results.get("metadatas", [])
    if not ids:
        print(f"No dormant lessons found for agent '{agent_name}'.")
        return

    print(f"\n{'─'*70}")
    print(f"  Dormant Lessons — {agent_name}  ({len(ids)} total)")
    print(f"{'─'*70}")
    for lid, doc, meta in zip(ids, docs, metas):
        status = "SUPERSEDED" if meta.get("superseded") else "dormant"
        print(f"\n  ID:        {lid}")
        print(f"  Lesson:    {doc[:110]}")
        print(f"  Domain:    {meta.get('domain', '?')}")
        print(f"  Decay:     {meta.get('confidence_decay', '?'):.4f}"
              f"  (orig {meta.get('confidence', '?'):.2f})")
        print(f"  Selected:  {meta.get('selection_count', 0)}×"
              f"  last={meta.get('last_selected_run', 'never')}")
        print(f"  Status:    {status}")
    print()


# ── Phase 1: Scan ─────────────────────────────────────────────────────────────

def phase_scan(profile: dict, agent_cfg: dict, seen_cache: dict,
               dirs: dict | None = None, date_str: str = "",
               yaml_cfg: dict | None = None) -> dict:
    log("Phase 1: Scanning sources...")

    all_items: list[dict] = []

    # Per-agent keyword queries (existing behaviour)
    for query, n in profile["arxiv_queries"]:
        all_items.extend(fetch_arxiv(query, n))

    # Category-tag scan — Qwen phase, cached per day (Feature 1)
    if dirs and date_str:
        arxiv_cfg    = (yaml_cfg or {}).get("arxiv", {})
        cat_tags     = arxiv_cfg.get("category_tags", ["cs.AI", "cs.LG", "cs.MA"])
        per_tag      = int(arxiv_cfg.get("results_per_tag", 5))
        cat_items    = fetch_arxiv_by_categories(
                           cat_tags, per_tag, dirs["agent_dir"], date_str)
        log(f"  arXiv categories ({', '.join(cat_tags)}): {len(cat_items)} papers")
        all_items.extend(cat_items)

    if profile.get("fetch_github_trending"):
        trending_query = profile.get(
            "github_trending_query", "ai agent machine-learning"
        )
        all_items.extend(fetch_github_trending(trending_query))

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

    # Sanitize externally-sourced content before it enters the Qwen prompt
    token_budget = 2000
    sanitized = [sanitize_item(item, token_budget) for item in filtered]

    prompt = f"""You are the scan phase of a nightly research agent.
Agent: {profile['name']}
Context: {profile['context']}
Tracks: {', '.join(profile['tracks'])}

Tonight's items ({len(sanitized)} pre-scored for relevance):
{json.dumps(sanitized, indent=2)[:8000]}

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
    perf    = load_perf_log(dirs["perf_log"])
    applied = load_applied_log(dirs)

    if not perf and not applied:
        return {"observations": ["No performance data yet."], "improvement_areas": []}

    applied_block = ""
    if applied:
        reverted = [e for e in applied if e.get("event") == "reverted"]
        applied_ok = [e for e in applied if e.get("event") == "applied"]
        applied_block = (
            f"\nRecent build-job outcomes ({len(applied_ok)} applied, "
            f"{len(reverted)} reverted):\n"
            f"{json.dumps(applied[-10:], indent=2)[:1000]}\n"
        )

    prompt = f"""You are the reflection phase of a {profile['name']} nightly review.
Performance events:
{json.dumps(perf, indent=2)[:2500]}
{applied_block}
Analyze:
1. What patterns appear in failures or escalations?
2. Which previously applied changes were reverted and why?
3. What tasks could use a cheaper/faster model?
4. One concrete process improvement for tomorrow?

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
    # Qwen output is untrusted — sanitize before sending to Claude
    enriched = [sanitize_item(item) for item in enriched]
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

    result, _tier = reason(prompt)  # synthesis: local-first, escalate on low confidence
    try:
        return extract_json(result)
    except Exception:
        log("Deep research parse failed")
        return {"research": [], "synthesis": ""}

# ── Phase 4: Judge + Stage ────────────────────────────────────────────────────

def phase_judge_and_stage(profile: dict, scan: dict, reflect: dict, research: dict, experimentation: dict = None) -> dict:
    log("Phase 4: Judging and staging changes...")

    compressed = ollama_compress(scan, reflect)

    experimentation_summary = ""
    if experimentation:
        validated_changes = experimentation.get("validated_changes", [])
        experiment_count = len(experimentation.get("experiments", []))
        validation_count = len(validated_changes)
        experimentation_summary = f"""

Experimentation Results ({validation_count}/{experiment_count} changes validated):
{json.dumps([{
    "title": vc["title"],
    "validation_score": vc["validation_score"],
    "experiment_type": vc["experiment_type"]
} for vc in validated_changes], indent=2)}

Validation notes: Only changes with validation score >= 0.7 are considered for staging."""
    else:
        experimentation_summary = "\nExperimentation: Skipped (--disable-experimentation flag used)"

    prompt = f"""You are the judgment phase of a {profile['name']}.

Scan summary: {compressed.get('scan_summary', '')}
Reflection summary: {compressed.get('reflect_summary', '')}
Deep research: {json.dumps(research, indent=2)[:3000]}
{experimentation_summary}

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
  ]
}}"""

    result, _tier = reason(prompt)  # judge: local-first, escalate on low confidence
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
{json.dumps([{"title": a.get("title", ""), "description": a.get("description", "")}
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

    result, _tier = reason(prompt, force_local=True)  # lesson extraction: summarization, never escalate

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


# ── Experimentation Phase ─────────────────────────────────────────────────────

def phase_experimentation(profile: dict, scan: dict, reflect: dict, research: dict, dirs: dict, date_str: str) -> dict:
    """Phase 3.5: Experimentation - validate proposed changes through safe testing.
    
    This phase takes the suggested changes from research and designs safe experiments
    to validate their effectiveness before committing to staging.
    """
    log("Phase 3.5: Experimentation via controlled validation...")
    
    # Extract suggested changes from research
    suggested_changes = []
    for finding in research.get("research", []):
        if finding.get("suggests_change", False):
            suggested_changes.append({
                "title": finding.get("title", ""),
                "description": finding.get("change_description", ""),
                "applicability": finding.get("applicability", "low"),
                "applicable_to": finding.get("applicable_to", []),
                "deep_summary": finding.get("deep_summary", "")
            })
    
    if not suggested_changes:
        log("  No suggested changes to experiment with")
        return {"experiments": [], "validation_results": {}, "validated_changes": []}
    
    # Design experiments for each suggested change
    experiments = []
    for change in suggested_changes:
        experiment = {
            "change_title": change["title"],
            "change_description": change.get("description", ""),
            "hypothesis": f"Implementing '{change['title']}' will improve performance in {change['applicable_to']}",
            "experiment_type": _determine_experiment_type(change),
            "success_metrics": _define_success_metrics(change, profile),
            "risk_level": "low",  # Experiments are designed to be safe
            "estimated_duration": "5-15 minutes",
            "rollback_plan": f"Revert to baseline if experiment shows negative impact"
        }
        experiments.append(experiment)
    
    validation_results = {}
    validated_changes = []

    for experiment in experiments:
        change_title = experiment["change_title"]
        log(f"  Running {experiment['experiment_type']} for: {change_title[:60]}")
        validation_score, metrics = _run_experiment(experiment, profile)
        validation_results[change_title] = {
            "score": validation_score,
            "passed": validation_score >= 0.7,
            "metrics": metrics,
            "recommendation": "proceed" if validation_score >= 0.7 else "reject",
        }

        if validation_score >= 0.7:
            validated_changes.append({
                "title": change_title,
                "description": experiment["change_description"],
                "validation_score": validation_score,
                "experiment_type": experiment["experiment_type"],
            })
    
    log(f"  Completed {len(experiments)} experiments, validated {len(validated_changes)} changes")
    
    return {
        "experiments": experiments,
        "validation_results": validation_results,
        "validated_changes": validated_changes
    }


def _determine_experiment_type(change: dict) -> str:
    """Determine what type of experiment to run for a change."""
    desc = change.get("description", "").lower()
    title = change.get("title", "").lower()
    
    if any(word in desc + title for word in ["model", "llm", "prompt"]):
        return "ab_test_prompt"  # A/B test different prompts
    elif any(word in desc + title for word in ["config", "parameter", "setting"]):
        return "config_validation"  # Validate config changes in sandbox
    elif any(word in desc + title for word in ["tool", "integration", "api"]):
        return "mock_integration"  # Test with mocked external services
    elif any(word in desc + title for word in ["algorithm", "approach", "method"]):
        return "algorithm_prototype"  # Build minimal prototype
    else:
        return "logic_validation"  # Validate reasoning/logic


def _define_success_metrics(change: dict, profile: dict) -> list[str]:
    """Define what metrics would indicate a successful experiment."""
    base_metrics = ["implementation_feasibility", "risk_assessment"]
    
    change_desc = change.get("description", "").lower()
    
    if any(word in change_desc for word in ["performance", "speed", "efficiency"]):
        base_metrics.append("performance_improvement")
    if any(word in change_desc for word in ["accuracy", "quality", "better"]):
        base_metrics.append("quality_improvement")
    if any(word in change_desc for word in ["cost", "expense", "token"]):
        base_metrics.append("cost_reduction")
    if any(word in change_desc for word in ["user", "experience", "interface"]):
        base_metrics.append("user_satisfaction")
        
    return base_metrics


def _run_ab_test_prompt(change: dict, profile: dict) -> tuple[float, dict]:
    """Test a proposed prompt/model change by evaluating it from two angles with Ollama.

    Returns (score 0-1, metrics dict).
    """
    context = f"Agent: {profile['name']}\nTracks: {', '.join(profile['tracks'])}"

    prompt_a = (
        f"{context}\n\n"
        f"Proposed change: {change.get('change_title', '')}\n"
        f"Description: {change.get('change_description', '')}\n\n"
        "Rate this change on three dimensions (1-10 each):\n"
        "  clarity — how clear and specific is the proposed change?\n"
        "  feasibility — how easily can it be implemented safely?\n"
        "  impact — how much will it improve research quality?\n"
        'Return JSON only: {"clarity": N, "feasibility": N, "impact": N, "reasoning": "..."}'
    )
    prompt_b = (
        f"{context}\n\n"
        f"Proposed change: {change.get('change_title', '')}\n"
        f"Description: {change.get('change_description', '')}\n\n"
        "Identify the top risks of this change and rate overall risk (1-10, higher = riskier).\n"
        'Return JSON only: {"risks": ["..."], "risk_score": N, "mitigatable": true}'
    )

    result_a = ollama_chat(prompt_a)
    result_b = ollama_chat(prompt_b)

    score = 0.5
    metrics: dict = {}
    try:
        a = extract_json(result_a)
        avg_pos = (
            float(a.get("clarity", 5)) +
            float(a.get("feasibility", 5)) +
            float(a.get("impact", 5))
        ) / 30.0
        score += avg_pos * 0.35
        metrics.update({"clarity": a.get("clarity"), "feasibility": a.get("feasibility"),
                        "impact": a.get("impact"), "reasoning_a": a.get("reasoning", "")})
    except Exception:
        pass
    try:
        b = extract_json(result_b)
        risk_penalty = (float(b.get("risk_score", 5)) / 10.0) * 0.25
        score -= risk_penalty
        if b.get("mitigatable"):
            score += 0.05
        metrics.update({"risk_score": b.get("risk_score"), "mitigatable": b.get("mitigatable"),
                        "risks": b.get("risks", [])})
    except Exception:
        pass

    return max(0.0, min(1.0, score)), metrics


def _run_config_validation(change: dict) -> tuple[float, dict]:
    """Write proposed config content to a temp file and attempt to parse it.

    Returns (score 0-1, metrics dict).
    """
    content = change.get("change_description", "")
    metrics: dict = {"method": "config_parse"}

    # Try JSON parse
    try:
        json.loads(content)
        metrics["parse_result"] = "valid_json"
        return 0.9, metrics
    except (json.JSONDecodeError, ValueError):
        pass

    # Try YAML parse
    if YAML_AVAILABLE:
        try:
            yaml.safe_load(content)
            metrics["parse_result"] = "valid_yaml"
            return 0.85, metrics
        except Exception:
            pass

    # Try writing to temp file (checks for encoding/path issues at minimum)
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=True) as f:
            f.write(content)
        metrics["parse_result"] = "writable_text"
        return 0.55, metrics
    except Exception as e:
        metrics["parse_result"] = f"write_failed: {e}"
        return 0.2, metrics


def _run_mock_integration(change: dict) -> tuple[float, dict]:
    """Check reachability of any URLs referenced in the proposed change.

    Returns (score 0-1, metrics dict).
    """
    text = change.get("change_description", "") + " " + change.get("change_title", "")
    urls = re.findall(r"https?://[^\s<>\"{}|\\^`\[\]]+", text)
    metrics: dict = {"urls_found": len(urls)}

    if not urls:
        return 0.6, metrics   # No URLs to test — neutral pass

    reachable = 0
    for url in urls[:3]:
        try:
            r = requests.head(url, timeout=5, allow_redirects=True)
            if r.status_code < 500:
                reachable += 1
        except Exception:
            pass

    metrics["urls_reachable"] = reachable
    score = 0.4 + (reachable / len(urls[:3])) * 0.5
    return score, metrics


def _run_logic_validation(change: dict, profile: dict) -> tuple[float, dict]:
    """Ask Ollama to evaluate the feasibility and logical soundness of a change.

    Returns (score 0-1, metrics dict).
    """
    prompt = (
        f"You are evaluating a proposed change for a {profile['name']}.\n\n"
        f"Change: {change.get('change_title', '')}\n"
        f"Description: {change.get('change_description', '')}\n\n"
        "Is this change logically sound, safe to implement, and likely to improve "
        "the agent's research quality?\n"
        "Rate 1-10 and give a brief explanation.\n"
        'Return JSON only: {"score": N, "feasible": true, "reasoning": "..."}'
    )
    result = ollama_chat(prompt)
    metrics: dict = {}
    try:
        obj = extract_json(result)
        raw = float(obj.get("score", 5))
        score = raw / 10.0
        if not obj.get("feasible", True):
            score *= 0.6
        metrics = {"ollama_score": raw, "feasible": obj.get("feasible"),
                   "reasoning": obj.get("reasoning", "")}
        return max(0.0, min(1.0, score)), metrics
    except Exception:
        return 0.5, {"error": "parse_failed"}


def _run_experiment(experiment: dict, profile: dict) -> tuple[float, dict]:
    """Dispatch to the appropriate real experiment runner based on experiment_type."""
    etype = experiment.get("experiment_type", "logic_validation")
    if etype == "ab_test_prompt":
        return _run_ab_test_prompt(experiment, profile)
    elif etype == "config_validation":
        return _run_config_validation(experiment)
    elif etype == "mock_integration":
        return _run_mock_integration(experiment)
    else:
        return _run_logic_validation(experiment, profile)


# ── Write Staged Files ────────────────────────────────────────────────────────

def write_staging(judge: dict, date_str: str, dirs: dict,
                  dry_run: bool = False) -> list:
    staging_dir = dirs["staging_dir"]
    actions     = judge.get("staged_actions", [])
    manifest    = []
    for i, action in enumerate(actions):
        risk  = action.get("risk", "high")
        fname = f"{date_str}_{i:02d}_{action.get('action_type', 'change')}_{risk}.staged"
        fpath = staging_dir / fname
        if dry_run:
            log(f"  [DRY RUN] Would stage [{risk}]: {action.get('title', '')}")
        else:
            with open(fpath, "w") as f:
                json.dump(action, f, indent=2)
            log(f"  Staged [{risk}]: {action.get('title', '')}")
        manifest.append({"file": str(fpath), "risk": risk, "title": action.get("title", "")})
    if not dry_run:
        with open(staging_dir / f"{date_str}_manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)
    return manifest

# ── Write Changelog ───────────────────────────────────────────────────────────

def write_changelog(date_str: str, agent_name: str, profile: dict,
                    scan: dict, reflect: dict, research: dict,
                    judge: dict, manifest: list, dirs: dict,
                    experimentation: dict = None,
                    dry_run: bool = False) -> str:
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
    
    lines += ["\n## Phase 3.5 — Experimentation\n"]
    if experimentation:
        experiments = experimentation.get("experiments", [])
        validation_results = experimentation.get("validation_results", {})
        validated_changes = experimentation.get("validated_changes", [])
        
        lines.append(f"Ran {len(experiments)} experiments, validated {len(validated_changes)} changes.\n")
        
        if validated_changes:
            lines.append("**Validated Changes:**\n")
            for change in validated_changes:
                lines.append(f"- {change['title']} (validation score: {change['validation_score']:.2f})")
                lines.append(f"  Experiment type: {change['experiment_type']}\n")
        else:
            lines.append("No changes passed validation threshold.\n")
            
        # Show some experiment details
        if experiments:
            lines.append("**Experiment Details:**\n")
            for exp in experiments[:3]:  # Show first 3 experiments
                lines.append(f"- {exp['change_title']}: {exp['experiment_type']}")
                validation = validation_results.get(exp['change_title'], {})
                if validation:
                    lines.append(f"  Result: {'PASS' if validation.get('passed') else 'FAIL'} (score: {validation.get('score', 0):.2f})")
                lines.append("")
    else:
        lines.append("Experimentation phase skipped (--disable-experimentation flag used)\n")

    lines += ["\n## Phase 4 — Staged Actions\n"]
    for m in manifest:
        lines.append(f"[{m['risk'].upper()}] {m['title']}")

    lines += [f"\n---\n*Generated by dream_cycle.py ({agent_name}) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    if dry_run:
        log(f"[DRY RUN] Would write changelog: {changelog}")
    else:
        with open(changelog, "w") as f:
            f.write("\n".join(lines))
        log(f"Changelog: {changelog}")
    return str(changelog)

# ── Send Summary ──────────────────────────────────────────────────────────────

def send_gmail_summary(changelog_path: str, judge: dict, scan: dict, agent_name: str,
                       dry_run: bool = False):
    if dry_run:
        log("[DRY RUN] Would send summary email")
        return
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

# ── Status display ────────────────────────────────────────────────────────────

def show_status(agent_name: str | None = None, n: int = 10):
    """Print a compact table of the last N runs from ChromaDB (or changelog files)."""
    chroma = get_chroma_client()
    rows: list[dict] = []

    if chroma:
        coll = _run_nodes(chroma)
        try:
            if agent_name:
                results = coll.get(
                    where={"agent_name": agent_name},
                    include=["documents", "metadatas"],
                )
            else:
                results = coll.get(include=["documents", "metadatas"])
            ids   = results.get("ids", [])
            metas = results.get("metadatas", [])
            for rid, meta in zip(ids, metas):
                rows.append({
                    "run_id":    rid,
                    "agent":     meta.get("agent_name", "?"),
                    "date":      meta.get("date_str", "?"),
                    "track":     meta.get("priority_track", "?")[:30],
                    "score":     meta.get("tonight_score", "?"),
                    "selected":  meta.get("times_selected", 0),
                })
        except Exception as exc:
            log(f"ChromaDB status query failed: {exc}")

    if not rows:
        # Fallback: scan changelog files
        for agent_dir in sorted(BASE_DIR.iterdir()):
            if not agent_dir.is_dir():
                continue
            if agent_name and agent_dir.name != agent_name:
                continue
            logs_dir = agent_dir / "logs"
            if not logs_dir.exists():
                continue
            for cl in sorted(logs_dir.glob("*-changelog.md"), reverse=True)[:n]:
                rows.append({
                    "run_id":   cl.stem,
                    "agent":    agent_dir.name,
                    "date":     cl.stem[:10],
                    "track":    "—",
                    "score":    "—",
                    "selected": "—",
                })

    if not rows:
        print("No run history found.")
        return

    rows.sort(key=lambda r: (r["date"], r["run_id"]), reverse=True)
    rows = rows[:n]

    print(f"\n{'Date':<12} {'Agent':<15} {'Score':>5}  {'Sel':>4}  {'Track'}")
    print("─" * 70)
    for r in rows:
        print(f"{r['date']:<12} {r['agent']:<15} {str(r['score']):>5}  "
              f"{str(r['selected']):>4}  {r['track']}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global LOCAL_MODEL

    parser = argparse.ArgumentParser(description="Dream Cycle — nightly research agent")
    parser.add_argument("--agent",
                        help="Agent profile to run (built-in or manifest agent id)")
    parser.add_argument("--reconfigure", action="store_true",
                        help="Re-run setup for the selected agent")
    parser.add_argument("--list-agents", action="store_true",
                        help="List available agent profiles and exit")
    parser.add_argument("--disable-ucb1", action="store_true",
                        help="Fall back to sequential (most-recent-first) context-parent selection")
    parser.add_argument("--disable-experimentation", action="store_true",
                        help="Skip the experimentation phase and go directly from research to judge")
    parser.add_argument("--audit-dormant", action="store_true",
                        help="Review dormant lessons for the selected agent and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run all phases but write no files and send no email")
    parser.add_argument("--status", action="store_true",
                        help="Print a summary table of recent runs and exit")
    parser.add_argument("--status-n", type=int, default=10, metavar="N",
                        help="Number of runs to show with --status (default: 10)")
    args = parser.parse_args()

    if args.status:
        show_status(agent_name=args.agent, n=args.status_n)
        return

    if args.list_agents:
        print("\nAvailable agents:")
        print("  Built-in profiles:")
        for name, p in AGENT_PROFILES.items():
            print(f"    {name:15} — {p['name']}")
        print("  Manifest agents:")
        discovered_agents = load_agent_manifests()
        if discovered_agents:
            for agent_id, manifest in discovered_agents.items():
                print(f"    {agent_id:15} — {manifest['name']} (v{manifest['version']})")
        else:
            print("    (none)")
        if PLUGINS_AVAILABLE:
            pm = PluginManager()
            pm.load_plugins()
            plugin_names = pm.list_agent_plugins()
            print("  Plugin agents:")
            if plugin_names:
                for pname in plugin_names:
                    p = pm.get_agent_plugin(pname)
                    print(f"    {pname:15} — {p.get_description() if p else ''}")
            else:
                print("    (none)")
        return

    def _manifest_to_profile(manifest: dict) -> dict:
        return {
            "name":                   manifest["name"],
            "tracks":                 [f"Custom: {manifest['type']}"],
            "arxiv_queries":          [],
            "default_github_repos":   [],
            "fetch_cves":             False,
            "fetch_github_trending":  False,
            "github_trending_query":  manifest.get("type", "ai agent machine-learning"),
            "context": f"Custom agent targeting: {', '.join(manifest['scan_targets'][:3])}...",
        }

    def _plugin_to_profile(plugin) -> dict:
        tracks = [t.get_name() for t in plugin.get_research_tracks()]
        arxiv_queries = []
        repos = []
        for track in plugin.get_research_tracks():
            for q in track.get_arxiv_queries():
                arxiv_queries.append((q.get("query", ""), q.get("max_results", 5)))
            repos.extend(track.get_github_repos())
        return {
            "name":                  plugin.get_agent_name(),
            "tracks":                tracks or ["General Research"],
            "arxiv_queries":         arxiv_queries,
            "default_github_repos":  repos,
            "fetch_cves":            any(t.get_cves_enabled() for t in plugin.get_research_tracks()),
            "fetch_github_trending": any(t.get_github_trending_enabled() for t in plugin.get_research_tracks()),
            "github_trending_query": plugin.get_agent_name(),
            "context":               plugin.get_description(),
        }

    # Determine which agent to run: CLI argument takes precedence, then default
    if args.agent:
        agent_name = args.agent
        if agent_name in AGENT_PROFILES:
            profile = AGENT_PROFILES[agent_name]
        else:
            discovered_agents = load_agent_manifests()
            if agent_name in discovered_agents:
                profile = _manifest_to_profile(discovered_agents[agent_name])
            elif PLUGINS_AVAILABLE:
                pm = PluginManager()
                pm.load_plugins()
                plugin = pm.get_agent_plugin(agent_name)
                if plugin:
                    profile = _plugin_to_profile(plugin)
                else:
                    print(f"Error: Unknown agent '{agent_name}'. Use --list-agents to see available agents.")
                    return
            else:
                print(f"Error: Unknown agent '{agent_name}'. Use --list-agents to see available agents.")
                return
    else:
        # No agent specified — check manifest agents, then plugins, then fall back to default
        discovered_agents = load_agent_manifests()
        if discovered_agents:
            agent_id, manifest = next(iter(discovered_agents.items()))
            agent_name = agent_id
            profile = _manifest_to_profile(manifest)
        elif PLUGINS_AVAILABLE:
            pm = PluginManager()
            pm.load_plugins()
            plugin_names = pm.list_agent_plugins()
            if plugin_names:
                agent_name = plugin_names[0]
                profile = _plugin_to_profile(pm.get_agent_plugin(agent_name))
            else:
                agent_name = "ai_research"
                profile = AGENT_PROFILES[agent_name]
        else:
            agent_name = "ai_research"
            profile = AGENT_PROFILES[agent_name]
    dirs       = get_agent_dirs(agent_name)
    date_str   = datetime.now().strftime("%Y-%m-%d")
    run_id     = f"{agent_name}_{date_str}_{datetime.now().strftime('%H%M%S')}"

    # --audit-dormant: review dormant lessons and exit (no run needed)
    if args.audit_dormant:
        audit_dormant_lessons(agent_name)
        return

    dry_run = args.dry_run
    log(f"=== Dream Cycle [{profile['name']}] — {date_str} (run={run_id})"
        f"{' [DRY RUN]' if dry_run else ''} ===")

    config   = load_config()
    yaml_cfg = load_yaml_config()

    if not config.get("global", {}).get("local_model"):
        config.setdefault("global", {})["local_model"] = select_local_model()
        save_config(config)
    LOCAL_MODEL = config["global"]["local_model"]

    agent_cfg = get_agent_config(config, agent_name)
    if not agent_cfg or args.reconfigure:
        agent_cfg = configure_agent(agent_name, profile)
        config    = set_agent_config(config, agent_name, agent_cfg)
        save_config(config)

    ucb1_c      = float(config.get("global", {}).get("ucb1_c", UCB1_C_DEFAULT))
    bridge_prob = float(
        yaml_cfg.get("cross_domain_bridge", {}).get("bridge_probability", 0.20)
    )
    log(f"Model: {LOCAL_MODEL} | Repos watched: {len(agent_cfg.get('github_repos', []))} "
        f"| UCB1 C={ucb1_c}{' (disabled)' if args.disable_ucb1 else ''}"
        f" | bridge_p={bridge_prob}")
    log(f"Routing: frontier={CLAUDE_MODEL} | local_threshold={LOCAL_CONFIDENCE_THRESHOLD} "
        f"| LOCAL_ONLY={LOCAL_ONLY}")

    seen_cache = load_seen_cache(dirs["seen_cache"])

    # Register this run in ChromaDB before phases start; stats updated after completion
    register_run_node(run_id, agent_name, date_str)

    # UCB1 context-parent selection (or sequential fallback with --disable-ucb1)
    # bridge_probability injects one cross-domain parent slot (Feature 4)
    parents = select_context_parents(agent_name, k=3,
                                     disable_ucb1=args.disable_ucb1, C=ucb1_c,
                                     bridge_probability=bridge_prob)
    parent_context = "\n".join(
        f"[{p['date_str']} score={p['tonight_score']}/10 "
        f"track={p['priority_track']}"
        + (f" bridge_from={p['bridge_domain']}" if p.get("bridge_domain") else "")
        + f"]: {p['summary']}"
        for p in parents
    )

    # Phase 1: Scan (Qwen) — now includes arXiv category fetch with date cache
    scan = phase_scan(profile, agent_cfg, seen_cache,
                      dirs=dirs, date_str=date_str, yaml_cfg=yaml_cfg)
    save_seen_cache(dirs["seen_cache"], seen_cache)

    reflect  = phase_reflect(profile, dirs)
    research = phase_deep_research(profile, scan, parent_context)
    # Phase 3.5: Experimentation - validate hypotheses before committing changes
    experimentation = phase_experimentation(profile, scan, reflect, research, dirs, date_str)
    judge    = phase_judge_and_stage(profile, scan, reflect, research, experimentation)

    # Phase 5: Lesson extraction (Claude/Sonnet)
    lessons = phase_extract_lessons(judge, run_id, agent_name)

    # Contradiction detection (Sonnet) — before storage (Feature 2)
    if yaml_cfg.get("contradiction_detection", {}).get("enabled", True):
        lessons = detect_contradictions(
            lessons, agent_name, scan.get("priority_track", ""))
        for lesson in lessons:
            if lesson.get("recommendation") == "supersede":
                cid = lesson.get("conflicting_lesson_id")
                if cid:
                    mark_lesson_superseded(cid)

    if not dry_run:
        store_lessons(lessons, agent_name, run_id)

    # Persist final score and summary; cumulative_value accumulates for UCB1
    if not dry_run:
        update_run_node(run_id, judge.get("tonight_score", 0), judge.get("summary", ""))

    # Post-run lesson decay cleanup — not inline with phases (Feature 3)
    if not dry_run:
        selected_run_ids = [p["run_id"] for p in parents]
        decay_cfg        = yaml_cfg.get("lesson_decay", {})
        apply_lesson_decay(
            agent_name, selected_run_ids, run_id,
            decay_factor      = float(decay_cfg.get("decay_factor",       0.98)),
            dormant_threshold = float(decay_cfg.get("dormant_threshold",  0.15)),
        )

    manifest = write_staging(judge, date_str, dirs, dry_run=dry_run)
    changelog_path = write_changelog(date_str, agent_name, profile,
                                     scan, reflect, research, judge, manifest, dirs,
                                     experimentation, dry_run=dry_run)
    send_gmail_summary(changelog_path, judge, scan, agent_name, dry_run=dry_run)

    log(f"=== Complete. {len(manifest)} actions staged. ===")
if __name__ == "__main__":
    main()
