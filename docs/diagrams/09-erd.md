# 09 — ERD

```mermaid
erDiagram
  RULES ||--o{ LOG : "rule_id (ON DELETE SET NULL)"
  RULES {
    uuid workspace_id "NOT NULL, RLS"
    bigint id PK "identity"
    bigint legacy_id "unique per workspace when set"
    text name "1..200"
    text description
    text rule_type "overdue, due_soon, high_priority, stale, blocked, status_change, reminder"
    jsonb config "object, default {}"
    jsonb channels "array, default console"
    text project_filter
    text category_filter
    int priority_filter
    jsonb status_filter "array or null"
    int check_interval_minutes "default 60, > 0"
    timestamptz last_checked_at
    timestamptz last_triggered_at
    boolean is_active "default true"
    bigint version "default 1"
    timestamptz created_at
    timestamptz updated_at
  }
  LOG {
    uuid workspace_id "NOT NULL, RLS"
    bigint id PK "identity"
    bigint legacy_id
    bigint rule_id FK
    bigint task_id
    text title "1..500"
    text message "1..8000"
    text notification_type "regex a-z0-9_ up to 60"
    text severity "info, warning, critical, success"
    text channel "default console"
    text task_title
    text task_status
    int task_priority
    text task_due_date
    text task_project
    jsonb metadata "object or null"
    text dedupe_key "8..200, unique per workspace"
    timestamptz sent_at "default now()"
    timestamptz read_at
    timestamptz dismissed_at
    timestamptz delivered_at
    text delivery_channel
    text delivery_error
    int delivery_attempts "default 0, >= 0"
    bigint version "default 1"
  }
  SCHEMA_MIGRATIONS {
    text name PK
    text sha256
    timestamptz applied_at
  }
```

Sources: `migrations/0001_notifications.sql`, `src/solo_notifications/db.py` (`migrate` creates `public.schema_migrations`)

## Indexes, RLS, grants

- `rules_legacy` unique `(workspace_id, legacy_id)` where not null; `log_dedupe` unique `(workspace_id, dedupe_key)` where not null; `log_type_sent`, `log_pending` (where `delivered_at IS NULL`), `log_task`
- FK เป็นแบบ composite `(workspace_id, rule_id)` → `rules (workspace_id, id)` จึงข้าม workspace ไม่ได้
- RLS `ENABLE` + `FORCE` บนทั้งสองตาราง; `notifications_api` ได้ SELECT, INSERT, UPDATE (ไม่มี DELETE)
- ไม่มี foreign key ไปยัง task (`task_id` เป็นเลข task ของ tasks cell, ข้าม DB) และไม่มี workspace table (workspace มาจาก claim `ws`)

Sources: `migrations/0001_notifications.sql`, `db/roles.sql`

## Data ที่เป็น "state" ในตาราง log

- Tibo monitor marker: แถว `notification_type = tibo_reset_monitor_state`, `channel = console`, dedupe_key ใช้ค่าคงที่ `STATE_KEY` ใน `tibo_reset.py`, `metadata.started_at` (ไม่มีตารางแยก)
- reminder: `metadata.task_uuid`, `metadata.reminder_at`

Sources: `src/solo_notifications/tibo_reset.py` (`STATE_KEY`, `run_monitor`), `src/solo_notifications/dispatch.py` (`_entry`)

## Open questions

- PRODUCT.md บอกว่า import 6 rules พร้อม legacy id; จำนวนแถวปัจจุบันใน production ไม่ทราบจาก repo
