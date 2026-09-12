from __future__ import annotations

import unittest

from tests.support import MockServer, event_payload, events_response
from ticketwatch.ticketmaster import (
    AuthError,
    DiscoveryClient,
    NotEntitled,
    RateLimited,
    TicketmasterError,
    redact,
)


class ClientTests(unittest.TestCase):
    def client(self, server: MockServer, **kwargs) -> DiscoveryClient:
        kwargs.setdefault("sleep", lambda _seconds: None)
        return DiscoveryClient(
            "SECRETKEY",
            base_url=f"{server.base_url}/discovery/v2",
            inventory_url=f"{server.base_url}/inventory-status/v1",
            timeout=5,
            **kwargs,
        )

    # -- happy paths -----------------------------------------------------
    def test_search_events_returns_the_embedded_list(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", events_response([event_payload()]))
            events = self.client(server).search_events(keyword="Sienna Spiro")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "Sienna Spiro")

    def test_no_results_is_not_an_error(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", {"page": {"totalPages": 0, "number": 0}})
            self.assertEqual(self.client(server).search_events(keyword="Nobody"), [])

    def test_filters_are_sent_as_query_parameters(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", events_response([]))
            self.client(server).search_events(
                keyword="Sienna Spiro",
                cities=["Toronto", "North York"],
                country_code="CA",
                attraction_id="K8vZ917qxR7",
                radius=50,
                radius_unit="km",
            )
            query = server.requests[0]["query"]
        self.assertEqual(query["keyword"], ["Sienna Spiro"])
        self.assertEqual(query["city"], ["Toronto", "North York"])
        self.assertEqual(query["countryCode"], ["CA"])
        self.assertEqual(query["attractionId"], ["K8vZ917qxR7"])
        self.assertEqual(query["radius"], ["50"])
        self.assertEqual(query["unit"], ["km"])
        self.assertEqual(query["apikey"], ["SECRETKEY"])

    def test_pagination_is_followed(self):
        pages = [
            events_response([event_payload(event_id="A")], page=0, total_pages=2),
            events_response([event_payload(event_id="B")], page=1, total_pages=2),
        ]
        with MockServer() as server:
            server.route(
                "/discovery/v2/events.json",
                lambda query, count: (200, pages[int(query.get("page", ["0"])[0])]),
            )
            events = self.client(server).search_events(keyword="Sienna Spiro")
        self.assertEqual([e["id"] for e in events], ["A", "B"])

    def test_pagination_stops_at_the_page_cap(self):
        with MockServer() as server:
            server.route(
                "/discovery/v2/events.json",
                lambda query, count: (200, events_response([event_payload()], page=count - 1, total_pages=999)),
            )
            self.client(server).search_events(keyword="x", max_pages=3)
            self.assertEqual(len(server.requests), 3)

    def test_search_attractions(self):
        with MockServer() as server:
            server.json_route(
                "/discovery/v2/attractions.json",
                {"_embedded": {"attractions": [{"id": "K8vZ917qxR7", "name": "Sienna Spiro"}]}},
            )
            found = self.client(server).search_attractions("Sienna Spiro")
        self.assertEqual(found[0]["id"], "K8vZ917qxR7")

    # -- inventory -------------------------------------------------------
    def test_inventory_status_maps_ids_to_statuses(self):
        with MockServer() as server:
            server.json_route(
                "/inventory-status/v1/availability",
                [{"eventId": "A", "status": "AVAILABLE"}, {"eventId": "B", "status": "SOLD_OUT"}],
            )
            statuses = self.client(server).inventory_status(["A", "B"])
        self.assertEqual(statuses, {"A": "AVAILABLE", "B": "SOLD_OUT"})

    def test_inventory_status_batches_large_id_lists(self):
        ids = [f"E{i}" for i in range(45)]
        with MockServer() as server:
            server.json_route("/inventory-status/v1/availability", [])
            self.client(server).inventory_status(ids)
            self.assertEqual(len(server.requests), 3)  # 20 + 20 + 5

    def test_inventory_status_with_no_ids_makes_no_call(self):
        with MockServer() as server:
            server.json_route("/inventory-status/v1/availability", [])
            self.assertEqual(self.client(server).inventory_status([]), {})
            self.assertEqual(server.requests, [])

    def test_inventory_403_means_not_entitled(self):
        with MockServer() as server:
            server.json_route("/inventory-status/v1/availability", {"fault": "no access"}, status=403)
            with self.assertRaises(NotEntitled):
                self.client(server).inventory_status(["A"])

    # -- failures --------------------------------------------------------
    def test_401_is_an_auth_error(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", {"fault": {"faultstring": "Invalid ApiKey"}}, status=401)
            with self.assertRaises(AuthError):
                self.client(server).search_events(keyword="x")

    def test_429_is_a_rate_limit_with_retry_after(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", {"fault": "quota"}, status=429)
            server.server.extra_headers = {"Retry-After": "42"}
            with self.assertRaises(RateLimited) as ctx:
                self.client(server).search_events(keyword="x")
        self.assertEqual(ctx.exception.retry_after, 42.0)

    def test_server_errors_are_retried_then_succeed(self):
        def flaky(query, count):
            if count < 3:
                return 503, {"fault": "try later"}
            return 200, events_response([event_payload()])

        with MockServer() as server:
            server.route("/discovery/v2/events.json", flaky)
            events = self.client(server, max_retries=3).search_events(keyword="x")
        self.assertEqual(len(events), 1)
        self.assertEqual(len(server.requests), 3)

    def test_server_errors_eventually_give_up(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", {"fault": "down"}, status=503)
            with self.assertRaises(TicketmasterError):
                self.client(server, max_retries=1).search_events(keyword="x")
            self.assertEqual(len(server.requests), 2)

    def test_invalid_json_is_retried_then_reported(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", "<html>not json</html>")
            with self.assertRaises(TicketmasterError) as ctx:
                self.client(server, max_retries=1).search_events(keyword="x")
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_the_api_key_never_appears_in_an_error_message(self):
        with MockServer() as server:
            server.json_route("/discovery/v2/events.json", "SECRETKEY leaked in the body", status=404)
            with self.assertRaises(TicketmasterError) as ctx:
                self.client(server).search_events(keyword="x")
        self.assertNotIn("SECRETKEY", str(ctx.exception))
        self.assertIn("***", str(ctx.exception))

    def test_connection_refused_is_wrapped(self):
        client = DiscoveryClient(
            "SECRETKEY",
            base_url="http://127.0.0.1:9/discovery/v2",
            timeout=1,
            max_retries=0,
            sleep=lambda _s: None,
        )
        with self.assertRaises(TicketmasterError):
            client.search_events(keyword="x")


class RedactTests(unittest.TestCase):
    def test_redact_replaces_every_occurrence(self):
        self.assertEqual(redact("a=KEY&b=KEY", "KEY"), "a=***&b=***")

    def test_redact_with_no_key_is_a_no_op(self):
        self.assertEqual(redact("nothing to hide", ""), "nothing to hide")


if __name__ == "__main__":
    unittest.main()
