#!/bin/bash
# ==============================================================================
# Voiceover Studio Pro - Launch Script
# ==============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🎙️  Launching Voiceover Studio Pro..."

# Check Python virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip
    ./.venv/bin/pip install -r backend/requirements.txt
fi

# Activate venv and start server
export PORT="${PORT:-5055}"
echo "🚀 Starting server at http://127.0.0.1:$PORT"

# Open browser in background after 1.5s
(sleep 1.5 && open "http://127.0.0.1:$PORT") &

exec ./.venv/bin/python backend/server.py
