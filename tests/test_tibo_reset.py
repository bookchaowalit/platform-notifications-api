"""Reset-monitor contracts; all source I/O uses fake HTTP transport."""

import importlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

NOW = datetime(2026, 10, 15, 6, tzinfo=UTC)


def monitor():
    # Fail as an assertion while the feature is absent, rather than a collection error.
    from solo_notifications import dispatch

    assert hasattr(dispatch, "run_tibo_monitor"), (
        "dispatcher must expose the opt-in monitor"
    )
    return importlib.import_module("solo_notifications.tibo_reset")


def tweet(post_id="123", text="Day 10/ Reset", at=None, **extra):
    return {
        "id": post_id,
        "url": f"https://x.com/thsottiaux/status/{post_id}",
        "text": text,
        "at": at if isinstance(at, str) else (at or NOW).isoformat(),
        "is_reply": False,
        **extra,
    }


def feed(*tweets, **extra):
    return {
        "version": 1,
        "fetched_at": NOW.isoformat(),
        "source": "x-api",
        "source_scope": "timeline",
        "stale": False,
        "profile": {"handle": "thsottiaux", "name": "Tibo"},
        "tweets": list(tweets),
        "banked_observations": [],
        "events": [],
        **extra,
    }


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Day 10/ Reset", "promised"),
        (
            "The reports of my reset button being lost are greatly exaggerated\n\nDay 10/ Reset",
            "promised",
        ),
        ("Codex usage reset coming in two hours", "promised"),
        ("We have reset usage limits for Codex", "confirmed"),
        ("The reset has been processed", "confirmed"),
        ("We added a banked reset", "banked"),
        ("I will grant a banked reset tomorrow", "banked"),
        ("No reset today", None),
        ("We have not reset usage limits", None),
        ("If we reset usage limits tomorrow", None),
        ("Will there be a reset?", None),
        ("I hope a reset lands today", None),
        ("Factory reset your phone", None),
        ("Reset your password", None),
        ("Codex release: faster model and rate limit boost", None),
        ("RT @someone: Day 10/ Reset", None),
        ("Reset button joke, just kidding", None),
        ("We shipped a new feature", None),
        ("Codex reset might arrive tomorrow", None),
    ],
)
def test_conservative_categories(text, want):
    assert monitor().classify(text) == want


def test_context_reply_needs_an_accepted_parent_in_this_batch():
    m = monitor()
    reply = tweet(
        "124",
        "Landing within the next 2 hours",
        is_reply=True,
        in_reply_to_tweet_id="123",
    )
    posts = m.parse_relay(feed(tweet(), reply), NOW)
    assert [(p.id, c) for p, c in m.matches(posts)] == [
        ("123", "promised"),
        ("124", "promised"),
    ]
    assert m.matches(m.parse_relay(feed(reply), NOW)) == []
    assert (
        m.matches(m.parse_relay(feed(tweet(text="New feature tomorrow"), reply), NOW))
        == []
    )


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        ({"profile": {"handle": "other"}}, "unexpected profile"),
        ({"stale": True}, "invalid or stale relay"),
        ({"fetched_at": "2026-10-14T00:00:00Z"}, "stale or in the future"),
        ({"fetched_at": "bad"}, "Invalid isoformat string"),
        ({"fetched_at": "2026-10-15T06:00:00"}, "must include timezone"),
        ({"tweets": {}}, "invalid tweets"),
    ],
)
def test_reject_invalid_source_envelope(bad, reason):
    with pytest.raises(ValueError, match=reason):
        monitor().parse_relay(feed(tweet(), **bad), NOW)


def test_skip_wrong_author_bad_links_dates_and_retweets_but_keep_valid_posts():
    rows = [
        tweet("123"),
        tweet("124", url="https://evil.test/thsottiaux/status/124"),
        tweet("125", url="https://x.com/other/status/125"),
        tweet("126", at="bad"),
        tweet("127", is_retweet=True),
        tweet("128", url="https://x.com/thsottiaux/status/999"),
    ]
    assert [p.id for p in monitor().parse_relay(feed(*rows), NOW)] == ["123"]
    assert (
        monitor().parse_relay(feed(), NOW) == []
    )  # fresh but quiet timeline is healthy


