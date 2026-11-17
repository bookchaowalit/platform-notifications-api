"""The morning digest: one Telegram message a day with system status and the task picture.

Task notifications other than explicit reminders (overdue, due_soon, high_priority,
stale, blocked) are no longer sent every run. They wait for the first dispatcher run
at or after ``MORNING_HOUR`` Bangkok time, which sends them together with the public
uptime status as one message:

    ☀️ สรุปเช้า อ. 17 พ.ย.

    ระบบ ✅ ปกติ 61/61

    งาน (14)
    ⛔ ติดขัด 1
    • Fix the deploy
    ⏰ เลยกำหนด 12
    • Main Quest W3 · เลย 17 วัน
    ...

Each task shows once, under its most urgent kind. A console-only marker in the
notifications log (``dedupe_key`` per Bangkok day) records that the digest went out,
so a later run the same day sends nothing again; a marker that cannot be written
means the digest may be sent twice, never skipped.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

BANGKOK_HOUR_DEFAULT = 8
STATUS_URL_DEFAULT = "https://monitor.bookchaowalit.com/status"
STATE_TYPE = "morning_digest_state"
PER_SECTION = 5
MAX_MESSAGE = 4000

# Most urgent first: a task matched by several rules is listed under the first one.
SECTIONS = (
    ("blocked", "⛔", "ติดขัด"),
    ("overdue", "⏰", "เลยกำหนด"),
    ("due_soon", "📅", "ใกล้ครบกำหนด"),
    ("high_priority", "🔥", "สำคัญ"),
    ("stale", "🕸️", "ไม่ขยับ"),
)
MORNING_KINDS = tuple(kind for kind, _, _ in SECTIONS)
COUNT_ONLY = ("stale",)  # long tail: the count is the signal, the list is noise

WEEKDAYS = ("จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา.")
MONTHS = (
    "ม.ค.",
    "ก.พ.",
    "มี.ค.",
    "เม.ย.",
    "พ.ค.",
    "มิ.ย.",
    "ก.ค.",
    "ส.ค.",
    "ก.ย.",
    "ต.ค.",
    "พ.ย.",
    "ธ.ค.",
)


def state_key(day: date) -> str:
    return f"morning-digest:{day.isoformat()}"


def thai_day(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _days(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def _when(kind: str, due: date | None, today: date) -> str:
    if due is None:
        return ""
    gap = (today - due).days
    if kind == "overdue" and gap > 0:
        return f" · เลย {gap} วัน"
    if kind == "due_soon":
        return " · วันนี้" if gap == 0 else " · พรุ่งนี้" if gap == -1 else f" · {due:%d/%m}"
    return ""


def _sort_key(entry: dict[str, Any]) -> tuple[int, str]:
    return (entry.get("task_priority") or 9, str(entry.get("task_due_date") or "9999"))


def task_lines(entries: list[dict[str, Any]], today: date) -> list[str]:
    """The task part of the digest: each task once, under its most urgent kind."""
    rank = {kind: i for i, kind in enumerate(MORNING_KINDS)}
    best: dict[Any, dict[str, Any]] = {}
    for e in entries:
        key = e.get("task_id") or e.get("task_title") or e.get("title")
        kept = best.get(key)
        if (
            kept is None
            or rank[e["notification_type"]] < rank[kept["notification_type"]]
        ):
            best[key] = e
    if not best:
        return ["งาน ✅ ไม่มีเรื่องค้าง"]
    lines = [f"งาน ({len(best)})"]
    for kind, icon, label in SECTIONS:
        rows = sorted(
            (e for e in best.values() if e["notification_type"] == kind), key=_sort_key
        )
        if not rows:
            continue
        lines.append(f"{icon} {label} {len(rows)}")
        if kind in COUNT_ONLY:
            continue
        for e in rows[:PER_SECTION]:
            title = _clip(e.get("task_title") or e.get("title") or "", 90)
            lines.append(
                f"• {title}{_when(kind, _days(e.get('task_due_date')), today)}"
            )
        if len(rows) > PER_SECTION:
            lines.append(f"  + อีก {len(rows) - PER_SECTION}")
    return lines


def status_line(status: dict[str, Any] | None) -> str:
    """One line from the uptime monitor's public /status (own systems only)."""
    if status is None:
        return "ระบบ ⚠️ อ่านสถานะ monitor ไม่ได้"
    results = [r for r in status.get("results") or [] if isinstance(r, dict)]
    if not results:
        return "ระบบ ⚠️ monitor ไม่มีข้อมูล"
    down = [str(r.get("name")) for r in results if r.get("status") == "down"]
    # Not checked yet, stale, or any status other than up/down: no evidence it works.
    stale = [
        r
        for r in results
        if r.get("status") != "down" and (r.get("stale") or r.get("status") != "up")
    ]
    up = sum(1 for r in results if r.get("status") == "up" and not r.get("stale"))
    if not down and not stale:
        return f"ระบบ ✅ ปกติ {up}/{len(results)}"
    parts = []
    if down:
        names = ", ".join(down[:5]) + (f" +{len(down) - 5}" if len(down) > 5 else "")
        parts.append(f"🔴 ล่ม {len(down)}: {names}")
    if stale:
        parts.append(f"⚪ ไม่ได้เช็ค {len(stale)}")
    parts.append(f"✅ ปกติ {up}")
    return "ระบบ " + " · ".join(parts)


