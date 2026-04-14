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

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

import platform
import random
import re
from urllib.parse import urlparse

try:
    import jsonschema
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False

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

# ── Manifest-based agent registry ─────────────────────────────────────────────

# Agent types that map to an existing built-in profile for default scanning config.
_MANIFEST_TYPE_TO_BUILTIN = {
    "security":    "security",
    "marketing":   "marketing",
    "programming": "programming",
    "research":    "ai_research",
}

# Safe identifier pattern: lowercase letter, then alphanumeric/underscore, 1–63 chars total.
_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Strict JSON Schema for manifest files.  additionalProperties: false ensures no
# unexpected fields sneak through — a common vector for prototype-pollution style attacks.
MANIFEST_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "pattern": r"^[a-z][a-z0-9_]{0,62}$",
            "description": "Unique agent identifier (lowercase, alphanumeric/underscore)",
        },
        "name":    {"type": "string", "minLength": 1, "maxLength": 100},
        "version": {
            "type": "string",
            "pattern": r"^\d+\.\d+\.\d+$",
            "description": "Semantic version string (e.g. 1.0.0)",
        },
        "type": {
            "type": "string",
            "enum": ["research", "security", "marketing", "programming"],
        },
        "memory_namespace": {
            "type": "string",
            "pattern": r"^[a-z][a-z0-9_]{0,62}$",
        },
        "scan_targets": {
            "type": "array",
            "items": {
                "type": "string",
                # Closed enum — prevents arbitrary filesystem paths from entering
                "enum": ["arxiv", "github_trending", "cves", "github_releases"],
            },
            "uniqueItems": True,
            "maxItems": 4,
        },
        "active":       {"type": "boolean"},
        "token_budget": {
            "type": "integer",
            "minimum": 100,
            "maximum": 8000,
            "description": "Max tokens of scan output per agent (default 2000)",
        },
        "mcp_endpoint": {"type": "string", "maxLength": 256},
        "checksum":     {
            "type": "string",
            "pattern": r"^[0-9a-f]{64}$",
            "description": "Optional SHA-256 hex digest of this file (excluding this field)",
        },
    },
    "required": ["id", "name", "version", "type", "memory_namespace",
                 "scan_targets", "active"],
    "additionalProperties": False,
}

# Allowed URL schemes for mcp_endpoint values.
_ALLOWED_MCP_SCHEMES = {"http", "https"}


def _validate_manifest_schema(manifest: dict, source: str) -> list[str]:
    """Return a list of schema violation strings; empty list means the manifest is valid.

    Uses jsonschema when available; falls back to manual required-field and type
    checks when it is not installed so the validator degrades gracefully.
    """
    errors: list[str] = []

    if JSONSCHEMA_AVAILABLE:
        try:
            validator = jsonschema.Draft7Validator(MANIFEST_JSON_SCHEMA)
            for err in sorted(validator.iter_errors(manifest), key=str):
                path = ".".join(str(p) for p in err.absolute_path) or "<root>"
                errors.append(f"{path}: {err.message}")
        except Exception as exc:
            errors.append(f"Schema validation exception: {exc}")
        return errors

    # ── Fallback: manual checks (no jsonschema) ───────────────────────────────
    required = MANIFEST_JSON_SCHEMA["required"]
    for field in required:
        if field not in manifest:
            errors.append(f"Missing required field: {field}")

    # Reject unexpected fields
    allowed = set(MANIFEST_JSON_SCHEMA["properties"].keys())
    extra   = set(manifest.keys()) - allowed
    if extra:
        errors.append(f"Unexpected fields (additionalProperties=false): {extra}")

    # Type checks for critical fields
    str_fields = ["id", "name", "version", "type", "memory_namespace"]
    for f in str_fields:
        if f in manifest and not isinstance(manifest[f], str):
            errors.append(f"Field '{f}' must be a string")
    if "active" in manifest and not isinstance(manifest["active"], bool):
        errors.append("Field 'active' must be a boolean")
    if "scan_targets" in manifest:
        if not isinstance(manifest["scan_targets"], list):
            errors.append("Field 'scan_targets' must be an array")
        else:
            allowed_targets = {"arxiv", "github_trending", "cves", "github_releases"}
            for t in manifest["scan_targets"]:
                if t not in allowed_targets:
                    errors.append(f"scan_targets: '{t}' is not an allowed value")

    # ID/namespace pattern
    for id_field in ("id", "memory_namespace"):
        val = manifest.get(id_field, "")
        if isinstance(val, str) and not _SAFE_ID_RE.match(val):
            errors.append(f"Field '{id_field}' fails pattern ^[a-z][a-z0-9_]{{0,62}}$")

    return errors