def test_relay_fetch_once_bounded_json_and_no_authentication():
    m = monitor()
    seen = []

    def response(request):
        seen.append(request)
        return httpx.Response(200, json=feed(tweet()))

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        posts = m.fetch_posts(m.Settings(enabled=True), NOW, client=client)
    assert [p.id for p in posts] == ["123"]
    assert len(seen) == 1
    assert str(seen[0].url) == "https://codex-reset.com/api/feed"
    assert "authorization" not in seen[0].headers
    assert seen[0].headers["user-agent"].startswith("SoloEmpireNotifications/0.1")


@pytest.mark.parametrize(
    ("status", "body"),
    [(302, b""), (200, b"not-json"), (200, b"x" * 1_048_577)],
    ids=["redirect", "malformed-json", "oversized"],
)
def test_source_rejects_redirect_malformed_or_oversized(status, body):
    m = monitor()
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(status, content=body)
            )
        ) as client,
        pytest.raises((ValueError, httpx.HTTPError)),
    ):
        m.fetch_posts(m.Settings(enabled=True), NOW, client=client)


class Notes:
    def __init__(self):
        self.entries = {}
        self.down = False

    def lookup(self, key):
        return self.entries.get(key)

    def queue(self, fields):
        if self.down:
            raise RuntimeError("queue unavailable")
        if fields["dedupe_key"] in self.entries:
            return "existing"
        self.entries[fields["dedupe_key"]] = fields
        return "created"


def run(notes, posts, now=NOW, **kw):
    return monitor().run_monitor(
        notes,
        now,
        env={"TIBO_RESET_MONITOR_ENABLED": "1"},
        fetch=lambda settings, at: posts,
        **kw,
    )


def test_first_success_seeds_without_history_then_new_posts_queue_once():
    m = monitor()
    notes = Notes()
    old = m.parse_relay(feed(tweet(at=NOW - timedelta(hours=1))), NOW)
    assert run(notes, old)["queued"] == 0
    assert len(notes.entries) == 1
    marker = notes.entries[m.STATE_KEY]
    assert marker["notification_type"] == "tibo_reset_monitor_state"
    assert marker["channel"] == "console"
    fresh = m.parse_relay(feed(tweet("124", at=NOW + timedelta(minutes=1))), NOW)
    assert run(notes, fresh, NOW + timedelta(minutes=30))["queued"] == 1
    assert run(notes, fresh, NOW + timedelta(hours=1))["queued"] == 0
    alert = next(
        v for v in notes.entries.values() if v["notification_type"] == "tibo_reset"
    )
    assert "Data: codex-reset.com" in alert["message"]
    assert "https://x.com/thsottiaux/status/124" in alert["message"]
    assert "13:01" in alert["message"]
    assert "Day 10/ Reset" in alert["message"]
    assert "ประกาศว่าจะรีเซ็ต" in alert["message"]


def test_dry_run_reports_matches_without_any_writes_or_initialization():
    m = monitor()
    notes = Notes()
    result = run(notes, m.parse_relay(feed(tweet()), NOW), dry_run=True)
    assert result["matched"] == 1
    assert result["queued"] == 0
    assert notes.entries == {}


def test_disabled_does_not_fetch_or_touch_notes():
    def forbidden(*args):
        pytest.fail("disabled monitor must do no I/O")

    assert monitor().run_monitor(None, NOW, env={}, fetch=forbidden)["enabled"] is False


def test_source_failure_does_not_initialize_state():
    def broken(*args):
        raise httpx.ConnectError("source unavailable")

    notes = Notes()
    result = monitor().run_monitor(
        notes, NOW, env={"TIBO_RESET_MONITOR_ENABLED": "1"}, fetch=broken
    )
    assert result["errors"] == 1
    assert notes.entries == {}


def test_queue_failure_retries_and_old_future_posts_are_excluded():
    m = monitor()
    notes = Notes()
    run(notes, [])
    posts = m.parse_relay(
        feed(
            tweet("124", at=NOW + timedelta(minutes=1)),
            tweet("125", at=NOW - timedelta(days=3)),
            tweet("126", at=NOW + timedelta(days=1)),
        ),
        NOW,
    )
    notes.down = True
    assert run(notes, posts, NOW + timedelta(hours=1))["errors"] == 1
    notes.down = False
    assert run(notes, posts, NOW + timedelta(hours=1))["queued"] == 1


