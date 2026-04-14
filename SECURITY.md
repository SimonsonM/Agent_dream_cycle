# Security Policy

## Supported Versions

Security fixes are applied to the `main` branch only. There are no versioned release branches.

---

## Reporting a Vulnerability

Please **do not open a public GitHub issue** for security reports.

Email: [Mike Simonson](https://linkedin.com/in/simonsonmba) — use the contact link on LinkedIn to request a private email address.

Include:
- A clear description of the vulnerability and its impact
- Steps to reproduce (proof-of-concept code or a manifest file if applicable)
- The files and line numbers affected

Expected response time: **within 5 business days**.

---

## Security Architecture

### Trust Boundaries

```
External data sources (arXiv, GitHub, NVD)
        │  untrusted text
        ▼
  sanitize_llm_input()  ─── strips injection markers, enforces token budget
        │  sanitized JSON
        ▼
  Qwen (local Ollama)  ──── scan / reflect phases — output treated as untrusted
        │  sanitized again
        ▼
  Claude Sonnet  ─────────── research / judge phases
        │  structured JSON
        ▼
  ChromaDB (agent_memories)  namespace isolation enforced at write layer
        │  validated entries only
        ▼
  Consolidation pass  ──────  per-entry integrity check before global merge
```

### Manifest Pipeline

Every agent manifest file passes a six-stage validation before enrollment:

| Stage | Check | Failure action |
|-------|-------|----------------|
| 1 | JSON parse — must be a valid JSON object | Skip + log |
| 2 | JSON Schema — `additionalProperties: false`, closed enum for `scan_targets`, regex patterns for `id` and `memory_namespace` | Skip + log all errors |
| 3 | SHA-256 checksum — if `checksum` field is present it must match the file's bytes; **required** for registry-sourced paths | Skip + log |
| 4 | `mcp_endpoint` URL allow-list — only `http://` and `https://` schemes accepted | Skip + log |
| 5 | `active` flag — inactive manifests are silently skipped | Skip |
| 6 | Duplicate ID guard — first ID wins; collisions logged | Skip |

---

## Known Attack Vectors and Mitigations

### 1. Prompt Injection via External Content

**Vector:** A malicious arXiv paper title, CVE description, or GitHub release note contains `<system>`, `[INST]`, or role-marker syntax intended to override the model's instructions.

**Mitigation (`dream_cycle.py`):**
- `sanitize_llm_input()` applies `_INJECTION_PATTERNS` (30+ compiled regexes) to strip XML/SGML role tags, LLaMA/Mistral/Qwen chat-template tokens, bracket role markers, and common jailbreak preambles before any text enters a prompt
- Applied twice: once in `phase_scan()` before the Qwen prompt, and again in `phase_deep_research()` before the Claude Sonnet prompt (Qwen output is treated as untrusted)
- Each field is independently capped at `token_budget × 4` characters (default 8 000 chars); the whole items block is capped at the same budget
- `scan_targets` is a closed enum in the manifest schema — arbitrary filesystem paths cannot reach a prompt via that field

**Configuration:** Set `token_budget` in an agent manifest (100–8 000; default 2 000) to tighten limits for high-risk agents.

---

### 2. Malicious Manifest Files

**Vector:** A crafted `*.json` in `~/.dream_cycle/agents/` includes unexpected fields (prototype-pollution style), invalid types to cause runtime crashes, or a `mcp_endpoint` of `file:///etc/passwd` to cause local-file reads.

**Mitigation (`dream_cycle.py`):**
- `additionalProperties: false` in `MANIFEST_JSON_SCHEMA` — any field not in the schema causes rejection
- `scan_targets` is a closed enum (`arxiv`, `github_trending`, `cves`, `github_releases`) — no filesystem paths can be supplied here
- `mcp_endpoint` is validated by `_validate_mcp_endpoint()` — only `http://` and `https://` with a non-empty host are accepted; `file://`, `javascript:`, bare paths, and empty hosts are rejected
- Optional `checksum` field (SHA-256 hex of the file's raw bytes) — when present, a mismatch causes the manifest to be skipped
- All validation is non-fatal: bad manifests are logged and skipped, never crash the cycle

---

### 3. Windows Registry Source Injection

**Vector:** An attacker with write access to `HKCU\Software\DreamCycle\Agents` adds a registry entry pointing to a directory they control (e.g. a network share, a system directory, or a path containing traversal sequences).

**Mitigation (`dream_cycle.py`):**
- Only `REG_SZ` (type 1) values are accepted — `REG_EXPAND_SZ` is rejected to prevent environment-variable expansion to unexpected paths
- Values exceeding Windows `MAX_PATH` (260 chars) are rejected
- UNC paths (`\\server\share`) are rejected unconditionally
- Resolved paths must descend from `%USERPROFILE%` or `%APPDATA%` — elevation to `C:\Windows` or `C:\Program Files` is blocked
- Path traversal (`..` components after `Path.resolve()`) is rejected
- Registry-sourced manifests **must** include a `checksum` field — manifests without it are rejected, making it impossible to silently deploy an unsigned manifest via the registry

---

### 4. Namespace Isolation Violations

**Vector:** An agent (or MCP client) attempts to write to or read from another agent's memory namespace, or the ChromaDB collection contains a spoofed entry with a mismatched namespace tag.

**Mitigation (`dream_cycle.py` and `lumen_mcp_server.py`):**

**Write layer:**
- `_enforce_namespace()` validates every namespace string against `^[a-z][a-z0-9_]{0,62}$` before any ChromaDB write; invalid strings abort the write
- Every stored document includes `namespace_owner = agent_name` as a provenance metadata field

**Read / consolidation layer:**
- `_check_namespace_entry_integrity()` runs on every entry during the consolidation pass:
  1. Metadata `namespace` tag must match the queried namespace
  2. Document must be valid JSON with required keys (`run_id`, `tonight_score`, `date`)
  3. `tonight_score` must be numeric in `[0, 10]`
  4. `run_id` must not contain path-traversal characters
- Flagged entries are logged and excluded from the cross-namespace summary — never blindly merged

**Lumen MCP server:**
- `_validate_namespace()` rejects non-matching namespace strings with `400 Bad Request` on `add_memory`, `query_memory`, and `delete_memory`
- `delete_memory` enforces namespace ownership: if the stored entry's `namespace` field does not match the caller's declared `namespace`, the call returns `403 Forbidden`

---

### 5. Unauthenticated Lumen MCP Access

**Vector:** Any process on the same machine that can communicate with the Lumen MCP server reads or modifies agent memories without authorization.

**Mitigation (`lumen_mcp_server.py`):**
- `_bootstrap_token()` generates a 32-byte cryptographically random hex token (`secrets.token_hex(32)`) on first run and writes it to `~/.dream_cycle/.env` with `chmod 600`
- The token is loaded once at module level into `_SERVER_TOKEN`
- Every tool calls `_auth_error()` as its first line; if `_SERVER_TOKEN` is empty (env var not set when the server was spawned), the tool returns `{"error": "401 Unauthorized"}` immediately — no ChromaDB access occurs
- `.mcp.json` passes `LUMEN_MCP_TOKEN` from the shell environment to the server process via the MCP `env` block
- For stdio transport (the current default), the server is a child process of the MCP client — the primary security boundary is process isolation

**Bootstrap:**
```bash
# Generate / display the token
python3 lumen_mcp_server.py --bootstrap

# Add to shell profile for persistent export
echo 'source ~/.dream_cycle/.env && export LUMEN_MCP_TOKEN' >> ~/.bashrc
```

---

## Dependency Security

| Dependency | Role | Notes |
|------------|------|-------|
| `anthropic` | Claude API client | Pin to a specific version in production |
| `requests` | HTTP fetches (arXiv, GitHub, NVD) | TLS verification is on by default; never pass `verify=False` |
| `chromadb` | Vector database | Runs locally; no network exposure by default |
| `mcp` | MCP server framework | Required for Lumen only |
| `jsonschema` | Manifest validation | Falls back to manual checks if absent |
| `pyyaml` | config.yaml parsing | Use `yaml.safe_load()` only — never `yaml.load()` |

`requests` TLS: the codebase always uses the default `verify=True`. Do not override this.

`yaml.safe_load()` is used throughout — the unsafe `yaml.load()` is never called.

---

## Secure Configuration Checklist

- [ ] `ANTHROPIC_API_KEY` stored in a secrets manager or shell profile, not committed to git
- [ ] `LUMEN_MCP_TOKEN` exported in shell profile (`source ~/.dream_cycle/.env && export LUMEN_MCP_TOKEN`)
- [ ] `~/.dream_cycle/.env` is `chmod 600` (auto-set by `_bootstrap_token()`)
- [ ] `~/dream-cycle/chroma_db/` is not world-readable (`chmod 700 ~/dream-cycle/`)
- [ ] `config.json` does not contain API keys (it stores only model names and repo lists)
- [ ] On Windows: restrict write access to `HKCU\Software\DreamCycle\Agents` registry key
- [ ] For production: add a `checksum` field to every manifest file and verify it on deployment
- [ ] Review `~/dream-logs/YYYY-MM-DD-build-report.md` each morning — MEDIUM and HIGH risk staged actions require human sign-off

---

## Audit Log Locations

| Event | Log location |
|-------|-------------|
| Manifest validation errors | stdout (cron log) |
| Injection-defense strips | stdout with `[injection-defense]` prefix |
| Namespace violations | stdout with `[namespace-isolation]` prefix |
| Registry path rejections | stdout with `[registry-hardening]` prefix |
| Token auth failures | Tool return value `{"error": "401 Unauthorized"}` |
| Consolidation anomalies | stdout with `[namespace-isolation] FLAGGED` prefix |
| Staged action risk levels | `~/dream-logs/YYYY-MM-DD-build-report.md` |

---

## Development Guidelines

- **Never** call `yaml.load()` — always use `yaml.safe_load()`
- **Never** pass `verify=False` to any `requests` call
- **Never** interpolate un-sanitized external text directly into an LLM prompt — always call `sanitize_llm_input()` first
- **Never** write to a ChromaDB namespace without calling `_enforce_namespace()` first
- **Never** expand paths from manifests or registry values without resolving them and checking against an allowed-root list
- When adding a new tool to Lumen, add `_auth_error()` and `_validate_namespace()` calls as the first two lines
- When adding a new manifest field, add it to `MANIFEST_JSON_SCHEMA` with explicit type constraints before using it anywhere in the pipeline
