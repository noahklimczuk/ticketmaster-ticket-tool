from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.support import MockServer, event_payload, events_response
from ticketwatch import cli


class ArgvTests(unittest.TestCase):
    def test_no_arguments_means_watch(self):
        self.assertEqual(cli.normalize_argv([]), ["watch"])

    def test_bare_flags_imply_watch(self):
        self.assertEqual(cli.normalize_argv(["--once", "-i", "30"]), ["watch", "--once", "-i", "30"])

    def test_an_explicit_subcommand_is_left_alone(self):
        self.assertEqual(cli.normalize_argv(["check", "--json"]), ["check", "--json"])

    def test_help_is_not_swallowed(self):
        self.assertEqual(cli.normalize_argv(["--help"]), ["--help"])

    def test_cli_flags_map_onto_config_keys(self):
        args = cli.build_parser().parse_args([
            "watch", "--api-key", "abc", "-k", "Someone", "--city", "Toronto", "--city", "Hamilton",
            "-i", "45", "--ntfy", "topic", "--no-desktop", "--alert-on", "on_sale,sold_out",
        ])
        overrides = cli.overrides_from_args(args)
        self.assertEqual(overrides["api_key"], "abc")
        self.assertEqual(overrides["keyword"], "Someone")
        self.assertEqual(overrides["cities"], ["Toronto", "Hamilton"])
        self.assertEqual(overrides["interval_seconds"], 45)
        self.assertEqual(overrides["alert_on"], ["on_sale", "sold_out"])
        self.assertEqual(overrides["notifiers"], {"ntfy_topic": "topic", "desktop": False})

    def test_untouched_flags_are_left_out_of_the_overrides(self):
        args = cli.build_parser().parse_args(["watch"])
        self.assertEqual(cli.overrides_from_args(args), {})