def test_concurrent_initialization_reuses_canonical_marker():
    m = monitor()

    class RacingNotes(Notes):
        def queue(self, fields):
            if fields["dedupe_key"] == m.STATE_KEY:
                self.entries[m.STATE_KEY] = {
                    **fields,
                    "metadata": {
                        "started_at": (NOW - timedelta(minutes=5)).isoformat()
                    },
                }
                return "existing"
            return super().queue(fields)

    notes = RacingNotes()
    assert run(notes, m.parse_relay(feed(tweet()), NOW))["queued"] == 1


def test_explicit_x_mode_requires_credentials_and_never_falls_back():
    m = monitor()
    with pytest.raises(ValueError, match="requires bearer token"):
        m.Settings.from_env(
            {"TIBO_RESET_MONITOR_ENABLED": "1", "TIBO_RESET_SOURCE": "x"}
        )
    with pytest.raises(ValueError, match="unsupported reset source"):
        m.Settings.from_env(
            {
                "TIBO_RESET_MONITOR_ENABLED": "1",
                "TIBO_RESET_SOURCE": "https://evil.test",
            }
        )


def test_x_mode_verifies_fixed_author_and_ignores_retweets():
    m = monitor()
    seen = []

    def respond(request):
        seen.append(request)
        assert request.url.host == "api.x.com"
        if request.url.path == "/2/users/by/username/thsottiaux":
            return httpx.Response(
                200, json={"data": {"id": "42", "username": "thsottiaux"}}
            )
        assert request.url.path == "/2/users/42/tweets"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "123",
                        "text": "Day 10/ Reset",
                        "created_at": NOW.isoformat(),
                        "author_id": "42",
                    },
                    {
                        "id": "124",
                        "text": "Day 10/ Reset",
                        "created_at": NOW.isoformat(),
                        "author_id": "99",
                    },
                    {
                        "id": "125",
                        "text": "Day 10/ Reset",
                        "created_at": NOW.isoformat(),
                        "author_id": "42",
                        "referenced_tweets": [{"type": "retweeted", "id": "7"}],
                    },
                ]
            },
        )

    settings = m.Settings.from_env(
        {
            "TIBO_RESET_MONITOR_ENABLED": "1",
            "TIBO_RESET_SOURCE": "x",
            "X_BEARER_TOKEN": "fake-test-token",
            "TIBO_X_USER_ID": "42",
        }
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert [p.id for p in m.fetch_posts(settings, NOW, client=client)] == ["123"]
    assert len(seen) == 2
    assert all(r.headers["authorization"] == "Bearer fake-test-token" for r in seen)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("I'm resetting Codex usage limits", "promised"),
        ("Codex usage limits have been reset", "confirmed"),
        ("Resetting my git branch tomorrow", None),
        ("A database reset is coming tonight", None),
    ],
)
def test_reset_language_is_bound_to_usage_announcement(text, want):
    assert monitor().classify(text) == want


def test_old_or_future_parents_do_not_authorize_context_reply():
    m = monitor()
    notes = Notes()
    run(notes, [])
    rows = [
        tweet("123", at=NOW - timedelta(days=3)),
        tweet(
            "124",
            "Landing within the next 2 hours",
            at=NOW + timedelta(minutes=1),
            is_reply=True,
            in_reply_to_tweet_id="123",
        ),
    ]
    assert (
        run(notes, m.parse_relay(feed(*rows), NOW), NOW + timedelta(hours=1))["matched"]
        == 0
    )


def test_failed_initial_queue_is_retried_without_a_historical_alert():
    m = monitor()
    notes = Notes()
    notes.down = True
    assert run(notes, m.parse_relay(feed(tweet()), NOW))["errors"] == 1
    assert notes.entries == {}
    notes.down = False
    result = run(notes, m.parse_relay(feed(tweet()), NOW), NOW + timedelta(minutes=30))
    assert result["initialized"] is True
    assert result["queued"] == 0


