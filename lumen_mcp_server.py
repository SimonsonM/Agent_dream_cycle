#!/usr/bin/env python3
"""
Lumen MCP Server — Agent memory access via FastMCP
===================================================
Exposes the agent_memories ChromaDB collection as MCP tools so Claude Code
(and other MCP clients) can read and write to per-agent memory namespaces.

Install:
    pip install mcp chromadb

Authentication:
    Lumen requires a shared secret in the LUMEN_MCP_TOKEN environment variable.
    On first startup the token is auto-generated and written to:
        ~/.dream_cycle/.env  (chmod 600)

    Export it before launching any client that needs to call these tools:
        source ~/.dream_cycle/.env
        export LUMEN_MCP_TOKEN

    The .mcp.json env block passes the token to the server automatically when
    Claude Code spawns it.  Tools return {"error": "401 Unauthorized"} when the
    server was started without LUMEN_MCP_TOKEN set in its environment.

Run (stdio transport, registered via .mcp.json):
    python3 lumen_mcp_server.py

Bootstrap (generate / display the token without starting the server):
    python3 lumen_mcp_server.py --bootstrap

Tools
-----
add_memory(content, namespace, tags)   Write a memory to a namespace.
query_memory(query, namespace, n)      Semantic search inside a namespace.
list_namespaces()                      List all namespaces that have memories.
delete_memory(id, namespace)           Delete a memory (own namespace only).

Trust rules
-----------
- Server-level auth: LUMEN_MCP_TOKEN must be set in the server process env.
  Tools return 401 when the token is absent.
- Namespace isolation: agents may write to any namespace but may only delete
  entries that belong to the namespace they declare (cross-namespace deletes
  are rejected with a 403 payload).
"""

from __future__ import annotations

import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── ChromaDB ──────────────────────────────────────────────────────────────────
try:
    import chromadb
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False

# ── FastMCP ───────────────────────────────────────────────────────────────────
try:
    from mcp.server.fastmcp import FastMCP
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    class FastMCP:  # type: ignore[no-redef]
        def __init__(self, name: str):
            self.name = name
        def tool(self):
            def decorator(fn):
                return fn
            return decorator
        def run(self):
            raise RuntimeError(
                "The 'mcp' package is required to run Lumen.\n"
                "Install it with:  pip install mcp"
            )

# ── Paths ─────────────────────────────────────────────────────────────────────
CHROMA_DIR      = Path.home() / "dream-cycle" / "chroma_db"
COLLECTION_NAME = "agent_memories"
_DOT_DREAM_DIR  = Path.home() / ".dream_cycle"
_ENV_FILE       = _DOT_DREAM_DIR / ".env"

# ── Token auth ────────────────────────────────────────────────────────────────

