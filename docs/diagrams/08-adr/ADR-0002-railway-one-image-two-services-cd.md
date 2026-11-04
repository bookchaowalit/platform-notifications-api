# ADR-0002 — Railway: image เดียว สองบริการ, CD จาก `main` หลัง CI

- Status: accepted (retro-documented)
- Date of decision: 2026-10-10 (dispatcher, `6d4826a`); CD 2026-10-18 (`f0af24f`, `4e4daa2`)

## Context

ต้องการให้ reminder ทำงานได้โดยไม่พึ่งเครื่อง owner (`dispatch.py` docstring) และ API ควรหลับเมื่อไม่ใช้งาน

## Decision

- Dockerfile เดียว (`python:3.13-slim`, `uv sync --frozen`, user `notifications` uid 10001), CMD `notifications-api`
- `notifications-api`: Serverless (`sleepApplication: true`), healthcheck `/health` timeout 60
- `notifications-dispatcher`: `startCommand: notifications-dispatch`, `cronSchedule: */30 * * * *`, `restartPolicyType: NEVER`
- ทั้งสองบริการมี source GitHub `bookchaowalit/platform-notifications-api` branch `main`, `checkSuites: true` (Wait for CI); variables `managed: false` (secret อยู่ใน Railway เท่านั้น)
- ไม่ใช้ `railway up` อีก (`README.md`, `AGENTS.md`)

## Consequences

- merge ลง `main` = deploy production ทั้งสองบริการ (รวม commit ที่เป็น docs เท่านั้น — ไม่มี watch paths)
- rollback = redeploy deployment เก่าใน Railway (manual)
- dispatcher fail ไม่ restart; รอรอบ cron ถัดไป

## Evidence

`.railway/railway.ts`, `Dockerfile`, `README.md` §Develop and operate, `AGENTS.md`, git log `6d4826a`, `f0af24f`, `4e4daa2`