def test_x_wrong_identity_stops_before_timeline_request():
    m = monitor()
    settings = m.Settings(enabled=True, source="x", bearer_token="fake", user_id="42")
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200, json={"data": {"id": "99", "username": "thsottiaux"}}
        )

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises(ValueError, match="unexpected X author"),
    ):
        m.fetch_posts(settings, NOW, client=client)
    assert len(seen) == 1


def test_public_preview_outputs_categories_without_accessing_notes_or_writes(
    monkeypatch, capsys
):
    m = monitor()
    monkeypatch.setenv(
        "TIBO_RESET_SOURCE", "x"
    )  # preview must still use the no-key public relay
    monkeypatch.setenv("NOTIFICATIONS_DISPATCH_TOKEN", "never-use-this-test-token")
    seen = []

    def public(settings, now):
        seen.append(settings)
        return [
            m.Post("123", "Day 10/ Reset", now, "https://x.com/thsottiaux/status/123")
        ]

    monkeypatch.setattr(m, "fetch_posts", public)
    assert m.main(["--preview"]) == 0
    output = capsys.readouterr().out
    assert '"category": "promised"' in output
    assert "never-use-this-test-token" not in output
    assert seen[0].source == "relay"
    assert seen[0].bearer_token == ""


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (
            "Usage limits have been reset for all paid ChatGPT Work and Codex users",
            "confirmed",
        ),
        ("We've reset 5-hour and weekly rate limits for all users", "confirmed"),
        ("We’ve reset 5-hour and weekly rate limits for all users", "confirmed"),
        ("A reset is also landing by midnight today", "promised"),
        ("We are resetting usage limits for Codex", "promised"),
        ("We haven’t reset usage limits", None),
    ],
)
def test_actual_announcement_forms(text, want):
    assert monitor().classify(text) == want


@pytest.mark.parametrize(
    "text",
    [
        "We aren't resetting Codex usage limits",
        "We aren’t resetting Codex usage limits",
        "We weren't resetting Codex usage limits",
        "We weren’t resetting Codex usage limits",
        "We couldn't reset usage limits tomorrow",
        "We couldn’t reset usage limits tomorrow",
        "We may reset usage limits tomorrow",
        "We might reset usage limits tomorrow",
        "We would reset usage limits tomorrow",
        "If we reset usage limits tomorrow",
    ],
)
def test_review_finding1_negations_and_hypotheticals_never_announce(text):
    assert monitor().classify(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "We will have reset usage limits in two hours",
        "We’ll have reset usage limits in two hours",
        "We are going to have reset usage limits tomorrow",
    ],
)
def test_review_finding2_future_perfect_is_promised_with_honest_alert(text):
    m = monitor()
    assert m.classify(text) == "promised"
    notes = Notes()
    run(notes, [])
    run(notes, m.parse_relay(feed(tweet(text=text)), NOW))
    alert = next(
        e for e in notes.entries.values() if e["notification_type"] == "tibo_reset"
    )
    assert "ประกาศว่าจะรีเซ็ต" in alert["message"]
    assert "ประกาศว่ารีเซ็ตแล้ว" not in alert["message"]


@pytest.mark.parametrize(
    "text",
    [
        "We will have added a banked reset tomorrow",
        "We’ll have granted a banked reset tomorrow",
        "We are going to have credited a banked reset tomorrow",
    ],
)
def test_review_finding2_future_banked_credit_never_claims_delivery(text):
    m = monitor()
    assert m.classify(text) == "banked"
    notes = Notes()
    run(notes, [])
    run(notes, m.parse_relay(feed(tweet(text=text)), NOW))
    alert = next(
        e for e in notes.entries.values() if e["notification_type"] == "tibo_reset"
    )
    assert "ประกาศว่าจะเพิ่ม banked reset" in alert["message"]
    assert "ประกาศว่าเพิ่ม banked reset แล้ว" not in alert["message"]


@pytest.mark.parametrize(
    "text",
    [
        "We will reset our pricing tomorrow",
        "We are resetting our sleep schedule today",
        "We have reset our pricing limits",
        "We have reset the project time limits",
    ],
)
def test_review_finding3_unrelated_reset_does_not_authorize_landing_reply(text):
    m = monitor()
    assert m.classify(text) is None
    rows = [
        tweet(text=text),
        tweet(
            "124",
            "Landing within the next 2 hours",
            is_reply=True,
            in_reply_to_tweet_id="123",
        ),
    ]
    assert m.matches(m.parse_relay(feed(*rows), NOW)) == []


