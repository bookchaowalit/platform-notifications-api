"""Notifications API: rules, idempotent queueing, delivery results, isolation, authorization."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import OWNER_ACTIONS


@pytest.fixture
def owner(platform):
    actor, ws = uuid.uuid4(), uuid.uuid4()
    platform.grants[actor] = set(OWNER_ACTIONS)
    return actor, ws


def queue(client, headers, **extra):
    body = {
        "title": "Task reminder",
        "message": "Pay the invoice",
        "notification_type": "overdue",
        "severity": "warning",
        "task_id": 7,
        "task_title": "Pay",
        "task_status": "todo",
        **extra,
    }
    return client.post("/v1/notifications", json=body, headers=headers)


def test_rules_lifecycle(client, token, owner, platform):
    headers = token(*owner)
    rule = client.post(
        "/v1/notification-rules",
        headers=headers,
        json={
            "name": "Overdue tasks",
            "rule_type": "overdue",
            "channels": ["console", "telegram"],
            "legacy_id": 1,
        },
    ).json()
    assert rule["config"] == {}
    assert rule["channels"] == ["console", "telegram"]
    assert rule["is_active"] is True
    checked = client.patch(
        f"/v1/notification-rules/{rule['id']}",
        headers=headers,
        json={"last_checked_at": "2026-10-10T08:00:00+00:00", "expected_version": 1},
    ).json()
    assert checked["version"] == 2
    assert checked["last_checked_at"].startswith("2026-10-10")
    assert client.get(
        "/v1/notification-rules", headers=headers, params={"active": True}
    ).json()["items"]
    assert (
        client.patch(
            f"/v1/notification-rules/{rule['id']}", headers=headers, json={"name": None}
        ).status_code
        == 422
    )
    audited = [
        e
        for e in platform.executions.values()
        if e.get("resource_id") == str(rule["id"])
    ]
    assert {e["action"] for e in audited} == {
        "notifications.rule.created",
        "notifications.rule.updated",
    }


def test_queueing_is_idempotent_by_dedupe_key(client, token, owner, platform):
    headers = token(*owner)
    key = f"task:7:{uuid.uuid4().hex}"
    first = queue(client, headers, dedupe_key=key)
    again = queue(client, headers, dedupe_key=key, message="different text")
    assert first.status_code == 201
    assert first.json()["outcome"] == "created"
    assert again.json()["outcome"] == "existing"
    assert again.json()["notification"]["id"] == first.json()["notification"]["id"]
    assert again.json()["notification"]["message"] == "Pay the invoice"
    executions = [
        e
        for e in platform.executions.values()
        if e.get("action") == "notifications.notification.queued"
        and e.get("resource_id") == str(first.json()["notification"]["id"])
    ]
    assert len(executions) == 1  # the replay started no execution


def test_concurrent_queue_with_one_key_keeps_one_entry(client, token, owner, cluster):
    headers = token(*owner)
    key = f"task:8:{uuid.uuid4().hex}"
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(lambda _: queue(client, headers, dedupe_key=key).json(), range(3))
        )
    assert len({r["notification"]["id"] for r in results}) == 1
    with cluster.connect("postgres", "notifications") as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM notifications.log WHERE dedupe_key = %s", (key,)
            ).fetchone()[0]
            == 1
        )


def test_delivery_results(client, token, owner):
    headers = token(*owner)
    entry = queue(
        client, headers, notification_type="engagement_review", metadata={"review": 3}
    ).json()["notification"]
    path = f"/v1/notifications/{entry['id']}/delivery"
    failed = client.post(
        path, headers=headers, json={"outcome": "failed", "error": "telegram 502"}
    ).json()
    assert (
        failed["delivery_attempts"],
        failed["delivery_error"],
        failed["delivered_at"],
    ) == (1, "telegram 502", None)
    pending = client.get(
        "/v1/notifications",
        headers=headers,
        params={
            "notification_type": "engagement_review",
            "undelivered": True,
            "max_attempts": 3,
        },
    ).json()["items"]
    assert [p["id"] for p in pending] == [entry["id"]]
    assert pending[0]["metadata"] == {"review": 3}
    assert (
        client.post(
            path, headers=headers, json={"outcome": "delivered", "channel": "console"}
        ).status_code
        == 422
    )
    done = client.post(
        path, headers=headers, json={"outcome": "delivered", "channel": "telegram"}
    ).json()
    assert (
        done["delivery_attempts"],
        done["delivery_error"],
        done["delivery_channel"],
    ) == (2, None, "telegram")
    assert done["delivered_at"] is not None
    console = client.post(path, headers=headers, json={"outcome": "console"}).json()
    assert console["channel"] == "console"
    assert console["delivery_attempts"] == 2


def test_filters_and_stats(client, token, owner):
    headers = token(*owner)
    queue(client, headers, notification_type="due_soon", severity="info")
    queue(client, headers, notification_type="due_soon", severity="critical")
    stats = client.get(
        "/v1/notifications/stats", headers=headers, params={"group_by": "severity"}
    ).json()["groups"]
    assert stats["critical"] >= 1
    newest = client.get(
        "/v1/notifications",
        headers=headers,
        params={"notification_type": ["due_soon"], "order": "desc", "limit": 1},
    ).json()["items"]
    assert newest[0]["severity"] == "critical"


def test_permissions_and_isolation(client, token, owner, platform):
    entry = queue(client, token(*owner)).json()["notification"]
    reader = uuid.uuid4()
    platform.grants[reader] = {"notifications.notification:read"}
    assert (
        client.get(
            f"/v1/notifications/{entry['id']}", headers=token(reader, owner[1])
        ).status_code
        == 200
    )
    assert queue(client, token(reader, owner[1])).status_code == 403
    assert (
        client.post(
            f"/v1/notifications/{entry['id']}/delivery",
            headers=token(reader, owner[1]),
            json={"outcome": "failed"},
        ).status_code
        == 403
    )
    stranger, other_ws = uuid.uuid4(), uuid.uuid4()
    platform.grants[stranger] = set(OWNER_ACTIONS)
    assert (
        client.get(
            f"/v1/notifications/{entry['id']}", headers=token(stranger, other_ws)
        ).status_code
        == 404
    )
    assert (
        client.get("/v1/notifications", headers=token(stranger, other_ws)).json()[
            "items"
        ]
        == []
    )


def test_invalid_input_and_platform_down(client, token, owner, platform):
    headers = token(*owner)
    assert queue(client, headers, notification_type="Bad Type").status_code == 422
    assert queue(client, headers, unknown_field=1).status_code == 422
    assert queue(client, headers, rule_id=999999).status_code == 422
    platform.down = True
    assert client.get("/v1/notifications", headers=headers).status_code == 503


def test_runtime_role_sees_nothing_without_a_workspace(cluster, client, token, owner):
    queue(client, token(*owner))
    with cluster.connect("notifications_api") as conn:
        assert conn.execute("SELECT count(*) FROM notifications.log").fetchone()[0] == 0


def test_existing_entry_is_returned_only_to_readers(client, token, owner, platform):
    key = f"task:9:{uuid.uuid4().hex}"
    queue(client, token(*owner), dedupe_key=key, metadata={"secret": "x"})
    writer = uuid.uuid4()
    platform.grants[writer] = {
        "notifications.notification:write"
    }  # may queue, may not read
    probe = queue(client, token(writer, owner[1]), dedupe_key=key)
    assert probe.status_code == 404
    assert "secret" not in probe.text
