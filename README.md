An autonomous nightly research agent that scans AI research, reflects on its own performance, and stages self-improvements — all while you sleep.

Every night at 11:15 PM, a four-phase pipeline runs. By morning, there is a changelog on your desk.

---

## The Idea

Most agents are reactive. You ask, they answer. Dream Cycle runs whether you ask or not.

It watches arXiv, GitHub Trending, and CVE feeds. It reviews how it performed that day. It reads the most relevant papers in depth. It decides whether anything it found should change how it operates — and if so, stages the work for a 4 AM build job to apply.

The agent finds the research that makes the agent better at researching. That is not a metaphor.

Inspired by the Dan Simmons *Hyperion Cantos* series and the Reddit post ["My OpenClaw agent dreams at night"](https://www.reddit.com/r/better_claw/).

---

## Architecture

```
11:15 PM  Phase 1 — SCAN      Qwen 3.5 local    $0.00   ~10 min
11:25 PM  Phase 2 — REFLECT   Qwen 3.5 local    $0.00   ~5 min
11:30 PM  Phase 3 — RESEARCH  Claude Sonnet     ~$0.30  ~15 min
11:45 PM  Phase 4 — JUDGE     Claude Sonnet     ~$0.10  ~10 min
 4:00 AM  BUILD JOB           No LLM            $0.00   ~2 min
                                                ------
                                        TOTAL   ~$0.40/night
```

**Phase 1 — Scan**
Pulls from arXiv (AI/ML, CV, Security), GitHub Trending, and the NVD CVE feed. Scores each finding across your configured research tracks. Autonomously decides tonight's priority track based on what is freshest and most actionable.

**Phase 2 — Reflect**
Reviews today's agent performance log. Identifies patterns in task failures, model escalations, and routing decisions. Produces one concrete improvement suggestion per night.

**Phase 3 — Deep Research**
Takes the top 5 findings and goes deep using Claude Sonnet. Reads iteratively — a finding that builds on another finding gets followed further. Cross-references against your current stack and active projects.

**Phase 4 — Judge and Stage**
Decides what is worth acting on. Stages changes to `~/dream-cycle/dream-staging/` by risk level. Writes a rollback script for every staged action. Nothing touches live config directly.

**4 AM Build Job**
Auto-applies LOW risk changes only. Flags MEDIUM and HIGH for morning review. Writes a build report.

---

## Risk Levels

Every staged change is scored before anything runs.

| Level  | Behavior                        | Examples                                      |
|--------|---------------------------------|-----------------------------------------------|
| LOW    | Auto-applied at 4 AM            | Doc updates, model pulls, config tweaks       |
| MEDIUM | Staged for human review         | Workflow changes, new tool integrations       |
| HIGH   | Noted, never auto-applied       | Anything touching live systems                |

Every auto-applied change writes a `rollback_TIMESTAMP.sh` to `~/dream-cycle/dream-staging/applied/`. One command undoes any night's work.

---

## Model Routing

Dream Cycle uses a hybrid routing strategy to keep costs near zero.

- **Scan and Reflect** use a local Ollama model (Qwen 3.5 9B or 27B). No API cost, no rate limits, no data leaving your machine.
- **Deep Research and Judge** use Claude Sonnet. These phases require genuine multi-step reasoning. Pay for it only here.

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
```

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) with a local model pulled
- Anthropic API key (`ANTHROPIC_API_KEY` in environment)
- Ubuntu / macOS with cron available

```bash
pip install anthropic requests
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
dream-cycle/
  dream_cycle.py              Main orchestrator — runs at 11:15 PM
  build_job.py                4 AM build — applies low-risk staged changes
  perf_log.py                 Performance logger — call from your agents
  setup.sh                    One-time install and cron registration
  dream-staging/
    YYYY-MM-DD_manifest.json  Tonight's staged action manifest
    *.staged                  Individual staged actions (JSON)
    applied/
      rollback_*.sh           Rollback scripts for every applied change
  performance.jsonl           Agent event log — feeds reflection phase

~/dream-logs/
  YYYY-MM-DD-changelog.md
  YYYY-MM-DD-build-report.md
```

---

## Rollback

```bash
# List rollback scripts
ls ~/dream-cycle/dream-staging/applied/rollback_*.sh

# Undo a specific night
bash ~/dream-cycle/dream-staging/applied/rollback_20260330_040012_update_model_config.sh
```

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
