"""Test helpers: a local stand-in for the Ticketmaster API and payload builders."""

from __future__ import annotations

import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

Route = Callable[[Dict[str, List[str]], int], Tuple[int, Any]]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _respond(self, status: int, payload: Any) -> None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode("utf-8")
            content_type = "application/json"
        else:
            body = str(payload).encode("utf-8")
            content_type = "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in getattr(self.server, "extra_headers", {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        self.server.requests.append({"path": parsed.path, "query": query, "method": "GET"})
        route = self.server.routes.get(parsed.path)
        if route is None:
            self._respond(404, {"errors": [{"detail": "no such route"}]})
            return
        status, payload = route(query, len(self.server.requests))
        self._respond(status, payload)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        self.server.requests.append(
            {"path": parsed.path, "method": "POST", "body": body, "headers": dict(self.headers)}
        )
        self._respond(200, {"ok": True})

    def log_message(self, *args: Any) -> None:  # keep the test output clean
        pass


class MockServer:
    """A throwaway HTTP server on 127.0.0.1 that answers canned JSON."""

    def __init__(self, routes: Dict[str, Route] | None = None) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.routes = dict(routes or {})
        self.server.requests = []
        self.server.extra_headers = {}
        # A short poll interval keeps shutdown() snappy between tests.
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    # -- lifecycle -------------------------------------------------------
    def __enter__(self) -> "MockServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    # -- helpers ---------------------------------------------------------
    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def requests(self) -> List[Dict[str, Any]]:
        return self.server.requests

    def route(self, path: str, handler: Route) -> None:
        self.server.routes[path] = handler

    def json_route(self, path: str, payload: Any, status: int = 200) -> None:
        self.server.routes[path] = lambda query, count: (status, payload)


# --------------------------------------------------------------------------- #
# payload builders
# --------------------------------------------------------------------------- #
def event_payload(
    event_id: str = "G5vYZ9abc123",
    name: str = "Sienna Spiro",
    city: str = "Toronto",
    venue: str = "History",
    local_date: str = "2026-10-25",
    local_time: str = "19:00:00",
    status: str = "onsale",
    public_start: str | None = "2026-02-14T15:00:00Z",
    public_end: str | None = "2026-10-25T23:00:00Z",
    presales: List[Dict[str, Any]] | None = None,
    price_min: float | None = 59.5,
    price_max: float | None = 149.0,
    currency: str = "CAD",
    attraction_name: str = "Sienna Spiro",
    attraction_id: str = "K8vZ917qxR7",
    country: str = "CA",
) -> Dict[str, Any]:
    """A Discovery API event, shaped like the real thing."""
    sales: Dict[str, Any] = {"public": {"startTBD": public_start is None, "startTBA": False}}
    if public_start:
        sales["public"]["startDateTime"] = public_start
    if public_end:
        sales["public"]["endDateTime"] = public_end
    if presales:
        sales["presales"] = presales

    payload: Dict[str, Any] = {
        "name": name,
        "type": "event",
        "id": event_id,
        "url": f"https://www.ticketmaster.ca/event/{event_id}",
        "locale": "en-ca",
        "sales": sales,
        "dates": {
            "start": {"localDate": local_date, "localTime": local_time, "dateTime": f"{local_date}T23:00:00Z"},
            "timezone": "America/Toronto",
            "status": {"code": status},
            "spanMultipleDays": False,
        },
        "_embedded": {
            "venues": [
                {
                    "name": venue,
                    "city": {"name": city},
                    "state": {"name": "Ontario", "stateCode": "ON"},
                    "country": {"name": "Canada", "countryCode": country},
                }
            ],
            "attractions": [{"name": attraction_name, "id": attraction_id}],
        },
        "ticketLimit": {"info": "There is a 4 ticket limit for this event."},
    }
    if price_min is not None or price_max is not None:
        payload["priceRanges"] = [{"type": "standard", "currency": currency, "min": price_min, "max": price_max}]
    return payload


