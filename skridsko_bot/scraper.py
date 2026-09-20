"""Fetch the schedule page, with retries and a last-good-HTML fallback."""

from __future__ import annotations

import logging
import time as time_mod
from datetime import date
from pathlib import Path

import requests

from .config import Config
from .models import ScrapeResult
from .parser import parse_page

log = logging.getLogger(__name__)

CACHE_FILENAME = "last-good.html"


class FetchError(RuntimeError):
    """The page could not be fetched and no cached copy was usable."""


def _cache_path(config: Config) -> Path:
    return Path(config.cache_dir) / CACHE_FILENAME


def _read_cache(config: Config) -> str | None:
    path = _cache_path(config)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_cache(config: Config, html: str) -> None:
    path = _cache_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    except OSError as exc:
        log.warning("could not write HTML cache to %s: %s", path, exc)


def fetch_html(config: Config, *, session: requests.Session | None = None) -> str:
    """GET the page, retrying with backoff. Raises :class:`FetchError` on failure."""
    http = session or requests.Session()
    headers = {
        "User-Agent": config.user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    }
    last_error: Exception | None = None

    for attempt in range(1, config.http_retries + 1):
        try:
            response = http.get(config.url, headers=headers, timeout=config.http_timeout)
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            log.info("fetched %s (%d bytes)", config.url, len(response.content))
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            backoff = min(2**attempt, 30)
            log.warning("fetch attempt %d/%d failed: %s", attempt, config.http_retries, exc)
            if attempt < config.http_retries:
                time_mod.sleep(backoff)

    raise FetchError(f"could not fetch {config.url}: {last_error}")


def scrape(
    config: Config,
    *,
    today: date | None = None,
    html: str | None = None,
    session: requests.Session | None = None,
) -> ScrapeResult:
    """Fetch (or reuse supplied HTML) and parse into a :class:`ScrapeResult`."""
    from_cache = False

    if html is None:
        try:
            html = fetch_html(config, session=session)
            _write_cache(config, html)
        except FetchError as exc:
            cached = _read_cache(config)
            if cached is None:
                raise
            log.warning("using cached HTML after fetch failure: %s", exc)
            html, from_cache = cached, True

    result = parse_page(html, today=today, page_url=config.url)
    result.fetched_from_cache = from_cache
    if from_cache:
        result.warnings.append(
            "The site could not be reached; this schedule comes from the last cached copy."
        )

    if config.rink_filter:
        before = len(result.sessions)
        result.sessions = [
            s
            for s in result.sessions
            if any(wanted in s.rink.casefold() for wanted in config.rink_filter)
        ]
        log.info("rink filter kept %d of %d sessions", len(result.sessions), before)

    return result
