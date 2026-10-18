"""Evaluate notification rules against the Tasks API and deliver what is due by Telegram.

Runs as the cell's cron service (``notifications-dispatch``), so reminders work
without the owner's machine. Every read and write goes through the HTTP APIs
with the dispatcher's delegated automation token (``reminder-worker``), the
same authorization and audit path as any other client.

Each run:

1. **Rules.** Every active rule whose check interval has passed is evaluated
   against open tasks (Leantime tasks excluded) and its matches are queued with
   one ``dedupe_key`` per rule, task snapshot and Bangkok day, so a task is
   queued at most once a day per rule (the same key the old local runner used).
2. **Delivery.** Outside quiet hours (22:00-07:00 Asia/Bangkok), undelivered
   task notifications from the last two days go out as one Telegram digest
   (at most ``MAX_PER_DIGEST`` lines; the rest wait for the next run), and each
   pending engagement review digest as its own message. Every entry gets its
   delivery result recorded (delivered, or failed with the error, up to three
   attempts). A delivered explicit reminder is acknowledged on the task
   (``/reminders/delivered``) so it does not fire again.

One broken rule or entry is logged and skipped; it never blocks the others.
Delivery is at-least-once: a message whose result cannot be recorded (after
retries) may be sent again on the next run, never silently dropped.

    notifications-dispatch [--dry-run]

Environment: ``EMPIRE_TASKS_API``, ``EMPIRE_NOTIFICATIONS_API``,
``NOTIFICATIONS_DISPATCH_TOKEN`` (reminder-worker), and for delivery
``TELEGRAM_BOT_TOKEN`` + ``TELEGRAM_CHAT_ID``. Nothing secret is printed.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from solo_notifications._sentry import capture
from solo_notifications.tibo_reset import run_monitor as run_tibo_monitor

BANGKOK = ZoneInfo("Asia/Bangkok")
CLOSED = ("done", "cancelled", "archived")
TASK_KINDS = ("overdue", "due_soon", "high_priority", "stale", "blocked", "reminder")
WHOLE_MESSAGE_KINDS = (
    "engagement_review",
    "platform_check",
    "tibo_reset",
)  # sent as their own message, not in the digest
MAX_PER_DIGEST = 20
MAX_MESSAGE = 4000  # Telegram allows 4096 characters
TITLE = 500  # the notifications API limit for title and task_title
MAX_ATTEMPTS = 3
QUIET = (22, 7)  # from 22:00 until 07:00 Bangkok time


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req, fp, code, msg, headers, newurl
    ):  # never forward a token
        return None


class ApiError(RuntimeError):
    def __init__(self, status: int, where: str) -> None:
        super().__init__(f"{where}: HTTP {status}")
        self.status = status


class Http:
    """Bearer JSON client for one API (https only, no redirects)."""

    def __init__(self, base: str, token: str, timeout: float = 30.0) -> None:
        parts = urllib.parse.urlsplit(base)
        if parts.scheme != "https" and parts.hostname not in (
            "127.0.0.1",
            "localhost",
            "::1",
        ):
            raise SystemExit("API URLs must be https")
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout
        self.opener = urllib.request.build_opener(_NoRedirect)

    def call(
        self, method: str, path: str, body: Any = None, params: dict | None = None
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        query = f"?{urllib.parse.urlencode(clean, doseq=True)}" if clean else ""
        request = urllib.request.Request(
            f"{self.base}{path}{query}",
            method=method,
            data=json.dumps(body, default=str).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Empire-Channel": "workflow",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raise ApiError(error.code, f"{method} {path}") from None


class Tasks:
    def __init__(self, http: Http) -> None:
        self.http = http

    def list(self, **params: Any) -> list[dict[str, Any]]:
        """Open tasks matching the filters, as {id (number), title, status, priority, due_date, ...}."""
        items, after = [], None
        while True:
            page = self.http.call(
                "GET", "/v1/tasks", params={**params, "limit": 200, "after": after}
            )["items"]
            items.extend(page)
            if len(page) < 200:
                break
            after = page[-1]["number"]
        return [
            {
                **t,
                "id": t["number"],
                "uuid": t["id"],
                "due_date": (t.get("due_date") or None) and str(t["due_date"])[:10],
            }
            for t in items
            if (t.get("external_source") or "") != "leantime"
        ]

    def reminder_delivered(
        self, task_uuid: str, reminder_at: str, channel: str
    ) -> None:
        self.http.call(
            "POST",
            f"/v1/tasks/{task_uuid}/reminders/delivered",
            {"reminder_at": reminder_at, "channel": channel},
        )


class Notifications:
    def __init__(self, http: Http) -> None:
        self.http = http

    def rules(self) -> list[dict[str, Any]]:
        return self.http.call(
            "GET", "/v1/notification-rules", params={"active": "true"}
        )["items"]

    def touch_rule(self, rule_id: int, fields: dict[str, Any]) -> None:
        self.http.call("PATCH", f"/v1/notification-rules/{rule_id}", fields)

    def queue(self, fields: dict[str, Any]) -> str:
        return self.http.call("POST", "/v1/notifications", fields)["outcome"]

    def lookup(self, key: str) -> dict[str, Any] | None:
        items = self.http.call(
            "GET", "/v1/notifications", params={"dedupe_key": key, "limit": 1}
        )["items"]
        return items[0] if items else None

    def pending(self, kinds: list[str], since: str) -> list[dict[str, Any]]:
        return self.http.call(
            "GET",
            "/v1/notifications",
            params={
                "notification_type": kinds,
                "undelivered": "true",
                "max_attempts": MAX_ATTEMPTS,
                "since": since,
                "order": "asc",
                "limit": 500,
            },
        )["items"]

    def delivered_reminders(self, task_id: int) -> list[dict[str, Any]]:
        return self.http.call(
            "GET",
            "/v1/notifications",
            params={
                "notification_type": "reminder",
                "task_id": task_id,
                "undelivered": "false",
                "order": "desc",
                "limit": 50,
            },
        )["items"]

    def record(
        self,
        entry_id: int,
        outcome: str,
        *,
        channel: str | None = None,
        error: str | None = None,
    ) -> None:
        self.http.call(
            "POST",
            f"/v1/notifications/{entry_id}/delivery",
            {"outcome": outcome, "channel": channel, "error": error},
        )


# --- rules ---------------------------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _number(config: dict[str, Any], key: str, default: int, top: int) -> int:
    try:
        value = int(config.get(key) if config.get(key) is not None else default)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if 0 < value <= top else default


def _entry(
    task: dict[str, Any], kind: str, title: str, message: str, severity: str
) -> dict[str, Any]:
    entry = {
        "task_id": task["id"],
        "title": _clip(title, TITLE),
        "message": _clip(message, 8000),
        "notification_type": kind,
        "severity": severity,
        "task_title": _clip(task["title"], TITLE),
        "task_status": task["status"],
        "task_priority": task["priority"],
        "task_due_date": task["due_date"],
        "task_project": task.get("project"),
    }
    if kind == "reminder":  # what the delivery acknowledges on the task
        entry["metadata"] = {
            "task_uuid": task["uuid"],
            "reminder_at": str(task.get("reminder_at")),
        }
    return entry


def evaluate(rule: dict[str, Any], tasks: Tasks, now: datetime) -> list[dict[str, Any]]:
    """The matches of one rule (the same rules the local runner applied)."""
    config, kind = rule.get("config") or {}, rule["rule_type"]
    today = now.astimezone(BANGKOK).date()
    if kind == "overdue":
        rows = [
            t
            for t in tasks.list(due_before=today.isoformat())
            if t["status"] not in CLOSED
        ]
        return [
            _entry(
                t,
                kind,
                f"Overdue: {t['title']}",
                f'Task "{t["title"]}" is overdue (due: {t["due_date"]})',
                "critical" if (t["priority"] or 9) <= 2 else "warning",
            )
            for t in sorted(rows, key=lambda t: t["due_date"] or "")
        ]
    if kind == "due_soon":
        hours = _number(config, "hours", 24, 24 * 365)
        cutoff = (now.astimezone(BANGKOK) + timedelta(hours=hours)).date()
        rows = [
            t
            for t in tasks.list(due_before=(cutoff + timedelta(days=1)).isoformat())
            if t["due_date"]
            and today.isoformat() <= t["due_date"] <= cutoff.isoformat()
            and t["status"] not in CLOSED
        ]
        return [
            _entry(
                t,
                kind,
                f"Due soon: {t['title']}",
                f'Task "{t["title"]}" is due within {hours}h (due: {t["due_date"]})',
                "warning",
            )
            for t in sorted(rows, key=lambda t: t["due_date"])
        ]
    if kind == "high_priority":
        top, horizon = (
            _number(config, "max_priority", 2, 10),
            (today + timedelta(days=7)).isoformat(),
        )
        rows = [
            t
            for t in tasks.list(status=["todo", "in_progress"])
            if t["priority"] is not None
            and t["priority"] <= top
            and (
                t["status"] == "in_progress"
                or (t["due_date"] and t["due_date"] <= horizon)
            )
        ]
        return [
            _entry(
                t,
                kind,
                f"High priority: {t['title']}",
                f'Task "{t["title"]}" has priority {t["priority"]}',
                "info",
            )
            for t in sorted(rows, key=lambda t: (t["priority"], t["due_date"] or ""))
        ]
    if kind == "stale":
        cutoff = (now - timedelta(days=_number(config, "days", 7, 3650))).isoformat()
        rows = [
            t
            for t in tasks.list(status=["todo", "in_progress"])
            if (t["status"] == "in_progress" or t["due_date"])
            and (not t.get("updated_at") or str(t["updated_at"]) < cutoff)
        ]
        return [
            _entry(
                t,
                kind,
                f"Stale: {t['title']}",
                f'Task "{t["title"]}" has had no update since'
                f" {str(t.get('updated_at') or 'never')[:10]}",
                "warning",
            )
            for t in rows
        ]
    if kind == "blocked":
        return [
            _entry(
                t,
                kind,
                f"Blocked: {t['title']}",
                f'Task "{t["title"]}" is blocked',
                "critical",
            )
            for t in tasks.list(status=["blocked"])
        ]
    if kind == "reminder":
        rows = [
            t
            for t in tasks.list(reminder_due_before=now.astimezone(UTC).isoformat())
            if t["status"] not in ("done", "cancelled")
        ]
        return [
            _entry(
                t,
                kind,
                f"เตือนงาน: {t['title']}",
                f"ถึงเวลาเตือนงานที่ตั้งไว้: {t.get('reminder_at')}",
                "info",
            )
            for t in rows
        ]
    return []


def dedupe_key(*parts: Any) -> str:
    """Same construction as solo-empire notifications_store.dedupe_key."""
    return (
        "k-"
        + hashlib.sha256(
            json.dumps(parts, default=str, ensure_ascii=False).encode()
        ).hexdigest()[:60]
    )


def due(rule: dict[str, Any], now: datetime) -> bool:
    last = rule.get("last_checked_at")
    if not last:
        return True
    checked = datetime.fromisoformat(str(last).replace("Z", "+00:00"))  # noqa: FURB162 - keeps Z-normalisation; removing it changes which inputs are accepted
    return now - checked >= timedelta(
        minutes=int(rule.get("check_interval_minutes") or 60)
    )


def _log(event: str, **fields: Any) -> None:
    print(
        json.dumps({"event": f"notifications.dispatch.{event}", **fields}),
        file=sys.stderr,
    )


_reported: set[str] = set()


def _report_once(kind: str, exc: BaseException) -> None:
    """Send a per-row failure to Sentry at most once per kind and run (cron: one run per process)."""
    if kind not in _reported:
        _reported.add(kind)
        capture(exc)


def _already_delivered(
    notes: Notifications, tasks: Tasks, match: dict[str, Any]
) -> bool:
    """A reminder delivered earlier whose task acknowledgement failed: acknowledge it now, send nothing."""
    at = match["metadata"]["reminder_at"]
    for entry in notes.delivered_reminders(match["task_id"]):
        if (entry.get("metadata") or {}).get("reminder_at") == at:
            tasks.reminder_delivered(match["metadata"]["task_uuid"], at, "telegram")
            return True
    return False


def run_rules(
    notes: Notifications, tasks: Tasks, now: datetime, *, dry_run: bool
) -> dict[str, int]:
    counts = {"rules": 0, "matched": 0, "queued": 0, "errors": 0}
    bangkok_day = now.astimezone(BANGKOK).date().isoformat()
    for rule in notes.rules():
        if not due(rule, now):
            continue
        counts["rules"] += 1
        try:
            matches = evaluate(rule, tasks, now)
        except (
            ApiError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
        ) as exc:  # one broken rule never blocks the rest
            counts["errors"] += 1
            _report_once("rule_failed", exc)
            _log(
                "rule_failed",
                rule_id=rule.get("id"),
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )
            continue
        counts["matched"] += len(matches)
        queued, failed = 0, False
        for m in matches:
            parts = [
                "notification-runner",
                rule["legacy_id"] or rule["id"],
                m["task_id"],
                m["notification_type"],
                m["task_status"],
                m["task_due_date"],
                m["task_priority"],
                bangkok_day,
            ]
            if (
                m["notification_type"] == "reminder"
            ):  # a new reminder time the same day is a new notification
                parts.append(m["metadata"]["reminder_at"])
            if dry_run:
                continue
            try:
                if m["notification_type"] == "reminder" and _already_delivered(
                    notes, tasks, m
                ):
                    continue
                if (
                    notes.queue(
                        {**m, "rule_id": rule["id"], "dedupe_key": dedupe_key(*parts)}
                    )
                    == "created"
                ):
                    queued += 1
            except ApiError as exc:
                counts["errors"] += 1
                failed = True
                _report_once("queue_failed", exc)
                _log(
                    "queue_failed",
                    rule_id=rule["id"],
                    task_id=m["task_id"],
                    detail=str(exc),
                )
        counts["queued"] += queued
        if (
            not dry_run and not failed
        ):  # a failed queue is retried on the next run, not after the interval
            stamp = {"last_checked_at": now.isoformat()}
            if queued:
                stamp["last_triggered_at"] = now.isoformat()
            notes.touch_rule(rule["id"], stamp)
    return counts


# --- delivery --------------------------------------------------------------------------------------


def quiet(now: datetime) -> bool:
    hour = now.astimezone(BANGKOK).hour
    return hour >= QUIET[0] or hour < QUIET[1]


ICONS = {
    "overdue": "⏰",
    "due_soon": "📅",
    "high_priority": "🔥",
    "stale": "🕸️",
    "blocked": "⛔",
    "reminder": "🔔",
}


def digest(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """The entries that fit in one message, and the message. Only these count as delivered."""
    lines, taken = [], []
    for e in entries[:MAX_PER_DIGEST]:
        due_text = f" (due {e['task_due_date']})" if e.get("task_due_date") else ""
        line = f"{ICONS.get(e['notification_type'], '•')} {_clip(e.get('task_title') or e['title'], 200)}{due_text}"
        if len("\n".join([*lines, line])) + 60 > MAX_MESSAGE:
            break
        lines.append(line)
        taken.append(e)
    return taken, "\n".join([f"🔔 Solo Empire — แจ้งเตือนงาน ({len(taken)})", *lines])


def chunks(text: str) -> list[str]:
    """A long message as consecutive Telegram-sized parts (nothing is cut off)."""
    return [text[i : i + MAX_MESSAGE] for i in range(0, len(text), MAX_MESSAGE)] or [""]


def telegram_sender(bot_token: str, chat_id: str) -> Callable[[str], None]:
    bot_token, chat_id = bot_token.strip(), chat_id.strip()
    if not bot_token.replace(":", "").replace("_", "").replace("-", "").isalnum():
        raise SystemExit("TELEGRAM_BOT_TOKEN has an unexpected format")  # never echo it
    opener = urllib.request.build_opener(_NoRedirect)

    def send(text: str) -> None:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            method="POST",
            data=json.dumps(
                {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(request, timeout=20) as response:
                response.read()
        except (
            urllib.error.HTTPError
        ) as error:  # never include the URL: it carries the bot token
            raise RuntimeError(f"telegram_http_{error.code}") from None
        except Exception as error:  # noqa: BLE001 - messages of URL errors may carry the token
            raise RuntimeError(f"telegram_{type(error).__name__}") from None

    return send


def _record(
    notes: Notifications,
    entry: dict[str, Any],
    outcome: str,
    counts: dict[str, int],
    **kw: Any,
) -> None:
    for attempt in range(3):  # a lost result means the entry is sent again next run
        try:
            notes.record(entry["id"], outcome, **kw)
            return
        except ApiError as exc:
            if (
                attempt == 2 or exc.status < 500
            ):  # keep going: the other entries were delivered too
                counts["errors"] += 1
                _report_once("record_failed", exc)
                _log(
                    "record_failed",
                    notification_id=entry["id"],
                    outcome=outcome,
                    detail=str(exc),
                )
                return
            time.sleep(2 * (attempt + 1))


def deliver(
    notes: Notifications,
    send: Callable[[str], None] | None,
    now: datetime,
    *,
    dry_run: bool,
    tasks: Tasks | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, int]:
    counts = {"delivered": 0, "failed": 0, "waiting": 0, "errors": 0}
    since = (now - timedelta(days=2)).isoformat()
    task_entries = notes.pending(list(TASK_KINDS), since)
    # Whole-message kinds: an engagement review and the daily platform check (warning/breach) from empire-platform.
    reviews = notes.pending(list(WHOLE_MESSAGE_KINDS), since)
    if send is None or quiet(now) or dry_run:
        counts["waiting"] = len(task_entries) + len(reviews)
        return counts
    batches = []
    if task_entries:
        taken, text = digest(task_entries)
        batches.append((taken, [text]))
        counts["waiting"] += len(task_entries) - len(taken)
    batches += [([r], chunks(f"{r['title']}\n\n{r['message']}")) for r in reviews]
    clock = clock or (lambda: datetime.now(UTC))
    for entries, texts in batches:
        if quiet(clock()):  # a long run that reaches quiet hours stops sending
            counts["waiting"] += len(entries)
            continue
        sent = 0
        try:
            for text in texts:
                send(text)
                sent += 1
        except RuntimeError as exc:
            _report_once("send_failed", exc)
            if sent:  # part of a long review went out: resending would repeat it
                _log(
                    "partial_send",
                    notification_id=entries[0]["id"],
                    parts=len(texts),
                    sent=sent,
                    detail=str(exc),
                )
            else:
                for e in entries:
                    _record(notes, e, "failed", counts, error=str(exc))
                counts["failed"] += len(entries)
                continue
        for e in entries:
            _record(notes, e, "delivered", counts, channel="telegram")
            meta = e.get("metadata") or {}
            if (
                e["notification_type"] == "reminder"
                and tasks is not None
                and meta.get("task_uuid")
            ):
                try:
                    tasks.reminder_delivered(
                        meta["task_uuid"], meta["reminder_at"], "telegram"
                    )
                except ApiError as exc:
                    counts["errors"] += 1
                    _report_once("reminder_ack_failed", exc)
                    _log(
                        "reminder_ack_failed", task_id=e.get("task_id"), detail=str(exc)
                    )
        counts["delivered"] += len(entries)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="notifications-dispatch", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate only: queue nothing, send nothing",
    )
    args = parser.parse_args(argv)
    env = os.environ
    missing = [
        k
        for k in (
            "EMPIRE_TASKS_API",
            "EMPIRE_NOTIFICATIONS_API",
            "NOTIFICATIONS_DISPATCH_TOKEN",
        )
        if not env.get(k)
    ]
    if missing:
        print(
            json.dumps(
                {
                    "event": "notifications.dispatch",
                    "error": "missing_config",
                    "missing": missing,
                }
            )
        )
        return 2
    token = env["NOTIFICATIONS_DISPATCH_TOKEN"]
    tasks = Tasks(Http(env["EMPIRE_TASKS_API"], token))
    notes = Notifications(Http(env["EMPIRE_NOTIFICATIONS_API"], token))
    send = (
        telegram_sender(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"])
        if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")
        else None
    )
    now = datetime.now(UTC)
    rules = {"rules": 0, "matched": 0, "queued": 0, "errors": 0}
    monitor = {"enabled": False, "matched": 0, "queued": 0, "errors": 0}
    try:
        rules = run_rules(notes, tasks, now, dry_run=args.dry_run)
    except (ApiError, OSError, urllib.error.URLError) as exc:
        capture(exc)
        rules["errors"] += 1
        _log("rules_failed", error=type(exc).__name__)
    try:
        monitor = run_tibo_monitor(notes, now, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - preserve existing delivery even if monitor setup fails
        capture(exc)
        monitor["errors"] += 1
        _log("tibo_monitor_failed", error=type(exc).__name__)
    try:
        delivery = deliver(notes, send, now, dry_run=args.dry_run, tasks=tasks)
    except (ApiError, OSError, urllib.error.URLError) as exc:
        capture(exc)
        print(
            json.dumps(
                {
                    "event": "notifications.dispatch",
                    "error": type(exc).__name__,
                    "detail": str(exc),
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "event": "notifications.dispatch",
                "dry_run": args.dry_run,
                "telegram": send is not None,
                "quiet": quiet(now),
                **rules,
                **delivery,
                "tibo_reset": monitor,
                "errors": rules["errors"] + delivery["errors"] + monitor["errors"],
            }
        )
    )
    return (
        1
        if delivery["failed"]
        or rules["errors"]
        or delivery["errors"]
        or monitor["errors"]
        else 0
    )


if __name__ == "__main__":
    sys.exit(main())
