#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "Installing STRIX Telegram Bot for macOS (launchd)..."

# 1. Copy .env.example if .env_bot doesn't exist
if [ ! -f "$REPO_DIR/.env_bot" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env_bot"
    echo "Created .env_bot — edit it with your token before running."
fi

# 2. Install launchd plist
PLIST_SRC="$REPO_DIR/com.strix.telegram-bot.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.strix.telegram-bot.plist"

if [ -f "$PLIST_SRC" ]; then
    cp "$PLIST_SRC" "$PLIST_DST"
    echo "Plist copied to $PLIST_DST"
else
    echo "WARNING: com.strix.telegram-bot.plist not found — create it manually."
fi

# 3. Create strix_runs directory
mkdir -p "$REPO_DIR/strix_runs"

# 4. Validate Python version
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    echo "ERROR: python3 not found. Install Python 3.12+."
    exit 1
fi

echo ""
echo "Installation complete."
echo ""
echo "To start the bot:"
echo "  launchctl bootstrap gui/501 $PLIST_DST"
echo ""
echo "To check status:"
echo "  launchctl list | grep strix"
echo ""
echo "To view logs:"
echo "  tail -f $REPO_DIR/logs/strix_bot.log"
echo ""
echo "Don't forget to edit .env_bot with your Telegram token."
