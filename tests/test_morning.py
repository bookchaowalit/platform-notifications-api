"""The morning digest: held task kinds, one message a day, status line and rendering."""

from datetime import UTC, date, datetime

from test_dispatch import FakeNotes, FakeTasks, rule, task

from solo_notifications import dispatch, morning

EARLY = datetime(2026, 11, 17, 0, 30, tzinfo=UTC)  # 07:30 Bangkok
MORNING = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)  # 08:00 Bangkok (Tuesday)
NOON = datetime(2026, 11, 17, 5, 0, tzinfo=UTC)  # 12:00 Bangkok
UP = {"results": [{"name": f"s{n}", "status": "up", "stale": False} for n in range(3)]}


class Notes(FakeNotes):
    def lookup(self, key):
        found = [e for e in self.entries if e.get("dedupe_key") == key]
        return found[0] if found else None


def setup(tasks, kinds=("overdue", "blocked", "stale", "reminder")):
    notes = Notes([rule(i + 1, k) for i, k in enumerate(kinds)])
    t = FakeTasks(tasks)
    dispatch.run_rules(notes, t, MORNING, dry_run=False)
    return notes, t


def run(notes, sent, now, m, tasks=None):
    due = m.due(notes, now.astimezone(dispatch.BANGKOK))
    return dispatch.deliver(
        notes,
        sent.append,
        now,
        dry_run=False,
        tasks=tasks,
        clock=lambda: now,
        morning=m,
        morning_due=due,
    )


def test_task_kinds_wait_for_the_morning_and_go_out_once():
    notes, _ = setup(
        [
            task(1, due_date="2026-10-31", title="Send 20 intros", priority=1),
            task(2, status="blocked", title="Deploy"),
        ]
    )
    m = morning.Morning(status=lambda: UP)
    sent = []
    early = run(notes, sent, EARLY, m)
    assert sent == []
    assert early["waiting"] == 3  # task 1 is overdue and stale

    counts = run(notes, sent, MORNING, m)
    assert len(sent) == 1
    text = sent[0]
    assert text.startswith("☀️ สรุปเช้า อ. 17 พ.ย.")
    assert "ระบบ ✅ ปกติ 3/3" in text
    assert "⛔ ติดขัด 1\n• Deploy" in text
    assert "⏰ เลยกำหนด 1\n• Send 20 intros · เลย 17 วัน" in text
    assert counts["delivered"] == 3
    assert "งาน (2)" in text

    later = run(notes, sent, NOON, m)
    assert len(sent) == 1  # the marker stops a second digest today
    assert later["delivered"] == 0


def test_reminders_still_go_out_right_away():
    notes, tasks = setup(
        [task(7, reminder_at="2026-11-17T00:00:00+00:00")], kinds=("reminder",)
    )
    m = morning.Morning(status=lambda: UP)
    sent = []
    run(notes, sent, EARLY, m, tasks=tasks)
    assert len(sent) == 1
    assert sent[0].startswith("🔔 เตือนงาน (1)")
    assert tasks.acks


def test_a_task_shows_once_under_its_most_urgent_kind():
    entries = [
        {"task_id": 1, "task_title": "A", "notification_type": "stale"},
        {"task_id": 1, "task_title": "A", "notification_type": "blocked"},
        {"task_id": 2, "task_title": "B", "notification_type": "stale"},
    ]
    lines = morning.task_lines(entries, date(2026, 11, 17))
    assert lines == ["งาน (2)", "⛔ ติดขัด 1", "• A", "🕸️ ไม่ขยับ 1"]


def test_long_sections_are_capped():
    entries = [
        {
            "task_id": n,
            "task_title": f"T{n}",
            "notification_type": "overdue",
            "task_due_date": "2026-11-10",
            "task_priority": 3,
        }
        for n in range(9)
    ]
    lines = morning.task_lines(entries, date(2026, 11, 17))
    assert lines[1] == "⏰ เลยกำหนด 9"
    assert lines[-1] == "  + อีก 4"
    assert len([line for line in lines if line.startswith("•")]) == morning.PER_SECTION


def test_due_soon_reads_today_and_tomorrow():
    today = date(2026, 11, 17)
    assert morning._when("due_soon", today, today) == " · วันนี้"
    assert morning._when("due_soon", date(2026, 11, 18), today) == " · พรุ่งนี้"


def test_status_line_variants():
    assert morning.status_line(None) == "ระบบ ⚠️ อ่านสถานะ monitor ไม่ได้"
    assert morning.status_line({"results": []}) == "ระบบ ⚠️ monitor ไม่มีข้อมูล"
    mixed = {
        "results": [
            {"name": "api", "status": "down", "stale": False},
            {"name": "web", "status": "up", "stale": True},
            {"name": "db", "status": "up", "stale": False},
        ]
    }
    assert morning.status_line(mixed) == "ระบบ 🔴 ล่ม 1: api · ⚪ ไม่ได้เช็ค 1 · ✅ ปกติ 1"


def test_empty_day_and_board_link():
    text = morning.render([], UP, date(2026, 11, 17), "https://board.example")
    assert "งาน ✅ ไม่มีเรื่องค้าง" in text
    assert text.endswith("→ ดูทั้งหมด: https://board.example")


def test_a_failed_send_records_failure_and_leaves_no_marker():
    notes, _ = setup([task(2, status="blocked")])
    m = morning.Morning(status=lambda: UP)

    def broken(_text):
        raise RuntimeError("telegram_http_502")

    counts = dispatch.deliver(
        notes,
        broken,
        MORNING,
        dry_run=False,
        clock=lambda: MORNING,
        morning=m,
        morning_due=True,
    )
    assert counts["failed"] == 1
    assert notes.lookup(morning.state_key(date(2026, 11, 17))) is None


