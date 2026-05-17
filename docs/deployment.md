# Deployment Guide

## Manual (macOS / Linux)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env_bot
nano .env_bot  # Fill in your token, keys, and user IDs
```

### 3. Run

```bash
bash start_bot.sh
```

## Persistent (LaunchAgent — macOS)

Create `~/Library/LaunchAgents/com.strix.telegram-bot.plist`.

Then load:

```bash
launchctl load ~/Library/LaunchAgents/com.strix.telegram-bot.plist
```

## Monitoring

- Logs: `bot.log` (auto-rotates at 10MB, 5 backups)
- Each scan creates a directory in `strix_runs/<job_id>/`
- Run `tail -f bot.log` to watch activity in real-time
