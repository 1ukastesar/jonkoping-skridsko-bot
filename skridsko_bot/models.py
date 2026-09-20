"""Domain objects shared by the parser, formatter and sender."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum


class PuckStatus(str, Enum):
    """Whether sticks (and pucks or a bandy ball) are allowed during a session.

    The page marks stick-free sessions with an asterisk and explains the marker
    in a footnote per rink; everything unmarked is ordinary skating where sticks
    and pucks are allowed.
    """

    WITH_PUCK = "with_puck"
    WITHOUT_PUCK = "without_puck"
    UNKNOWN = "unknown"

    @property
    def label_en(self) -> str:
        return {
            PuckStatus.WITH_PUCK: "stick and puck allowed",
            PuckStatus.WITHOUT_PUCK: "no stick or puck",
            PuckStatus.UNKNOWN: "puck status not stated",
        }[self]

    @property
    def default_label_sv(self) -> str:
        return {
            PuckStatus.WITH_PUCK: "klubba och puck tillåtet",
            PuckStatus.WITHOUT_PUCK: "utan klubba och puck",
            PuckStatus.UNKNOWN: "ej angivet",
        }[self]

    @property
    def emoji(self) -> str:
        return {
            PuckStatus.WITH_PUCK: "\N{ICE HOCKEY STICK AND PUCK}",
            PuckStatus.WITHOUT_PUCK: "\N{ICE SKATE}",
            PuckStatus.UNKNOWN: "\N{WHITE QUESTION MARK ORNAMENT}",
        }[self]


@dataclass(frozen=True)
class Session:
    """One block of public skating at one rink."""

    rink: str
    start: time
    end: time | None = None
    puck: PuckStatus = PuckStatus.UNKNOWN
    weekday: int | None = None  # 0 = Monday, matching date.weekday()
    on_date: date | None = None  # set when the page names an explicit date
    marker: str = ""  # the asterisk(s) the page put on this time, if any
    puck_text_sv: str = ""  # the page's own wording, e.g. "Utan klubba och boll"
    note: str = ""  # remark column / extra marker meaning, e.g. "Del av banan"
    source_text: str = ""

    def occurs_on(self, day: date) -> bool:
        if self.on_date is not None:
            return self.on_date == day
        if self.weekday is not None:
            return self.weekday == day.weekday()
        return False

    @property
    def time_range(self) -> str:
        if self.end is None:
            return f"{self.start:%H:%M}"
        return f"{self.start:%H:%M}\N{EN DASH}{self.end:%H:%M}"

    @property
    def label_sv(self) -> str:
        return self.puck_text_sv or self.puck.default_label_sv

    def sort_key(self) -> tuple:
        return (self.start, self.end or self.start, self.rink.casefold())


@dataclass
class ScrapeResult:
    """Everything one scrape produced, including diagnostics."""

    sessions: list[Session] = field(default_factory=list)
    fetched_from_cache: bool = False
    page_url: str = ""
    warnings: list[str] = field(default_factory=list)
    unparsed_lines: list[str] = field(default_factory=list)
    rink_notes: dict[str, list[str]] = field(default_factory=dict)

    def sessions_on(self, day: date) -> list[Session]:
        found = [s for s in self.sessions if s.occurs_on(day)]
        found.sort(key=Session.sort_key)
        return found

    def notes_for(self, rink: str) -> list[str]:
        return self.rink_notes.get(rink, [])

    @property
    def last_listed_date(self) -> date | None:
        """The furthest date the page actually lists.

        The municipality publishes roughly a week of explicit dates at a time,
        so this is how the bot can tell "no skating today" apart from "nobody
        has updated the page".
        """
        dated = [s.on_date for s in self.sessions if s.on_date is not None]
        return max(dated) if dated else None
