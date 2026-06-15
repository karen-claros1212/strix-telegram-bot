# Architecture

## Overview

STRIX Telegram Bot bridges Telegram and STRIX 1.0.4's autonomous security scanning engine. It handles user interaction, job lifecycle, file management, and result delivery using raw HTTP polling (no external Telegram library).

## Component Diagram

```
Telegram ──→ raw HTTP polling ──→ StrixBot ──→ StrixRuntimeBridge ──→ STRIX 1.0.4
    ↑              │                      │              │              │
    └──────────────┘                      └──────────────┘              └── AgentCoordinator
                                          │                                         └── run_strix_scan()
                                          │
                                          └── JobStore (legacy, for completed jobs)
```

## Key Modules

| Module | Responsibility |
|---|---|
| `bot.py` | Telegram message handlers, raw HTTP polling, file downloads, report delivery |
| `config.py` | Settings dataclass, loads `.env_bot` |
| `security.py` | User/chat whitelist policy (AccessPolicy) |
| `models.py` | FocusPreset, MenuState, ProfileType, ScanMode, ScopeMode, JobState, JobPhase, ScanMode |
| `telegram.py` | Raw HTTP wrapper for Telegram Bot API (sendMessage, getUpdates, etc.) |
| `strix/runtime_bridge.py` | StrixRuntimeBridge — asyncio thread wrapping AgentCoordinator + run_strix_scan |
| `strix/evidence_vault.py` | EvidenceVault — stores artifacts with path traversal protection |
| `strix/report_collector.py` | ReportCollector — reads STRIX scan results (run.json, artifacts, etc.) |
| `strix/caido_panel.py` | CaidoPanel — manages Caido proxy integration |
| `jobs/job_store.py` | JobStore — persistent job state (JSON files, for completed jobs) |
| `state/chat_session.py` | ChatSession — per-chat session state (mode, selected agent) |
| `ui/keyboards.py` | Inline keyboard builders, callback parsing |
| `ui/panels.py` | PanelManager — wizard state per chat |
| `ui/messages.py` | Display text (phase mapping, escape_md, etc.) |
| `safety/redaction.py` | Text redaction for sensitive data |
| `safety/attachment_policy.py` | Target sanitization |

## Authorization Flow

1. `_handle_command()` reads `update["message"]["from"]["id"]` and `update["message"]["chat"]["id"]`
2. `_handle_callback()` reads `update["callback_query"]["from"]["id"]` and `update["callback_query"]["message"]["chat"]["id"]`
3. `is_authorized(user_id, chat_id)` checks against allowed_users/allowed_chats
4. No decorator — single layer of authorization

## Scan Lifecycle

1. User initiates scan (via /start wizard or button)
2. `_launch_scan()` calls `bridge.start_scan(targets, ...)`
3. `StrixRuntimeBridge` creates `AgentCoordinator`, starts daemon thread with asyncio loop
4. `_poll_root()` discovers root agent (parent=None)
5. `run_strix_scan()` executes with `scan_config` (targets, local_sources, diff_scope, etc.)
6. Events captured via `_capture_event()` → `_queue_event()`
7. Bot polls events via `bridge.poll_events()` in main loop

## Local Sources

`collect_local_sources(targets)` from `strix.interface.utils` converts targets to local source entries for the STRIX sandbox. This replaces the old hardcoded `local_sources: []`.

## Event Pump

Events are queued in `StrixRuntimeBridge._event_queue` and drained by `bridge.poll_events()` called from the main Telegram polling loop. Events include:
- `agent_message` — agent responses
- `tool_call` / `tool_output` — tool execution
- `stream_delta` — streaming text (not yet displayed)
- `scan_complete` / `scan_cancelled` / `scan_error` — lifecycle

## Phase Mapping

Bridge returns string phases: `"running"`, `"waiting"`, `"completed"`, `"stopped"`, `"failed"`.
These are mapped to display strings via `_BRIDGE_PHASE_MAP` in `ui/messages.py`.
Legacy JobStore jobs use `JobPhase` enum values.

## Evidence Vault

Artifacts are stored in `<run>/evidence/<subdir>/` with path traversal protection:
- `_safe_name()` strips path components and sanitizes special characters
- `_classify_evidence()` determines artifact type from subdir and extension
- `manifest.json` tracks all stored artifacts with SHA256 hashes

## Report Collector

Reads STRIX scan results from:
- `<run>/run.json` — scan configuration and metadata
- `<run>/evidence/` — evidence artifacts
- `<run>/penetration_test_report.md` — final report
- `<run>/vulnerabilities.csv` / `vulnerabilities.json` — vulnerability data
