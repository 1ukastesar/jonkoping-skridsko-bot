"""Parse the jonkoping.se public-skating page into :class:`Session` objects.

How the real page encodes the puck rule
---------------------------------------
Each rink gets a ``Dag och datum | Tid | Anmärkning`` table. Times that are
reserved for stick-free skating carry an asterisk, and a footnote under the
table says what the asterisk means for that rink:

    * Utan klubba och puck          (Smedjehov, Husqvarna Garden)
    * Utan klubba och boll          (Råslätts bandybana)
    * Friåkning utan klubba och puck ** Delad bana   (Vapenvallen)

The page states the convention once at the top: *"Vissa tider är reserverade
för att åka skridskor utan klubba och puck. Se tider markerade med *."* So an
unmarked time is ordinary skating where sticks and pucks are allowed, and the
parser reads the footnotes rather than hard-coding any wording.

Parsing is layered so a CMS change degrades into a diagnostic rather than an
empty message: tables with inferred column roles first, then headings plus the
paragraphs and list items under them, and whatever is left over is reported in
``ScrapeResult.unparsed_lines``.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, time

from bs4 import BeautifulSoup, Tag

from .models import PuckStatus, ScrapeResult, Session

log = logging.getLogger(__name__)

WEEKDAYS: dict[str, int] = {
    "mandag": 0,
    "man": 0,
    "tisdag": 1,
    "tis": 1,
    "onsdag": 2,
    "ons": 2,
    "torsdag": 3,
    "tors": 3,
    "tor": 3,
    "fredag": 4,
    "fre": 4,
    "lordag": 5,
    "lor": 5,
    "sondag": 6,
    "son": 6,
}

MONTHS: dict[str, int] = {
    "januari": 1,
    "februari": 2,
    "mars": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "augusti": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}

# Table column labels, so a header row is recognised without relying on <th>
# (the live page marks every cell as <th>, and one table has no header at all).
HEADER_LABELS = {
    "dag",
    "datum",
    "dag och datum",
    "veckodag",
    "tid",
    "tider",
    "klockan",
    "anmarkning",
    "anmarkningar",
    "typ",
    "typ av akning",
    "kommentar",
    "ovrigt",
}

# Headings that describe a kind of session rather than a venue.
GENERIC_HEADINGS = {
    "allmanhetens akning",
    "allmanhetens skridskoakning",
    "skridskoakning",
    "skridskoakning, allmanhetens akning",
    "skridskoakning allmanhetens akning",
    "oppettider",
    "tider",
    "tider och priser",
    "priser",
    "avgifter",
    "med klubba",
    "utan klubba",
    "med klubba och puck",
    "utan klubba och puck",
    "isbanor",
    "isbanor spolade av andra an jonkopings kommun",
    "inomhus",
    "utomhus",
    "lankar",
    "kontakt",
    "kontakta jonkopings kommun",
    "hitta direkt",
    "sociala medier",
    "sidinnehall",
    "relaterad information",
    "hyra is",
    "regler",
    "ordningsregler",
    "bra att veta",
}

# Headings that are about something else at the venue, not a rink.
NON_RINK_PREFIXES = ("parkering", "entre", "entré", "hitta hit", "vagbeskrivning")

RINK_HINTS = (
    "hall",
    "arena",
    "isbana",
    "ishall",
    "bandybana",
    "rink",
    "sporthall",
    "garden",
    "vallen",
    "stadion",
    " ip",
    "center",
    "centrum",
)

_T = r"(\d{1,2})(?:[.:](\d{2}))?"
TIME_RANGE_RE = re.compile(rf"\b{_T}\s*(?:-|–|—|till)\s*{_T}\b")
EXPLICIT_TIME_RE = re.compile(r"\b(\d{1,2})[.:](\d{2})\b")
KL_TIME_RE = re.compile(rf"\bkl\.?\s*{_T}\b")
MARKER_AFTER_RE = re.compile(r"\s{0,2}(\*{1,3})")

DATE_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:\s*[-–]?\s*(\d{4}))?\b")
DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
DATE_WORD_RE = re.compile(
    r"\b(\d{1,2})\s*(januari|februari|mars|april|maj|juni|juli|augusti|september|"
    r"oktober|november|december)\b"
)

PUCK_NOUN = r"(klubba|klubbor|klubban|puck|puckar|pucken|boll|bollar|bollen)"
WITHOUT_PUCK_RE = re.compile(rf"\b(utan|ingen|inga|forbjudet\s+med)\b[^.;|]{{0,30}}?\b{PUCK_NOUN}\b")
PUCK_FORBIDDEN_RE = re.compile(rf"\b{PUCK_NOUN}\b[^.;|]{{0,20}}?\b(ej|inte|icke)\s+tillat")
WITH_PUCK_RE = re.compile(rf"\b(med|klubbis)\b[^.;|]{{0,30}}?\b{PUCK_NOUN}\b")
PUCK_WORD_RE = re.compile(rf"\b{PUCK_NOUN}\b")

LEGEND_LINE_RE = re.compile(r"^\s*\*")
LEGEND_ENTRY_RE = re.compile(r"(\*{1,3})\s*([^*]{2,80}?)\s*(?=\*{1,3}|$)")
MARKER_CONVENTION_RE = re.compile(r"markerade?\s+med\s+\*")

EVERY_DAY_RE = re.compile(r"\b(alla\s+dagar|varje\s+dag|dagligen|alla\s+veckodagar)\b")
WEEKDAYS_WORD_RE = re.compile(r"\b(vardagar|veckodagar)\b")
WEEKEND_WORD_RE = re.compile(r"\b(helg|helger|helgdagar|veckoslut)\b")

CANCELLED_RE = re.compile(r"\b(stangt|installd|installt|installda|ingen\s+akning|ej\s+akning)\b")

SCHEDULE_HINT_RE = re.compile(
    r"\b(mandag|tisdag|onsdag|torsdag|fredag|lordag|sondag|man|tis|ons|tors|tor|fre|lor|son|"
    r"kl|akning|klubba|puck|boll|vardagar|helg)\b"
)

TIME_LIKE_RE = re.compile(r"\d")


def fold(text: str) -> str:
    """Lowercase and strip diacritics so 'Måndag' and 'mandag' compare equal."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped).strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _mask_dates(folded: str) -> str:
    """Blank out date-looking tokens, keeping length so offsets still line up."""
    masked = DATE_ISO_RE.sub(lambda m: " " * len(m.group(0)), folded)
    masked = DATE_SLASH_RE.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def _to_time(hour: str, minute: str | None) -> time | None:
    h, m = int(hour), int(minute or 0)
    if 0 <= h <= 23 and 0 <= m <= 59:
        return time(hour=h, minute=m)
    if h == 24 and m == 0:
        return time(hour=23, minute=59)
    return None


