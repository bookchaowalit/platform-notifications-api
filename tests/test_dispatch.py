"""The dispatcher: rule evaluation, once-a-day queueing, quiet hours, digest delivery and failures."""

from datetime import UTC, datetime

from solo_notifications import dispatch

NOON = datetime(2026, 10, 10, 5, 0, tzinfo=UTC)  # 12:00 in Bangkok
NIGHT = datetime(2026, 10, 10, 16, 0, tzinfo=UTC)  # 23:00 in Bangkok


class FakeTasks:
    def __init__(self, tasks):
        self.tasks, self.acks = tasks, []

    def reminder_delivered(self, task_uuid, reminder_at, channel):
        self.acks.append((task_uuid, reminder_at, channel))

    def list(self, **params):
        rows = self.tasks
        if "status" in params:
            rows = [t for t in rows if t["status"] in params["status"]]
        if "due_before" in params:
            rows = [
                t
                for t in rows
                if t["due_date"] and t["due_date"] < params["due_before"]
            ]
        if "reminder_due_before" in params:
            rows = [
                t
                for t in rows
                if t.get("reminder_at")
                and t["reminder_at"] <= params["reminder_due_before"]
            ]
        return rows


class FakeNotes:
    def __init__(self, rules):
        self.rules_list, self.entries, self.keys, self.touched, self.records = (
            rules,
            [],
            {},
            [],
            [],
        )

    def rules(self):
        return self.rules_list

    def touch_rule(self, rule_id, fields):
        self.touched.append((rule_id, fields))
        for r in self.rules_list:
            if r["id"] == rule_id:
                r.update(fields)

    def queue(self, fields):
        if getattr(self, "queue_down", False):
            raise dispatch.ApiError(503, "POST /v1/notifications")
        if fields["dedupe_key"] in self.keys:
            return "existing"
        entry = {
            **fields,
            "id": len(self.entries) + 1,
            "delivered_at": None,
            "delivery_attempts": 0,
        }
        self.entries.append(entry)
        self.keys[fields["dedupe_key"]] = entry["id"]
        return "created"

    def delivered_reminders(self, task_id):
        return [
            e
            for e in self.entries
            if e["notification_type"] == "reminder"
            and e["task_id"] == task_id
            and e["delivered_at"]
        ]

    def pending(self, kinds, since):
        return [
            e
            for e in self.entries
            if e["notification_type"] in kinds
            and not e["delivered_at"]
            and e["delivery_attempts"] < dispatch.MAX_ATTEMPTS
        ]

    def record(self, entry_id, outcome, *, channel=None, error=None):
        if entry_id in getattr(self, "broken", ()):
            raise dispatch.ApiError(503, "POST delivery")
        e = self.entries[entry_id - 1]
        e["delivery_attempts"] += 1
        if outcome == "delivered":
            e["delivered_at"] = "now"
        self.records.append((entry_id, outcome, channel, error))


def task(n, **extra):
    return {
        "id": n,
        "number": n,
        "uuid": f"uuid-{n}",
        "title": f"Task {n}",
        "status": "todo",
        "priority": 3,
        "due_date": None,
        "project": "ops",
        **extra,
    }


def rule(rid, kind, **extra):
    return {
        "id": rid,
        "legacy_id": rid,
        "rule_type": kind,
        "config": {},
        "check_interval_minutes": 60,
        "last_checked_at": None,
        **extra,
    }


def test_rules_match_like_the_local_runner():
    tasks = FakeTasks(
        [
            task(1, due_date="2026-10-01", priority=1),
            task(2, due_date="2026-10-11"),
            task(3, status="blocked"),
            task(4, status="done", due_date="2026-10-01"),
            task(5, status="in_progress", priority=2),
        ]
    )
    kinds = {
        k: sorted(m["task_id"] for m in dispatch.evaluate(rule(1, k), tasks, NOON))
        for k in ("overdue", "due_soon", "high_priority", "blocked")
    }
    assert kinds == {
        "overdue": [1],
        "due_soon": [2],
        "high_priority": [1, 5],
        "blocked": [3],
    }
    assert (
        dispatch.evaluate(rule(1, "overdue"), tasks, NOON)[0]["severity"] == "critical"
    )


