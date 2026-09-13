from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests.support import listing, merged
from ticketwatch.aggregate import MergedEvent, merge_key, merge_listings

NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


class MergeTests(unittest.TestCase):
    def test_the_same_night_in_the_same_city_is_one_show(self):
        events = merge_listings([
            listing(platform="ticketmaster"),
            listing(platform="seatgeek", venue="History Toronto"),
            listing(platform="bandsintown", venue=""),
        ])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].platforms, ["bandsintown", "seatgeek", "ticketmaster"])

    def test_different_nights_stay_apart(self):
        events = merge_listings([listing(local_date="2026-10-25"), listing(local_date="2026-11-02")])
        self.assertEqual(len(events), 2)

    def test_different_cities_stay_apart(self):
        events = merge_listings([listing(city="Toronto"), listing(city="Montreal")])
        self.assertEqual(len(events), 2)

    def test_boroughs_merge_with_their_city(self):
        self.assertEqual(merge_key(listing(city="North York")), merge_key(listing(city="Toronto")))

    def test_results_come_back_in_date_order(self):
        events = merge_listings([
            listing(local_date="2026-12-01"), listing(local_date="2026-10-25"), listing(local_date="2026-11-02"),
        ])
        self.assertEqual([e.local_date for e in events], ["2026-10-25", "2026-11-02", "2026-12-01"])

    def test_details_are_taken_from_whichever_platform_has_them(self):
        event = merged(
            listing(platform="bandsintown", venue="", price_min=None, price_max=None),
            listing(platform="ticketmaster", venue="History"),
        )
        self.assertEqual(event.venue, "History")
        self.assertEqual(event.where, "History, Toronto, ON")

    def test_a_priced_listing_beats_an_unpriced_one_from_the_same_platform(self):
        event = MergedEvent(id="x")
        event.add(listing(platform="seatgeek", price_min=None, price_max=None))
        event.add(listing(platform="seatgeek", price_min=42.0))
        self.assertEqual(event.sources["seatgeek"].price_min, 42.0)


class CheapestTests(unittest.TestCase):
    def test_it_finds_the_cheapest_platform(self):
        event = merged(
            listing(platform="ticketmaster", price_min=120.0),
            listing(platform="seatgeek", price_min=88.0),
        )
        self.assertEqual(event.cheapest.platform, "seatgeek")
        self.assertEqual(event.price_min, 88.0)
        self.assertIn("88.00 CAD on SeatGeek", event.price_range)

    def test_quotes_are_ordered_cheapest_first(self):
        event = merged(
            listing(platform="ticketmaster", price_min=120.0),
            listing(platform="seatgeek", price_min=88.0),
        )
        self.assertEqual([q.platform for q in event.quotes], ["seatgeek", "ticketmaster"])

    def test_platforms_without_a_price_are_not_quoted(self):
        event = merged(listing(platform="ticketmaster", price_min=99.0),
                       listing(platform="bandsintown", price_min=None, price_max=None))
        self.assertEqual([q.platform for q in event.quotes], ["ticketmaster"])

    def test_no_prices_anywhere(self):
        event = merged(listing(platform="bandsintown", price_min=None, price_max=None))
        self.assertIsNone(event.cheapest)
        self.assertEqual(event.price_range, "")

    def test_mixed_currencies_are_flagged_rather_than_silently_compared(self):
        event = merged(
            listing(platform="ticketmaster", price_min=120.0, currency="CAD"),
            listing(platform="seatgeek", price_min=95.0, currency="USD"),
        )
        self.assertTrue(event.mixed_currency)
        # The price label stays short; the caveat travels with the alert.
        self.assertEqual(event.price_range, "95.00 USD on SeatGeek (+fees)")

    def test_one_currency_is_not_flagged(self):
        event = merged(listing(platform="ticketmaster", price_min=120.0, currency="CAD"),
                       listing(platform="seatgeek", price_min=95.0, currency="CAD"))
        self.assertFalse(event.mixed_currency)

    def test_savings_are_only_reported_within_one_currency(self):
        dearer = merged(listing(price_min=120.0, currency="CAD"))
        cheaper = merged(listing(price_min=90.0, currency="CAD"))
        self.assertEqual(cheaper.savings_against(dearer), 30.0)
        self.assertIsNone(cheaper.savings_against(merged(listing(price_min=120.0, currency="USD"))))
        self.assertIsNone(dearer.savings_against(cheaper))  # it went up, not down