@dataclass(frozen=True)
class TimeRange:
    start: time
    end: time | None
    marker: str = ""  # asterisks the page attached to this specific time


def parse_time_ranges(text: str) -> list[TimeRange]:
    """Every ``HH:MM-HH:MM`` in *text*, with any asterisk that follows it."""
    folded = fold(text)
    masked = _mask_dates(folded)
    ranges: list[TimeRange] = []

    for match in TIME_RANGE_RE.finditer(masked):
        start = _to_time(match.group(1), match.group(2))
        end = _to_time(match.group(3), match.group(4))
        if start is None:
            continue
        if end is not None and end <= start and match.group(4) is None:
            end = None  # "18-1" tail is more likely noise than an overnight session
        trailing = MARKER_AFTER_RE.match(folded, match.end())
        ranges.append(TimeRange(start, end, trailing.group(1) if trailing else ""))

    if ranges:
        return ranges

    # No range found: accept a single stated start time.
    singles: list[TimeRange] = []
    for pattern in (KL_TIME_RE, EXPLICIT_TIME_RE):
        for match in pattern.finditer(masked):
            groups = match.groups()
            start = _to_time(groups[0], groups[1] if len(groups) > 1 else None)
            if start is None:
                continue
            trailing = MARKER_AFTER_RE.match(folded, match.end())
            singles.append(TimeRange(start, None, trailing.group(1) if trailing else ""))
        if singles:
            break
    return singles