def _validate_manifest_checksum(manifest: dict, manifest_path: Path) -> bool:
    """If the manifest includes a 'checksum' field, verify it matches the file's SHA-256.

    The digest is computed over the raw bytes of the file as stored on disk.
    Returns True when the checksum is absent (nothing to verify) or matches.
    Returns False (and logs a warning) when it is present and does not match.
    """
    declared = manifest.get("checksum")
    if not declared:
        return True  # field absent — optional, no verification needed

    raw = manifest_path.read_bytes()
    computed = hashlib.sha256(raw).hexdigest()
    if computed != declared:
        log(f"  CHECKSUM MISMATCH in {manifest_path.name}: "
            f"declared={declared[:12]}… computed={computed[:12]}…")
        return False
    return True


def _validate_mcp_endpoint(url: str, source: str) -> bool:
    """Return True if the mcp_endpoint value is safe; log and return False otherwise.

    Only http:// and https:// schemes are allowed.  file://, javascript:, data:,
    and bare paths are all rejected to prevent SSRF / local-file reads.
    """
    if not url:
        return True  # empty — no endpoint configured, that is fine
    try:
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_MCP_SCHEMES:
            log(f"  Rejected mcp_endpoint '{url}' in {source}: "
                f"scheme '{parsed.scheme}' not in {_ALLOWED_MCP_SCHEMES}")
            return False
        if not parsed.netloc:
            log(f"  Rejected mcp_endpoint '{url}' in {source}: missing host")
            return False
    except Exception as exc:
        log(f"  Rejected mcp_endpoint '{url}' in {source}: parse error {exc}")
        return False
    return True


# Populated by _collect_registry_dirs() on Windows; used by load_agent_manifests()
# to apply additional scrutiny to registry-sourced paths.
_REGISTRY_SOURCED_DIRS: set[Path] = set()

# Maximum character length accepted for a registry-supplied path string.
_MAX_REGISTRY_PATH_LEN = 260  # Windows MAX_PATH

# Only REG_SZ (1) values are accepted from the registry.
# REG_EXPAND_SZ (2) can embed environment-variable expansions that may
# point outside any expected directory; we reject it entirely.
_REG_SZ = 1


def _is_safe_registry_path(raw_value: str, reg_type: int,
                             allowed_roots: list[Path]) -> tuple[bool, str]:
    """Validate a registry-sourced directory path before treating it as a manifest dir.

    Checks applied (in order):
      1. Value type is REG_SZ — reject REG_EXPAND_SZ and all other types.
      2. Value is a string within _MAX_REGISTRY_PATH_LEN characters.
      3. Value does not start with \\\\ (UNC path — network share).
      4. Resolved path does not contain '..' components.
      5. Resolved path is a descendant of at least one allowed_root.

    Returns:
        (True, "")          — safe to use
        (False, "<reason>") — rejected; reason is logged by caller
    """
    if reg_type != _REG_SZ:
        return False, f"registry value type {reg_type} rejected (only REG_SZ=1 allowed)"
    if not isinstance(raw_value, str):
        return False, "registry value is not a string"
    if len(raw_value) > _MAX_REGISTRY_PATH_LEN:
        return False, f"path exceeds {_MAX_REGISTRY_PATH_LEN} chars"
    if raw_value.startswith("\\\\"):
        return False, "UNC (network share) paths are not allowed"
    try:
        resolved = Path(raw_value).resolve()
    except Exception as exc:
        return False, f"path resolution failed: {exc}"
    if ".." in resolved.parts:
        return False, "path traversal ('..') detected after resolution"
    # Must be under at least one allowed root
    for root in allowed_roots:
        try:
            resolved.relative_to(root.resolve())
            return True, ""
        except ValueError:
            continue
    roots_str = ", ".join(str(r) for r in allowed_roots)
    return False, f"path '{resolved}' is outside allowed roots ({roots_str})"