class AvailabilityTests(unittest.TestCase):
    def test_one_platform_with_stock_makes_the_show_buyable(self):
        event = merged(listing(platform="ticketmaster", availability="sold_out"),
                       listing(platform="seatgeek", availability="on_sale"))
        self.assertTrue(event.buyable(NOW))
        self.assertEqual(event.availability(NOW), "on_sale")

    def test_sold_out_everywhere(self):
        event = merged(listing(platform="ticketmaster", availability="sold_out"),
                       listing(platform="seatgeek", availability="sold_out"))
        self.assertFalse(event.buyable(NOW))
        self.assertEqual(event.availability(NOW), "sold_out")

    def test_low_stock_shows_through(self):
        event = merged(listing(availability="on_sale", inventory_status="FEW_TICKETS_LEFT"))
        self.assertEqual(event.availability(NOW), "few_left")
        self.assertTrue(event.low_stock)

    def test_a_scheduled_onsale_carries_its_start_time(self):
        event = merged(listing(availability="scheduled", on_sale_at="2026-06-01T15:00:00Z"))
        self.assertEqual(event.availability(NOW), "scheduled")
        self.assertEqual(event.next_sale_start(NOW), datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc))

    def test_cancelled_only_when_every_platform_agrees(self):
        both = merged(listing(platform="ticketmaster", availability="cancelled"),
                      listing(platform="seatgeek", availability="cancelled"))
        self.assertEqual(both.availability(NOW), "cancelled")
        one = merged(listing(platform="ticketmaster", availability="cancelled"),
                     listing(platform="seatgeek", availability="on_sale"))
        self.assertEqual(one.availability(NOW), "on_sale")

    def test_a_presale_is_labelled(self):
        event = merged(listing(availability="presale"))
        self.assertEqual(event.availability(NOW), "presale")
        self.assertIn("presale", event.presale_label)


class LinkTests(unittest.TestCase):
    def test_the_headline_link_goes_to_the_cheapest_seller(self):
        event = merged(listing(platform="ticketmaster", price_min=120.0),
                       listing(platform="seatgeek", price_min=88.0))
        self.assertIn("seatgeek", event.url)

    def test_links_list_every_seller_then_the_search_pages(self):
        event = merged(listing(platform="ticketmaster", price_min=120.0))
        links = event.links()
        self.assertEqual(links[0]["label"], "Ticketmaster")
        self.assertIn("StubHub", [l["label"] for l in links])

    def test_a_real_seatgeek_listing_replaces_its_search_link(self):
        event = merged(listing(platform="seatgeek", price_min=88.0))
        labels = [l["label"] for l in event.links()]
        self.assertEqual(labels.count("SeatGeek"), 1)


class SerialisationTests(unittest.TestCase):
    def test_round_trip(self):
        event = merged(listing(platform="ticketmaster", price_min=120.0),
                       listing(platform="seatgeek", price_min=88.0))
        restored = MergedEvent.from_dict(event.to_dict())
        self.assertEqual(restored.id, event.id)
        self.assertEqual(restored.platforms, event.platforms)
        self.assertEqual(restored.price_min, 88.0)

    def test_describe_mentions_the_platform_count(self):
        event = merged(listing(platform="ticketmaster", price_min=120.0),
                       listing(platform="seatgeek", price_min=88.0))
        text = event.describe(NOW)
        self.assertIn("Sienna Spiro", text)
        self.assertIn("88.00 CAD on SeatGeek", text)
        self.assertIn("2 platforms", text)


if __name__ == "__main__":
    unittest.main()
