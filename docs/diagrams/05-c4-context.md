# 05 — C4 Context

```mermaid
flowchart LR
  OWNER(["Owner"])
  SYS["platform-notifications-api: notification rules, log, dispatcher"]
  EP["empire-platform: authz decide, executions, audit"]
  TASKS["Tasks API: EMPIRE_TASKS_API"]
  TG["Telegram Bot API"]
  RELAY["codex-reset.com relay feed (opt-in)"]
  XAPI["api.x.com (opt-in, source=x)"]
  NEON[("Neon PostgreSQL: project platform-notifications-api")]
  SENTRY["Sentry (when SENTRY_DSN set)"]
  UP["platform-uptime-worker"]
  GW["platform-api-gateway: gateway.bookchaowalit.com"]
  HUB["platform-mcp-remote-worker"]
  MON["empire-platform monitor: writes platform_check"]

  OWNER -->|"MCP, CLI client"| GW
  HUB -->|"HTTPS + owner-mcp token"| GW
  GW -->|"/v1/notifications/..."| SYS
  OWNER -->|"notifications-mcp stdio"| SYS
  MON -->|"POST /v1/notifications"| SYS
  SYS -->|"caller token to authz decide and executions"| EP
  SYS -->|"GET /v1/tasks, POST reminders/delivered"| TASKS
  SYS -->|"sendMessage"| TG
  TG --> OWNER
  SYS -->|"one GET per run"| RELAY
  SYS -->|"up to two GETs per run"| XAPI
  SYS --> NEON
  SYS -->|"scrubbed errors"| SENTRY
  UP -->|"GET /health hourly"| SYS
```

> ใช้ flowchart แทน `C4Context` เพื่อให้ label ยาว render บน GitHub ได้

Sources: `README.md`, `PRODUCT.md` §Users / §Operating Context, `src/solo_notifications/platform.py`, `src/solo_notifications/dispatch.py` (`Tasks`, `telegram_sender`), `src/solo_notifications/tibo_reset.py` (`RELAY_URL`, `fetch_posts`), `src/solo_notifications/_sentry.py`, `src/solo_notifications/db.py`, platform-uptime-worker `src/targets.ts` (`notifications-api`, in `SLEEPS`), platform-empire `src/empire_platform/monitor.py`, ops record: api-gateway-edge (2026-11-03) — owner CLI `client.env` and remote MCP hub point `notifications` at the gateway

## Open questions

- Dispatcher ใช้ `EMPIRE_NOTIFICATIONS_API` / `EMPIRE_TASKS_API` ค่าใดใน Railway (ตรงหรือผ่าน gateway) — เป็น Railway variable ไม่อยู่ใน repo
- ops record api-gateway-edge ระบุ `tasks→empire-platform` (Tasks API อยู่ใน empire-platform) — ยืนยันสำหรับ dispatcher ไม่ได้จาก repo นี้
