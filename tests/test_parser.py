from datetime import date, time
from pathlib import Path

import pytest

from skridsko_bot.models import PuckStatus
from skridsko_bot.parser import (
    classify_puck,
    parse_dates,
    parse_page,
    parse_time_ranges,
    parse_weekdays,
)

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2025, 12, 22)  # a Monday, mid-season


@pytest.fixture(scope="module")
def tables_result():
    html = (FIXTURES / "schedule_tables.html").read_text(encoding="utf-8")
    return parse_page(html, today=TODAY)


# --- low level helpers ------------------------------------------------------


def as_tuples(text):
    return [(r.start, r.end) for r in parse_time_ranges(text)]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("12.00–13.20", [(time(12, 0), time(13, 20))]),
        ("kl. 17:00 - 18:00", [(time(17, 0), time(18, 0))]),
        ("18-19.30", [(time(18, 0), time(19, 30))]),
        ("10.00–11.00 och 11.15–12.15", [(time(10, 0), time(11, 0)), (time(11, 15), time(12, 15))]),
        ("kl 18.00", [(time(18, 0), None)]),
        ("18:00-20.00", [(time(18, 0), time(20, 0))]),  # mixed separators, as on the page
        ("Måndag", []),
    ],
)
def test_parse_time_ranges(text, expected):
    assert as_tuples(text) == expected


def test_dates_are_not_read_as_times():
    assert as_tuples("Lördag 26/12") == []
    assert as_tuples("Lördag 26/12 kl. 13.00–14.30") == [(time(13, 0), time(14, 30))]


def test_asterisk_is_captured_per_time_range():
    ranges = parse_time_ranges("09:00-11:20* 11:30-14:30")
    assert [(r.start, r.marker) for r in ranges] == [
        (time(9, 0), "*"),
        (time(11, 30), ""),
    ]


def test_double_asterisk_is_kept_distinct():
    assert parse_time_ranges("13:00-14:00**")[0].marker == "**"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Måndag", [0]),
        ("Lördag–söndag", [5, 6]),
        ("måndag-fredag", [0, 1, 2, 3, 4]),
        ("Tisdag och torsdag", [1, 3]),
        ("Alla dagar", [0, 1, 2, 3, 4, 5, 6]),
        ("Vardagar", [0, 1, 2, 3, 4]),
        ("Helger", [5, 6]),
        ("Ingen dag här", []),
    ],
)
def test_parse_weekdays(text, expected):
    assert parse_weekdays(text) == expected


def test_parse_dates_picks_nearest_year():
    assert parse_dates("Lördag 26/12", today=TODAY) == [date(2025, 12, 26)]
    assert parse_dates("3/1", today=TODAY) == [date(2026, 1, 3)]
    assert parse_dates("2026-02-14", today=TODAY) == [date(2026, 2, 14)]
    assert parse_dates("14 februari", today=TODAY) == [date(2026, 2, 14)]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Åkning utan klubba och puck", PuckStatus.WITHOUT_PUCK),
        ("Åkning med klubba och puck", PuckStatus.WITH_PUCK),
        ("Familjeåkning utan klubba", PuckStatus.WITHOUT_PUCK),
        ("Klubbis", PuckStatus.WITH_PUCK),
        ("Allmänhetens åkning", PuckStatus.UNKNOWN),
    ],
)
def test_classify_puck(text, expected):
    assert classify_puck(text) == expected


def test_classify_puck_prefers_the_first_argument():
    assert classify_puck("med klubba", "utan klubba") == PuckStatus.WITH_PUCK
    assert classify_puck("", "utan klubba") == PuckStatus.WITHOUT_PUCK


# --- whole page ------------------------------------------------------------


def test_monday_session_from_table(tables_result):
    monday = tables_result.sessions_on(date(2025, 12, 22))
    rosenlund = [s for s in monday if "Rosenlund" in s.rink]
    assert len(rosenlund) == 1
    session = rosenlund[0]
    assert (session.start, session.end) == (time(12, 0), time(13, 20))
    assert session.puck is PuckStatus.WITHOUT_PUCK


def test_wednesday_is_with_puck(tables_result):
    wednesday = tables_result.sessions_on(date(2025, 12, 24))
    assert any(
        s.puck is PuckStatus.WITH_PUCK and s.start == time(11, 0) for s in wednesday
    ), wednesday


def test_weekday_range_expands_to_both_days(tables_result):
    saturday = tables_result.sessions_on(date(2025, 12, 27))
    sunday = tables_result.sessions_on(date(2025, 12, 28))
    assert any(s.start == time(14, 0) and "Rosenlund" in s.rink for s in saturday)
    assert any(s.start == time(14, 0) and "Rosenlund" in s.rink for s in sunday)


def test_rink_name_comes_from_heading_not_subheading(tables_result):
    tuesday = tables_result.sessions_on(date(2025, 12, 23))
    raslatt = [s for s in tuesday if "Råslätt" in s.rink]
    assert len(raslatt) == 1
    assert raslatt[0].puck is PuckStatus.WITHOUT_PUCK
    assert raslatt[0].start == time(17, 0)


