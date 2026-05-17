# Usage Guide

## Sending Targets

### URLs (with or without protocol)

```
https://example.com
example.com
https://sub.domain.com/path
```

### IP addresses (public only)

```
93.184.216.34
```

### Git repositories

```
git@github.com:user/repo.git
```

### Local files (attachments)

Send any file as a Telegram attachment. The file is uploaded to the Strix sandbox and analyzed.

## Understanding Output

### During scan

Strix sends messages with findings as they happen.

### On completion

You receive:
- **Penetration Test Report** (`.md`) — Full vulnerability report
- **Vulnerabilities CSV** (`.csv`) — Structured list of findings

### Job statuses

| Status | Meaning |
|---|---|
| COMPLETED | Scan finished with report |
| FAILED | Scan encountered an error |
| STOPPED | User pressed STOP |
| Timeout | Job exceeded time limit (2h default) |

## Best Practices

1. **Be specific**: "Scan https://example.com for OWASP Top 10" produces better results
2. **Use attachments**: Source code helps Strix find code-level vulnerabilities
3. **Interact when asked**: When Strix asks for clarification, provide context
4. **Cancel stale jobs**: Press STOP and refine your target if a scan takes too long
