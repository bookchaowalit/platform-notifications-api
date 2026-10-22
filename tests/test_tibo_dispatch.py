"""Tibo alerts share existing delivery and survive independent source/task outages."""

import json

from test_dispatch import NIGHT, NOON, FakeNotes, FakeTasks

from solo_notifications import dispatch


def test_reset_alert_is_separate_quiet_hours_apply_and_state_is_never_delivered():
    notes = FakeNotes([])
    for key, kind in (
        ("marker01", "tibo_reset_monitor_state"),
        ("alert001", "tibo_reset"),
    ):
        notes.queue(
            {
                "dedupe_key": key,
                "notification_type": kind,
                "title": "Tibo",
                "message": "reset original",
            }
        )
    sent = []
    assert (
        dispatch.deliver(notes, sent.append, NIGHT, dry_run=False, clock=lambda: NIGHT)[
            "waiting"
        ]
        == 1
    )
    assert not sent
    assert (
        dispatch.deliver(notes, sent.append, NOON, dry_run=False, clock=lambda: NOON)[
            "delivered"
        ]
        == 1
    )
    assert sent == ["Tibo\n\nreset original"]
    assert notes.entries[0]["delivered_at"] is None


def test_dedupe_lookup_passes_exact_key_without_fetching_full_log():
    class Http:
        def call(self, method, path, params):
            assert (method, path, params) == (
                "GET",
                "/v1/notifications",
                {"dedupe_key": "state-key", "limit": 1},
            )
            return {"items": [{"id": 8}]}

    assert dispatch.Notifications(Http()).lookup("state-key") == {"id": 8}


def test_source_and_task_rule_outages_do_not_prevent_pending_delivery(
    monkeypatch, capsys
):
    notes = FakeNotes([])
    notes.queue(
        {
            "dedupe_key": "existing1",
            "notification_type": "platform_check",
            "title": "Existing",
            "message": "ready",
        }
    )

    def down(*args, **kwargs):
        raise dispatch.ApiError(503, "GET tasks")

    for key in ("EMPIRE_TASKS_API", "EMPIRE_NOTIFICATIONS_API"):
        monkeypatch.setenv(key, "https://example.test")
    monkeypatch.setenv("NOTIFICATIONS_DISPATCH_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake")
    monkeypatch.setenv("TIBO_RESET_MONITOR_ENABLED", "1")
    monkeypatch.setattr(dispatch, "Notifications", lambda http: notes)
    monkeypatch.setattr(dispatch, "Tasks", lambda http: FakeTasks([]))
    monkeypatch.setattr(dispatch, "run_rules", down)
    monkeypatch.setattr(dispatch, "run_tibo_monitor", down, raising=False)
    monkeypatch.setattr(dispatch, "quiet", lambda now: False)
    sent = []
    monkeypatch.setattr(dispatch, "telegram_sender", lambda *args: sent.append)
    result = dispatch.main([])
    assert sent == ["Existing\n\nready"]
    assert notes.entries[0]["delivered_at"] == "now"
    assert result == 1
    assert json.loads(capsys.readouterr().out)["errors"] >= 2