def _collect_registry_dirs(allowed_roots: list[Path]) -> list[Path]:
    """Read HKCU\\Software\\DreamCycle\\Agents registry key and return validated paths.

    All paths from the registry are treated as untrusted and passed through
    _is_safe_registry_path() before being used.  Validated paths are added to
    _REGISTRY_SOURCED_DIRS so load_agent_manifests() can apply stricter checks.
    """
    result: list[Path] = []
    try:
        import winreg  # type: ignore[import]
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\DreamCycle\Agents")
        i = 0
        while True:
            try:
                _vname, raw_value, reg_type = winreg.EnumValue(key, i)
                ok, reason = _is_safe_registry_path(raw_value, reg_type, allowed_roots)
                if not ok:
                    log(f"  [registry-hardening] Rejected registry path "
                        f"'{str(raw_value)[:60]}': {reason}")
                    i += 1
                    continue
                rpath = Path(raw_value)
                if rpath.is_dir():
                    result.append(rpath)
                    _REGISTRY_SOURCED_DIRS.add(rpath.resolve())
                    log(f"  [registry-hardening] Accepted registry path (untrusted): {rpath}")
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    except Exception:
        pass  # winreg unavailable or key absent — not an error on Linux/macOS
    return result


def get_agent_manifest_dirs() -> list[Path]:
    """Return all valid manifest directories for the current platform.

    Linux  : ~/.dream_cycle/agents/
    macOS  : ~/.dream_cycle/agents/
             ~/Library/Application Support/dream_cycle/agents/
    Windows: %APPDATA%/dream_cycle/agents/
             HKCU\\Software\\DreamCycle\\Agents (REG_SZ values only;
             validated against allowed roots before use — see _is_safe_registry_path)

    Only directories that currently exist on disk are returned.
    pathlib.Path is used throughout — no hardcoded separators.
    """
    system     = platform.system()
    candidates: list[Path] = []

    # Common to all platforms
    candidates.append(Path.home() / ".dream_cycle" / "agents")

    if system == "Darwin":
        candidates.append(
            Path.home() / "Library" / "Application Support" / "dream_cycle" / "agents"
        )
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "dream_cycle" / "agents")
        # Registry paths are only accepted when they descend from the user's
        # home dir or %APPDATA% — prevents elevation to system directories.
        allowed_roots = [Path.home()]
        if appdata:
            allowed_roots.append(Path(appdata))
        candidates.extend(_collect_registry_dirs(allowed_roots))

    return [d for d in candidates if d.is_dir()]


