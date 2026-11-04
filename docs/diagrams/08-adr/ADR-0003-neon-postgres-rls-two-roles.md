# ADR-0003 — Neon PostgreSQL, RLS ต่อ workspace, roles migrator / runtime

- Status: accepted (retro-documented)
- Date of decision: 2026-10-10 (`4caa84d`)

## Decision

- Neon project ของ cell เอง (`provision` สร้าง/ใช้ซ้ำ project, pg_version 16, region ค่าเริ่มต้น `aws-ap-southeast-1`), database `notifications`
- `notifications_migrator` เป็นเจ้าของ schema และรัน migration เท่านั้น; `notifications_api` (runtime) NOSUPERUSER, NOBYPASSRLS, NOINHERIT, ไม่ได้เป็นเจ้าของอะไร, มีแค่ SELECT / INSERT / UPDATE (ไม่มี DELETE)
- ทุกตาราง `ENABLE` + `FORCE ROW LEVEL SECURITY` policy `workspace_isolation` เทียบ `workspace_id = notifications.current_workspace()` ซึ่งอ่าน `app.workspace_id` ที่ API ตั้งต่อ transaction จาก claim `ws` ของ token
- Migration append-only: `migrate()` เก็บ sha256 ใน `public.schema_migrations` และปฏิเสธไฟล์ที่ถูกแก้
- Connection URL ต่อ role จาก `NOTIFICATIONS_DB_URL_<ROLE>` หรือไฟล์ owner-only (mode 600)

## Consequences

- runtime role ที่ไม่ได้ bind workspace เห็น 0 แถว (`tests/test_api.py::test_runtime_role_sees_nothing_without_a_workspace`)
- ไม่มีทางลบข้อมูลผ่าน API; log โตไปเรื่อย ๆ (ไม่มี retention — gap)

## Evidence

`migrations/0001_notifications.sql`, `db/roles.sql`, `src/solo_notifications/db.py`, `src/solo_notifications/app.py` (`bound`), `AGENTS.md`
