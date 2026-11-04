# 11 — State machines

ไม่มีคอลัมน์ `status` เดียว: สถานะของ log entry มาจาก `delivered_at`, `delivery_error`, `delivery_attempts`, `channel` และ `sent_at` ส่วน rule มี `is_active` + `version`. ชื่อ state ด้านล่างเป็นชื่ออธิบาย ไม่ใช่ค่าที่เก็บใน DB; ค่าที่ API ยอมรับจริงคือ delivery `outcome` = `delivered | failed | console` และ queue `outcome` = `created | existing`

## Notification log entry (as seen by the dispatcher)

```mermaid
stateDiagram-v2
  [*] --> Queued: POST /v1/notifications outcome created
  Queued --> Queued: same dedupe_key, outcome existing
  Queued --> Delivered: outcome delivered, attempts + 1
  Queued --> Failed: outcome failed, attempts + 1
  Failed --> Delivered: next run sends, attempts + 1
  Failed --> Failed: next run fails again, attempts + 1
  Failed --> Exhausted: attempts reaches 3, dispatcher stops picking it
  Queued --> AgedOut: sent_at older than 2 days, not picked
  Failed --> AgedOut: sent_at older than 2 days
  Queued --> Queued: outcome console, channel=console, no attempt
  Delivered --> Delivered: API still accepts later outcomes
  Delivered --> [*]
  Exhausted --> [*]
  AgedOut --> [*]
```

Sources: `src/solo_notifications/app.py` (`queue`, `record_delivery`), `src/solo_notifications/dispatch.py` (`Notifications.pending` with `undelivered=true`, `max_attempts=MAX_ATTEMPTS`, `since=now-2 days`; `deliver`)

ข้อสังเกตที่ตรงกับโค้ด:

- `record_delivery` ไม่ตรวจ state เดิม: entry ที่ delivered แล้วยังรับ `failed` ได้ (attempts + 1, `delivered_at` คงอยู่) และ `console` ไม่กันการส่ง — dispatcher เลือก pending จาก `notification_type` + `delivered_at IS NULL` ไม่ได้ดู `channel`
- Partial send ของข้อความยาวที่ส่งไปบางส่วนแล้ว: log `partial_send` แล้ว **บันทึกเป็น delivered** เพื่อไม่ส่งซ้ำ (`tests/test_dispatch.py::test_a_partly_sent_review_is_not_resent`)
- ไม่มี state สำหรับ read / dismissed ในทางปฏิบัติ (`read_at`, `dismissed_at` ไม่มี route ตั้งค่า — gap)
- Exhausted และ AgedOut ไม่มีการแจ้งหรือ metric (gap; PRODUCT.md ตั้งเป้า exhausted = 0 แต่ไม่มีตัววัด)

## Which kinds can reach Telegram

```mermaid
stateDiagram-v2
  state kind <<choice>>
  [*] --> kind
  kind --> Digest: overdue, due_soon, high_priority, stale, blocked, reminder
  kind --> OwnMessage: engagement_review, platform_check, tibo_reset
  kind --> NeverSent: any other type, e.g. note, agent_note, status_change, tibo_reset_monitor_state
  Digest --> [*]
  OwnMessage --> [*]
  NeverSent --> [*]
```

Sources: `src/solo_notifications/dispatch.py` (`TASK_KINDS`, `WHOLE_MESSAGE_KINDS`), `src/solo_notifications/mcp_server.py` (`NoteKind`)

## Rule

```mermaid
stateDiagram-v2
  [*] --> Active: POST rule (is_active default true)
  [*] --> Inactive: POST via MCP rule.create (forced false)
  Active --> Inactive: PATCH is_active=false
  Inactive --> Active: PATCH is_active=true (HTTP only)
  Active --> Checked: dispatcher run, no queue failure, PATCH last_checked_at
  Checked --> Active: check_interval_minutes passed (due)
  Active --> Active: queue failed, not stamped, retried next run
```

Sources: `src/solo_notifications/app.py` (`create_rule`, `update_rule`), `src/solo_notifications/dispatch.py` (`due`, `run_rules`), `src/solo_notifications/mcp_server.py` (`rule_create`)

ทุก PATCH เพิ่ม `version` เมื่อมี field เปลี่ยน; `expected_version` ไม่ตรง = 409 `version_conflict`. dispatcher PATCH โดยไม่ส่ง `expected_version`

## Tibo reset monitor

```mermaid
stateDiagram-v2
  [*] --> Disabled
  Disabled --> Uninitialized: TIBO_RESET_MONITOR_ENABLED=1
  Uninitialized --> Uninitialized: fetch failed or queue failed, no state created
  Uninitialized --> Seeded: first successful read, console marker created, no alerts
  Seeded --> Seeded: each poll queues accepted posts since started_at, within lookback
  Seeded --> Disabled: flag removed, no reads or queues
```

Sources: `src/solo_notifications/tibo_reset.py` (`Settings.from_env`, `run_monitor`), `README.md` §Tibo reset alerts
