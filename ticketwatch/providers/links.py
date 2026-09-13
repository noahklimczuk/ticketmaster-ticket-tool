"""Deep links to the marketplaces that have no public API.

StubHub, Vivid Seats and Gametime all gate their APIs behind partner approval,
and scraping them is both against their terms and a losing fight with their bot
protection. What we can do - and what actually helps - is hand you a search link
for the exact artist and city, one click from the alert.
"""

from __future__ import annotations

import urllib.parse
from typing import Dict, List

SEARCH_SITES = (
    ("stubhub", "StubHub", "https://www.stubhub.com/secure/search?q={query}"),
    ("vividseats", "Vivid Seats", "https://www.vividseats.com/search?searchTerm={query}"),
    ("gametime", "Gametime", "https://gametime.co/search?q={query}"),
    ("seatgeek_web", "SeatGeek", "https://seatgeek.com/search?search={query}"),
)


def search_links(artist: str, city: str = "") -> List[Dict[str, str]]:
    """One search URL per marketplace we cannot query directly."""
    terms = " ".join(part for part in (artist, city) if part).strip()
    if not terms:
        return []
    encoded = urllib.parse.quote_plus(terms)
    return [
        {"id": site_id, "label": label, "url": template.format(query=encoded)}
        for site_id, label, template in SEARCH_SITES
    ]