def load_agent_manifests() -> dict:
    """Scan all platform manifest dirs and build a registry of active agents.

    Validation pipeline per manifest file:
      1. JSON parse (TypeError/ValueError → skip)
      2. JSON Schema (strict: required fields, types, no extra fields)
      3. Optional SHA-256 checksum verification
      4. mcp_endpoint URL scheme allow-list
      5. Inactive manifests are silently skipped
      6. Duplicate IDs: first encountered wins

    Never raises; invalid manifests are logged and skipped.
    """
    registry: dict[str, dict] = {}
    dirs = get_agent_manifest_dirs()
    if not dirs:
        return registry

    log(f"Scanning {len(dirs)} manifest dir(s): {[str(d) for d in dirs]}")
    for manifest_dir in dirs:
        is_registry_path = manifest_dir.resolve() in _REGISTRY_SOURCED_DIRS

        for manifest_file in sorted(manifest_dir.glob("*.json")):
            # ── Step 1: Parse JSON ────────────────────────────────────────────
            try:
                raw_bytes = manifest_file.read_bytes()
                manifest  = json.loads(raw_bytes)
                if not isinstance(manifest, dict):
                    raise TypeError("top-level value is not a JSON object")
            except Exception as exc:
                log(f"  Manifest parse error ({manifest_file.name}): {exc}")
                continue

            # ── Step 2: JSON Schema validation ────────────────────────────────
            schema_errors = _validate_manifest_schema(manifest, manifest_file.name)
            if schema_errors:
                for err in schema_errors:
                    log(f"  Schema error in {manifest_file.name}: {err}")
                log(f"  Skipping {manifest_file.name}: failed schema validation "
                    f"({len(schema_errors)} error(s))")
                continue

            # ── Step 3: SHA-256 checksum ──────────────────────────────────────
            # Registry-sourced manifests MUST include a checksum field — the
            # registry is an untrusted source and a checksum proves the file
            # has not been tampered with after the author signed it.
            if is_registry_path and not manifest.get("checksum"):
                log(f"  Skipping {manifest_file.name}: registry-sourced manifests "
                    f"require a 'checksum' field (SHA-256 of file contents)")
                continue
            if not _validate_manifest_checksum(manifest, manifest_file):
                log(f"  Skipping {manifest_file.name}: checksum mismatch")
                continue

            # ── Step 4: mcp_endpoint URL allow-list ──────────────────────────
            endpoint = manifest.get("mcp_endpoint", "")
            if not _validate_mcp_endpoint(endpoint, manifest_file.name):
                log(f"  Skipping {manifest_file.name}: invalid mcp_endpoint")
                continue

            # ── Step 5: Active flag ───────────────────────────────────────────
            if not manifest.get("active", False):
                log(f"  Skipping {manifest_file.name}: inactive")
                continue

            # ── Step 6: Duplicate ID guard ────────────────────────────────────
            agent_id = manifest["id"]
            if agent_id in registry:
                log(f"  Duplicate id '{agent_id}' in {manifest_file.name} — skipped")
                continue

            registry[agent_id] = manifest
            trust_label = " [REGISTRY-SOURCED — untrusted]" if is_registry_path else ""
            log(f"  Registered manifest agent: {agent_id} ({manifest['name']}){trust_label}")

    return registry


def manifest_to_profile(manifest: dict) -> dict:
    """Convert a validated manifest dict to an AGENT_PROFILES-compatible profile dict.

    The manifest's *type* selects the closest built-in profile as a base for
    scan configuration (arxiv queries, GitHub trending, CVE toggle, etc.).
    Manifest-specific fields (memory_namespace, mcp_endpoint, scan_targets)
    are preserved as extra keys so the rest of the cycle can use them.
    scan_targets is a closed enum validated by the schema, so no path expansion
    is needed or performed here.
    """
    base_key     = _MANIFEST_TYPE_TO_BUILTIN.get(manifest.get("type", ""), "ai_research")
    base         = dict(AGENT_PROFILES[base_key])
    scan_targets: list[str] = manifest.get("scan_targets", [])

    return {
        **base,
        "name":             manifest["name"],
        "memory_namespace": manifest["memory_namespace"],
        "mcp_endpoint":     manifest.get("mcp_endpoint", ""),
        "scan_targets":     scan_targets,  # already validated enum values
        "token_budget":     int(manifest.get("token_budget", 2000)),
        "fetch_cves":              "cves" in scan_targets,
        "fetch_github_trending":   "github_trending" in scan_targets,
        "_from_manifest":          True,
    }

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


def _agent_memories(chroma):
    """Per-agent namespace memories — also exposed via the Lumen MCP server."""
    return chroma.get_or_create_collection(
        name="agent_memories",
        metadata={"hnsw:space": "cosine"},
    )


def _enforce_namespace(agent_name: str, namespace: str) -> bool:
    """Verify that *namespace* is a valid safe identifier and owned by *agent_name*.

    The ownership rule: the namespace must either equal the agent_name exactly,
    or match the declared memory_namespace from the agent's profile.  This is
    checked by the caller; this helper only validates the namespace string itself.

    Returns True when the namespace passes all checks; False otherwise.
    """
    if not namespace or not isinstance(namespace, str):
        log(f"  [namespace-isolation] Rejected empty/non-string namespace for {agent_name}")
        return False
    if not _SAFE_ID_RE.match(namespace):
        log(f"  [namespace-isolation] Rejected unsafe namespace '{namespace}' "
            f"(fails ^[a-z][a-z0-9_]{{0,62}}$)")
        return False
    return True


