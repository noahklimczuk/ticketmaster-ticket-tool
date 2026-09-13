# ticketwatch

Watches **every ticket platform at once** for the shows you want, tells you the
moment they are buyable, and points at whoever is selling them cheapest.

Built for one job: catching Sienna Spiro tickets in Toronto. It works for any
artist in any city.

- **Control panel** in your browser — start, stop, compare prices, change settings
- **One .exe** on Windows, no Python install, no terminal
- **Cheapest price across platforms**, side by side, with a direct buy link
- **New dates** as soon as *any* platform lists them, not just Ticketmaster
- Alerts by email, phone push, desktop popup, Slack/Discord, or your own script

---

## Get it running

### Windows: just the .exe

1. Go to the repo's **Actions** tab → **build-exe** → **Run workflow**.
2. Wait ~2 minutes, download the **ticketwatch-windows** artifact, unzip it.
3. Double-click `ticketwatch.exe`. The control panel opens in your browser.
4. Paste a Ticketmaster API key into Settings (free, link below) and press **Start watching**.

Keep the little black window open — closing it quits the watcher.

> Windows may warn about an unrecognised app: **More info → Run anyway**. The
> binary is unsigned because code-signing certificates cost money; the workflow
> that built it is in this repo and runs the full test suite first.

### Mac, Linux, or from source

```bash
git clone -b claude/sienna-spiro-ticket-monitor-nchk75 \
  https://github.com/noahklimczuk/ticketmaster-ticket-tool.git
cd ticketmaster-ticket-tool
export TICKETMASTER_API_KEY="your-consumer-key"
python3 -m ticketwatch gui
```

No dependencies, Python 3.9+. `packaging/build.sh` makes a standalone binary for
your machine if you want one.

### The API key

<https://developer.ticketmaster.com/> — sign up, copy the **Consumer Key** (not
the Secret). Free tier is 5,000 calls/day; polling every 60s uses about 2,900.

---

## The control panel

`ticketwatch gui` (or double-clicking the .exe) opens a page on `127.0.0.1:8765`
that only your machine can reach:

- **Available now** banner with the cheapest price and a buy button
- **Every show found**, each with a price-per-platform table, cheapest first
- **Platform chips** showing what is set up and what is failing
- **Alerts** and an activity log, updating every two seconds
- **Settings** — artist, cities, interval, API keys, email, phone push — saved
  to a `config.json` that only you can read

Useful flags:

```bash
ticketwatch gui --start           # begin watching the moment it opens
ticketwatch gui --port 9000
ticketwatch gui --host 0.0.0.0    # reach it from your phone on the same wifi
ticketwatch gui --no-open         # do not launch a browser
```

Beyond `127.0.0.1` the panel generates a secret token and puts it in the URL —
open exactly the link it prints.

---

## Platforms

