"""Opt-in Tibo reset announcements; public relay preview never touches the notifications API."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from solo_notifications._sentry import capture

RELAY_URL = "https://codex-reset.com/api/feed"
STATE_KEY = "tibo-reset-monitor:thsottiaux:started:v1"
USER_AGENT = "SoloEmpireNotifications/0.1 (+https://github.com/bookchaowalit/platform-notifications-api)"
MAX_BYTES = 1_048_576
BANGKOK = ZoneInfo("Asia/Bangkok")


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    source: str = "relay"
    lookback_hours: int = 48
    bearer_token: str = ""
    user_id: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        if env.get("TIBO_RESET_MONITOR_ENABLED") != "1":
            return cls()
        source = env.get("TIBO_RESET_SOURCE", "relay")
        if source not in ("relay", "x"):
            raise ValueError("unsupported reset source")
        hours = int(env.get("TIBO_RESET_LOOKBACK_HOURS", "48"))
        if not 1 <= hours <= 168:
            raise ValueError("lookback must be 1..168 hours")
        token, user_id = env.get("X_BEARER_TOKEN", ""), env.get("TIBO_X_USER_ID", "")
        if source == "x" and (
            not token
            or any(c.isspace() for c in token)
            or not re.fullmatch(r"[0-9]+", user_id)
        ):
            raise ValueError("X source requires bearer token and numeric user id")
        return cls(True, source, hours, token, user_id)


@dataclass(frozen=True)
class Post:
    id: str
    text: str
    at: datetime
    url: str
    parent_id: str | None = None
    provenance: str = "codex-reset.com"


def _date(value: Any) -> datetime:
    if not isinstance(value, str):
        # ValueError, not TypeError: callers treat any malformed relay data as ValueError.
        raise ValueError("missing timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162 - keeps Z-normalisation; removing it changes which inputs are accepted
    if stamp.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return stamp.astimezone(UTC)


def _post(row: Any, *, provenance: str = "codex-reset.com") -> Post | None:
    if not isinstance(row, dict):
        return None
    post_id, text = row.get("id"), row.get("text")
    if not isinstance(post_id, str) or not re.fullmatch(r"[0-9]{1,30}", post_id):
        return None
    url = f"https://x.com/thsottiaux/status/{post_id}"
    if (
        row.get("url") != url
        or row.get("is_retweet")
        or not isinstance(text, str)
        or not 0 < len(text) <= 6000
    ):
        return None
    if re.match(r"\s*RT\s+@", text, re.IGNORECASE):
        return None
    try:
        at = _date(row.get("at"))
    except (ValueError, TypeError, OverflowError):
        return None
    parent = row.get("in_reply_to_tweet_id") if row.get("is_reply") is True else None
    return Post(
        post_id, text, at, url, parent if isinstance(parent, str) else None, provenance
    )


def parse_relay(payload: Any, now: datetime) -> list[Post]:
    """Trust only a fresh, identified timeline's raw posts, never inferred relay events."""
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("stale") is not False
    ):
        raise ValueError("invalid or stale relay")
    profile = payload.get("profile")
    if not isinstance(profile, dict) or profile.get("handle") != "thsottiaux":
        raise ValueError("unexpected profile")
    fetched = _date(payload.get("fetched_at"))
    if now - fetched > timedelta(hours=2) or fetched - now > timedelta(minutes=5):
        raise ValueError("relay fetch timestamp is stale or in the future")
    rows = payload.get("tweets")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("invalid tweets")
    return [post for row in rows if (post := _post(row)) is not None]


def _get(
    client: httpx.Client,
    url: str,
    *,
    token: str = "",
    params: dict[str, Any] | None = None,
) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with client.stream(
        "GET", url, headers=headers, params=params, timeout=10, follow_redirects=False
    ) as response:
        response.raise_for_status()  # rejects redirects as well as failures
        raw = bytearray()
        for part in response.iter_bytes():
            raw.extend(part)
            if len(raw) > MAX_BYTES:
                raise ValueError("source response too large")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("invalid source JSON") from exc


