import { useState } from "react";

const PHASES = [
  {
    id: 1,
    time: "11:15 PM",
    name: "SCAN",
    model: "Qwen 3.5 local",
    cost: "~$0.00",
    color: "#00ff9d",
    duration: "~10 min",
    description: "Broad intelligence gathering across all four tracks. Agent autonomously decides tonight's priority.",
    sources: ["arXiv (AI/ML, CV, Security)", "GitHub Trending", "NVD CVE Feed"],
    output: "Scored findings list + priority track decision",
    icon: "◉",
  },
  {
    id: 2,
    time: "11:25 PM",
    name: "REFLECT",
    model: "Qwen 3.5 local",
    cost: "~$0.00",
    color: "#ff9d00",
    duration: "~5 min",
    description: "Reviews today's agent performance log. Identifies patterns in failures, escalations, and routing decisions.",
    sources: ["~/dream-cycle/performance.jsonl"],
    output: "Observations + improvement suggestion",
    icon: "◎",
  },
  {
    id: 3,
    time: "11:30 PM",
    name: "RESEARCH",
    model: "Claude Sonnet",
    cost: "~$0.30",
    color: "#ff4d6d",
    duration: "~15 min",
    description: "Deep iterative analysis of top findings. Cross-references against your current stack, active projects, and four tracks.",
    sources: ["Top 5 findings from Phase 1", "Iterative depth on connected papers"],
    output: "Deep summaries + applicability scores + change suggestions",
    icon: "⬡",
  },
  {
    id: 4,
    time: "11:45 PM",
    name: "JUDGE",
    model: "Claude Sonnet",
    cost: "~$0.10",
    color: "#9d4dff",
    duration: "~10 min",
    description: "Decides what's worth doing. Stages changes by risk level. Writes rollback scripts for everything.",
    sources: ["All previous phase outputs"],
    output: "Staged files + rollback scripts + tonight's score",
    icon: "▣",
  },
  {
    id: 5,
    time: "4:00 AM",
    name: "BUILD",
    model: "No LLM",
    cost: "$0.00",
    color: "#4daaff",
    duration: "~2 min",
    description: "Auto-applies LOW risk changes only. Flags MEDIUM/HIGH for your morning review. Nothing irreversible runs automatically.",
    sources: ["~/dream-cycle/dream-staging/*_manifest.json"],
    output: "Applied changes + build report + flagged items",
    icon: "⬢",
  },
];

const RISK_LEVELS = [
  { level: "LOW", color: "#00ff9d", emoji: "🟢", desc: "Auto-applied at 4 AM", examples: "Doc updates, model pulls, config tweaks" },
  { level: "MEDIUM", color: "#ff9d00", emoji: "🟡", desc: "Staged for your review", examples: "Workflow changes, new tool integrations" },
  { level: "HIGH", color: "#ff4d6d", emoji: "🔴", desc: "Noted, never auto-applied", examples: "Anything touching live systems" },
];

const TRACKS = [
  { name: "AI/ML", icon: "⬡", desc: "Models, agents, frameworks, MCP servers" },
  { name: "Cybersecurity", icon: "◉", desc: "CVEs, threat intel, Security+ relevance" },
  { name: "Robotics/CV", icon: "▣", desc: "OpenCV, MediaPipe, Raspberry Pi, rover" },
  { name: "Data Analytics", icon: "◎", desc: "Power BI, Fabric, MLflow, pipelines" },
];

// ── Built-in agents shown on the AGENTS tab ────────────────────────────────
const BUILTIN_AGENTS = [
  { id: "ai_research",  name: "AI Research Agent",         namespace: "ai_research",  type: "research",    builtin: true },
  { id: "security",     name: "Security Research Agent",   namespace: "security",     type: "security",    builtin: true },
  { id: "programming",  name: "Programming Intelligence",  namespace: "programming",  type: "programming", builtin: true },
  { id: "marketing",    name: "Marketing Intelligence",    namespace: "marketing",    type: "marketing",   builtin: true },
];

