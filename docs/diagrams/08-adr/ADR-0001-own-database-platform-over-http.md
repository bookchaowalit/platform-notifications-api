# ADR-0001 — Cell มีฐานข้อมูลของตัวเอง; authz, executions และ audit ผ่าน empire-platform ด้วย token ของผู้เรียก

- Status: accepted (retro-documented)
- Date of decision: 2026-10-10 (commit `4caa84d` "notifications cell with its own database, using empire-platform over HTTP")

## Context

การแจ้งเตือนเคยอยู่ใน control plane กลางของ solo-empire (SQLite) แยก deploy และสิทธิ์ไม่ได้ (`DOCPACK/00-product-card.md` §2) registry ระบุว่า SQLite tables ถูก drop โดย migration 083 ของ solo-empire

## Decision

- ข้อมูล (rules, log) อยู่ใน DB ของ cell เอง
- ทุก read ถาม `POST /v1/authz/decide`; ทุก write ทำ precheck → เปลี่ยนแถวใน local transaction → `POST /v1/executions` (ผูกกับ resource id จริง) → commit → `POST /v1/executions/{id}/finish` พร้อม `before_hash` / `after_hash`
- ส่งต่อ bearer token ของผู้เรียกเอง; `HttpPlatform` fail closed (transport error หรือ status ที่ไม่คาด = ไม่อนุญาต / 503)
- ไม่ publish graph events (log เป็น operational record)

## Consequences

- API ใช้ไม่ได้ถ้า empire-platform ล่ม (`authorization_unavailable` 503)
- ถ้า `finish` ล้มเหลวหลัง commit: ข้อมูลเปลี่ยนแล้วแต่ execution ไม่ถูกปิด — โค้ด `capture(exc)` + log `notifications.finish_failed` ไม่มี retry (gap ที่ยอมรับไว้ในโค้ด)
- Queue ซ้ำด้วย `dedupe_key` เดิม: ไม่สร้าง execution (`skip`)

## Evidence

`src/solo_notifications/app.py` (module docstring, `allow`, `write`, `audit`), `src/solo_notifications/platform.py`, `README.md` intro, `tests/test_e2e_platform.py::test_queue_and_delivery_are_executions_with_audit`, solo-empire registry `repository-catalog/registries/online-systems.yaml` entry `notifications`
