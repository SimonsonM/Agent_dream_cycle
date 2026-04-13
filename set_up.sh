#!/bin/bash
# Dream Cycle Setup Script — Multi-Agent Edition
# Run once to install dependencies and register cron jobs.

set -e

DREAM_DIR="$HOME/dream-cycle"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Dream Cycle Setup ==="

# ── Dependencies ───────────────────────────────────────────────────────────────
echo "[1/4] Installing Python dependencies..."
pip install anthropic requests --break-system-packages --quiet

# ── Directories ────────────────────────────────────────────────────────────────
echo "[2/4] Creating directories..."
mkdir -p "$HOME/dream-logs"
for agent in security marketing programming ai_research; do
    mkdir -p "$HOME/dream-cycle/$agent/staging/applied"
    mkdir -p "$HOME/dream-cycle/$agent/logs"
done

# ── Copy scripts ───────────────────────────────────────────────────────────────
echo "[3/4] Installing scripts to $DREAM_DIR..."
cp "$SCRIPT_DIR/dream_cycle.py" "$DREAM_DIR/"
cp "$SCRIPT_DIR/build_job.py"   "$DREAM_DIR/"
cp "$SCRIPT_DIR/perf_log.py"    "$DREAM_DIR/"
chmod +x "$DREAM_DIR/dream_cycle.py"
chmod +x "$DREAM_DIR/build_job.py"
chmod +x "$DREAM_DIR/perf_log.py"

# ── Cron jobs ──────────────────────────────────────────────────────────────────
echo "[4/4] Installing cron jobs..."
TEMP_CRON=$(mktemp)
crontab -l 2>/dev/null | grep -v "dream_cycle\|build_job" > "$TEMP_CRON" || true

# Default: runs ai_research. Duplicate + stagger 20+ min per extra agent.
cat >> "$TEMP_CRON" << EOF

# Dream Cycle — nightly research (default: ai_research)
15 23 * * * cd $DREAM_DIR && /usr/bin/python3 dream_cycle.py --agent ai_research >> $HOME/dream-logs/ai_research.log 2>&1

# Dream Cycle — 4 AM build job (processes all agents automatically)
0 4 * * * cd $DREAM_DIR && /usr/bin/python3 build_job.py >> $HOME/dream-logs/build_job.log 2>&1
EOF

crontab "$TEMP_CRON"
rm "$TEMP_CRON"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Available agents:  security | marketing | programming | ai_research"
echo ""
echo "Run an agent:"
echo "  python3 ~/dream-cycle/dream_cycle.py --agent security"
echo "  python3 ~/dream-cycle/dream_cycle.py --agent marketing"
echo "  python3 ~/dream-cycle/dream_cycle.py --agent programming"
echo "  python3 ~/dream-cycle/dream_cycle.py --agent ai_research"
echo ""
echo "List all agents:"
echo "  python3 ~/dream-cycle/dream_cycle.py --list-agents"
echo ""
echo "Reconfigure an agent's GitHub repos:"
echo "  python3 ~/dream-cycle/dream_cycle.py --agent security --reconfigure"
echo ""
echo "Add more agents to cron (stagger by 20+ min to avoid API rate limits):"
echo "  35 23 * * * cd $DREAM_DIR && python3 dream_cycle.py --agent security >> ~/dream-logs/security.log 2>&1"
echo "  55 23 * * * cd $DREAM_DIR && python3 dream_cycle.py --agent marketing >> ~/dream-logs/marketing.log 2>&1"
echo ""
echo "Log a task for an agent:"
echo "  python3 ~/dream-cycle/perf_log.py --agent security --task 'vuln scan' --outcome success --model qwen2.5:7b"
echo ""
echo "Run build job for one agent:"
echo "  python3 ~/dream-cycle/build_job.py --agent security"
echo ""
echo "Morning output:"
echo "  ~/dream-cycle/AGENT/logs/YYYY-MM-DD-changelog.md"
echo "  ~/dream-logs/YYYY-MM-DD-build-report.md"
