from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from tests.support import FakeProvider, listing
from ticketwatch.alerts import Alert
from ticketwatch.config import Config
from ticketwatch.monitor import Monitor
from ticketwatch.notify import Dispatcher
from ticketwatch.providers.base import ProviderError, ProviderRateLimited
from ticketwatch.state import StateStore

BEFORE_SALE = datetime(2026, 1, 1, tzinfo=timezone.utc)
DURING_SALE = datetime(2026, 5, 1, tzinfo=timezone.utc)


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
        self.now = DURING_SALE
        self.slept: List[float] = []

    def build(self, *providers, **config_kwargs) -> Monitor:
        config_kwargs.setdefault("api_key", "k")
        config_kwargs.setdefault("state_file", str(self.state_path))
        cfg = Config(**config_kwargs)
        self.dispatcher = RecordingDispatcher()
        return Monitor(
            cfg,
            providers or (FakeProvider([listing()]),),
            StateStore(cfg.state_file),
            self.dispatcher,
            sleep=self.slept.append,
            clock=lambda: self.now,
        )


class CheckTests(MonitorTestCase):
    def test_first_check_reports_a_new_date_and_remembers_it(self):
        result = self.build().check_once()
        self.assertEqual([a.kind for a in result.alerts], ["new_event"])
        self.assertEqual([a.kind for a in self.dispatcher.sent], ["new_event"])
        self.assertTrue(self.state_path.exists())

    def test_second_identical_check_is_silent(self):
        monitor = self.build()
        monitor.check_once()
        self.dispatcher.sent.clear()
        self.assertEqual(monitor.check_once().alerts, [])

    def test_listings_from_every_platform_are_merged_into_one_show(self):
        monitor = self.build(
            FakeProvider([listing(platform="ticketmaster", price_min=59.5)], name="ticketmaster"),
            FakeProvider([listing(platform="seatgeek", price_min=42.0)], name="seatgeek"),
        )
        result = monitor.check_once()
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].platforms, ["seatgeek", "ticketmaster"])

    def test_the_cheapest_platform_wins(self):
        monitor = self.build(
            FakeProvider([listing(platform="ticketmaster", price_min=120.0)], name="ticketmaster"),
            FakeProvider([listing(platform="seatgeek", price_min=88.0)], name="seatgeek"),
        )
        event = monitor.check_once().events[0]
        self.assertEqual(event.cheapest.platform, "seatgeek")
        self.assertEqual(event.price_min, 88.0)

    def test_check_result_names_the_cheapest_buyable_show(self):
        monitor = self.build(FakeProvider([
            listing(event_id="A", local_date="2026-10-25", price_min=200.0),
            listing(event_id="B", local_date="2026-11-02", price_min=75.0),
        ]))
        self.assertEqual(self.build and monitor.check_once().cheapest.price_min, 75.0)

    def test_a_date_only_another_platform_knows_about_still_shows_up(self):
        monitor = self.build(
            FakeProvider([listing(platform="ticketmaster", local_date="2026-10-25")], name="ticketmaster"),
            FakeProvider(
                [listing(platform="bandsintown", local_date="2026-12-01", price_min=None, price_max=None)],
                name="bandsintown",
            ),
        )
        result = monitor.check_once()
        self.assertEqual([e.local_date for e in result.events], ["2026-10-25", "2026-12-01"])
        self.assertEqual({a.kind for a in result.alerts}, {"new_event"})

    def test_a_price_drop_is_reported(self):
        provider = FakeProvider([listing(price_min=120.0)])
        monitor = self.build(provider)
        monitor.check_once()
        self.dispatcher.sent.clear()

        provider.listings = [listing(price_min=80.0)]
        result = monitor.check_once()
        self.assertEqual([a.kind for a in result.alerts], ["cheaper"])
        self.assertIn("40.00 CAD (33%)", result.alerts[0].body)

    def test_a_trivial_price_wobble_is_not_worth_an_alert(self):
        provider = FakeProvider([listing(price_min=100.0)])
        monitor = self.build(provider, alert_on=["cheaper"])
        monitor.check_once()
        provider.listings = [listing(price_min=99.0)]  # 1%, below the 5% floor
        self.assertEqual(monitor.check_once().alerts, [])

    def test_a_show_appearing_on_a_new_platform_is_reported(self):
        tm = FakeProvider([listing(platform="ticketmaster")], name="ticketmaster")
        sg = FakeProvider([], name="seatgeek")
        monitor = self.build(tm, sg, alert_on=["new_event", "new_platform"])
        monitor.check_once()
        self.dispatcher.sent.clear()

        sg.listings = [listing(platform="seatgeek", price_min=42.0)]
        result = monitor.check_once()
        self.assertEqual([a.kind for a in result.alerts], ["new_platform"])
        self.assertIn("seatgeek", result.alerts[0].body)

    def test_other_artists_and_cities_are_ignored(self):
        monitor = self.build(FakeProvider([
            listing(event_id="WANTED", city="Toronto"),
            listing(event_id="OTHER_CITY", city="Ottawa"),
            listing(event_id="OTHER_ARTIST", artist="Taylor Swift"),
        ]))
        result = monitor.check_once()
        self.assertEqual([e.city for e in result.events], ["Toronto"])

    def test_toronto_boroughs_count_as_toronto(self):
        monitor = self.build(FakeProvider([listing(city="North York", local_date="2026-11-02")]))
        self.assertEqual(len(monitor.check_once().events), 1)

    def test_every_platform_gets_the_same_query(self):
        tm, sg = FakeProvider([], name="ticketmaster"), FakeProvider([], name="seatgeek")
        self.build(tm, sg, keyword="Sienna Spiro", cities=["Toronto"]).check_once()
        for provider in (tm, sg):
            self.assertEqual(provider.last_query.keyword, "Sienna Spiro")
            self.assertEqual(list(provider.last_query.cities), ["Toronto"])

    def test_only_configured_alert_kinds_are_sent(self):
        self.assertEqual(self.build(alert_on=["on_sale"]).check_once().alerts, [])