def _read_dotenv_token() -> str:
    """Read LUMEN_MCP_TOKEN from ~/.dream_cycle/.env if it exists."""
    if not _ENV_FILE.exists():
        return ""
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("LUMEN_MCP_TOKEN=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip()
    return ""


def _bootstrap_token() -> str:
    """Return the server token, generating and persisting it on first run.

    Priority:
      1. LUMEN_MCP_TOKEN environment variable (set by shell or .mcp.json env block)
      2. ~/.dream_cycle/.env  (LUMEN_MCP_TOKEN=<hex>)
      3. Generate 32-byte hex, append to ~/.dream_cycle/.env (chmod 600), return it
    """
    # 1. Env var wins
    env_val = os.environ.get("LUMEN_MCP_TOKEN", "").strip()
    if env_val:
        return env_val

    # 2. Try the .env file
    file_val = _read_dotenv_token()
    if file_val:
        return file_val

    # 3. Generate a fresh token
    token = secrets.token_hex(32)
    _DOT_DREAM_DIR.mkdir(parents=True, exist_ok=True)
    with open(_ENV_FILE, "a") as fh:
        fh.write(f"\nLUMEN_MCP_TOKEN={token}\n")
    _ENV_FILE.chmod(0o600)
    print(
        f"[lumen] Token generated and written to {_ENV_FILE}\n"
        f"[lumen] Add to your shell profile:  source {_ENV_FILE} && export LUMEN_MCP_TOKEN",
        file=sys.stderr,
    )
    return token


# Load (or generate) the token once at module level.
# Tools check this value; a missing/empty token means the server is not
# properly configured and all calls are rejected with 401.
_SERVER_TOKEN: str = _bootstrap_token()


def _auth_error() -> dict | None:
    """Return a 401 payload when the server lacks a valid token; None otherwise."""
    if not _SERVER_TOKEN:
        return {
            "error":  "401 Unauthorized",
            "reason": (
                "LUMEN_MCP_TOKEN is not set in the server environment. "
                f"Source {_ENV_FILE} and re-launch the server."
            ),
        }
    return None


# ── Namespace validation ──────────────────────────────────────────────────────

# Must match dream_cycle.py _SAFE_ID_RE — kept in sync manually.
_SAFE_NS_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _validate_namespace(namespace: str) -> dict | None:
    """Return a 400 error dict if namespace is not a safe identifier; None if OK."""
    if not isinstance(namespace, str) or not _SAFE_NS_RE.match(namespace):
        return {
            "error":  "400 Bad Request",
            "reason": (
                f"Invalid namespace '{namespace}'. "
                "Must match ^[a-z][a-z0-9_]{0,62}$ (lowercase, no separators)."
            ),
        }
    return None


# ── Server ────────────────────────────────────────────────────────────────────
mcp = FastMCP("lumen")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_collection():
    """Return the agent_memories ChromaDB collection, creating it if needed."""
    if not CHROMA_AVAILABLE:
        raise RuntimeError("chromadb is not installed.  Run:  pip install chromadb")
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def add_memory(content: str, namespace: str,
               tags: list[str] | None = None) -> dict:
    """Add a memory entry to a namespace.

    Requires LUMEN_MCP_TOKEN to be set in the server environment.

    Args:
        content:   The text to store.
        namespace: Logical agent namespace (e.g. "security", "ai_research").
        tags:      Optional list of keyword tags for later filtering.

    Returns:
        {"id": str, "namespace": str}  on success.
        {"error": "401 Unauthorized"}  when the server token is not configured.
    """
    if (err := _auth_error()):
        return err
    if (err := _validate_namespace(namespace)):
        return err
    tags = tags or []
    coll = _get_collection()
    memory_id = f"{namespace}_{uuid.uuid4().hex[:12]}"
    coll.upsert(
        ids=[memory_id],
        documents=[content],
        metadatas=[{
            "namespace":  namespace,
            "tags":       ",".join(tags),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }],
    )
    return {"id": memory_id, "namespace": namespace}


@mcp.tool()
def query_memory(query: str, namespace: str, n: int = 5) -> list[dict]:
    """Semantic search within a namespace.

    Requires LUMEN_MCP_TOKEN to be set in the server environment.

    Args:
        query:     Natural-language search string.
        namespace: Namespace to search within.
        n:         Maximum number of results to return (default 5).

    Returns:
        List of {"id", "content", "metadata", "distance"} dicts, closest first.
        [{"error": "401 Unauthorized"}] when auth fails.
    """
    if (err := _auth_error()):
        return [err]
    if (err := _validate_namespace(namespace)):
        return [err]
    coll = _get_collection()
    try:
        results = coll.query(
            query_texts=[query],
            n_results=max(1, n),
            where={"namespace": namespace},
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        return [{"error": str(exc)}]

    return [
        {
            "id":       rid,
            "content":  doc,
            "metadata": meta,
            "distance": round(dist, 4),
        }
        for rid, doc, meta, dist in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )
    ]


@mcp.tool()
def list_namespaces() -> list[str] | dict:
    """List all namespaces that currently have stored memories.

    Requires LUMEN_MCP_TOKEN to be set in the server environment.

    Returns:
        Sorted list of namespace strings, or {"error": "401 Unauthorized"}.
    """
    if (err := _auth_error()):
        return err
    coll = _get_collection()
    all_items  = coll.get(include=["metadatas"])
    namespaces: set[str] = set()
    for meta in all_items.get("metadatas") or []:
        ns = meta.get("namespace")
        if ns:
            namespaces.add(ns)
    return sorted(namespaces)


@mcp.tool()
def delete_memory(id: str, namespace: str) -> dict:
    """Delete a memory entry (own namespace only).

    Requires LUMEN_MCP_TOKEN to be set in the server environment.
    Cross-namespace deletes are rejected with a 403 payload — agents may only
    delete entries that belong to the namespace they declare.

    Args:
        id:        The memory id returned by add_memory.
        namespace: The caller's own namespace (enforced server-side).

    Returns:
        {"deleted": True, "id": str}  on success.
        {"error": "401 Unauthorized"} when server token is not configured.
        {"error": "403 Forbidden"}    when id belongs to a different namespace.
        {"error": "not_found"}        when id does not exist.
    """
    if (err := _auth_error()):
        return err
    if (err := _validate_namespace(namespace)):
        return err
    coll = _get_collection()
    result = coll.get(ids=[id], include=["metadatas"])
    if not result["ids"]:
        return {"error": "not_found", "id": id}

    stored_ns = (result["metadatas"][0] or {}).get("namespace", "")
    if stored_ns != namespace:
        return {
            "error":              "403 Forbidden",
            "reason":             "cross-namespace delete denied",
            "stored_namespace":   stored_ns,
            "declared_namespace": namespace,
        }

    coll.delete(ids=[id])
    return {"deleted": True, "id": id}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--bootstrap" in sys.argv:
        print(f"LUMEN_MCP_TOKEN={_SERVER_TOKEN}")
        print(f"Written to: {_ENV_FILE}")
        sys.exit(0)
    mcp.run()
