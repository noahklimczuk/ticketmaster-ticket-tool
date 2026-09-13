from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests.support import event_payload
from ticketwatch import alerts
from ticketwatch.alerts import EventRecord, detect_changes
from ticketwatch.events import EventSnapshot

BEFORE_SALE = datetime(2026, 1, 1, tzinfo=timezone.utc)
DURING_SALE = datetime(2026, 5, 1, tzinfo=timezone.utc)


def snap(**kwargs) -> EventSnapshot:
    return EventSnapshot.from_api(event_payload(**kwargs))


def record(snapshot: EventSnapshot, last_alert_at: str = None, seen: datetime = DURING_SALE) -> EventRecord:
    """A previous-poll record. `seen` is when we last looked, which is what the
    previous availability is evaluated against."""
    stamp = seen.isoformat()
    return EventRecord(snapshot=snapshot, first_seen=stamp, last_seen=stamp, last_alert_at=last_alert_at)


class DetectTests(unittest.TestCase):
    def test_first_sighting_is_a_new_event_alert(self):
        found = detect_changes({}, [snap()], now=BEFORE_SALE, artist="Sienna Spiro")
        self.assertEqual([a.kind for a in found], [alerts.NEW_EVENT])
        self.assertIn("New Sienna Spiro date found", found[0].title)
        self.assertIn("Toronto", found[0].title)

    def test_nothing_changed_means_no_alerts(self):
        event = snap()
        previous = {event.id: record(event, seen=BEFORE_SALE)}
        self.assertEqual(detect_changes(previous, [event], now=BEFORE_SALE), [])

    def test_the_sale_window_simply_opening_raises_on_sale(self):
        """The payload is identical - only the clock moved past the onsale time."""
        event = snap()
        previous = {event.id: record(event, seen=BEFORE_SALE)}
        found = detect_changes(previous, [event], now=DURING_SALE)
        self.assertEqual([a.kind for a in found], [alerts.ON_SALE])
        self.assertTrue(found[0].urgent)
        self.assertEqual(found[0].url, event.url)

    def test_sold_out_to_available_is_a_restock(self):
        before = snap()
        before.inventory_status = "SOLD_OUT"
        after = snap()
        after.inventory_status = "AVAILABLE"
        found = detect_changes({before.id: record(before)}, [after], now=DURING_SALE)
        self.assertEqual([a.kind for a in found], [alerts.BACK_IN_STOCK])
        self.assertTrue(found[0].urgent)

    def test_running_out_raises_low_inventory(self):
        before, after = snap(), snap()
        before.inventory_status = "AVAILABLE"
        after.inventory_status = "FEW_TICKETS_LEFT"
        found = detect_changes({before.id: record(before)}, [after], now=DURING_SALE)
        self.assertEqual([a.kind for a in found], [alerts.LOW_INVENTORY])

    def test_selling_out_is_reported_too(self):
        before, after = snap(), snap()
        before.inventory_status = "AVAILABLE"
        after.inventory_status = "SOLD_OUT"
        found = detect_changes({before.id: record(before)}, [after], now=DURING_SALE)
        self.assertEqual([a.kind for a in found], [alerts.SOLD_OUT])
        self.assertFalse(found[0].urgent)

    def test_cancellation_is_a_status_change(self):
        before = snap()
        after = snap(status="cancelled")
        found = detect_changes({before.id: record(before)}, [after], now=DURING_SALE)
        self.assertEqual([a.kind for a in found], [alerts.STATUS_CHANGE])

    def test_price_change_alone(self):
        before = snap()
        after = snap(price_min=45.0)
        found = detect_changes({before.id: record(before)}, [after], now=DURING_SALE,
                               enabled=["price_change", "on_sale"])
        self.assertEqual([a.kind for a in found], [alerts.PRICE_CHANGE])

    def test_a_vanished_event_is_reported_once(self):
        gone = snap()
        found = detect_changes({gone.id: record(gone)}, [], now=DURING_SALE, enabled=["gone"])
        self.assertEqual([a.kind for a in found], [alerts.GONE])

    def test_disabled_kinds_are_filtered_out(self):
        event = snap()
        found = detect_changes({}, [event], now=BEFORE_SALE, enabled=["on_sale"])
        self.assertEqual(found, [])

    def test_repeat_alerts_are_off_by_default(self):
        event = snap()
        previous = {event.id: record(event, last_alert_at="2026-05-01T00:00:00+00:00")}
        found = detect_changes(previous, [event], now=DURING_SALE)
        self.assertEqual(found, [])

    def test_repeat_alerts_fire_once_the_cooldown_passes(self):
        event = snap()
        previous = {event.id: record(event, last_alert_at="2026-04-30T23:00:00+00:00")}
        found = detect_changes(previous, [event], now=DURING_SALE, repeat_minutes=30)
        self.assertEqual([a.kind for a in found], [alerts.ON_SALE])
        self.assertTrue(found[0].repeat)
        self.assertIn("still available", found[0].title)

    def test_repeat_alerts_respect_the_cooldown(self):
        event = snap()
        previous = {event.id: record(event, last_alert_at="2026-04-30T23:50:00+00:00")}
        self.assertEqual(detect_changes(previous, [event], now=DURING_SALE, repeat_minutes=30), [])

    def test_repeat_never_fires_for_something_you_cannot_buy(self):
        event = snap(status="offsale")
        previous = {event.id: record(event, last_alert_at="2026-01-01T00:00:00+00:00")}
        self.assertEqual(detect_changes(previous, [event], now=DURING_SALE, repeat_minutes=1), [])

    def test_presale_opening_is_its_own_kind(self):
        before = snap()
        after = snap(presales=[{"name": "Artist Presale", "startDateTime": "2025-12-20T15:00:00Z",
                                "endDateTime": "2026-02-14T14:00:00Z"}])
        found = detect_changes({before.id: record(before, seen=BEFORE_SALE)}, [after], now=BEFORE_SALE)
        self.assertEqual([a.kind for a in found], [alerts.PRESALE])

    def test_alert_body_carries_the_useful_details(self):
        event = snap()
        event.inventory_status = "AVAILABLE"
        found = detect_changes({}, [event], now=DURING_SALE)
        body = found[0].body
        self.assertIn("History, Toronto, ON", body)
        self.assertIn("2026-10-25 19:00", body)
        self.assertIn("59.50-149.00 CAD", body)
        self.assertIn("AVAILABLE", body)
        self.assertIn("4 ticket limit", body)

    def test_headlines_do_not_repeat_the_artist_name(self):
        event = snap()
        new_event = detect_changes({}, [event], now=BEFORE_SALE, artist="Sienna Spiro")[0]
        self.assertEqual(new_event.title, "New Sienna Spiro date found: Toronto - 2026-10-25 19:00")

        on_sale = detect_changes(
            {event.id: record(event, seen=BEFORE_SALE)}, [event], now=DURING_SALE, artist="Sienna Spiro"
        )[0]
        self.assertEqual(on_sale.title, "TICKETS ON SALE: Sienna Spiro - Toronto - 2026-10-25 19:00")

    def test_the_body_compares_platforms_and_warns_about_currencies(self):
        from tests.support import listing, merged

        event = merged(listing(platform="ticketmaster", price_min=120.0, currency="CAD"),
                       listing(platform="seatgeek", price_min=95.0, currency="USD"))
        body = detect_changes({}, [event], now=DURING_SALE, artist="Sienna Spiro")[0].body
        self.assertIn("Cheapest: 95.00 USD on SeatGeek", body)
        self.assertIn("seatgeek: 95.00 USD", body)
        self.assertIn("ticketmaster: 120.00 CAD", body)
        self.assertIn("different currencies", body)
        self.assertIn("Listed on: seatgeek, ticketmaster", body)

    def test_error_alert_shape(self):
        alert = alerts.error_alert("boom")
        self.assertEqual(alert.kind, alerts.ERROR)
        self.assertFalse(alert.urgent)
        self.assertIn("boom", alert.as_text())


if __name__ == "__main__":
    unittest.main()
