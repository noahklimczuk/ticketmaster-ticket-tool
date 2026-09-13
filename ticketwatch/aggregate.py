"""Fold every platform's listings into one row per show, cheapest price first.

Two platforms describing the same night in the same city are the same show, so
they get merged. What matters afterwards is: can I buy it, where is it cheapest,
and is this a date I have never seen before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .events import human_delta, now_utc, parse_iso
from .matcher import canonical_city, normalize
from .providers.base import (
    CANCELLED,
    ON_SALE,
    PRESALE,
    SCHEDULED,
    SOLD_OUT,
    UNAVAILABLE,
    UNKNOWN,
    Listing,
)
from .providers.links import search_links

# Best-first: the strongest signal any platform gives us wins.
_RANK = [ON_SALE, PRESALE, SCHEDULED, SOLD_OUT, UNAVAILABLE, CANCELLED, UNKNOWN]
_RANK_INDEX = {label: i for i, label in enumerate(_RANK)}

LOW_STOCK_STATUSES = {"FEW_TICKETS_LEFT", "LIMITED_AVAILABILITY"}

#: Search-link ids that stand in for a platform we may already have listed.
_LINK_ALIASES = {"seatgeek_web": "seatgeek"}


def merge_key(listing: Listing) -> str:
    """Same city, same night, same show - whichever borough a platform filed it under."""
    return f"{canonical_city(listing.city) or 'unknown'}|{listing.local_date or 'tba'}"


@dataclass
class MergedEvent:
    """One show, as seen by every platform at once.

    Deliberately quacks like EventSnapshot so the alerting and notification
    code does not care which it is holding.
    """

    id: str
    sources: Dict[str, Listing] = field(default_factory=dict)

    # ---------------------------------------------------------------- #
    # construction
    # ---------------------------------------------------------------- #
    @classmethod
    def from_listings(cls, listings: Sequence[Listing]) -> "MergedEvent":
        event = cls(id=merge_key(listings[0]))
        for listing in listings:
            event.add(listing)
        return event

    def add(self, listing: Listing) -> None:
        existing = self.sources.get(listing.platform)
        # Keep whichever entry knows more: a price beats no price.
        if existing is None or (listing.has_price and not existing.has_price):
            self.sources[listing.platform] = listing

    # ---------------------------------------------------------------- #
    # identity
    # ---------------------------------------------------------------- #
    @property
    def primary(self) -> Listing:
        """The listing we describe the show from: the richest one available."""
        ordered = sorted(
            self.sources.values(),
            key=lambda l: (0 if l.venue else 1, 0 if l.platform == "ticketmaster" else 1),
        )
        return ordered[0]

    @property
    def name(self) -> str:
        primary = self.primary
        return primary.artist or primary.title or "Unknown event"

    @property
    def artist(self) -> str:
        return self.primary.artist

    @property
    def city(self) -> str:
        return next((l.city for l in self.sources.values() if l.city), "")

    @property
    def venue(self) -> str:
        return next((l.venue for l in self.sources.values() if l.venue), "")

    @property
    def region(self) -> str:
        return next((l.region for l in self.sources.values() if l.region), "")

    @property
    def country(self) -> str:
        return next((l.country for l in self.sources.values() if l.country), "")

    @property
    def local_date(self) -> str:
        return next((l.local_date for l in self.sources.values() if l.local_date), "")

    @property
    def local_time(self) -> str:
        return next((l.local_time for l in self.sources.values() if l.local_time), "")

    @property
    def when(self) -> str:
        if not self.local_date:
            return "date TBA"
        return f"{self.local_date} {self.local_time[:5]}" if self.local_time else self.local_date

    @property
    def where(self) -> str:
        return ", ".join(p for p in (self.venue, self.city, self.region) if p) or "venue TBA"

    @property
    def platforms(self) -> List[str]:
        return sorted(self.sources)

    # ---------------------------------------------------------------- #
    # availability
    # ---------------------------------------------------------------- #
    def availability(self, now: Optional[datetime] = None) -> str:
        if not self.sources:
            return UNKNOWN
        if all(l.availability == CANCELLED for l in self.sources.values()):
            return CANCELLED
        best = min(self.sources.values(), key=lambda l: _RANK_INDEX.get(l.availability, len(_RANK)))
        if best.availability == ON_SALE and self.low_stock:
            return "few_left"
        return best.availability

    def buyable(self, now: Optional[datetime] = None) -> bool:
        return any(l.buyable for l in self.sources.values())

    @property
    def low_stock(self) -> bool:
        return any((l.inventory_status or "") in LOW_STOCK_STATUSES for l in self.sources.values())

    @property
    def inventory_status(self) -> Optional[str]:
        for listing in self.sources.values():
            if listing.inventory_status:
                return listing.inventory_status
        return None

    @property
    def presale_label(self) -> str:
        for listing in self.sources.values():
            if listing.availability == PRESALE:
                return f"{listing.platform} presale"
        return ""

    @property
    def ticket_limit(self) -> str:
        for listing in self.sources.values():
            if listing.note and "limit" in listing.note.lower():
                return listing.note
        return ""

    def next_sale_start(self, now: Optional[datetime] = None) -> Optional[datetime]:
        now = now or now_utc()
        upcoming = [
            start
            for start in (parse_iso(l.on_sale_at) for l in self.sources.values())
            if start and start > now
        ]
        return min(upcoming) if upcoming else None

    # ---------------------------------------------------------------- #
    # price
    # ---------------------------------------------------------------- #
    @property
    def quotes(self) -> List[Listing]:
        """Every platform that actually told us a price, cheapest first."""
        priced = [l for l in self.sources.values() if l.has_price]
        return sorted(priced, key=lambda l: l.price_min)

    @property
    def mixed_currency(self) -> bool:
        return len({l.currency for l in self.quotes if l.currency}) > 1

    @property
    def cheapest(self) -> Optional[Listing]:
        quotes = self.quotes
        return quotes[0] if quotes else None

    @property
    def price_min(self) -> Optional[float]:
        cheapest = self.cheapest
        return cheapest.price_min if cheapest else None

    @property
    def price_max(self) -> Optional[float]:
        prices = [l.price_max for l in self.sources.values() if l.price_max is not None]
        return max(prices) if prices else None

    @property
    def currency(self) -> str:
        cheapest = self.cheapest
        return cheapest.currency if cheapest else ""

    @property
    def price_range(self) -> str:
        """What the cheapest ticket costs, and who is selling it that cheaply."""
        cheapest = self.cheapest
        if not cheapest:
            return ""
        label = f"{cheapest.price_label()} on {cheapest.platform_label}"
        if not cheapest.fees_included:
            label += " (+fees)"
        return label

    def savings_against(self, other: Optional["MergedEvent"]) -> Optional[float]:
        """How much cheaper we got since a previous look, in the same currency."""
        if other is None or self.price_min is None or other.price_min is None:
            return None
        if self.currency != other.currency:
            return None
        drop = other.price_min - self.price_min
        return drop if drop > 0 else None

    # ---------------------------------------------------------------- #
    # links
    # ---------------------------------------------------------------- #
    @property
    def url(self) -> str:
        cheapest = self.cheapest
        if cheapest and cheapest.url:
            return cheapest.url
        for platform in ("ticketmaster", "seatgeek", "bandsintown"):
            listing = self.sources.get(platform)
            if listing and listing.url:
                return listing.url
        return next((l.url for l in self.sources.values() if l.url), "")

    def links(self) -> List[Dict[str, str]]:
        """Every place you could buy this, plus searches for the walled gardens."""
        out = [
            {"id": l.platform, "label": l.platform_label, "url": l.url, "price": l.price_label()}
            for l in sorted(self.sources.values(), key=lambda l: (l.price_min is None, l.price_min or 0))
            if l.url
        ]
        have = {entry["id"] for entry in out}
        for link in search_links(self.artist or self.name, self.city):
            # A real listing beats a search link for the same marketplace.
            if _LINK_ALIASES.get(link["id"], link["id"]) not in have:
                out.append({**link, "price": ""})
        return out

    # ---------------------------------------------------------------- #
    # serialisation
    # ---------------------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "sources": {name: l.to_dict() for name, l in self.sources.items()}}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MergedEvent":
        event = cls(id=str(data.get("id") or ""))
        for name, raw in (data.get("sources") or {}).items():
            if isinstance(raw, dict):
                event.sources[str(name)] = Listing.from_dict(raw)
        return event

    # ---------------------------------------------------------------- #
    def describe(self, now: Optional[datetime] = None) -> str:
        now = now or now_utc()
        parts = [f"{self.name} - {self.when} - {self.where}", f"[{self.availability(now)}]"]
        if self.price_range:
            parts.append(f"from {self.price_range}")
        if len(self.platforms) > 1:
            parts.append(f"({len(self.platforms)} platforms)")
        upcoming = self.next_sale_start(now)
        if upcoming and not self.buyable(now):
            parts.append(f"on sale {upcoming.strftime('%Y-%m-%d %H:%M UTC')} ({human_delta(upcoming - now)})")
        return " ".join(parts)


def merge_listings(listings: Iterable[Listing]) -> List[MergedEvent]:
    """Group listings from every platform into one entry per show."""
    buckets: Dict[str, List[Listing]] = {}
    for listing in listings:
        buckets.setdefault(merge_key(listing), []).append(listing)
    events = [MergedEvent.from_listings(group) for group in buckets.values()]
    return sorted(events, key=lambda e: (e.local_date or "9999", e.local_time or ""))