def test_queue_once_a_day_and_respect_the_check_interval():
    notes = FakeNotes([rule(1, "overdue")])
    tasks = FakeTasks([task(1, due_date="2026-10-01")])
    first = dispatch.run_rules(notes, tasks, NOON, dry_run=False)
    assert (first["queued"], notes.rules_list[0]["last_triggered_at"]) == (
        1,
        NOON.isoformat(),
    )
    notes.rules_list[0]["last_checked_at"] = None  # force a re-check the same day
    again = dispatch.run_rules(notes, tasks, NOON, dry_run=False)
    assert again == {"rules": 1, "matched": 1, "queued": 0, "errors": 0}
    skipped = dispatch.run_rules(
        notes, tasks, NOON, dry_run=False
    )  # checked a moment ago
    assert skipped["rules"] == 0


def test_dry_run_queues_and_touches_nothing():
    notes = FakeNotes([rule(1, "overdue")])
    counts = dispatch.run_rules(
        notes, FakeTasks([task(1, due_date="2026-10-01")]), NOON, dry_run=True
    )
    assert counts["matched"] == 1
    assert notes.entries == []
    assert notes.touched == []


def test_digest_delivery_quiet_hours_and_failures():
    notes = FakeNotes([rule(1, "overdue")])
    dispatch.run_rules(
        notes,
        FakeTasks([task(n, due_date="2026-10-01") for n in range(1, 25)]),
        NOON,
        dry_run=False,
    )
    notes.entries.append(
        {
            "id": len(notes.entries) + 1,
            "notification_type": "engagement_review",
            "title": "Review",
            "message": "3 engagements",
            "delivered_at": None,
            "delivery_attempts": 0,
        }
    )
    sent = []
    assert (
        dispatch.deliver(notes, sent.append, NIGHT, dry_run=False, clock=lambda: NIGHT)[
            "waiting"
        ]
        == 25
    )
    assert sent == []
    counts = dispatch.deliver(
        notes, sent.append, NOON, dry_run=False, clock=lambda: NOON
    )
    assert counts == {"delivered": 21, "failed": 0, "waiting": 4, "errors": 0}
    assert len(sent) == 2
    assert sent[0].startswith("🔔 Solo Empire")
    assert sent[0].count("\n") == 20
    assert all(
        channel == "telegram"
        for _, outcome, channel, _ in notes.records
        if outcome == "delivered"
    )

    def broken(text):
        raise RuntimeError("telegram_http_502")

    failed = dispatch.deliver(notes, broken, NOON, dry_run=False, clock=lambda: NOON)
    assert failed["failed"] == 4
    assert notes.records[-1][1:] == (
        "failed",
        None,
        "telegram_http_502",
    )


def test_without_telegram_nothing_is_marked_delivered():
    notes = FakeNotes([rule(1, "blocked")])
    dispatch.run_rules(
        notes, FakeTasks([task(1, status="blocked")]), NOON, dry_run=False
    )
    assert dispatch.deliver(notes, None, NOON, dry_run=False) == {
        "delivered": 0,
        "failed": 0,
        "waiting": 1,
        "errors": 0,
    }
    assert notes.records == []