class FailureTests(MonitorTestCase):
    def test_one_platform_failing_does_not_lose_the_others(self):
        monitor = self.build(
            FakeProvider([listing(platform="ticketmaster")], name="ticketmaster"),
            FakeProvider(error=ProviderError("seatgeek is down"), name="seatgeek"),
        )
        result = monitor.check_once()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.provider_errors, {"seatgeek": "seatgeek is down"})

    def test_every_platform_failing_is_a_failed_check(self):
        monitor = self.build(
            FakeProvider(error=ProviderError("tm down"), name="ticketmaster"),
            FakeProvider(error=ProviderError("sg down"), name="seatgeek"),
        )
        result = monitor.check_once()
        self.assertFalse(result.ok)
        self.assertIn("tm down", result.error)
        self.assertIn("sg down", result.error)
        self.assertFalse(self.state_path.exists())

    def test_no_platforms_at_all_is_reported_clearly(self):
        monitor = self.build(*[])
        monitor.providers = []
        result = monitor.check_once()
        self.assertIn("No ticket platforms", result.error)

    def test_a_provider_bug_is_contained(self):
        monitor = self.build(
            FakeProvider(error=ValueError("boom"), name="seatgeek"),
            FakeProvider([listing()], name="ticketmaster"),
        )
        result = monitor.check_once()
        self.assertTrue(result.ok)
        self.assertIn("ValueError", result.provider_errors["seatgeek"])

    def test_repeated_total_failures_raise_one_alert_not_a_flood(self):
        monitor = self.build(
            FakeProvider(error=ProviderError("still down"), name="ticketmaster"),
            alert_on=["on_sale", "error"],
        )
        for _ in range(6):
            monitor.check_once()
        self.assertEqual([a.kind for a in self.dispatcher.sent], ["error"])

    def test_recovery_resets_the_failure_alert(self):
        provider = FakeProvider(error=ProviderError("down"), name="ticketmaster")
        monitor = self.build(provider, alert_on=["new_event", "error"])
        for _ in range(3):
            monitor.check_once()
        provider.error = None
        provider.listings = [listing()]
        monitor.check_once()
        self.assertEqual([a.kind for a in self.dispatcher.sent], ["error", "new_event"])

    def test_rate_limiting_is_honoured(self):
        monitor = self.build(
            FakeProvider(error=ProviderRateLimited("slow down", retry_after=90), name="ticketmaster")
        )
        self.assertEqual(monitor.check_once().retry_after, 90)


class LoopTests(MonitorTestCase):
    def test_run_forever_stops_after_max_iterations(self):
        monitor = self.build()
        self.assertEqual(monitor.run_forever(max_iterations=3), 3)
        self.assertEqual(monitor.checks, 3)

    def test_no_sleep_after_the_final_iteration(self):
        self.build(interval_seconds=60).run_forever(max_iterations=1)
        self.assertEqual(self.slept, [])

    def test_it_sleeps_between_iterations(self):
        self.build(interval_seconds=10, jitter=0.0).run_forever(max_iterations=2)
        self.assertAlmostEqual(sum(self.slept), 10.0, places=5)

    def test_stop_requests_are_obeyed_mid_sleep(self):
        monitor = self.build(interval_seconds=600, jitter=0.0)

        def stop_after_one_slice(seconds):
            self.slept.append(seconds)
            monitor.request_stop()

        monitor._sleep = stop_after_one_slice
        monitor.run_forever(max_iterations=10)
        self.assertEqual(monitor.checks, 1)
        self.assertEqual(self.slept, [1.0])

    def test_failures_back_the_polling_off(self):
        monitor = self.build(
            FakeProvider(error=ProviderError("down"), name="tm"), interval_seconds=10, jitter=0.0
        )
        monitor.run_forever(max_iterations=3)
        self.assertAlmostEqual(sum(self.slept), 60.0, places=5)

    def test_the_backoff_is_capped(self):
        monitor = self.build(
            FakeProvider(error=ProviderError("down"), name="tm"), interval_seconds=300, jitter=0.0
        )
        monitor.run_forever(max_iterations=2)
        self.assertLessEqual(sum(self.slept), 600.0)

    def test_a_rate_limit_delay_wins_over_the_interval(self):
        monitor = self.build(
            FakeProvider(error=ProviderRateLimited("slow", retry_after=120), name="tm"),
            interval_seconds=10, jitter=0.0,
        )
        monitor.run_forever(max_iterations=2)
        self.assertAlmostEqual(sum(self.slept), 120.0, places=5)

    def test_jitter_keeps_the_delay_near_the_interval(self):
        monitor = self.build(interval_seconds=100, jitter=0.2)
        delays = [monitor.next_delay() for _ in range(200)]
        self.assertTrue(all(80 <= d <= 120 for d in delays))

    def test_the_ui_callback_sees_every_check(self):
        seen = []
        monitor = self.build(interval_seconds=5, jitter=0.0)
        monitor.on_check = lambda result, delay: seen.append((len(result.events), delay))
        monitor.run_forever(max_iterations=2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], (1, 5.0))

    def test_a_broken_callback_never_stops_the_loop(self):
        monitor = self.build()
        monitor.on_check = lambda result, delay: 1 / 0
        self.assertEqual(monitor.run_forever(max_iterations=2), 2)


if __name__ == "__main__":
    unittest.main()