def fetch_posts(
    settings: Settings, now: datetime, *, client: httpx.Client | None = None
) -> list[Post]:
    if client is None:
        with httpx.Client(trust_env=False) as owned:
            return fetch_posts(settings, now, client=owned)
    if settings.source == "relay":
        return parse_relay(_get(client, RELAY_URL), now)
    if (
        settings.source != "x"
        or not settings.bearer_token
        or not re.fullmatch(r"[0-9]+", settings.user_id)
    ):
        raise ValueError("X source configuration missing")
    # Verify the configured numeric ID belongs to the fixed handle before reading a timeline.
    identity = _get(
        client,
        "https://api.x.com/2/users/by/username/thsottiaux",
        token=settings.bearer_token,
    )
    data = identity.get("data") if isinstance(identity, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("id") != settings.user_id
        or data.get("username", "").lower() != "thsottiaux"
    ):
        raise ValueError("unexpected X author")
    payload = _get(
        client,
        f"https://api.x.com/2/users/{settings.user_id}/tweets",
        token=settings.bearer_token,
        params={
            "tweet.fields": "created_at,author_id,referenced_tweets",
            "max_results": 100,
        },
    )
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("invalid X response")
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        # ValueError, not TypeError: callers treat any malformed X response as ValueError.
        raise ValueError("invalid X posts")
    posts = []
    for row in rows:
        if not isinstance(row, dict) or row.get("author_id") != settings.user_id:
            continue
        refs = row.get("referenced_tweets", [])
        if not isinstance(refs, list) or any(
            not isinstance(r, dict) or r.get("type") == "retweeted" for r in refs
        ):
            continue
        parent = next(
            (r.get("id") for r in refs if r.get("type") == "replied_to"), None
        )
        normalized = {
            **row,
            "url": f"https://x.com/thsottiaux/status/{row.get('id')}",
            "at": row.get("created_at"),
            "is_reply": parent is not None,
            "in_reply_to_tweet_id": parent,
        }
        if (post := _post(normalized, provenance="X API (@thsottiaux)")) is not None:
            posts.append(post)
    return posts


_SUPPRESS = re.compile(
    r"\?|\b(?:no|not|never|won't|isn't|haven't|hasn't|didn't|don't|doesn't|cannot|can't|without)\b|"
    r"\b(?:if|unless|maybe|might|could|would|hope|wish|speculat\w*|jok\w*|kidding)\b|"
    r"\bmay(?:\s+|,\s*[^,.!?;\n]{1,80},\s*)(?:(?:\w+ly|also|well)\s+){0,3}"
    r"(?:reset|be|have|add|grant|credit)\b|"
    r"\b(?:password|factory|phone|device|git|branch|database|server)\b|^\s*RT\s+@",
    re.IGNORECASE,
)
_BANKED_DELIVERED = re.compile(
    r"\b(?:added|granted|credited)\b.{0,50}\bbanked resets?\b", re.IGNORECASE
)
# A product mention elsewhere does not make an unrelated reset a quota announcement.
_USAGE_CONTEXT = re.compile(
    r"\b(?:usage|quotas?|rate limits?|(?:Codex|ChatGPT)\s+resets?)\b", re.IGNORECASE
)
# Attach the modal to the reset predicate through an optional parenthetical and auxiliaries.
_FUTURE_ASPECT = re.compile(
    r"\b(?:will|shall|going to)(?:\s+|,\s*[^,.!?;\n]{1,80},\s*)"
    r"(?:(?:have|be|been|\w+ly|also|already|soon|just)\s+){0,6}"
    r"(?:reset|added|granted|credited)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    text = text.replace(
        "\u2019", "'"
    )  # RIGHT SINGLE QUOTATION MARK, as typed in "We've"
    text = re.sub(r"(?i)\b(\w+)n't\b", r"\1 not", text)
    text = re.sub(r"(?i)\b(we|i|you|they)'ve\b", r"\1 have", text)
    return re.sub(r"(?i)\b(we|i|you|they|he|she|it)'ll\b", r"\1 will", text)


