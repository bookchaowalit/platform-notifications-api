# 01 — Business process (As-Is / To-Be)

## As-Is: จากงาน (task) ถึงข้อความ Telegram (ตามโค้ดวันนี้)

ทุก 30 นาที Railway cron รัน `notifications-dispatch` หนึ่งครั้ง: (1) ประเมินกฎที่ active และถึงรอบ (2) Tibo reset monitor ถ้าเปิด (3) ส่งรายการที่ค้างทาง Telegram นอก quiet hours

```mermaid
flowchart TD
  CRON["Railway cron every 30 min"] --> START["notifications-dispatch"]
  START --> CFG{"EMPIRE_TASKS_API, EMPIRE_NOTIFICATIONS_API, NOTIFICATIONS_DISPATCH_TOKEN set?"}
  CFG -->|no| EXIT2["print missing_config, exit 2"]
  CFG -->|yes| RULES["GET /v1/notification-rules?active=true"]
  RULES --> DUE{"last_checked_at + check_interval_minutes passed?"}
  DUE -->|no| SKIP["skip rule"]
  DUE -->|yes| EVAL["evaluate rule against GET /v1/tasks (Leantime tasks excluded)"]
  EVAL -->|"ApiError, KeyError, TypeError, ValueError, OverflowError"| RFAIL["log rule_failed, Sentry once, next rule"]
  EVAL --> MATCH["each match: dedupe_key = rule, task snapshot, Bangkok day (+ reminder_at)"]
  MATCH --> ACK{"reminder already delivered earlier?"}
  ACK -->|yes| ACKTASK["POST /v1/tasks/uuid/reminders/delivered, queue nothing"]
  ACK -->|no| QUEUE["POST /v1/notifications (created or existing)"]
  QUEUE -->|"ApiError"| QFAIL["log queue_failed; rule NOT stamped, retried next run"]
  QUEUE --> STAMP["PATCH rule last_checked_at (+ last_triggered_at if queued)"]
  STAMP --> TIBO["Tibo reset monitor (opt-in, default off)"]
  SKIP --> TIBO
  RFAIL --> TIBO
  QFAIL --> TIBO
  TIBO --> PEND["GET pending: undelivered, delivery_attempts < 3, sent_at within 2 days"]
  PEND --> GATE{"Telegram configured AND not quiet hours AND not dry-run?"}
  GATE -->|no| WAIT["count as waiting, send nothing"]
  GATE -->|yes| DIGEST["task kinds: one digest, max 20 lines and 4000 chars"]
  GATE -->|yes| WHOLE["engagement_review, platform_check, tibo_reset: own message each"]
  DIGEST --> SEND["Telegram sendMessage"]
  WHOLE --> SEND
  SEND -->|ok| DELIV["POST delivery outcome=delivered channel=telegram; ack reminder on task"]
  SEND -->|"RuntimeError, nothing sent"| FAILR["POST delivery outcome=failed (attempt + 1)"]
  DELIV --> SUMMARY["print JSON summary; exit 1 if any failed or errors"]
  FAILR --> SUMMARY
  WAIT --> SUMMARY
```

Sources: `src/solo_notifications/dispatch.py` (`main`, `run_rules`, `evaluate`, `due`, `deliver`, `digest`, `quiet`), `src/solo_notifications/tibo_reset.py` (`run_monitor`), `.railway/railway.ts`

ข้อเท็จจริงสำคัญจากโค้ด:

- quiet hours = 22:00–07:00 Asia/Bangkok (`QUIET = (22, 7)`) ตรวจก่อนส่ง **ทุก batch** (`quiet(clock())`) ไม่ใช่แค่ตอนเริ่ม
- `MAX_ATTEMPTS = 3` ใช้เป็นตัวกรอง `max_attempts` ตอนดึง pending: entry ที่ล้มเหลวครบ 3 ครั้งจะไม่ถูกหยิบอีก (ไม่มีการแจ้งเตือนแยก)
- pending ดึงเฉพาะ `sent_at` ภายใน 2 วันที่ผ่านมา: entry ที่ค้างเกิน 2 วันไม่ถูกส่งอีก (gap: ไม่มีการรายงาน)
- กฎ `status_change` มีใน schema / API แต่ `evaluate()` คืน `[]` สำหรับชนิดนี้ (gap: ยังไม่ implement ใน dispatcher)

## As-Is: Tibo reset monitor (opt-in)

```mermaid
flowchart TD
  A["run_monitor"] --> EN{"TIBO_RESET_MONITOR_ENABLED == 1?"}
  EN -->|no| OFF["return enabled=false, no fetch"]
  EN -->|yes| SRC{"TIBO_RESET_SOURCE"}
  SRC -->|relay| R["GET codex-reset.com/api/feed (one call)"]
  SRC -->|x| X["GET api.x.com users/by/username, then users/id/tweets"]
  R --> P["validate envelope and posts"]
  X --> P
  P --> M{"state marker exists? (dedupe_key tibo-reset-monitor...)"}
  M -->|"no, not dry-run"| SEED["queue console-only marker; first run sends no history"]
  M -->|yes| WIN["eligible = posts since marker start, within lookback, not in future"]
  WIN --> CL["classify: promised, confirmed, banked, or ignore"]
  CL --> Q["queue tibo_reset entry, dedupe by post id + category"]
```

Sources: `src/solo_notifications/tibo_reset.py` (`Settings.from_env`, `fetch_posts`, `parse_relay`, `classify`, `matches`, `_alert`, `run_monitor`), `README.md` §Tibo reset alerts

## To-Be (จากเอกสารใน repo เท่านั้น)

- `PRODUCT.md` §Measures: due reminders ส่งภายใน 1 รอบนอก quiet hours (เป้า: ทั้งหมด), entry ที่ล้มเหลวครบ 3 ครั้ง (เป้า: 0), ข้อความใน quiet hours (เป้า: 0), 5xx และ p95 latency ของ `/v1/notifications`
- `PRODUCT.md` §Capabilities: out of scope = ช่องทางอื่นนอก Telegram และ preference ต่อคน
- `specs/001-l2-baseline/spec.md`: ไม่มีพฤติกรรมใหม่ (tooling, lock, tests เท่านั้น); รีวิว 2026-10-24 ขอให้แก้ตาราง §4 ที่เสีย

## Open questions

- To-Be ของกระบวนการ (เช่น channel ใหม่, การแจ้งเมื่อ entry ล้มเหลวครบ 3 ครั้ง, การจัดการ entry ที่เกิน 2 วัน) ไม่มีใน PRODUCT.md / specs / issues — ต้องให้ owner กำหนด
- กฎ `status_change` ตั้งใจให้ dispatcher รองรับหรือไม่ (ตอนนี้ถูกข้ามแบบเงียบ)
- `TIBO_RESET_MONITOR_ENABLED` ใน production เปิดอยู่หรือไม่ (PRODUCT.md ระบุ `[ต้องยืนยัน]`; registry บอกว่า staged แต่ยังไม่ live)
