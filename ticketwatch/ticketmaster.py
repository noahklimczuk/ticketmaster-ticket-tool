"""A small, dependency-free client for Ticketmaster's public Discovery API.

Docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/

This talks to the official, documented, key-authenticated API. It does not
scrape ticketmaster.com and it cannot and will not buy anything for you.
"""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

from . import __version__

LOG = logging.getLogger(__name__)

DISCOVERY_BASE = "https://app.ticketmaster.com/discovery/v2"
INVENTORY_BASE = "https://app.ticketmaster.com/inventory-status/v1"
USER_AGENT = f"ticketwatch/{__version__} (+https://github.com/noahklimczuk/ticketmaster-ticket-tool)"

# Discovery refuses deep paging beyond 1000 results; we never need that many.
MAX_PAGES = 5
PAGE_SIZE = 50


class TicketmasterError(Exception):
    """Any failure talking to the API."""


class AuthError(TicketmasterError):
    """The API key was missing, wrong, or not authorised for this endpoint."""


class NotEntitled(TicketmasterError):
    """The key is valid but this particular endpoint is not enabled for it."""


class RateLimited(TicketmasterError):
    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def redact(text: str, api_key: str) -> str:
    """Never let the key reach a log file or a stack trace."""
    if api_key and api_key in text:
        text = text.replace(api_key, "***")
    return text


class DiscoveryClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DISCOVERY_BASE,
        inventory_url: str = INVENTORY_BASE,
        timeout: float = 20.0,
        max_retries: int = 3,
        opener: Optional[Any] = None,
        sleep=time.sleep,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.inventory_url = inventory_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self._opener = opener or urllib.request.build_opener()
        self._sleep = sleep

    # ------------------------------------------------------------------ #
    # transport
    # ------------------------------------------------------------------ #
    def _request(self, url: str, params: Dict[str, Any]) -> Any:
        query = dict(params)
        query["apikey"] = self.api_key
        full = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        safe = redact(full, self.api_key)

        attempt = 0
        while True:
            attempt += 1
            try:
                request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
                with self._opener.open(request, timeout=self.timeout) as response:
                    payload = response.read().decode("utf-8", errors="replace")
                if not payload.strip():
                    return {}
                return json.loads(payload)

            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:400]
                except Exception:  # pragma: no cover - body is best effort
                    pass
                body = redact(body, self.api_key)

                if exc.code in (401, 403):
                    # 401 is a bad key; 403 on a secondary endpoint usually means
                    # "your key does not have this product enabled".
                    if exc.code == 403 and "inventory" in url:
                        raise NotEntitled(f"Inventory status not enabled for this API key: {body}") from exc
                    raise AuthError(f"Ticketmaster rejected the API key ({exc.code}): {body}") from exc
                if exc.code == 429:
                    retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
                    raise RateLimited(
                        f"Rate limited by Ticketmaster: {body or 'quota exceeded'}", retry_after=retry_after
                    ) from exc
                if exc.code >= 500 and attempt <= self.max_retries:
                    self._backoff(attempt, f"HTTP {exc.code}")
                    continue
                raise TicketmasterError(f"HTTP {exc.code} from {safe}: {body}") from exc

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt <= self.max_retries:
                    self._backoff(attempt, str(exc))
                    continue
                raise TicketmasterError(f"Network error calling {safe}: {exc}") from exc

            except json.JSONDecodeError as exc:
                if attempt <= self.max_retries:
                    self._backoff(attempt, "bad JSON")
                    continue
                raise TicketmasterError(f"Ticketmaster returned invalid JSON from {safe}: {exc}") from exc

    def _backoff(self, attempt: int, reason: str) -> None:
        delay = min(30.0, (2 ** (attempt - 1))) + random.uniform(0, 0.5)
        LOG.debug("Retrying in %.1fs after %s (attempt %d)", delay, reason, attempt)
        self._sleep(delay)

    # ------------------------------------------------------------------ #
    # endpoints
    # ------------------------------------------------------------------ #
    def search_events(
        self,
        keyword: Optional[str] = None,
        cities: Optional[Iterable[str]] = None,
        country_code: Optional[str] = None,
        state_code: Optional[str] = None,
        attraction_id: Optional[str] = None,
        venue_id: Optional[str] = None,
        classification_name: Optional[str] = None,
        radius: Optional[int] = None,
        radius_unit: str = "km",
        size: int = PAGE_SIZE,
        max_pages: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Return every event matching the filters, following pagination."""
        params: Dict[str, Any] = {"size": size, "sort": "date,asc"}
        if keyword:
            params["keyword"] = keyword
        cities = [c for c in (cities or []) if c]
        if cities:
            params["city"] = cities
        if country_code:
            params["countryCode"] = country_code
        if state_code:
            params["stateCode"] = state_code
        if attraction_id:
            params["attractionId"] = attraction_id
        if venue_id:
            params["venueId"] = venue_id
        if classification_name:
            params["classificationName"] = classification_name
        if radius:
            params["radius"] = radius
            params["unit"] = "miles" if radius_unit.startswith("mi") else "km"

        events: List[Dict[str, Any]] = []
        page = 0
        while page < max_pages:
            params["page"] = page
            payload = self._request(f"{self.base_url}/events.json", params)
            batch = (payload.get("_embedded") or {}).get("events") or []
            events.extend(batch)
            page_info = payload.get("page") or {}
            total_pages = int(page_info.get("totalPages") or 1)
            if not batch or page + 1 >= total_pages:
                break
            page += 1
        return events

    def search_attractions(self, keyword: str, size: int = 20) -> List[Dict[str, Any]]:
        payload = self._request(f"{self.base_url}/attractions.json", {"keyword": keyword, "size": size})
        return (payload.get("_embedded") or {}).get("attractions") or []

    def inventory_status(self, event_ids: Iterable[str]) -> Dict[str, str]:
        """Map event id -> AVAILABLE / FEW_TICKETS_LEFT / SOLD_OUT / ...

        This endpoint is the closest thing to a real "can I buy right now?"
        signal. Not every API key is entitled to it, so callers should treat
        NotEntitled as "skip this extra check" rather than a failure.
        """
        ids = [e for e in event_ids if e]
        if not ids:
            return {}
        out: Dict[str, str] = {}
        # The endpoint takes a comma separated list; keep batches modest.
        for start in range(0, len(ids), 20):
            batch = ids[start:start + 20]
            payload = self._request(f"{self.inventory_url}/availability", {"events": ",".join(batch)})
            for item in _as_list(payload):
                event_id = item.get("eventId") or item.get("id")
                status = item.get("status")
                if event_id and status:
                    out[str(event_id)] = str(status).upper()
        return out


def _as_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("_embedded", "events", "availability"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
            if isinstance(nested, dict):
                for inner in nested.values():
                    if isinstance(inner, list):
                        return [item for item in inner if isinstance(item, dict)]
    return []


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
