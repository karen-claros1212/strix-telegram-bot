# Architecture — Radamanthys Premium Mirror (STRIX 1.6.2)

## Overview

Radamanthys (the STRIX Telegram bot) is a **passive projection** of the official
STRIX 1.6.2 TUI runtime. The guiding principle is **"Radamanthys does less,
STRIX does more"**: the bot owns Telegram interaction, job tracking, and
delivery, but delegates the scan lifecycle, target setup, MCP roster, usage,
and report artifacts to the official `GoTuiRuntime` / `ReportState` /
`AgentCoordinator`. No parallel event queue, no synthetic lifecycle events, no
regenerated reports.

```
Telegram ── raw HTTP polling ── StrixBot ── StrixRuntimeBridge ── GoTuiRuntime (STRIX 1.6.2)
     ▲                 │                  │                    │
     └─────────────────┘                  │                    ├── AgentCoordinator
                                          │                    ├── TuiLiveView (events)
                                          │                    ├── TuiController (scan_state, MCP roster)
                                          │                    └── ReportState (per-run, usage, vulns, SARIF)
                                          │
                                          ├── JobStore (atomic JSON)
                                          ├── ReportDeliveryTracker (atomic JSON)
                                          └── StateManager (atomic JSON)
```

## Workstreams (premium mirror 1.6.2)

| # | Area | What the bot does now |
|---|------|-----------------------|
| A | Lifecycle | Delegates the whole start to `GoTuiRuntime.prepare_and_start()` (preflight → `persist_current` → `prepare_run` → `telemetry_start` → `init_run_state` → `start_scan`). No manual `prepare_run`/`init_run_state`/`start_scan`. |
| B | Security | `AccessPolicy` is **fail-closed**: an empty user allowlist FATALs at boot. In groups/supergroups the user **and** the chat must be allowlisted. |
| C | Durability | Every JSON state goes through the single `atomic_write_json()` primitive (temp sibling + fsync + `os.replace`). Stale "scanning" jobs are reconciled to `failed` at startup. |
| D | Projection | Projects the official MCP roster (`controller.mcp_connections`), LLM usage (`ReportState.get_total_llm_usage()`), and agent count. No synthetic values. |
| E | Inputs | Uploaded files go through the official `workspace_files` mechanism (`read_workspace_files` → `extra_files`), not a bot-private wrap dir. Local dirs stay `local_code` targets. |
| F | Outputs | Reads the official `findings.sarif` (2.1.0), `penetration_test_report.md`, `vulnerabilities.csv`. Never regenerates them. |
| G | Cleanup | Stop/shutdown uses only the official `GoTuiRuntime.quit()`. Integral STOP test covers start → stop → terminal. |
| H | Contracts | `tests/test_strix_162_compat.py` guards the 1.6.2 API surface the mirror depends on. |

## Key Modules

| Module | Responsibility |
|---|---|
| `bot.py` | Telegram handlers, raw HTTP polling, uploads, report delivery, stale-job reconciliation |
| `config.py` | Settings dataclass, loads `.env_bot` |
| `security.py` | `AccessPolicy` — fail-closed user/chat authorization (group AND) |
| `persistence.py` | `atomic_write_json()` — the single atomic JSON write primitive |
| `models.py` | `JobState`, `JobPhase`, `MenuState`, `ScanMode`, enums |
| `telegram.py` | Raw HTTP wrapper for the Telegram Bot API |
| `strix/runtime_bridge.py` | `StrixRuntimeBridge` — thin projection of `GoTuiRuntime` (delegates lifecycle, projects state) |
| `strix/delivery_state.py` | `ReportDeliveryTracker` — per-run delivery state machine (atomic JSON) |
| `strix/report_collector.py` | `ReportCollector` — reads official outputs (MD, CSV, SARIF, evidence) |
| `strix/report_delivery.py` | `deliver_report_document()` — ships the official MD report |
| `strix/evidence_vault.py` | `EvidenceVault` — artifacts with path-traversal protection |
| `jobs/job_store.py` | `JobStore` — persistent job state (atomic JSON) |
| `state/state_manager.py` | `StateManager` — bot state (atomic JSON) |
| `ui/keyboards.py`, `ui/panels.py`, `ui/messages.py` | Inline keyboards, wizard state, display text |

## Scan Lifecycle (Workstream A)

1. User initiates a scan (wizard or button) → `_launch_scan()`.
2. `_prepare_scan_targets()` resolves local dirs to targets and routes uploads
   to `workspace_files` (Workstream E).
3. `bridge.start_scan()` builds the `args` namespace (targets_info, instruction,
   `local_sources`, `workspace_files`) and starts the runner thread.
4. The thread constructs `GoTuiRuntime(args)` and awaits
   `runtime.prepare_and_start()` — the official 1.6.2 choreography.
5. On success the bridge reads back `args.run_name`, exposes
   `runtime.scan_task`, and confirms startup. On preflight/preparation failure
   the controller's `scan_state == "failed"` is surfaced to the user.
6. Events are projected from `TuiLiveView` (no parallel queue).
7. Stop/shutdown calls `runtime.quit()` (Workstream G).

## Authorization (Workstream B)

- `is_authorized(user_id, chat_id, chat_type)`:
  - the user must be in `allowed_users` (always);
  - in `group`/`supergroup` chats the chat must also be in `allowed_chats`.
- `AccessPolicy.validate()` FATALs at boot if `allowed_users` is empty
  (fail-closed: an empty allowlist would serve nobody).

## Durability (Workstream C)

`atomic_write_json(path, data)` writes to a temp file in the same directory,
`fsync`s, then `os.replace`s onto the target — a crash leaves either the old or
the new file, never a partial one. Used by `JobStore`, `ReportDeliveryTracker`,
`StateManager`, and the Telegram offset. At startup, jobs still marked active
are reconciled to `failed` (the bridge is fresh, so they are orphans).

## Projection (Workstream D)

`to_status_dict()` projects, all read from the engine (no synthesis):
- `mcp_connections` / `mcp_count` — from `controller.mcp_connections`
  (populated by `capture_mcp_status` → `set_mcp_connections`);
- `llm_usage` — from `ReportState.get_total_llm_usage()`;
- `agent_count` — from the coordinator graph snapshot.

## Outputs (Workstream F)

The official STRIX 1.6.2 run dir contains `findings.sarif` (2.1.0),
`penetration_test_report.md`, `vulnerabilities.csv`, `coverage.json`, and
`run.json`. `ReportCollector` reads them (including the SARIF) and the reports
menu offers Markdown / CSV / SARIF / JSON.

## Phase Mapping

The bridge projects the controller's `scan_state`
(`preparing`/`running`/`waiting`/`completed`/`failed`/`stopped`). Display text is
mapped in `ui/messages.py`.

## Updating Strix

Because Radamanthys delegates to the official runtime, a Strix bump is bounded:

1. Bump the pin in `pyproject.toml` (`strix-agent==X.Y.Z`).
2. `pip install -e .`
3. Run the contract suite — `tests/test_strix_162_compat.py` guards every
   Strix symbol the bot imports (lifecycle, coordinator, MCP projection,
   workspace_files, SARIF, `wait_kind` semantics, `quit`/cleanup).
4. Run the full suite + `python -m strix_telegram_bot --check`.
5. If a contract test fails, adapt **only** the contract that genuinely
   changed. The tests exist to catch real incompatibilities, not to block
   legitimate updates.

The baseline is frozen at `strix-agent==1.6.2` (tag `premium-mirror-1.6.2`).
