from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tests.support import event_payload
from ticketwatch.events import EventSnapshot, human_delta, parse_iso

BEFORE_SALE = datetime(2026, 1, 1, tzinfo=timezone.utc)
DURING_SALE = datetime(2026, 5, 1, tzinfo=timezone.utc)
AFTER_SALE = datetime(2026, 12, 1, tzinfo=timezone.utc)


class ParsingTests(unittest.TestCase):
    def test_parses_zulu_timestamps(self):
        self.assertEqual(
            parse_iso("2026-10-25T23:00:00Z"),
            datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc),
        )

    def test_parses_offset_timestamps_into_utc(self):
        self.assertEqual(
            parse_iso("2026-10-25T19:00:00-04:00"),
            datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc),
        )

    def test_bad_values_are_none(self):
        for value in (None, "", "not a date", 12345):
            self.assertIsNone(parse_iso(value))

    def test_from_api_flattens_the_payload(self):
        event = EventSnapshot.from_api(event_payload())
        self.assertEqual(event.id, "G5vYZ9abc123")
        self.assertEqual(event.name, "Sienna Spiro")
        self.assertEqual(event.venue, "History")
        self.assertEqual(event.city, "Toronto")
        self.assertEqual(event.region, "ON")
        self.assertEqual(event.country, "CA")
        self.assertEqual(event.local_date, "2026-10-25")
        self.assertEqual(event.status_code, "onsale")
        self.assertEqual(event.price_min, 59.5)
        self.assertEqual(event.price_max, 149.0)
        self.assertEqual(event.currency, "CAD")
        self.assertEqual(event.attractions, ["Sienna Spiro"])
        self.assertEqual(event.attraction_ids, ["K8vZ917qxR7"])
        self.assertIn("4 ticket limit", event.ticket_limit)

    def test_from_api_survives_a_sparse_payload(self):
        event = EventSnapshot.from_api({"id": "X1", "name": "Mystery show"})
        self.assertEqual(event.id, "X1")
        self.assertEqual(event.venue, "")
        self.assertEqual(event.when, "date TBA")
        self.assertEqual(event.where, "venue TBA")
        self.assertEqual(event.price_range, "")

    def test_round_trips_through_a_dict(self):
        original = EventSnapshot.from_api(
            event_payload(presales=[{"name": "Artist Presale", "startDateTime": "2026-02-10T15:00:00Z",
                                     "endDateTime": "2026-02-14T14:00:00Z"}])
        )
        restored = EventSnapshot.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(restored.presales[0].name, "Artist Presale")

    def test_from_dict_ignores_unknown_fields(self):
        data = EventSnapshot.from_api(event_payload()).to_dict()
        data["something_new_ticketmaster_added"] = True
        self.assertEqual(EventSnapshot.from_dict(data).id, "G5vYZ9abc123")


class AvailabilityTests(unittest.TestCase):
    def snapshot(self, **kwargs) -> EventSnapshot:
        return EventSnapshot.from_api(event_payload(**kwargs))

    def test_before_the_public_onsale_it_is_scheduled(self):
        event = self.snapshot()
        self.assertEqual(event.availability(BEFORE_SALE), "scheduled")
        self.assertFalse(event.buyable(BEFORE_SALE))

    def test_inside_the_window_it_is_on_sale(self):
        event = self.snapshot()
        self.assertEqual(event.availability(DURING_SALE), "on_sale")
        self.assertTrue(event.buyable(DURING_SALE))

    def test_after_the_window_closes_it_is_not_buyable(self):
        event = self.snapshot()
        self.assertFalse(event.buyable(AFTER_SALE))

    def test_an_active_presale_counts_as_buyable(self):
        event = self.snapshot(
            presales=[{"name": "Artist Presale", "startDateTime": "2025-12-20T15:00:00Z",
                       "endDateTime": "2026-02-14T14:00:00Z"}]
        )
        self.assertTrue(event.buyable(BEFORE_SALE))
        self.assertEqual(event.availability(BEFORE_SALE), "presale")

    def test_a_finished_presale_does_not(self):
        event = self.snapshot(
            presales=[{"name": "Artist Presale", "startDateTime": "2025-11-01T15:00:00Z",
                       "endDateTime": "2025-12-01T14:00:00Z"}]
        )
        self.assertFalse(event.buyable(BEFORE_SALE))

    def test_inventory_status_overrides_the_sale_window(self):
        event = self.snapshot()
        event.inventory_status = "SOLD_OUT"
        self.assertFalse(event.buyable(DURING_SALE))
        self.assertEqual(event.availability(DURING_SALE), "sold_out")

    def test_few_tickets_left_is_still_buyable(self):
        event = self.snapshot()
        event.inventory_status = "FEW_TICKETS_LEFT"
        self.assertTrue(event.buyable(DURING_SALE))
        self.assertEqual(event.availability(DURING_SALE), "few_left")

    def test_inventory_available_beats_an_offsale_status(self):
        event = self.snapshot(status="offsale")
        event.inventory_status = "AVAILABLE"
        self.assertTrue(event.buyable(DURING_SALE))

    def test_cancelled_is_never_buyable(self):
        event = self.snapshot(status="cancelled")
        event.inventory_status = "AVAILABLE"
        self.assertFalse(event.buyable(DURING_SALE))
        self.assertEqual(event.availability(DURING_SALE), "cancelled")

    def test_offsale_without_inventory_data_is_not_buyable(self):
        event = self.snapshot(status="offsale")
        self.assertFalse(event.buyable(DURING_SALE))
        self.assertEqual(event.availability(DURING_SALE), "offsale")

    def test_onsale_with_no_published_dates_is_trusted(self):
        event = self.snapshot(public_start=None, public_end=None)
        self.assertTrue(event.buyable(DURING_SALE))

    def test_next_sale_start_prefers_the_soonest(self):
        event = self.snapshot(
            presales=[{"name": "Presale", "startDateTime": "2026-02-10T15:00:00Z",
                       "endDateTime": "2026-02-14T14:00:00Z"}]
        )
        self.assertEqual(
            event.next_sale_start(BEFORE_SALE),
            datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
        )

    def test_no_next_sale_start_once_everything_has_begun(self):
        self.assertIsNone(self.snapshot().next_sale_start(DURING_SALE))


class PresentationTests(unittest.TestCase):
    def test_describe_mentions_the_countdown_when_not_yet_on_sale(self):
        event = EventSnapshot.from_api(event_payload())
        text = event.describe(BEFORE_SALE)
        self.assertIn("Sienna Spiro", text)
        self.assertIn("History, Toronto, ON", text)
        self.assertIn("[scheduled]", text)
        self.assertIn("on sale 2026-02-14", text)

    def test_price_range_formatting(self):
        self.assertEqual(EventSnapshot.from_api(event_payload()).price_range, "59.50-149.00 CAD")
        single = EventSnapshot.from_api(event_payload(price_min=80.0, price_max=80.0))
        self.assertEqual(single.price_range, "80.00 CAD")

    def test_human_delta(self):
        self.assertEqual(human_delta(timedelta(days=3, hours=2)), "in 3d 2h")
        self.assertEqual(human_delta(timedelta(hours=2, minutes=5)), "in 2h 5m")
        self.assertEqual(human_delta(timedelta(minutes=7)), "in 7m")
        self.assertEqual(human_delta(timedelta(seconds=5)), "in under a minute")
        self.assertEqual(human_delta(timedelta(seconds=-5)), "now")


if __name__ == "__main__":
    unittest.main()
