"""Post a payload to a Discord webhook, honouring 429 rate limits."""

from __future__ import annotations

import logging
import time as time_mod

import requests

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


class WebhookError(RuntimeError):
    """Discord rejected the message."""


def send(
    webhook_url: str,
    payload: dict,
    *,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> None:
    http = session or requests.Session()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = http.post(webhook_url, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == MAX_ATTEMPTS:
                raise WebhookError(f"webhook POST failed: {exc}") from exc
            wait = min(2**attempt, 30)
            log.warning("webhook POST error (%s), retrying in %ss", exc, wait)
            time_mod.sleep(wait)
            continue

        if response.status_code == 429:
            retry_after = 5.0
            try:
                retry_after = float(response.json().get("retry_after", retry_after))
            except (ValueError, AttributeError):
                pass
            log.warning("rate limited by Discord, waiting %.1fs", retry_after)
            time_mod.sleep(min(retry_after, 60))
            continue

        if response.status_code in {500, 502, 503, 504}:
            if attempt == MAX_ATTEMPTS:
                raise WebhookError(f"Discord returned {response.status_code}")
            wait = min(2**attempt, 30)
            log.warning("Discord %s, retrying in %ss", response.status_code, wait)
            time_mod.sleep(wait)
            continue

        if not response.ok:
            raise WebhookError(
                f"Discord returned {response.status_code}: {response.text[:500]}"
            )

        log.info("posted schedule to Discord (%s)", response.status_code)
        return

    raise WebhookError("gave up posting to Discord after repeated rate limits")
