# Deployment Guide

## Requirements

- Python 3.12+
- [Strix CLI](https://github.com/usestrix/strix-agent) installed in PATH
  (pinned to `strix-agent==1.6.2` — the stable baseline)
- Docker (Colima or Docker Desktop)
- Telegram Bot Token from [@BotFather](https://t.me/botfather)

## 1. Install

```bash
git clone https://github.com/karen-claros1212/strix-telegram-bot.git
cd strix-telegram-bot
pip install -e .
```

## 2. Configure

```bash
cp .env.example .env_bot
nano .env_bot
```

Required variables:

| Variable | Example | Description |
|----------|---------|-------------|
| `STRIX_TG_TOKEN` | `891751...` | Bot token from @BotFather |
| `STRIX_TG_ALLOWED_USERS` | `8166253211` | Your Telegram user ID (get from @userinfobot) |
| `STRIX_LLM` | `deepseek/deepseek-v4-pro` | LLM model for scans |
| `LLM_API_KEY` | `sk-...` | API key for the LLM provider |

Optional but recommended:

| Variable | Default | Description |
|----------|---------|-------------|
| `STRIX_JOB_TIMEOUT_SECONDS` | 7200 | Max job duration (2 hours) |
| `STRIX_MAX_CONCURRENT_JOBS` | 3 | Max simultaneous scans |

> **Note:** The bot loads `.env_bot` automatically inside Python. No need to source it in a shell — this keeps credentials out of `ps aux`.

> **Fail-closed:** `STRIX_TG_ALLOWED_USERS` is effectively required. If the
> allowlist is empty the bot FATALs at boot with a clear configuration message
> instead of serving everyone. In group chats, also set `STRIX_TG_ALLOWED_CHATS`
> (a user **and** the chat must be allowlisted).

## 3. Run

### Manual (quick test)

```bash
cd /path/to/workspace
python -m strix_telegram_bot
```

The bot writes logs to `bot.log` and scan outputs to `strix_runs/`.

### Persistent with LaunchAgent (macOS)

The repo includes a production-ready LaunchAgent plist:

```bash
# 1. Copy plist
cp com.strix.telegram-bot.plist ~/Library/LaunchAgents/

# 2. Load it (starts immediately + at login)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.strix.telegram-bot.plist

# 3. Verify
launchctl list com.strix.telegram-bot
```

The LaunchAgent:
- Starts the bot at login (`RunAtLoad`)
- Restarts on crash (`KeepAlive`)
- Sets `DOCKER_HOST` and `PATH` for Colima compatibility
- Logs stdout/stderr to `strix_telegram_bot/stdout.log` and `strix_telegram_bot/stderr.log`

#### Stop

```bash
launchctl bootout gui/$(id -u)/com.strix.telegram-bot
```

## 4. Docker (Colima on macOS)

If using Colima, ensure the Docker socket is available:

```bash
colima start
ls ~/.colima/default/docker.sock  # Should exist
```

The LaunchAgent plist already sets `DOCKER_HOST=unix:///Users/.../.colima/default/docker.sock`.

## 5. Monitoring

```bash
# Watch real-time activity
tail -f bot.log

# Check LaunchAgent status
launchctl list com.strix.telegram-bot

# View scan outputs
ls -lt strix_runs/
```

## Updating Strix

Radamanthys is a passive projection of the official Strix runtime, so a Strix
bump is a small, bounded change:

```bash
# 1. Bump the pin in pyproject.toml (strix-agent==X.Y.Z)
# 2. Reinstall
pip install -e .
# 3. Run the contract suite — it guards every Strix symbol the bot uses
python -m pytest tests/test_strix_162_compat.py -q
# 4. Run the full suite
python -m pytest tests/ -q
# 5. Smoke check
python -m strix_telegram_bot --check
```

If a contract test fails, adapt **only** the contract that genuinely changed
(the contract tests exist to catch real incompatibilities, not to block
legitimate updates). The full update cadence is documented in
`docs/architecture.md`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Bot doesn't start | Missing `.env_bot` | Ensure it exists in the working directory |
| `STRIX_TG_TOKEN requerido` | `.env_bot` not found or wrong format | Check file exists and uses `export KEY="val"` format |
| `Docker not available` | Docker not in PATH | Add `PATH` to plist EnvironmentVariables |
| Bot not responding | Duplicate instance | Kill all processes, restart via LaunchAgent |
| Bot FATALs: "empty allowlist" | `STRIX_TG_ALLOWED_USERS` unset | Set at least one user ID (fail-closed) |
