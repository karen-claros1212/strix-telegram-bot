# Security Policy & Threat Model

## Supported Version

| Version | Supported |
|---------|-----------|
| Current (HEAD) | ✅ Active development |
| Older commits | ❌ Not supported |

## Threat Model

### Trust Boundary

```
[Telegram] ──→ [Bot Service] ──→ [Strix Agent] ──→ [Docker Sandbox]
    │                   │                  │                  │
    │   TLS 1.2+        │  Whitelist       │  LLM API         │  Isolated
    │   Bot Token       │  + Rate Limit    │  (outbound)      │  container
```

### Assets Protected

| Asset | Risk if compromised | Protection |
|-------|-------------------|------------|
| **LLM API Key** | Attacker runs scans at your cost | Never logged, loaded from `.env_bot` only |
| **Telegram Bot Token** | Attacker controls the bot | Stored in file, never in env vars visible via `ps` |
| **Target systems** | Unauthorized scanning | Whitelist + private-IP filtering |
| **Host system (Mac Mini)** | Container escape | Docker sandbox isolation |
| **Scan reports** | Data leakage | Stored locally, deleted with runs |

### Attack Scenarios & Mitigations

| Scenario | Likelihood | Impact | Mitigation |
|----------|-----------|--------|------------|
| **Telegram account compromise** | Low | High | Whitelist restricts to specific user IDs + chat IDs |
| **Prompt injection via Telegram** | Medium | Medium | User input delimited in code block, system directives marked "non-negotiable" |
| **SSRF via malicious target** | Low | High | `_is_private_target()` blocks RFC 1918, loopback, link-local |
| **Container escape** | Low | Critical | Docker sandbox, `docker rm -f` on job completion |
| **Token leak via `ps aux`** | Medium | High | `.env_bot` loaded inside Python, not shell-sourced |
| **Resource exhaustion** | Medium | Medium | Rate limiting (max concurrent jobs), job timeout (2h default) |
| **Orphaned containers** | Low | Medium | Cleanup at startup + periodic cleanup every hour |
| **Path traversal via filename** | Low | Medium | `_safe_filename()` strips `/`, `\`, `..`, null bytes |
| **Dependency vulnerability** | Medium | Medium | Minimal deps (`python-telegram-bot` only), Strix runs isolated |

### Security Controls by Layer

#### Network
- Outbound only to LLM API and scan targets
- Docker container has no inbound ports exposed
- No webhook — polling only (no public endpoint needed)

#### Authentication
- Telegram Bot Token as API credential
- `AccessPolicy` whitelist enforces user + chat level
- No secrets in git history (`.env_bot` in `.gitignore`)

#### Execution
- Targets validated against private IP ranges
- File attachments sanitized before Docker copy
- Jobs timeout after 2 hours (configurable)
- Stale containers removed on bot startup

#### Data
- Reports stored locally in `strix_runs/<job_id>/`
- Old runs auto-deleted after 7 days
- No database — ephemeral state only
- No telemetry or external data transmission

### Reporting a Vulnerability

This is a personal project. If you find a security issue, please open a GitHub Issue with:

- Description of the vulnerability
- Steps to reproduce (if applicable)
- Suggested fix (optional)

Do not disclose vulnerabilities publicly until they have been addressed.
