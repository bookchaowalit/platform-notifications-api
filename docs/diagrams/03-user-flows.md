# 03 — User flows

ไม่มี UI: "ผู้ใช้" คือ owner ผ่าน MCP / CLI client และ Telegram ฝั่งรับ ด้านล่างคือ flow หลักต่อ role ตาม route และ tool ที่มีจริง

## Flow A — Owner รับแจ้งเตือนงาน (Telegram)

```mermaid
flowchart LR
  T["Owner sets due date, priority, status or reminder_at in tasks cell"] --> C["Dispatcher run, every 30 min"]
  C --> Q["Match queued once per rule, task snapshot, Bangkok day"]
  Q --> H{"07:00-22:00 Bangkok?"}
  H -->|no| W["Waits for next allowed run"]
  H -->|yes| D["Telegram digest: 'Solo Empire — แจ้งเตือนงาน (n)', icon per kind"]
  D --> R["Reminder acknowledged on the task, does not fire again"]
```

Sources: `src/solo_notifications/dispatch.py` (`evaluate`, `run_rules`, `quiet`, `digest`, `ICONS`, `deliver`)

## Flow B — Owner / agent ดูและจัดการผ่าน MCP

```mermaid
flowchart TD
  S["Start notifications-mcp (stdio)"] --> CFG{"EMPIRE_NOTIFICATIONS_API https or loopback, token found?"}
  CFG -->|no| E["SystemExit with config message"]
  CFG -->|yes| TOOLS["Tools registered"]
  TOOLS --> R1["notifications.rule.list / get"]
  TOOLS --> R2["notifications.notification.list / get / stats"]
  TOOLS --> W1["notifications.notification.create: console note"]
  TOOLS --> W2["notifications.rule.create: inactive rule"]
  RO{"EMPIRE_MCP_READONLY=1"} -.->|"write tools not registered"| W1
  RO -.-> W2
  R1 --> API["HTTP API with caller bearer, X-Empire-Channel mcp"]
  R2 --> API
  W1 --> API
  W2 --> API
  API -->|">= 400"| ERR["fixed message from status only, e.g. 403: forbidden"]
```

Sources: `src/solo_notifications/mcp_server.py` (`api_url`, `bearer`, `Toolbox.call`, `failure`, `build_server`, `main`), `.mcp.json`

## Flow C — Owner เปลี่ยนกฎ (HTTP เท่านั้น)

```mermaid
flowchart LR
  G["GET /v1/notification-rules/id"] --> P["PATCH with expected_version"]
  P -->|"version differs"| C409["409 version_conflict + current version"]
  P -->|ok| OK["version + 1, updated_at = now()"]
```

Sources: `src/solo_notifications/app.py` (`update_rule`)

## Flow D — Tibo reset preview (ไม่เขียนอะไร)

`python -m solo_notifications.tibo_reset --preview` อ่าน relay สาธารณะครั้งเดียว พิมพ์ JSON ของ match 48 ชั่วโมงล่าสุด ไม่แตะ notifications API ไม่อ่าน bot config

Sources: `src/solo_notifications/tibo_reset.py` (`main`), `README.md`

## Open questions

- Client `ops-toolkit infra/scripts/utils/notifications_store.py` (PRODUCT.md) และ hub `platform-mcp-remote-worker` ไม่ได้อ่านในรอบนี้ — flow ฝั่งนั้นไม่ได้วาด
