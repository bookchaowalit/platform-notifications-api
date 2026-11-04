# 17 — Test coverage matrix

ระดับ: **unit** = ฟังก์ชันล้วน / fake ในหน่วยความจำ; **integration** = FastAPI TestClient + PostgreSQL จริงแบบ throwaway (`pgserver`) + `FakePlatform`; **e2e** = กับ empire-platform จริงในโปรเซส (ข้ามใน CI). ไม่มี test ที่เรียก service hosted

| Feature | Unit | Integration | E2E | Files |
|---|---|---|---|---|
| Rules CRUD + `expected_version` | — | yes | — | `tests/test_api.py::test_rules_lifecycle` |
| Queue + dedupe (incl. concurrent) | — | yes | yes (skipped in CI) | `test_api.py::test_queueing_is_idempotent_by_dedupe_key`, `test_concurrent_queue_with_one_key_keeps_one_entry`, `test_e2e_platform.py::test_queue_and_delivery_are_executions_with_audit` |
| Existing entry only to readers | — | yes | — | `test_api.py::test_existing_entry_is_returned_only_to_readers` |
| Delivery outcomes / attempts | — | yes | yes (skipped) | `test_api.py::test_delivery_results` |
| Log filters + stats | — | yes | — | `test_api.py::test_filters_and_stats` |
| Permissions + workspace isolation (RLS) | — | yes | yes (skipped) | `test_api.py::test_permissions_and_isolation`, `test_runtime_role_sees_nothing_without_a_workspace`, `test_e2e_platform.py::test_read_only_delegation_reads_but_cannot_queue` |
| Invalid input, platform down (503) | — | yes | — | `test_api.py::test_invalid_input_and_platform_down` |
| Token verification + rollover key | yes | — | — | `tests/test_rollover_key.py` (4 test functions) |
| Rule evaluation (6 kinds) | yes | — | — | `tests/test_dispatch.py::test_rules_match_like_the_local_runner`, `test_a_broken_rule_config_is_skipped` |
| Once a day + check interval, failed queue keeps rule due | yes | — | — | `test_dispatch.py::test_queue_once_a_day_and_respect_the_check_interval`, `test_a_failed_queue_keeps_the_rule_due`, `test_dry_run_queues_and_touches_nothing` |
| Digest, quiet hours, failures, limits | yes | — | — | `test_dispatch.py::test_digest_delivery_quiet_hours_and_failures`, `test_quiet_hours_are_checked_before_each_message`, `test_long_titles_never_drop_lines_silently`, `test_long_reviews_are_split_not_cut`, `test_a_partly_sent_review_is_not_resent`, `test_platform_check_is_sent_as_its_own_message` |
| No Telegram config / bad bot token / missing config | yes | — | — | `test_dispatch.py::test_without_telegram_nothing_is_marked_delivered`, `test_a_malformed_bot_token_is_refused_without_echo`, `test_main_reports_missing_config` |
| Record retry, reminder ack | yes | — | — | `test_dispatch.py::test_a_failed_record_does_not_stop_the_others`, `test_delivered_reminders_are_acknowledged_on_the_task`, `test_a_lost_reminder_ack_is_retried_without_resending` |
| Tibo classify / parse / fetch / monitor | yes | yes | — | `tests/test_tibo_reset.py` (unit), `tests/test_tibo_dispatch.py` (unit), `tests/test_tibo_api.py::test_seed_restart_dedupe_and_delivery_through_notifications_api` (through the real API + DB) |
| MCP tools, strictness, errors, token/URL config | yes | — | — | `tests/test_mcp.py` (fake HTTP transport) |
| Sentry init + scrub | yes | — | — | `tests/test_sentry.py` |
| `db.py` migrate | — | yes (fixture `cluster` runs `migrate`) | — | `tests/conftest.py` |
| `db.py provision` (Neon API) | **gap** | **gap** | — | none |
| `HttpPlatform` real HTTP client | **gap** (only via e2e, skipped) | — | skipped | `tests/test_e2e_platform.py` |
| `telegram_sender` real HTTP | **gap** (format check only) | — | — | — |
| `status_change` rule kind | **gap** (not implemented) | — | — | — |
| Deployed service smoke | — | — | **gap** | ไม่มี deploy smoke workflow |

CI gate: coverage-threshold 77 (spec: วัดได้ 79.79% วันที่ 2026-10-22); SPEC-001 รีวิว 2026-10-24 รายงาน "163 passed, 1 skipped"

Sources: `tests/*.py`, `tests/conftest.py`, `.github/workflows/ci.yml`, `specs/001-l2-baseline/spec.md`
