"""A local control panel: start, stop, watch prices, all from a browser tab.

The page is served from this machine only. It talks to a small JSON API that
drives the same Monitor the command line uses, so nothing behaves differently
just because you clicked it.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import webbrowser
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .alerts import Alert
from .config import Config, ConfigError, build_config, load_config_file, write_config_file
from .events import now_utc
from .monitor import CheckResult, Monitor
from .notify import Notifier, build_dispatcher
from .providers import build_providers
from .state import StateStore
from .webui import PAGE

LOG = logging.getLogger(__name__)

# Settings the panel is allowed to change.
EDITABLE = {
    "keyword", "cities", "country_code", "interval_seconds", "repeat_alert_minutes",
    "price_drop_percent", "alert_on", "api_key", "seatgeek_client_id", "bandsintown_app_id",
    "strict_artist_match", "check_inventory",
}
EDITABLE_NOTIFIERS = {
    "console", "desktop", "open_browser", "ntfy_topic", "webhook_url", "command",
    "email_to", "smtp_password", "smtp_host", "smtp_port", "smtp_user", "email_from",
}


class FeedNotifier(Notifier):
    """Keeps the last few alerts around so the page can show them."""

    name = "panel"

    def __init__(self, feed: deque) -> None:
        self.feed = feed

    def send(self, alert: Alert) -> None:
        self.feed.appendleft(
            {
                "kind": alert.kind,
                "title": alert.title,
                "body": alert.body,
                "url": alert.url,
                "urgent": alert.urgent,
                "at": now_utc().isoformat(),
            }
        )


class GuiApp:
    """The state behind the panel: one monitor, one thread, one feed of news."""

    def __init__(self, config: Config, config_path: Optional[Path] = None) -> None:
        self.config = config
        self.config_path = Path(config_path) if config_path else Path("config.json")
        self.alerts: deque = deque(maxlen=40)
        self.log: deque = deque(maxlen=120)
        self.lock = threading.RLock()
        self._check_lock = threading.Lock()

        self.monitor: Optional[Monitor] = None
        self.thread: Optional[threading.Thread] = None
        self.last_check: Optional[datetime] = None
        self.next_check: Optional[datetime] = None
        self.last_result: Optional[CheckResult] = None
        self.last_error: Optional[str] = None
        self.events: List[Any] = []

        self._load_remembered_events()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def _load_remembered_events(self) -> None:
        """Show what we knew from last time before the first check lands."""
        try:
            records = StateStore(self.config.state_file).load()
            self.events = [record.snapshot for record in records.values()]
        except Exception as exc:  # pragma: no cover - unreadable state is not fatal
            LOG.debug("Could not preload state: %s", exc)

    def note(self, text: str, level: str = "info") -> None:
        self.log.appendleft({"at": now_utc().isoformat(), "text": text, "level": level})

    def build_monitor(self) -> Monitor:
        dispatcher = build_dispatcher(self.config.notifiers)
        dispatcher.notifiers.append(FeedNotifier(self.alerts))
        return Monitor(
            self.config,
            build_providers(self.config),
            StateStore(self.config.state_file),
            dispatcher,
            on_check=self._on_check,
        )

    def _on_check(self, result: CheckResult, delay: float) -> None:
        with self.lock:
            self.last_check = result.checked_at or now_utc()
            self.next_check = self.last_check + timedelta(seconds=delay)
            self.last_result = result
            self.last_error = result.error
            if result.ok:
                self.events = result.events
        if result.error:
            self.note(f"Check failed: {result.error}", "error")
        else:
            for platform, message in result.provider_errors.items():
                self.note(f"{platform} unavailable: {message}", "warn")

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> Dict[str, Any]:
        with self.lock:
            if self.running:
                return self.snapshot()
            try:
                self.config.validate()
            except ConfigError as exc:
                self.last_error = str(exc)
                self.note(str(exc), "error")
                return self.snapshot()

            self.monitor = self.build_monitor()
            self.last_error = None
            self.thread = threading.Thread(target=self._run, daemon=True, name="ticketwatch-monitor")
            self.thread.start()
            self.note(
                f"Watching {self.config.keyword} in {', '.join(self.config.cities) or 'anywhere'} "
                f"across {len(self.monitor.providers)} platform(s), every {self.config.interval_seconds}s"
            )
        return self.snapshot()

    def _run(self) -> None:
        try:
            self.monitor.run_forever()
        except Exception as exc:  # pragma: no cover - defensive
            LOG.exception("Monitor thread died")
            with self.lock:
                self.last_error = str(exc)
            self.note(f"Monitor stopped: {exc}", "error")

    def stop(self) -> Dict[str, Any]:
        with self.lock:
            monitor, thread = self.monitor, self.thread
        if monitor:
            monitor.request_stop()
        if thread:
            thread.join(timeout=3)
        with self.lock:
            self.next_check = None
            if not self.running:
                self.note("Stopped watching")
        return self.snapshot()

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    def check_now(self) -> Dict[str, Any]:
        """One check on demand, without disturbing the polling loop."""
        with self._check_lock:
            monitor = self.monitor or self.build_monitor()
            try:
                self.config.validate()
            except ConfigError as exc:
                with self.lock:
                    self.last_error = str(exc)
                self.note(str(exc), "error")
                return self.snapshot()
            result = monitor.check_once()
            self._on_check(result, self.config.interval_seconds)
            if result.ok:
                self.note(f"Checked: {len(result.events)} show(s), {len(result.buyable)} buyable now")
        return self.snapshot()

    def test_notify(self) -> Dict[str, Any]:
        from .cli import sample_alert

        dispatcher = build_dispatcher(self.config.notifiers)
        dispatcher.notifiers.append(FeedNotifier(self.alerts))
        dispatcher.dispatch([sample_alert(self.config.keyword, self.config.cities[0] if self.config.cities else "")])
        self.note(f"Test alert sent via {', '.join(dispatcher.channel_names)}")
        return self.snapshot()

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Save changes to the config file, then reload from it."""
        data = load_config_file(self.config_path) if self.config_path.is_file() else {}
        notifiers = dict(data.get("notifiers") or {})

        for key, value in (patch or {}).items():
            if key == "notifiers" and isinstance(value, dict):
                for nkey, nvalue in value.items():
                    if nkey in EDITABLE_NOTIFIERS:
                        notifiers[nkey] = nvalue or None if isinstance(nvalue, str) else nvalue
            elif key in EDITABLE:
                data[key] = value
        if notifiers:
            data["notifiers"] = notifiers

        was_running = self.running
        if was_running:
            self.stop()

        write_config_file(self.config_path, data)
        try:
            self.config = build_config(config_path=self.config_path)
        except ConfigError as exc:
            self.last_error = str(exc)
            self.note(f"Settings rejected: {exc}", "error")
            return self.snapshot()

        self.note("Settings saved")
        # Say straight away what is still wrong, rather than at Start time.
        try:
            self.config.validate()
            self.last_error = None
        except ConfigError as exc:
            self.last_error = str(exc)
            self.note(str(exc), "warn")
            return self.snapshot()

        self.monitor = None
        self._load_remembered_events()
        if was_running:
            self.start()
        return self.snapshot()

    # ------------------------------------------------------------------ #
    # what the page renders
    # ------------------------------------------------------------------ #
    def platform_rows(self) -> List[Dict[str, Any]]:
        from .providers import PROVIDER_CLASSES

        errors = self.last_result.provider_errors if self.last_result else {}
        rows = []
        for cls in PROVIDER_CLASSES:
            configured = cls.from_config(self.config) is not None
            rows.append(
                {
                    "name": cls.name,
                    "label": cls.label,
                    "configured": configured,
                    "reports_prices": cls.reports_prices,
                    "hint": cls.credential_hint,
                    "error": errors.get(cls.name),
                }
            )
        return rows

    def event_rows(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or now_utc()
        rows = []
        for event in sorted(self.events, key=lambda e: (e.local_date or "9999", e.local_time or "")):
            upcoming = event.next_sale_start(now)
            rows.append(
                {
                    "id": event.id,
                    "name": event.name,
                    "when": event.when,
                    "where": event.where,
                    "city": event.city,
                    "availability": event.availability(now),
                    "buyable": event.buyable(now),
                    "price": event.price_range,
                    "price_min": event.price_min,
                    "currency": event.currency,
                    "cheapest_platform": event.cheapest.platform_label if event.cheapest else "",
                    "mixed_currency": event.mixed_currency,
                    "platforms": event.platforms,
                    "url": event.url,
                    "links": event.links(),
                    "quotes": [
                        {"platform": q.platform_label, "price": q.price_label(), "url": q.url,
                         "count": q.listing_count}
                        for q in event.quotes
                    ],
                    "on_sale_at": upcoming.isoformat() if upcoming else None,
                }
            )
        return rows

    def snapshot(self) -> Dict[str, Any]:
        cfg = self.config
        with self.lock:
            rows = self.event_rows()
            buyable = [r for r in rows if r["buyable"]]
            priced = [r for r in buyable if r["price_min"] is not None]
            cheapest = min(priced, key=lambda r: r["price_min"]) if priced else None
            return {
                "running": self.running,
                "checks": self.monitor.checks if self.monitor else 0,
                "last_check": self.last_check.isoformat() if self.last_check else None,
                "next_check": self.next_check.isoformat() if self.next_check else None,
                "error": self.last_error,
                "events": rows,
                "buyable_count": len(buyable),
                "cheapest": cheapest,
                "platforms": self.platform_rows(),
                "alerts": list(self.alerts)[:12],
                "log": list(self.log)[:30],
                "channels": build_dispatcher(cfg.notifiers).channel_names,
                "settings": {
                    "keyword": cfg.keyword,
                    "cities": cfg.cities,
                    "country_code": cfg.country_code,
                    "interval_seconds": cfg.interval_seconds,
                    "repeat_alert_minutes": cfg.repeat_alert_minutes,
                    "price_drop_percent": cfg.price_drop_percent,
                    "alert_on": cfg.alert_on,
                    "api_key_set": bool(cfg.api_key),
                    "seatgeek_client_id_set": bool(cfg.seatgeek_client_id),
                    "bandsintown_app_id": cfg.bandsintown_app_id,
                    "email_to": cfg.notifiers.email_to or "",
                    "email_password_set": bool(cfg.notifiers.smtp_password),
                    "ntfy_topic": cfg.notifiers.ntfy_topic or "",
                    "desktop": cfg.notifiers.desktop,
                    "open_browser": cfg.notifiers.open_browser,
                },
                "config_path": str(self.config_path),
            }


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ticketwatch"

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _authorised(self, query: Dict[str, List[str]]) -> bool:
        token = self.server.token
        if not token:
            return True
        supplied = (query.get("token") or [""])[0] or self.headers.get("X-Ticketwatch-Token", "")
        return secrets.compare_digest(supplied, token)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except json.JSONDecodeError:
            return {}

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorised(query):
            self._json({"error": "bad token"}, 403)
            return
        if parsed.path in ("/", "/index.html"):
            page = PAGE.replace("__TOKEN__", self.server.token or "")
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif parsed.path == "/api/state":
            self._json(self.server.app.snapshot())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        if not self._authorised(parse_qs(parsed.query)):
            self._json({"error": "bad token"}, 403)
            return
        app = self.server.app
        routes = {
            "/api/start": lambda: app.start(),
            "/api/stop": lambda: app.stop(),
            "/api/check": lambda: app.check_now(),
            "/api/test-notify": lambda: app.test_notify(),
            "/api/settings": lambda: app.update_settings(self._body()),
        }
        handler = routes.get(parsed.path)
        if handler is None:
            self._json({"error": "not found"}, 404)
            return
        try:
            self._json(handler())
        except Exception as exc:  # never 500 silently; the page shows the message
            LOG.exception("Panel action failed")
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def log_message(self, *args: Any) -> None:
        pass  # the panel has its own activity log


def make_server(app: GuiApp, host: str = "127.0.0.1", port: int = 8765, token: str = "") -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    server.block_on_close = False
    server.app = app
    server.token = token
    return server


def serve(
    config: Config,
    config_path: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    autostart: bool = False,
) -> None:
    """Run the panel until Ctrl-C."""
    app = GuiApp(config, config_path)
    # Anything beyond this machine needs a shared secret in the URL.
    token = "" if host in ("127.0.0.1", "localhost", "::1") else secrets.token_urlsafe(16)
    server = make_server(app, host, port, token)

    shown = host if host != "0.0.0.0" else "<this machine's IP>"
    url = f"http://{shown}:{server.server_address[1]}/"
    if token:
        url += f"?token={token}"

    if autostart:
        app.start()
    print(f"\nticketwatch control panel: {url}")
    print("Leave this window open. Press Ctrl-C to quit.\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - headless machines
            pass
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        app.stop()
        server.shutdown()
        server.server_close()
