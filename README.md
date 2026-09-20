# jonkoping-skridsko-bot

Scrapes the City of Jönköping's public ice-skating page once a day and posts the
day's sessions to a Discord channel via webhook, saying for each one whether
sticks and pucks are allowed (*med / utan klubba och puck*).

Source page:
<https://www.jonkoping.se/fritid-kultur--natur/idrott-motion-och-bad/skridskoakning-allmanhetens-akning>

```
⛸️ Ice skating — Wednesday 23 September
🏟️ Smedjehovs ishall, Norrahammar (ishockeyrink, inomhus)
🏒 08:00–14:00 — stick and puck allowed (med klubba och puck)
🏟️ Husqvarna Garden, D-hallen
⛸ 09:00–11:20 — no stick or puck (utan klubba och puck)
🏒 11:30–14:30 — stick and puck allowed (med klubba och puck)
```

## Quick start

```bash
cp .env.example .env
$EDITOR .env                      # paste your webhook URL
docker compose up -d --build
docker compose logs -f
```

To see what it would post without touching Discord:

```bash
docker compose run --rm skridsko-bot --dry-run
```

## Getting a webhook URL

In Discord: **Edit Channel → Integrations → Webhooks → New Webhook**, pick the
channel, then **Copy Webhook URL**. That URL is a credential — anyone holding it
can post to the channel, so it belongs in `.env` (git-ignored), not in the image.

## Configuration

All settings are environment variables; only the first is required.

| Variable | Default | Meaning |
|---|---|---|
| `SKRIDSKO_WEBHOOK_URL` | — | Discord channel webhook URL |
| `SKRIDSKO_URL` | the page above | Page to scrape |
| `SKRIDSKO_POST_AT` | `07:00` | Local time of the daily post |
| `SKRIDSKO_TZ` | `Europe/Stockholm` | Timezone for `POST_AT` and for "today" |
| `SKRIDSKO_LOOKAHEAD_DAYS` | `0` | `0` = today only, `1` = today + tomorrow, … |
| `SKRIDSKO_RINKS` | all | Comma-separated substrings; only matching rinks are reported |
| `SKRIDSKO_MENTION` | none | Prefix text, e.g. `<@&ROLE_ID>` or `@here` |
| `SKRIDSKO_POST_WHEN_EMPTY` | `true` | `false` keeps quiet on days with no sessions |
| `SKRIDSKO_CACHE_DIR` | `/var/cache/skridsko-bot` | Last-good HTML + healthcheck stamp |
| `SKRIDSKO_LOG_LEVEL` | `INFO` | `DEBUG` also logs per-fragment parsing |
| `SKRIDSKO_HTTP_TIMEOUT` | `20` | Seconds per request |
| `SKRIDSKO_HTTP_RETRIES` | `3` | Fetch attempts before falling back to cache |
| `SKRIDSKO_USER_AGENT` | project string | Put a contact address here; it is polite and it keeps you unblocked |

## CLI

```
python -m skridsko_bot                      # daily loop (what the container runs)
python -m skridsko_bot --once               # one post, then exit (for cron/systemd timers)
python -m skridsko_bot --dry-run            # print the payload, do not post
python -m skridsko_bot --html-file page.html --dry-run
python -m skridsko_bot --dry-run --date 2025-12-28
```

`--dry-run` needs no webhook URL, so it is the fastest way to check parsing.

## How the page encodes the puck rule

This is the part worth knowing, because it is not obvious from looking at the
page. Each rink gets a `Dag och datum | Tid | Anmärkning` table, and **the puck
rule is carried by an asterisk on the time**, explained by a footnote under
that rink's table:

| Rink | Footnote |
|---|---|
| Smedjehov, Husqvarna Garden B/C/D | `* Utan klubba och puck` |
| Råslätts IP (bandy) | `* Utan klubba och boll ** Del av banan` |
| Vapenvallen | `* Friåkning utan klubba och puck ** Delad bana` |

The page states the convention once at the top: *"Vissa tider är reserverade
för att åka skridskor utan klubba och puck. Se tider markerade med *."* So:

- `09:00-11:20*` → **no stick or puck**
- `11:30-14:30` (unmarked) → **stick and puck allowed**
- `**` is a separate remark (*part of the rink* / *shared rink*), not a puck rule

The parser reads those footnotes instead of hard-coding the wording, and derives
the unmarked wording by inverting the footnote — so the bandy rink is described
as *med klubba och boll*, never in terms of pucks. Where a rink has no legend at
all, the bot says "puck status not stated on the site" rather than guessing.

