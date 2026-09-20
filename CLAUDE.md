# CLAUDE.md — jonkoping-skridsko-bot

Context for working on this project. Read before changing the parser.

## What this is

A Discord bot that scrapes the City of Jönköping's public ice-skating page once
a day and posts the day's sessions to a channel webhook, stating for each one
whether sticks and pucks are allowed.

- Source page: <https://www.jonkoping.se/fritid-kultur--natur/idrott-motion-och-bad/skridskoakning-allmanhetens-akning>
- Deployment: Docker Compose, long-lived container with an internal daily scheduler
- Python 3.12, three runtime deps (`requests`, `beautifulsoup4`, `lxml`)
- No bot token, no gateway, no slash commands — webhook POST only

## Layout

```
skridsko_bot/
  config.py          Config.from_env(); every setting is a SKRIDSKO_* env var
  scraper.py         HTTP fetch, retries, last-good-HTML cache fallback
  parser.py          HTML -> Session objects.  The hard part. Read the notes below.
  models.py          Session, PuckStatus, ScrapeResult
  formatter.py       Session objects -> Discord webhook payload
  discord_sender.py  POST with 429/5xx handling
  app.py             run_once() and the daily run_forever() loop
  __main__.py        CLI
tests/
  fixtures/jonkoping_2026-09-18.html   the REAL page, saved. The primary test input.
  fixtures/schedule_tables.html        synthetic, covers layouts the live page doesn't use
```

## How the page encodes the puck rule — read this first

Not obvious from looking at the page. Each rink has a
`Dag och datum | Tid | Anmärkning` table, and **the puck rule is an asterisk on
the time**, explained by a footnote *under that rink's table*:

| Rink | Footnote |
|---|---|
| Smedjehov, Husqvarna Garden B/C/D | `* Utan klubba och puck` |
| Råslätts IP (bandy) | `* Utan klubba och boll ** Del av banan` |
| Vapenvallen | `* Friåkning utan klubba och puck ** Delad bana` |

The convention is stated once at the top of the page: *"Vissa tider är
reserverade för att åka skridskor utan klubba och puck. Se tider markerade med
*."* So:

- `09:00-11:20*` → no stick or puck
- `11:30-14:30` (unmarked) → stick and puck allowed
- `**` is a separate remark (part of / shared rink), **not** a puck rule

`parser.py` reads each rink's footnote rather than matching Swedish phrases, and
derives the unmarked wording by inverting the footnote (`invert_legend`), so the
bandy rink reads *med klubba och boll* and never mentions pucks. Where a rink
has no legend, the output says "puck status not stated on the site" rather than
guessing. **Do not hard-code the wording** — the footnotes differ per rink and
the municipality edits them.

## Traps in the live markup

Each of these broke a first attempt and is now covered by a test:

1. **Every cell is `<th>`**, including data rows. Header detection goes by
   content (`_is_header_row`), never by tag.
2. **Husqvarna Garden D-hallen has no header row at all** — its first row is
   data (`Tis 22/9`). Do not assume `rows[0]` is a header.
3. **One cell can hold two time ranges**, only one of them asterisked:
   `09:00-11:20* 11:30-14:30`. Markers are captured per range, not per cell.
4. **Mixed time separators**: `18:00-20.00`.
5. **Dates must be masked before time matching**, or `26/12` parses as a clock
   time. `_mask_dates` replaces them with spaces of equal length so the marker
   lookahead offsets still line up.
6. **The page lists explicit dates (`Lör 19/9`), about a week at a time** — not
   recurring weekdays. Past the last listed date there is nothing to report, and
   the message says which date the schedule ends on, so "no skating today" is
   distinguishable from "nobody updated the page".
7. **Three rinks currently list no times** (Råslätts IP, Vapenvallen,
   B-hallen). Empty tables are normal, not a parse failure.
8. **Headings that are not rinks**: `Parkering Råslätts IP`, `Länkar`,
   `Kontakta Jönköpings kommun`. See `GENERIC_HEADINGS` / `NON_RINK_PREFIXES`.

## Discord quirks

- **Embeds sharing an identical `url` get merged into one.** This silently hid
  every day but the first when `SKRIDSKO_LOOKAHEAD_DAYS > 0`. Each day's embed
  now links to `<page>#YYYY-MM-DD`. Guarded by
  `test_each_day_gets_a_distinct_embed_url` — do not "tidy up" those fragments.
- Limits: 10 embeds and ~6000 characters per message, 25 fields per embed, 1024
  characters per field value. `build_payload` trims to fit.
- The webhook URL is a credential. It lives in `.env` (git-ignored) and is never
  logged — `describe_config` prints `webhook=set` only.

## Working on this

```bash
python -m pytest tests -q                      # 71 tests, none touch the network
python -m skridsko_bot --dry-run               # real fetch, prints payload, no post
python -m skridsko_bot --dry-run --html-file tests/fixtures/jonkoping_2026-09-18.html --date 2026-09-23
python -m skridsko_bot --once                  # one real post, then exit
docker compose up -d --build                   # the daily loop
```

Every run logs its effective config and the days it will report, at INFO. That
line is the first thing to check when the output looks wrong.

### When the page layout changes

Symptom: the post says "nothing listed" while the page shows times.

```bash
curl -sL "$SKRIDSKO_URL" -o /tmp/page.html
python -m skridsko_bot --dry-run --html-file /tmp/page.html --log-level DEBUG
```

The `Unparsed lines` block shows what the parser saw but could not interpret —
nothing is dropped silently. Save that HTML into `tests/fixtures/` with the
date in the filename, add a test asserting the sessions it should yield, then
fix the parser. Keep the old fixture and its tests; they document the layouts
seen so far.

## Conventions

- Parser changes need a fixture-backed test. "I ran it once and it looked right"
  is how the asterisk rule got missed the first time.
- Prefer content-based heuristics over CSS selectors — this is a municipal CMS
  and the markup will change again.
- Anything unparseable goes to `ScrapeResult.unparsed_lines`, never to `pass`.
- The bot makes one GET per day. Keep it that way, and keep a contact address in
  `SKRIDSKO_USER_AGENT`.
- Not a git repo yet — `git init` before the first substantial change.

## Known limitations

- A rink switching to recurring weekdays (`Måndag 12.00–13.20`, no date) would be
  treated as every Monday forever; a stale table would keep being reported.
- A line mixing both rules in prose (`12.00 utan klubba, 13.00 med klubba`) is
  resolved by whichever comes first. Table layouts are handled exactly.
- Råslätt and Vapenvallen are weather-dependent and can close at short notice;
  the bot repeats the page's standing warning but cannot know about a same-day
  cancellation.
