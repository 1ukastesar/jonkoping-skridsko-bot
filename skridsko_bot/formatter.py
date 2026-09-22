"""Render a day's sessions as a Discord webhook payload."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime

from .models import PuckStatus, ScrapeResult, Session

EMBED_COLOR = 0x5BC8F5  # pale ice blue

WEEKDAY_EN = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
WEEKDAY_SV = (
    "måndag",
    "tisdag",
    "onsdag",
    "torsdag",
    "fredag",
    "lördag",
    "söndag",
)
MONTH_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

FIELD_VALUE_LIMIT = 1024
DESCRIPTION_LIMIT = 4096
MAX_FIELDS = 24
TOTAL_EMBED_LIMIT = 5800  # Discord's ~6000 character budget for a message


def _embeds_length(embeds: list[dict]) -> int:
    total = 0
    for embed in embeds:
        total += len(embed.get("title", "")) + len(embed.get("description", ""))
        total += len(embed.get("footer", {}).get("text", ""))
        for field in embed.get("fields", []):
            total += len(field["name"]) + len(field["value"])
    return total


def human_date(day: date) -> str:
    return f"{WEEKDAY_EN[day.weekday()]} {day.day} {MONTH_EN[day.month - 1]}"


def session_line(session: Session) -> str:
    puck = session.puck
    if puck is PuckStatus.UNKNOWN:
        suffix = "puck status not stated on the site"
    else:
        suffix = f"{puck.label_en} *({session.label_sv.lower()})*"
    line = f"{puck.emoji} **{session.time_range}** — {suffix}"
    if session.note:
        line += f" · {session.note}"
    return line


def group_by_rink(sessions: list[Session]) -> "OrderedDict[str, list[Session]]":
    grouped: OrderedDict[str, list[Session]] = OrderedDict()
    for session in sorted(sessions, key=Session.sort_key):
        grouped.setdefault(session.rink, []).append(session)
    return grouped


def _truncate(lines: list[str], limit: int) -> str:
    out: list[str] = []
    length = 0
    for line in lines:
        if length + len(line) + 1 > limit - 20:
            out.append("… (truncated)")
            break
        out.append(line)
        length += len(line) + 1
    return "\n".join(out)


def _no_sessions_text(result: ScrapeResult, day: date) -> str:
    text = "No public skating (*allmänhetens åkning*) is listed for this day."
    last = result.last_listed_date
    if last is not None and last < day:
        stale_days = (day - last).days
        text += (
            f"\nThe page's schedule currently ends on {human_date(last)}"
            f" ({stale_days} day{'s' if stale_days != 1 else ''} ago), so it may simply"
            " not have been refreshed yet."
        )
    elif last is not None:
        text += f"\nOther days are listed, up to {human_date(last)} — nothing for this one."
    return text


def _day_blocks(result: ScrapeResult, day: date) -> list[str]:
    sessions = result.sessions_on(day)
    grouped = group_by_rink(sessions)
    if not sessions:
        return [_no_sessions_text(result, day)]
    blocks: list[str] = []
    for rink, items in grouped.items():
        blocks.append(f"**{rink}**")
        blocks.extend(session_line(s) for s in items)
        blocks.extend(f"-# {note}" for note in result.notes_for(rink))
    return blocks


def build_embed(result: ScrapeResult, days: list[date], *, now: datetime | None = None) -> dict:
    """One embed covering every requested day."""
    embed: dict = {
        "color": EMBED_COLOR,
        "url": result.page_url or None,
    }

    if len(days) == 1:
        day = days[0]
        embed["title"] = f"⛸️ Ice skating — {human_date(day)}"[:256]
        sessions = result.sessions_on(day)
        grouped = group_by_rink(sessions)
        if not sessions:
            embed["description"] = _no_sessions_text(result, day)
        elif len(grouped) <= MAX_FIELDS:
            embed["fields"] = [
                {
                    "name": f"🏟️ {rink}"[:256],
                    "value": _truncate(
                        [session_line(s) for s in items]
                        + [f"-# {note}" for note in result.notes_for(rink)],
                        FIELD_VALUE_LIMIT,
                    ),
                    "inline": False,
                }
                for rink, items in grouped.items()
            ]
        else:
            embed["description"] = _truncate(_day_blocks(result, day), DESCRIPTION_LIMIT)
    else:
        embed["title"] = f"⛸️ Ice skating — {human_date(days[0])} to {human_date(days[-1])}"[:256]
        blocks: list[str] = []
        for day in days:
            blocks.append(f"__**📅 {human_date(day)}**__")
            blocks.extend(_day_blocks(result, day))
            blocks.append("")
        embed["description"] = _truncate(blocks, DESCRIPTION_LIMIT)

    all_sessions = [s for day in days for s in result.sessions_on(day)]
    notes: list[str] = []
    if result.fetched_from_cache:
        notes.append("⚠️ Site unreachable — showing the last cached schedule.")
    unknown = [s for s in all_sessions if s.puck is PuckStatus.UNKNOWN]
    if unknown:
        notes.append(
            f"ℹ️ {len(unknown)} session(s) did not say whether sticks and pucks "
            "(*klubba och puck*) are allowed."
        )
    if notes:
        existing = embed.get("description", "")
        joined = "\n".join(notes)
        embed["description"] = f"{existing}\n\n{joined}".strip()[:DESCRIPTION_LIMIT]

    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    embed["footer"] = {"text": f"jonkoping.se · checked {stamp}"}
    return {k: v for k, v in embed.items() if v is not None}


def build_payload(
    result: ScrapeResult,
    days: list[date],
    *,
    mention: str = "",
    now: datetime | None = None,
) -> dict:
    """The full webhook body: an optional mention plus one embed covering all days."""
    embeds = [build_embed(result, days, now=now)] if days else []

    payload: dict = {
        "username": "Skridskobot",
        "embeds": embeds,
        "allowed_mentions": {"parse": ["roles", "users", "everyone"] if mention else []},
    }
    if mention:
        payload["content"] = mention
    return payload


def render_plaintext(result: ScrapeResult, days: list[date]) -> str:
    """Console rendering used by --dry-run."""
    out: list[str] = []
    for day in days:
        sessions = result.sessions_on(day)
        out.append(f"== {human_date(day)} ==")
        if not sessions:
            out.append("  (no sessions listed)")
        for rink, items in group_by_rink(sessions).items():
            out.append(f"  {rink}")
            for item in items:
                label = item.puck.label_en
                marker = f" [{item.marker}]" if item.marker else ""
                sv = f" ({item.label_sv})" if item.puck is not PuckStatus.UNKNOWN else ""
                note = f"  · {item.note}" if item.note else ""
                out.append(f"    {item.time_range}{marker}  {label}{sv}{note}")
            for note in result.notes_for(rink):
                out.append(f"    note: {note}")
        out.append("")
    if result.warnings:
        out.append("Warnings:")
        out.extend(f"  ! {w}" for w in result.warnings)
    if result.unparsed_lines:
        out.append(f"Unparsed lines ({len(result.unparsed_lines)}):")
        out.extend(f"  ? {line}" for line in result.unparsed_lines[:40])
    return "\n".join(out)
