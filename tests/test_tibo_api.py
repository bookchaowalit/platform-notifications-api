"""The monitor's state and alerts survive restarts through the real local API."""

import uuid
from datetime import UTC, datetime, timedelta

from conftest import OWNER_ACTIONS

from solo_notifications import dispatch, tibo_reset


def test_seed_restart_dedupe_and_delivery_through_notifications_api(
    client, token, platform
):
    actor, workspace = uuid.uuid4(), uuid.uuid4()
    platform.grants[actor] = set(OWNER_ACTIONS)
    headers = token(actor, workspace)

    class LocalHttp:
        # Only the external HTTP boundary is replaced; API validation, auth, transactions,
        # dedupe and durable rows all run against the disposable PostgreSQL fixture.
        def call(self, method, path, body=None, params=None):
            response = client.request(
                method, path, json=body, params=params, headers=headers
            )
            if response.is_error:
                raise dispatch.ApiError(response.status_code, path)
            return response.json()

    now = datetime(2026, 10, 15, 6, tzinfo=UTC)
    enabled = {"TIBO_RESET_MONITOR_ENABLED": "1"}
    old = tibo_reset.Post(
        "100",
        "Day 9/ Reset",
        now - timedelta(days=1),
        "https://x.com/thsottiaux/status/100",
    )
    notes = dispatch.Notifications(LocalHttp())
    seeded = tibo_reset.run_monitor(notes, now, env=enabled, fetch=lambda *_: [old])
    assert seeded["initialized"]
    assert seeded["queued"] == 0
    assert seeded["errors"] == 0

    # A new client has no process-local memory. It must recover the seed and dedupe
    # from the API, queue only the new post, and preserve its original source.
    restarted = dispatch.Notifications(LocalHttp())
    fresh = tibo_reset.Post(
        "101",
        "We have reset usage limits for Codex",
        now + timedelta(minutes=1),
        "https://x.com/thsottiaux/status/101",
    )
    first = tibo_reset.run_monitor(
        restarted,
        now + timedelta(minutes=30),
        env=enabled,
        fetch=lambda *_: [old, fresh],
    )
    repeat = tibo_reset.run_monitor(
        restarted, now + timedelta(hours=1), env=enabled, fetch=lambda *_: [old, fresh]
    )
    assert first["queued"] == 1
    assert first["errors"] == 0
    assert repeat["queued"] == 0
    assert repeat["errors"] == 0

    sent = []
    delivery = dispatch.deliver(
        restarted,
        sent.append,
        now + timedelta(hours=1),
        dry_run=False,
        clock=lambda: now + timedelta(hours=1),
    )
    assert delivery["delivered"] == 1
    assert len(sent) == 1
    assert "https://x.com/thsottiaux/status/101" in sent[0]
    assert "ประกาศว่ารีเซ็ตแล้ว" in sent[0]
    assert "Data: codex-reset.com" in sent[0]
    assert (
        dispatch.deliver(
            restarted,
            sent.append,
            now + timedelta(hours=1),
            dry_run=False,
            clock=lambda: now + timedelta(hours=1),
        )["delivered"]
        == 0
    )
    marker = restarted.lookup(tibo_reset.STATE_KEY)
    assert marker["delivery_attempts"] == 0
    assert marker["delivered_at"] is None
    entries = client.get("/v1/notifications", headers=headers).json()["items"]
    assert len(entries) == 2
    alert = next(item for item in entries if item["notification_type"] == "tibo_reset")
    assert alert["metadata"]["category"] == "confirmed"
    assert alert["delivery_channel"] == "telegram"
    assert alert["delivery_attempts"] == 1