def _check_namespace_entry_integrity(entry_doc: str, entry_meta: dict,
                                      expected_ns: str) -> tuple[bool, str]:
    """Validate a single namespace entry before it is promoted to global memory.

    Checks:
      1. Metadata namespace tag matches the namespace we queried.
      2. Document is valid JSON with expected keys (run_id, tonight_score, date).
      3. tonight_score is a number in [0, 10].
      4. run_id does not contain path-traversal characters.

    Returns:
        (ok: bool, reason: str)  — reason is '' when ok is True.
    """
    # Check 1: namespace tag consistency
    stored_ns = entry_meta.get("namespace", "")
    if stored_ns != expected_ns:
        return False, (f"namespace mismatch: stored='{stored_ns}' expected='{expected_ns}' "
                       "— possible cross-namespace pollution")

    # Check 2: document is valid JSON with expected structure
    try:
        doc = json.loads(entry_doc)
    except (json.JSONDecodeError, TypeError):
        return False, "document is not valid JSON"
    if not isinstance(doc, dict):
        return False, "document top-level is not a JSON object"
    for required_key in ("run_id", "tonight_score", "date"):
        if required_key not in doc:
            return False, f"document missing expected key: '{required_key}'"

    # Check 3: score is numeric in bounds
    score = doc.get("tonight_score")
    if not isinstance(score, (int, float)) or not (0 <= score <= 10):
        return False, f"tonight_score out of range: {score!r}"

    # Check 4: run_id sanity (no slashes, nulls, or excessive length)
    run_id = str(doc.get("run_id", ""))
    if len(run_id) > 128 or any(c in run_id for c in ("/", "\\", "\x00", "..")):
        return False, f"suspicious run_id: {run_id[:40]!r}"

    return True, ""


def write_to_agent_namespace(agent_name: str, namespace: str,
                              judge: dict, scan: dict, run_id: str):
    """Persist tonight's judge output to the agent's memory namespace.

    Namespace isolation is enforced at the write layer:
    - The namespace string is validated against a safe-identifier pattern.
    - The upserted metadata includes 'namespace_owner' = agent_name so that
      the consolidation pass can verify provenance of every entry it reads.

    The same agent_memories collection is exposed read/write via lumen_mcp_server.py,
    so anything stored here is immediately queryable by Claude Code through MCP tools.
    """
    # ── Write-layer namespace enforcement ────────────────────────────────────
    if not _enforce_namespace(agent_name, namespace):
        log(f"  [namespace-isolation] Aborting write for '{agent_name}': "
            f"namespace '{namespace}' failed validation")
        return

    chroma = get_chroma_client()
    if chroma is None:
        log(f"ChromaDB unavailable — skipping namespace write for '{namespace}'")
        return
    coll = _agent_memories(chroma)

    content = json.dumps({
        "run_id":         run_id,
        "tonight_score":  judge.get("tonight_score", 0),
        "summary":        judge.get("summary", "")[:500],
        "priority_track": scan.get("priority_track", ""),
        "staged_count":   len(judge.get("staged_actions", [])),
        "date":           datetime.now().strftime("%Y-%m-%d"),
    })
    memory_id = f"{namespace}_{run_id}"
    coll.upsert(
        ids=[memory_id],
        documents=[content],
        metadatas=[{
            "namespace":       namespace,
            "namespace_owner": agent_name,   # provenance field for integrity checks
            "agent_name":      agent_name,
            "run_id":          run_id,
            "tags":            scan.get("priority_track", ""),
            "created_at":      datetime.now().isoformat(),
        }],
    )
    log(f"Namespace '{namespace}': run output written to agent_memories")


