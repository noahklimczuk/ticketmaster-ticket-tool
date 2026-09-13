"""Bandsintown - the widest net for spotting a date nobody else has listed yet.

No prices, but it hears about new shows early and links straight to whoever is
selling them, which is exactly what "tell me about new dates" needs.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

from ..http import HttpAuthError, HttpError, HttpRateLimited, JsonHttpClient
from .base import ON_SALE, UNKNOWN, Listing, Provider, ProviderError, ProviderQuery, ProviderRateLimited

API_ROOT = "https://rest.bandsintown.com"


class BandsintownProvider(Provider):
    name = "bandsintown"
    label = "Bandsintown"
    credential_hint = "Any app id you choose (see bandsintown.com/api)"
    reports_prices = False

    def __init__(self, app_id: str, client: Optional[JsonHttpClient] = None, api_root: str = API_ROOT) -> None:
        super().__init__()
        self.app_id = app_id
        self.api_root = api_root.rstrip("/")
        self.http = client or JsonHttpClient()

    @classmethod
    def from_config(cls, config) -> Optional["BandsintownProvider"]:
        app_id = getattr(config, "bandsintown_app_id", "") or ""
        if not app_id:
            return None
        return cls(
            app_id,
            client=JsonHttpClient(timeout=config.timeout_seconds, max_retries=config.max_retries),
            api_root=getattr(config, "bandsintown_base_url", API_ROOT),
        )

    # ------------------------------------------------------------------ #
    def fetch(self, query: ProviderQuery) -> List[Listing]:
        if not query.keyword:
            return []
        artist = urllib.parse.quote(query.keyword, safe="")
        url = f"{self.api_root}/artists/{artist}/events"
        try:
            payload = self.http.get(url, {"app_id": self.app_id, "date": "upcoming"})
        except HttpAuthError as exc:
            raise ProviderError(f"Bandsintown rejected the app id: {exc}") from exc
        except HttpRateLimited as exc:
            raise ProviderRateLimited(str(exc), exc.retry_after) from exc
        except HttpError as exc:
            raise ProviderError(str(exc)) from exc

        if isinstance(payload, dict):  # an error body, or "artist not found"
            return []
        return [self.to_listing(e, query.keyword) for e in payload if isinstance(e, dict)]

    # ------------------------------------------------------------------ #
    def to_listing(self, event: Dict[str, Any], artist: str) -> Listing:
        venue = event.get("venue") or {}
        offers = [o for o in (event.get("offers") or []) if isinstance(o, dict)]
        ticket_offer = next((o for o in offers if str(o.get("type", "")).lower() == "tickets"), None)
        offer = ticket_offer or (offers[0] if offers else {})
        status = str(offer.get("status") or "").lower()

        local = str(event.get("datetime") or "")
        date, _, time_part = local.partition("T")
        lineup = [name for name in (event.get("lineup") or []) if name]

        return Listing(
            platform=self.name,
            event_id=str(event.get("id") or ""),
            title=str(event.get("title") or " / ".join(lineup) or artist),
            artist=lineup[0] if lineup else artist,
            venue=str(venue.get("name") or ""),
            city=str(venue.get("city") or ""),
            region=str(venue.get("region") or ""),
            country=str(venue.get("country") or ""),
            local_date=date,
            local_time=time_part,
            url=str(offer.get("url") or event.get("url") or ""),
            availability=ON_SALE if status == "available" else UNKNOWN,
            note="announced on Bandsintown",
        )
