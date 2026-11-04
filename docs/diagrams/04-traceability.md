# 04 — Traceability (RTM)

Requirement มาจาก `PRODUCT.md` (§Capabilities and Constraints, §Product Purpose) และ `specs/001-l2-baseline/spec.md` (AC-01..AC-07). ทุกแถว release ผ่าน `.github/workflows/ci.yml` (job `api` + `security`) แล้ว Railway Wait for CI deploy จาก `main`

| Req | Requirement | Code module | Test | Release / workflow |
|---|---|---|---|---|
| AC-01 | queue once per rule, task snapshot, Bangkok day; respect check interval | `dispatch.py` `run_rules`, `dedupe_key`, `due` | `tests/test_dispatch.py::test_queue_once_a_day_and_respect_the_check_interval` | ci.yml `api` |
| AC-02 | quiet hours 22:00–07:00 checked before each message | `dispatch.py` `quiet`, `deliver` | `tests/test_dispatch.py::test_quiet_hours_are_checked_before_each_message`, `test_digest_delivery_quiet_hours_and_failures` | ci.yml `api` |
| AC-03 | no Telegram config, nothing marked delivered | `dispatch.py` `deliver`, `main` | `tests/test_dispatch.py::test_without_telegram_nothing_is_marked_delivered` | ci.yml `api` |
| AC-04 | dedupe by key returns `outcome: existing` | `app.py` `queue` | `tests/test_api.py::test_queueing_is_idempotent_by_dedupe_key`, `test_concurrent_queue_with_one_key_keeps_one_entry` | ci.yml `api` |
| AC-05 | existing entry only to readers | `app.py` `queue` (`allow` read, `not_found=True`) | `tests/test_api.py::test_existing_entry_is_returned_only_to_readers` | ci.yml `api` |
| AC-06 | invalid relay envelopes rejected | `tibo_reset.py` `parse_relay` | `tests/test_tibo_reset.py::test_reject_invalid_source_envelope` | ci.yml `api` |
| AC-07 | reset alert own message, quiet hours apply, marker never delivered | `dispatch.py` `WHOLE_MESSAGE_KINDS`, `tibo_reset.py` `run_monitor` | `tests/test_tibo_dispatch.py::test_reset_alert_is_separate_quiet_hours_apply_and_state_is_never_delivered` | ci.yml `api` |
| P-01 | rules list / get / create / patch with `expected_version` | `app.py` rule routes | `tests/test_api.py::test_rules_lifecycle` | ci.yml `api` |
| P-02 | log list filters, stats by type / severity / channel | `app.py` `list_notifications`, `stats` | `tests/test_api.py::test_filters_and_stats` | ci.yml `api` |
| P-03 | delivery `delivered` / `failed` count an attempt; `console` does not | `app.py` `record_delivery` | `tests/test_api.py::test_delivery_results` | ci.yml `api` |
| P-04 | at most 20 lines / 4000 chars per digest | `dispatch.py` `digest`, `MAX_PER_DIGEST`, `MAX_MESSAGE` | `tests/test_dispatch.py::test_long_titles_never_drop_lines_silently`, `test_long_reviews_are_split_not_cut` | ci.yml `api` |
| P-05 | at most 3 delivery attempts | `dispatch.py` `MAX_ATTEMPTS` (pending filter) | `tests/test_api.py::test_delivery_results` (filter `max_attempts=3`); `tests/test_dispatch.py::test_digest_delivery_quiet_hours_and_failures` (fake applies the same filter) | ci.yml `api` |
| P-06 | broken rule or entry skipped, not fatal | `dispatch.py` `run_rules`, `_record` | `tests/test_dispatch.py::test_a_broken_rule_config_is_skipped`, `test_a_failed_record_does_not_stop_the_others`, `test_a_failed_queue_keeps_the_rule_due` | ci.yml `api` |
| P-07 | delivered explicit reminders acknowledged on the task | `dispatch.py` `deliver`, `_already_delivered` | `tests/test_dispatch.py::test_delivered_reminders_are_acknowledged_on_the_task`, `test_a_lost_reminder_ack_is_retried_without_resending` | ci.yml `api` |
| P-08 | MCP never causes an outside message (inactive rule create, console notes, no rule update) | `mcp_server.py` `build_server` | `tests/test_mcp.py::test_notes_are_console_only_and_cannot_use_delivered_kinds`, `test_rules_are_created_inactive_and_cannot_change_channels_or_activation`, `test_there_is_no_rule_update_tool_so_an_active_rule_cannot_be_widened` | ci.yml `api` |
| P-09 | RLS per workspace; authz through empire-platform | migration, `app.py` `bound`, `allow`, `write` | `tests/test_api.py::test_permissions_and_isolation`, `test_runtime_role_sees_nothing_without_a_workspace`, `tests/test_e2e_platform.py` (skipped in CI) | ci.yml `api` |
| P-10 | signing-key rollover | `auth.py` `TokenVerifier.extra_public_key_pems` | `tests/test_rollover_key.py` | ci.yml `api` |
| P-11 | errors to Sentry without personal data | `_sentry.py` | `tests/test_sentry.py` | ci.yml `api` |
| M-01..04 | Measures (delivery within one run, 0 exhausted entries, 0 quiet-hour sends, 5xx / p95) | — | — | **gap**: ไม่มี metric / dashboard ใดในโค้ดวัดค่าเหล่านี้ |

Sources: `PRODUCT.md`, `specs/001-l2-baseline/spec.md`, `tests/*.py`, `src/solo_notifications/*.py`, `.github/workflows/ci.yml`

## Open questions

- SPEC-001 ยัง "in review", owner approval pending; รีวิว 2026-10-24 ขอแก้ตาราง §4 ที่เสีย
- `tests/test_e2e_platform.py` ข้ามใน CI (`pytest.importorskip("platform_api")`) — ไม่มีหลักฐานการรันกับ empire-platform จริงใน CI