def classify(text: str) -> str | None:
    """Conservative language recognition; announcement categories never verify a user's quota."""
    if not isinstance(text, str):
        return None
    text = _normalize(text)
    if _SUPPRESS.search(text):
        return None
    if re.search(r"\bbanked resets?\b", text, re.IGNORECASE):
        if _BANKED_DELIVERED.search(text) or re.search(
            r"\b(?:will|going to)\s+(?:add|grant|credit)\b.{0,50}\bbanked resets?\b",
            text,
            re.IGNORECASE,
        ):
            return "banked"
        return None
    relevant = _USAGE_CONTEXT.search(text) is not None
    future = _FUTURE_ASPECT.search(text) is not None
    completion = re.search(
        r"\breset\s+(?:has been|was|is)\s+(?:processed|completed|applied)\b",
        text,
        re.IGNORECASE,
    )
    if not future and (
        completion
        or (
            relevant
            and re.search(
                r"\b(?:have|has|just)\s+reset\s+[^.!?\n]{0,80}(?:usage|limits|quotas)\b|"
                r"\b(?:usage limits|quotas)\s+(?:have been|were|are)\s+reset\b",
                text,
                re.IGNORECASE,
            )
        )
    ):
        return "confirmed"
    # Standalone announcements name no other thing being reset; generic promises need usage context.
    if re.search(r"\bday\s+\d+\s*/\s*reset\b", text, re.IGNORECASE) or re.fullmatch(
        r"\s*(?:A |The )?reset (?:is )?(?:also )?(?:coming|landing|incoming) "
        r"(?:by midnight today|today|tonight|tomorrow|soon)[.!]?\s*",
        text,
        re.IGNORECASE,
    ):
        return "promised"
    if relevant and (
        future
        or re.search(
            r"\breset(?:s|ting)?\b.{0,50}\b(?:coming|today|tonight|tomorrow|soon|in \d+ hours?)\b|"
            r"\b(?:will|going to|about to)\s+reset\b|\breset\s+(?:incoming|time)\b|"
            r"\bresetting\s+(?:Codex\s+)?(?:usage limits|quotas)\b",
            text,
            re.IGNORECASE,
        )
    ):
        return "promised"
    return None


def matches(posts: list[Post]) -> list[tuple[Post, str]]:
    accepted = {
        p.id: category for p in posts if (category := classify(p.text)) is not None
    }
    result = []
    for post in posts:
        category = accepted.get(post.id)
        if (
            category is None
            and post.parent_id in accepted
            and not _SUPPRESS.search(post.text)
            and re.fullmatch(
                r"\s*Landing within (?:the )?next\s*\d+\s*hours?[.!]?\s*",
                post.text,
                re.IGNORECASE,
            )
        ):
            category = "promised"
        if category:
            result.append((post, category))
    return result


def _alert(post: Post, category: str) -> dict[str, Any]:
    labels = {
        "promised": "ประกาศว่าจะรีเซ็ต",
        "confirmed": "ประกาศว่ารีเซ็ตแล้ว",
        "banked": "ประกาศเกี่ยวกับ banked reset",
    }
    detail = labels[category]
    if category == "banked":
        text = _normalize(post.text)
        delivered = _BANKED_DELIVERED.search(text) and not _FUTURE_ASPECT.search(text)
        detail = (
            "ประกาศว่าเพิ่ม banked reset แล้ว"
            if delivered
            else "ประกาศว่าจะเพิ่ม banked reset"
        )
    return {
        "notification_type": "tibo_reset",
        "title": f"Tibo — {labels[category]}",
        "severity": "info",
        "channel": "telegram",
        "message": f"Tibo (@thsottiaux): {detail}\n"
        f"เวลาโพสต์: {post.at.astimezone(BANGKOK):%Y-%m-%d %H:%M} (Asia/Bangkok)\n\n"
        f"{post.text}\n\n{post.url}\nData: {post.provenance}\n"
        "เป็นประกาศของ Tibo; ยังไม่ได้ตรวจสอบโควตาบัญชีของคุณ",
        "dedupe_key": f"tibo-reset:thsottiaux:{post.id}:{category}",
        "metadata": {
            "post_id": post.id,
            "category": category,
            "source": post.provenance,
            "posted_at": post.at.isoformat(),
        },
    }


