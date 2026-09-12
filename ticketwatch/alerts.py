"""What changed since the last poll, and is it worth waking someone up for."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence

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
ERROR = "error"

URGENT_KINDS = {NEW_EVENT, ON_SALE, PRESALE, BACK_IN_STOCK, LOW_INVENTORY}

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
    ERROR: "Ticket monitor problem",
}


@dataclass
class Alert:
    kind: str
    title: str
    body: str
    event: Optional[EventSnapshot] = None
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

    snapshot: EventSnapshot
    first_seen: str = ""
    last_seen: str = ""
    last_alert_at: Optional[str] = None
    last_alert_kind: Optional[str] = None


def _headline(kind: str, event: Optional[EventSnapshot], artist: str = "") -> str:
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


def _body(event: EventSnapshot, now: datetime) -> str:
    lines = [
        f"{event.name}",
        f"{event.when} - {event.where}",
        f"Status: {event.availability(now)}"
        + (f" (Ticketmaster: {event.inventory_status})" if event.inventory_status else ""),
    ]
    if event.price_range:
        lines.append(f"Price range: {event.price_range}")
    if event.ticket_limit:
        lines.append(f"Ticket limit: {event.ticket_limit}")
    upcoming = event.next_sale_start(now)
    if upcoming and not event.buyable(now):
        lines.append(f"Sale starts {upcoming.strftime('%Y-%m-%d %H:%M UTC')} ({human_delta(upcoming - now)})")
    presale = event.active_presale(now)
    if presale:
        lines.append(f"Presale running: {presale.name}")
    return "\n".join(lines)


def make_alert(kind: str, event: Optional[EventSnapshot], now: datetime, artist: str = "", repeat: bool = False) -> Alert:
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

        if kind:
            alerts.append(make_alert(kind, event, now, artist))
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


def _prices_changed(before: EventSnapshot, after: EventSnapshot) -> bool:
    return (before.price_min, before.price_max) != (after.price_min, after.price_max)


def _parse(value: Optional[str]) -> Optional[datetime]:
    return parse_iso(value)
