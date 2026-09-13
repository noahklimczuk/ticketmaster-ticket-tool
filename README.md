# ticketwatch

Keeps asking Ticketmaster whether **Sienna Spiro tickets in Toronto** are actually
buyable, and makes noise the second they are — terminal bell, desktop popup, phone
push, Slack/Discord, email, or a command of your own.

Zero dependencies. Python 3.9+. One file of config.

```
$ ticketwatch check
  BUY NOW Sienna Spiro - 2026-10-25 19:00 - History, Toronto, ON [on_sale] from 59.50-149.00 CAD
          https://www.ticketmaster.ca/event/G5vYZ9abc123
```

---

## Quick start

**1. Get a free API key** (takes about two minutes)

Go to <https://developer.ticketmaster.com/>, sign up, and copy the **Consumer Key**
from your app. The free tier allows 5,000 calls a day, which is plenty — polling
every 60 seconds uses about 2,880.

**2. Point the tool at it**

```bash
git clone https://github.com/noahklimczuk/ticketmaster-ticket-tool.git
cd ticketmaster-ticket-tool
export TICKETMASTER_API_KEY="your-consumer-key"
```

**3. Watch**

```bash
python3 -m ticketwatch watch
```

That's it. It polls every 60 seconds and stays quiet until something changes.
Leave it running in a terminal tab. `Ctrl-C` stops it.

Optional install so you can type `ticketwatch` anywhere:

```bash
pip install -e .
```

---

## What it actually checks

For every event it finds, the tool works out whether you could put tickets in a
cart *right now*, using two signals:

| Signal | What it tells us |
| --- | --- |
| `dates.status.code` + `sales.public` / `sales.presales` windows | Whether the onsale has started, is scheduled, is in presale, or has ended |
| Ticketmaster's inventory-status endpoint | `AVAILABLE`, `FEW_TICKETS_LEFT`, `SOLD_OUT`, `CANCELLED` |

When the inventory endpoint answers, it wins — an event can say "onsale" long
after the last ticket went. If your API key isn't entitled to that endpoint, the
tool notices once, says so, and falls back to the sale windows.

You get told about:

| Alert | Fires when |
| --- | --- |
| `new_event` | A Toronto date appears that wasn't there before |
| `on_sale` | The public onsale opens (including the moment a scheduled onsale time simply arrives) |
| `presale` | A presale window opens |
| `back_in_stock` | Sold out → available again (returns and released holds) |
| `low_inventory` | Ticketmaster starts reporting "few tickets left" |
| `sold_out`, `status_change`, `price_change`, `gone` | Off by default — add them to `alert_on` if you want them |

Each alert fires **once** per change. State lives in `state.json`; delete it to
start over.

---

## Commands

```bash
ticketwatch watch                 # poll forever (the default; bare `ticketwatch` does this too)
ticketwatch watch --once          # a single check
ticketwatch check                 # one check, prints a table
ticketwatch check --json          # same, machine readable (for cron/scripts)
ticketwatch resolve               # find the artist's Ticketmaster id and every upcoming date
ticketwatch setup-email you@x.com # set up email alerts and send a test message
ticketwatch test-notify           # fire a fake alert through every channel you configured
ticketwatch status                # what the monitor currently remembers
ticketwatch init                  # write a starter config.json
```

`check` exits **0** if something is buyable, **1** if not, **2** on error — handy in
a shell loop or a cron job.

### Useful flags

```bash
ticketwatch watch -i 30                       # poll every 30 seconds
ticketwatch watch --city Toronto --city Hamilton
ticketwatch watch -k "Sienna Spiro"           # any artist
ticketwatch watch --repeat-minutes 10         # keep nagging while tickets are available
ticketwatch watch --open-browser              # pop the Ticketmaster page open on an onsale
ticketwatch watch --alert-on on_sale,back_in_stock,sold_out
```

### Pin it to the exact artist

Keyword search can drift (tribute acts, support slots). Lock onto the real artist id:

```bash
$ ticketwatch resolve
Matching artists (use the id with --attraction-id for an exact watch):
  K8vZ917qxR7      Sienna Spiro
                   https://www.ticketmaster.ca/sienna-spiro-tickets/artist/3376314
...
$ ticketwatch watch --attraction-id K8vZ917qxR7
```

`resolve` also prints every upcoming date it can see, so it's the fastest way to
confirm the Toronto show exists before you start watching.

---

## Getting alerted

Configure any combination in `config.json` (or with flags / env vars).