def run_monitor(
    notes: Any,
    now: datetime,
    *,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
    fetch: Callable[[Settings, datetime], list[Post]] | None = None,
) -> dict[str, Any]:
    """Source failures and per-alert queue failures are isolated; failed writes retry next poll."""
    reported = False
    result = {
        "enabled": False,
        "matched": 0,
        "queued": 0,
        "errors": 0,
        "initialized": False,
    }
    try:
        settings = Settings.from_env(os.environ if env is None else env)
        result["enabled"] = settings.enabled
        if not settings.enabled:
            return result
        posts = (fetch or fetch_posts)(settings, now)
        marker = notes.lookup(STATE_KEY)
        if marker is None and not dry_run:
            outcome = notes.queue(
                {
                    "notification_type": "tibo_reset_monitor_state",
                    "channel": "console",
                    "severity": "info",
                    "title": "Tibo reset monitor initialized",
                    "message": "Internal monitor state; never delivered.",
                    "dedupe_key": STATE_KEY,
                    "metadata": {"started_at": now.isoformat()},
                }
            )
            # Always reuse the persisted winner, including another process's earlier start time.
            marker = notes.lookup(STATE_KEY)
            if marker is None:
                raise ValueError("monitor state unavailable after initialization")
            result["initialized"] = outcome == "created"
            if outcome == "created":
                return result  # first successful read seeds only; no historical alerts
        since = (
            _date(marker["metadata"]["started_at"])
            if marker
            else now - timedelta(hours=settings.lookback_hours)
        )
        since = max(since, now - timedelta(hours=settings.lookback_hours))
        # Parent context is also bounded by time: historical/future parents cannot authorize a reply.
        eligible = [p for p in posts if since <= p.at <= now]
        for post, category in matches(eligible):
            result["matched"] += 1
            if dry_run:
                continue
            try:
                result["queued"] += notes.queue(_alert(post, category)) == "created"
            except Exception as exc:  # noqa: BLE001 - count the failure and keep queueing the other alerts
                if not reported:  # one Sentry event per run, not one per alert
                    capture(exc)
                    reported = True
                result["errors"] += 1
    except Exception as exc:  # noqa: BLE001 - the monitor must never break the dispatch run
        # Do not log exception text: sources, API clients or malformed config may carry secrets.
        # Sentry gets it scrubbed (no locals; URL passwords, tokens and JWTs removed).
        capture(exc)
        result["errors"] += 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preview",
        "--dry-run",
        action="store_true",
        required=True,
        help="read the public relay only; never read API/bot config or write state",
    )
    parser.parse_args(argv)
    now = datetime.now(UTC)
    try:
        posts = fetch_posts(Settings(enabled=True), now)
        recent = [p for p in posts if now - timedelta(hours=48) <= p.at <= now]
        found = [
            {
                "id": p.id,
                "category": c,
                "url": p.url,
                "text": p.text,
                "at": p.at.isoformat(),
            }
            for p, c in matches(recent)
        ]
        print(
            json.dumps(
                {
                    "preview": True,
                    "source": "codex-reset.com",
                    "posts": len(posts),
                    "matches": found,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - preview reports the error class instead of a traceback
        capture(exc)
        print(
            json.dumps(
                {
                    "preview": True,
                    "source": "codex-reset.com",
                    "error": type(exc).__name__,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
