#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "Installing STRIX Telegram Bot for Linux (systemd)..."

# 1. Copy .env.example if .env_bot doesn't exist
if [ ! -f "$REPO_DIR/.env_bot" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env_bot"
    echo "Created .env_bot — edit it with your token before running."
fi

# 2. Create systemd service
SERVICE_NAME="strix-telegram-bot"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

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

# Use sudo to install systemd service
if command -v sudo &>/dev/null; then
    sudo mv /tmp/strix-telegram-bot.service "$SERVICE_PATH"
    sudo systemctl daemon-reload
    echo "Service installed at $SERVICE_PATH"
else
    echo "WARNING: sudo not available. Service file at /tmp/strix-telegram-bot.service"
    echo "Install manually: sudo mv ... && sudo systemctl daemon-reload"
fi

# 3. Create strix_runs directory
mkdir -p "$REPO_DIR/strix_runs"

echo ""
echo "Installation complete."
echo ""
echo "To start the bot:"
echo "  sudo systemctl enable $SERVICE_NAME"
echo "  sudo systemctl start $SERVICE_NAME"
echo ""
echo "To view logs:"
echo "  journalctl -u $SERVICE_NAME -f"
echo ""
echo "Don't forget to edit .env_bot with your Telegram token."
