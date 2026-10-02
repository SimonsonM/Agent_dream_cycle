An autonomous nightly research agent that scans AI research, reflects on its own performance, and stages self-improvements — all while you sleep.

Every night at 11:15 PM, a five-phase pipeline runs. By morning, there is a changelog on your desk.

---

## The Idea

Most agents are reactive. You ask, they answer. Dream Cycle runs whether you ask or not.

It watches arXiv, GitHub Trending, and CVE feeds. It reviews how it performed that day. It reads the most relevant papers in depth. It decides whether anything it found should change how it operates — and if so, stages the work for a 4 AM build job to apply.

The agent finds the research that makes the agent better at researching. That is not a metaphor.

Inspired by the Dan Simmons *Hyperion Cantos* series and the Reddit post ["My OpenClaw agent dreams at night"](https://www.reddit.com/r/better_claw/).

---

## Architecture

```
11:15 PM  Phase 1 — SCAN      
11:25 PM  Phase 2 — REFLECT   
11:30 PM  Phase 3 — RESEARCH 
11:40 PM  Phase 4— EXPERIMENTATION  
11:45 PM  Phase 5 — JUDGE    
 4:00 AM  BUILD JOB  
```


**Phase 1 — Scan**
Pulls from arXiv (AI/ML, CV, Security), GitHub Trending, and the NVD CVE feed. Scores each finding across your configured research tracks. Autonomously decides tonight's priority track based on what is freshest and most actionable.

**Phase 2 — Reflect**
Reviews today's agent performance log. Identifies patterns in task failures, model escalations, and routing decisions. Produces one concrete improvement suggestion per night.

**Phase 3 — Deep Research**
Takes the top 5 findings and goes deep using Claude Sonnet. Reads iteratively — a finding that builds on another finding gets followed further. Cross-references against your current stack and active projects.

**Phase 3.5 — Experimentation**
Validates proposed prompt changes by replay, not by asking a model whether its own idea is good. A candidate instruction is run against the last several nights of real scan inputs (saved to `<AGENT>/replay/`), baseline vs candidate, and a Claude judge compares the outputs pairwise with randomized A/B order. A change passes only with at least 3 saved nights of input, a 60%+ win rate, and no drop in valid-JSON rate. Config, tool and workflow changes have no automated test: they score a neutral 0.5 and always go to human review. Until 3 nights of inputs exist, every experiment returns `insufficient_data`.

**Phase 4 — Judge and Stage**
Decides what is worth acting on and returns a run summary and a 0-10 `tonight_score` (the UCB1 reward). Every staged action's risk is re-derived by a deterministic policy (`policy.py`); the model's own risk label can only be raised, never lowered. Stages changes to `~/dream-cycle/<AGENT>/staging/`. Nothing touches live config directly.

**Phase 5 — Lessons**
Distills the run into structured lessons for the corpus. Skipped when the Judge failed, so bad runs do not pollute lessons or UCB1.

**4 AM Build Job**
Auto-applies LOW risk changes only, after re-checking policy at apply time. Flags MEDIUM and HIGH for morning review. Writes a build report.

## Risk Levels

Risk is decided by `policy.py`, not by the LLM. Effective risk = the higher of the model's declared risk and the policy result. The policy runs when changes are staged and again in the build job.

| Level  | Behavior                  | What qualifies                                                                 |
|--------|---------------------------|--------------------------------------------------------------------------------|
| LOW    | Auto-applied at 4 AM      | Docs (`.md/.txt/.rst`), parseable config (`.json/.yaml`) inside allowed dirs, plain `ollama pull <name>` |
| MEDIUM | Staged for human review   | Workflow changes, unparseable config, files with other suffixes                |
| HIGH   | Noted, never auto-applied | Scripts (always), unknown action types, protected or out-of-bounds paths, oversized content |

Protected paths (never auto-writable): `config.json`, `config.yaml`, `.mcp.json`, `.env`, anything under `staging/`, `chroma_db/`, `.git/`, `.ssh/`, `systemd/`.

Every auto-applied change writes a `rollback_TIMESTAMP_*.sh` to `~/dream-cycle/<AGENT>/staging/applied/`. Rollbacks are generated from structured data only and fully quoted; LLM-supplied shell is never executed. A rollback restores the timestamped `.bak` copy (or removes a newly created file) and logs a `reverted` record. Applied changes also record a SHA-256 of their content in `applied_changes.jsonl`.

---

## Model Routing

Dream Cycle uses a hybrid routing strategy to keep costs near zero.

- **Scan and Reflect** use a local Ollama model (Qwen 3.5 9B or 27B). No API cost, no rate limits, no data leaving your machine.
- **Deep Research and Judge** start local and escalate to Claude Sonnet only when needed. These phases require genuine multi-step reasoning.

**Structured output.** Phases that return JSON (scan, reflect, research, judge, lessons) go through `llm_json()`: Claude calls use forced tool-use, Ollama calls pass the JSON schema as `format`, and every result is validated with `jsonschema`. Invalid local output gets one repair retry, then escalates to Claude. Escalation is driven by validity, not by the model's self-reported confidence.

## Run Health

Failures are visible instead of silently producing empty output. Each phase is tracked as `ok`, `degraded` or `failed`. LLM failures and previously swallowed exceptions are counted. The Judge is skipped when there are no findings and no research. The email subject gets a `[DEGRADED]` or `[FAILED]` prefix with phase notes in the body, a `YYYY-MM-DD.health.json` is written next to the changelog, and the process exits with code 2 on failure so cron or systemd can alert on it.

Recommended Ollama models by hardware:

```bash
ollama pull qwen3.5:27b    # 20GB+ VRAM
ollama pull qwen3.5:9b     # 8-16GB VRAM / most laptops
```

---

## Research Tracks

The agent scans across all configured tracks every night and decides priority autonomously. Default tracks:

- AI/ML — models, agents, frameworks, tooling
- Cybersecurity — CVEs, threat intel, vulnerability research
- Robotics/CV — OpenCV, MediaPipe, embedded systems
- Data Analytics — pipelines, visualization, MLOps

Edit `TRACKS` in `dream_cycle.py` to match your work.

---

## Morning Deliverables

```
~/dream-logs/YYYY-MM-DD-changelog.md      Full research report, staged action summary
~/dream-logs/YYYY-MM-DD-build-report.md   What was applied, what needs your review
~/dream-logs/YYYY-MM-DD.mail             Gmail summary (if msmtp not configured)
<AGENT>/logs/YYYY-MM-DD.health.json       Per-phase status, LLM failure counts
```

---

---

## Agent Auto-Detection

Agents are loaded from two sources on every startup:

**Built-in profiles** — `security`, `marketing`, `programming`, `ai_research` compiled into `dream_cycle.py`.

**Manifest files** — any `*.json` file placed in a platform-appropriate directory:

| Platform | Directory |
|----------|-----------|
| Linux    | `~/.dream_cycle/agents/` |
| macOS    | `~/.dream_cycle/agents/` and `~/Library/Application Support/dream_cycle/agents/` |
| Windows  | `%APPDATA%\dream_cycle\agents\` (plus optional `HKCU\Software\DreamCycle\Agents` registry keys) |

Copy `agents/example_agent.json` to get started:

```json
{
  "id":               "my_agent",
  "name":             "My Custom Agent",
  "version":          "1.0.0",
  "type":             "research",
  "memory_namespace": "my_agent",
  "scan_targets":     ["arxiv", "github_trending"],
  "active":           true,
  "mcp_endpoint":     ""
}
```

Required fields: `id`, `name`, `version`, `type`, `memory_namespace`, `scan_targets`, `active`.

Setting `active: false` skips the agent at startup without deleting the file.  The `type` field maps the agent to the nearest built-in scanning profile (`research` → `ai_research`, `security`, `marketing`, `programming`).

```bash
# List all registered agents (built-in + manifest)
python3 ~/dream-cycle/dream_cycle.py --list-agents

# Run a manifest-registered agent
python3 ~/dream-cycle/dream_cycle.py --agent my_agent
```

---

## Lumen MCP Server

`lumen_mcp_server.py` exposes per-agent memory namespaces via FastMCP so Claude Code (and other MCP clients) can read and write agent memories without touching ChromaDB directly.

**Install:**
```bash
pip install mcp chromadb
```

**Register** (already done via `.mcp.json` — Claude Code picks it up automatically).

**Tools:**

| Tool | Description |
|------|-------------|
| `add_memory(content, namespace, tags[])` | Write a memory to a namespace |
| `query_memory(query, namespace, n)` | Cosine-similarity search within a namespace |
| `list_namespaces()` | List all namespaces with stored data |
| `delete_memory(id, namespace)` | Delete an entry (own namespace only — cross-namespace deletes are rejected) |

The backend is the `agent_memories` ChromaDB collection at `~/dream-cycle/chroma_db` — the same path used by the dream cycle itself.  Memories written during a nightly run are immediately queryable via MCP tools, and vice versa.

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) with a local model pulled
- Anthropic API key (`ANTHROPIC_API_KEY` in environment)
- Ubuntu / macOS / Windows with cron or Task Scheduler available

```bash
pip install anthropic requests          # required (plus jsonschema below)
pip install chromadb                    # optional — enables memory persistence
pip install mcp                         # optional — enables Lumen MCP server
pip install jsonschema                  # required — validates LLM JSON output
pip install pyyaml                      # optional — enables config.yaml features
pip install pytest                      # optional — run the tests
```

---

## Installation

```bash
git clone https://github.com/SimonsonM/dream-cycle
cd dream-cycle
bash setup.sh
```

`setup.sh` installs dependencies, creates directories, and registers both cron jobs. That is the entire setup.

**Test a full run immediately:**

```bash
python3 ~/dream-cycle/dream_cycle.py
```

---

## Feeding the Reflection Phase

Phase 2 is only as good as the data it has. Call `perf_log.py` from your agents, MCP servers, or shell scripts after tasks complete:

```bash
python3 ~/dream-cycle/perf_log.py \
  --task "summarize quarterly report" \
  --outcome success \
  --model qwen3.5:9b \
  --duration 6.2

python3 ~/dream-cycle/perf_log.py \
  --task "debug async race condition" \
  --outcome escalated \
  --model qwen3.5:9b \
  --note "routed to Claude, too complex for local"
```

Outcomes: `success` | `failed` | `escalated` | `timeout`

Even a few logged events per day gives the reflection phase something real to work with.

---

## File Structure

```
dream-cycle/                    (repo)
  dream_cycle.py                Main orchestrator — runs at 11:15 PM
  build_job.py                  4 AM build — applies low-risk staged changes
  policy.py                     Deterministic risk policy and path protection
  replay_eval.py                Replay A/B evaluation for prompt changes
  perf_log.py                   Performance logger — call from your agents
  set_up.sh                     One-time install and cron registration
  config.yaml                   Extended feature config (arXiv, decay, bridge...)
  lumen_mcp_server.py           MCP server — per-agent memory namespaces
  .mcp.json                     Registers Lumen with Claude Code
  agents/
    example_agent.json          Manifest schema reference (active: false)
  tests/                        pytest suite (policy, build job, llm_json, replay)
  ROADMAP.md                    Planned changes

~/dream-cycle/                  (runtime, created by set_up.sh)
  config.json                   Model selection and per-agent repo lists
  chroma_db/                    ChromaDB — lessons, run nodes, agent memories
  <AGENT>/
    staging/
      YYYY-MM-DD_manifest.json  Tonight's staged action manifest
      *.staged                  Individual staged actions (JSON)
      applied/
        rollback_*.sh           Rollback scripts for every applied change
    logs/
      YYYY-MM-DD-changelog.md   Morning research report
      YYYY-MM-DD.health.json    Run health
    replay/
      *_scan_inputs.json        Saved scan inputs for replay evals (last 14)
    performance.jsonl           Agent event log — feeds Phase 2
    seen_cache.json             Dedup cache (7-day TTL)

~/.dream_cycle/agents/          (manifest registry — add your own here)
  *.json                        Custom agent manifests

~/dream-logs/
  YYYY-MM-DD-changelog.md
  YYYY-MM-DD-build-report.md
```

---

## Rollback

```bash
# List rollback scripts for an agent
ls ~/dream-cycle/<AGENT>/staging/applied/rollback_*.sh

# Undo a specific change
bash ~/dream-cycle/<AGENT>/staging/applied/rollback_20260330_040012_update_model_config.sh
```

---

## Testing

```bash
pip install pytest jsonschema pyyaml anthropic requests python-dotenv chromadb
python3 -m pytest tests -q
```

Known issue: `tests/test_dream_cycle.py` and `tests/test_experimentation.py` fail at import (they reference functions that no longer exist) and several manifest tests fail on a schema mismatch. These predate the hardening work and are tracked in [ROADMAP.md](ROADMAP.md).

---

## Roadmap

See [ROADMAP.md](ROADMAP.md).

---

## Background

This project grew out of two things: a longstanding interest in agents that improve themselves over time, and a practical frustration with research debt — the gap between what is happening in AI and what your current tools actually reflect.

The four-phase structure was influenced by the ["My OpenClaw agent dreams at night"](https://www.reddit.com/r/better_claw/) post. The iterative depth improvement in Phase 3 — where the agent follows a finding further if it connects to another finding — came directly from a paper the dream cycle itself surfaced. That felt worth building.

The *Hyperion Cantos* connection is not accidental. If you have read the series, you know why.

---

## License

MIT

---

*Built by [Mike Simonson](https://linkedin.com/in/simonsonmba)*