def test_main_reports_missing_config(capsys, monkeypatch):
    for key in (
        "EMPIRE_TASKS_API",
        "EMPIRE_NOTIFICATIONS_API",
        "NOTIFICATIONS_DISPATCH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    assert dispatch.main([]) == 2
    assert "missing_config" in capsys.readouterr().out


def test_long_titles_never_drop_lines_silently():
    notes = FakeNotes([rule(1, "blocked")])
    dispatch.run_rules(
        notes,
        FakeTasks(
            [task(n, status="blocked", title="x" * 499 + str(n)) for n in range(1, 21)]
        ),
        NOON,
        dry_run=False,
    )
    assert all(len(e["title"]) <= dispatch.TITLE for e in notes.entries)
    sent = []
    counts = dispatch.deliver(
        notes, sent.append, NOON, dry_run=False, clock=lambda: NOON
    )
    assert len(sent[0]) <= dispatch.MAX_MESSAGE
    assert counts["delivered"] + counts["waiting"] == 20
    assert counts["delivered"] == sent[0].count(
        "\n"
    )  # every recorded entry is in the message


def test_long_reviews_are_split_not_cut():
    notes = FakeNotes([])
    notes.entries.append(
        {
            "id": 1,
            "notification_type": "engagement_review",
            "title": "Review",
            "message": "y" * 9000,
            "delivered_at": None,
            "delivery_attempts": 0,
        }
    )
    sent = []
    dispatch.deliver(notes, sent.append, NOON, dry_run=False, clock=lambda: NOON)
    assert len(sent) == 3
    assert sum(map(len, sent)) == len("Review\n\n") + 9000


def test_a_failed_record_does_not_stop_the_others(monkeypatch):
    monkeypatch.setattr(dispatch.time, "sleep", lambda _s: None)
    notes = FakeNotes([rule(1, "blocked")])
    dispatch.run_rules(
        notes,
        FakeTasks([task(n, status="blocked") for n in (1, 2, 3)]),
        NOON,
        dry_run=False,
    )
    notes.broken = {2}
    counts = dispatch.deliver(notes, [].append, NOON, dry_run=False, clock=lambda: NOON)
    assert counts["errors"] == 1
    assert [e["delivered_at"] for e in notes.entries] == [
        "now",
        None,
        "now",
    ]


def test_a_broken_rule_config_is_skipped():
    notes = FakeNotes(
        [
            rule(1, "due_soon", config={"hours": None}),
            rule(2, "stale", config={"days": "x"}),
            rule(3, "blocked"),
        ]
    )
    counts = dispatch.run_rules(
        notes, FakeTasks([task(1, status="blocked")]), NOON, dry_run=False
    )
    assert counts["queued"] == 1
    assert counts["errors"] == 0


def test_delivered_reminders_are_acknowledged_on_the_task():
    tasks = FakeTasks([task(7, reminder_at="2026-10-10T04:00:00+00:00")])
    notes = FakeNotes([rule(1, "reminder")])
    dispatch.run_rules(notes, tasks, NOON, dry_run=False)
    dispatch.deliver(
        notes, [].append, NOON, dry_run=False, tasks=tasks, clock=lambda: NOON
    )
    assert tasks.acks == [("uuid-7", "2026-10-10T04:00:00+00:00", "telegram")]


def test_quiet_hours_are_checked_before_each_message():
    notes = FakeNotes([rule(1, "blocked")])
    dispatch.run_rules(
        notes, FakeTasks([task(1, status="blocked")]), NOON, dry_run=False
    )
    counts = dispatch.deliver(
        notes, [].append, NOON, dry_run=False, clock=lambda: NIGHT
    )
    assert counts["delivered"] == 0
    assert counts["waiting"] == 1


def test_a_malformed_bot_token_is_refused_without_echo(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        dispatch.telegram_sender("bad token/../x", "1")
    assert "bad token" not in str(exc.value)


def test_a_failed_queue_keeps_the_rule_due():
    notes = FakeNotes([rule(1, "blocked")])
    notes.queue_down = True
    counts = dispatch.run_rules(
        notes, FakeTasks([task(1, status="blocked")]), NOON, dry_run=False
    )
    assert counts["errors"] == 1
    assert notes.touched == []


def test_a_lost_reminder_ack_is_retried_without_resending():
    tasks = FakeTasks([task(7, reminder_at="2026-10-10T04:00:00+00:00")])
    notes = FakeNotes([rule(1, "reminder")])
    dispatch.run_rules(notes, tasks, NOON, dry_run=False)
    dispatch.deliver(
        notes, [].append, NOON, dry_run=False, clock=lambda: NOON
    )  # no tasks: the ack is lost
    later = NOON + dispatch.timedelta(days=1)
    notes.rules_list[0]["last_checked_at"] = None
    counts = dispatch.run_rules(notes, tasks, later, dry_run=False)
    assert counts["queued"] == 0
    assert len(notes.entries) == 1
    assert tasks.acks == [("uuid-7", "2026-10-10T04:00:00+00:00", "telegram")]


def test_a_partly_sent_review_is_not_resent():
    notes = FakeNotes([])
    notes.entries.append(
        {
            "id": 1,
            "notification_type": "engagement_review",
            "title": "Review",
            "message": "y" * 9000,
            "delivered_at": None,
            "delivery_attempts": 0,
        }
    )
    calls = []

    def flaky(text):
        calls.append(text)
        if len(calls) == 2:
            raise RuntimeError("telegram_http_502")

    dispatch.deliver(notes, flaky, NOON, dry_run=False, clock=lambda: NOON)
    assert notes.entries[0]["delivered_at"] == "now"


def test_platform_check_is_sent_as_its_own_message():
    notes = FakeNotes([])
    notes.entries.append(
        {
            "id": 1,
            "notification_type": "platform_check",
            "title": "Tasks platform: warning",
            "message": "- warn token_expiring",
            "delivered_at": None,
            "delivery_attempts": 0,
        }
    )
    sent = []
    counts = dispatch.deliver(
        notes, sent.append, NOON, dry_run=False, clock=lambda: NOON
    )
    assert counts["delivered"] == 1
    assert sent == ["Tasks platform: warning\n\n- warn token_expiring"]
