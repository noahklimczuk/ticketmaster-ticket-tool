"""Ticket platforms we can ask about an artist's dates and prices."""

from __future__ import annotations

from .base import Listing, Provider, ProviderError, ProviderQuery, ProviderRateLimited, ProviderResult
from .bandsintown import BandsintownProvider
from .links import SEARCH_SITES, search_links
from .seatgeek import SeatGeekProvider
from .ticketmaster import TicketmasterProvider

#: Order matters only for display; every provider is polled independently.
PROVIDER_CLASSES = (TicketmasterProvider, SeatGeekProvider, BandsintownProvider)

__all__ = [
    "Listing",
    "Provider",
    "ProviderError",
    "ProviderRateLimited",
    "ProviderQuery",
    "ProviderResult",
    "TicketmasterProvider",
    "SeatGeekProvider",
    "BandsintownProvider",
    "PROVIDER_CLASSES",
    "SEARCH_SITES",
    "search_links",
    "build_providers",
]


def build_providers(config) -> list:
    """Instantiate every provider that has what it needs to run."""
    providers = []
    for cls in PROVIDER_CLASSES:
        provider = cls.from_config(config)
        if provider is not None:
            providers.append(provider)
    return providers
