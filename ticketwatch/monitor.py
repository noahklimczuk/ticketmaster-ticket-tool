"""The polling loop: ask Ticketmaster, compare, shout, sleep, repeat."""

from __future__ import annotations

import logging
import random
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from .alerts import Alert, detect_changes, error_alert
from .config import Config
from .events import EventSnapshot, now_utc
from .matcher import filter_events
from .notify import Dispatcher
from .state import StateStore
from .ticketmaster import AuthError, DiscoveryClient, NotEntitled, RateLimited, TicketmasterError

LOG = logging.getLogger(__name__)

# Consecutive API failures before we bother the user about it.
FAILURE_ALERT_THRESHOLD = 3


@dataclass
class CheckResult:
    events: List[EventSnapshot] = field(default_factory=list)
    alerts: List[Alert] = field(default_factory=list)
    error: Optional[str] = None
    retry_after: Optional[float] = None
    checked_at: Optional[datetime] = None

    @property
    def buyable(self) -> List[EventSnapshot]:
        now = self.checked_at or now_utc()
        return [e for e in self.events if e.buyable(now)]

    @property
    def ok(self) -> bool:
        return self.error is None


class Monitor:
    def __init__(
        self,
        config: Config,
        client: DiscoveryClient,
        store: StateStore,
        dispatcher: Dispatcher,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.dispatcher = dispatcher
        self._sleep = sleep
        self._clock = clock
        self._stop = False
        self._inventory_supported = config.check_inventory
        self._consecutive_failures = 0
        self._failure_alert_sent = False
        self.checks = 0

    # ------------------------------------------------------------------ #
    def request_stop(self, *_args) -> None:
        self._stop = True

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self.request_stop)
            except (ValueError, OSError):  # pragma: no cover - non main thread
                LOG.debug("Could not install handler for %s", sig)

    # ------------------------------------------------------------------ #
    def fetch(self) -> List[EventSnapshot]:
        cfg = self.config
        raw = self.client.search_events(
            keyword=cfg.keyword or None,
            cities=cfg.cities if cfg.use_api_city_filter else None,
            country_code=cfg.country_code if cfg.use_api_city_filter else None,
            state_code=cfg.state_code if cfg.use_api_city_filter else None,
            attraction_id=cfg.attraction_id,
            venue_id=cfg.venue_id,
            classification_name=cfg.classification_name,
            radius=cfg.radius,
            radius_unit=cfg.radius_unit,
        )
        snapshots = [EventSnapshot.from_api(item) for item in raw if isinstance(item, dict)]
        matched = filter_events(
            snapshots,
            keyword=cfg.keyword,
            cities=cfg.cities,
            attraction_id=cfg.attraction_id or "",
            strict=cfg.strict_artist_match,
            country_code=cfg.country_code or "",
        )
        LOG.debug("Discovery returned %d events, %d matched the filters", len(snapshots), len(matched))

        if self._inventory_supported and matched:
            try:
                statuses = self.client.inventory_status([e.id for e in matched])
                for event in matched:
                    event.inventory_status = statuses.get(event.id)
            except NotEntitled as exc:
                LOG.info("Inventory status unavailable for this API key, relying on sale dates (%s)", exc)
                self._inventory_supported = False
            except (RateLimited, AuthError):
                raise
            except TicketmasterError as exc:
                LOG.warning("Inventory status check failed this round: %s", exc)
        return matched

    # ------------------------------------------------------------------ #
    def check_once(self, notify: bool = True) -> CheckResult:
        now = self._clock()
        self.checks += 1
        try:
            events = self.fetch()
        except AuthError:
            raise
        except RateLimited as exc:
            self._consecutive_failures += 1
            LOG.warning("Rate limited by Ticketmaster: %s", exc)
            return CheckResult(
                error=str(exc),
                retry_after=exc.retry_after or self.config.interval_seconds * 4,
                checked_at=now,
            )
        except TicketmasterError as exc:
            self._consecutive_failures += 1
            LOG.error("Check failed (%d in a row): %s", self._consecutive_failures, exc)
            result = CheckResult(error=str(exc), checked_at=now)
            if (
                notify
                and self._consecutive_failures >= FAILURE_ALERT_THRESHOLD
                and not self._failure_alert_sent
                and "error" in self.config.alert_on
            ):
                alert = error_alert(
                    f"{self._consecutive_failures} consecutive failures talking to Ticketmaster.\n"
                    f"Last error: {exc}\nStill retrying every {self.config.interval_seconds}s."
                )
                self.dispatcher.dispatch([alert])
                self._failure_alert_sent = True
                result.alerts = [alert]
            return result

        if self._consecutive_failures:
            LOG.info("Recovered after %d failed check(s)", self._consecutive_failures)
        self._consecutive_failures = 0
        self._failure_alert_sent = False

        previous = self.store.load()
        alerts = detect_changes(
            previous,
            events,
            now=now,
            enabled=self.config.alert_on,
            repeat_minutes=self.config.repeat_alert_minutes,
            artist=self.config.keyword,
        )
        if notify and alerts:
            self.dispatcher.dispatch(alerts)
        self.store.save(self.store.merge(previous, events, alerts, timestamp=now.isoformat()))
        return CheckResult(events=events, alerts=alerts, checked_at=now)

    # ------------------------------------------------------------------ #
    def next_delay(self) -> float:
        cfg = self.config
        spread = cfg.interval_seconds * cfg.jitter
        return max(1.0, cfg.interval_seconds + random.uniform(-spread, spread))

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in slices so Ctrl-C is felt immediately."""
        remaining = seconds
        while remaining > 0 and not self._stop:
            slice_len = min(1.0, remaining)
            self._sleep(slice_len)
            remaining -= slice_len

    def run_forever(self, max_iterations: Optional[int] = None) -> int:
        """Poll until stopped. Returns the number of checks performed."""
        iterations = 0
        while not self._stop:
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1
            try:
                result = self.check_once()
            except AuthError as exc:
                LOG.error("%s", exc)
                raise
            except KeyboardInterrupt:  # pragma: no cover - interactive
                self.request_stop()
                break

            delay = self.next_delay()
            if result.retry_after:
                delay = max(delay, float(result.retry_after))
            elif not result.ok:
                # Back off on trouble: 2x, 4x, 8x the interval, capped at 10 min.
                delay = min(delay * (2 ** min(self._consecutive_failures, 3)), 600.0)
            elif result.events:
                LOG.info(
                    "%d matching event(s), %d buyable right now",
                    len(result.events),
                    len(result.buyable),
                )
            else:
                LOG.info("No matching events yet")

            if self._stop or (max_iterations is not None and iterations >= max_iterations):
                break
            LOG.debug("Sleeping %.0fs", delay)
            self._interruptible_sleep(delay)
        return iterations
