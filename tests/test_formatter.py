from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from skridsko_bot.formatter import build_payload, human_date, render_plaintext, session_line
from skridsko_bot.models import PuckStatus, ScrapeResult, Session
from skridsko_bot.parser import parse_page

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2025, 12, 22)
NOW = datetime(2025, 12, 22, 7, 0)


@pytest.fixture(scope="module")
def result():
    html = (FIXTURES / "schedule_tables.html").read_text(encoding="utf-8")
    parsed = parse_page(html, today=TODAY, page_url="https://example.invalid/skridsko")
    return parsed


def test_human_date():
    assert human_date(date(2025, 12, 22)) == "Monday 22 December"


def test_session_line_names_both_languages():
    session = Session(rink="X", start=time(12, 0), end=time(13, 20), puck=PuckStatus.WITHOUT_PUCK)
    line = session_line(session)
    assert "12:00" in line and "13:20" in line
    assert "no stick or puck" in line
    assert "utan klubba och puck" in line


def test_unknown_puck_is_stated_explicitly():
    session = Session(rink="X", start=time(12, 0), puck=PuckStatus.UNKNOWN)
    assert "not stated" in session_line(session)


def test_payload_has_a_single_embed_for_multiple_days(result):
    payload = build_payload(result, [TODAY, date(2025, 12, 23)], now=NOW)
    assert len(payload["embeds"]) == 1
    title = payload["embeds"][0]["title"]
    assert "Monday 22 December" in title
    assert "Tuesday 23 December" in title
    description = payload["embeds"][0]["description"]
    assert "Monday 22 December" in description
    assert "Tuesday 23 December" in description


def test_payload_groups_sessions_by_rink(result):
    payload = build_payload(result, [date(2025, 12, 28)], now=NOW)
    names = [field["name"] for field in payload["embeds"][0]["fields"]]
    assert any("Gränna" in name for name in names)
    assert any("Rosenlund" in name for name in names)


def test_empty_day_says_so():
    empty = ScrapeResult(sessions=[], page_url="https://example.invalid")
    payload = build_payload(empty, [TODAY], now=NOW)
    assert "No public skating" in payload["embeds"][0]["description"]


def test_no_mention_means_no_pings(result):
    payload = build_payload(result, [TODAY], now=NOW)
    assert payload["allowed_mentions"]["parse"] == []
    assert "content" not in payload


def test_mention_is_passed_through(result):
    payload = build_payload(result, [TODAY], mention="<@&1234>", now=NOW)
    assert payload["content"] == "<@&1234>"
    assert "roles" in payload["allowed_mentions"]["parse"]


def test_user_mention_is_actually_allowed_to_ping(result):
    # Discord only pings types listed in allowed_mentions.parse, regardless of content.
    payload = build_payload(result, [TODAY], mention="<@123456789012345678>", now=NOW)
    assert "users" in payload["allowed_mentions"]["parse"]


def test_cache_warning_reaches_the_embed():
    stale = ScrapeResult(sessions=[], page_url="https://example.invalid")
    stale.fetched_from_cache = True
    payload = build_payload(stale, [TODAY], now=NOW)
    assert "cached" in payload["embeds"][0]["description"]


def test_embed_fields_stay_within_discord_limits(result):
    payload = build_payload(result, [TODAY, date(2025, 12, 28)], now=NOW)
    for embed in payload["embeds"]:
        assert len(embed["title"]) <= 256
        assert len(embed.get("description", "")) <= 4096
        fields = embed.get("fields", [])
        assert len(fields) <= 25
        for field in fields:
            assert len(field["name"]) <= 256
            assert len(field["value"]) <= 1024


def test_plaintext_rendering_mentions_times(result):
    text = render_plaintext(result, [TODAY])
    assert "12:00–13:20" in text
    assert "no stick or puck" in text


# --- against the real saved page -------------------------------------------

REAL_TODAY = date(2026, 9, 23)


@pytest.fixture(scope="module")
def real_result():
    from skridsko_bot.parser import parse_page

    html = (FIXTURES / "jonkoping_2026-09-18.html").read_text(encoding="utf-8")
    return parse_page(html, today=REAL_TODAY, page_url="https://www.jonkoping.se/")


def test_real_page_message_states_both_puck_rules(real_result):
    payload = build_payload(real_result, [REAL_TODAY], now=NOW)
    values = "\n".join(f["value"] for f in payload["embeds"][0]["fields"])
    assert "no stick or puck" in values
    assert "utan klubba och puck" in values
    assert "stick and puck allowed" in values
    assert "med klubba och puck" in values


def test_real_page_message_lists_both_rinks_open_that_day(real_result):
    payload = build_payload(real_result, [REAL_TODAY], now=NOW)
    names = [f["name"] for f in payload["embeds"][0]["fields"]]
    assert any("Smedjehov" in n for n in names)
    assert any("D-hallen" in n for n in names)


def test_a_day_beyond_the_published_window_says_the_page_may_be_stale(real_result):
    payload = build_payload(real_result, [date(2026, 10, 15)], now=NOW)
    description = payload["embeds"][0]["description"]
    assert "may simply not have been refreshed" in description
    assert "Sunday 27 September" in description


def test_a_gap_day_inside_the_window_does_not_claim_staleness(real_result):
    payload = build_payload(real_result, [date(2026, 9, 20)], now=NOW)
    description = payload["embeds"][0]["description"]
    assert "refreshed" not in description
    assert "Other days are listed" in description


def test_multiple_days_share_one_embed_and_url(real_result):
    days = [date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]
    payload = build_payload(real_result, days, now=NOW)
    assert len(payload["embeds"]) == 1
    assert payload["embeds"][0]["url"] == "https://www.jonkoping.se/"


def test_lookahead_still_fits_in_a_single_message(real_result):
    many = [date(2026, 9, 19) + timedelta(days=i) for i in range(20)]
    payload = build_payload(real_result, many, now=NOW)
    assert len(payload["embeds"]) == 1


def test_total_embed_size_stays_within_budget(real_result):
    from skridsko_bot.formatter import _embeds_length

    many = [date(2026, 9, 19) + timedelta(days=i) for i in range(10)]
    payload = build_payload(real_result, many, now=NOW)
    assert _embeds_length(payload["embeds"]) <= 5800


def test_no_url_when_the_page_url_is_unknown():
    empty = ScrapeResult(sessions=[], page_url="")
    payload = build_payload(empty, [TODAY], now=NOW)
    assert "url" not in payload["embeds"][0]
