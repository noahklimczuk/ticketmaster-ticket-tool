"""Deciding which of the events Ticketmaster hands back are the ones you want.

The Discovery API's own `city` filter is picky: a show at a Toronto venue whose
record says "North York" or "Etobicoke" is dropped server side. So by default we
ask the API only for the artist, then filter here where we can be generous.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Sequence

from .events import EventSnapshot

# Municipalities that are part of the same city but are recorded separately.
CITY_ALIASES = {
    "toronto": {"toronto", "north york", "scarborough", "etobicoke", "east york", "york", "downsview"},
    "new york": {"new york", "new york city", "nyc", "manhattan", "brooklyn", "queens", "bronx"},
    "los angeles": {"los angeles", "hollywood", "west hollywood", "inglewood"},
    "london": {"london", "wembley"},
}

# Platforms disagree on country format: Ticketmaster says "CA", Bandsintown
# says "Canada". Both mean the same place.
COUNTRY_ALIASES = {
    "ca": {"ca", "can", "canada"},
    "us": {"us", "usa", "united states", "united states of america", "america"},
    "gb": {"gb", "uk", "united kingdom", "great britain", "england", "scotland", "wales"},
    "ie": {"ie", "ireland"},
    "au": {"au", "australia"},
    "nz": {"nz", "new zealand"},
    "de": {"de", "germany", "deutschland"},
    "fr": {"fr", "france"},
    "nl": {"nl", "netherlands", "holland"},
    "es": {"es", "spain"},
    "it": {"it", "italy"},
    "mx": {"mx", "mexico"},
}

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation: 'Beyoncé - Live!' -> 'beyonce live'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _PUNCT.sub(" ", stripped.lower()).strip()


def city_variants(city: str) -> set:
    key = normalize(city)
    variants = {key}
    for canonical, aliases in CITY_ALIASES.items():
        if key == canonical or key in aliases:
            variants |= {normalize(a) for a in aliases}
            variants.add(canonical)
    return {v for v in variants if v}


def country_matches(actual: str, wanted: str) -> bool:
    """True when both strings name the same country, code or spelled out."""
    if not wanted or not actual:
        return True
    left, right = normalize(actual), normalize(wanted)
    if left == right:
        return True
    for group in COUNTRY_ALIASES.values():
        if left in group and right in group:
            return True
    return False


def canonical_city(city: str) -> str:
    """Fold a borough onto its city, so platforms that file a show differently
    ("North York" on one, "Toronto" on another) still line up as one show."""
    key = normalize(city)
    for canonical, aliases in CITY_ALIASES.items():
        if key == canonical or key in aliases:
            return canonical
    return key


def city_matches(event_city: str, wanted: Sequence[str]) -> bool:
    if not wanted:
        return True
    actual = normalize(event_city)
    if not actual:
        return False
    for city in wanted:
        if actual in city_variants(city):
            return True
    return False


def artist_matches(event: EventSnapshot, keyword: str, attraction_id: str = "", strict: bool = True) -> bool:
    if attraction_id and attraction_id in event.attraction_ids:
        return True
    if not keyword:
        return not attraction_id  # nothing to check against
    if not strict:
        return True
    target = normalize(keyword)
    if not target:
        return True
    haystacks = [normalize(event.name), *[normalize(a) for a in event.attractions]]
    return any(target in hay for hay in haystacks if hay)


def listing_matches(listing, keyword: str, attraction_id: str = "", strict: bool = True) -> bool:
    """Same artist test as filter_events, for a platform-neutral Listing."""
    if not keyword or not strict:
        return True
    target = normalize(keyword)
    if not target:
        return True
    haystacks = [normalize(listing.artist), normalize(listing.title)]
    return any(target in hay for hay in haystacks if hay)


def filter_listings(
    listings: Iterable,
    keyword: str = "",
    cities: Sequence[str] = (),
    strict: bool = True,
    country_code: str = "",
) -> List:
    """Keep only the listings for this artist, in these cities."""
    kept = []
    for listing in listings:
        if not listing_matches(listing, keyword, strict=strict):
            continue
        if not city_matches(listing.city, cities):
            continue
        if not country_matches(listing.country, country_code):
            continue
        kept.append(listing)
    return kept


def filter_events(
    events: Iterable[EventSnapshot],
    keyword: str = "",
    cities: Sequence[str] = (),
    attraction_id: str = "",
    strict: bool = True,
    country_code: str = "",
) -> List[EventSnapshot]:
    """Keep only the events for this artist, in these cities."""
    kept: List[EventSnapshot] = []
    for event in events:
        if not artist_matches(event, keyword, attraction_id, strict):
            continue
        if not city_matches(event.city, cities):
            continue
        if not country_matches(event.country, country_code):
            continue
        kept.append(event)
    return kept
