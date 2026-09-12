from __future__ import annotations

import unittest

from tests.support import event_payload
from ticketwatch.events import EventSnapshot
from ticketwatch.matcher import artist_matches, city_matches, filter_events, normalize


def snap(**kwargs) -> EventSnapshot:
    return EventSnapshot.from_api(event_payload(**kwargs))


class NormalizeTests(unittest.TestCase):
    def test_strips_case_accents_and_punctuation(self):
        self.assertEqual(normalize("Beyoncé - Live!"), "beyonce live")
        self.assertEqual(normalize("  SIENNA  SPIRO  "), "sienna spiro")
        self.assertEqual(normalize(""), "")


class CityTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(city_matches("Toronto", ["Toronto"]))

    def test_case_and_spacing_insensitive(self):
        self.assertTrue(city_matches("  toronto ", ["TORONTO"]))

    def test_toronto_boroughs_count_as_toronto(self):
        for borough in ("North York", "Scarborough", "Etobicoke", "East York"):
            self.assertTrue(city_matches(borough, ["Toronto"]), borough)

    def test_other_cities_do_not(self):
        for elsewhere in ("Hamilton", "Mississauga", "Ottawa", "Buffalo"):
            self.assertFalse(city_matches(elsewhere, ["Toronto"]), elsewhere)

    def test_empty_wanted_list_matches_everything(self):
        self.assertTrue(city_matches("Reykjavik", []))

    def test_blank_event_city_never_matches_a_requested_city(self):
        self.assertFalse(city_matches("", ["Toronto"]))


class ArtistTests(unittest.TestCase):
    def test_matches_on_attraction_name(self):
        event = snap(name="An Evening With Friends", attraction_name="Sienna Spiro")
        self.assertTrue(artist_matches(event, "Sienna Spiro"))

    def test_matches_on_event_name(self):
        event = snap(name="Sienna Spiro: My House Tour", attraction_name="Someone Else")
        self.assertTrue(artist_matches(event, "sienna spiro"))

    def test_rejects_a_different_artist(self):
        event = snap(name="Taylor Swift", attraction_name="Taylor Swift")
        self.assertFalse(artist_matches(event, "Sienna Spiro"))

    def test_attraction_id_wins_even_with_an_odd_name(self):
        event = snap(name="Secret Show", attraction_name="TBA", attraction_id="K8vZ917qxR7")
        self.assertTrue(artist_matches(event, "Sienna Spiro", attraction_id="K8vZ917qxR7"))

    def test_loose_mode_keeps_everything(self):
        event = snap(name="Taylor Swift", attraction_name="Taylor Swift")
        self.assertTrue(artist_matches(event, "Sienna Spiro", strict=False))


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            snap(event_id="A", city="Toronto"),
            snap(event_id="B", city="North York", venue="Some Arena"),
            snap(event_id="C", city="Montreal"),
            snap(event_id="D", city="Toronto", name="Taylor Swift", attraction_name="Taylor Swift",
                 attraction_id="K8vZ_other"),
            snap(event_id="E", city="Toronto", country="US"),
        ]

    def test_keeps_the_right_artist_in_the_right_city(self):
        kept = filter_events(self.events, keyword="Sienna Spiro", cities=["Toronto"])
        self.assertEqual([e.id for e in kept], ["A", "B", "E"])

    def test_country_filter_applies(self):
        kept = filter_events(self.events, keyword="Sienna Spiro", cities=["Toronto"], country_code="CA")
        self.assertEqual([e.id for e in kept], ["A", "B"])

    def test_no_city_filter_returns_every_date_for_the_artist(self):
        kept = filter_events(self.events, keyword="Sienna Spiro", cities=[])
        self.assertEqual([e.id for e in kept], ["A", "B", "C", "E"])


if __name__ == "__main__":
    unittest.main()
