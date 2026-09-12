from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from tests.support import event_payload
from ticketwatch.alerts import Alert
from ticketwatch.config import Config
from ticketwatch.monitor import Monitor
from ticketwatch.notify import Dispatcher
from ticketwatch.state import StateStore
from ticketwatch.ticketmaster import AuthError, NotEntitled, RateLimited, TicketmasterError

BEFORE_SALE = datetime(2026, 1, 1, tzinfo=timezone.utc)
DURING_SALE = datetime(2026, 5, 1, tzinfo=timezone.utc)


class FakeClient:
    """Stands in for DiscoveryClient without touching the network."""

    def __init__(self, events: List[Dict[str, Any]] = None) -> None:
        self.events = list(events or [])
        self.inventory: Dict[str, str] = {}
        self.error: Exception = None
        self.inventory_error: Exception = None
        self.search_calls = 0
        self.inventory_calls = 0
        self.last_kwargs: Dict[str, Any] = {}

    def search_events(self, **kwargs: Any) -> List[Dict[str, Any]]:
        self.search_calls += 1
        self.last_kwargs = kwargs
        if self.error:
            raise self.error
        return list(self.events)

    def inventory_status(self, ids) -> Dict[str, str]:
        self.inventory_calls += 1
        if self.inventory_error:
            raise self.inventory_error
        return {event_id: self.inventory[event_id] for event_id in ids if event_id in self.inventory}


class RecordingDispatcher(Dispatcher):
    def __init__(self) -> None:
        super().__init__([])
        self.sent: List[Alert] = []

    def dispatch(self, alerts) -> int:
        self.sent.extend(alerts)
        return len(alerts)


class MonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "state.json"
        self.now = BEFORE_SALE
        self.slept: List[float] = []

    def build(self, client: FakeClient, **config_kwargs) -> Monitor:
        config_kwargs.setdefault("api_key", "k")
        config_kwargs.setdefault("state_file", str(self.state_path))
        cfg = Config(**config_kwargs)
        self.dispatcher = RecordingDispatcher()
        return Monitor(
            cfg,
            client,
            StateStore(cfg.state_file),
            self.dispatcher,
            sleep=self.slept.append,
            clock=lambda: self.now,
        )


class CheckTests(MonitorTestCase):
    def test_first_check_reports_a_new_event_and_remembers_it(self):
        monitor = self.build(FakeClient([event_payload()]))
        result = monitor.check_once()
        self.assertEqual([a.kind for a in result.alerts], ["new_event"])
        self.assertEqual([a.kind for a in self.dispatcher.sent], ["new_event"])
        self.assertTrue(self.state_path.exists())

    def test_second_identical_check_is_silent(self):
        monitor = self.build(FakeClient([event_payload()]))
        monitor.check_once()
        self.dispatcher.sent.clear()
        result = monitor.check_once()
        self.assertEqual(result.alerts, [])
        self.assertEqual(self.dispatcher.sent, [])

    def test_the_onsale_moment_is_caught_on_the_next_poll(self):
        monitor = self.build(FakeClient([event_payload()]))
        monitor.check_once()
        self.dispatcher.sent.clear()

        self.now = DURING_SALE
        result = monitor.check_once()
        self.assertEqual([a.kind for a in result.alerts], ["on_sale"])
        self.assertTrue(result.buyable)
        self.assertTrue(self.dispatcher.sent[0].urgent)

    def test_a_restock_is_caught(self):
        client = FakeClient([event_payload()])
        client.inventory = {"G5vYZ9abc123": "SOLD_OUT"}
        monitor = self.build(client)
        self.now = DURING_SALE
        monitor.check_once()
        self.dispatcher.sent.clear()

        client.inventory = {"G5vYZ9abc123": "AVAILABLE"}
        result = monitor.check_once()
        self.assertEqual([a.kind for a in result.alerts], ["back_in_stock"])

    def test_events_for_other_artists_and_cities_are_ignored(self):
        client = FakeClient([
            event_payload(event_id="WANTED", city="Toronto"),
            event_payload(event_id="OTHER_CITY", city="Ottawa"),
            event_payload(event_id="OTHER_ARTIST", name="Taylor Swift", attraction_name="Taylor Swift",
                          attraction_id="K8vZ_other"),
        ])
        monitor = self.build(client)
        result = monitor.check_once()
        self.assertEqual([e.id for e in result.events], ["WANTED"])

    def test_inventory_status_is_attached_to_matching_events(self):
        client = FakeClient([event_payload()])
        client.inventory = {"G5vYZ9abc123": "FEW_TICKETS_LEFT"}
        monitor = self.build(client)
        result = monitor.check_once()
        self.assertEqual(result.events[0].inventory_status, "FEW_TICKETS_LEFT")

    def test_inventory_can_be_turned_off(self):
        client = FakeClient([event_payload()])
        monitor = self.build(client, check_inventory=False)
        monitor.check_once()
        self.assertEqual(client.inventory_calls, 0)

    def test_an_unentitled_key_falls_back_to_sale_dates_and_stops_asking(self):
        client = FakeClient([event_payload()])
        client.inventory_error = NotEntitled("no access")
        monitor = self.build(client)
        self.now = DURING_SALE

        result = monitor.check_once()
        self.assertEqual(client.inventory_calls, 1)
        self.assertTrue(result.buyable)  # sale window still says yes

        monitor.check_once()
        self.assertEqual(client.inventory_calls, 1)  # not asked a second time

    def test_a_transient_inventory_failure_does_not_sink_the_check(self):
        client = FakeClient([event_payload()])
        client.inventory_error = TicketmasterError("hiccup")
        monitor = self.build(client)
        result = monitor.check_once()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.events), 1)
        self.assertIsNone(result.events[0].inventory_status)

    def test_only_configured_alert_kinds_are_sent(self):
        monitor = self.build(FakeClient([event_payload()]), alert_on=["on_sale"])
        result = monitor.check_once()
        self.assertEqual(result.alerts, [])

    def test_city_filtering_is_done_locally_by_default(self):
        client = FakeClient([event_payload()])
        monitor = self.build(client)
        monitor.check_once()
        self.assertIsNone(client.last_kwargs["cities"])

    def test_the_api_city_filter_can_be_switched_on(self):
        client = FakeClient([event_payload()])
        monitor = self.build(client, use_api_city_filter=True)
        monitor.check_once()
        self.assertEqual(client.last_kwargs["cities"], ["Toronto"])


