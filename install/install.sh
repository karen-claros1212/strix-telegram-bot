#!/usr/bin/env bash
set -euo pipefail

# STRIX Telegram Bot — cross-platform installer
# Detects OS and delegates to the platform-specific installer.

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BOT_DIR="${STRIX_BOT_DIR:-$HOME/.strix/telegram-bot}"
STRIX_BIN="${STRIX_BIN:-$(command -v strix || true)}"

echo "STRIX Telegram Bot Installer"
echo "Repo dir: $REPO_DIR"
echo "Install target: $BOT_DIR"

# Detect OS
case "$(uname -s)" in
    Darwin)
        echo "Detected: macOS"
        exec "$REPO_DIR/install/install-macos.sh" "$@"
        ;;
    Linux)
        if grep -qi microsoft /proc/version 2>/dev/null; then
            echo "Detected: WSL/Ubuntu"
            exec "$REPO_DIR/install/install-wsl.sh" "$@"
        else
            echo "Detected: Linux"
            exec "$REPO_DIR/install/install-linux.sh" "$@"
        fi
        ;;
    *)
        echo "Unsupported OS: $(uname -s)"
        echo "Manual install:"
        echo "  1. cp .env.example .env_bot && edit token"
        echo "  2. python3 -m strix_telegram_bot"
        exit 1
        ;;
esac