## How the parsing works

`skridsko_bot/parser.py` avoids fixed CSS selectors, because the municipality's
CMS will change eventually. It splits the page into sections by heading, then:

1. **Tables.** Column roles are inferred from content, not position: which
   column holds times, which holds the day, which holds remarks. A header row is
   recognised by what it contains — necessary here, because the live page marks
   *every* cell as `<th>` and the D-hallen table has no header row at all. A cell
   holding two times (`09:00-11:20* 11:30-14:30`) becomes two sessions with
   their own markers. A layout with several time columns
   (`Dag | Utan klubba | Med klubba`) takes the puck rule from the column header.
2. **Headings plus the paragraphs and list items under them**, for rinks listed
   as prose rather than a table. Headings such as *Utan klubba och puck* are
   recognised as session types, not venues; *Parkering …*, *Länkar* and
   *Kontakta …* are excluded from rink names.
3. Anything left over is reported, not dropped, in the `Unparsed lines` section
   of `--dry-run` output and counted in the logs.

Times, weekdays and dates are read tolerantly: `18:00-20.00` (mixed separators,
as on the live page), `kl. 17:00 - 18:00`, `Lör 19/9`, `Mån 21/9`,
`måndag–fredag`, `vardagar`, `helger`, `2026-02-14`, `14 februari`. Dates are
masked out before time matching so `26/12` is never read as a clock time, and
`Puckar ej tillåtna` is understood as well as `utan klubba`. Rows saying *stängt*
or *inställd* are skipped rather than posted as sessions, and standing advisories
that mention pucks or weather are attached to the rink as a small note.

### Known limitations

- **The page publishes about a week at a time,** as explicit dates
  (`Lör 19/9`, `Mån 21/9`). Outside that window there is nothing to report, so
  on a day past the last listed date the bot says so and names the last date it
  found — that distinguishes "no skating today" from "nobody updated the page".
  Set `SKRIDSKO_POST_WHEN_EMPTY=false` to stay silent on those days instead.
- **Recurring rows have no season.** If a rink ever switches to
  *Måndag 12.00–13.20* with no date, that is treated as every Monday, and a
  stale table would keep being read. Dated rows match that date alone.
- **Weather-dependent rinks.** Råslätts IP and Vapenvallen can close at short
  notice; the page says so and the bot repeats that note, but it cannot know
  about a same-day cancellation.
- **The site blocks some automated clients.** If you get 403s, set
  `SKRIDSKO_USER_AGENT` to something identifying with a contact address.

## When the page layout changes

Symptom: the daily post says "nothing listed" while the page clearly shows times.

```bash
curl -sL "$SKRIDSKO_URL" -o /tmp/page.html
python -m skridsko_bot --dry-run --html-file /tmp/page.html --log-level DEBUG
```

The `Unparsed lines` block shows what the parser saw but could not interpret.
Drop that HTML into `tests/fixtures/`, add a test to `tests/test_parser.py`
asserting the sessions you expect, and adjust the parser until it passes.

## Tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests -q
```

66 tests, none touching the network. `tests/fixtures/jonkoping_2026-09-18.html`
is the real page as saved on 2026-09-18; the suite asserts that all 15 sessions
it lists come out with the right rink, date, times and puck marker — including
the two-times-in-one-cell rows, the header-less D-hallen table and the four
asterisked stick-free slots. A second synthetic fixture covers layouts the live
page does not currently use (weekday ranges, separate puck columns, prose
listings), so a future redesign has something to land on.

## Running without Docker

```bash
python3 -m venv /opt/skridsko-bot/venv
/opt/skridsko-bot/venv/bin/pip install -r requirements.txt
```

Then either run the loop as a service:

```ini
# /etc/systemd/system/skridsko-bot.service
[Unit]
Description=Jönköping ice skating Discord bot
After=network-online.target

[Service]
Type=simple
User=skridsko
EnvironmentFile=/etc/default/skridsko-bot
WorkingDirectory=/opt/skridsko-bot
ExecStart=/opt/skridsko-bot/venv/bin/python -m skridsko_bot
Restart=on-failure
RestartSec=30
StateDirectory=skridsko-bot
CacheDirectory=skridsko-bot
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

…or drop the internal scheduler and use a timer / cron entry calling
`python -m skridsko_bot --once`.

## Courtesy

One GET per day against a municipal website is negligible load, and the bot
caches the last good copy so an outage does not turn into a retry storm. Keep it
at one request a day, and keep a contact address in the user agent.