| Platform | What it gives us | Setup |
| --- | --- | --- |
| **Ticketmaster** | Primary inventory, onsale times, presales, real availability, prices | Free API key — **required** |
| **SeatGeek** | Resale prices and listing counts, often below face value | Optional client ID from [seatgeek.com/account/develop](https://seatgeek.com/account/develop) |
| **Bandsintown** | New dates, often before they hit Ticketmaster, with a link to whoever sells them | Optional app id (any name you pick). On by default |
| StubHub, Vivid Seats, Gametime | One-click search links per show | No public API — see below |

**About StubHub and Vivid Seats.** Both gate their APIs behind partner approval,
and scraping them breaks their terms and loses to their bot protection. So the
tool does not pretend to price them: every show gets a **search link** for each,
pre-filled with the artist and city, one click from the alert. If you get partner
credentials, `ticketwatch/providers/` is built to take another provider — the
shape is a `fetch()` that returns `Listing` objects.

Each platform is polled independently. One being down, rate-limited, or
misconfigured never stops the others — the panel shows which one is unhappy.

---

## Cheapest tickets

Listings for the same city and night are merged into one show, wherever they came
from (a Toronto venue filed under "North York" on one platform still matches).
Each show then shows every platform's price, cheapest first, and the buy button
points at the cheapest one.

```
2026-10-25 19:00    History, Toronto, ON                    ON SALE
  SeatGeek    137 listings                     88.00 CAD    buy
  Ticketmaster                                 95.00 CAD    buy
  Also check: StubHub ↗  Vivid Seats ↗  Gametime ↗
```

Two honest caveats:

- **Prices exclude fees** unless a platform says otherwise — the `+fees` marker
  is a reminder, and resale fees can be brutal.
- **Currencies are not converted.** If one platform quotes USD and another CAD,
  the tool flags it rather than pretending to compare. No invented exchange rates.

A `cheaper` alert fires when the cheapest price drops by more than
`price_drop_percent` (5% by default) since the last look.

**On rate limits.** Ticketmaster's free tier allows 5,000 calls a day. Each check
costs it 2 calls (events plus inventory), so the 60-second default lands around
2,900/day. Drop to 30s and you will need `--no-inventory` to stay inside it;
`ticketwatch watch` prints the estimate on startup and warns you. The other
platforms are polled once per check and back off on their own 429s.

---

## New dates

`new_event` fires the first time a show appears on *any* platform — including a
date Ticketmaster has not listed yet, which is where Bandsintown earns its place.
It is on by default. Alerts fire once per change, so you get told when something
happens and left alone when it does not.

| Alert | Fires when |
| --- | --- |
| `new_event` | A date appears that we have never seen, on any platform |
| `on_sale` | The public onsale opens, including a scheduled time simply arriving |
| `presale` | A presale window opens |
| `back_in_stock` | Sold out → available again |
| `low_inventory` | Ticketmaster starts reporting "few tickets left" |
| `cheaper` | The cheapest price drops meaningfully |
| `new_platform` | The show appears on a platform it was not on before (off by default) |
| `sold_out`, `status_change`, `price_change`, `gone` | Off by default |

---

## Command line

Everything the panel does is also a command:

```bash
ticketwatch gui                   # the control panel (the .exe default)
ticketwatch watch                 # poll forever in the terminal
ticketwatch watch --once          # a single check
ticketwatch check                 # one check, prints a table; exit 0 if buyable
ticketwatch check --json          # machine readable, for scripts and cron
ticketwatch resolve               # find the artist's Ticketmaster id and all dates
ticketwatch setup-email you@x.com # set up email alerts and send a test
ticketwatch test-notify           # fire a fake alert through every channel
ticketwatch status                # what the monitor currently remembers
ticketwatch init                  # write a starter config.json
```

```bash
ticketwatch watch -i 30 --seatgeek-id YOUR_ID --open-browser --repeat-minutes 10
ticketwatch watch --city Toronto --city Hamilton -k "Sienna Spiro"
ticketwatch watch --alert-on on_sale,cheaper,new_event
```

---

## Getting alerted

| Channel | How | Notes |
| --- | --- | --- |
| **Panel** | always | Live list, alert feed, activity log |
| **Terminal** | on by default | Colour, and a bell on urgent alerts |
| **Desktop popup** | on by default | macOS, Linux (`notify-send`), Windows |
| **Email** | `ticketwatch setup-email you@example.com` | HTML mail with a one-tap **Buy** button |
| **Phone push** | `--ntfy my-secret-topic` | Install [ntfy](https://ntfy.sh), subscribe to the topic |
| **Slack / Discord** | `--webhook https://...` | Payload shape detected from the URL |
| **Browser** | `--open-browser` | Opens the cheapest seller when tickets go live |
| **Your own script** | `--command 'say tickets'` | Gets `TICKETWATCH_*` environment variables |

### Email

```bash
ticketwatch setup-email you@example.com
```

Works out the mail server from your address (Gmail, Outlook, Yahoo, iCloud,
Fastmail, Proton Bridge), asks for the password without echoing it, saves to your
gitignored `config.json` at `600`, and sends a test message.

**Gmail needs an app password**: turn on 2-Step Verification, create one at
<https://myaccount.google.com/apppasswords>, paste it (the spaces get stripped).
Prefer to keep it out of the file? `--no-store-password` and export
`TICKETWATCH_SMTP_PASSWORD` instead.

---

## Keeping it running

The panel only watches while it is open. For something that survives a reboot:

**Windows** — put a shortcut to `ticketwatch.exe` in
`shell:startup` (Win+R → `shell:startup`).

**Linux** — `~/.config/systemd/user/ticketwatch.service`:

```ini
[Unit]
Description=Ticket watcher
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
systemctl --user daemon-reload && systemctl --user enable --now ticketwatch
```

**macOS** — a launchd agent at `~/Library/LaunchAgents/com.ticketwatch.plist`
running `/usr/bin/python3 -m ticketwatch watch -c /path/to/config.json` with
`RunAtLoad` and `KeepAlive` set, then `launchctl load` it. Or simply run
`ticketwatch watch` inside `tmux` / `screen`.

**No machine left on** — `.github/workflows/ticket-check.yml` polls from GitHub
every five minutes. Add `TICKETMASTER_API_KEY` plus `ALERT_EMAIL` +
`SMTP_APP_PASSWORD` (or `NTFY_TOPIC`) as repository secrets. GitHub's scheduler
is best effort, so treat it as a backstop rather than your onsale plan.

---

## Configuration

`config.json` next to the tool, or `--config path`. The panel writes it for you.
It is gitignored, because it holds your keys.

Precedence: **defaults → config.json → environment → command line flags.**

| Key | Default | Meaning |
| --- | --- | --- |
| `api_key` | — | Ticketmaster consumer key (or `TICKETMASTER_API_KEY`) |
| `seatgeek_client_id` | `null` | Turns on SeatGeek price comparison |
| `seatgeek_currency` | `"USD"` | What SeatGeek prices are quoted in |
| `bandsintown_app_id` | `"ticketwatch"` | Any name; empty string switches it off |
| `keyword` | `"Sienna Spiro"` | Artist to watch |
| `cities` | `["Toronto"]` | `[]` means anywhere |
| `country_code` | `"CA"` | Matches both `CA` and `Canada` |
| `interval_seconds` | `60` | Seconds between checks |
| `price_drop_percent` | `5.0` | How far a price must fall to be worth an alert |
| `alert_on` | the six buyable kinds | Which changes are worth a notification |
| `repeat_alert_minutes` | `0` | Re-alert while tickets stay available |
| `attraction_id` | `null` | Exact Ticketmaster artist id — beats keyword matching |
| `state_file` | `"state.json"` | Where "already told you" is remembered |

Every key works as an environment variable too: `TICKETWATCH_INTERVAL_SECONDS=30`,
`TICKETWATCH_SEATGEEK_CLIENT_ID=...`, `TICKETWATCH_EMAIL_TO=...`.

---

## What it will not do

- **It cannot buy tickets.** No cart automation, no checkout, no queue bots. It
  watches and tells you; the clicking is yours.
- **It does not scrape.** Everything comes from official, key-authenticated APIs,
  which is why it keeps working and does not trip bot protection.
- **It does not convert currencies** or guess at fees.
- **It will not beat a fast onsale by itself.** A 60-second poll tells you inside
  a minute. For a known onsale time, be on the page early — `check` prints the
  countdown.

---

## Troubleshooting

**"No ticket platform is configured"** — add your Ticketmaster key in the panel's
Settings, or `export TICKETMASTER_API_KEY=...`.

**"Ticketmaster rejected the API key (401)"** — that is usually the Consumer
*Secret*. Use the Consumer **Key**. New keys take a few minutes to activate.

**A platform chip is red** — hover it for the reason. The other platforms carry
on regardless; clear `bandsintown_app_id` if you want to stop asking it.

**No shows found** — `ticketwatch resolve --all-cities` lists everything
Ticketmaster has for that artist. Pin it exactly with `--attraction-id`.

**Email says "refused the login"** — Gmail, Yahoo and iCloud reject normal
passwords over SMTP. Use an app password.

**Windows SmartScreen blocks the .exe** — More info → Run anyway. It is unsigned.

---

## Development

```bash
python3 -m unittest discover -s tests -t . -v
```

303 tests, no network and no API keys required: local stand-in servers answer as
Ticketmaster, SeatGeek, Bandsintown and an SMTP server would, including
pagination, 401s, 429s, 500s and rejected mail logins.

```
ticketwatch/
  providers/        One module per platform, all behind one small interface
  aggregate.py      Merges platforms into one show, works out the cheapest
  events.py         Ticketmaster payload -> snapshot, availability rules
  matcher.py        Artist, city and country matching across platforms
  alerts.py         What changed since last time, and is it worth saying
  monitor.py        The poll loop, backoff, partial-failure handling
  notify.py         Console, desktop, email, ntfy, webhook, command, browser
  gui.py + webui.py The control panel and its JSON API
  cli.py            Subcommands
packaging/          PyInstaller spec and build scripts for the executable
```

MIT licensed.
