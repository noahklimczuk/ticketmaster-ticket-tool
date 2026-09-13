"""Ticketmaster, via the official Discovery API - the primary market."""

from __future__ import annotations

from typing import Any, List, Optional

from ..events import EventSnapshot
from ..ticketmaster import DiscoveryClient, NotEntitled, RateLimited, TicketmasterError
from .base import (
    CANCELLED,
    ON_SALE,
    PRESALE,
    SCHEDULED,
    SOLD_OUT,
    UNAVAILABLE,
    Listing,
    Provider,
    ProviderError,
    ProviderQuery,
    ProviderRateLimited,
)

# EventSnapshot.availability() already speaks nearly the same language.
_AVAILABILITY = {
    "on_sale": ON_SALE,
    "presale": PRESALE,
    "scheduled": SCHEDULED,
    "sold_out": SOLD_OUT,
    "few_left": ON_SALE,
    "cancelled": CANCELLED,
}


class TicketmasterProvider(Provider):
    name = "ticketmaster"
    label = "Ticketmaster"
    credential_hint = "Free API key from developer.ticketmaster.com"
    reports_prices = True

    def __init__(self, client: DiscoveryClient, check_inventory: bool = True) -> None:
        super().__init__()
        self.client = client
        self.check_inventory = check_inventory
        self._inventory_supported = check_inventory

    @classmethod
    def from_config(cls, config) -> Optional["TicketmasterProvider"]:
        if not getattr(config, "api_key", ""):
            return None
        client = DiscoveryClient(
            config.api_key,
            base_url=config.discovery_base_url,
            inventory_url=config.inventory_base_url,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        )
        return cls(client, check_inventory=config.check_inventory)

    # ------------------------------------------------------------------ #
    def fetch(self, query: ProviderQuery) -> List[Listing]:
        try:
            raw = self.client.search_events(
                keyword=query.keyword or None,
                attraction_id=query.attraction_id or None,
            )
        except RateLimited as exc:
            raise ProviderRateLimited(str(exc), exc.retry_after) from exc
        except TicketmasterError as exc:
            raise ProviderError(str(exc)) from exc

        snapshots = [EventSnapshot.from_api(item) for item in raw if isinstance(item, dict)]
        if self._inventory_supported and snapshots:
            try:
                statuses = self.client.inventory_status([s.id for s in snapshots])
                for snapshot in snapshots:
                    snapshot.inventory_status = statuses.get(snapshot.id)
            except NotEntitled:
                self._inventory_supported = False
            except TicketmasterError:
                pass  # sale windows still tell us plenty
        return [self.to_listing(snapshot) for snapshot in snapshots]

    # ------------------------------------------------------------------ #
    def to_listing(self, snapshot: EventSnapshot) -> Listing:
        label = snapshot.availability()
        return Listing(
            platform=self.name,
            event_id=snapshot.id,
            title=snapshot.name,
            artist=snapshot.attractions[0] if snapshot.attractions else snapshot.name,
            venue=snapshot.venue,
            city=snapshot.city,
            region=snapshot.region,
            country=snapshot.country,
            local_date=snapshot.local_date,
            local_time=snapshot.local_time,
            url=snapshot.url,
            price_min=snapshot.price_min,
            price_max=snapshot.price_max,
            currency=snapshot.currency,
            availability=_AVAILABILITY.get(label, UNAVAILABLE),
            inventory_status=snapshot.inventory_status,
            on_sale_at=snapshot.public_start,
            note=snapshot.ticket_limit,
        )
