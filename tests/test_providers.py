from __future__ import annotations

import unittest

from tests.support import MockServer, event_payload, events_response
from ticketwatch.config import Config
from ticketwatch.providers import build_providers, search_links
from ticketwatch.providers.bandsintown import BandsintownProvider
from ticketwatch.providers.base import ON_SALE, SOLD_OUT, UNKNOWN, Provider, ProviderError, ProviderQuery
from ticketwatch.providers.seatgeek import SeatGeekProvider
from ticketwatch.providers.ticketmaster import TicketmasterProvider

QUERY = ProviderQuery(keyword="Sienna Spiro", cities=["Toronto"], country_code="CA")


class TicketmasterProviderTests(unittest.TestCase):
    def provider(self, server: MockServer, **overrides) -> TicketmasterProvider:
        cfg = Config(
            api_key="TEST-KEY",
            discovery_base_url=f"{server.base_url}/discovery/v2",
            inventory_base_url=f"{server.base_url}/inventory-status/v1",
            max_retries=0,
            **overrides,
        )
        return TicketmasterProvider.from_config(cfg)

    def test_events_become_platform_neutral_listings(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", events_response([event_payload()]))
            server.json_route("/inventory-status/v1/availability", [])
            listings = self.provider(server).fetch(QUERY)
        self.assertEqual(len(listings), 1)
        entry = listings[0]
        self.assertEqual(entry.platform, "ticketmaster")
        self.assertEqual(entry.artist, "Sienna Spiro")
        self.assertEqual(entry.venue, "History")
        self.assertEqual(entry.city, "Toronto")
        self.assertEqual(entry.price_min, 59.5)
        self.assertEqual(entry.currency, "CAD")

    def test_inventory_status_rides_along(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", events_response([event_payload()]))
            server.json_route("/inventory-status/v1/availability",
                              [{"eventId": "G5vYZ9abc123", "status": "FEW_TICKETS_LEFT"}])
            entry = self.provider(server).fetch(QUERY)[0]
        self.assertEqual(entry.inventory_status, "FEW_TICKETS_LEFT")
        self.assertEqual(entry.availability, ON_SALE)

    def test_sold_out_is_carried_across(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", events_response([event_payload()]))
            server.json_route("/inventory-status/v1/availability",
                              [{"eventId": "G5vYZ9abc123", "status": "SOLD_OUT"}])
            self.assertEqual(self.provider(server).fetch(QUERY)[0].availability, SOLD_OUT)

    def test_an_unentitled_key_stops_the_inventory_calls(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", events_response([event_payload()]))
            server.json_route("/inventory-status/v1/availability", {"fault": "no"}, status=403)
            provider = self.provider(server)
            provider.fetch(QUERY)
            first = len([r for r in server.requests if "inventory" in r["path"]])
            provider.fetch(QUERY)
            second = len([r for r in server.requests if "inventory" in r["path"]])
        self.assertEqual((first, second), (1, 1))

    def test_inventory_can_be_switched_off(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", events_response([event_payload()]))
            self.provider(server, check_inventory=False).fetch(QUERY)
            self.assertEqual([r for r in server.requests if "inventory" in r["path"]], [])

    def test_failures_arrive_as_provider_errors(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", {"fault": "down"}, status=503)
            result = self.provider(server).collect(QUERY)
        self.assertFalse(result.ok)
        self.assertEqual(result.platform, "ticketmaster")

    def test_no_api_key_means_no_provider(self):
        self.assertIsNone(TicketmasterProvider.from_config(Config()))


class SeatGeekProviderTests(unittest.TestCase):
    def provider(self, server: MockServer) -> SeatGeekProvider:
        cfg = Config(seatgeek_client_id="SG-SECRET", seatgeek_base_url=f"{server.base_url}/2/events",
                     max_retries=0)
        return SeatGeekProvider.from_config(cfg)

    def payload(self, **overrides):
        event = {
            "id": 6931,
            "title": "Sienna Spiro",
            "url": "https://seatgeek.com/sienna-spiro-tickets/6931",
            "datetime_local": "2026-10-25T19:00:00",
            "venue": {"name": "History", "city": "Toronto", "state": "ON", "country": "CA"},
            "performers": [{"name": "Sienna Spiro"}],
            "stats": {"lowest_price": 88, "highest_price": 410, "listing_count": 137},
        }
        event.update(overrides)
        return {"events": [event]}

    def test_it_reads_the_lowest_price(self):
        with MockServer() as server:
            server.json_route("/2/events", self.payload())
            entry = self.provider(server).fetch(QUERY)[0]
        self.assertEqual(entry.platform, "seatgeek")
        self.assertEqual(entry.price_min, 88.0)
        self.assertEqual(entry.price_max, 410.0)
        self.assertEqual(entry.listing_count, 137)
        self.assertEqual(entry.local_date, "2026-10-25")
        self.assertEqual(entry.availability, ON_SALE)
        self.assertTrue(entry.resale)

    def test_no_listings_means_sold_out(self):
        with MockServer() as server:
            server.json_route("/2/events", self.payload(stats={"listing_count": 0}))
            self.assertEqual(self.provider(server).fetch(QUERY)[0].availability, SOLD_OUT)

    def test_missing_stats_are_survivable(self):
        with MockServer() as server:
            server.json_route("/2/events", self.payload(stats={}))
            entry = self.provider(server).fetch(QUERY)[0]
        self.assertIsNone(entry.price_min)
        self.assertEqual(entry.availability, UNKNOWN)

    def test_the_client_id_is_sent_and_never_leaked(self):
        with MockServer() as server:
            server.json_route("/2/events", {"fault": "nope"}, status=401)
            result = self.provider(server).collect(QUERY)
        self.assertIn("client ID", result.error)
        self.assertNotIn("SG-SECRET", result.error)

    def test_no_client_id_means_no_provider(self):
        self.assertIsNone(SeatGeekProvider.from_config(Config(api_key="k")))


class BandsintownProviderTests(unittest.TestCase):
    def provider(self, server: MockServer) -> BandsintownProvider:
        cfg = Config(api_key="k", bandsintown_app_id="ticketwatch",
                     bandsintown_base_url=server.base_url, max_retries=0)
        return BandsintownProvider.from_config(cfg)

    def payload(self, **overrides):
        event = {
            "id": "1039",
            "url": "https://www.bandsintown.com/e/1039",
            "datetime": "2026-12-01T20:00:00",
            "title": "",
            "lineup": ["Sienna Spiro"],
            "venue": {"name": "The Great Hall", "city": "Toronto", "region": "ON", "country": "Canada"},
            "offers": [{"type": "Tickets", "url": "https://tickets.test/buy", "status": "available"}],
        }
        event.update(overrides)
        return [event]

    def test_it_finds_a_date_with_a_link_to_buy(self):
        with MockServer() as server:
            server.json_route("/artists/Sienna%20Spiro/events", self.payload())
            entry = self.provider(server).fetch(QUERY)[0]
        self.assertEqual(entry.platform, "bandsintown")
        self.assertEqual(entry.local_date, "2026-12-01")
        self.assertEqual(entry.city, "Toronto")
        self.assertEqual(entry.url, "https://tickets.test/buy")
        self.assertEqual(entry.availability, ON_SALE)
        self.assertIsNone(entry.price_min)  # Bandsintown never quotes a price

    def test_an_offer_that_is_not_available_yet(self):
        with MockServer() as server:
            server.json_route("/artists/Sienna%20Spiro/events",
                              self.payload(offers=[{"type": "Tickets", "url": "x", "status": "sold out"}]))
            self.assertEqual(self.provider(server).fetch(QUERY)[0].availability, UNKNOWN)

    def test_an_unknown_artist_returns_nothing_rather_than_exploding(self):
        with MockServer() as server:
            server.json_route("/artists/Sienna%20Spiro/events", {"errorMessage": "Unknown Artist"})
            self.assertEqual(self.provider(server).fetch(QUERY), [])

    def test_without_a_keyword_there_is_nothing_to_ask(self):
        with MockServer() as server:
            self.assertEqual(self.provider(server).fetch(ProviderQuery()), [])

    def test_it_is_off_when_the_app_id_is_cleared(self):
        self.assertIsNone(BandsintownProvider.from_config(Config(api_key="k", bandsintown_app_id="")))


class BuildProvidersTests(unittest.TestCase):
    def test_ticketmaster_and_bandsintown_by_default(self):
        names = [p.name for p in build_providers(Config(api_key="k"))]
        self.assertEqual(names, ["ticketmaster", "bandsintown"])

    def test_seatgeek_joins_when_given_a_client_id(self):
        names = [p.name for p in build_providers(Config(api_key="k", seatgeek_client_id="s"))]
        self.assertEqual(names, ["ticketmaster", "seatgeek", "bandsintown"])

    def test_nothing_configured_means_no_providers(self):
        self.assertEqual(build_providers(Config(bandsintown_app_id="")), [])


class SearchLinkTests(unittest.TestCase):
    def test_links_cover_the_marketplaces_without_an_api(self):
        links = {link["id"] for link in search_links("Sienna Spiro", "Toronto")}
        self.assertEqual(links, {"stubhub", "vividseats", "gametime", "seatgeek_web"})

    def test_the_artist_and_city_are_encoded_into_the_url(self):
        stubhub = next(l for l in search_links("Sienna Spiro", "Toronto") if l["id"] == "stubhub")
        self.assertIn("Sienna+Spiro+Toronto", stubhub["url"])

    def test_no_artist_means_no_links(self):
        self.assertEqual(search_links(""), [])


class CollectTests(unittest.TestCase):
    def test_collect_turns_a_bug_into_a_reportable_error(self):
        class Broken(Provider):
            name = "broken"

            def fetch(self, query):
                raise ValueError("oops")

        result = Broken().collect(QUERY)
        self.assertFalse(result.ok)
        self.assertIn("ValueError: oops", result.error)

    def test_collect_passes_through_a_clean_failure(self):
        class Sad(Provider):
            name = "sad"

            def fetch(self, query):
                raise ProviderError("their API is down")

        self.assertEqual(Sad().collect(QUERY).error, "their API is down")


if __name__ == "__main__":
    unittest.main()
