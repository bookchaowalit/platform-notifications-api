"""Notifications API: notification rules and the notification log.

The cell keeps its own database. Authorization, execution records and audit
live in empire-platform and are reached over HTTP with the caller's token:
reads ask ``/v1/authz/decide``; writes run a recorded precheck, change rows in
an open local transaction, start a platform execution bound to the exact
record, commit, then finish the execution with row hashes.

- Queueing (``POST /v1/notifications``) is idempotent with a ``dedupe_key``:
  the same key returns the existing entry (``outcome: existing``), so callers
  decide whether to retry its delivery. Keys are the callers' (for example one
  per task snapshot per day).
- Delivery results (``POST /v1/notifications/{id}/delivery``) update the entry
  atomically: ``delivered`` sets delivered_at/channel and clears the error,
  ``failed`` records the error; both count an attempt. ``console`` marks a
  local observation (no attempt, not a delivery).

Permissions: ``notifications.rule:read|write`` and ``notifications.notification:read|write``.
"""

import contextvars
import hashlib
import json
import logging
import os
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

import psycopg
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
)
from fastapi import (
    Request as HttpRequest,
)
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from solo_notifications._sentry import capture
from solo_notifications.auth import Principal, TokenVerifier, Unauthenticated
from solo_notifications.platform import Action, Denied, Platform, PlatformUnavailable

log = logging.getLogger("solo_notifications")
correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)
CORRELATION = re.compile(r"^[A-Za-z0-9-]{8,64}$")
RuleType = Literal[
    "overdue",
    "due_soon",
    "high_priority",
    "stale",
    "blocked",
    "status_change",
    "reminder",
]
Severity = Literal["info", "warning", "critical", "success"]

RULE_COLUMNS = (
    "name",
    "description",
    "rule_type",
    "config",
    "channels",
    "project_filter",
    "category_filter",
    "priority_filter",
    "status_filter",
    "check_interval_minutes",
    "last_checked_at",
    "last_triggered_at",
    "is_active",
)
RULE_JSON = {"config", "channels", "status_filter"}
RULE_SELECT = (
    "id, legacy_id, " + ", ".join(RULE_COLUMNS) + ", version, created_at, updated_at"
)
LOG_COLUMNS = (
    "rule_id",
    "task_id",
    "title",
    "message",
    "notification_type",
    "severity",
    "channel",
    "task_title",
    "task_status",
    "task_priority",
    "task_due_date",
    "task_project",
    "metadata",
    "dedupe_key",
)
LOG_SELECT = (
    "id, legacy_id, "
    + ", ".join(LOG_COLUMNS)
    + ", sent_at, read_at, dismissed_at, delivered_at,"
    " delivery_channel, delivery_error, delivery_attempts, version"
)


class RuleFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    rule_type: RuleType | None = None
    config: dict[str, Any] | None = None
    channels: list[str] | None = None
    project_filter: str | None = Field(default=None, max_length=200)
    category_filter: str | None = Field(default=None, max_length=200)
    priority_filter: int | None = None
    status_filter: list[str] | None = None
    check_interval_minutes: int | None = Field(default=None, gt=0, le=100000)
    last_checked_at: datetime | None = None
    last_triggered_at: datetime | None = None
    is_active: bool | None = None


class RuleCreate(RuleFields):
    name: str = Field(min_length=1, max_length=200)
    rule_type: RuleType
    legacy_id: int | None = Field(default=None, gt=0)


class RuleUpdate(RuleFields):
    expected_version: int | None = Field(default=None, ge=1)


