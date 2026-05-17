# Architecture

## Overview

Strix Telegram Bot bridges Telegram and Strix's autonomous security scanning engine. It handles user interaction, job lifecycle, file management, and result delivery.

## Component Diagram

```
Telegram ──→ python-telegram-bot ──→ BotService ──→ JobRunner ──→ StrixAgent ──→ Docker Sandbox
    ↑               │                                    │
    └───────────────┘◄──── callbacks (on_new_message, on_waiting, on_complete)
```

## Key Modules

| Module | Responsibility |
|---|---|
| `bot.py` | Telegram message handlers, file downloads, report delivery |
| `runner.py` | Job lifecycle, Strix agent execution, message monitoring, container cleanup |
| `config.py` | Environment variable parsing, Settings dataclass |
| `security.py` | User/chat whitelist policy |
| `models.py` | JobStatus, JobContext, JobState dataclasses |
| `instructions.py` | Builds Strix scan instructions from user input |
| `__main__.py` | Entry point, logging setup, log rotation, old run cleanup |
