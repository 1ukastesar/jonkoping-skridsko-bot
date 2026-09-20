"""Command line entry point: python -m skridsko_bot"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from .app import run_forever, run_once
from .config import Config, ConfigError


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skridsko_bot",
        description="Post Jönköping public ice-skating times to a Discord webhook.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="scrape and post a single time, then exit (use with cron/systemd timers)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be posted instead of calling Discord",
    )
    parser.add_argument(
        "--html-file",
        type=Path,
        help="parse a saved copy of the page instead of fetching it (offline testing)",
    )
    parser.add_argument(
        "--date",
        dest="for_date",
        help="pretend today is this ISO date (YYYY-MM-DD), for testing",
    )
    parser.add_argument(
        "--log-level",
        help="override SKRIDSKO_LOG_LEVEL (DEBUG, INFO, WARNING, ERROR)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    needs_webhook = not args.dry_run
    try:
        config = Config.from_env(require_webhook=needs_webhook)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, (args.log_level or config.log_level), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    html = None
    if args.html_file:
        try:
            html = args.html_file.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"could not read {args.html_file}: {exc}", file=sys.stderr)
            return 2

    on_date: date | None = None
    if args.for_date:
        try:
            on_date = datetime.strptime(args.for_date, "%Y-%m-%d").date()
        except ValueError:
            print("--date must be YYYY-MM-DD", file=sys.stderr)
            return 2

    if args.once or args.dry_run or html is not None:
        try:
            run_once(config, html=html, dry_run=args.dry_run, on_date=on_date)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("skridsko_bot").exception("run failed")
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    return run_forever(config)


if __name__ == "__main__":
    raise SystemExit(main())
