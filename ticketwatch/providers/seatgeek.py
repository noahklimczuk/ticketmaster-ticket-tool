"""SeatGeek, via its official Platform API - mostly resale, and it quotes prices."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..http import HttpAuthError, HttpError, HttpRateLimited, JsonHttpClient
from .base import ON_SALE, SOLD_OUT, UNKNOWN, Listing, Provider, ProviderError, ProviderQuery, ProviderRateLimited

API_URL = "https://api.seatgeek.com/2/events"


class SeatGeekProvider(Provider):
    name = "seatgeek"
    label = "SeatGeek"
    credential_hint = "Client ID from seatgeek.com/account/develop"
    reports_prices = True

    def __init__(self, client_id: str, client: Optional[JsonHttpClient] = None,
                 api_url: str = API_URL, currency: str = "USD") -> None:
        super().__init__()
        self.client_id = client_id
        self.api_url = api_url
        self.currency = currency
        self.http = client or JsonHttpClient(secrets=[client_id])

    @classmethod
    def from_config(cls, config) -> Optional["SeatGeekProvider"]:
        client_id = getattr(config, "seatgeek_client_id", "") or ""
        if not client_id:
            return None
        return cls(
            client_id,
            client=JsonHttpClient(
                timeout=config.timeout_seconds, max_retries=config.max_retries, secrets=[client_id]
            ),
            api_url=getattr(config, "seatgeek_base_url", API_URL),
            currency=getattr(config, "seatgeek_currency", "USD"),
        )

    # ------------------------------------------------------------------ #
    def fetch(self, query: ProviderQuery) -> List[Listing]:
        params = {
            "client_id": self.client_id,
            "q": query.keyword,
            "per_page": 50,
            "sort": "datetime_local.asc",
            "datetime_utc.gte": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            payload = self.http.get(self.api_url, params)
        except HttpAuthError as exc:
            raise ProviderError(f"SeatGeek rejected the client ID: {exc}") from exc
        except HttpRateLimited as exc:
            raise ProviderRateLimited(str(exc), exc.retry_after) from exc
        except HttpError as exc:
            raise ProviderError(str(exc)) from exc

        events = payload.get("events") if isinstance(payload, dict) else None
        return [self.to_listing(e) for e in (events or []) if isinstance(e, dict)]

    # ------------------------------------------------------------------ #
    def to_listing(self, event: Dict[str, Any]) -> Listing:
        venue = event.get("venue") or {}
        stats = event.get("stats") or {}
        performers = [p for p in (event.get("performers") or []) if isinstance(p, dict)]
        local = str(event.get("datetime_local") or "")
        date, _, time_part = local.partition("T")

        lowest = _number(stats.get("lowest_price"))
        highest = _number(stats.get("highest_price"))
        count = stats.get("listing_count")
        count = int(count) if isinstance(count, (int, float)) else None

        if count == 0 or (count is None and lowest is None):
            availability = SOLD_OUT if count == 0 else UNKNOWN
        else:
            availability = ON_SALE

        return Listing(
            platform=self.name,
            event_id=str(event.get("id") or ""),
            title=str(event.get("title") or ""),
            artist=str(performers[0].get("name") if performers else event.get("title") or ""),
            venue=str(venue.get("name") or ""),
            city=str(venue.get("city") or ""),
            region=str(venue.get("state") or ""),
            country=str(venue.get("country") or ""),
            local_date=date,
            local_time=time_part,
            url=str(event.get("url") or ""),
            price_min=lowest,
            price_max=highest,
            currency=self.currency,
            availability=availability,
            listing_count=count,
            resale=True,
            note=f"{count} listings" if count else "",
        )


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None
