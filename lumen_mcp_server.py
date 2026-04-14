#!/usr/bin/env python3
"""
Lumen MCP Server — Agent memory access via FastMCP
===================================================
Exposes the agent_memories ChromaDB collection as MCP tools so Claude Code
(and other MCP clients) can read and write to per-agent memory namespaces.

Install:
    pip install mcp chromadb

Run (stdio transport, registered via .mcp.json):
    python3 lumen_mcp_server.py

Tools
-----
add_memory(content, namespace, tags)   Write a memory to a namespace.
query_memory(query, namespace, n)      Semantic search inside a namespace.
list_namespaces()                      List all namespaces that have memories.
delete_memory(id, namespace)           Delete a memory (own namespace only).

Trust rules
-----------
Agents may write to any namespace but may only delete entries that belong to
the namespace they declare — preventing cross-namespace sabotage.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
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
    # Provide a stub so the module can be imported for inspection even without
    # the mcp package installed.  Running the server itself will fail gracefully.
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

# ── Paths (mirrors dream_cycle.py) ───────────────────────────────────────────
CHROMA_DIR      = Path.home() / "dream-cycle" / "chroma_db"
COLLECTION_NAME = "agent_memories"

# ── Server ────────────────────────────────────────────────────────────────────
mcp = FastMCP("lumen")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_collection():
    """Return the agent_memories ChromaDB collection, creating it if needed."""
    if not CHROMA_AVAILABLE:
        raise RuntimeError(
            "chromadb is not installed.  Run:  pip install chromadb"
        )
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def add_memory(content: str, namespace: str, tags: list[str] | None = None) -> dict:
    """Add a memory entry to a namespace.

    Args:
        content:   The text to store.
        namespace: Logical agent namespace (e.g. "security", "ai_research").
        tags:      Optional list of keyword tags for later filtering.

    Returns:
        {"id": str, "namespace": str}
    """
    tags = tags or []
    coll = _get_collection()
    memory_id = f"{namespace}_{uuid.uuid4().hex[:12]}"
    coll.upsert(
        ids=[memory_id],
        documents=[content],
        metadatas=[{
            "namespace":  namespace,
            "tags":       ",".join(tags),
            "created_at": datetime.utcnow().isoformat(),
        }],
    )
    return {"id": memory_id, "namespace": namespace}


@mcp.tool()
def query_memory(query: str, namespace: str, n: int = 5) -> list[dict]:
    """Semantic search within a namespace.

    Args:
        query:     Natural-language search string.
        namespace: Namespace to search within.
        n:         Maximum number of results to return (default 5).

    Returns:
        List of {"id", "content", "metadata", "distance"} dicts, closest first.
    """
    coll = _get_collection()
    try:
        results = coll.query(
            query_texts=[query],
            n_results=max(1, n),
            where={"namespace": namespace},
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        # ChromaDB raises when the collection is empty or where clause finds nothing
        return [{"error": str(exc)}]

    out: list[dict] = []
    for rid, doc, meta, dist in zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        out.append({
            "id":       rid,
            "content":  doc,
            "metadata": meta,
            "distance": round(dist, 4),
        })
    return out


@mcp.tool()
def list_namespaces() -> list[str]:
    """List all namespaces that currently have stored memories.

    Returns:
        Sorted list of namespace strings.
    """
    coll = _get_collection()
    all_items = coll.get(include=["metadatas"])
    namespaces: set[str] = set()
    for meta in all_items.get("metadatas") or []:
        ns = meta.get("namespace")
        if ns:
            namespaces.add(ns)
    return sorted(namespaces)


@mcp.tool()
def delete_memory(id: str, namespace: str) -> dict:
    """Delete a memory entry.

    Agents may only delete entries that belong to the namespace they declare —
    cross-namespace deletes are rejected (trust rule).

    Args:
        id:        The memory id returned by add_memory.
        namespace: The caller's own namespace.

    Returns:
        {"deleted": True, "id": str}  on success.
        {"error": str, ...}           on failure or forbidden.
    """
    coll = _get_collection()
    result = coll.get(ids=[id], include=["metadatas"])
    if not result["ids"]:
        return {"error": "not_found", "id": id}

    stored_ns = (result["metadatas"][0] or {}).get("namespace", "")
    if stored_ns != namespace:
        return {
            "error":  "forbidden",
            "reason": "cross-namespace delete denied",
            "stored_namespace":   stored_ns,
            "declared_namespace": namespace,
        }

    coll.delete(ids=[id])
    return {"deleted": True, "id": id}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
