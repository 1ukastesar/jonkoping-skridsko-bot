"""Wire scraping, formatting and posting together; run once or on a daily loop."""

from __future__ import annotations

import logging
import signal
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from . import discord_sender
from .config import Config
from .formatter import build_payload, render_plaintext
from .models import ScrapeResult
from .scraper import scrape

log = logging.getLogger(__name__)

HEALTH_FILENAME = "healthy"


def target_days(config: Config, today: date) -> list[date]:
    return [today + timedelta(days=offset) for offset in range(config.lookahead_days + 1)]


def describe_config(config: Config) -> str:
    """The settings actually in effect, so a stray .env line is visible in the log."""
    parts = [
        f"url={config.url}",
        f"tz={config.timezone.key}",
        f"post_at={config.post_at:%H:%M}",
        f"lookahead_days={config.lookahead_days}",
        f"rinks={','.join(config.rink_filter) if config.rink_filter else 'all'}",
        f"post_when_empty={config.post_when_empty}",
        f"mention={'yes' if config.mention else 'no'}",
        f"webhook={'set' if config.webhook_url else 'unset'}",
    ]
    return " ".join(parts)


def touch_health(config: Config) -> None:
    path = Path(config.cache_dir) / HEALTH_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().isoformat(), encoding="utf-8")
    except OSError as exc:
        log.debug("could not write health file: %s", exc)


def run_once(
    config: Config,
    *,
    html: str | None = None,
    dry_run: bool = False,
    on_date: date | None = None,
    session: requests.Session | None = None,
) -> ScrapeResult:
    """One scrape-and-post cycle."""
    now = datetime.now(config.timezone)
    today = on_date or now.date()
    days = target_days(config, today)
    log.info("config: %s", describe_config(config))
    log.info(
        "reporting %d day(s): %s",
        len(days),
        ", ".join(day.isoformat() for day in days),
    )

    result = scrape(config, today=today, html=html, session=session)

    for warning in result.warnings:
        log.warning("%s", warning)
    if result.unparsed_lines:
        log.info("%d line(s) could not be parsed (run with --dry-run to inspect)",
                 len(result.unparsed_lines))

    has_sessions = any(result.sessions_on(day) for day in days)
    if not has_sessions and not config.post_when_empty:
        log.info("nothing scheduled for %s and SKRIDSKO_POST_WHEN_EMPTY is off; not posting",
                 today.isoformat())
        if dry_run:
            print(render_plaintext(result, days))
        return result

    payload = build_payload(result, days, mention=config.mention, now=now)

    if dry_run:
        print(render_plaintext(result, days))
        print("\n--- webhook payload ---")
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return result

    discord_sender.send(config.webhook_url, payload, timeout=config.http_timeout,
                        session=session)
    touch_health(config)
    return result


def next_run_at(config: Config, now: datetime) -> datetime:
    """Next occurrence of the configured local post time, strictly after *now*."""
    candidate = now.replace(
        hour=config.post_at.hour,
        minute=config.post_at.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def run_forever(config: Config) -> int:
    """Daily loop. Returns a process exit code."""
    stop = threading.Event()

    def _handle(signum, _frame):
        log.info("received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    http = requests.Session()
    touch_health(config)
    log.info(
        "scheduler started; posting daily at %02d:%02d %s",
        config.post_at.hour,
        config.post_at.minute,
        config.timezone.key,
    )

    while not stop.is_set():
        now = datetime.now(config.timezone)
        target = next_run_at(config, now)
        wait_seconds = (target - now).total_seconds()
        log.info("next post at %s (in %.0f min)", target.isoformat(timespec="minutes"),
                 wait_seconds / 60)

        # Wake up regularly so a DST shift or clock change is picked up.
        while not stop.is_set() and datetime.now(config.timezone) < target:
            remaining = (target - datetime.now(config.timezone)).total_seconds()
            if stop.wait(timeout=min(60.0, max(1.0, remaining))):
                break

        if stop.is_set():
            break

        try:
            run_once(config, session=http)
        except Exception:  # noqa: BLE001 - a bad day must not kill the loop
            log.exception("the daily run failed; will try again tomorrow")

    log.info("stopped")
    return 0
