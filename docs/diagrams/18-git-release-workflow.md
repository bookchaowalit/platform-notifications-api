# 18 — Git & release workflow

ประวัติจริง: 2026-10-10 ถึง 2026-10-15 commit ตรงลง `main`; 2026-10-17 ถึง 2026-10-18 ใช้ PR #1–#9 (merge ใน local แล้ว push, บาง PR เป็น squash/fast-forward); 2026-10-22 และ 2026-10-24 commit ตรงลง `main` อีกครั้ง (SPEC-001 ระบุ "Main-only: committed straight to main")

```mermaid
gitGraph
  commit id: "4caa84d cell + own DB"
  commit id: "6d4826a dispatcher cron"
  commit id: "031f171 MCP server"
  commit id: "b2309f7 Tibo monitor"
  branch ci/devsecops-phase1
  commit id: "PR 1 shared CI"
  checkout main
  merge ci/devsecops-phase1 id: "952b2cb"
  commit id: "39f8cb3 PR 2 fix"
  branch feat/rollover-signing-key
  commit id: "1a68db8 rollover key"
  commit id: "131ab3e Codex round 1"
  checkout main
  merge feat/rollover-signing-key id: "337be4d PR 3"
  commit id: "1297ff5 PR 4 Sentry"
  commit id: "2215f4c PR 5"
  commit id: "819029e PR 6"
  commit id: "b951a7e PR 7"
  branch docs/cd-github-wait-for-ci
  commit id: "f0af24f CD docs"
  checkout main
  merge docs/cd-github-wait-for-ci id: "e137c43 PR 8"
  branch chore/iac-github-source
  commit id: "4e4daa2 IaC source"
  checkout main
  merge chore/iac-github-source id: "bce22ea PR 9"
  commit id: "5a4a5c8 L2 baseline"
  commit id: "8e420e2 SPEC review"
```

Sources: `git log origin/main --first-parent`, `gh pr list --state all` (PR #1–#9 merged 2026-10-17..18), `specs/001-l2-baseline/spec.md`

## Path to production

```mermaid
flowchart LR
  W["work on branch or main (local)"] --> T["local: pytest, ruff, mypy (AGENTS.md)"]
  T --> REV["Codex review Stop hook / reviewer (per commit messages)"]
  REV --> M["merge locally, push main"]
  M --> CI["GitHub Actions ci.yml: api + security"]
  CI -->|green| RW["Railway Wait for CI deploys both services"]
  CI -->|red| STAY["previous deployment stays"]
  RW --> V["manual: /health + one authorized read (AGENTS.md)"]
  V --> REG["update solo-empire registry entry notifications"]
```

Sources: `AGENTS.md`, `README.md` §Develop and operate, `.railway/railway.ts`, `.github/workflows/ci.yml`, commit `131ab3e` ("Codex round 1")

ข้อเท็จจริง: `main` ไม่มี branch protection และไม่มี ruleset (`gh api repos/.../branches/main/protection` = 404, rulesets = `[]`); ไม่มี tag / release

## Open questions

- Branch `origin/docs/cd-github-wait-for-ci` ยังค้างอยู่หลัง merge — ลบได้หรือไม่
