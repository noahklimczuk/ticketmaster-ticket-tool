"""Turn a raw Discovery API event into something we can compare over time."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Inventory-status values that mean "there is stock on the primary market".
INVENTORY_AVAILABLE = {"AVAILABLE", "FEW_TICKETS_LEFT", "LIMITED_AVAILABILITY"}
INVENTORY_GONE = {"SOLD_OUT", "CANCELLED", "TICKETS_NOT_AVAILABLE", "OFFSALE", "NOT_AVAILABLE"}
LOW_INVENTORY = {"FEW_TICKETS_LEFT", "LIMITED_AVAILABILITY"}

# dates.status.code values.
STATUS_ONSALE = "onsale"
DEAD_STATUSES = {"cancelled", "canceled"}


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse the UTC timestamps Discovery returns ('2026-10-25T23:00:00Z')."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Presale:
    name: str = ""
    start: Optional[str] = None
    end: Optional[str] = None

    def active(self, now: datetime) -> bool:
        start, end = parse_iso(self.start), parse_iso(self.end)
        if start and now < start:
            return False
        if end and now > end:
            return False
        return bool(start or end)


@dataclass
class EventSnapshot:
    """A flattened event, safe to serialise into the state file."""

    id: str
    name: str = ""
    url: str = ""
    venue: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    local_date: str = ""
    local_time: str = ""
    timezone_name: str = ""
    status_code: str = ""
    public_start: Optional[str] = None
    public_end: Optional[str] = None
    public_tbd: bool = False
    presales: List[Presale] = field(default_factory=list)
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    currency: str = ""
    inventory_status: Optional[str] = None
    attractions: List[str] = field(default_factory=list)
    attraction_ids: List[str] = field(default_factory=list)
    seatmap_url: str = ""
    ticket_limit: str = ""

    # ---------------------------------------------------------------- #
    # construction
    # ---------------------------------------------------------------- #
    @classmethod
    def from_api(cls, payload: Dict[str, Any], inventory_status: Optional[str] = None) -> "EventSnapshot":
        embedded = payload.get("_embedded") or {}
        venues = embedded.get("venues") or []
        venue = venues[0] if venues and isinstance(venues[0], dict) else {}
        attractions = [a for a in (embedded.get("attractions") or []) if isinstance(a, dict)]

        dates = payload.get("dates") or {}
        start = dates.get("start") or {}
        sales = payload.get("sales") or {}
        public = sales.get("public") or {}

        prices = [p for p in (payload.get("priceRanges") or []) if isinstance(p, dict)]
        price_min = min((p.get("min") for p in prices if isinstance(p.get("min"), (int, float))), default=None)
        price_max = max((p.get("max") for p in prices if isinstance(p.get("max"), (int, float))), default=None)
        currency = next((p.get("currency", "") for p in prices if p.get("currency")), "")

        return cls(
            id=str(payload.get("id") or ""),
            name=str(payload.get("name") or ""),
            url=str(payload.get("url") or ""),
            venue=str(venue.get("name") or ""),
            city=str(((venue.get("city") or {}).get("name")) or ""),
            region=str(((venue.get("state") or {}).get("stateCode")) or ((venue.get("state") or {}).get("name")) or ""),
            country=str(((venue.get("country") or {}).get("countryCode")) or ""),
            local_date=str(start.get("localDate") or ""),
            local_time=str(start.get("localTime") or ""),
            timezone_name=str(dates.get("timezone") or ""),
            status_code=str(((dates.get("status") or {}).get("code")) or "").lower(),
            public_start=public.get("startDateTime"),
            public_end=public.get("endDateTime"),
            public_tbd=bool(public.get("startTBD") or public.get("startTBA")),
            presales=[
                Presale(
                    name=str(p.get("name") or "Presale"),
                    start=p.get("startDateTime"),
                    end=p.get("endDateTime"),
                )
                for p in (sales.get("presales") or [])
                if isinstance(p, dict)
            ],
            price_min=price_min,
            price_max=price_max,
            currency=currency,
            inventory_status=(inventory_status or None),
            attractions=[str(a.get("name") or "") for a in attractions],
            attraction_ids=[str(a.get("id") or "") for a in attractions],
            seatmap_url=str(((payload.get("seatmap") or {}).get("staticUrl")) or ""),
            ticket_limit=str(((payload.get("ticketLimit") or {}).get("info")) or ""),
        )

    # ---------------------------------------------------------------- #
    # serialisation
    # ---------------------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventSnapshot":
        data = dict(data)
        presales = data.pop("presales", []) or []
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in known}
        snapshot = cls(**clean)
        snapshot.presales = [
            Presale(**{k: v for k, v in p.items() if k in Presale.__dataclass_fields__})  # type: ignore[attr-defined]
            for p in presales
            if isinstance(p, dict)
        ]
        return snapshot

    # ---------------------------------------------------------------- #
    # availability
    # ---------------------------------------------------------------- #
    def public_window(self, now: Optional[datetime] = None) -> str:
        """'open' | 'scheduled' | 'closed' | 'unknown'."""
        now = now or now_utc()
        start, end = parse_iso(self.public_start), parse_iso(self.public_end)
        if start and now < start:
            return "scheduled"
        if end and now > end:
            return "closed"
        if start or end:
            return "open"
        return "unknown"

    def active_presale(self, now: Optional[datetime] = None) -> Optional[Presale]:
        now = now or now_utc()
        for presale in self.presales:
            if presale.active(now):
                return presale
        return None

    def next_sale_start(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """Soonest upcoming sale start (public or presale), if any."""
        now = now or now_utc()
        upcoming = []
        public_start = parse_iso(self.public_start)
        if public_start and public_start > now:
            upcoming.append(public_start)
        for presale in self.presales:
            start = parse_iso(presale.start)
            if start and start > now:
                upcoming.append(start)
        return min(upcoming) if upcoming else None

    @property
    def inventory_known(self) -> bool:
        return bool(self.inventory_status)

    @property
    def sold_out(self) -> bool:
        return (self.inventory_status or "") in INVENTORY_GONE

    @property
    def low_stock(self) -> bool:
        return (self.inventory_status or "") in LOW_INVENTORY

    def buyable(self, now: Optional[datetime] = None) -> bool:
        """Can someone put these in a cart right this second?"""
        now = now or now_utc()
        if self.status_code in DEAD_STATUSES:
            return False
        if self.inventory_known:
            # The inventory endpoint is authoritative when we have it.
            return self.inventory_status in INVENTORY_AVAILABLE
        if self.active_presale(now):
            return True
        window = self.public_window(now)
        if window in ("closed", "scheduled"):
            return False
        if self.status_code and self.status_code != STATUS_ONSALE:
            return False  # offsale / postponed / rescheduled
        if window == "open":
            return True
        # No sale dates published at all: trust Ticketmaster's own status code.
        return self.status_code == STATUS_ONSALE

    def availability(self, now: Optional[datetime] = None) -> str:
        """A short machine-comparable label used for change detection."""
        now = now or now_utc()
        if self.status_code in DEAD_STATUSES:
            return "cancelled"
        if self.sold_out:
            return "sold_out"
        if self.low_stock:
            return "few_left"
        if self.buyable(now):
            return "on_sale" if self.public_window(now) != "scheduled" else "presale"
        if self.active_presale(now):
            return "presale"
        if self.public_window(now) == "scheduled" or self.public_tbd:
            return "scheduled"
        if self.status_code and self.status_code != STATUS_ONSALE:
            return self.status_code  # offsale, postponed, rescheduled...
        return "unavailable"

    # ---------------------------------------------------------------- #
    # presentation
    # ---------------------------------------------------------------- #
    @property
    def when(self) -> str:
        if not self.local_date:
            return "date TBA"
        if self.local_time:
            return f"{self.local_date} {self.local_time[:5]}"
        return self.local_date

    @property
    def where(self) -> str:
        bits = [b for b in (self.venue, self.city, self.region) if b]
        return ", ".join(bits) if bits else "venue TBA"

    @property
    def price_range(self) -> str:
        if self.price_min is None and self.price_max is None:
            return ""
        currency = f" {self.currency}" if self.currency else ""
        if self.price_min is not None and self.price_max is not None and self.price_min != self.price_max:
            return f"{self.price_min:.2f}-{self.price_max:.2f}{currency}"
        value = self.price_min if self.price_min is not None else self.price_max
        return f"{value:.2f}{currency}"

    def describe(self, now: Optional[datetime] = None) -> str:
        now = now or now_utc()
        parts = [f"{self.name} - {self.when} - {self.where}", f"[{self.availability(now)}]"]
        if self.price_range:
            parts.append(f"from {self.price_range}")
        upcoming = self.next_sale_start(now)
        if upcoming and not self.buyable(now):
            parts.append(f"on sale {upcoming.strftime('%Y-%m-%d %H:%M UTC')} ({human_delta(upcoming - now)})")
        return " ".join(parts)

    def compare_key(self, now: Optional[datetime] = None) -> Tuple[str, str, str, Optional[float], Optional[float]]:
        """Fields whose change is worth noticing."""
        now = now or now_utc()
        return (
            self.availability(now),
            self.status_code,
            self.inventory_status or "",
            self.price_min,
            self.price_max,
        )


def human_delta(delta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "now"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"in {days}d {hours}h"
    if hours:
        return f"in {hours}h {minutes}m"
    if minutes:
        return f"in {minutes}m"
    return "in under a minute"
