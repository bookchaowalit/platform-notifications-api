# SPEC-001 — L2 baseline: locked dependencies, strict types, domain rules under test

Standard: ADR-0004 — TDD + SDD (architecture-docs, `docs/architecture/decisions/ADR-0004-tdd-sdd-standard.md`).
Big Loop Plan Wave 2 (solo-empire `repository-catalog/roadmaps/BIG-LOOP-PLAN-2026-10-21.md`).

## Document control

| Field | Value |
|---|---|
| Spec ID / version | SPEC-001 / 1.0 |
| Status | in review (implemented; owner approval pending) |
| Repository / path | bookchaowalit/platform-notifications-api / `specs/001-l2-baseline/spec.md` |
| Owner (maker) | Claude Code (Wave 2 cells session) |
| Reviewer (checker) | Fresh Claude reviewer session (no maker context) |
| Approver | Owner (bookchaowalit): pending, see the Wave 2 report owner actions |
| Requirement links | Big Loop DoD L2: `PRODUCT.md`, ≥ 3 test files, Sentry, type check; L1 lockfile |
| Main-only | yes: committed straight to `main`, so the red run is local (ADR-0004 rule 2, v1.1) |
| Created / approved | 2026-10-22 / pending owner |

## 1. Problem and intent

- **Problem:** The notifications cell (API and dispatcher from one image) had no lockfile (pip installed unpinned versions) and no type, lint or coverage gates in CI; its dispatcher and Tibo-monitor rules were already covered by their own test files.
- **Outcome:** the image installs exactly `uv.lock` (`uv sync --frozen`), CI runs Ruff, mypy
  (strict on `src/`) and a coverage gate on shared CI v1.1.1, and the cell's own rules have
  tests of their own beyond the generic contract suite.
- **Why now:** Wave 2 of the Big Loop Plan; milestone M1 is 2026-11-15.

## 2. Scope

| In scope | Out of scope (non-goals) |
|---|---|
| Lock file and frozen install, CI caller bump, Ruff/mypy config and fixes, rule tests, `PRODUCT.md` | New endpoints or fields; schema changes |

## 3. Behaviour — scenarios

### AC-01 — Queue once a day

- **Given** an active rule that matched a task
- **When** the dispatcher runs again the same Bangkok day
- **Then** no second entry is queued and the check interval is respected
- **Test:** `tests/test_dispatch.py::test_queue_once_a_day_and_respect_the_check_interval` · level: unit / API with local fakes

### AC-02 — Quiet hours

- **Given** pending entries
- **When** a message would go out between 22:00 and 07:00 Bangkok time
- **Then** it is held, checked before each message
- **Test:** `tests/test_dispatch.py::test_quiet_hours_are_checked_before_each_message` · level: unit / API with local fakes

### AC-03 — No Telegram, no delivery

- **Given** no Telegram configuration
- **When** the dispatcher runs
- **Then** nothing is marked delivered
- **Test:** `tests/test_dispatch.py::test_without_telegram_nothing_is_marked_delivered` · level: unit / API with local fakes

### AC-04 — Dedupe by key

- **Given** an entry queued with a dedupe key
- **When** the same key is queued again
- **Then** the existing entry is returned with outcome existing
- **Test:** `tests/test_api.py::test_queueing_is_idempotent_by_dedupe_key` · level: unit / API with local fakes

### AC-05 — Existing entries only to readers

- **Given** a writer without read permission
- **When** they queue a known dedupe key
- **Then** the existing entry is not revealed
- **Test:** `tests/test_api.py::test_existing_entry_is_returned_only_to_readers` · level: unit / API with local fakes

### AC-06 — Invalid relay envelopes

- **Given** a relay feed with a wrong profile, stale or naive timestamp, or bad tweets
- **When** it is parsed
- **Then** ValueError with the specific reason
- **Test:** `tests/test_tibo_reset.py::test_reject_invalid_source_envelope` · level: unit / API with local fakes

### AC-07 — Reset alerts respect quiet hours and never deliver state

- **Given** the Tibo monitor enabled
- **When** an accepted post is queued
- **Then** it is its own message, quiet hours apply, the state marker is never delivered
- **Test:** `tests/test_tibo_dispatch.py::test_reset_alert_is_separate_quiet_hours_apply_and_state_is_never_delivered` · level: unit / API with local fakes

## 4. Interfaces and data

| Interface | Change | Contract |
|---|---|---|
| G | E | T |
| G | E | T |
| P | O | S |
| n | o | t |
| s | o | l |

- **Data model / migration:** N/A — no schema change.
- **Backward compatibility:** same routes and payloads; only tooling, locks, typing fixes and tests changed unless a scenario above says otherwise.

## 5. Non-functional requirements

| NFR | Target | How it is tested |
|---|---|---|
| Performance | N/A — no hot-path change beyond one comparison per declared range | — |
| Security / privacy | Authorization unchanged; Sentry sends no PII and scrubs tokens/URLs | existing auth tests, Sentry tests |
| Reliability / observability | Reproducible install from the lock file; opt-in Sentry error reporting | image rehearsal before push; Sentry tests |
| Accessibility / UX | N/A — API only | — |

## 6. Test plan (TDD)

- **Test levels:** unit and API tests with local fakes; no hosted service is called.
- **Test data:** generated in each test; no production data.
- **Coverage:** CI gate `coverage-threshold: "77"` (measured 79.79% on 2026-10-22, minus 2 points).
- **Not test-first (exceptions):** tests that pin behaviour the service already had
  (characterization tests, marked below) were written after the code by nature; tooling and
  config changes are covered by CI itself.

### Red / green log

| Scenario | Test | Red: commit / run + failure message | Green: commit / run | Refactor note |
|---|---|---|---|---|
| AC-01 | `tests/test_dispatch.py::test_queue_once_a_day_and_respect_the_check_interval` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-02 | `tests/test_dispatch.py::test_quiet_hours_are_checked_before_each_message` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-03 | `tests/test_dispatch.py::test_without_telegram_nothing_is_marked_delivered` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-04 | `tests/test_api.py::test_queueing_is_idempotent_by_dedupe_key` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-05 | `tests/test_api.py::test_existing_entry_is_returned_only_to_readers` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-06 | `tests/test_tibo_reset.py::test_reject_invalid_source_envelope` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |
| AC-07 | `tests/test_tibo_dispatch.py::test_reset_alert_is_separate_quiet_hours_apply_and_state_is_never_delivered` | characterization (behaviour already present; passed on first run) | CI run of the pushed tip | none |

## 7. Risks and open questions

| # | Risk or question | Impact | Owner | Resolution / date |
|---|---|---|---|---|
| 1 | Stored rows written before a range rule existed may already be inverted | a patch touching one of those dates is refused until both are fixed | owner | patches that do not touch the range still work |

## 8. Change log

| Version | Date | Change | Scenarios affected | Approved by |
|---|---|---|---|---|
| 1.0 | 2026-10-22 | Implemented with the Wave 2 baseline | all | pending owner |

## Approval

| Gate | Role | Name / date | Decision | Evidence |
|---|---|---|---|---|
| Implemented (DoD) | Reviewer (checker) | Fresh Claude reviewer / 2026-10-22 | see Wave 2 report | CI run of the pushed tip |
| Implemented (DoD) | Approver | Owner | pending | Wave 2 report |