def test_puck_status_from_column_headers(tables_result):
    sunday = tables_result.sessions_on(date(2025, 12, 28))
    granna = {(s.start, s.puck) for s in sunday if "Gränna" in s.rink}
    assert (time(10, 0), PuckStatus.WITHOUT_PUCK) in granna
    assert (time(11, 15), PuckStatus.WITH_PUCK) in granna


def test_explicit_date_only_matches_that_date(tables_result):
    on_boxing_day = tables_result.sessions_on(date(2025, 12, 26))
    bankeryd = [s for s in on_boxing_day if "Bankeryd" in s.rink]
    assert len(bankeryd) == 1
    assert bankeryd[0].on_date == date(2025, 12, 26)
    # The same weekday a week later must not inherit the one-off date.
    later = tables_result.sessions_on(date(2026, 1, 2))
    assert not [s for s in later if "Bankeryd" in s.rink]


def test_closed_days_are_not_reported_as_sessions(tables_result):
    text = " ".join(s.source_text for s in tables_result.sessions)
    assert "stängt" not in text.casefold()


def test_no_warnings_on_a_well_formed_page(tables_result):
    assert tables_result.warnings == []
    assert tables_result.sessions


def test_empty_page_produces_a_warning():
    result = parse_page("<html><body><main><p>Inget här.</p></main></body></html>", today=TODAY)
    assert result.sessions == []
    assert result.warnings


# --- the real page, saved 2026-09-18 ---------------------------------------

REAL_TODAY = date(2026, 9, 20)


@pytest.fixture(scope="module")
def real_result():
    html = (FIXTURES / "jonkoping_2026-09-18.html").read_text(encoding="utf-8")
    return parse_page(html, today=REAL_TODAY, page_url="https://www.jonkoping.se/")


def test_real_page_yields_every_listed_session(real_result):
    # 6 at Smedjehov, 3 in C-hallen, 6 in D-hallen; the other three rinks list none yet.
    assert len(real_result.sessions) == 15
    assert real_result.warnings == []
    assert real_result.unparsed_lines == []


def test_real_page_reads_the_asterisk_as_the_puck_marker(real_result):
    marked = [s for s in real_result.sessions if s.marker == "*"]
    assert len(marked) == 4
    assert all(s.puck is PuckStatus.WITHOUT_PUCK for s in marked)
    assert all("utan klubba och puck" in s.puck_text_sv.casefold() for s in marked)


def test_real_page_treats_unmarked_times_as_stick_and_puck(real_result):
    unmarked = [s for s in real_result.sessions if not s.marker]
    assert len(unmarked) == 11
    assert all(s.puck is PuckStatus.WITH_PUCK for s in unmarked)
    # Wording is derived from the rink's own footnote, not hard-coded.
    assert all(s.label_sv == "med klubba och puck" for s in unmarked)


def test_real_page_splits_a_cell_holding_two_times(real_result):
    wednesday = [
        s for s in real_result.sessions_on(date(2026, 9, 23)) if "D-hallen" in s.rink
    ]
    assert [(s.time_range, s.puck) for s in wednesday] == [
        ("09:00–11:20", PuckStatus.WITHOUT_PUCK),
        ("11:30–14:30", PuckStatus.WITH_PUCK),
    ]


def test_real_page_handles_a_table_without_a_header_row(real_result):
    # Husqvarna Garden D-hallen starts straight at "Tis 22/9".
    tuesday = real_result.sessions_on(date(2026, 9, 22))
    assert any("D-hallen" in s.rink and s.start == time(11, 30) for s in tuesday)


def test_real_page_dates_are_absolute_not_recurring(real_result):
    assert all(s.on_date is not None for s in real_result.sessions)
    a_week_later = real_result.sessions_on(date(2026, 9, 30))
    assert a_week_later == []


def test_real_page_rink_names_come_from_the_headings(real_result):
    rinks = {s.rink for s in real_result.sessions}
    assert any(r.startswith("Smedjehovs ishall") for r in rinks)
    assert "Husqvarna Garden, C-hallen" in rinks
    assert "Husqvarna Garden, D-hallen" in rinks
    assert not any("Parkering" in r for r in rinks)
    assert not any("Kontakt" in r or "Länkar" in r for r in rinks)


def test_bandy_legend_is_inverted_without_mentioning_pucks():
    from skridsko_bot.parser import invert_legend

    assert invert_legend("Utan klubba och boll") == "med klubba och boll"
    assert invert_legend("Friåkning utan klubba och puck") == "med klubba och puck"
    assert invert_legend("Del av banan") == ""


def test_legend_parsing_splits_multiple_markers():
    from skridsko_bot.parser import parse_legend

    legend = parse_legend(["* Utan klubba och boll ** Del av banan"])
    assert legend == {"*": "Utan klubba och boll", "**": "Del av banan"}


def test_prose_mentioning_an_asterisk_is_not_a_legend():
    from skridsko_bot.parser import parse_legend

    line = "Obs! Vissa tider är reserverade för att åka skridskor utan klubba och puck. Se tider markerade med *."
    assert parse_legend([line]) == {}


def test_pucks_forbidden_phrasing_is_understood():
    assert classify_puck("Puckar ej tillåtna på bandybanan.") is PuckStatus.WITHOUT_PUCK