class EndToEndTests(unittest.TestCase):
    """Drive the real CLI against a local stand-in for Ticketmaster."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.server = MockServer()
        self.server.__enter__()
        self.addCleanup(self.server.__exit__)

    def write_config(self, **extra) -> Path:
        data = {
            "api_key": "TEST-KEY",
            "keyword": "Sienna Spiro",
            "cities": ["Toronto"],
            "state_file": "state.json",
            "discovery_base_url": f"{self.server.base_url}/discovery/v2",
            "inventory_base_url": f"{self.server.base_url}/inventory-status/v1",
            "notifiers": {"console": False, "desktop": False},
        }
        data.update(extra)
        path = self.dir / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def serve(self, events, inventory=None):
        self.server.json_route("/discovery/v2/events.json", events_response(events))
        self.server.json_route("/inventory-status/v1/availability", inventory or [])

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    # -- check -----------------------------------------------------------
    def test_check_reports_a_buyable_event_and_exits_zero(self):
        self.serve([event_payload()], [{"eventId": "G5vYZ9abc123", "status": "AVAILABLE"}])
        config = self.write_config()
        code, out, _ = self.run_cli(["check", "-c", str(config)])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("BUY NOW", out)
        self.assertIn("History, Toronto, ON", out)

    def test_check_exits_one_when_nothing_is_buyable(self):
        self.serve([event_payload()], [{"eventId": "G5vYZ9abc123", "status": "SOLD_OUT"}])
        code, out, _ = self.run_cli(["check", "-c", str(self.write_config())])
        self.assertEqual(code, cli.EXIT_NOT_AVAILABLE)
        self.assertNotIn("BUY NOW", out)

    def test_check_json_is_parseable(self):
        self.serve([event_payload()], [{"eventId": "G5vYZ9abc123", "status": "AVAILABLE"}])
        code, out, _ = self.run_cli(["check", "-c", str(self.write_config()), "--json"])
        payload = json.loads(out)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(payload["buyable"], ["G5vYZ9abc123"])
        self.assertEqual(payload["events"][0]["venue"], "History")
        self.assertEqual(payload["alerts"][0]["kind"], "new_event")

    def test_check_says_so_when_there_is_nothing_yet(self):
        self.serve([])
        code, out, _ = self.run_cli(["check", "-c", str(self.write_config())])
        self.assertEqual(code, cli.EXIT_NOT_AVAILABLE)
        self.assertIn("no matching events", out)

    def test_check_reports_api_failure_on_stderr(self):
        self.server.json_route("/discovery/v2/events.json", {"fault": "boom"}, status=500)
        code, _, err = self.run_cli(["check", "-c", str(self.write_config(max_retries=0))])
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("error", err.lower())

    def test_a_bad_api_key_is_explained(self):
        self.server.json_route("/discovery/v2/events.json", {"fault": "Invalid ApiKey"}, status=401)
        code, _, err = self.run_cli(["check", "-c", str(self.write_config())])
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("API key", err)
        self.assertNotIn("TEST-KEY", err)

    # -- watch -----------------------------------------------------------
    def test_watch_once_runs_a_single_check_and_saves_state(self):
        self.serve([event_payload()])
        config = self.write_config()
        code, _, _ = self.run_cli(["watch", "-c", str(config), "--once"])
        self.assertEqual(code, cli.EXIT_OK)
        state = json.loads((self.dir / "state.json").read_text(encoding="utf-8"))
        self.assertIn("G5vYZ9abc123", state["events"])

    def test_watch_alerts_only_once_for_the_same_news(self):
        self.serve([event_payload()])
        config = self.write_config(notifiers={"console": True, "desktop": False})
        _, first, _ = self.run_cli(["watch", "-c", str(config), "--once"])
        _, second, _ = self.run_cli(["watch", "-c", str(config), "--once"])
        self.assertIn("New Sienna Spiro date found", first)
        self.assertEqual(second.strip(), "")

    # -- resolve / status / init ----------------------------------------
    def test_resolve_lists_artist_ids_and_dates(self):
        self.serve([event_payload()])
        self.server.json_route(
            "/discovery/v2/attractions.json",
            {"_embedded": {"attractions": [{"id": "K8vZ917qxR7", "name": "Sienna Spiro",
                                            "url": "https://www.ticketmaster.ca/artist/3376314"}]}},
        )
        code, out, _ = self.run_cli(["resolve", "-c", str(self.write_config())])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("K8vZ917qxR7", out)
        self.assertIn("Upcoming dates", out)

    def test_status_shows_what_is_remembered(self):
        self.serve([event_payload()])
        config = self.write_config()
        self.run_cli(["check", "-c", str(config)])
        code, out, _ = self.run_cli(["status", "-c", str(config)])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("1 event(s) remembered", out)

    def test_status_before_any_check(self):
        code, out, _ = self.run_cli(["status", "-c", str(self.write_config())])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("No state yet", out)

    def test_test_notify_sends_through_the_console(self):
        config = self.write_config(notifiers={"console": True, "desktop": False})
        code, out, _ = self.run_cli(["test-notify", "-c", str(config)])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("test alert", out)
        self.assertIn("Sent a test alert through: console", out)

    def test_test_notify_complains_when_nothing_is_configured(self):
        code, _, err = self.run_cli(["test-notify", "-c", str(self.write_config())])
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("No notification channels", err)

    def test_init_writes_a_starter_config(self):
        path = self.dir / "fresh.json"
        code, out, _ = self.run_cli(["init", str(path)])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["keyword"], "Sienna Spiro")
        self.assertIn("Wrote", out)

    def test_init_will_not_clobber_without_force(self):
        path = self.dir / "fresh.json"
        self.run_cli(["init", str(path)])
        code, _, err = self.run_cli(["init", str(path)])
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("already exists", err)
        self.assertEqual(self.run_cli(["init", str(path), "--force"])[0], cli.EXIT_OK)

    # -- config errors ---------------------------------------------------
    def test_a_missing_config_file_is_reported(self):
        code, _, err = self.run_cli(["check", "-c", str(self.dir / "nope.json")])
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("not found", err)

    def test_command_line_beats_the_config_file(self):
        # Inventory says AVAILABLE so the assertion does not depend on the wall clock.
        self.serve([event_payload(city="Ottawa")], [{"eventId": "G5vYZ9abc123", "status": "AVAILABLE"}])
        config = self.write_config()
        code, out, _ = self.run_cli(["check", "-c", str(config), "--city", "Ottawa"])
        self.assertIn("Ottawa", out)
        self.assertEqual(code, cli.EXIT_OK)


if __name__ == "__main__":
    unittest.main()
