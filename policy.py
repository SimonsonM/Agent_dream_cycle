"""
Deterministic risk policy for staged actions.

The LLM's self-declared risk is advisory only. Effective risk is
max(declared, policy). Nothing here calls a model.
"""
import json
import re
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

BASE_DIR = Path.home() / "dream-cycle"
LOGS_DIR = Path.home() / "dream-logs"

LEVELS = ("low", "medium", "high")
MAX_CONTENT_BYTES = 64 * 1024
DOC_SUFFIXES = {".md", ".txt", ".rst"}
CONFIG_SUFFIXES = {".json", ".yaml", ".yml"}
# Never auto-writable, even inside BASE_DIR (runtime state, creds, schedulers).
PROTECTED_NAMES = {"config.json", "config.yaml", ".mcp.json", ".env", "crontab",
                   "authorized_keys", "manifest.json"}
# Path components that are never auto-writable (staging could self-escalate).
PROTECTED_PARTS = {"staging", "chroma_db", ".git", ".ssh", "systemd"}
MODEL_RE = re.compile(r"^ollama pull [A-Za-z0-9][A-Za-z0-9._:/-]{0,100}$")


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 2   # unknown -> high


def max_risk(a: str, b: str) -> str:
    return LEVELS[max(_rank(a), _rank(b))]


def safe_target(file_path: str, allowed=None) -> Path | None:
    """Resolved path if inside an allowed dir and not protected; else None."""
    allowed = allowed if allowed is not None else (BASE_DIR, LOGS_DIR)
    try:
        target = Path(file_path).expanduser().resolve()
    except Exception:
        return None
    if target.name in PROTECTED_NAMES or PROTECTED_PARTS & set(target.parts):
        return None
    for root in allowed:
        try:
            target.relative_to(Path(root).resolve())
            return target
        except ValueError:
            continue
    return None


def _parses(content: str, suffix: str) -> bool:
    try:
        if suffix == ".json":
            json.loads(content)
        else:
            if yaml is None:
                return False
            yaml.safe_load(content)
        return True
    except Exception:
        return False


def policy_risk(action: dict, allowed=None) -> tuple[str, list[str]]:
    """Return (risk, reasons) from action content alone."""
    atype = action.get("action_type", "")
    content = action.get("content", "") or ""
    path = action.get("file_path", "") or ""
    why: list[str] = []

    if not isinstance(content, str) or len(content.encode()) > MAX_CONTENT_BYTES:
        return "high", ["content missing/oversized"]

    if atype == "model_pull":
        if MODEL_RE.match(content.strip()):
            return "low", why
        return "high", ["model_pull content not a plain 'ollama pull <name>'"]

    if atype in ("documentation", "config"):
        target = safe_target(path, allowed) if path else None
        if target is None:
            return "high", ["file_path missing, protected, or outside allowed dirs"]
        suffixes = DOC_SUFFIXES if atype == "documentation" else CONFIG_SUFFIXES
        if target.suffix.lower() not in suffixes:
            return "medium", [f"{atype} suffix {target.suffix!r} not auto-applicable"]
        if atype == "config" and not _parses(content, target.suffix.lower()):
            return "medium", ["config content does not parse"]
        return "low", why

    if atype == "workflow":
        return "medium", ["workflow changes need review"]
    if atype == "script":
        return "high", ["scripts are never auto-applied"]
    return "high", [f"unknown action_type {atype!r}"]


def effective_risk(action: dict, allowed=None) -> tuple[str, list[str]]:
    declared = str(action.get("risk", "high")).lower()
    pol, why = policy_risk(action, allowed)
    final = max_risk(declared, pol)
    if final != declared:
        why = why + [f"declared {declared!r} raised to {final!r}"]
    return final, why