def parse_weekdays(text: str) -> list[int]:
    """Weekday indices named in *text*, expanding ranges like 'måndag-fredag'."""
    folded = fold(text)
    if EVERY_DAY_RE.search(folded):
        return list(range(7))

    found: set[int] = set()
    if WEEKDAYS_WORD_RE.search(folded):
        found.update(range(5))
    if WEEKEND_WORD_RE.search(folded):
        found.update({5, 6})

    names = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
    token_re = re.compile(rf"\b({names})\b")
    tokens = [(m.start(), WEEKDAYS[m.group(1)], m.group(0)) for m in token_re.finditer(folded)]

    consumed: set[int] = set()
    for i in range(len(tokens) - 1):
        pos_a, day_a, word_a = tokens[i]
        pos_b, day_b, _ = tokens[i + 1]
        between = folded[pos_a + len(word_a) : pos_b]
        if re.fullmatch(r"\s*(?:-|–|—|till)\s*", between):
            found.update((day_a + step) % 7 for step in range((day_b - day_a) % 7 + 1))
            consumed.update({i, i + 1})

    for i, (_, day, _) in enumerate(tokens):
        if i not in consumed:
            found.add(day)

    return sorted(found)


def parse_dates(text: str, *, today: date) -> list[date]:
    """Explicit calendar dates in *text*, resolving a missing year sensibly."""
    folded = fold(text)
    out: list[date] = []

    for match in DATE_ISO_RE.finditer(folded):
        try:
            out.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue

    for match in DATE_SLASH_RE.finditer(folded):
        out.extend(
            _resolve(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)) if match.group(3) else None,
                today,
            )
        )

    for match in DATE_WORD_RE.finditer(folded):
        out.extend(_resolve(int(match.group(1)), MONTHS[match.group(2)], None, today))

    seen: set[date] = set()
    return [d for d in out if not (d in seen or seen.add(d))]


def _resolve(day: int, month: int, year: int | None, today: date) -> list[date]:
    candidates = [year] if year else [today.year, today.year + 1, today.year - 1]
    resolved: list[date] = []
    for candidate_year in candidates:
        try:
            resolved.append(date(candidate_year, month, day))
        except (ValueError, TypeError):
            continue
    if not resolved:
        return []
    if year:
        return resolved[:1]
    # The skating season spans a new year, so take the reading closest to today.
    resolved.sort(key=lambda d: abs((d - today).days))
    return resolved[:1]


def classify_puck(*texts: str) -> PuckStatus:
    """First text that states a rule wins; earlier arguments are more specific."""
    for text in texts:
        if not text:
            continue
        folded = fold(text)
        if PUCK_FORBIDDEN_RE.search(folded):
            return PuckStatus.WITHOUT_PUCK
        without = WITHOUT_PUCK_RE.search(folded)
        with_puck = WITH_PUCK_RE.search(folded)
        if without and with_puck:
            # "utan klubba och puck" also satisfies the 'med ... puck' pattern.
            return (
                PuckStatus.WITHOUT_PUCK
                if without.start() <= with_puck.start()
                else PuckStatus.WITH_PUCK
            )
        if without:
            return PuckStatus.WITHOUT_PUCK
        if with_puck:
            return PuckStatus.WITH_PUCK
        if "klubbis" in folded:
            return PuckStatus.WITH_PUCK
    return PuckStatus.UNKNOWN


def parse_legend(lines: list[str]) -> dict[str, str]:
    """Map asterisk markers to the footnote text that explains them."""
    legend: dict[str, str] = {}
    for line in lines:
        if not LEGEND_LINE_RE.match(line):
            continue
        for match in LEGEND_ENTRY_RE.finditer(line):
            marker, meaning = match.group(1), _clean(match.group(2))
            if meaning and marker not in legend:
                legend[marker] = meaning
    return legend


INVERT_LEGEND_RE = re.compile(r"\butan\s+(.+)$", re.IGNORECASE)


def invert_legend(legend_text: str) -> str:
    """Turn the marker's meaning into what an *unmarked* time allows.

    ``"Utan klubba och puck"`` -> ``"med klubba och puck"``, and
    ``"Friåkning utan klubba och boll"`` -> ``"med klubba och boll"``, so a bandy
    rink is never described in terms of pucks.
    """
    match = INVERT_LEGEND_RE.search(legend_text)
    if not match:
        return ""
    return f"med {match.group(1).strip().rstrip('.')}".lower()