def events_response(events: List[Dict[str, Any]], page: int = 0, total_pages: int = 1) -> Dict[str, Any]:
    return {
        "_embedded": {"events": events},
        "page": {"size": 50, "totalElements": len(events), "totalPages": total_pages, "number": page},
    }


# --------------------------------------------------------------------------- #
# platform-neutral builders
# --------------------------------------------------------------------------- #
def listing(
    platform: str = "ticketmaster",
    event_id: str = "G5vYZ9abc123",
    artist: str = "Sienna Spiro",
    city: str = "Toronto",
    venue: str = "History",
    local_date: str = "2026-10-25",
    local_time: str = "19:00:00",
    price_min=59.5,
    price_max=149.0,
    currency: str = "CAD",
    availability: str = "on_sale",
    **extra: Any,
):
    """A Listing with believable defaults."""
    from ticketwatch.providers.base import Listing

    fields = dict(
        platform=platform,
        event_id=event_id,
        title=artist,
        artist=artist,
        venue=venue,
        city=city,
        region="ON",
        country="CA",
        local_date=local_date,
        local_time=local_time,
        url=f"https://{platform}.test/event/{event_id}",
        price_min=price_min,
        price_max=price_max,
        currency=currency,
        availability=availability,
    )
    fields.update(extra)
    return Listing(**fields)


def merged(*listings):
    """A MergedEvent built from one or more listings."""
    from ticketwatch.aggregate import MergedEvent

    return MergedEvent.from_listings(list(listings) or [listing()])


class FakeProvider:
    """A platform that returns whatever the test tells it to."""

    label = "Fake"
    credential_hint = ""
    reports_prices = True

    def __init__(self, listings=None, error=None, name: str = "fake") -> None:
        self.name = name
        self.listings = list(listings or [])
        self.error = error
        self.calls = 0
        self.last_query = None

    def fetch(self, query):
        self.calls += 1
        self.last_query = query
        if self.error:
            raise self.error
        return list(self.listings)

    def collect(self, query):
        from ticketwatch.providers.base import Provider

        return Provider.collect(self, query)


# --------------------------------------------------------------------------- #
# a minimal SMTP server, so the real sending path is exercised too
# --------------------------------------------------------------------------- #
class _SMTPHandler(socketserver.StreamRequestHandler):
    """Enough of SMTP to accept a message from smtplib and remember it."""

    def _say(self, text: str) -> None:
        self.wfile.write(text.encode("ascii") + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        self._say("220 mock.test ESMTP ready")
        collecting = False
        lines: List[str] = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace")

            if collecting:
                if line.strip() == ".":
                    collecting = False
                    self.server.messages.append("".join(lines))
                    lines = []
                    self._say("250 2.0.0 Message accepted")
                else:
                    lines.append(line)
                continue

            command = line.strip()
            verb = command.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self._say("250-mock.test")
                self._say("250 AUTH PLAIN")
            elif verb == "AUTH":
                self.server.auth_attempts.append(command)
                if self.server.reject_auth:
                    self._say("535 5.7.8 Username and Password not accepted")
                else:
                    self._say("235 2.7.0 Accepted")
            elif verb == "DATA":
                collecting = True
                self._say("354 End data with <CR><LF>.<CR><LF>")
            elif verb == "QUIT":
                self._say("221 2.0.0 Bye")
                return
            else:  # MAIL FROM, RCPT TO, RSET, NOOP...
                self._say("250 2.1.0 OK")


class MockSMTPServer:
    """A throwaway SMTP server on 127.0.0.1 that records what it is sent."""

    def __init__(self, reject_auth: bool = False) -> None:
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTPHandler)
        # Without these, server_close() joins every handler thread - and one
        # still sitting in readline() would hang the whole test run.
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.server.messages: List[str] = []
        self.server.auth_attempts: List[str] = []
        self.server.reject_auth = reject_auth
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    def __enter__(self) -> "MockSMTPServer":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def host(self) -> str:
        return self.server.server_address[0]

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def messages(self) -> List[str]:
        return self.server.messages

    @property
    def auth_attempts(self) -> List[str]:
        return self.server.auth_attempts