const MANIFEST_SCHEMA = [
  { field: "id",               required: true,  desc: "Unique agent identifier" },
  { field: "name",             required: true,  desc: "Human-readable display name" },
  { field: "version",          required: true,  desc: "Semver string (e.g. 1.0.0)" },
  { field: "type",             required: true,  desc: "research | security | marketing | programming" },
  { field: "memory_namespace", required: true,  desc: "Lumen ChromaDB namespace for this agent" },
  { field: "scan_targets",     required: true,  desc: "Array: arxiv | github_trending | cves | github_releases" },
  { field: "active",           required: true,  desc: "Boolean — false skips the agent at startup" },
  { field: "mcp_endpoint",     required: false, desc: "Optional custom MCP server URL" },
];

const FILES = [
  { name: "dream_cycle.py", role: "Main orchestrator — runs at 11:15 PM", path: "~/dream-cycle/" },
  { name: "build_job.py", role: "4 AM build — applies low-risk changes", path: "~/dream-cycle/" },
  { name: "perf_log.py", role: "Performance logger — call from your agents", path: "~/dream-cycle/" },
  { name: "setup.sh", role: "One-time install + cron registration", path: "~/dream-cycle/" },
  { name: "YYYY-MM-DD-changelog.md", role: "Morning briefing — full research report", path: "~/dream-logs/" },
  { name: "YYYY-MM-DD-build-report.md", role: "What was applied + what needs review", path: "~/dream-logs/" },
  { name: "rollback_*.sh", role: "Undo any night's changes, one command", path: "~/dream-cycle/dream-staging/applied/" },
  { name: "performance.jsonl", role: "Agent event log — feeds reflection phase", path: "~/dream-cycle/" },
];

const COMMANDS = [
  { label: "Install", cmd: "bash ~/dream-cycle/setup.sh" },
  { label: "Test run now", cmd: "python3 ~/dream-cycle/dream_cycle.py" },
  { label: "View today's changelog", cmd: "cat ~/dream-logs/$(date +%Y-%m-%d)-changelog.md" },
  { label: "View staged actions", cmd: "ls ~/dream-cycle/dream-staging/*.staged" },
  { label: "List rollback scripts", cmd: "ls ~/dream-cycle/dream-staging/applied/rollback_*.sh" },
  { label: "Log an agent task", cmd: "python3 ~/dream-cycle/perf_log.py --task 'summarize file' --outcome success --model qwen3.5:9b" },
  { label: "Check cron jobs", cmd: "crontab -l | grep dream" },
];

