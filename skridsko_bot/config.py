"""Configuration, loaded entirely from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time as dtime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_URL = (
    "https://www.jonkoping.se/fritid-kultur--natur/idrott-motion-och-bad/"
    "skridskoakning-allmanhetens-akning"
)

DEFAULT_USER_AGENT = (
    "jonkoping-skridsko-bot/1.0 (+https://github.com/; daily public-skating schedule bot)"
)


class ConfigError(RuntimeError):
    """Raised when the environment is missing or contains invalid settings."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean-ish value, got {raw!r}")


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _parse_post_at(raw: str) -> dtime:
    parts = raw.split(":")
    if len(parts) != 2:
        raise ConfigError(f"SKRIDSKO_POST_AT must look like HH:MM, got {raw!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        return dtime(hour=hour, minute=minute)
    except ValueError as exc:
        raise ConfigError(f"SKRIDSKO_POST_AT must look like HH:MM, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    webhook_url: str
    url: str = DEFAULT_URL
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Stockholm"))
    post_at: dtime = field(default_factory=lambda: dtime(hour=7, minute=0))
    lookahead_days: int = 0
    rink_filter: tuple[str, ...] = ()
    mention: str = ""
    post_when_empty: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    http_timeout: int = 20
    http_retries: int = 3
    cache_dir: str = "/var/cache/skridsko-bot"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, *, require_webhook: bool = True) -> "Config":
        webhook = _env("SKRIDSKO_WEBHOOK_URL", "") or ""
        if require_webhook and not webhook:
            raise ConfigError(
                "SKRIDSKO_WEBHOOK_URL is not set. Create a webhook in your Discord "
                "channel (Edit Channel > Integrations > Webhooks) and put its URL there."
            )
        if webhook and not webhook.startswith("https://"):
            raise ConfigError("SKRIDSKO_WEBHOOK_URL must be an https:// URL")

        tz_name = _env("SKRIDSKO_TZ", "Europe/Stockholm") or "Europe/Stockholm"
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unknown timezone {tz_name!r}") from exc

        rinks_raw = _env("SKRIDSKO_RINKS", "") or ""
        rinks = tuple(
            part.strip().casefold() for part in rinks_raw.split(",") if part.strip()
        )

        return cls(
            webhook_url=webhook,
            url=_env("SKRIDSKO_URL", DEFAULT_URL) or DEFAULT_URL,
            timezone=tz,
            post_at=_parse_post_at(_env("SKRIDSKO_POST_AT", "07:00") or "07:00"),
            lookahead_days=_env_int("SKRIDSKO_LOOKAHEAD_DAYS", 0, minimum=0),
            rink_filter=rinks,
            mention=_env("SKRIDSKO_MENTION", "") or "",
            post_when_empty=_env_bool("SKRIDSKO_POST_WHEN_EMPTY", True),
            user_agent=_env("SKRIDSKO_USER_AGENT", DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT,
            http_timeout=_env_int("SKRIDSKO_HTTP_TIMEOUT", 20, minimum=1),
            http_retries=_env_int("SKRIDSKO_HTTP_RETRIES", 3, minimum=1),
            cache_dir=_env("SKRIDSKO_CACHE_DIR", "/var/cache/skridsko-bot")
            or "/var/cache/skridsko-bot",
            log_level=(_env("SKRIDSKO_LOG_LEVEL", "INFO") or "INFO").upper(),
        )