class Rule(BaseModel):
    id: int
    legacy_id: int | None
    name: str
    description: str | None
    rule_type: str
    config: dict[str, Any]
    channels: list[str]
    project_filter: str | None
    category_filter: str | None
    priority_filter: int | None
    status_filter: list[str] | None
    check_interval_minutes: int
    last_checked_at: datetime | None
    last_triggered_at: datetime | None
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class NotificationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: int | None = Field(default=None, gt=0)
    task_id: int | None = None
    title: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=8000)
    notification_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,59}$")
    severity: Severity = "info"
    channel: str = Field(default="console", max_length=60)
    task_title: str | None = Field(default=None, max_length=500)
    task_status: str | None = Field(default=None, max_length=60)
    task_priority: int | None = None
    task_due_date: str | None = Field(default=None, max_length=40)
    task_project: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] | None = None
    dedupe_key: str | None = Field(default=None, min_length=8, max_length=200)
    legacy_id: int | None = Field(default=None, gt=0)
    sent_at: datetime | None = None  # import only; the service stamps new entries


class Notification(BaseModel):
    id: int
    legacy_id: int | None
    rule_id: int | None
    task_id: int | None
    title: str
    message: str
    notification_type: str
    severity: str
    channel: str
    task_title: str | None
    task_status: str | None
    task_priority: int | None
    task_due_date: str | None
    task_project: str | None
    metadata: dict[str, Any] | None
    dedupe_key: str | None
    sent_at: datetime
    read_at: datetime | None
    dismissed_at: datetime | None
    delivered_at: datetime | None
    delivery_channel: str | None
    delivery_error: str | None
    delivery_attempts: int
    version: int


class QueueResult(BaseModel):
    outcome: Literal["created", "existing"]
    notification: Notification


class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["delivered", "failed", "console"]
    channel: str | None = Field(default=None, max_length=200)
    error: str | None = Field(default=None, max_length=1000)


class Items(BaseModel):
    items: list[dict[str, Any]]


class Stats(BaseModel):
    groups: dict[str, int]


def row_hash(row: dict[str, Any] | None) -> str | None:
    if row is None:
        return None
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass
class Plan:
    permission: str
    resource_type: str
    resource_id: str
    audit: dict[str, Any]
    result: Any


