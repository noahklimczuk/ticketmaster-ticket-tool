from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tests.support import event_payload
from ticketwatch.alerts import EventRecord, make_alert
from ticketwatch.events import EventSnapshot
from ticketwatch.state import StateStore

NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


def snap(**kwargs) -> EventSnapshot:
    return EventSnapshot.from_api(event_payload(**kwargs))


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "nested" / "state.json"
        self.store = StateStore(str(self.path))

    def test_missing_file_loads_as_empty(self):
        self.assertEqual(self.store.load(), {})

    def test_save_then_load_round_trip(self):
        event = snap()
        records = self.store.merge({}, [event], [], timestamp=NOW.isoformat())
        self.store.save(records)

        loaded = self.store.load()
        self.assertEqual(list(loaded), [event.id])
        self.assertEqual(loaded[event.id].snapshot, event)
        self.assertEqual(loaded[event.id].first_seen, NOW.isoformat())

    def test_save_creates_parent_directories(self):
        self.store.save({})
        self.assertTrue(self.path.exists())

    def test_corrupt_file_is_ignored_rather_than_fatal(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json at all", encoding="utf-8")
        self.assertEqual(self.store.load(), {})

    def test_file_from_a_future_version_is_ignored(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"version": 99, "events": {}}), encoding="utf-8")
        self.assertEqual(self.store.load(), {})

    def test_unreadable_entries_are_skipped_but_the_rest_survive(self):
        event = snap()
        self.store.save(self.store.merge({}, [event], [], timestamp=NOW.isoformat()))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["events"]["junk"] = {"snapshot": "not an object"}
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(list(self.store.load()), [event.id])

    def test_no_temp_files_are_left_behind(self):
        self.store.save(self.store.merge({}, [snap()], [], timestamp=NOW.isoformat()))
        leftovers = [p.name for p in self.path.parent.iterdir() if p.name != "state.json"]
        self.assertEqual(leftovers, [])

    def test_merge_keeps_the_original_first_seen(self):
        event = snap()
        first = self.store.merge({}, [event], [], timestamp="2026-01-01T00:00:00+00:00")
        second = self.store.merge(first, [event], [], timestamp=NOW.isoformat())
        self.assertEqual(second[event.id].first_seen, "2026-01-01T00:00:00+00:00")
        self.assertEqual(second[event.id].last_seen, NOW.isoformat())

    def test_merge_records_when_an_alert_was_sent(self):
        event = snap()
        alert = make_alert("on_sale", event, NOW)
        records = self.store.merge({}, [event], [alert], timestamp=NOW.isoformat())
        self.assertEqual(records[event.id].last_alert_at, NOW.isoformat())
        self.assertEqual(records[event.id].last_alert_kind, "on_sale")

    def test_merge_preserves_a_previous_alert_timestamp_when_quiet(self):
        event = snap()
        previous = {event.id: EventRecord(snapshot=event, first_seen="x", last_seen="x",
                                          last_alert_at="2026-04-01T00:00:00+00:00", last_alert_kind="on_sale")}
        records = self.store.merge(previous, [event], [], timestamp=NOW.isoformat())
        self.assertEqual(records[event.id].last_alert_at, "2026-04-01T00:00:00+00:00")

    def test_merge_drops_events_that_disappeared(self):
        gone, still_here = snap(event_id="GONE"), snap(event_id="HERE")
        previous = self.store.merge({}, [gone, still_here], [], timestamp=NOW.isoformat())
        records = self.store.merge(previous, [still_here], [], timestamp=NOW.isoformat())
        self.assertEqual(list(records), ["HERE"])


if __name__ == "__main__":
    unittest.main()