def render(
    entries: list[dict[str, Any]],
    status: dict[str, Any] | None,
    today: date,
    board_url: str | None = None,
) -> str:
    lines = [
        f"☀️ สรุปเช้า {thai_day(today)}",
        "",
        status_line(status),
        "",
        *task_lines(entries, today),
    ]
    if board_url:
        lines += ["", f"→ ดูทั้งหมด: {board_url}"]
    text = "\n".join(lines)
    return text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None  # follow no redirect


def fetch_status(url: str, timeout: float = 10.0) -> dict[str, Any] | None:
    """The monitor's public status, or None when it cannot be read (never raises)."""
    if urllib.parse.urlsplit(url).scheme != "https":
        return None
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(
        url, headers={"User-Agent": "notifications-dispatch"}
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read(2_000_000))
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class Morning:
    """When the morning digest is due, and what it says."""

    def __init__(
        self,
        *,
        hour: int = BANGKOK_HOUR_DEFAULT,
        status: Callable[[], dict[str, Any] | None] | None = None,
        board_url: str | None = None,
    ) -> None:
        self.hour = hour
        self.status = status or (lambda: None)
        self.board_url = board_url

    @classmethod
    def from_env(cls, env: Any) -> "Morning | None":
        if (env.get("MORNING_DIGEST") or "on").strip().lower() in (
            "0",
            "off",
            "false",
            "no",
        ):
            return None
        try:
            hour = int(env.get("MORNING_DIGEST_HOUR") or BANGKOK_HOUR_DEFAULT)
        except ValueError:
            hour = BANGKOK_HOUR_DEFAULT
        hour = (
            hour if 7 <= hour <= 21 else BANGKOK_HOUR_DEFAULT
        )  # inside the sending window
        url = env.get("MORNING_STATUS_URL") or STATUS_URL_DEFAULT
        return cls(
            hour=hour,
            status=lambda: fetch_status(url),
            board_url=env.get("MORNING_BOARD_URL") or None,
        )

    def render(self, entries: list[dict[str, Any]], today: date) -> str:
        return render(entries, self.status(), today, self.board_url)

    def due(self, notes: Any, local_now: datetime) -> bool:
        """Past the morning hour and no digest recorded today (a failed lookup means not due)."""
        if local_now.hour < self.hour:
            return False
        return notes.lookup(state_key(local_now.date())) is None

    def mark(self, notes: Any, local_now: datetime, count: int) -> None:
        notes.queue(
            {
                "notification_type": STATE_TYPE,
                "channel": "console",
                "severity": "info",
                "title": f"Morning digest {local_now.date().isoformat()}",
                "message": "Internal marker: the morning digest was sent; never delivered.",
                "dedupe_key": state_key(local_now.date()),
                "metadata": {"sent_at": local_now.isoformat(), "entries": count},
            }
        )