def create_app(
    connect: Callable[[], psycopg.Connection],
    verifier: TokenVerifier,
    platform: Platform,
) -> FastAPI:
    app = FastAPI(title="Notifications API", version="1.0.0", description=__doc__)

    @app.middleware("http")
    async def correlate(request: HttpRequest, call_next):
        given = request.headers.get("x-correlation-id", "")
        token = correlation_id.set(
            given if CORRELATION.match(given) else uuid.uuid4().hex
        )
        try:
            response = await call_next(request)
        finally:
            value = correlation_id.get()
            correlation_id.reset(token)
        response.headers["X-Correlation-Id"] = value
        return response

    def principal(
        authorization: Annotated[str | None, Header()] = None,
        x_empire_channel: Annotated[str | None, Header()] = None,
    ) -> Principal:
        try:
            return verifier.verify(authorization, x_empire_channel)
        except Unauthenticated as exc:
            raise HTTPException(
                status_code=401, detail={"error": "unauthenticated", "reason": str(exc)}
            ) from exc

    Caller = Annotated[Principal, Depends(principal)]

    @contextmanager
    def bound(caller: Principal) -> Iterator[psycopg.Connection]:
        try:
            conn = connect()
        except psycopg.OperationalError as exc:
            raise HTTPException(
                status_code=503, detail={"error": "unavailable"}
            ) from exc
        conn.row_factory = dict_row
        try:
            conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(caller.workspace),),
            )
            yield conn
        finally:
            conn.rollback()
            conn.close()

    def allow(
        caller: Principal,
        action: str,
        resource_type: str,
        resource_id: str = "*",
        *,
        not_found: bool = False,
    ) -> None:
        try:
            allowed = platform.decide(
                caller.token, caller.channel, Action(action, resource_type, resource_id)
            )
        except Denied as exc:
            raise HTTPException(
                status_code=401,
                detail={"error": "unauthenticated", "reason": exc.reason},
            ) from exc
        except PlatformUnavailable as exc:
            raise HTTPException(
                status_code=503, detail={"error": "authorization_unavailable"}
            ) from exc
        if not allowed:
            if not_found:
                raise HTTPException(status_code=404, detail={"error": "not_found"})
            raise HTTPException(status_code=403, detail={"error": "deny"})

    def write(
        caller: Principal,
        op: Callable[[psycopg.Connection], Plan],
        *,
        precheck: tuple[str, str, str],
    ) -> Any:
        """Precheck, local change, platform execution bound to the exact record, commit, finish."""
        allow(caller, *precheck)
        with bound(caller) as conn:
            try:
                plan = op(conn)
                if plan.audit.get(
                    "skip"
                ):  # nothing changed (an existing queued entry): no execution
                    conn.rollback()
                    return plan.result
                try:
                    execution, _replayed, _status = platform.begin(
                        caller.token,
                        caller.channel,
                        Action(plan.permission, plan.resource_type, plan.resource_id),
                        None,
                        correlation_id.get(),
                    )
                except Denied as exc:
                    code = exc.status if exc.status in (401, 403, 409) else 403
                    raise HTTPException(
                        status_code=code,
                        detail={"error": exc.decision, "reason": exc.reason},
                    ) from exc
                except PlatformUnavailable as exc:
                    raise HTTPException(
                        status_code=503, detail={"error": "authorization_unavailable"}
                    ) from exc
                conn.commit()
            except psycopg.errors.UniqueViolation as exc:
                raise HTTPException(
                    status_code=409, detail={"error": "conflict"}
                ) from exc
            except psycopg.errors.ForeignKeyViolation as exc:
                raise HTTPException(
                    status_code=422, detail={"error": "reference_not_found"}
                ) from exc
            except (
                psycopg.errors.NotNullViolation,
                psycopg.errors.CheckViolation,
            ) as exc:
                raise HTTPException(
                    status_code=422, detail={"error": "invalid_value"}
                ) from exc
        try:
            platform.finish(
                caller.token,
                caller.channel,
                execution,
                {"status": "succeeded", "service": "notifications", **plan.audit},
            )
        except (PlatformUnavailable, Denied) as exc:
            capture(exc)  # the write succeeded; only the audit finish failed
            log.warning(
                json.dumps(
                    {
                        "event": "notifications.finish_failed",
                        "execution": str(execution),
                    }
                )
            )
        return plan.result

    def audit(
        entity: str, verb: str, record_id: int, before: dict | None, after: dict | None
    ) -> dict[str, Any]:
        return {
            "action": f"notifications.{entity}.{verb}",
            "resource_type": entity,
            "resource_id": str(record_id),
            "before_hash": row_hash(before),
            "after_hash": row_hash(after),
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- rules -------------------------------------------------------------------------------

    @app.get("/v1/notification-rules", response_model=Items)
    def list_rules(
        caller: Caller, active: bool | None = None, rule_type: RuleType | None = None
    ) -> Items:
        allow(caller, "notifications.rule:read", "rule")
        where, params = ["TRUE"], []
        if active is not None:
            where.append("is_active = %s")
            params.append(active)
        if rule_type is not None:
            where.append("rule_type = %s")
            params.append(rule_type)
        with bound(caller) as conn:
            rows = conn.execute(
                f"SELECT {RULE_SELECT} FROM notifications.rules WHERE {' AND '.join(where)}"
                " ORDER BY id",
                params,
            ).fetchall()
        return Items(items=[Rule(**row).model_dump(mode="json") for row in rows])

    @app.get("/v1/notification-rules/{rule_id}", response_model=Rule)
    def get_rule(rule_id: int, caller: Caller) -> Rule:
        allow(caller, "notifications.rule:read", "rule", str(rule_id), not_found=True)
        with bound(caller) as conn:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- table/column names are module constants or spec-validated identifiers; values are bound as %s parameters
            row = conn.execute(
                f"SELECT {RULE_SELECT} FROM notifications.rules WHERE id = %s",
                (rule_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        return Rule(**row)

    def rule_values(body: RuleFields, names: set[str]) -> dict[str, Any]:
        data = {name: getattr(body, name) for name in RULE_COLUMNS if name in names}
        nulls = sorted(
            n
            for n in (
                "name",
                "rule_type",
                "config",
                "channels",
                "check_interval_minutes",
                "is_active",
            )
            if n in data and data[n] is None
        )
        if nulls:
            raise HTTPException(
                status_code=422, detail={"error": "invalid_value", "fields": nulls}
            )
        for name in RULE_JSON & set(data):
            data[name] = json.dumps(data[name]) if data[name] is not None else None
        return data

    @app.post("/v1/notification-rules", status_code=201, response_model=Rule)
    def create_rule(body: RuleCreate, caller: Caller) -> Rule:
        data = {
            k: v
            for k, v in rule_values(
                body, body.model_fields_set | {"name", "rule_type"}
            ).items()
            if v is not None
        }

        def op(conn: psycopg.Connection) -> Plan:
            columns, params = (
                ["workspace_id", *data],
                [caller.workspace, *data.values()],
            )
            if body.legacy_id is not None:
                columns.append("legacy_id")
                params.append(body.legacy_id)
            row = conn.execute(
                f"INSERT INTO notifications.rules ({', '.join(columns)})"
                f" VALUES ({', '.join(['%s'] * len(columns))}) RETURNING {RULE_SELECT}",
                params,
            ).fetchone()
            return Plan(
                "notifications.rule:write",
                "rule",
                str(row["id"]),
                audit("rule", "created", row["id"], None, row),
                row,
            )

        return Rule(
            **write(caller, op, precheck=("notifications.rule:write", "rule", "new"))
        )

    @app.patch("/v1/notification-rules/{rule_id}", response_model=Rule)
    def update_rule(rule_id: int, body: RuleUpdate, caller: Caller) -> Rule:
        data = rule_values(body, body.model_fields_set - {"expected_version"})

        def op(conn: psycopg.Connection) -> Plan:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- table/column names are module constants or spec-validated identifiers; values are bound as %s parameters
            before = conn.execute(
                f"SELECT {RULE_SELECT} FROM notifications.rules WHERE id = %s FOR UPDATE",
                (rule_id,),
            ).fetchone()
            if before is None:
                raise HTTPException(status_code=404, detail={"error": "not_found"})
            if (
                body.expected_version is not None
                and before["version"] != body.expected_version
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"error": "version_conflict", "version": before["version"]},
                )
            row = before
            if data:
                assignments = ", ".join(f"{name} = %s" for name in data)
                row = conn.execute(
                    f"UPDATE notifications.rules SET {assignments}, version = version + 1,"
                    f" updated_at = now() WHERE id = %s RETURNING {RULE_SELECT}",
                    [*data.values(), rule_id],
                ).fetchone()
            return Plan(
                "notifications.rule:write",
                "rule",
                str(rule_id),
                audit("rule", "updated", rule_id, before, row),
                row,
            )

        return Rule(
            **write(
                caller, op, precheck=("notifications.rule:write", "rule", str(rule_id))
            )
        )

    # --- log ---------------------------------------------------------------------------------

    @app.get("/v1/notifications", response_model=Items)
    def list_notifications(
        caller: Caller,
        notification_type: Annotated[list[str] | None, Query()] = None,
        since: datetime | None = None,
        until: datetime | None = None,
        task_id: int | None = None,
        rule_id: int | None = None,
        undelivered: bool | None = None,
        max_attempts: Annotated[int | None, Query(ge=0)] = None,
        dedupe_key: str | None = None,
        order: Literal["asc", "desc"] = "asc",
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Items:
        allow(caller, "notifications.notification:read", "notification")
        where, params = ["TRUE"], []
        if notification_type:
            where.append("notification_type = ANY(%s)")
            params.append(list(notification_type))
        for column, op_, value in (
            ("sent_at", ">=", since),
            ("sent_at", "<", until),
            ("task_id", "=", task_id),
            ("rule_id", "=", rule_id),
            ("dedupe_key", "=", dedupe_key),
        ):
            if value is not None:
                where.append(f"{column} {op_} %s")
                params.append(value)
        if undelivered is not None:
            where.append(
                "delivered_at IS NULL" if undelivered else "delivered_at IS NOT NULL"
            )
        if max_attempts is not None:
            where.append("delivery_attempts < %s")
            params.append(max_attempts)
        direction = "DESC" if order == "desc" else "ASC"
        with bound(caller) as conn:
            rows = conn.execute(
                f"SELECT {LOG_SELECT} FROM notifications.log WHERE {' AND '.join(where)}"
                f" ORDER BY id {direction} LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        return Items(
            items=[Notification(**row).model_dump(mode="json") for row in rows]
        )

    @app.get("/v1/notifications/stats", response_model=Stats)
    def stats(
        caller: Caller,
        group_by: Literal[
            "notification_type", "severity", "channel"
        ] = "notification_type",
        since: datetime | None = None,
    ) -> Stats:
        allow(caller, "notifications.notification:read", "notification")
        where, params = "TRUE", []
        if since is not None:
            where, params = "sent_at >= %s", [since]
        with bound(caller) as conn:
            rows = conn.execute(
                f"SELECT {group_by} AS key, count(*) AS n FROM notifications.log WHERE {where}"
                " GROUP BY 1",
                params,
            ).fetchall()
        return Stats(groups={row["key"]: row["n"] for row in rows})

    @app.get("/v1/notifications/{notification_id}", response_model=Notification)
    def get_notification(notification_id: int, caller: Caller) -> Notification:
        allow(
            caller,
            "notifications.notification:read",
            "notification",
            str(notification_id),
            not_found=True,
        )
        with bound(caller) as conn:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- table/column names are module constants or spec-validated identifiers; values are bound as %s parameters
            row = conn.execute(
                f"SELECT {LOG_SELECT} FROM notifications.log WHERE id = %s",
                (notification_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        return Notification(**row)

    @app.post("/v1/notifications", status_code=201, response_model=QueueResult)
    def queue(body: NotificationCreate, caller: Caller) -> QueueResult:
        data = {name: getattr(body, name) for name in LOG_COLUMNS}
        data["metadata"] = (
            json.dumps(data["metadata"]) if data["metadata"] is not None else None
        )

        def op(conn: psycopg.Connection) -> Plan:
            if body.dedupe_key:
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- table/column names are module constants or spec-validated identifiers; values are bound as %s parameters
                existing = conn.execute(
                    f"SELECT {LOG_SELECT} FROM notifications.log WHERE dedupe_key = %s",
                    (body.dedupe_key,),
                ).fetchone()
                if existing is not None:
                    # Returning it reveals its content: the caller must be able to read it.
                    allow(
                        caller,
                        "notifications.notification:read",
                        "notification",
                        str(existing["id"]),
                        not_found=True,
                    )
                    return Plan("", "", "", {"skip": True}, ("existing", existing))
            columns, params = (
                ["workspace_id", *data],
                [caller.workspace, *data.values()],
            )
            for extra in ("legacy_id", "sent_at"):
                if getattr(body, extra) is not None:
                    columns.append(extra)
                    params.append(getattr(body, extra))
            # ON CONFLICT: a concurrent queue with the same key keeps the first entry.
            row = conn.execute(
                f"INSERT INTO notifications.log ({', '.join(columns)})"
                f" VALUES ({', '.join(['%s'] * len(columns))})"
                " ON CONFLICT (workspace_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING"
                f" RETURNING {LOG_SELECT}",
                params,
            ).fetchone()
            if row is None:
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- table/column names are module constants or spec-validated identifiers; values are bound as %s parameters
                existing = conn.execute(
                    f"SELECT {LOG_SELECT} FROM notifications.log WHERE dedupe_key = %s",
                    (body.dedupe_key,),
                ).fetchone()
                allow(
                    caller,
                    "notifications.notification:read",
                    "notification",
                    str(existing["id"]),
                    not_found=True,
                )
                return Plan("", "", "", {"skip": True}, ("existing", existing))
            return Plan(
                "notifications.notification:write",
                "notification",
                str(row["id"]),
                audit("notification", "queued", row["id"], None, row),
                ("created", row),
            )

        outcome, row = write(
            caller,
            op,
            precheck=("notifications.notification:write", "notification", "new"),
        )
        return QueueResult(outcome=outcome, notification=Notification(**row))

    @app.post(
        "/v1/notifications/{notification_id}/delivery", response_model=Notification
    )
    def record_delivery(
        notification_id: int, body: Delivery, caller: Caller
    ) -> Notification:
        if body.outcome == "delivered" and (
            not body.channel or body.channel == "console"
        ):
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_value", "reason": "console_is_not_delivery"},
            )

        def op(conn: psycopg.Connection) -> Plan:
            # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- table/column names are module constants or spec-validated identifiers; values are bound as %s parameters
            before = conn.execute(
                f"SELECT {LOG_SELECT} FROM notifications.log WHERE id = %s FOR UPDATE",
                (notification_id,),
            ).fetchone()
            if before is None:
                raise HTTPException(status_code=404, detail={"error": "not_found"})
            if body.outcome == "delivered":
                sql, params = (
                    (
                        "delivered_at = now(), delivery_channel = %s, delivery_error = NULL,"
                        " delivery_attempts = delivery_attempts + 1"
                    ),
                    [body.channel],
                )
            elif body.outcome == "failed":
                sql, params = (
                    "delivery_error = %s, delivery_attempts = delivery_attempts + 1",
                    [body.error or "delivery_failed"],
                )
            else:
                sql, params = "channel = 'console', delivery_error = NULL", []
            row = conn.execute(
                f"UPDATE notifications.log SET {sql}, version = version + 1 WHERE id = %s"
                f" RETURNING {LOG_SELECT}",
                [*params, notification_id],
            ).fetchone()
            return Plan(
                "notifications.notification:write",
                "notification",
                str(notification_id),
                audit("notification", body.outcome, notification_id, before, row),
                row,
            )

        return Notification(
            **write(
                caller,
                op,
                precheck=(
                    "notifications.notification:write",
                    "notification",
                    str(notification_id),
                ),
            )
        )

    return app


def main() -> None:
    import uvicorn

    from solo_notifications.db import default_cluster
    from solo_notifications.platform import HttpPlatform

    hosted = os.environ.get("NOTIFICATIONS_DB_MODE") == "hosted"
    pem = os.environ.get("NOTIFICATIONS_JWT_PUBLIC_KEY_PEM", "").strip()
    # A second key for the same issuer during a signing-key rollover; unset = off.
    next_pem = os.environ.get("NOTIFICATIONS_JWT_NEXT_PUBLIC_KEY_PEM", "").strip()
    issuer = os.environ.get("NOTIFICATIONS_JWT_ISSUER", "")
    platform_url = os.environ.get("EMPIRE_PLATFORM_URL", "")
    if not pem or not issuer or not platform_url:
        raise SystemExit(
            "set NOTIFICATIONS_JWT_PUBLIC_KEY_PEM, NOTIFICATIONS_JWT_ISSUER and EMPIRE_PLATFORM_URL"
        )
    if hosted and issuer == "empire-platform-dev":
        raise SystemExit("hosted mode refuses the dev issuer")
    cluster = default_cluster()
    app = create_app(
        lambda: cluster.connect("notifications_api"),
        TokenVerifier(
            pem, issuer, extra_public_key_pems=(next_pem,) if next_pem else ()
        ),
        HttpPlatform(platform_url),
    )
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0" if hosted else "127.0.0.1"),
        port=int(os.environ.get("PORT", "3612")),
        proxy_headers=False,
    )
