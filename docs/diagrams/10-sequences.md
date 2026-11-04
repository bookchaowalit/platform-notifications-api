# 10 — Sequences

## S1 — Queue a notification (`POST /v1/notifications`)

```mermaid
sequenceDiagram
  participant C as Caller
  participant A as notifications-api
  participant P as empire-platform
  participant D as Neon notifications
  C->>A: POST /v1/notifications (Bearer, X-Empire-Channel)
  A->>A: verify RS256 token, audience notifications-api
  A->>P: POST /v1/authz/decide notifications.notification:write, new
  P-->>A: allow / deny
  A->>D: BEGIN, set_config app.workspace_id
  alt dedupe_key already present
    A->>D: SELECT by dedupe_key
    A->>P: decide notifications.notification:read on existing id
    A->>D: ROLLBACK
    A-->>C: 201 outcome existing (or 404 if caller cannot read)
  else new
    A->>D: INSERT ... ON CONFLICT DO NOTHING RETURNING
    A->>P: POST /v1/executions (resource id = new row)
    P-->>A: 201 execution_id
    A->>D: COMMIT
    A->>P: POST /v1/executions/id/finish (succeeded, before_hash, after_hash)
    Note over A,P: finish failure: capture + log finish_failed, response still 201
    A-->>C: 201 outcome created
  end
```

Sources: `src/solo_notifications/app.py` (`queue`, `write`, `allow`, `bound`, `audit`), `src/solo_notifications/platform.py`, `src/solo_notifications/auth.py`

## S2 — Record a delivery result

```mermaid
sequenceDiagram
  participant C as Caller
  participant A as notifications-api
  participant P as empire-platform
  participant D as Neon
  C->>A: POST /v1/notifications/id/delivery {outcome, channel, error}
  alt outcome delivered with no channel or channel console
    A-->>C: 422 console_is_not_delivery
  end
  A->>P: decide notifications.notification:write on id
  A->>D: SELECT ... FOR UPDATE
  alt delivered
    A->>D: delivered_at=now(), delivery_channel, error NULL, attempts+1
  else failed
    A->>D: delivery_error, attempts+1
  else console
    A->>D: channel=console, error NULL (no attempt)
  end
  A->>P: begin execution, then COMMIT, then finish
  A-->>C: 200 Notification
```

Sources: `src/solo_notifications/app.py` (`record_delivery`, `Delivery`)

## S3 — Dispatcher delivery run

```mermaid
sequenceDiagram
  participant R as Railway cron
  participant X as notifications-dispatch
  participant N as notifications-api
  participant T as Tasks API
  participant G as Telegram
  R->>X: start (every 30 min)
  X->>N: GET rules active=true
  X->>T: GET /v1/tasks (paged, 200 per page)
  X->>N: POST /v1/notifications per match (dedupe_key)
  X->>N: PATCH rule last_checked_at
  X->>N: GET pending task kinds and whole-message kinds
  alt quiet hours or no Telegram or dry-run
    X-->>R: summary, waiting = n
  else allowed
    X->>G: sendMessage digest / each review
    alt send ok
      X->>N: POST delivery delivered telegram (up to 3 tries on 5xx)
      X->>T: POST /v1/tasks/uuid/reminders/delivered (reminder only)
    else send raised before any part
      X->>N: POST delivery failed error
    end
    X-->>R: JSON summary, exit 0 or 1
  end
```

Sources: `src/solo_notifications/dispatch.py` (`main`, `run_rules`, `Tasks.list`, `deliver`, `_record`, `telegram_sender`)

## S4 — Tibo monitor first and later runs

```mermaid
sequenceDiagram
  participant X as notifications-dispatch
  participant S as codex-reset.com or api.x.com
  participant N as notifications-api
  X->>S: GET feed (relay) or identity + tweets (x)
  S-->>X: posts (validated, max 1 MiB, no redirects)
  X->>N: GET notifications dedupe_key=STATE_KEY
  alt no marker
    X->>N: POST console marker (metadata.started_at)
    X->>N: GET marker again (reuse persisted winner)
    Note over X: created now: return, no historical alerts
  else marker exists
    X->>N: POST tibo_reset per accepted post (dedupe post id + category)
  end
```

Sources: `src/solo_notifications/tibo_reset.py` (`_get`, `fetch_posts`, `run_monitor`)
