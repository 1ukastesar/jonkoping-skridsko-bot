from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from skridsko_bot.app import next_run_at, target_days, run_once
from skridsko_bot.config import Config
from skridsko_bot.models import PuckStatus

FIXTURES = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Europe/Stockholm")


def make_config(tmp_path, **overrides):
    defaults = dict(
        webhook_url="https://discord.com/api/webhooks/1/abc",
        url="https://example.invalid/skridsko",
        timezone=TZ,
        post_at=time(7, 0),
        cache_dir=str(tmp_path),
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_next_run_is_today_when_still_ahead(tmp_path):
    config = make_config(tmp_path)
    now = datetime(2025, 12, 22, 6, 0, tzinfo=TZ)
    assert next_run_at(config, now) == datetime(2025, 12, 22, 7, 0, tzinfo=TZ)


def test_next_run_rolls_over_after_the_post_time(tmp_path):
    config = make_config(tmp_path)
    now = datetime(2025, 12, 22, 7, 0, tzinfo=TZ)
    assert next_run_at(config, now) == datetime(2025, 12, 23, 7, 0, tzinfo=TZ)


def test_lookahead_controls_the_day_list(tmp_path):
    from datetime import date

    config = make_config(tmp_path, lookahead_days=2)
    assert target_days(config, date(2025, 12, 22)) == [
        date(2025, 12, 22),
        date(2025, 12, 23),
        date(2025, 12, 24),
    ]


def test_run_once_posts_the_payload(tmp_path, monkeypatch):
    from datetime import date

    sent = {}

    def fake_send(url, payload, **kwargs):
        sent["url"] = url
        sent["payload"] = payload

    monkeypatch.setattr("skridsko_bot.app.discord_sender.send", fake_send)

    config = make_config(tmp_path)
    html = (FIXTURES / "schedule_tables.html").read_text(encoding="utf-8")
    result = run_once(config, html=html, on_date=date(2025, 12, 22))

    assert sent["url"] == config.webhook_url
    assert sent["payload"]["embeds"][0]["title"].endswith("Monday 22 December")
    assert any(s.puck is PuckStatus.WITHOUT_PUCK for s in result.sessions)
    assert (tmp_path / "healthy").exists()


def test_run_once_can_stay_silent_on_empty_days(tmp_path, monkeypatch):
    from datetime import date

    calls = []
    monkeypatch.setattr(
        "skridsko_bot.app.discord_sender.send",
        lambda *a, **k: calls.append(a),
    )

    config = make_config(tmp_path, post_when_empty=False)
    off_season = "<html><body><main><h1>Skridskoåkning</h1><p>Isen är avstängd.</p></main></body></html>"
    run_once(config, html=off_season, on_date=date(2026, 6, 1))
    assert calls == []


def test_rink_filter_is_applied(tmp_path, monkeypatch):
    from datetime import date

    monkeypatch.setattr("skridsko_bot.app.discord_sender.send", lambda *a, **k: None)
    config = make_config(tmp_path, rink_filter=("gränna",))
    html = (FIXTURES / "schedule_tables.html").read_text(encoding="utf-8")
    result = run_once(config, html=html, on_date=date(2025, 12, 28))
    assert result.sessions
    assert all("Gränna" in s.rink for s in result.sessions)


def test_describe_config_reports_the_effective_settings(tmp_path):
    from skridsko_bot.app import describe_config

    text = describe_config(make_config(tmp_path, lookahead_days=3, rink_filter=("gränna",)))
    assert "lookahead_days=3" in text
    assert "rinks=gränna" in text
    assert "webhook=set" in text
    # The webhook URL is a credential; only its presence is logged.
    assert "discord.com" not in text