def _resolve_puck(
    marker: str,
    legend: dict[str, str],
    convention: bool,
    *fallback_texts: str,
) -> tuple[PuckStatus, str, str]:
    """Return (status, the page's own Swedish wording, extra note)."""
    if marker:
        meaning = legend.get(marker, "")
        status = classify_puck(meaning) if meaning else PuckStatus.UNKNOWN
        if status is not PuckStatus.UNKNOWN:
            return status, meaning, ""
        # A marker we cannot read: a single star means stick-free page-wide,
        # anything else (e.g. "** Delad bana") is just a remark.
        if marker == "*":
            return PuckStatus.WITHOUT_PUCK, meaning, ""
        return (PuckStatus.WITH_PUCK if convention else PuckStatus.UNKNOWN), "", meaning

    status = classify_puck(*fallback_texts)
    if status is not PuckStatus.UNKNOWN:
        return status, "", ""
    if convention:
        # Unmarked time under an asterisk convention: the opposite of the footnote.
        return PuckStatus.WITH_PUCK, invert_legend(legend.get("*", "")), ""
    return PuckStatus.UNKNOWN, "", ""


def _heading_is_rink(heading: str) -> bool:
    folded = fold(heading)
    if not folded or folded in GENERIC_HEADINGS:
        return False
    if folded.startswith(NON_RINK_PREFIXES):
        return False
    if PUCK_WORD_RE.search(folded) or parse_weekdays(folded):
        return False
    return len(folded) <= 80


def _pick_rink(headings: list[str], caption: str = "", fallback: str = "Public skating") -> str:
    if caption and _heading_is_rink(caption):
        return caption
    for heading in reversed(headings):
        if _heading_is_rink(heading) and any(h in fold(heading) for h in RINK_HINTS):
            return heading
    for heading in reversed(headings):
        if _heading_is_rink(heading):
            return heading
    return caption or fallback


@dataclass
class _Section:
    """A heading and everything under it, up to the next heading."""

    headings: list[str] = field(default_factory=list)
    level: int = 0
    tables: list[Tag] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " | ".join(self.lines)


def _main_content(soup: BeautifulSoup) -> Tag:
    for selector in ("main", "[role=main]", "article", "#content", ".main-content", ".content"):
        node = soup.select_one(selector)
        if node is not None:
            return node
    return soup.body or soup


def _split_sections(root: Tag) -> list[_Section]:
    sections: list[_Section] = [_Section()]
    headings: list[str] = []
    seen_tables: set[int] = set()

    for node in root.find_all(["h1", "h2", "h3", "h4", "h5", "table", "p", "li", "dt", "dd"]):
        name = node.name

        if name in {"h1", "h2", "h3", "h4", "h5"}:
            level = int(name[1])
            headings = headings[: level - 1]
            while len(headings) < level - 1:
                headings.append("")
            headings.append(_clean(node.get_text(" ")))
            sections.append(_Section(headings=list(headings), level=level))
            continue

        section = sections[-1]

        if name == "table":
            if id(node) not in seen_tables:
                seen_tables.add(id(node))
                section.tables.append(node)
            continue

        if node.find_parent("table") is not None:
            continue  # cell contents are read by the table pass
        if node.find(["table", "p", "li"]) is not None:
            continue  # handled when we reach the nested element itself

        for line in (_clean(part) for part in node.get_text("\n").split("\n")):
            if line and line not in section.lines:
                section.lines.append(line)

    return sections


def _has_date(text: str) -> bool:
    folded = fold(text)
    return bool(
        DATE_SLASH_RE.search(folded) or DATE_ISO_RE.search(folded) or DATE_WORD_RE.search(folded)
    )


def _is_header_row(cells: list[str], other_rows: list[list[str]] | None = None) -> bool:
    """A first row is a header when it carries labels rather than schedule data.

    The live page marks every cell as ``<th>`` and one table has no header row at
    all, so this goes by content: no times, no dates, and either recognised
    labels (``Dag och datum``, ``Tid``, ``Anmärkning``) or a table whose later
    rows do hold times — which also covers labels like ``Utan klubba``.
    """
    filled = [c for c in cells if c]
    if not filled:
        return False
    if any(parse_time_ranges(c) for c in filled) or any(_has_date(c) for c in filled):
        return False
    if all(fold(c) in HEADER_LABELS for c in filled):
        return True
    return any(parse_time_ranges(cell) for row in (other_rows or []) for cell in row)