| Channel | How | Notes |
| --- | --- | --- |
| **Terminal** | on by default | Colour + a bell on urgent alerts |
| **Desktop popup** | on by default | macOS Notification Centre, Linux `notify-send`, Windows balloon |
| **Phone push** | `--ntfy my-secret-topic` | Install the [ntfy](https://ntfy.sh) app, subscribe to the same topic. No account needed — pick a topic nobody will guess |
| **Slack / Discord** | `--webhook https://hooks.slack.com/...` | Payload shape is detected from the URL; anything else gets full JSON |
| **Email** | `ticketwatch setup-email you@example.com` | HTML mail with a one-tap **Buy on Ticketmaster** button. See below |
| **Browser** | `--open-browser` | Opens the event page when tickets go buyable |
| **Your own script** | `--command 'say "tickets"'` | Gets `TICKETWATCH_KIND`, `_TITLE`, `_BODY`, `_URL`, `_EVENT_ID`, `_VENUE`, `_CITY`, `_DATE`, `_JSON` |

Check they work before you rely on them:

```bash
ticketwatch test-notify
```

### Email alerts

One command sets it up, sends a test message, and proves it works:

```bash
ticketwatch setup-email you@example.com
```

It works out the mail server from your address (Gmail, Outlook, Yahoo, iCloud,
Fastmail, Proton Bridge), asks for the password without echoing it, and writes
everything to your **gitignored** `config.json` with `600` permissions. After
that, `ticketwatch watch` emails you on every alert.

**Gmail needs an app password**, not your normal one:

1. Turn on 2-Step Verification if it is not already on.
2. Create an app password at <https://myaccount.google.com/apppasswords>.
3. Paste it when asked — the spaces Google shows are decoration and get stripped.

Prefer to keep the password out of the file? Use the environment instead:

```bash
ticketwatch setup-email you@example.com --no-store-password
export TICKETWATCH_SMTP_PASSWORD='your-app-password'
```

Each alert arrives as an HTML mail whose subject is readable on a lock screen
(`🎟 TICKETS ON SALE: Sienna Spiro - Toronto - 2026-10-25 19:00`) and whose body
is a card with the venue, date, price, ticket limit, and a **Buy on Ticketmaster**
button straight to the event page. A plain-text version rides along for clients
that do not do HTML.

For an SMTP server we cannot guess:

```bash
ticketwatch setup-email you@example.com --smtp-host smtp.example.com --smtp-port 587
```

A belt-and-braces setup for an onsale you really care about:

```bash
ticketwatch setup-email you@example.com          # once
ticketwatch watch -i 20 --ntfy sienna-toronto-8f3k --open-browser --repeat-minutes 5
```

---

## Keeping it running

### macOS (survives logout, restarts itself)

Save as `~/Library/LaunchAgents/com.ticketwatch.plist`, editing the paths:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ticketwatch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>-m</string><string>ticketwatch</string>
    <string>watch</string>
    <string>-c</string><string>/Users/you/ticketmaster-ticket-tool/config.json</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/you/ticketmaster-ticket-tool</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/ticketwatch.log</string>
  <key>StandardErrorPath</key><string>/tmp/ticketwatch.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.ticketwatch.plist
```

### Linux (systemd user service)

`~/.config/systemd/user/ticketwatch.service`:

```ini
[Unit]
Description=Ticketmaster ticket watcher
After=network-online.target

[Service]
WorkingDirectory=%h/ticketmaster-ticket-tool
ExecStart=/usr/bin/python3 -m ticketwatch watch -c %h/ticketmaster-ticket-tool/config.json
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now ticketwatch
journalctl --user -u ticketwatch -f
```

### Cron (one check every five minutes)

```cron
*/5 * * * * cd ~/ticketmaster-ticket-tool && /usr/bin/python3 -m ticketwatch check >> ~/ticketwatch.log 2>&1
```

### GitHub Actions (no machine left on)

`.github/workflows/ticket-check.yml` is ready to go — add `TICKETMASTER_API_KEY`
and `NTFY_TOPIC` as repository secrets and enable Actions. GitHub's scheduler is
best effort and won't beat five-minute granularity, so treat it as a backstop
rather than your onsale strategy.

---

## Configuration

```bash
ticketwatch init          # writes config.json
```

Precedence: **defaults → `config.json` → environment → command line flags.**
`config.json` is gitignored, because it holds your key.

| Key | Default | Meaning |
| --- | --- | --- |
| `api_key` | — | Ticketmaster consumer key (or `TICKETMASTER_API_KEY`) |
| `keyword` | `"Sienna Spiro"` | Artist to search for |
| `cities` | `["Toronto"]` | Cities to keep; `[]` means anywhere |
| `country_code` | `"CA"` | ISO country filter |
| `attraction_id` | `null` | Exact artist id — beats keyword matching |
| `interval_seconds` | `60` | Seconds between polls |
| `jitter` | `0.15` | Randomises the interval by ±15% |
| `alert_on` | the five buyable kinds | Which changes are worth a notification |
| `repeat_alert_minutes` | `0` | Re-alert this often while tickets stay available (0 = once only) |
| `check_inventory` | `true` | Use the inventory-status endpoint as well as sale dates |
| `strict_artist_match` | `true` | Require the keyword to appear in the event or artist name |
| `use_api_city_filter` | `false` | Let Ticketmaster filter by city (see below) |
| `notifiers.email_to` | `null` | Where to email alerts (`setup-email` fills this in) |
| `notifiers.smtp_*` | inferred | Worked out from the address for the common providers |
| `state_file` | `"state.json"` | Where "already told you" is remembered |
| `log_level` / `log_file` | `INFO` / none | Logging |

Every key also works as an environment variable: `TICKETWATCH_INTERVAL_SECONDS=30`,
`TICKETWATCH_NTFY_TOPIC=my-topic`, and so on.

**Why city filtering happens locally.** Ticketmaster's own `city=Toronto` filter
drops shows at Toronto venues whose record says North York, Scarborough or
Etobicoke. So the tool asks the API for the artist only, then filters here, where
those count as Toronto. Set `use_api_city_filter: true` if you'd rather have the
API do it.

---

## Rate limits and being a good citizen

The free tier is 5,000 calls/day and 5 calls/second. Each poll is 1 call, plus 1
more if the inventory check is on.

| Interval | Calls/day | Fits the free tier? |
| --- | --- | --- |
| 60s | ~2,880 | yes |
| 30s | ~5,760 | no — use `--no-inventory` (~2,880) |
| 20s | ~8,640 | no |

`ticketwatch watch` prints its own estimate on startup and warns you if the
interval would blow the quota. On HTTP 429 it honours `Retry-After` and backs off;
on server errors it backs off exponentially up to ten minutes.

---

## What this does not do

- **It cannot buy tickets for you.** No cart automation, no checkout, no queue
  bots. It watches and tells you; the buying is yours.
- **It doesn't scrape ticketmaster.com.** Everything goes through the official
  Discovery API with your key, which is why it stays working and doesn't trip
  bot protection.
- **It doesn't cover resale** (StubHub, Vivid, SeatGeek). Ticketmaster's own
  resale inventory shows up in the inventory status; other marketplaces don't.
- **It won't beat a fast onsale on its own.** A 60-second poll tells you within a
  minute. For a sale with a known start time, be on the page beforehand — use the
  countdown that `check` prints.

---

## Troubleshooting

**"No Ticketmaster API key"** — set `TICKETMASTER_API_KEY` or put `api_key` in
`config.json`.

**"Ticketmaster rejected the API key (401)"** — you're probably using the Consumer
*Secret*. Use the Consumer **Key**. Newly created keys can take a few minutes to
activate.

**No events found** — run `ticketwatch resolve --all-cities` to see everything
Ticketmaster has for that artist. If the Toronto date is filed under a borough,
it should still match; if the artist name differs, use `--attraction-id`.

**"Inventory status unavailable for this API key"** — harmless. That endpoint isn't
enabled for every key; sale windows are used instead. Add `--no-inventory` to skip
the call entirely.

**Email says "refused the login"** — Gmail, Yahoo and iCloud reject normal
passwords over SMTP. Create an app password (Gmail:
<https://myaccount.google.com/apppasswords>, which only appears once 2-Step
Verification is on) and re-run `ticketwatch setup-email`.

**The email never arrives** — check the spam folder for the first one, then run
`ticketwatch test-notify` to see the error the server returns.

**Too many notifications** — trim `alert_on`, or set `repeat_alert_minutes` to 0.

**Nothing happens on desktop (Linux)** — install `libnotify-bin` for `notify-send`.

---

## Development

```bash
python3 -m unittest discover -s tests -t . -v
```

210 tests, no network access and no API key required — a local stand-in HTTP
server answers as Ticketmaster would, including pagination, 401s, 429s and 500s.

```
ticketwatch/
  ticketmaster.py   Discovery API client (urllib, retries, key redaction)
  events.py         Event payload -> snapshot, and the availability rules
  matcher.py        Artist and city matching, including Toronto's boroughs
  alerts.py         Change detection between polls
  state.py          Atomic JSON state file
  notify.py         Console, desktop, ntfy, webhook, email, command, browser
  monitor.py        The poll loop, backoff, and failure handling
  cli.py            Argument parsing and the subcommands
```

MIT licensed.
