# platform-notifications-api
The notifications cell: notification rules and the notification log (what
was queued for which task snapshot, and whether a channel delivered it). It
keeps its own Neon database and reuses
[empire-platform](https://github.com/bookchaowalit/platform-empire) for
identity, authorization, executions and audit over HTTP with the caller's
token (same pattern as decisions and habits). Log entries are
operational records, so the cell publishes no graph events and runs no
publisher. Delivery runs in the cell too (`notifications-dispatch`, below).

| | |
|---|---|
| API | Railway project `platform-notifications-api`, service `notifications-api` (Serverless) |
| Dispatcher | service `notifications-dispatcher` (Railway cron, `*/30 * * * *`) |
| Data | Neon project `platform-notifications-api`, database `notifications` |
| Registry | solo-empire `repository-catalog/registries/online-systems.yaml` (`notifications`) |

## API

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/v1/notification-rules` | list (`active`, `rule_type`) / create |
| GET / PATCH | `/v1/notification-rules/{id}` | one rule / change it (`expected_version`) |
| GET | `/v1/notifications` | log (`notification_type`, `since`, `until`, `task_id`, `rule_id`, `undelivered`, `max_attempts`, `dedupe_key`, `order`, paging) |
| GET | `/v1/notifications/stats` | counts by type, severity or channel (`since`) |
| GET | `/v1/notifications/{id}` | one entry |
| POST | `/v1/notifications` | queue; a known `dedupe_key` returns the existing entry (`outcome: existing`) |
| POST | `/v1/notifications/{id}/delivery` | `delivered` (channel), `failed` (error), or `console` |

Permissions: `notifications.rule:read|write`, `notifications.notification:read|write`.

## Dispatcher

`notifications-dispatch [--dry-run]` evaluates the active rules (overdue,
due_soon, high_priority, stale, blocked, reminder) against the tasks cell,
queues each match once per rule, task snapshot and Bangkok day, then — outside
22:00-07:00 Asia/Bangkok — sends pending task entries as one Telegram digest
and each engagement review as its own message, records every result, and
acknowledges delivered explicit reminders on the task. A broken rule or entry
is logged and skipped. Variables (Railway only): `EMPIRE_TASKS_API`,
`EMPIRE_NOTIFICATIONS_API`, `NOTIFICATIONS_DISPATCH_TOKEN` (the 30-day
`reminder-worker` automation token; rotate with the other cell tokens),
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Tibo reset alerts (opt-in)

The existing `notifications-dispatcher` can monitor Tibo (`@thsottiaux`)
for Codex / ChatGPT Work reset announcements and send each accepted post as
its own message to the existing Telegram chat. It is **disabled by default**.
This adds no service, subscription or schedule: polling uses the existing
30-minute cron. Telegram delivery observes the existing 22:00–07:00 Bangkok
quiet hours; announcements queued overnight wait until the next allowed run.

To enable after deployment, set `TIBO_RESET_MONITOR_ENABLED=1` and
`TIBO_RESET_SOURCE=relay` in the dispatcher environment. Keep the existing
`EMPIRE_TASKS_API`, `EMPIRE_NOTIFICATIONS_API`, `NOTIFICATIONS_DISPATCH_TOKEN`,
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` configuration. The dispatch token
needs notification read/write permissions for durable state and dedupe.
`TIBO_RESET_LOOKBACK_HOURS` defaults to `48` (allowed range 1–168).

The default `relay` source makes at most one public GET per run to
[the free raw-post feed](https://codex-reset.com/api/feed), documented by
[the relay developers](https://tiboreset.com/developers). It requires no key
or subscription. Each alert says `Data: codex-reset.com`: this is a third-party
relay of Tibo's posts, not a direct X read. Only original post text and validated
`https://x.com/thsottiaux/status/<id>` links are classified. Relay predictions,
events and banked-reset observations are ignored. Invalid profiles, stale fetches,
redirects and malformed or oversized responses are rejected; bad individual posts
are skipped. Freshly fetched timelines remain valid on quiet posting days.

Alternatively, explicitly select `TIBO_RESET_SOURCE=x` and configure
`X_BEARER_TOKEN` plus numeric `TIBO_X_USER_ID`. The monitor verifies that ID
against the fixed `thsottiaux` username, then reads the
[official X user-posts endpoint](https://docs.x.com/x-api/users/get-posts).
This performs up to two official API reads per run and may incur X API charges
under your plan. Credentials are sent only to `api.x.com`, with redirects disabled.
There is no automatic switching between sources and no X request from relay mode.

The first successful source read creates a console-only internal marker in the
notifications API and sends **no historical posts**. Later polls consider posts
at or after that original start time, within the lookback window, and never in
the future. The API atomically deduplicates by original post ID and category,
including across restarts and concurrent runs. No local state files or schema
changes are needed. Internal markers are never delivered. A failed fetch never
initializes state; failed queues retry on later polls. Source failures and task
rule failures do not prevent independent pending notifications from being delivered.
Turning off the monitor stops its reads and queues; already queued messages retain
the existing delivery behavior.

Categories are conservative: explicit announcements and `Day N/ Reset` mean
**promised**, explicit completion claims mean **confirmed**, and banked-reset
messages distinguish a promise from a claim that credit was added. These labels
report what Tibo said and never verify your account's quota. Negations, questions,
hypotheticals, unrelated resets, speculation and retweets are ignored. A reply
such as “Landing within the next 2 hours” qualifies only with an accepted reset
parent in the same eligible batch. Unrecognized wording can be missed.

Read-only public preview (no notification API, bot configuration or writes):

```bash
.venv/bin/python -m solo_notifications.tibo_reset --preview
```

The equivalent `--dry-run` previews the relay too. The regular
`notifications-dispatch --dry-run` respects the enable flag and reads the
configured source and existing state to report match counts, without creating
state, queueing alerts or sending Telegram messages.

Example Telegram message:

```text
Tibo — ประกาศว่าจะรีเซ็ต

Tibo (@thsottiaux): ประกาศว่าจะรีเซ็ต
เวลาโพสต์: 2026-10-15 11:55 (Asia/Bangkok)

The reports of my reset button being lost are greatly exaggerated

Day 10/ Reset

https://x.com/thsottiaux/status/2110595484180214059
Data: codex-reset.com
เป็นประกาศของ Tibo; ยังไม่ได้ตรวจสอบโควตาบัญชีของคุณ
```

## MCP server (`notifications-mcp`)

`solo_notifications.mcp_server` is this cell's MCP server (stdio). Every tool calls the cell's HTTP API with the caller's own
token, so MCP has exactly the authorization the API has: it never touches the database, and no tool accepts
an actor, workspace, delegation or token argument (strict models reject unknown arguments before any HTTP
call). Errors reach the client as a fixed message from the HTTP status only (for example `403: forbidden`), never upstream text; tokens with whitespace or control characters are refused.

- Tools: `notifications.rule.{list,get}`, `notifications.notification.{list,get,stats}`.
- Write tools: `notifications.notification.create` (console-only note of kind note/agent_note/status_change, which the dispatcher never sends), and `notifications.rule.create` (always inactive). There is deliberately no rule-update tool: editing an active rule could widen what the dispatcher sends to a person, so rules are changed by the owner only. MCP can never cause an outside message. Set `EMPIRE_MCP_READONLY=1` to register only the read tools.
- URL: `EMPIRE_NOTIFICATIONS_API` (env, else the owner-only `~/.config/empire-platform/client.env`); https only (http only on loopback), no credentials, query or fragment.
- Token, first match wins: `EMPIRE_TOKEN`, `EMPIRE_TOKEN_FILE` (mode 600), then the automation token named by
  `EMPIRE_MCP_AUTOMATION` (default `owner-cli`, lower-case letters, digits and hyphens) from `EMPIRE_AUTOMATION_TOKEN_<NAME>` or
  `~/.config/empire-platform/tokens/<name>.jwt`.
- Run: `uv pip install --python .venv/bin/python -e ".[mcp]"`, then `.venv/bin/notifications-mcp` (see `.mcp.json`).
- Tests: `.venv/bin/pytest tests/test_mcp.py` (fake HTTP transport; no database, no network).

## Develop and operate

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/pytest -q          # add ../platform-empire[dev] for the end-to-end test
NEON_API_KEY=... .venv/bin/notifications-db provision
npm install && railway config apply && railway up --ci --service notifications-api
railway up --ci --service notifications-dispatcher
railway run --service notifications-dispatcher -- .venv/bin/notifications-dispatch --dry-run
```

Railway variables (never in the repository): `SENTRY_DSN` (error reporting; unset = off,
see `src/solo_notifications/_sentry.py`), `NOTIFICATIONS_DB_MODE=hosted`,
`NOTIFICATIONS_DB_URL_API`, `NOTIFICATIONS_JWT_PUBLIC_KEY_PEM`,
`NOTIFICATIONS_JWT_ISSUER`, `EMPIRE_PLATFORM_URL`.