def _column_roles(
    header: list[str], rows: list[list[str]]
) -> tuple[list[int], int | None, list[int]]:
    """Work out which columns hold times, the day, and remarks."""
    width = max((len(row) for row in rows), default=0)
    time_counts = [0] * width
    day_counts = [0] * width

    for row in rows:
        for index, cell in enumerate(row):
            if not cell:
                continue
            if parse_time_ranges(cell):
                time_counts[index] += 1
            elif parse_weekdays(cell) or TIME_LIKE_RE.search(cell):
                day_counts[index] += 1

    time_cols = [i for i, count in enumerate(time_counts) if count]
    if not time_cols and header:
        time_cols = [i for i, label in enumerate(header) if "tid" in fold(label)]

    day_col: int | None = None
    candidates = [i for i in range(width) if i not in time_cols]
    if candidates:
        day_col = max(candidates, key=lambda i: (day_counts[i], -i))
        if day_counts[day_col] == 0:
            day_col = candidates[0] if candidates[0] not in time_cols else None

    remark_cols = [i for i in range(width) if i not in time_cols and i != day_col]
    return time_cols, day_col, remark_cols


@dataclass
class _Candidate:
    """One schedule entry recovered from the page, before day expansion."""

    rink: str
    day_text: str
    ranges: list[TimeRange]
    puck_context: tuple[str, ...]
    note: str
    row_text: str


def _table_candidates(table: Tag, rink: str) -> list[_Candidate]:
    all_rows = [
        [_clean(cell.get_text(" ")) for cell in row.find_all(["th", "td"])]
        for row in table.find_all("tr")
    ]
    all_rows = [row for row in all_rows if any(row)]
    if not all_rows:
        return []

    header: list[str] = []
    if _is_header_row(all_rows[0], all_rows[1:]):
        header = all_rows[0]
        data_rows = all_rows[1:]
    else:
        data_rows = all_rows
    if not data_rows:
        return []

    time_cols, day_col, remark_cols = _column_roles(header, data_rows)
    caption = table.find("caption")
    caption_text = _clean(caption.get_text(" ")) if caption else ""

    candidates: list[_Candidate] = []
    for row in data_rows:
        row_text = " | ".join(cell for cell in row if cell)
        if not row_text:
            continue
        day_text = row[day_col] if day_col is not None and day_col < len(row) else row_text
        note = " ".join(row[i] for i in remark_cols if i < len(row) and row[i]).strip()

        usable = [i for i in time_cols if i < len(row) and row[i]]
        if not usable:
            continue

        for index in usable:
            ranges = parse_time_ranges(row[index])
            if not ranges:
                continue
            column_label = header[index] if index < len(header) else ""
            context: tuple[str, ...]
            if len(time_cols) > 1:
                # A layout like "Dag | Utan klubba | Med klubba": the column says it.
                context = (column_label, note)
            else:
                context = (note, row_text, caption_text)
            candidates.append(
                _Candidate(
                    rink=rink,
                    day_text=day_text,
                    ranges=ranges,
                    puck_context=context,
                    note=note,
                    row_text=row_text,
                )
            )
    return candidates


def _line_candidates(section: _Section, rink: str) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for line in section.lines:
        if LEGEND_LINE_RE.match(line):
            continue
        ranges = parse_time_ranges(line)
        if not ranges:
            continue
        candidates.append(
            _Candidate(
                rink=rink,
                day_text=line,
                ranges=ranges,
                puck_context=(line, " | ".join(section.headings)),
                note="",
                row_text=line,
            )
        )
    return candidates


def _collect_rink_notes(section: _Section) -> list[str]:
    """Standing advisories for a rink: puck rules and weather caveats.

    Lines naming a specific date are left out - a one-off closure would
    otherwise be repeated under that rink every single day.
    """
    notes: list[str] = []
    for line in section.lines:
        if LEGEND_LINE_RE.match(line) or parse_time_ranges(line) or _has_date(line):
            continue
        folded = fold(line)
        if not PUCK_WORD_RE.search(folded) and not CANCELLED_RE.search(folded):
            continue
        if len(line) > 220:
            line = line[:217] + "…"
        if line not in notes:
            notes.append(line)
    return notes


