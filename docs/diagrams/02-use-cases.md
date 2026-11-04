# 02 — Use cases

Actors มาจากโค้ดจริง: token RS256 ที่ `auth.py` ตรวจ (audience `notifications-api`, channel `api|mcp|workflow|cli|event`) และ permission 4 ตัวที่ `app.py` ถาม empire-platform (`notifications.rule:read|write`, `notifications.notification:read|write`) ไม่มีผู้ใช้ภายนอก

```mermaid
flowchart LR
  OWNER(["Owner: owner token, lifetime up to 1 h"])
  MCPC(["MCP client: notifications-mcp, owner-mcp automation by default"])
  DISP(["Dispatcher cron: reminder-worker automation token, channel workflow"])
  AUTO(["Other automation with a delegated token, e.g. empire-platform monitor"])
  TG(["Telegram chat: receives messages"])

  subgraph CELL["notifications-api"]
    UC1["List / get rules"]
    UC2["Create rule"]
    UC3["Patch rule with expected_version"]
    UC4["List / get / stats notification log"]
    UC5["Queue notification, idempotent by dedupe_key"]
    UC6["Record delivery: delivered, failed, console"]
    UC7["Health check"]
  end

  subgraph DSP["notifications-dispatcher"]
    UC8["Evaluate rules against tasks"]
    UC9["Send digest and whole messages"]
    UC10["Acknowledge reminder on task"]
    UC11["Monitor Tibo reset posts (opt-in)"]
  end

  OWNER --> UC1
  OWNER --> UC2
  OWNER --> UC3
  OWNER --> UC4
  OWNER --> UC5
  OWNER --> UC6
  MCPC --> UC1
  MCPC --> UC4
  MCPC -->|"forced is_active=false"| UC2
  MCPC -->|"console-only kinds note, agent_note, status_change"| UC5
  DISP --> UC1
  DISP --> UC3
  DISP --> UC4
  DISP --> UC5
  DISP --> UC6
  DISP --> UC8
  DISP --> UC9
  DISP --> UC10
  DISP --> UC11
  AUTO --> UC5
  UC9 --> TG
```

Sources: `src/solo_notifications/auth.py` (`AUDIENCE`, `CHANNELS`, `MAX_LIFETIME_SECONDS`, `HARD_MAX_LIFETIME_SECONDS`), `src/solo_notifications/app.py` (routes + `allow`), `src/solo_notifications/mcp_server.py` (`build_server`, `NoteKind`, `bearer`), `src/solo_notifications/dispatch.py`, `PRODUCT.md` §Users, platform-empire `src/empire_platform/monitor.py` (writes via `EMPIRE_NOTIFICATIONS_API`)

## หมายเหตุ

- `/health` ไม่ต้องใช้ token (UC7) — ผู้เรียกจริงคือ `platform-uptime-worker` และ Railway healthcheck
- ไม่มี use case สำหรับลบ rule / ลบ log (ไม่มี DELETE route; runtime role ไม่มีสิทธิ์ DELETE ใน migration)
- MCP ไม่มี `rule.update` โดยตั้งใจ (`AGENTS.md`, `tests/test_mcp.py::test_there_is_no_rule_update_tool_so_an_active_rule_cannot_be_widened`)
- คอลัมน์ `read_at` / `dismissed_at` มีใน schema แต่ไม่มี route ไหนตั้งค่า (gap)

## Open questions

- ใครเขียน entry ชนิด `engagement_review` (dispatcher ส่งชนิดนี้ แต่ไม่พบผู้เขียนใน repo นี้; `platform_check` มาจาก empire-platform `monitor.py`)
- README ระบุ MCP default automation = `owner-cli` แต่โค้ด `bearer()` ใช้ `owner-mcp` (docstring ก็บอก `owner-mcp`) — README ล้าสมัย
