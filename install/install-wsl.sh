#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WSL_DETECTED=$(grep -qi microsoft /proc/version 2>/dev/null && echo "yes" || echo "no")

echo "Installing STRIX Telegram Bot for WSL/Ubuntu..."
echo "WSL detected: $WSL_DETECTED"

if [ "$WSL_DETECTED" != "yes" ]; then
    echo "WARNING: Not running under WSL. This script is for WSL/Ubuntu."
    echo "If you're on native Linux, use install-linux.sh instead."
fi

# 1. Copy .env.example if .env_bot doesn't exist
if [ ! -f "$REPO_DIR/.env_bot" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env_bot"
    echo "Created .env_bot — edit it with your token before running."
fi

# 2. Try systemd (WSL2 with systemd enabled)
SERVICE_NAME="strix-telegram-bot"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if command -v systemctl &>/dev/null && systemctl --version &>/dev/null; then
    cat > /tmp/strix-telegram-bot.service << EOF
[Unit]
Description=STRIX Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
ExecStart=/usr/bin/python3 -m strix_telegram_bot
Restart=always
RestartSec=10
User=$(whoami)
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

    if command -v sudo &>/dev/null; then
        sudo mv /tmp/strix-telegram-bot.service "$SERVICE_PATH"
        sudo systemctl daemon-reload
        echo "Systemd service installed."
    else
        echo "WARNING: sudo not available. Service file at /tmp/strix-telegram-bot.service"
    fi
else
    echo "Systemd not available. Using screen/tmux wrapper instead."
    echo ""
    echo "Run the bot manually:"
    echo "  cd $REPO_DIR && python3 -m strix_telegram_bot"
    echo ""
    echo "Or use tmux:"
    echo "  tmux new-session -d -s strix-bot 'cd $REPO_DIR && python3 -m strix_telegram_bot'"
fi

# 3. Create strix_runs directory
mkdir -p "$REPO_DIR/strix_runs"

echo ""
echo "Don't forget to edit .env_bot with your Telegram token."
