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
        if country_code and event.country and normalize(event.country) != normalize(country_code):
            continue
        kept.append(event)
    return kept
