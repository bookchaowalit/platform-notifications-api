# ADR-0004 — MCP ไม่สามารถทำให้เกิดข้อความออกนอกระบบ

- Status: accepted (retro-documented)
- Date of decision: 2026-10-12 (`031f171`, `9271c14` "remove notifications.rule.update (it could widen an active rule the dispatcher sends from)")

## Decision

- `notifications-mcp` เรียก HTTP API ด้วย token ของผู้เรียกเท่านั้น ไม่แตะ DB และไม่มี argument actor / workspace / delegation / token (strict pydantic `extra="forbid"`)
- `notifications.notification.create` รับเฉพาะชนิด `note`, `agent_note`, `status_change` (dispatcher ไม่ส่งชนิดเหล่านี้) และไม่ส่ง `channel` จึงได้ค่าเริ่มต้น `console`
- `notifications.rule.create` บังคับ `is_active: false`; ไม่มี rule update tool
- `EMPIRE_MCP_READONLY=1` ลงทะเบียนเฉพาะ read tools
- error ถึง client เป็นข้อความคงที่จาก HTTP status เท่านั้น

## Consequences

- เปิดกฎหรือแก้กฎทำได้โดย owner ผ่าน HTTP API เท่านั้น
- ข้อจำกัดนี้อยู่ที่ MCP layer; API เองไม่จำกัดชนิดหรือ channel ของ entry ที่ queue (ผู้ที่มี `notifications.notification:write` ผ่าน HTTP queue ชนิดที่ dispatcher ส่งได้)

## Evidence

`src/solo_notifications/mcp_server.py`, `AGENTS.md`, `README.md` §MCP server, `tests/test_mcp.py`
