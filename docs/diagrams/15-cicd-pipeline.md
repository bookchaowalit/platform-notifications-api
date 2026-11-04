# 15 — CI/CD pipeline

```mermaid
flowchart TD
  PUSH["push to main"] --> CI
  PR["pull_request"] --> CI
  WD["workflow_dispatch"] --> CI
  CI["ci.yml: permissions contents read, concurrency ci-ref cancel-in-progress"]
  CI --> API["job api: ci-python.yml @87dc5e1c (v1.1.1), python 3.12, coverage-threshold 77"]
  CI --> SEC["job security: security.yml @87dc5e1c (v1.1.1)"]
  API --> S1["Checkout"] --> S2["Set up uv and Python"] --> S3["Install dependencies"] --> S4["Lint: ruff check + ruff format --check"] --> S5["Typecheck: mypy"] --> S6["Test: pytest"] --> S7["Coverage gate 77%"]
  SEC --> G1["gitleaks"]
  SEC --> G2["semgrep"]
  SEC --> G3["osv-scanner"]
  SEC --> G4["trivy-fs"]
  S7 --> CHK{"all check suites green on main?"}
  G1 --> CHK
  G2 --> CHK
  G3 --> CHK
  G4 --> CHK
  CHK -->|"yes, Railway Wait for CI checkSuites"| DEP1["deploy notifications-api"]
  CHK -->|yes| DEP2["deploy notifications-dispatcher"]
  CHK -->|no| KEEP["production stays on previous deployment"]
```

Sources: `.github/workflows/ci.yml`, platform-ci-workflows `.github/workflows/ci-python.yml` and `.github/workflows/security.yml` at `87dc5e1c7e0416886a6371d5a101bb2ccba38edd` (jobs `python`; `gitleaks`, `semgrep`, `osv-scanner`, `trivy-fs`), `.railway/railway.ts` (`checkSuites: true`), `README.md` §Develop and operate

หมายเหตุ:

- runner = ค่าเริ่มต้นของ reusable workflow `"ubuntu-24.04"` (repo public, GitHub-hosted)
- `postgres` input ไม่ได้เปิด — tests ใช้ embedded PostgreSQL จาก `pgserver` (dev extra) ซึ่งเป็นเหตุผลที่ใช้ Python 3.12 ใน CI แต่ image ใช้ 3.13
- `tests/test_e2e_platform.py` ถูกข้ามใน CI (ไม่มี `platform_api`)
- ไม่มี deploy smoke / `/health` check หลัง deploy ใน workflow (AGENTS.md ให้ตรวจด้วยมือ) — gap
- ไม่มี migration step อัตโนมัติ — gap/open question (ดู 16)
- ops record: railway-deploy-approval-gate (2026-11-03) บอกว่าตั้งแต่ ~2026-10-28 GitHub deploys บน Railway บางบริการค้างที่ `NEEDS_APPROVAL`; commit ล่าสุดของ repo นี้ (2026-10-24) อยู่ก่อนช่วงนั้น จึงยังไม่ทราบว่ากระทบ repo นี้หรือไม่
