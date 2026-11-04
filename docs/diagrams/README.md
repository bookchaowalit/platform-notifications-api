# Core 22 SDLC diagram pack — platform-notifications-api

ชุดภาพ Core 22 ตาม SDLC Diagram Catalog v2 วาดจากโค้ดและ config ใน repo นี้ (ณ `origin/main` `8e420e2`, 2026-10-24; เขียน 2026-11-04)

repo นี้คือ notifications cell: FastAPI service `notifications-api` + Railway cron `notifications-dispatcher` จาก image เดียวกัน, ฐานข้อมูล Neon ของตัวเอง (`notifications` schema, RLS ต่อ workspace), ใช้ `empire-platform` สำหรับ authz / executions / audit ผ่าน HTTP ด้วย token ของผู้เรียก และมี MCP server (`notifications-mcp`, stdio)

ทุกไฟล์มี `Sources:` ใต้ภาพ และ "Open questions" สำหรับสิ่งที่ยืนยันจากโค้ดไม่ได้ ช่องว่าง (gap) คือสิ่งที่โค้ดยังไม่ทำ ไม่ใช่การเดา

| # | Item | Status | File | Sources read |
|---|---|---|---|---|
| 01 | Business process As-Is / To-Be | done | [01-business-process.md](01-business-process.md) | `dispatch.py`, `tibo_reset.py`, `app.py`, `PRODUCT.md`, `README.md`, `specs/001-l2-baseline/spec.md` |
| 02 | Use cases | done | [02-use-cases.md](02-use-cases.md) | `auth.py`, `app.py`, `mcp_server.py`, `dispatch.py`, `PRODUCT.md` |
| 03 | User flows | done | [03-user-flows.md](03-user-flows.md) | `app.py`, `mcp_server.py`, `dispatch.py`, `README.md` |
| 04 | Traceability (RTM) | done | [04-traceability.md](04-traceability.md) | `PRODUCT.md`, `specs/001-l2-baseline/spec.md`, `tests/`, `.github/workflows/ci.yml` |
| 05 | C4 Context | done | [05-c4-context.md](05-c4-context.md) | `README.md`, `PRODUCT.md`, `dispatch.py`, `tibo_reset.py`, `platform.py`, ops records |
| 06 | C4 Container | done | [06-c4-container.md](06-c4-container.md) | `.railway/railway.ts`, `Dockerfile`, `pyproject.toml`, `db.py`, `mcp_server.py` |
| 07 | C4 Component | done | [07-c4-component.md](07-c4-component.md) | `src/solo_notifications/*.py` |
| 08 | ADRs | done (4) | [08-adr/](08-adr/) | git log, `app.py`, `platform.py`, `auth.py`, `.railway/railway.ts`, `migrations/0001_notifications.sql`, `mcp_server.py`, `AGENTS.md` |
| 09 | ERD | done | [09-erd.md](09-erd.md) | `migrations/0001_notifications.sql`, `db/roles.sql`, `db.py` |
| 10 | Sequences | done | [10-sequences.md](10-sequences.md) | `app.py`, `platform.py`, `dispatch.py`, `tibo_reset.py` |
| 11 | State machines | done | [11-state-machines.md](11-state-machines.md) | `app.py` (`record_delivery`, `queue`), `dispatch.py`, `tibo_reset.py`, migration |
| 12 | API & integrations | done | [12-api-integration.md](12-api-integration.md) | `app.py`, `dispatch.py`, `tibo_reset.py`, `platform.py`, `db.py`, `mcp_server.py` |
| 13 | Threat model | done | [13-threat-model.md](13-threat-model.md) | `auth.py`, `app.py`, `platform.py`, `_sentry.py`, `dispatch.py`, `tibo_reset.py`, `mcp_server.py`, migration, `db/roles.sql` |
| 14 | Backup & DR | done | [14-backup-dr.md](14-backup-dr.md) | `README.md`, `db.py`, migration, ops record prod-baseline-monitoring-backups, platform-db-backup README |
| 15 | CI/CD pipeline | done | [15-cicd-pipeline.md](15-cicd-pipeline.md) | `.github/workflows/ci.yml`, platform-ci-workflows `ci-python.yml` / `security.yml` @ `87dc5e1c`, `.railway/railway.ts` |
| 16 | Deployment | done | [16-deployment.md](16-deployment.md) | `.railway/railway.ts`, `Dockerfile`, `app.py` `main()`, `db.py`, `README.md`, `PRODUCT.md` |
| 17 | Test coverage matrix | done | [17-test-coverage-matrix.md](17-test-coverage-matrix.md) | `tests/*.py`, `ci.yml` |
| 18 | Git & release workflow | done | [18-git-release-workflow.md](18-git-release-workflow.md) | `git log --first-parent`, `gh pr list`, `AGENTS.md`, `README.md` |
| 19 | Service dependency map | done | [19-service-dependency-map.md](19-service-dependency-map.md) | `dispatch.py`, `platform.py`, `tibo_reset.py`, `db.py`, `_sentry.py`, ops records |
| 20 | Observability | done | [20-observability.md](20-observability.md) | `_sentry.py`, `dispatch.py`, `app.py`, `tibo_reset.py`, platform-uptime-worker `src/targets.ts` |
| 21 | Incident response | done | [21-incident-response.md](21-incident-response.md) | `README.md`, `AGENTS.md`, `dispatch.py`, `_sentry.py`, uptime targets, ops records |
| 22 | DORA | done (2 computed, 3 proxy / not computable) | [22-dora.md](22-dora.md) | `git log`, `gh run list`, `gh pr list` |

## ADR list

- [ADR-0001](08-adr/ADR-0001-own-database-platform-over-http.md) Cell มีฐานข้อมูลของตัวเอง, authz/executions/audit ผ่าน empire-platform ด้วย token ของผู้เรียก
- [ADR-0002](08-adr/ADR-0002-railway-one-image-two-services-cd.md) Railway: image เดียว สองบริการ (Serverless API + cron dispatcher), CD จาก `main` หลัง CI
- [ADR-0003](08-adr/ADR-0003-neon-postgres-rls-two-roles.md) Neon PostgreSQL + RLS ต่อ workspace + roles migrator/runtime
- [ADR-0004](08-adr/ADR-0004-mcp-cannot-cause-outside-message.md) MCP ไม่สามารถทำให้เกิดข้อความออกนอกระบบ

## เอกสารที่มีอยู่แล้ว (reuse / ลิงก์)

- [`README.md`](../../README.md) — API, dispatcher, Tibo monitor, MCP, deploy
- [`PRODUCT.md`](../../PRODUCT.md) — users, purpose, capabilities, measures
- [`specs/001-l2-baseline/spec.md`](../../specs/001-l2-baseline/spec.md) — AC-01..AC-07
- [`DOCPACK/00-product-card.md`](../../DOCPACK/00-product-card.md) — L0 product card (2026-10-11, บางส่วนล้าสมัย เช่น "ไม่มี CI")
- Central: [CRON-DAG.md](https://github.com/bookchaowalit/architecture-docs/blob/main/docs/architecture/CRON-DAG.md) (ระบุว่างาน scheduled ย้ายมาอยู่ใน cron ของ cell เช่น notifications dispatcher)
