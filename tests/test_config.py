from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ticketwatch.config import Config, ConfigError, build_config, env_overrides, find_default_config


class DefaultsTests(unittest.TestCase):
    def test_defaults_are_what_was_asked_for(self):
        cfg = Config(api_key="k")
        self.assertEqual(cfg.keyword, "Sienna Spiro")
        self.assertEqual(cfg.cities, ["Toronto"])
        self.assertEqual(cfg.country_code, "CA")
        self.assertEqual(cfg.interval_seconds, 60)
        cfg.validate()

    def test_missing_api_key_is_rejected_with_a_useful_message(self):
        with self.assertRaises(ConfigError) as ctx:
            Config().validate()
        self.assertIn("developer.ticketmaster.com", str(ctx.exception))

    def test_bandsintown_alone_is_not_enough(self):
        """It finds dates but cannot price or sell anything."""
        with self.assertRaises(ConfigError):
            Config(bandsintown_app_id="ticketwatch").validate()

    def test_seatgeek_alone_is_enough(self):
        Config(seatgeek_client_id="abc").validate()

    def test_platform_lists(self):
        cfg = Config(api_key="k", seatgeek_client_id="s")
        self.assertEqual(cfg.enabled_platforms, ["ticketmaster", "seatgeek", "bandsintown"])
        self.assertEqual(cfg.primary_platforms, ["ticketmaster", "seatgeek"])

    def test_seatgeek_id_is_redacted_too(self):
        self.assertEqual(Config(api_key="k", seatgeek_client_id="secret").redacted()["seatgeek_client_id"], "***")

    def test_absurd_intervals_are_rejected(self):
        with self.assertRaises(ConfigError):
            Config(api_key="k", interval_seconds=1).validate()

    def test_unknown_alert_kinds_are_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            Config(api_key="k", alert_on=["on_sale", "banana"]).validate()
        self.assertIn("banana", str(ctx.exception))

    def test_nothing_to_watch_is_rejected(self):
        with self.assertRaises(ConfigError):
            Config(api_key="k", keyword="", attraction_id=None).validate()

    def test_daily_request_estimate(self):
        self.assertEqual(Config(api_key="k", interval_seconds=60).daily_request_estimate, 2880)
        self.assertEqual(Config(api_key="k", interval_seconds=60, check_inventory=False).daily_request_estimate, 1440)

    def test_redacted_hides_secrets(self):
        cfg = Config(api_key="super-secret")
        cfg.notifiers.smtp_password = "hunter2"
        redacted = cfg.redacted()
        self.assertEqual(redacted["api_key"], "***")
        self.assertEqual(redacted["notifiers"]["smtp_password"], "***")


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, data) -> Path:
        path = self.dir / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_reads_a_config_file(self):
        path = self.write({"api_key": "abc", "keyword": "Someone Else", "cities": ["Montreal"],
                           "interval_seconds": 120, "notifiers": {"ntfy_topic": "my-topic"}})
        cfg = build_config(config_path=path, environ={})
        self.assertEqual(cfg.api_key, "abc")
        self.assertEqual(cfg.keyword, "Someone Else")
        self.assertEqual(cfg.cities, ["Montreal"])
        self.assertEqual(cfg.interval_seconds, 120)
        self.assertEqual(cfg.notifiers.ntfy_topic, "my-topic")

    def test_comment_keys_are_allowed(self):
        path = self.write({"_comment": "hello", "api_key": "abc"})
        self.assertEqual(build_config(config_path=path, environ={}).api_key, "abc")

    def test_comment_keys_are_allowed_inside_notifiers_too(self):
        path = self.write({"api_key": "abc", "notifiers": {"_note": "hello", "ntfy_topic": "t"}})
        self.assertEqual(build_config(config_path=path, environ={}).notifiers.ntfy_topic, "t")

    def test_unknown_notifier_keys_are_rejected(self):
        path = self.write({"api_key": "abc", "notifiers": {"emails": "typo"}})
        with self.assertRaises(ConfigError) as ctx:
            build_config(config_path=path, environ={})
        self.assertIn("emails", str(ctx.exception))

    def test_the_shipped_example_config_actually_loads(self):
        example = Path(__file__).resolve().parent.parent / "config.example.json"
        cfg = build_config(config_path=example, environ={})
        self.assertEqual(cfg.keyword, "Sienna Spiro")
        self.assertEqual(cfg.cities, ["Toronto"])

    def test_unknown_keys_are_rejected_loudly(self):
        path = self.write({"api_key": "abc", "keywords": "typo"})
        with self.assertRaises(ConfigError) as ctx:
            build_config(config_path=path, environ={})
        self.assertIn("keywords", str(ctx.exception))

    def test_invalid_json_is_reported_clearly(self):
        path = self.dir / "config.json"
        path.write_text("{oops", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            build_config(config_path=path, environ={})
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_environment_beats_the_file(self):
        path = self.write({"api_key": "from-file", "interval_seconds": 30})
        cfg = build_config(
            config_path=path,
            environ={"TICKETMASTER_API_KEY": "from-env", "TICKETWATCH_INTERVAL_SECONDS": "300"},
        )
        self.assertEqual(cfg.api_key, "from-env")
        self.assertEqual(cfg.interval_seconds, 300)

    def test_cli_beats_the_environment(self):
        cfg = build_config(
            cli_overrides={"interval_seconds": 15, "api_key": "from-cli"},
            environ={"TICKETMASTER_API_KEY": "from-env", "TICKETWATCH_INTERVAL_SECONDS": "300"},
        )
        self.assertEqual(cfg.api_key, "from-cli")
        self.assertEqual(cfg.interval_seconds, 15)

    def test_none_cli_values_do_not_clobber(self):
        cfg = build_config(cli_overrides={"keyword": None}, environ={"TICKETMASTER_API_KEY": "k"})
        self.assertEqual(cfg.keyword, "Sienna Spiro")

    def test_env_parses_lists_and_booleans(self):
        cfg = build_config(environ={
            "TICKETMASTER_API_KEY": "k",
            "TICKETWATCH_CITIES": "Toronto, Hamilton",
            "TICKETWATCH_CHECK_INVENTORY": "false",
            "TICKETWATCH_DESKTOP": "0",
        })
        self.assertEqual(cfg.cities, ["Toronto", "Hamilton"])
        self.assertFalse(cfg.check_inventory)
        self.assertFalse(cfg.notifiers.desktop)

    def test_email_can_be_configured_entirely_from_the_environment(self):
        cfg = build_config(environ={
            "TICKETMASTER_API_KEY": "k",
            "TICKETWATCH_EMAIL_TO": "someone@gmail.com",
            "TICKETWATCH_SMTP_PASSWORD": "app-password",
        })
        self.assertEqual(cfg.notifiers.smtp_host, "smtp.gmail.com")
        self.assertEqual(cfg.notifiers.smtp_user, "someone@gmail.com")
        self.assertTrue(cfg.notifiers.email_ready)

    def test_unrelated_env_vars_are_ignored(self):
        overrides = env_overrides({"PATH": "/usr/bin", "HOME": "/root"})
        self.assertEqual(overrides, {})

    def test_relative_paths_resolve_next_to_the_config_file(self):
        path = self.write({"api_key": "abc", "state_file": "state.json", "log_file": "watch.log"})
        cfg = build_config(config_path=path, environ={})
        self.assertEqual(cfg.state_file, str(self.dir.resolve() / "state.json"))
        self.assertEqual(cfg.log_file, str(self.dir.resolve() / "watch.log"))

    def test_absolute_paths_are_left_alone(self):
        path = self.write({"api_key": "abc", "state_file": "/var/tmp/somewhere.json"})
        self.assertEqual(build_config(config_path=path, environ={}).state_file, "/var/tmp/somewhere.json")

    def test_find_default_config(self):
        self.assertIsNone(find_default_config(self.dir))
        self.write({"api_key": "abc"})
        self.assertEqual(find_default_config(self.dir), self.dir / "config.json")


if __name__ == "__main__":
    unittest.main()
