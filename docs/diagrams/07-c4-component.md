# 07 — C4 Component

```mermaid
flowchart TB
  subgraph APIC["notifications-api process"]
    MW["correlate middleware: X-Correlation-Id"]
    PR["principal(): TokenVerifier.verify"]
    ROUTES["routes: /health, /v1/notification-rules, /v1/notifications"]
    ALLOW["allow(): platform.decide"]
    WRITE["write(): precheck, local tx, platform.begin, commit, platform.finish"]
    BOUND["bound(): connect + set_config app.workspace_id, rollback + close"]
  end
  AUTH["auth.py: TokenVerifier RS256, rollover keys, Principal"]
  PLAT["platform.py: HttpPlatform, fail-closed"]
  DBM["db.py: HostedCluster / LocalCluster, migrate, provision"]
  SEN["_sentry.py: init_sentry, scrub, capture"]

  subgraph DSPC["notifications-dispatch process"]
    MAIN["main(): config check, run_rules, run_monitor, deliver, JSON summary"]
    HTTP["Http: bearer JSON client, https only, no redirects"]
    TK["Tasks client"]
    NT["Notifications client"]
    EVAL["evaluate(): overdue, due_soon, high_priority, stale, blocked, reminder"]
    DEL["deliver(), digest(), chunks(), _record() retries"]
    TGS["telegram_sender()"]
    TIBO["tibo_reset.py: Settings, fetch_posts, parse_relay, classify, run_monitor"]
  end

  subgraph MCPC["notifications-mcp process"]
    TB["Toolbox + StrictMCPServer: strict models, fixed errors"]
    BEAR["bearer(), api_url()"]
  end

  MW --> ROUTES
  ROUTES --> PR
  PR --> AUTH
  ROUTES --> ALLOW
  ROUTES --> WRITE
  ALLOW --> PLAT
  WRITE --> PLAT
  WRITE --> BOUND
  ROUTES --> BOUND
  BOUND --> DBM
  WRITE -->|"finish failed"| SEN
  MAIN --> EVAL
  EVAL --> TK
  MAIN --> NT
  MAIN --> TIBO
  TIBO --> NT
  MAIN --> DEL
  DEL --> NT
  DEL --> TGS
  DEL --> TK
  TK --> HTTP
  NT --> HTTP
  MAIN --> SEN
  TIBO --> SEN
  TB --> BEAR
```

Sources: `src/solo_notifications/app.py`, `src/solo_notifications/auth.py`, `src/solo_notifications/platform.py`, `src/solo_notifications/db.py`, `src/solo_notifications/_sentry.py`, `src/solo_notifications/dispatch.py`, `src/solo_notifications/tibo_reset.py`, `src/solo_notifications/mcp_server.py`, `src/solo_notifications/__init__.py`

หมายเหตุ: `Platform.publish` (`/v1/graph/events`) มีใน `platform.py` แต่ `app.py` ไม่เรียก — cell นี้ไม่ publish graph events ตาม README