export default function DreamCycle() {
  const [activePhase, setActivePhase] = useState(null);
  const [activeTab, setActiveTab] = useState("phases");
  const [copiedCmd, setCopiedCmd] = useState(null);

  const copyCmd = (cmd, i) => {
    navigator.clipboard.writeText(cmd);
    setCopiedCmd(i);
    setTimeout(() => setCopiedCmd(null), 1500);
  };

  const phase = activePhase !== null ? PHASES[activePhase] : null;

  return (
    <div style={{
      minHeight: "100vh",
      background: "#0a0a0f",
      color: "#c8c8d4",
      fontFamily: "'Courier New', 'Lucida Console', monospace",
      padding: "0",
      position: "relative",
      overflow: "hidden",
    }}>
      {/* Grid background */}
      <div style={{
        position: "fixed", inset: 0, opacity: 0.04,
        backgroundImage: "linear-gradient(#4daaff 1px, transparent 1px), linear-gradient(90deg, #4daaff 1px, transparent 1px)",
        backgroundSize: "40px 40px",
        pointerEvents: "none",
      }} />

      {/* Scan line effect */}
      <div style={{
        position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0,
        background: "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.03) 2px, rgba(0,0,0,0.03) 4px)",
      }} />

      <div style={{ position: "relative", zIndex: 1, maxWidth: 900, margin: "0 auto", padding: "32px 20px" }}>

        {/* Header */}
        <div style={{ marginBottom: 40 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 6 }}>
            <span style={{ fontSize: 11, color: "#4daaff", letterSpacing: 4, textTransform: "uppercase" }}>SYS://DREAM_CYCLE</span>
            <span style={{ fontSize: 11, color: "#333" }}>v1.0</span>
          </div>
          <h1 style={{
            fontSize: 36, fontWeight: 900, margin: 0, letterSpacing: -1,
            background: "linear-gradient(135deg, #ffffff 0%, #4daaff 60%, #9d4dff 100%)",
            WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
            lineHeight: 1.1,
          }}>
            NIGHTLY DREAM CYCLE
          </h1>
          <p style={{ margin: "10px 0 0", color: "#666", fontSize: 13, letterSpacing: 1 }}>
            AUTONOMOUS SELF-IMPROVING AGENT — 4-PHASE NIGHTLY RESEARCH LOOP
          </p>
          <div style={{ display: "flex", gap: 20, marginTop: 16 }}>
            {[
              { label: "COST/NIGHT", val: "~$0.40" },
              { label: "PHASES", val: "4 + BUILD" },
              { label: "AUTONOMY", val: "FULL + ROLLBACK" },
              { label: "TRACKS", val: "4" },
            ].map(s => (
              <div key={s.label} style={{ textAlign: "center" }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: "#fff" }}>{s.val}</div>
                <div style={{ fontSize: 9, color: "#444", letterSpacing: 2 }}>{s.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Tabs */}
        <div style={{ display: "flex", gap: 2, marginBottom: 24, borderBottom: "1px solid #1a1a2e" }}>
          {["phases", "agents", "tracks", "risk", "files", "commands"].map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)} style={{
              background: activeTab === tab ? "#0d0d1a" : "transparent",
              border: "none", borderBottom: activeTab === tab ? "2px solid #4daaff" : "2px solid transparent",
              color: activeTab === tab ? "#4daaff" : "#444",
              padding: "8px 16px", cursor: "pointer", fontSize: 11, letterSpacing: 2,
              textTransform: "uppercase", fontFamily: "inherit", transition: "all 0.15s",
            }}>
              {tab}
            </button>
          ))}
        </div>

        {/* PHASES TAB */}
        {activeTab === "phases" && (
          <div>
            {/* Timeline */}
            <div style={{ position: "relative", marginBottom: 32 }}>
              <div style={{ position: "absolute", left: 28, top: 20, bottom: 20, width: 1, background: "#1a1a2e" }} />
              {PHASES.map((p, i) => (
                <div key={p.id} onClick={() => setActivePhase(activePhase === i ? null : i)}
                  style={{
                    display: "flex", alignItems: "flex-start", gap: 20, marginBottom: 4,
                    cursor: "pointer", padding: "12px 12px 12px 0",
                    background: activePhase === i ? "rgba(255,255,255,0.02)" : "transparent",
                    borderRadius: 4, transition: "background 0.15s",
                  }}>
                  <div style={{
                    width: 56, height: 56, borderRadius: 4,
                    border: `1px solid ${p.color}22`,
                    background: activePhase === i ? `${p.color}12` : "#0d0d1a",
                    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                    flexShrink: 0, transition: "all 0.15s",
                  }}>
                    <span style={{ fontSize: 18, color: p.color }}>{p.icon}</span>
                    <span style={{ fontSize: 8, color: p.color, letterSpacing: 1, opacity: 0.7 }}>{p.id === 5 ? "4AM" : `${i + 1}`}</span>
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 3 }}>
                      <span style={{ fontSize: 14, fontWeight: 700, color: p.color, letterSpacing: 2 }}>{p.name}</span>
                      <span style={{ fontSize: 10, color: "#444" }}>{p.time}</span>
                      <span style={{ fontSize: 10, color: "#333" }}>{p.duration}</span>
                      <span style={{ marginLeft: "auto", fontSize: 10, color: p.cost === "$0.00" ? "#00ff9d" : "#ff9d00" }}>{p.cost}</span>
                    </div>
                    <div style={{ fontSize: 11, color: "#888", lineHeight: 1.5 }}>{p.description}</div>

                    {activePhase === i && (
                      <div style={{ marginTop: 14, padding: 14, background: "#0d0d1a", borderRadius: 4, border: `1px solid ${p.color}22` }}>
                        <div style={{ marginBottom: 10 }}>
                          <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 6 }}>SOURCES</div>
                          {p.sources.map(s => (
                            <div key={s} style={{ fontSize: 11, color: "#666", marginBottom: 3 }}>
                              <span style={{ color: p.color, marginRight: 8 }}>→</span>{s}
                            </div>
                          ))}
                        </div>
                        <div style={{ marginBottom: 10 }}>
                          <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 6 }}>MODEL</div>
                          <span style={{ fontSize: 11, color: "#fff", background: `${p.color}18`, padding: "2px 8px", borderRadius: 2 }}>{p.model}</span>
                        </div>
                        <div>
                          <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 6 }}>OUTPUT</div>
                          <div style={{ fontSize: 11, color: "#aaa" }}>{p.output}</div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Cost breakdown */}
            <div style={{ padding: 16, background: "#0d0d1a", border: "1px solid #1a1a2e", borderRadius: 4 }}>
              <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 10 }}>NIGHTLY COST BREAKDOWN</div>
              <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
                {[
                  { label: "Scan + Reflect", model: "Qwen local", cost: "$0.00" },
                  { label: "Deep Research", model: "Claude Sonnet", cost: "~$0.30" },
                  { label: "Judge + Stage", model: "Claude Sonnet", cost: "~$0.10" },
                  { label: "Build Job", model: "No LLM", cost: "$0.00" },
                  { label: "TOTAL", model: "", cost: "~$0.40" },
                ].map(c => (
                  <div key={c.label}>
                    <div style={{ fontSize: 10, color: "#555" }}>{c.label}</div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: c.label === "TOTAL" ? "#4daaff" : "#fff" }}>{c.cost}</div>
                    {c.model && <div style={{ fontSize: 9, color: "#333" }}>{c.model}</div>}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* AGENTS TAB */}
        {activeTab === "agents" && (
          <div>
            <div style={{ fontSize: 11, color: "#555", marginBottom: 20, lineHeight: 1.6 }}>
              Agents are discovered at startup from two sources: built-in profiles compiled into the
              orchestrator, and manifest files placed in <code style={{ color: "#4daaff", background: "#0a0a1a", padding: "1px 4px" }}>~/.dream_cycle/agents/*.json</code>.
              Each agent writes its nightly output to an isolated memory namespace in ChromaDB,
              accessible via the Lumen MCP server.
            </div>

            {/* Built-in agents */}
            <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 10 }}>BUILT-IN AGENTS</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 24 }}>
              {BUILTIN_AGENTS.map(a => (
                <div key={a.id} style={{ padding: 16, background: "#0d0d1a", border: "1px solid #1a1a2e", borderRadius: 4 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: 9, padding: "2px 6px", background: "#00ff9d18", color: "#00ff9d", borderRadius: 2, letterSpacing: 1 }}>BUILT-IN</span>
                    <span style={{ fontSize: 12, color: "#fff", fontWeight: 700 }}>{a.id}</span>
                  </div>
                  <div style={{ fontSize: 11, color: "#888", marginBottom: 4 }}>{a.name}</div>
                  <div style={{ fontSize: 10, color: "#444" }}>
                    namespace: <span style={{ color: "#4daaff" }}>{a.namespace}</span>
                  </div>
                </div>
              ))}
            </div>

            {/* Manifest-registered agents */}
            <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 10 }}>MANIFEST-REGISTERED AGENTS</div>
            <div style={{ padding: 16, background: "#0d0d1a", border: "1px solid #1a1a2e", borderRadius: 4, marginBottom: 24 }}>
              <div style={{ fontSize: 11, color: "#666", lineHeight: 1.6, marginBottom: 12 }}>
                Drop a <code style={{ color: "#4daaff", background: "#0a0a1a", padding: "1px 4px" }}>*.json</code> file
                into <code style={{ color: "#4daaff", background: "#0a0a1a", padding: "1px 4px" }}>~/.dream_cycle/agents/</code> (Linux/macOS)
                or <code style={{ color: "#4daaff", background: "#0a0a1a", padding: "1px 4px" }}>%APPDATA%\dream_cycle\agents\</code> (Windows).
                The cycle picks it up on next startup — no code changes required.
              </div>
              <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 8 }}>MANIFEST SCHEMA</div>
              {MANIFEST_SCHEMA.map(s => (
                <div key={s.field} style={{ display: "flex", gap: 12, padding: "5px 0", borderBottom: "1px solid #111" }}>
                  <code style={{ width: 160, fontSize: 11, color: s.required ? "#4daaff" : "#555", flexShrink: 0 }}>{s.field}</code>
                  <span style={{ fontSize: 10, color: "#333", width: 60, flexShrink: 0 }}>{s.required ? "required" : "optional"}</span>
                  <span style={{ fontSize: 11, color: "#666" }}>{s.desc}</span>
                </div>
              ))}
            </div>

            {/* Lumen MCP */}
            <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 10 }}>LUMEN MCP SERVER</div>
            <div style={{ padding: 16, background: "#0d0d1a", border: "1px solid #9d4dff22", borderLeft: "3px solid #9d4dff", borderRadius: 4 }}>
              <div style={{ fontSize: 11, color: "#888", marginBottom: 12, lineHeight: 1.6 }}>
                <code style={{ color: "#9d4dff", background: "#0a0a1a", padding: "1px 4px" }}>lumen_mcp_server.py</code> exposes
                the same ChromaDB collection as MCP tools. Claude Code connects via <code style={{ color: "#9d4dff", background: "#0a0a1a", padding: "1px 4px" }}>.mcp.json</code>.
              </div>
              {[
                { tool: "add_memory(content, namespace, tags[])", desc: "Write a memory to a namespace" },
                { tool: "query_memory(query, namespace, n)",      desc: "Cosine-similarity search within a namespace" },
                { tool: "list_namespaces()",                      desc: "Enumerate all namespaces with stored data" },
                { tool: "delete_memory(id, namespace)",           desc: "Delete own-namespace entries only (trust rule)" },
              ].map(t => (
                <div key={t.tool} style={{ display: "flex", gap: 12, padding: "6px 0", borderBottom: "1px solid #111" }}>
                  <code style={{ fontSize: 10, color: "#9d4dff", width: 320, flexShrink: 0 }}>{t.tool}</code>
                  <span style={{ fontSize: 11, color: "#666" }}>{t.desc}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* TRACKS TAB */}
        {activeTab === "tracks" && (
          <div>
            <div style={{ fontSize: 11, color: "#555", marginBottom: 20, lineHeight: 1.6 }}>
              The agent decides tonight's priority track autonomously based on what's freshest and most actionable across all feeds.
              All four tracks are always scanned — the priority track gets deeper research time.
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              {TRACKS.map(t => (
                <div key={t.name} style={{ padding: 20, background: "#0d0d1a", border: "1px solid #1a1a2e", borderRadius: 4 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                    <span style={{ fontSize: 20, color: "#4daaff" }}>{t.icon}</span>
                    <span style={{ fontSize: 14, fontWeight: 700, color: "#fff", letterSpacing: 1 }}>{t.name}</span>
                  </div>
                  <div style={{ fontSize: 11, color: "#666" }}>{t.desc}</div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 16, padding: 16, background: "#0d0d1a", border: "1px solid #1a1a2e", borderRadius: 4 }}>
              <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 8 }}>ROBOTICS TRACK NOTE</div>
              <div style={{ fontSize: 11, color: "#666", lineHeight: 1.6 }}>
                Once the Spark Labs contract comes through and the rover build starts, the Robotics/CV track will get heavier. 
                The dream cycle will automatically surface relevant OpenCV, MediaPipe, and ROS papers — 
                you'll wake up to "here's what changed in your field" without having to ask.
              </div>
            </div>
          </div>
        )}

        {/* RISK TAB */}
        {activeTab === "risk" && (
          <div>
            <div style={{ fontSize: 11, color: "#555", marginBottom: 20, lineHeight: 1.6 }}>
              Every staged change gets a risk score. The 4 AM build job auto-applies LOW risk only.
              MEDIUM and HIGH are flagged in your morning build report for human review.
              Every applied change has a rollback script.
            </div>
            {RISK_LEVELS.map(r => (
              <div key={r.level} style={{
                padding: 20, background: "#0d0d1a", border: `1px solid ${r.color}22`,
                borderLeft: `3px solid ${r.color}`, borderRadius: 4, marginBottom: 10,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                  <span>{r.emoji}</span>
                  <span style={{ fontSize: 14, fontWeight: 700, color: r.color, letterSpacing: 2 }}>{r.level}</span>
                  <span style={{ fontSize: 11, color: "#666" }}>{r.desc}</span>
                </div>
                <div style={{ fontSize: 11, color: "#555" }}>Examples: {r.examples}</div>
              </div>
            ))}
            <div style={{ padding: 16, background: "#0d0d1a", border: "1px solid #1a1a2e", borderRadius: 4, marginTop: 8 }}>
              <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 8 }}>ROLLBACK GUARANTEE</div>
              <div style={{ fontSize: 11, color: "#666", lineHeight: 1.6 }}>
                Every auto-applied change writes a <code style={{ color: "#4daaff", background: "#0a0a1a", padding: "1px 4px" }}>rollback_TIMESTAMP.sh</code> to 
                <code style={{ color: "#4daaff", background: "#0a0a1a", padding: "1px 4px" }}>~/dream-cycle/dream-staging/applied/</code>. 
                One bash command undoes any night's work entirely. Backup files (.bak) are kept for config changes.
              </div>
            </div>
          </div>
        )}

        {/* FILES TAB */}
        {activeTab === "files" && (
          <div>
            {FILES.map(f => (
              <div key={f.name} style={{
                display: "flex", alignItems: "flex-start", gap: 16, padding: "12px 0",
                borderBottom: "1px solid #111",
              }}>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#4daaff", marginTop: 4, flexShrink: 0 }} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, color: "#4daaff", fontWeight: 700, marginBottom: 2 }}>{f.name}</div>
                  <div style={{ fontSize: 11, color: "#666", marginBottom: 2 }}>{f.role}</div>
                  <div style={{ fontSize: 10, color: "#333" }}>{f.path}{f.name}</div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* COMMANDS TAB */}
        {activeTab === "commands" && (
          <div>
            <div style={{ fontSize: 11, color: "#555", marginBottom: 20 }}>
              Click any command to copy to clipboard.
            </div>
            {COMMANDS.map((c, i) => (
              <div key={i} onClick={() => copyCmd(c.cmd, i)} style={{
                padding: "12px 16px", background: "#0d0d1a",
                border: `1px solid ${copiedCmd === i ? "#00ff9d" : "#1a1a2e"}`,
                borderRadius: 4, marginBottom: 6, cursor: "pointer", transition: "border-color 0.2s",
              }}>
                <div style={{ fontSize: 9, color: "#444", letterSpacing: 2, marginBottom: 4 }}>{c.label}</div>
                <code style={{ fontSize: 11, color: copiedCmd === i ? "#00ff9d" : "#aaa" }}>{c.cmd}</code>
                {copiedCmd === i && <span style={{ fontSize: 9, color: "#00ff9d", marginLeft: 12, letterSpacing: 1 }}>COPIED</span>}
              </div>
            ))}
          </div>
        )}

        {/* Footer */}
        <div style={{ marginTop: 40, paddingTop: 16, borderTop: "1px solid #111", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 9, color: "#333", letterSpacing: 2 }}>DREAM_CYCLE v1.0 — AUTONOMOUS SELF-IMPROVEMENT</span>
          <span style={{ fontSize: 9, color: "#333", letterSpacing: 1 }}>inspired by Johnny 5 // Hyperion Cantos</span>
        </div>

      </div>
    </div>
  );
}
