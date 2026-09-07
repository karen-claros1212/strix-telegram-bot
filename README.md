# Strix Telegram Bot (Radamanthys)

> A Telegram bot interface for Strix — autonomous AI security scanning agent.

Send a URL, domain, or file to the bot, and Strix runs a full penetration test. Get real-time updates, vulnerability reports, and SARIF/CSV/Markdown exports — all from Telegram.

Radamanthys is a **passive premium mirror** of the official Strix 1.6.2 TUI runtime: it delegates the scan lifecycle, MCP roster, usage, and report artifacts to `GoTuiRuntime`, and projects them — no parallel event queue, no synthetic state, no regenerated reports.

## Features

- Send URLs, domains, IPs, or files as targets (files via official `workspace_files`)
- Real-time scan progress updates (projected from the official `TuiLiveView`)
- STOP button always visible on status message throughout the scan
- Official outputs on completion: Markdown report + CSV + SARIF 2.1.0
- Interactive mode — Strix agent asks questions, you answer in the chat
- MCP connection roster + LLM usage projected from the engine (no synthesis)
- Fail-closed user/chat whitelist (group chats require user **and** chat)
- Rate limiting (max concurrent jobs configurable)
- Auto-cleanup of Docker containers, orphaned runs, and old data
- Prompt injection protection — user input isolated from system directives
- Works with any LLM (DeepSeek, OpenAI, Anthropic, etc.)

## Quick Start (macOS)

### Prerequisites

- Python 3.12+
- Strix CLI installed (strix-agent == 1.6.2, pinned in `pyproject.toml`)
- Docker (via Colima or Docker Desktop)
- Telegram Bot Token (from @BotFather)

### Installation

```bash
git clone https://github.com/karen-claros1212/strix-telegram-bot.git
cd strix-telegram-bot
pip install -r requirements.txt
cp .env.example .env_bot
# Edit .env_bot with your token, LLM config, and user IDs
```

### Run

```bash
# The bot loads .env_bot automatically — no sourcing needed
python -m strix_telegram_bot
```

### Persistent (LaunchAgent)

```bash
cp com.strix.telegram-bot.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.strix.telegram-bot.plist
```

The bot auto-restarts on crash and launches at login.

### Docker on macOS (Colima)

If using Colima, ensure `~/.colima/default/docker.sock` exists, or set `DOCKER_HOST` in the plist's `EnvironmentVariables`.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `STRIX_TG_TOKEN` | Yes | — | Bot token from @BotFather |
| `STRIX_TG_ALLOWED_USERS` | Yes | — | Comma-separated Telegram user IDs |
| `STRIX_TG_ALLOWED_CHATS` | No | — | Comma-separated chat IDs |
| `STRIX_LLM` | Yes | — | LLM model (e.g. `openai/gpt-5`, `anthropic/claude-sonnet-4-6`, `strix/gpt-5`) |
| `LLM_API_KEY` | Yes | — | API key for LLM provider |
| `STRIX_JOB_TIMEOUT_SECONDS` | No | 7200 | Max job duration in seconds |
| `STRIX_MAX_CONCURRENT_JOBS` | No | 3 | Max simultaneous scans |
| `STRIX_WORK_ROOT` | No | `./strix_runs` | Output directory |
| `DOCKER_HOST` | No | — | Docker socket (Colima: `unix://${HOME}/.colima/default/docker.sock`) |

## Architecture

```
Telegram ──→ HTTP Polling ──→ StrixBot ──→ StrixRuntimeBridge ──→ GoTuiRuntime (Strix 1.6.2)
                                                                          ├── AgentCoordinator
                                                                          ├── TuiLiveView / TuiController
                                                                          └── ReportState (usage, vulns, SARIF)
```

The bot projects the official runtime state (MCP roster, LLM usage, agent tree,
scan phase) and reads the official outputs (`findings.sarif`,
`penetration_test_report.md`, `vulnerabilities.csv`). See
[docs/architecture.md](docs/architecture.md) for the full workstream breakdown.

## Deployment

See [docs/deployment.md](docs/deployment.md) for manual, LaunchAgent, and troubleshooting.

## License

Apache 2.0 — see [LICENSE](LICENSE).
Copyright 2026 Diego Claros.
