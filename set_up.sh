#!/bin/bash
# Dream Cycle Setup Script
# Run once to install dependencies and register cron jobs

set -e

DREAM_DIR="$HOME/dream-cycle"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Dream Cycle Setup ==="

# ── Dependencies ───────────────────────────────────────────────────────────────
echo "[1/4] Installing Python dependencies..."
pip install anthropic requests --break-system-packages --quiet

# ── Directories ────────────────────────────────────────────────────────────────
echo "[2/4] Creating directories..."
mkdir -p "$HOME/dream-cycle/dream-staging/applied"
mkdir -p "$HOME/dream-logs"

# ── Copy scripts ───────────────────────────────────────────────────────────────
echo "[3/4] Installing scripts to $DREAM_DIR..."
cp "$SCRIPT_DIR/dream_cycle.py" "$DREAM_DIR/"
cp "$SCRIPT_DIR/build_job.py" "$DREAM_DIR/"
cp "$SCRIPT_DIR/perf_log.py" "$DREAM_DIR/"
chmod +x "$DREAM_DIR/dream_cycle.py"
chmod +x "$DREAM_DIR/build_job.py"
chmod +x "$DREAM_DIR/perf_log.py"

# ── Cron jobs ──────────────────────────────────────────────────────────────────
echo "[4/4] Installing cron jobs..."

# Remove existing dream cycle crons to avoid duplicates
TEMP_CRON=$(mktemp)
crontab -l 2>/dev/null | grep -v "dream_cycle\|build_job" > "$TEMP_CRON" || true

# Add new cron jobs
cat >> "$TEMP_CRON" << EOF

# Dream Cycle — nightly research and self-improvement
15 23 * * * cd $DREAM_DIR && /usr/bin/python3 dream_cycle.py >> $HOME/dream-logs/dream_cycle.log 2>&1

# Dream Cycle — 4 AM build job (auto-applies low-risk staged changes)
0 4 * * * cd $DREAM_DIR && /usr/bin/python3 build_job.py >> $HOME/dream-logs/build_job.log 2>&1
EOF

crontab "$TEMP_CRON"
rm "$TEMP_CRON"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Cron jobs installed:"
echo "  11:15 PM — dream_cycle.py (scan, reflect, research, stage)"
echo "  4:00 AM  — build_job.py  (auto-apply low-risk, flag rest)"
echo ""
echo "Morning deliverables:"
echo "  ~/dream-logs/YYYY-MM-DD-changelog.md"
echo "  ~/dream-logs/YYYY-MM-DD-build-report.md"
echo ""
echo "Rollback any night's changes:"
echo "  ls ~/dream-cycle/dream-staging/applied/rollback_*.sh"
echo "  bash ~/dream-cycle/dream-staging/applied/rollback_TIMESTAMP_name.sh"
echo ""
echo "Log agent tasks for reflection phase:"
echo "  python3 ~/dream-cycle/perf_log.py --task 'summarize file' --outcome success --model qwen3.5:9b"
echo ""
echo "Test run (runs full cycle now):"
echo "  python3 ~/dream-cycle/dream_cycle.py"