def test_from_env():
    assert morning.Morning.from_env({"MORNING_DIGEST": "off"}) is None
    m = morning.Morning.from_env({"MORNING_DIGEST_HOUR": "3"})
    assert m is not None
    assert m.hour == morning.BANGKOK_HOUR_DEFAULT
    assert morning.fetch_status("http://insecure.example") is None


def test_the_digest_shows_the_current_evaluation_not_stale_queue_entries():
    notes, _ = setup([task(2, status="blocked", title="Deploy")], kinds=("blocked",))
    notes.entries.append(
        {
            "id": len(notes.entries) + 1,
            "notification_type": "blocked",
            "task_id": 9,
            "task_title": "Closed since 07:00",
            "delivered_at": None,
            "delivery_attempts": 0,
        }
    )
    current = []
    dispatch.run_rules(
        notes,
        FakeTasks([task(2, status="blocked", title="Deploy")]),
        MORNING,
        dry_run=False,
        force=True,
        current=current,
    )
    sent = []
    counts = dispatch.deliver(
        notes,
        sent.append,
        MORNING,
        dry_run=False,
        clock=lambda: MORNING,
        morning=morning.Morning(status=lambda: UP),
        morning_due=True,
        current=current,
    )
    assert "Closed since 07:00" not in sent[0]
    assert "• Deploy" in sent[0]
    assert counts["delivered"] == 2  # the stale entry is recorded, not resent tomorrow


def test_unknown_targets_are_not_reported_as_healthy():
    status = {"results": [{"name": "new", "status": "unknown", "stale": False}]}
    assert morning.status_line(status) == "ระบบ ⚪ ไม่ได้เช็ค 1 · ✅ ปกติ 0"


def test_pending_all_reads_every_page():
    class Http:
        def __init__(self):
            self.offsets = []

        def call(self, method, path, body=None, params=None):
            offset = params.get("offset") or 0
            self.offsets.append(offset)
            size = 500 if offset < 1000 else 3
            return {"items": [{"id": offset + n} for n in range(size)]}

    http = Http()
    items = dispatch.Notifications(http).pending_all(["overdue"], "2026-11-15")
    assert len(items) == 1003
    assert http.offsets == [0, 500, 1000]


def test_main_postpones_the_digest_when_rules_fail(monkeypatch):
    calls = {}

    class Broken(Notes):
        def rules(self):
            raise dispatch.ApiError(503, "GET /v1/notification-rules")

    notes = Broken([])
    for key in ("EMPIRE_TASKS_API", "EMPIRE_NOTIFICATIONS_API"):
        monkeypatch.setenv(key, "https://example.test")
    monkeypatch.setenv("NOTIFICATIONS_DISPATCH_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(dispatch, "Notifications", lambda _http: notes)
    monkeypatch.setattr(dispatch, "Tasks", lambda _http: FakeTasks([]))
    monkeypatch.setattr(dispatch, "telegram_sender", lambda *_a: [].append)
    monkeypatch.setattr(
        dispatch,
        "run_tibo_monitor",
        lambda *_a, **_k: {"enabled": False, "matched": 0, "queued": 0, "errors": 0},
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return MORNING

    monkeypatch.setattr(dispatch, "datetime", Clock)

    def deliver(*_a, **kw):
        calls.update(kw)
        return {"delivered": 0, "failed": 0, "waiting": 0, "errors": 0}

    monkeypatch.setattr(dispatch, "deliver", deliver)
    dispatch.main([])
    assert calls["morning"] is not None
    assert calls["morning_due"] is False


def test_the_digest_waits_when_the_run_reaches_quiet_hours():
    notes, _ = setup([task(2, status="blocked")])
    late = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)  # 22:00 Bangkok
    sent = []
    counts = dispatch.deliver(
        notes,
        sent.append,
        MORNING,
        dry_run=False,
        clock=lambda: late,
        morning=morning.Morning(status=lambda: UP),
        morning_due=True,
    )
    assert sent == []
    assert counts["waiting"] == 1
    assert notes.lookup(morning.state_key(date(2026, 11, 17))) is None


def test_a_failed_digest_on_an_empty_day_is_an_error():
    notes = Notes([])

    def broken(_text):
        raise RuntimeError("telegram_http_502")

    counts = dispatch.deliver(
        notes,
        broken,
        MORNING,
        dry_run=False,
        clock=lambda: MORNING,
        morning=morning.Morning(status=lambda: UP),
        morning_due=True,
    )
    assert counts["errors"] == 1


def test_jobs_line_names_late_and_failed_jobs():
    assert morning.jobs_line(UP) is None  # an older monitor sends no summary
    ok = {**UP, "jobs": {"total": 25, "late": [], "failed": []}}
    assert morning.jobs_line(ok) == "งาน cron ✅ ปกติ 25/25"
    bad = {
        **UP,
        "jobs": {
            "total": 25,
            "late": ["posture-monitor"],
            "failed": ["credits-worker"],
        },
    }
    assert (
        morning.jobs_line(bad)
        == "งาน cron 🔴 ล้ม 1: credits-worker · 🟡 ไม่รายงาน 1: posture-monitor"
    )
    text = morning.render([], bad, date(2026, 11, 17))
    assert "ระบบ ✅ ปกติ 3/3\nงาน cron 🔴 ล้ม 1" in text
