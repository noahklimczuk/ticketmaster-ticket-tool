from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.support import MockServer, event_payload, events_response
from ticketwatch.config import build_config
from ticketwatch.gui import GuiApp, make_server


class GuiTestCase(unittest.TestCase):
    """Every test drives the real panel against a local stand-in Ticketmaster."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.platform = MockServer()
        self.platform.__enter__()
        self.addCleanup(self.platform.__exit__)
        self.serve_events([event_payload()])

    def serve_events(self, events, inventory=None):
        self.platform.json_route("/discovery/v2/events.json", events_response(events))
        self.platform.json_route("/inventory-status/v1/availability", inventory or [])

    def app(self, **overrides) -> GuiApp:
        data = {
            "api_key": "TEST-KEY",
            "keyword": "Sienna Spiro",
            "cities": ["Toronto"],
            "state_file": "state.json",
            "interval_seconds": 5,
            "discovery_base_url": f"{self.platform.base_url}/discovery/v2",
            "inventory_base_url": f"{self.platform.base_url}/inventory-status/v1",
            "bandsintown_app_id": "",
            "notifiers": {"console": False, "desktop": False},
        }
        data.update(overrides)
        path = self.dir / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return GuiApp(build_config(config_path=path), path)


class SnapshotTests(GuiTestCase):
    def test_a_fresh_panel_is_stopped_and_empty(self):
        state = self.app().snapshot()
        self.assertFalse(state["running"])
        self.assertEqual(state["events"], [])
        self.assertIsNone(state["cheapest"])
        self.assertEqual(state["settings"]["keyword"], "Sienna Spiro")

    def test_credentials_never_reach_the_page(self):
        blob = json.dumps(self.app(seatgeek_client_id="SG-SECRET").snapshot())
        self.assertNotIn("TEST-KEY", blob)
        self.assertNotIn("SG-SECRET", blob)
        self.assertIn('"api_key_set": true', blob)

    def test_platform_rows_say_what_is_set_up(self):
        rows = {p["name"]: p for p in self.app().snapshot()["platforms"]}
        self.assertTrue(rows["ticketmaster"]["configured"])
        self.assertFalse(rows["seatgeek"]["configured"])
        self.assertIn("seatgeek.com", rows["seatgeek"]["hint"])

    def test_a_check_fills_the_page(self):
        state = self.app().check_now()
        self.assertEqual(len(state["events"]), 1)
        event = state["events"][0]
        self.assertEqual(event["when"], "2026-10-25 19:00")
        self.assertEqual(event["where"], "History, Toronto, ON")
        self.assertEqual(event["platforms"], ["ticketmaster"])
        self.assertEqual(event["quotes"][0]["price"], "59.50 CAD")
        self.assertTrue(any(link["label"] == "StubHub" for link in event["links"]))

    def test_the_cheapest_buyable_show_is_singled_out(self):
        self.serve_events([event_payload(), event_payload(event_id="B", local_date="2026-11-02", price_min=30.0)],
                          [{"eventId": "G5vYZ9abc123", "status": "AVAILABLE"},
                           {"eventId": "B", "status": "AVAILABLE"}])
        state = self.app().check_now()
        self.assertEqual(state["buyable_count"], 2)
        self.assertEqual(state["cheapest"]["price_min"], 30.0)

    def test_alerts_show_up_in_the_feed(self):
        state = self.app().check_now()
        self.assertEqual(len(state["alerts"]), 1)
        self.assertIn("New Sienna Spiro date found", state["alerts"][0]["title"])

    def test_a_platform_failure_is_shown_not_hidden(self):
        self.platform.json_route("/discovery/v2/events.json", {"fault": "down"}, status=503)
        app = self.app(max_retries=0)
        state = app.check_now()
        self.assertTrue(state["error"])
        self.assertTrue(any(row["error"] for row in state["platforms"]))

    def test_remembered_events_appear_before_the_first_check(self):
        app = self.app()
        app.check_now()
        reopened = self.app()  # same state file, fresh panel
        self.assertEqual(len(reopened.snapshot()["events"]), 1)


class LifecycleTests(GuiTestCase):
    def test_start_then_stop(self):
        app = self.app()
        self.addCleanup(app.stop)
        self.assertTrue(app.start()["running"])
        self.assertFalse(app.stop()["running"])

    def test_starting_twice_is_harmless(self):
        app = self.app()
        self.addCleanup(app.stop)
        app.start()
        self.assertTrue(app.start()["running"])

    def test_stopping_when_never_started(self):
        self.assertFalse(self.app().stop()["running"])

    def test_it_refuses_to_start_without_a_platform(self):
        app = self.app(api_key="")
        state = app.start()
        self.assertFalse(state["running"])
        self.assertIn("No ticket platform", state["error"])


class SettingsTests(GuiTestCase):
    def test_changes_are_saved_and_reloaded(self):
        app = self.app()
        state = app.update_settings({"keyword": "Someone Else", "cities": ["Montreal"], "interval_seconds": 120})
        self.assertEqual(state["settings"]["keyword"], "Someone Else")
        self.assertEqual(state["settings"]["cities"], ["Montreal"])
        saved = json.loads((self.dir / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["keyword"], "Someone Else")
        self.assertEqual(saved["interval_seconds"], 120)

    def test_the_saved_file_is_not_world_readable(self):
        app = self.app()
        app.update_settings({"api_key": "NEW-KEY"})
        self.assertEqual((self.dir / "config.json").stat().st_mode & 0o777, 0o600)

    def test_notifier_settings_go_to_the_right_place(self):
        app = self.app()
        state = app.update_settings({"notifiers": {"email_to": "me@example.test", "ntfy_topic": "topic"}})
        self.assertEqual(state["settings"]["email_to"], "me@example.test")
        self.assertEqual(state["settings"]["ntfy_topic"], "topic")

    def test_unknown_keys_are_ignored_rather_than_crashing(self):
        app = self.app()
        state = app.update_settings({"keyword": "Kept", "state_file": "/etc/passwd", "nonsense": 1})
        self.assertEqual(state["settings"]["keyword"], "Kept")
        self.assertNotIn("/etc/passwd", json.dumps(state))

    def test_settings_that_break_the_config_are_reported_at_once(self):
        """Not at Start time, when you have stopped looking at the form."""
        app = self.app()
        state = app.update_settings({"interval_seconds": 1})
        self.assertIn("interval_seconds", state["error"])

    def test_a_missing_api_key_is_pointed_out_while_you_are_still_editing(self):
        app = self.app(api_key="")
        state = app.update_settings({"keyword": "Anyone"})
        self.assertIn("No ticket platform", state["error"])

    def test_a_good_save_clears_the_error(self):
        app = self.app()
        app.update_settings({"interval_seconds": 1})
        self.assertIsNone(app.update_settings({"interval_seconds": 60})["error"])

    def test_a_running_watch_picks_up_new_settings(self):
        app = self.app()
        self.addCleanup(app.stop)
        app.start()
        state = app.update_settings({"keyword": "Someone Else"})
        self.assertTrue(state["running"])
        self.assertEqual(state["settings"]["keyword"], "Someone Else")


class HttpTests(GuiTestCase):
    def serve_panel(self, token: str = ""):
        app = self.app()
        server = make_server(app, "127.0.0.1", 0, token)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(lambda: (app.stop(), server.shutdown(), server.server_close()))
        return app, f"http://127.0.0.1:{server.server_address[1]}"

    def get(self, url, headers=None):
        request = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")

    def post(self, url, payload=None, headers=None):
        body = json.dumps(payload or {}).encode("utf-8")
        head = {"Content-Type": "application/json"}
        head.update(headers or {})
        request = urllib.request.Request(url, data=body, headers=head, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())

    def test_the_page_is_served(self):
        _, base = self.serve_panel()
        status, body = self.get(base + "/")
        self.assertEqual(status, 200)
        self.assertIn("ticketwatch", body)
        self.assertIn("/api/state", body)

    def test_state_endpoint(self):
        _, base = self.serve_panel()
        status, body = self.get(base + "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("settings", json.loads(body))

    def test_check_endpoint_returns_the_new_state(self):
        _, base = self.serve_panel()
        status, payload = self.post(base + "/api/check")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["events"]), 1)

    def test_start_and_stop_endpoints(self):
        _, base = self.serve_panel()
        self.assertTrue(self.post(base + "/api/start")[1]["running"])
        self.assertFalse(self.post(base + "/api/stop")[1]["running"])

    def test_settings_endpoint(self):
        _, base = self.serve_panel()
        _, payload = self.post(base + "/api/settings", {"keyword": "Changed"})
        self.assertEqual(payload["settings"]["keyword"], "Changed")

    def test_unknown_routes_are_404(self):
        _, base = self.serve_panel()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get(base + "/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_a_token_is_required_when_one_is_set(self):
        _, base = self.serve_panel(token="sekret")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get(base + "/api/state")
        self.assertEqual(ctx.exception.code, 403)

    def test_the_right_token_gets_in_by_query_or_header(self):
        _, base = self.serve_panel(token="sekret")
        self.assertEqual(self.get(base + "/api/state?token=sekret")[0], 200)
        self.assertEqual(self.get(base + "/api/state", {"X-Ticketwatch-Token": "sekret"})[0], 200)

    def test_the_page_carries_the_token_for_its_own_calls(self):
        _, base = self.serve_panel(token="sekret")
        _, body = self.get(base + "/?token=sekret")
        self.assertIn('"sekret"', body)
        self.assertNotIn("__TOKEN__", body)


if __name__ == "__main__":
    unittest.main()
