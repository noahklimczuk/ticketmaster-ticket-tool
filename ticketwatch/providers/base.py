"""What every ticket platform has to look like from the outside."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# Availability labels shared across platforms.
ON_SALE = "on_sale"
PRESALE = "presale"
SCHEDULED = "scheduled"
SOLD_OUT = "sold_out"
UNAVAILABLE = "unavailable"
CANCELLED = "cancelled"
UNKNOWN = "unknown"

BUYABLE = {ON_SALE, PRESALE}


class ProviderError(Exception):
    """One platform failed. The others carry on."""


class ProviderRateLimited(ProviderError):
    """Slow down - this platform said so."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class ProviderQuery:
    """What the user is looking for, in platform-neutral terms."""

    keyword: str = ""
    cities: Sequence[str] = ()
    country_code: str = ""
    attraction_id: str = ""
    strict: bool = True


PLATFORM_LABELS = {
    "ticketmaster": "Ticketmaster",
    "seatgeek": "SeatGeek",
    "bandsintown": "Bandsintown",
}


@dataclass
class Listing:
    """One platform's view of one show."""

    platform: str
    event_id: str
    title: str = ""
    artist: str = ""
    venue: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    local_date: str = ""          # YYYY-MM-DD
    local_time: str = ""          # HH:MM[:SS]
    url: str = ""
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    currency: str = ""
    fees_included: bool = False
    availability: str = UNKNOWN
    inventory_status: Optional[str] = None
    on_sale_at: Optional[str] = None      # ISO 8601 UTC
    listing_count: Optional[int] = None
    resale: bool = False
    note: str = ""

    # ---------------------------------------------------------------- #
    @property
    def buyable(self) -> bool:
        return self.availability in BUYABLE

    @property
    def has_price(self) -> bool:
        return self.price_min is not None

    @property
    def platform_label(self) -> str:
        return PLATFORM_LABELS.get(self.platform, self.platform.title())

    @property
    def when(self) -> str:
        if not self.local_date:
            return "date TBA"
        return f"{self.local_date} {self.local_time[:5]}" if self.local_time else self.local_date

    @property
    def where(self) -> str:
        return ", ".join(part for part in (self.venue, self.city, self.region) if part) or "venue TBA"

    def price_label(self) -> str:
        if self.price_min is None:
            return ""
        currency = f" {self.currency}" if self.currency else ""
        return f"{self.price_min:.2f}{currency}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Listing":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ProviderResult:
    """The outcome of asking one platform, error included."""

    platform: str
    listings: List[Listing] = field(default_factory=list)
    error: Optional[str] = None
    retry_after: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Provider:
    """Base class. Subclasses only have to fetch and normalise."""

    name = "provider"
    label = "Provider"
    #: What the user has to supply before this platform can be used at all.
    credential_hint = ""
    #: True when this platform reports prices we can compare.
    reports_prices = False

    def __init__(self, **kwargs: Any) -> None:
        self.enabled = True

    # -- construction ----------------------------------------------------
    @classmethod
    def from_config(cls, config) -> Optional["Provider"]:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- fetching --------------------------------------------------------
    def fetch(self, query: ProviderQuery) -> List[Listing]:  # pragma: no cover - overridden
        raise NotImplementedError

    def collect(self, query: ProviderQuery) -> ProviderResult:
        """Fetch, turning any failure into a result the caller can survive."""
        try:
            return ProviderResult(platform=self.name, listings=self.fetch(query))
        except ProviderRateLimited as exc:
            return ProviderResult(platform=self.name, error=str(exc), retry_after=exc.retry_after)
        except ProviderError as exc:
            return ProviderResult(platform=self.name, error=str(exc))
        except Exception as exc:  # a provider bug must not stop the others
            return ProviderResult(platform=self.name, error=f"{type(exc).__name__}: {exc}")
