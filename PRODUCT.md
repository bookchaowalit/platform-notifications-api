# Product

<!-- impeccable:product-schema 1 -->

## Platform

api

## Users

- The Solo Empire owner, who receives the Telegram digest and manages the rules, through the
  cell's own MCP server (`notifications-mcp`), the hosted tool hub (`platform-mcp-remote-worker`)
  and the ops-toolkit client `infra/scripts/utils/notifications_store.py`
  (`EMPIRE_NOTIFICATIONS_API`).
- The cell's own dispatcher (`notifications-dispatch`), which reads the tasks cell and writes to
  this API as the `reminder-worker` automation.
- Automations with an `empire-platform` token, within the permissions
  `notifications.rule:read|write` and `notifications.notification:read|write`.

## Product Purpose

Turn the owner's tasks into a small number of timely messages: rules (overdue, due soon, high
priority, stale, blocked, reminder) are evaluated against the tasks cell, each match is queued
once per rule, task snapshot and Bangkok day, and pending entries are sent as one Telegram digest
outside quiet hours. The notification log records what was queued and whether a channel
delivered it.

Success: every due reminder reaches Telegram once, nothing is sent between 22:00 and 07:00
Bangkok time, and failed sends are retried a bounded number of times.

## Positioning

One capability, one service: its own API (Railway), its own database (Neon) with row-level
security per workspace, and authorization, executions and audit through `empire-platform` over
HTTP with the caller's own token. Log entries are operational records, so the cell publishes no
graph events and runs no publisher. It replaced the Go notifier and the local notification
runner.

## Operating Context

- Production: `https://notifications-api-production-c9d8.up.railway.app` (`/health`), Railway
  project `platform-notifications-api`; `notifications-api` sleeps when idle (Serverless).
  Deployed by merging to `main` after CI (Railway Wait for CI).
- One image, two services: `notifications-api` and `notifications-dispatcher`
  (`notifications-dispatch`, Railway cron every 30 minutes).
- Data in Neon project `platform-notifications-api`, database `notifications`; 6 rules imported
  with their legacy ids. Telegram delivery verified 2026-10-15 (37 entries, no failed sends).
- The opt-in Tibo reset monitor (Codex / ChatGPT Work reset announcements) is in the code and
  off by default; its enable flags were staged on the dispatcher but not live per the registry
  [ต้องยืนยัน: current state of `TIBO_RESET_MONITOR_ENABLED` in production].
- Monitored by `platform-uptime-worker`; errors go to Sentry without personal data when
  `SENTRY_DSN` is set.

## Capabilities and Constraints

- `/v1/notification-rules`: list (active, rule type), get, create, patch (with
  `expected_version`).
- `/v1/notifications`: list (type, since/until, task, rule, undelivered, max attempts, dedupe key,
  order, paging), stats (by type, severity or channel), get, queue (a known `dedupe_key` returns
  the existing entry, `outcome: existing`, and only to a caller who may read it), and
  `/v1/notifications/{id}/delivery` (`delivered`, `failed` or `console`; the first two count an
  attempt).
- Dispatcher rules: quiet hours 22:00 to 07:00 Asia/Bangkok are checked before each message; at
  most 20 lines and 4000 characters per digest (the rest wait for the next run); at most 3
  delivery attempts per entry; without Telegram configuration nothing is marked delivered; a
  broken rule or entry is skipped, not fatal; delivered explicit reminders are acknowledged on
  the task.
- MCP can never cause an outside message: `notifications.rule.create` always creates an inactive
  rule, there is no rule-update tool, and MCP notes are console-only.
- Out of scope: channels other than Telegram, and per-person preferences.

## Measures

- Due reminders delivered within one dispatcher run outside quiet hours (target: all).
- Entries that reached 3 failed attempts (target: 0).
- Messages sent during quiet hours (target: 0).
- 5xx rate and p95 latency on `/v1/notifications` (Sentry and uptime worker).
