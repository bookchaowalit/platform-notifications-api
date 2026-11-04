# 06 — C4 Container

```mermaid
flowchart TB
  subgraph RW["Railway project platform-notifications-api (one Docker image)"]
    API["notifications-api: FastAPI + uvicorn, CMD notifications-api, Serverless sleepApplication, healthcheck /health"]
    DSP["notifications-dispatcher: startCommand notifications-dispatch, cron */30, restart NEVER"]
  end
  subgraph LOCAL["Owner machine"]
    MCP["notifications-mcp: stdio MCP server, extra mcp"]
    DBT["notifications-db: migrate / provision CLI"]
  end
  subgraph NEON["Neon project platform-notifications-api"]
    DB[("database notifications: schema notifications, tables rules + log, RLS")]
    MIG[("public.schema_migrations")]
  end
  EP["empire-platform"]
  TASKS["Tasks API"]
  TG["Telegram Bot API"]
  SRC["codex-reset.com / api.x.com"]

  API -->|"role notifications_api, set_config app.workspace_id"| DB
  DSP -->|"HTTPS, reminder-worker token, channel workflow"| API
  DSP --> TASKS
  DSP --> TG
  DSP -->|"opt-in"| SRC
  MCP -->|"HTTPS, caller token, channel mcp"| API
  API -->|"decide, begin, finish"| EP
  DBT -->|"role notifications_migrator"| DB
  DBT --> MIG
  DBT -->|"NEON_API_KEY, provision only"| NEONAPI["console.neon.tech API v2"]
```

Sources: `.railway/railway.ts`, `Dockerfile`, `pyproject.toml` (`[project.scripts]`, extras), `src/solo_notifications/app.py` (`main`, `bound`), `src/solo_notifications/db.py` (`HostedCluster`, `migrate`, `provision`, `NEON_API`), `src/solo_notifications/dispatch.py` (`Http`, `main`), `src/solo_notifications/mcp_server.py`, `.mcp.json`, `migrations/0001_notifications.sql`

หมายเหตุ: dispatcher ไม่ต่อ DB ตรง — ทุก read/write ผ่าน HTTP API ของ cell เอง (`dispatch.py` docstring) จึงได้ authz และ audit เหมือน client อื่น

## Open questions

- `notifications-db migrate` รันใน production อย่างไร/เมื่อไร (ไม่มี release step ใน `railway.ts` หรือ CI) — ดู 16
