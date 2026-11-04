# 21 — Incident response

ไม่มี runbook เฉพาะใน repo; flow นี้ประกอบจาก alert channel ที่มีจริงและขั้นตอนที่ README / AGENTS.md ระบุ

```mermaid
flowchart TD
  subgraph DETECT["Detect"]
    U["uptime worker: notifications-api /health down → Telegram (book_noti_bot)"]
    S["Sentry error event (if SENTRY_DSN set)"]
    O["Owner notices missing or duplicate Telegram digest"]
    R["Railway cron run failed (exit 1 / 2), seen only in Railway"]
  end
  U --> TRI
  S --> TRI
  O --> TRI
  R --> TRI
  TRI["Triage"] --> Q1{"which part?"}
  Q1 -->|"API 5xx / 503"| C1["check empire-platform and Neon (503 authorization_unavailable vs unavailable)"]
  Q1 -->|"no messages"| C2["railway run --service notifications-dispatcher -- notifications-dispatch --dry-run; check telegram flag, quiet, waiting, errors"]
  Q1 -->|"monitor"| C3["python -m solo_notifications.tibo_reset --preview"]
  Q1 -->|"bad deploy"| C4["Railway: redeploy earlier deployment"]
  C1 --> FIX["Fix on branch / main, pytest + ruff + mypy"]
  C2 --> FIX
  C3 --> FIX
  FIX --> SHIP["push main → CI → Wait for CI deploy"]
  C4 --> VERIFY
  SHIP --> VERIFY["verify /health + one authorized read"]
  VERIFY --> PM["postmortem: no template or location defined (gap)"]
```

Sources: `README.md` §Develop and operate (dry-run, rollback), `AGENTS.md` (verify `/health` + one authorized read), `src/solo_notifications/dispatch.py` (`main` exit codes, summary fields), `src/solo_notifications/tibo_reset.py` (`--preview`), `src/solo_notifications/app.py` (503 codes), platform-uptime-worker `src/targets.ts`, ops record: prod-baseline-monitoring-backups (2026-11-03)

## Self-healing already in code

- Failed sends retry on later runs (≤ 3 attempts); lost delivery records retried in-run on 5xx; lost reminder acks repaired next run; failed queues keep the rule due
- Rollback of Tibo monitor: unset `TIBO_RESET_MONITOR_ENABLED` (README)

## Gaps

- No dispatcher heartbeat, so "cron stopped" is not detected
- No on-call / severity definition, no postmortem template
- Duplicate sends possible by design (at-least-once) — no dedupe on the Telegram side

## Open questions

- ใครดู Railway cron failures และ Sentry issues ของ repo นี้ (มี alert rule ใน Sentry หรือไม่)
