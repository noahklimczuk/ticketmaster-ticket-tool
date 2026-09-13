"""Command line entry point for ticketwatch."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .alerts import make_alert
from .config import (
    ALL_ALERTS,
    Config,
    ConfigError,
    NotifierSettings,
    build_config,
    find_default_config,
    load_config_file,
)
from .events import EventSnapshot, now_utc
from .matcher import filter_events
from .monitor import Monitor
from .notify import EmailNotifier, build_dispatcher
from .state import StateStore
from .ticketmaster import AuthError, DiscoveryClient, TicketmasterError

LOG = logging.getLogger("ticketwatch")

EXIT_OK = 0
EXIT_NOT_AVAILABLE = 1
EXIT_ERROR = 2

SAMPLE_CONFIG = {
    "_comment": "ticketwatch config. Delete any line to fall back to the default.",
    "api_key": "PUT-YOUR-TICKETMASTER-CONSUMER-KEY-HERE",
    "keyword": "Sienna Spiro",
    "cities": ["Toronto"],
    "country_code": "CA",
    "interval_seconds": 60,
    "repeat_alert_minutes": 0,
    "alert_on": ["new_event", "on_sale", "presale", "back_in_stock", "low_inventory"],
    "state_file": "state.json",
    "notifiers": {
        "console": True,
        "desktop": True,
        "open_browser": False,
        "ntfy_topic": None,
        "webhook_url": None,
        "email_to": None,
        "smtp_password": None,
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ticketwatch",
        description="Watch Ticketmaster and shout the moment tickets are buyable.",
    )
    parser.add_argument("--version", action="version", version=f"ticketwatch {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", metavar="PATH", help="JSON config file (default: ./config.json)")
    common.add_argument("--api-key", help="Ticketmaster consumer key (or set TICKETMASTER_API_KEY)")
    common.add_argument("-k", "--keyword", help='Artist to watch (default: "Sienna Spiro")')
    common.add_argument("--city", action="append", dest="cities", metavar="CITY",
                        help="City to watch; repeat for more (default: Toronto)")
    common.add_argument("--country", dest="country_code", help="ISO country code, e.g. CA")
    common.add_argument("--attraction-id", help="Exact Ticketmaster attraction id (see: ticketwatch resolve)")
    common.add_argument("--venue-id", help="Restrict to one Ticketmaster venue id")
    common.add_argument("--state-file", help="Where to remember what we have already seen")
    common.add_argument("--no-inventory", dest="check_inventory", action="store_false", default=None,
                        help="Skip the inventory-status call (sale dates only)")
    common.add_argument("--loose-match", dest="strict_artist_match", action="store_false", default=None,
                        help="Do not require the keyword to appear in the event or artist name")
    common.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Default: INFO")
    common.add_argument("--log-file", help="Also append logs to this file")

    notify = argparse.ArgumentParser(add_help=False)
    notify.add_argument("--ntfy", dest="ntfy_topic", metavar="TOPIC",
                        help="Push to a phone via ntfy.sh using this topic")
    notify.add_argument("--ntfy-server", dest="ntfy_server", help="Self-hosted ntfy server URL")
    notify.add_argument("--webhook", dest="webhook_url", metavar="URL",
                        help="POST alerts to a Slack/Discord/any JSON webhook")
    notify.add_argument("--email-to", dest="email_to", help="Send alerts to this address (needs smtp_* settings)")
    notify.add_argument("--command", dest="command", metavar="CMD",
                        help="Shell command to run per alert (TICKETWATCH_* env vars are set)")
    notify.add_argument("--open-browser", dest="open_browser", action="store_true", default=None,
                        help="Open the Ticketmaster page when tickets become buyable")
    notify.add_argument("--no-desktop", dest="desktop", action="store_false", default=None,
                        help="Disable native desktop popups")
    notify.add_argument("-q", "--quiet", dest="console", action="store_false", default=None,
                        help="Do not print alerts to the terminal")

    sub = parser.add_subparsers(dest="command_name")

    watch = sub.add_parser("watch", parents=[common, notify], help="Poll forever until tickets appear (default)")
    watch.add_argument("-i", "--interval", dest="interval_seconds", type=int, metavar="SECONDS",
                       help="Seconds between checks (default: 60)")
    watch.add_argument("--repeat-minutes", dest="repeat_alert_minutes", type=int, metavar="MINUTES",
                       help="Re-alert this often while tickets stay available (default: off)")
    watch.add_argument("--alert-on", metavar="KINDS",
                       help="Comma separated list of %s" % ",".join(ALL_ALERTS))
    watch.add_argument("--once", action="store_true", help="Run a single check and exit")
    watch.add_argument("--max-checks", type=int, metavar="N", help="Stop after N checks (handy for testing)")

    check = sub.add_parser("check", parents=[common, notify], help="Run one check and print the result")
    check.add_argument("--json", action="store_true", help="Machine readable output")
    check.add_argument("--no-notify", action="store_true", help="Print only; do not send alerts anywhere")
    check.add_argument("--alert-on", metavar="KINDS", help="Comma separated list of alert kinds")

    resolve = sub.add_parser("resolve", parents=[common], help="Look up artist ids and every upcoming date")
    resolve.add_argument("--all-cities", action="store_true", help="Do not filter by city")
    resolve.add_argument("--json", action="store_true", help="Machine readable output")

    sub.add_parser("test-notify", parents=[common, notify], help="Send a fake alert through every channel")
    status = sub.add_parser("status", parents=[common], help="Show what the monitor currently remembers")
    status.add_argument("--json", action="store_true", help="Machine readable output")

    setup_email = sub.add_parser(
        "setup-email",
        help="Set up email alerts in one go (writes your gitignored config.json)",
    )
    setup_email.add_argument("address", nargs="?", help="Where alerts should be sent")
    setup_email.add_argument("-c", "--config", metavar="PATH", help="Config file to write (default: ./config.json)")
    setup_email.add_argument("--smtp-host", help="Only needed for providers we cannot work out from the address")
    setup_email.add_argument("--smtp-port", type=int)
    setup_email.add_argument("--password", help="App password. Prompted for if omitted; TICKETWATCH_SMTP_PASSWORD works too")
    setup_email.add_argument("--no-store-password", action="store_true",
                             help="Keep the password out of the config file and read it from the environment instead")
    setup_email.add_argument("--no-starttls", dest="starttls", action="store_false", default=True,
                             help="For local relays (or Proton Bridge) that do not use STARTTLS")
    setup_email.add_argument("--no-test", action="store_true", help="Skip the test email")

    init = sub.add_parser("init", help="Write a starter config.json")
    init.add_argument("path", nargs="?", default="config.json")
    init.add_argument("--force", action="store_true", help="Overwrite an existing file")

    return parser


CONFIG_KEYS = {
    "api_key", "keyword", "cities", "country_code", "attraction_id", "venue_id", "state_file",
    "check_inventory", "strict_artist_match", "log_level", "log_file", "interval_seconds",
    "repeat_alert_minutes",
}
NOTIFIER_KEYS = {
    "ntfy_topic", "ntfy_server", "webhook_url", "email_to", "command", "open_browser", "desktop", "console",
}


def overrides_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    data = vars(args)
    out: Dict[str, Any] = {k: v for k, v in data.items() if k in CONFIG_KEYS and v is not None}
    notifiers = {k: v for k, v in data.items() if k in NOTIFIER_KEYS and v is not None}
    if notifiers:
        out["notifiers"] = notifiers
    if data.get("alert_on"):
        out["alert_on"] = [part.strip() for part in str(data["alert_on"]).split(",") if part.strip()]
    return out


def setup_logging(level: str, log_file: Optional[str] = None) -> None:
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def load(args: argparse.Namespace) -> Config:
    path = Path(args.config).expanduser() if getattr(args, "config", None) else find_default_config()
    if path is not None and not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    cfg = build_config(config_path=path, cli_overrides=overrides_from_args(args))
    setup_logging(cfg.log_level, cfg.log_file)
    if path:
        LOG.debug("Loaded config from %s", path)
    cfg.validate()
    return cfg


def make_client(cfg: Config) -> DiscoveryClient:
    return DiscoveryClient(
        cfg.api_key,
        base_url=cfg.discovery_base_url,
        inventory_url=cfg.inventory_base_url,
        timeout=cfg.timeout_seconds,
        max_retries=cfg.max_retries,
    )


def make_monitor(cfg: Config) -> Monitor:
    client = make_client(cfg)
    store = StateStore(cfg.state_file)
    dispatcher = build_dispatcher(cfg.notifiers)
    return Monitor(cfg, client, store, dispatcher)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_watch(args: argparse.Namespace) -> int:
    cfg = load(args)
    monitor = make_monitor(cfg)
    monitor.install_signal_handlers()

    quota = cfg.daily_request_estimate
    LOG.info(
        "Watching %s in %s every %ss (about %d API calls/day; the free tier allows 5000)",
        cfg.keyword or cfg.attraction_id,
        ", ".join(cfg.cities) or "anywhere",
        cfg.interval_seconds,
        quota,
    )
    if quota > 5000:
        LOG.warning("That interval will exceed the free daily quota - consider --interval 60 or higher")
    LOG.info("Alerting via: %s", ", ".join(monitor.dispatcher.channel_names) or "nothing configured!")

    max_checks = 1 if getattr(args, "once", False) else getattr(args, "max_checks", None)
    try:
        monitor.run_forever(max_iterations=max_checks)
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        pass
    LOG.info("Stopped after %d check(s)", monitor.checks)
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    cfg = load(args)
    if args.json:
        cfg.notifiers.console = False  # keep stdout parseable
    monitor = make_monitor(cfg)
    try:
        result = monitor.check_once(notify=not args.no_notify)
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(
            {
                "checked_at": (result.checked_at or now_utc()).isoformat(),
                "error": result.error,
                "events": [e.to_dict() for e in result.events],
                "buyable": [e.id for e in result.buyable],
                "alerts": [{"kind": a.kind, "title": a.title, "body": a.body, "url": a.url} for a in result.alerts],
            },
            indent=2,
        ))
    else:
        if result.error:
            print(f"error: {result.error}", file=sys.stderr)
            return EXIT_ERROR
        print_events(result.events, result.checked_at)
    if result.error:
        return EXIT_ERROR
    return EXIT_OK if result.buyable else EXIT_NOT_AVAILABLE


def cmd_resolve(args: argparse.Namespace) -> int:
    cfg = load(args)
    client = make_client(cfg)
    try:
        attractions = client.search_attractions(cfg.keyword) if cfg.keyword else []
        raw_events = client.search_events(keyword=cfg.keyword or None, attraction_id=cfg.attraction_id)
    except TicketmasterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    events = [EventSnapshot.from_api(item) for item in raw_events if isinstance(item, dict)]
    if not args.all_cities:
        events = filter_events(
            events,
            keyword=cfg.keyword,
            cities=cfg.cities,
            attraction_id=cfg.attraction_id or "",
            strict=cfg.strict_artist_match,
        )

    if args.json:
        print(json.dumps(
            {
                "attractions": [{"id": a.get("id"), "name": a.get("name"), "url": a.get("url")} for a in attractions],
                "events": [e.to_dict() for e in events],
            },
            indent=2,
        ))
        return EXIT_OK

    if attractions:
        print("Matching artists (use the id with --attraction-id for an exact watch):")
        for item in attractions:
            print(f"  {item.get('id', '?'):<16} {item.get('name', '')}")
            if item.get("url"):
                print(f"  {'':<16} {item['url']}")
    else:
        print("No artists matched that keyword.")

    print()
    print(f"Upcoming dates ({'all cities' if args.all_cities else ', '.join(cfg.cities)}):")
    print_events(events, now_utc())
    return EXIT_OK


def sample_alert(keyword: str = "Sienna Spiro", city: str = "Toronto"):
    """A realistic-looking alert for testing notification channels."""
    sample = EventSnapshot(
        id="TEST",
        name=f"{keyword} (test alert)",
        url="https://www.ticketmaster.ca/",
        venue="History",
        city=city,
        local_date="2026-10-25",
        local_time="19:00:00",
        status_code="onsale",
        inventory_status="AVAILABLE",
        price_min=59.5,
        price_max=149.0,
        currency="CAD",
    )
    return make_alert("on_sale", sample, now_utc(), artist=keyword)


def cmd_test_notify(args: argparse.Namespace) -> int:
    cfg = load(args)
    dispatcher = build_dispatcher(cfg.notifiers)
    if not dispatcher.notifiers:
        print("No notification channels are configured.", file=sys.stderr)
        return EXIT_ERROR
    alert = sample_alert(cfg.keyword, cfg.cities[0] if cfg.cities else "Toronto")
    delivered = dispatcher.dispatch([alert])
    print(f"Sent a test alert through: {', '.join(dispatcher.channel_names)} ({delivered} delivery attempt(s) ok)")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load(args)
    records = StateStore(cfg.state_file).load()
    if args.json:
        print(json.dumps(
            {
                event_id: {
                    "snapshot": record.snapshot.to_dict(),
                    "first_seen": record.first_seen,
                    "last_seen": record.last_seen,
                    "last_alert_at": record.last_alert_at,
                    "last_alert_kind": record.last_alert_kind,
                }
                for event_id, record in records.items()
            },
            indent=2,
        ))
        return EXIT_OK
    if not records:
        print(f"No state yet ({cfg.state_file}). Run a check first.")
        return EXIT_OK
    print(f"{len(records)} event(s) remembered in {cfg.state_file}:")
    print_events([r.snapshot for r in records.values()], now_utc())
    for event_id, record in records.items():
        if record.last_alert_at:
            print(f"  last alert for {event_id}: {record.last_alert_kind} at {record.last_alert_at}")
    return EXIT_OK


def cmd_setup_email(args: argparse.Namespace) -> int:
    """Ask the three things we cannot guess, work out the rest, prove it works."""
    path = Path(args.config).expanduser() if args.config else (find_default_config() or Path("config.json"))
    existing = load_config_file(path) if path.is_file() else dict(SAMPLE_CONFIG)

    address = args.address or _prompt("Send ticket alerts to which email address? ")
    if not address:
        print("Which address? Pass it as an argument: ticketwatch setup-email you@example.com",
              file=sys.stderr)
        return EXIT_ERROR
    if "@" not in address:
        print(f"That does not look like an email address: {address}", file=sys.stderr)
        return EXIT_ERROR

    settings = NotifierSettings(email_to=address, smtp_host=args.smtp_host, smtp_starttls=args.starttls)
    if args.smtp_port:
        settings.smtp_port = args.smtp_port
    settings.apply_email_defaults()

    if not settings.smtp_host:
        print(f"We do not know the mail server for {address.rpartition('@')[2]}.")
        settings.smtp_host = args.smtp_host or _prompt("SMTP host (e.g. smtp.example.com): ")
        if not settings.smtp_host:
            print("Re-run with --smtp-host (and --smtp-port if it is not 587).", file=sys.stderr)
            return EXIT_ERROR
        port = _prompt(f"SMTP port [{settings.smtp_port}]: ")
        if port.strip():
            try:
                settings.smtp_port = int(port)
            except ValueError:
                print(f"{port} is not a port number.", file=sys.stderr)
                return EXIT_ERROR

    print(f"Sending through {settings.smtp_host}:{settings.smtp_port} as {settings.smtp_user}.")
    if settings.needs_app_password:
        print("This provider needs an app password, not your normal one.")
        if "gmail" in settings.smtp_host:
            print("  Create one at https://myaccount.google.com/apppasswords")
            print("  (it only appears once 2-Step Verification is on).")

    password = args.password or os.environ.get("TICKETWATCH_SMTP_PASSWORD") or ""
    if not password:
        if not sys.stdin.isatty():
            print(
                "No password given. Pass --password, or set TICKETWATCH_SMTP_PASSWORD.",
                file=sys.stderr,
            )
            return EXIT_ERROR
        password = getpass.getpass("App password (hidden): ")
    if not password:
        print("No password, no email.", file=sys.stderr)
        return EXIT_ERROR
    if settings.needs_app_password:
        # Google and friends show app passwords in groups of four; the spaces
        # are decoration and will fail the login if you keep them.
        password = password.replace(" ", "")
    settings.smtp_password = password

    notifiers = dict(existing.get("notifiers") or {})
    notifiers.update(
        {
            "email_to": settings.email_to,
            "email_from": settings.email_from,
            "smtp_host": settings.smtp_host,
            "smtp_port": settings.smtp_port,
            "smtp_user": settings.smtp_user,
            "smtp_starttls": settings.smtp_starttls,
            "smtp_password": None if args.no_store_password else password,
        }
    )
    existing["notifiers"] = notifiers
    existing.setdefault("api_key", SAMPLE_CONFIG["api_key"])

    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 - it holds a password
    except OSError:  # pragma: no cover - unusual filesystems
        pass
    print(f"\nSaved to {path} (gitignored, readable only by you).")
    if args.no_store_password:
        print("Password not stored. Export TICKETWATCH_SMTP_PASSWORD before running the watcher.")

    if args.no_test:
        return EXIT_OK

    print("Sending a test email...")
    try:
        EmailNotifier(settings).send(sample_alert())
    except Exception as exc:  # any SMTP failure, reported plainly
        print(f"\nCould not send: {exc}", file=sys.stderr)
        print("Fix the above and re-run `ticketwatch setup-email`.", file=sys.stderr)
        return EXIT_ERROR
    print(f"Sent. Check {address} - it should have a 'Buy on Ticketmaster' button.")
    return EXIT_OK


def _prompt(question: str) -> str:
    """Ask, but never block when there is nobody at the keyboard."""
    if not sys.stdin.isatty():
        return ""
    try:
        return input(question).strip()
    except EOFError:
        return ""


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return EXIT_ERROR
    path.write_text(json.dumps(SAMPLE_CONFIG, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}. Add your Ticketmaster API key, then run: ticketwatch watch")
    return EXIT_OK


# --------------------------------------------------------------------------- #
def print_events(events: List[EventSnapshot], now=None) -> None:
    now = now or now_utc()
    if not events:
        print("  (no matching events)")
        return
    for event in sorted(events, key=lambda e: (e.local_date or "9999", e.local_time or "")):
        marker = "BUY NOW " if event.buyable(now) else "        "
        print(f"  {marker}{event.describe(now)}")
        if event.url:
            print(f"          {event.url}")


def normalize_argv(argv: List[str]) -> List[str]:
    """`ticketwatch` and `ticketwatch --once` both mean `ticketwatch watch ...`."""
    argv = list(argv)
    if not argv:
        return ["watch"]
    if argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version"):
        return ["watch"] + argv
    return argv


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(list(sys.argv[1:] if argv is None else argv)))

    handlers = {
        "watch": cmd_watch,
        "check": cmd_check,
        "resolve": cmd_resolve,
        "test-notify": cmd_test_notify,
        "setup-email": cmd_setup_email,
        "status": cmd_status,
        "init": cmd_init,
    }
    handler = handlers[args.command_name]
    try:
        return handler(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except TicketmasterError as exc:
        print(f"ticketmaster error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
