"""Test helpers: a local stand-in for the Ticketmaster API and payload builders."""

from __future__ import annotations

import json
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
