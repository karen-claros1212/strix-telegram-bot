# Strix Telegram Bot

> A Telegram bot interface for Strix — autonomous AI security scanning agent.

Send a URL, domain, or file to the bot, and Strix runs a full penetration test. Get real-time updates, vulnerability reports, and CSV exports — all from Telegram.

## Features

- Send URLs, domains, IPs, or files as targets
- Real-time scan progress updates
- STOP button to cancel any scan
- Auto-generated Markdown report + CSV on completion
- Interactive mode — Strix asks questions, you answer in the chat
- User/chat whitelist for access control
- Auto-cleanup of Docker containers and old runs
- Works with any LLM (DeepSeek, OpenAI, Anthropic, etc.)

## Quick Start

### Prerequisites

- Python 3.11+, Strix CLI, Docker, Telegram Bot Token

### Installation

```bash
git clone https://github.com/karen-claros1212/strix-telegram-bot.git
cd strix-telegram-bot
pip install -r requirements.txt
cp .env.example .env_bot
# Edit .env_bot with your token, LLM config, and user IDs
bash start_bot.sh
```

### Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `STRIX_TG_TOKEN` | Yes | — | Bot token from @BotFather |
| `STRIX_TG_ALLOWED_USERS` | Yes | — | Comma-separated Telegram user IDs |
| `STRIX_TG_ALLOWED_CHATS` | No | — | Comma-separated chat IDs |
| `STRIX_LLM` | Yes | — | LLM model (e.g. `deepseek/deepseek-v4-pro`) |
| `LLM_API_KEY` | Yes | — | API key for LLM provider |
| `STRIX_JOB_TIMEOUT_SECONDS` | No | 7200 | Max job duration in seconds |
| `STRIX_WORK_ROOT` | No | `./strix_runs` | Output directory |

## Architecture

```
Telegram ──→ python-telegram-bot ──→ JobRunner ──→ StrixAgent ──→ Docker Sandbox
```

See [docs/architecture.md](docs/architecture.md) for details.

## Deployment

See [docs/deployment.md](docs/deployment.md) for manual and LaunchAgent setup.

## License

MIT