def parse_page(html: str, *, today: date | None = None, page_url: str = "") -> ScrapeResult:
    """Turn the raw page HTML into a :class:`ScrapeResult`."""
    today = today or date.today()
    soup = BeautifulSoup(html, "lxml")
    result = ScrapeResult(page_url=page_url)

    root = _main_content(soup)
    sections = _split_sections(root)
    if not any(section.tables or section.lines for section in sections):
        result.warnings.append("No content found in the page body - the layout may have changed.")
        return result

    # Anything before the first h2 is page-level: the shared legend lives there.
    page_lines: list[str] = []
    for section in sections:
        if section.level == 2:
            break
        page_lines.extend(section.lines)
    page_legend = parse_legend(page_lines)
    page_text = " | ".join(page_lines)
    convention = bool(page_legend) or bool(MARKER_CONVENTION_RE.search(fold(page_text)))

    seen: set[tuple] = set()

    for section in sections:
        legend = {**page_legend, **parse_legend(section.lines)}
        section_convention = convention or bool(legend)

        caption_text = ""
        for table in section.tables:
            caption = table.find("caption")
            if caption:
                caption_text = _clean(caption.get_text(" "))
                break
        rink = _pick_rink(section.headings, caption_text)

        candidates = [c for table in section.tables for c in _table_candidates(table, rink)]
        table_ranges = {(c.rink, r.start, r.end) for c in candidates for r in c.ranges}
        for candidate in _line_candidates(section, rink):
            if all((candidate.rink, r.start, r.end) in table_ranges for r in candidate.ranges):
                continue  # the same times already came out of a table
            candidates.append(candidate)

        notes = _collect_rink_notes(section)
        if notes and candidates:
            result.rink_notes.setdefault(rink, []).extend(
                note for note in notes if note not in result.rink_notes.get(rink, [])
            )

        for candidate in candidates:
            folded_row = fold(candidate.row_text)
            if CANCELLED_RE.search(folded_row):
                result.unparsed_lines.append(f"(skipped, closed/cancelled) {candidate.row_text}")
                continue

            weekdays = parse_weekdays(candidate.day_text)
            dates = parse_dates(candidate.day_text, today=today)
            if not weekdays and not dates:
                weekdays = parse_weekdays(" | ".join(section.headings))
            if not weekdays and not dates:
                result.unparsed_lines.append(f"(no day found) {candidate.row_text}")
                continue

            keys: list[tuple[int | None, date | None]]
            if dates:
                keys = [(None, d) for d in dates]
            else:
                keys = [(w, None) for w in weekdays]

            for weekday, on_date in keys:
                for time_range in candidate.ranges:
                    status, puck_sv, extra_note = _resolve_puck(
                        time_range.marker,
                        legend,
                        section_convention,
                        *candidate.puck_context,
                    )
                    row_note = candidate.note
                    if row_note and classify_puck(row_note) is status:
                        row_note = ""  # it only restates the puck rule already shown
                    note = " · ".join(part for part in (row_note, extra_note) if part)
                    session = Session(
                        rink=candidate.rink,
                        start=time_range.start,
                        end=time_range.end,
                        puck=status,
                        weekday=weekday if on_date is None else on_date.weekday(),
                        on_date=on_date,
                        marker=time_range.marker,
                        puck_text_sv=puck_sv,
                        note=note,
                        source_text=candidate.row_text,
                    )
                    identity = (
                        session.rink,
                        session.start,
                        session.end,
                        session.puck,
                        session.weekday,
                        session.on_date,
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    result.sessions.append(session)

    # Report schedule-looking leftovers so a layout change is visible.
    for section in sections:
        for line in section.lines:
            if LEGEND_LINE_RE.match(line) or parse_time_ranges(line):
                continue
            folded = fold(line)
            if len(folded) < 200 and SCHEDULE_HINT_RE.search(folded) and TIME_LIKE_RE.search(line):
                if line not in result.unparsed_lines:
                    result.unparsed_lines.append(line)

    if not result.sessions:
        result.warnings.append(
            "Parsed the page but found no skating sessions - the season may not have "
            "started, or the layout changed (check the unparsed lines)."
        )
    log.debug("parsed %d sessions from %d sections", len(result.sessions), len(sections))
    return result