class FailureTests(MonitorTestCase):
    def test_an_api_failure_is_reported_without_crashing(self):
        client = FakeClient([event_payload()])
        client.error = TicketmasterError("upstream is down")
        monitor = self.build(client)
        result = monitor.check_once()
        self.assertFalse(result.ok)
        self.assertIn("upstream is down", result.error)
        self.assertFalse(self.state_path.exists())

    def test_repeated_failures_raise_one_alert_not_a_flood(self):
        client = FakeClient([event_payload()])
        client.error = TicketmasterError("still down")
        monitor = self.build(client, alert_on=["on_sale", "error"])
        for _ in range(6):
            monitor.check_once()
        self.assertEqual([a.kind for a in self.dispatcher.sent], ["error"])

    def test_recovery_resets_the_failure_alert(self):
        client = FakeClient([event_payload()])
        client.error = TicketmasterError("down")
        monitor = self.build(client, alert_on=["new_event", "error"])
        for _ in range(3):
            monitor.check_once()
        client.error = None
        monitor.check_once()
        kinds = [a.kind for a in self.dispatcher.sent]
        self.assertEqual(kinds, ["error", "new_event"])

    def test_a_bad_api_key_stops_the_run(self):
        client = FakeClient([event_payload()])
        client.error = AuthError("bad key")
        monitor = self.build(client)
        with self.assertRaises(AuthError):
            monitor.check_once()

    def test_rate_limiting_is_honoured(self):
        client = FakeClient([event_payload()])
        client.error = RateLimited("slow down", retry_after=90)
        monitor = self.build(client)
        result = monitor.check_once()
        self.assertEqual(result.retry_after, 90)
        self.assertFalse(result.ok)


class LoopTests(MonitorTestCase):
    def test_run_forever_stops_after_max_iterations(self):
        monitor = self.build(FakeClient([event_payload()]))
        self.assertEqual(monitor.run_forever(max_iterations=3), 3)
        self.assertEqual(monitor.checks, 3)

    def test_no_sleep_after_the_final_iteration(self):
        monitor = self.build(FakeClient([event_payload()]), interval_seconds=60)
        monitor.run_forever(max_iterations=1)
        self.assertEqual(self.slept, [])

    def test_it_sleeps_between_iterations(self):
        monitor = self.build(FakeClient([event_payload()]), interval_seconds=10, jitter=0.0)
        monitor.run_forever(max_iterations=2)
        self.assertAlmostEqual(sum(self.slept), 10.0, places=5)

    def test_stop_requests_are_obeyed_mid_sleep(self):
        monitor = self.build(FakeClient([event_payload()]), interval_seconds=600, jitter=0.0)

        def stop_after_one_slice(seconds):
            self.slept.append(seconds)
            monitor.request_stop()

        monitor._sleep = stop_after_one_slice
        monitor.run_forever(max_iterations=10)
        self.assertEqual(monitor.checks, 1)
        self.assertEqual(self.slept, [1.0])  # one second slice, then out

    def test_failures_back_the_polling_off(self):
        client = FakeClient([event_payload()])
        client.error = TicketmasterError("down")
        monitor = self.build(client, interval_seconds=10, jitter=0.0)
        monitor.run_forever(max_iterations=3)
        # 10s * 2, then 10s * 4 - the interruptible sleep slices it into seconds.
        self.assertAlmostEqual(sum(self.slept), 60.0, places=5)

    def test_the_backoff_is_capped(self):
        client = FakeClient([event_payload()])
        client.error = TicketmasterError("down")
        monitor = self.build(client, interval_seconds=300, jitter=0.0)
        monitor.run_forever(max_iterations=2)
        self.assertLessEqual(sum(self.slept), 600.0)

    def test_jitter_keeps_the_delay_near_the_interval(self):
        monitor = self.build(FakeClient([]), interval_seconds=100, jitter=0.2)
        delays = [monitor.next_delay() for _ in range(200)]
        self.assertTrue(all(80 <= d <= 120 for d in delays))
        self.assertGreater(len(set(delays)), 1)

    def test_a_rate_limit_delay_wins_over_the_interval(self):
        client = FakeClient([event_payload()])
        client.error = RateLimited("slow down", retry_after=120)
        monitor = self.build(client, interval_seconds=10, jitter=0.0)
        monitor.run_forever(max_iterations=2)
        self.assertAlmostEqual(sum(self.slept), 120.0, places=5)


if __name__ == "__main__":
    unittest.main()
