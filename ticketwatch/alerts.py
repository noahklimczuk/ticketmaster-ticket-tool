"""What changed since the last poll, and is it worth waking someone up for."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .events import EventSnapshot, human_delta, now_utc, parse_iso
from .matcher import normalize

# Alert kinds (kept in sync with config.ALL_ALERTS).
NEW_EVENT = "new_event"
ON_SALE = "on_sale"
PRESALE = "presale"
BACK_IN_STOCK = "back_in_stock"
LOW_INVENTORY = "low_inventory"
SOLD_OUT = "sold_out"
STATUS_CHANGE = "status_change"
PRICE_CHANGE = "price_change"
GONE = "gone"
CHEAPER = "cheaper"
NEW_PLATFORM = "new_platform"
ERROR = "error"

URGENT_KINDS = {NEW_EVENT, ON_SALE, PRESALE, BACK_IN_STOCK, LOW_INVENTORY, CHEAPER}

# Availability labels meaning the tickets were previously out of reach, so a move
# to on-sale is a restock rather than a first onsale.
_WAS_GONE = {"sold_out", "unavailable", "offsale", "cancelled", "postponed", "rescheduled"}

_HEADLINES = {
    NEW_EVENT: "New Sienna Spiro date found",
    ON_SALE: "TICKETS ON SALE",
    PRESALE: "Presale is live",
    BACK_IN_STOCK: "TICKETS AVAILABLE AGAIN",
    LOW_INVENTORY: "Few tickets left",
    SOLD_OUT: "Sold out",
    STATUS_CHANGE: "Status changed",
    PRICE_CHANGE: "Price changed",
    GONE: "Event disappeared from results",
    CHEAPER: "Price dropped",
    NEW_PLATFORM: "Now listed somewhere new",
    ERROR: "Ticket monitor problem",
}


@dataclass
class Alert:
    kind: str
    title: str
    body: str
    event: Optional[Any] = None
    urgent: bool = False
    repeat: bool = False
    url: str = ""

    def as_text(self) -> str:
        lines = [self.title, self.body]
        if self.url:
            lines.append(self.url)
        return "\n".join(line for line in lines if line)


@dataclass
class EventRecord:
    """What we remembered about an event from the previous poll."""

    snapshot: Any
    first_seen: str = ""
    last_seen: str = ""
    last_alert_at: Optional[str] = None
    last_alert_kind: Optional[str] = None


def _headline(kind: str, event, artist: str = "") -> str:
    if kind == NEW_EVENT and artist:
        base = f"New {artist} date found"
    else:
        base = _HEADLINES.get(kind, kind.replace("_", " ").title())
    if event is None:
        return base
    # Don't say the artist's name twice when the headline already carries it.
    label = "" if artist and normalize(artist) in normalize(base) else event.name
    details = " - ".join(part for part in (label, event.city, event.when) if part)
    return f"{base}: {details}" if details else base


def _body(event, now: datetime) -> str:
    lines = [
        f"{event.name}",
        f"{event.when} - {event.where}",
        f"Status: {event.availability(now)}"
        + (f" (Ticketmaster: {event.inventory_status})" if event.inventory_status else ""),
    ]
    if event.price_range:
        lines.append(f"Cheapest: {event.price_range}")

    # One line per platform, cheapest first, so the comparison is right there.
    quotes = getattr(event, "quotes", [])
    if len(quotes) > 1:
        for listing in quotes:
            lines.append(f"  {listing.platform}: {listing.price_label()}")
    if getattr(event, "mixed_currency", False):
        lines.append("  (prices are in different currencies - compare carefully)")
    platforms = getattr(event, "platforms", [])
    if platforms:
        lines.append(f"Listed on: {', '.join(platforms)}")

    if event.ticket_limit:
        lines.append(f"Ticket limit: {event.ticket_limit}")
    upcoming = event.next_sale_start(now)
    if upcoming and not event.buyable(now):
        lines.append(f"Sale starts {upcoming.strftime('%Y-%m-%d %H:%M UTC')} ({human_delta(upcoming - now)})")
    if event.presale_label:
        lines.append(f"Presale running: {event.presale_label}")
    return "\n".join(lines)


def make_alert(kind: str, event, now: datetime, artist: str = "", repeat: bool = False) -> Alert:
    body = _body(event, now) if event else ""
    title = _headline(kind, event, artist)
    if repeat:
        title = f"{title} - still available"
    return Alert(
        kind=kind,
        title=title,
        body=body,
        event=event,
        urgent=kind in URGENT_KINDS,
        repeat=repeat,
        url=event.url if event else "",
    )


def error_alert(message: str) -> Alert:
    return Alert(kind=ERROR, title=_HEADLINES[ERROR], body=message, urgent=False)


def _transition_kind(previous: str, current: str, event: EventSnapshot, now: datetime) -> Optional[str]:
    """Name the transition between two availability labels."""
    if previous == current:
        return None
    if current == "sold_out":
        return SOLD_OUT
    if current == "few_left":
        return LOW_INVENTORY
    if current in ("on_sale", "presale"):
        if previous in _WAS_GONE:
            return BACK_IN_STOCK
        return ON_SALE if current == "on_sale" else PRESALE
    return STATUS_CHANGE


def detect_changes(
    previous: Dict[str, EventRecord],
    current: Sequence[EventSnapshot],
    now: Optional[datetime] = None,
    enabled: Optional[Iterable[str]] = None,
    repeat_minutes: int = 0,
    artist: str = "",
    price_drop_percent: float = 5.0,
) -> List[Alert]:
    """Compare this poll against the last one and produce alerts.

    The previous availability is recomputed as of the moment that snapshot was
    last seen, not as of now. That way a sale window that simply opened - same
    payload, later clock - still registers as a real transition.
    """
    now = now or now_utc()
    enabled_set = set(enabled) if enabled is not None else None
    alerts: List[Alert] = []
    seen_ids = set()

    for event in current:
        seen_ids.add(event.id)
        record = previous.get(event.id)
        if record is None:
            alerts.append(make_alert(NEW_EVENT, event, now, artist))
            continue

        seen_at = _parse(record.last_seen) or now
        before = record.snapshot.availability(seen_at)
        after = event.availability(now)
        kind = _transition_kind(before, after, event, now)

        # Only consume the event for an alert the user actually wants; an
        # unwanted kind must not mask a price drop in the same round.
        if kind and _wanted(enabled_set, kind):
            alerts.append(make_alert(kind, event, now, artist))
            continue

        cheaper = _price_drop(record.snapshot, event, price_drop_percent)
        # A drop is also a price change; if the user only asked for the generic
        # kind, report it as that rather than swallowing it entirely.
        drop_kind = _first_wanted(enabled_set, CHEAPER, PRICE_CHANGE)
        if cheaper and drop_kind:
            alert = make_alert(drop_kind, event, now, artist)
            alert.body = f"Now {cheaper} cheaper than when we last looked.\n\n" + alert.body
            alerts.append(alert)
            continue

        fresh = _new_platforms(record.snapshot, event)
        if fresh and _wanted(enabled_set, NEW_PLATFORM):
            alert = make_alert(NEW_PLATFORM, event, now, artist)
            alert.body = f"Newly listed on: {', '.join(fresh)}\n\n" + alert.body
            alert.urgent = event.buyable(now)
            alerts.append(alert)
            continue

        if _prices_changed(record.snapshot, event):
            alerts.append(make_alert(PRICE_CHANGE, event, now, artist))
            continue

        if repeat_minutes and event.buyable(now):
            last = _parse(record.last_alert_at)
            if last is None or now - last >= timedelta(minutes=repeat_minutes):
                kind = LOW_INVENTORY if event.low_stock else (PRESALE if after == "presale" else ON_SALE)
                alerts.append(make_alert(kind, event, now, artist, repeat=True))

    for event_id, record in previous.items():
        if event_id not in seen_ids:
            alerts.append(make_alert(GONE, record.snapshot, now, artist))

    if enabled_set is not None:
        alerts = [a for a in alerts if a.kind in enabled_set]
    return alerts


def _wanted(enabled_set: Optional[set], kind: str) -> bool:
    return enabled_set is None or kind in enabled_set


def _first_wanted(enabled_set: Optional[set], *kinds: str) -> Optional[str]:
    """The most specific enabled kind, or None if the user wants none of them."""
    for kind in kinds:
        if _wanted(enabled_set, kind):
            return kind
    return None


def _prices_changed(before, after) -> bool:
    return (before.price_min, before.price_max) != (after.price_min, after.price_max)


def _price_drop(before, after, percent: float) -> Optional[str]:
    """A meaningful drop in the cheapest price, described for humans."""
    old, new = before.price_min, after.price_min
    if old is None or new is None or new >= old:
        return None
    if getattr(before, "currency", "") != getattr(after, "currency", ""):
        return None  # not comparable
    drop = old - new
    if percent > 0 and drop < old * (percent / 100.0):
        return None
    currency = f" {after.currency}" if after.currency else ""
    return f"{drop:.2f}{currency} ({drop / old * 100:.0f}%)"


def _new_platforms(before, after) -> List[str]:
    return sorted(set(getattr(after, "platforms", [])) - set(getattr(before, "platforms", [])))


def _parse(value: Optional[str]) -> Optional[datetime]:
    return parse_iso(value)