@pytest.mark.parametrize(
    ("text", "category", "future_label", "completed_label"),
    [
        (
            "We will, as promised, have reset usage limits by tomorrow",
            "promised",
            "ประกาศว่าจะรีเซ็ต",
            "ประกาศว่ารีเซ็ตแล้ว",
        ),
        (
            "We’ll, after the rollout, have reset usage limits by tomorrow",
            "promised",
            "ประกาศว่าจะรีเซ็ต",
            "ประกาศว่ารีเซ็ตแล้ว",
        ),
        (
            "We will, as promised, have added a banked reset by tomorrow",
            "banked",
            "ประกาศว่าจะเพิ่ม banked reset",
            "ประกาศว่าเพิ่ม banked reset แล้ว",
        ),
        (
            "We are going to, after the rollout, have granted a banked reset tomorrow",
            "banked",
            "ประกาศว่าจะเพิ่ม banked reset",
            "ประกาศว่าเพิ่ม banked reset แล้ว",
        ),
    ],
)
def test_interposed_clause_preserves_future_category_and_alert(
    text, category, future_label, completed_label
):
    m = monitor()
    assert m.classify(text) == category
    notes = Notes()
    run(notes, [])
    assert run(notes, m.parse_relay(feed(tweet(text=text)), NOW))["queued"] == 1
    alert = next(
        e for e in notes.entries.values() if e["notification_type"] == "tibo_reset"
    )
    assert future_label in alert["message"]
    assert completed_label not in alert["message"]


@pytest.mark.parametrize(
    "text",
    [
        "We will reset our pricing for Codex tomorrow",
        "We are resetting our sleep schedule after Codex launch today",
        "We have reset our pricing limits for Codex",
        "We have reset our pricing limits for ChatGPT",
    ],
)
def test_product_mention_does_not_authorize_unrelated_reset_or_reply(text):
    m = monitor()
    assert m.classify(text) is None
    rows = [
        tweet(text=text),
        tweet(
            "124",
            "Landing within the next 2 hours",
            is_reply=True,
            in_reply_to_tweet_id="123",
        ),
    ]
    assert m.matches(m.parse_relay(feed(*rows), NOW)) == []


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("We will reset usage limits on May 1", "promised"),
        ("We have reset usage limits on May 1", "confirmed"),
        ("We have reset usage limits in May", "confirmed"),
        ("We may reset usage limits tomorrow", None),
        ("We may, after the rollout, have reset usage limits tomorrow", None),
        ("We may also have added a banked reset tomorrow", None),
        ("Codex reset coming tomorrow", "promised"),
        ("We will post details tomorrow. We have reset usage limits", "confirmed"),
        ("We will post details tomorrow. We added a banked reset", "banked"),
    ],
)
def test_month_dates_modal_uncertainty_and_sentence_boundaries(text, want):
    assert monitor().classify(text) == want


@pytest.mark.parametrize("joiner", [", but", " and"])
@pytest.mark.parametrize(
    ("predicate", "category", "completed_label", "future_label"),
    [
        ("have reset usage limits", "confirmed", "ประกาศว่ารีเซ็ตแล้ว", "ประกาศว่าจะรีเซ็ต"),
        (
            "have added a banked reset",
            "banked",
            "ประกาศว่าเพิ่ม banked reset แล้ว",
            "ประกาศว่าจะเพิ่ม banked reset",
        ),
    ],
)
def test_unrelated_future_clause_does_not_change_completed_reset_alert(
    joiner, predicate, category, completed_label, future_label
):
    m = monitor()
    text = f"We will share more details tomorrow{joiner} we {predicate}"
    assert m.classify(text) == category
    notes = Notes()
    run(notes, [])
    assert run(notes, m.parse_relay(feed(tweet(text=text)), NOW))["queued"] == 1
    alert = next(
        e for e in notes.entries.values() if e["notification_type"] == "tibo_reset"
    )
    assert completed_label in alert["message"]
    assert future_label not in alert["message"]