def consolidate_agent_namespaces(active_namespaces: list[str]) -> dict:
    """Query all active namespaces and return a cross-namespace summary dict.

    Each entry is passed through _check_namespace_entry_integrity() before being
    included.  Anomalous or out-of-distribution entries are flagged and excluded
    from the returned summary — they are not deleted but logged for audit.

    Returns {} when ChromaDB is unavailable or no namespaces have any entries.
    """
    chroma = get_chroma_client()
    if chroma is None or not active_namespaces:
        return {}

    coll     = _agent_memories(chroma)
    summary: dict[str, list[dict]] = {}
    flagged  = 0

    for ns in active_namespaces:
        if not _enforce_namespace("<consolidation>", ns):
            log(f"Consolidation: skipping invalid namespace '{ns}'")
            continue
        try:
            results = coll.get(
                where={"namespace": ns},
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            log(f"Consolidation: failed to query namespace '{ns}': {exc}")
            continue

        docs  = results.get("documents") or []
        metas = results.get("metadatas") or []
        good_entries: list[dict] = []

        for doc, meta in zip(docs, metas):
            ok, reason = _check_namespace_entry_integrity(doc, meta, ns)
            if not ok:
                flagged += 1
                log(f"  [namespace-isolation] FLAGGED entry in namespace '{ns}': {reason}")
                continue
            good_entries.append({
                "content": doc,
                "run_id":  meta.get("run_id", ""),
                "date":    meta.get("created_at", "")[:10],
            })

        # Keep only the five most-recent validated entries
        summary[ns] = good_entries[-5:] if good_entries else []

    if flagged:
        log(f"Consolidation: {flagged} anomalous/out-of-distribution "
            f"entry(ies) flagged and excluded from global memory")
    return summary


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
        confidence = float(lesson.get("confidence", 0.5))
        metadatas.append({
            "domain":                str(lesson.get("domain", "")),
            "confidence":            confidence,
            "source_run_id":         str(lesson.get("source_run_id", run_id)),
            "tags":                  ",".join(tags) if isinstance(tags, list) else str(tags),
            "agent_name":            agent_name,
            # ── contradiction metadata (Feature 2) ────────────────────────
            "contradiction":         bool(lesson.get("contradiction", False)),
            "conflicting_lesson_id": str(lesson.get("conflicting_lesson_id") or ""),
            "recommendation":        str(lesson.get("recommendation", "coexist")),
            # ── decay fields (Feature 3) ──────────────────────────────────
            "confidence_decay":      confidence,   # starts equal to base confidence
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

    raw = claude_chat(prompt)
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


# ── Prompt injection defense ──────────────────────────────────────────────────

# Patterns that look like system-prompt injection attempts.
# Compiled once; applied to every piece of external content before it enters
# any LLM prompt (scan items, reflection data, external summaries).
_INJECTION_PATTERNS: list[re.Pattern] = [
    # XML / SGML system-role markers
    re.compile(r"<\s*/?\s*system\s*>",              re.IGNORECASE),
    re.compile(r"<\s*/?\s*user\s*>",                re.IGNORECASE),
    re.compile(r"<\s*/?\s*assistant\s*>",           re.IGNORECASE),
    re.compile(r"<\s*/?\s*instruction\s*>",         re.IGNORECASE),
    re.compile(r"<\s*/?\s*prompt\s*>",              re.IGNORECASE),
    re.compile(r"<\s*/?\s*context\s*>",             re.IGNORECASE),
    # Pipe-delimited chat-template tokens (LLaMA / Mistral / Qwen family)
    re.compile(r"<\|system\|>",                     re.IGNORECASE),
    re.compile(r"<\|user\|>",                       re.IGNORECASE),
    re.compile(r"<\|assistant\|>",                  re.IGNORECASE),
    re.compile(r"<\|im_start\|>",                   re.IGNORECASE),
    re.compile(r"<\|im_end\|>",                     re.IGNORECASE),
    re.compile(r"<\|endoftext\|>",                  re.IGNORECASE),
    # Bracket-style role markers
    re.compile(r"\[SYSTEM\]",                       re.IGNORECASE),
    re.compile(r"\[USER\]",                         re.IGNORECASE),
    re.compile(r"\[ASSISTANT\]",                    re.IGNORECASE),
    re.compile(r"\[INST\]",                         re.IGNORECASE),
    re.compile(r"\[/INST\]",                        re.IGNORECASE),
    # Common jailbreak preambles (flag for stripping)
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+DAN\s+mode",            re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you\s+have\s+no\s+restrictions?", re.IGNORECASE),
]

# 1 token ≈ 4 characters (conservative estimate for most tokenizers).
_CHARS_PER_TOKEN = 4
# Hard ceiling: never let a single field exceed this many characters regardless
# of the configured budget (prevents runaway external content).
_ABSOLUTE_FIELD_CAP = 2000


def sanitize_llm_input(text: str, label: str = "", max_tokens: int = 2000) -> str:
    """Strip prompt-injection markers and enforce a token-budget character cap.

    Applied to every piece of external content (arXiv titles/summaries, CVE
    descriptions, GitHub release notes) before it is interpolated into an LLM
    prompt.  Never raises; returns a safe truncated string on any error.

    Args:
        text:       The raw external string to sanitize.
        label:      Human-readable label used in log messages (e.g. "arXiv title").
        max_tokens: Maximum token budget for this field (default 2000).
                    Converted to characters via _CHARS_PER_TOKEN.

    Returns:
        Cleaned, truncated string.
    """
    if not isinstance(text, str):
        return ""

    original_len = len(text)
    cleaned      = text

    # Strip injection patterns
    stripped_patterns: list[str] = []
    for pat in _INJECTION_PATTERNS:
        if pat.search(cleaned):
            stripped_patterns.append(pat.pattern[:40])
            cleaned = pat.sub("", cleaned)

    if stripped_patterns:
        log(f"  [injection-defense] Stripped pattern(s) from {label or 'input'}: "
            f"{stripped_patterns}")

    # Enforce character cap (token budget × chars-per-token, capped at absolute max)
    max_chars = min(max_tokens * _CHARS_PER_TOKEN, _ABSOLUTE_FIELD_CAP * 4)
    if len(cleaned) > max_chars:
        log(f"  [injection-defense] Truncated {label or 'input'} "
            f"{len(cleaned)} → {max_chars} chars")
        cleaned = cleaned[:max_chars]

    return cleaned.strip()


def sanitize_scan_items(items: list[dict], token_budget: int = 2000) -> list[dict]:
    """Sanitize the text fields of every item in a scan list in-place.

    Fields sanitized: title, summary, link (link is URL-safe-checked separately),
    description, body.  Returns the mutated list for convenience.
    """
    per_field_budget = max(100, token_budget // 4)
    for item in items:
        for field in ("title", "summary", "description", "body"):
            if field in item and isinstance(item[field], str):
                item[field] = sanitize_llm_input(
                    item[field],
                    label=f"item.{field}",
                    max_tokens=per_field_budget,
                )
        # Links: strip any javascript: / data: schemes
        link = item.get("link", "")
        if isinstance(link, str):
            parsed = urlparse(link)
            if parsed.scheme not in ("http", "https", ""):
                log(f"  [injection-defense] Stripped unsafe link scheme: {link[:80]}")
                item["link"] = ""
    return items


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

    # ── Sanitize external content before it enters the LLM prompt ─────────────
    token_budget = int(profile.get("token_budget", 2000))
    filtered     = sanitize_scan_items(filtered, token_budget=token_budget)
    # Hard-cap the serialized items injected into the prompt at budget × 4 chars
    items_str    = json.dumps(filtered, indent=2)[:token_budget * _CHARS_PER_TOKEN]

    prompt = f"""You are the scan phase of a nightly research agent.
Agent: {profile['name']}
Context: {profile['context']}
Tracks: {', '.join(profile['tracks'])}

Tonight's items ({len(filtered)} pre-scored for relevance):
{items_str}

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

    # Re-sanitize findings coming out of the scan phase before they reach Claude.
    # The Qwen model could theoretically reproduce injected content in its output.
    token_budget = int(profile.get("token_budget", 2000))
    findings     = sanitize_scan_items(list(findings), token_budget=token_budget)

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

    # ── Build combined profile registry ───────────────────────────────────────
    # Load manifest agents first so they can extend (but not silently replace)
    # the built-in profiles.  Manifest ids that collide with built-in names are
    # logged and skipped to avoid surprising behavior.
    manifest_registry = load_agent_manifests()
    manifest_profiles: dict[str, dict] = {}
    for agent_id, manifest in manifest_registry.items():
        if agent_id in AGENT_PROFILES:
            log(f"Manifest id '{agent_id}' shadows a built-in profile — skipped. "
                f"Rename the manifest id to avoid collision.")
            continue
        manifest_profiles[agent_id] = manifest_to_profile(manifest)

    all_profiles: dict[str, dict] = {**AGENT_PROFILES, **manifest_profiles}

    parser = argparse.ArgumentParser(description="Dream Cycle — nightly research agent")
    parser.add_argument("--agent",       choices=list(all_profiles.keys()),
                        help="Agent profile to run (built-in or manifest-registered)")
    parser.add_argument("--reconfigure", action="store_true",
                        help="Re-run setup for the selected agent")
    parser.add_argument("--list-agents", action="store_true",
                        help="List available agent profiles and exit")
    parser.add_argument("--disable-ucb1", action="store_true",
                        help="Fall back to sequential (most-recent-first) context-parent selection")
    parser.add_argument("--audit-dormant", action="store_true",
                        help="Review dormant lessons for the selected agent and exit")
    args = parser.parse_args()

    if args.list_agents:
        print("\nBuilt-in agents:")
        for name, p in AGENT_PROFILES.items():
            print(f"  {name:20} — {p['name']}")
            print(f"  {'':20}   Tracks: {', '.join(p['tracks'][:3])}...")
        if manifest_profiles:
            print("\nManifest-registered agents:")
            for name, p in manifest_profiles.items():
                ns = p.get("memory_namespace", "—")
                print(f"  {name:20} — {p['name']}  [namespace: {ns}]")
                print(f"  {'':20}   Tracks: {', '.join(p.get('tracks', [])[:3])}...")
        else:
            print("\nNo manifest agents found. "
                  "Add *.json manifests to ~/.dream_cycle/agents/ to register agents.")
        return

    agent_name = args.agent or "ai_research"
    profile    = all_profiles[agent_name]
    dirs       = get_agent_dirs(agent_name)
    date_str   = datetime.now().strftime("%Y-%m-%d")
    run_id     = f"{agent_name}_{date_str}_{datetime.now().strftime('%H%M%S')}"

    # --audit-dormant: review dormant lessons and exit (no run needed)
    if args.audit_dormant:
        audit_dormant_lessons(agent_name)
        return

    log(f"=== Dream Cycle [{profile['name']}] — {date_str} (run={run_id}) ===")

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
    judge    = phase_judge_and_stage(profile, scan, reflect, research)

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

    store_lessons(lessons, agent_name, run_id)

    # Persist final score and summary; cumulative_value accumulates for UCB1
    update_run_node(run_id, judge.get("tonight_score", 0), judge.get("summary", ""))

    # Write tonight's output to the agent's memory namespace (Lumen-readable via MCP)
    memory_namespace = profile.get("memory_namespace", agent_name)
    write_to_agent_namespace(agent_name, memory_namespace, judge, scan, run_id)

    # Consolidation pass: query every known active namespace so cross-agent
    # state is visible in the changelog and available for tomorrow's context.
    all_namespaces = list({
        p.get("memory_namespace", n)
        for n, p in all_profiles.items()
        if p.get("memory_namespace")
    })
    ns_summary = consolidate_agent_namespaces(all_namespaces)
    if ns_summary:
        active_ns = [ns for ns, entries in ns_summary.items() if entries]
        log(f"Namespace consolidation: {len(active_ns)} namespace(s) with data "
            f"({', '.join(active_ns)})")

    # Post-run lesson decay cleanup — not inline with phases (Feature 3)
    selected_run_ids = [p["run_id"] for p in parents]
    decay_cfg        = yaml_cfg.get("lesson_decay", {})
    apply_lesson_decay(
        agent_name, selected_run_ids, run_id,
        decay_factor      = float(decay_cfg.get("decay_factor",       0.98)),
        dormant_threshold = float(decay_cfg.get("dormant_threshold",  0.15)),
    )

    manifest = write_staging(judge, date_str, dirs)
    changelog_path = write_changelog(date_str, agent_name, profile,
                                     scan, reflect, research, judge, manifest, dirs)
    send_gmail_summary(changelog_path, judge, scan, agent_name)

    log(f"=== Complete. {len(manifest)} actions staged. ===")
if __name__ == "__main__":
    main()
