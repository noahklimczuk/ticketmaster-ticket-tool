"""The polling loop: ask Ticketmaster, compare, shout, sleep, repeat."""

from __future__ import annotations

import logging
import random
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .aggregate import MergedEvent, merge_listings
from .alerts import Alert, detect_changes, error_alert
from .config import Config
from .events import now_utc
from .matcher import filter_listings
from .notify import Dispatcher
from .providers.base import Listing, Provider, ProviderQuery, ProviderResult
from .state import StateStore

LOG = logging.getLogger(__name__)

# Consecutive API failures before we bother the user about it.
FAILURE_ALERT_THRESHOLD = 3


@dataclass
class CheckResult:
    events: List[MergedEvent] = field(default_factory=list)
    alerts: List[Alert] = field(default_factory=list)
    providers: List[ProviderResult] = field(default_factory=list)
    error: Optional[str] = None
    retry_after: Optional[float] = None
    checked_at: Optional[datetime] = None

    @property
    def provider_errors(self) -> Dict[str, str]:
        return {r.platform: r.error for r in self.providers if r.error}

    @property
    def cheapest(self) -> Optional[MergedEvent]:
        priced = [e for e in self.buyable if e.price_min is not None]
        return min(priced, key=lambda e: e.price_min) if priced else None

    @property
    def buyable(self) -> List[MergedEvent]:
        now = self.checked_at or now_utc()
        return [e for e in self.events if e.buyable(now)]

    @property
    def ok(self) -> bool:
        return self.error is None


class Monitor:
    def __init__(
        self,
        config: Config,
        providers: Sequence[Provider],
        store: StateStore,
        dispatcher: Dispatcher,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = now_utc,
        on_check: Optional[Callable[["CheckResult", float], None]] = None,
    ) -> None:
        self.config = config
        self.providers = list(providers)
        self.store = store
        self.dispatcher = dispatcher
        self._sleep = sleep
        self._clock = clock
        # Called with (result, seconds until the next poll) after every check.
        self.on_check = on_check
        self._stop = False
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
    def fetch(self) -> Tuple[List[MergedEvent], List[ProviderResult]]:
        """Ask every configured platform, then fold the answers into one list."""
        cfg = self.config
        query = ProviderQuery(
            keyword=cfg.keyword,
            cities=cfg.cities,
            country_code=cfg.country_code,
            attraction_id=cfg.attraction_id or "",
            strict=cfg.strict_artist_match,
        )
        results = [provider.collect(query) for provider in self.providers]

        listings: List[Listing] = []
        for result in results:
            listings.extend(result.listings)
            if result.error:
                LOG.warning("%s failed this round: %s", result.platform, result.error)

        matched = filter_listings(
            listings,
            keyword=cfg.keyword,
            cities=cfg.cities,
            strict=cfg.strict_artist_match,
            country_code=cfg.country_code or "",
        )
        events = merge_listings(matched)
        LOG.debug(
            "%d listing(s) from %d platform(s) -> %d show(s)",
            len(listings), len(results), len(events),
        )
        return events, results

    # ------------------------------------------------------------------ #
    def check_once(self, notify: bool = True) -> CheckResult:
        now = self._clock()
        self.checks += 1

        if not self.providers:
            return CheckResult(error="No ticket platforms are configured", checked_at=now)

        events, results = self.fetch()
        working = [r for r in results if r.ok]
        waits = [r.retry_after for r in results if r.retry_after]
        retry_after = max(waits) if waits else None

        if not working:
            # Every platform is down or misconfigured; that is a real failure.
            self._consecutive_failures += 1
            detail = "; ".join(f"{r.platform}: {r.error}" for r in results)
            LOG.error("Check failed (%d in a row): %s", self._consecutive_failures, detail)
            result = CheckResult(error=detail, providers=results, retry_after=retry_after, checked_at=now)
            if (
                notify
                and self._consecutive_failures >= FAILURE_ALERT_THRESHOLD
                and not self._failure_alert_sent
                and "error" in self.config.alert_on
            ):
                alert = error_alert(
                    f"{self._consecutive_failures} failed checks in a row.\n{detail}\n"
                    f"Still retrying every {self.config.interval_seconds}s."
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
            price_drop_percent=self.config.price_drop_percent,
        )
        if notify and alerts:
            self.dispatcher.dispatch(alerts)
        self.store.save(self.store.merge(previous, events, alerts, timestamp=now.isoformat()))
        return CheckResult(
            events=events, alerts=alerts, providers=results, retry_after=retry_after, checked_at=now
        )

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

            if self.on_check:
                try:
                    self.on_check(result, delay)
                except Exception:  # pragma: no cover - a UI must never break the loop
                    LOG.exception("on_check callback failed")

            if self._stop or (max_iterations is not None and iterations >= max_iterations):
                break
            LOG.debug("Sleeping %.0fs", delay)
            self._interruptible_sleep(delay)
        return iterations
