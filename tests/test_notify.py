from __future__ import annotations

import io
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from tests.support import MockServer, event_payload
from ticketwatch.alerts import make_alert
from ticketwatch.config import NotifierSettings
from ticketwatch.events import EventSnapshot
from ticketwatch.notify import (
    BrowserNotifier,
    CommandNotifier,
    ConsoleNotifier,
    DesktopNotifier,
    Dispatcher,
    EmailNotifier,
    NtfyNotifier,
    WebhookNotifier,
    build_dispatcher,
)

NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


def alert(kind: str = "on_sale", repeat: bool = False):
    event = EventSnapshot.from_api(event_payload())
    event.inventory_status = "AVAILABLE"
    return make_alert(kind, event, NOW, artist="Sienna Spiro", repeat=repeat)


class ConsoleTests(unittest.TestCase):
    def test_prints_title_body_and_link(self):
        stream = io.StringIO()
        ConsoleNotifier(stream=stream, bell=False).send(alert())
        text = stream.getvalue()
        self.assertIn("TICKETS ON SALE", text)
        self.assertIn("History, Toronto, ON", text)
        self.assertIn("https://www.ticketmaster.ca/event/", text)

    def test_rings_the_bell_for_urgent_alerts_only(self):
        urgent, calm = io.StringIO(), io.StringIO()
        ConsoleNotifier(stream=urgent).send(alert("on_sale"))
        ConsoleNotifier(stream=calm).send(alert("sold_out"))
        self.assertIn("\a", urgent.getvalue())
        self.assertNotIn("\a", calm.getvalue())


class DesktopTests(unittest.TestCase):
    def test_macos_uses_osascript(self):
        calls = []
        DesktopNotifier(system="Darwin", runner=calls.append).send(alert())
        self.assertEqual(calls[0][0], "osascript")
        self.assertIn("display notification", calls[0][2])

    def test_linux_uses_notify_send_when_present(self):
        calls = []
        with mock.patch("ticketwatch.notify.shutil.which", return_value="/usr/bin/notify-send"):
            DesktopNotifier(system="Linux", runner=calls.append).send(alert())
        self.assertEqual(calls[0][0], "notify-send")
        self.assertIn("-u", calls[0])
        self.assertIn("critical", calls[0])

    def test_linux_without_notify_send_is_a_quiet_no_op(self):
        calls = []
        with mock.patch("ticketwatch.notify.shutil.which", return_value=None):
            DesktopNotifier(system="Linux", runner=calls.append).send(alert())
        self.assertEqual(calls, [])

    def test_windows_uses_powershell(self):
        calls = []
        DesktopNotifier(system="Windows", runner=calls.append).send(alert())
        self.assertEqual(calls[0][0], "powershell")


class BrowserTests(unittest.TestCase):
    def test_opens_for_an_urgent_alert(self):
        opened = []
        BrowserNotifier(opener=opened.append).send(alert("on_sale"))
        self.assertEqual(len(opened), 1)

    def test_stays_shut_for_non_urgent_and_repeat_alerts(self):
        opened = []
        BrowserNotifier(opener=opened.append).send(alert("sold_out"))
        BrowserNotifier(opener=opened.append).send(alert("on_sale", repeat=True))
        self.assertEqual(opened, [])


class NtfyTests(unittest.TestCase):
    def test_posts_to_the_topic_with_useful_headers(self):
        with MockServer() as server:
            NtfyNotifier("sienna-alerts", server=server.base_url).send(alert())
            request = server.requests[0]
        self.assertEqual(request["path"], "/sienna-alerts")
        self.assertEqual(request["headers"]["Priority"], "urgent")
        self.assertIn("TICKETS ON SALE", request["headers"]["Title"])
        self.assertIn("ticketmaster.ca", request["headers"]["Click"])
        self.assertIn("History, Toronto, ON", request["body"])

    def test_non_urgent_alerts_use_default_priority(self):
        with MockServer() as server:
            NtfyNotifier("topic", server=server.base_url).send(alert("sold_out"))
            self.assertEqual(server.requests[0]["headers"]["Priority"], "default")


class WebhookTests(unittest.TestCase):
    def test_slack_payload_is_a_text_field(self):
        payload = WebhookNotifier("https://hooks.slack.com/services/x")._payload(alert())
        self.assertEqual(set(payload), {"text"})
        self.assertIn("TICKETS ON SALE", payload["text"])

    def test_discord_payload_is_a_content_field(self):
        payload = WebhookNotifier("https://discord.com/api/webhooks/1/abc")._payload(alert())
        self.assertEqual(set(payload), {"content"})

    def test_generic_payload_includes_the_whole_event(self):
        payload = WebhookNotifier("https://example.test/hook")._payload(alert())
        self.assertEqual(payload["kind"], "on_sale")
        self.assertTrue(payload["urgent"])
        self.assertEqual(payload["event"]["city"], "Toronto")

    def test_posts_json_over_the_wire(self):
        with MockServer() as server:
            WebhookNotifier(f"{server.base_url}/hook").send(alert())
            request = server.requests[0]
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertEqual(json.loads(request["body"])["kind"], "on_sale")


class EmailTests(unittest.TestCase):
    def test_builds_a_sensible_message(self):
        sent = []
        settings = NotifierSettings(email_to="me@example.test", email_from="bot@example.test", smtp_host="smtp.test")
        EmailNotifier(settings, sender=sent.append).send(alert())
        message = sent[0]
        self.assertEqual(message["To"], "me@example.test")
        self.assertEqual(message["From"], "bot@example.test")
        self.assertIn("TICKETS ON SALE", message["Subject"])
        self.assertIn("ticketmaster.ca", message.get_content())


class CommandTests(unittest.TestCase):
    def test_passes_the_alert_through_environment_variables(self):
        calls = []
        CommandNotifier("say tickets", runner=lambda cmd, env: calls.append((cmd, env))).send(alert())
        cmd, env = calls[0]
        self.assertEqual(cmd, "say tickets")
        self.assertEqual(env["TICKETWATCH_KIND"], "on_sale")
        self.assertEqual(env["TICKETWATCH_CITY"], "Toronto")
        self.assertEqual(env["TICKETWATCH_URGENT"], "1")
        self.assertEqual(json.loads(env["TICKETWATCH_JSON"])["venue"], "History")


class BrokenNotifier:
    name = "broken"

    def send(self, _alert):
        raise OSError("network is on fire")


class ExplodingNotifier:
    name = "exploding"

    def send(self, _alert):
        raise ValueError("unexpected")


class DispatcherTests(unittest.TestCase):
    def test_one_broken_channel_does_not_stop_the_others(self):
        stream = io.StringIO()
        dispatcher = Dispatcher([BrokenNotifier(), ConsoleNotifier(stream=stream, bell=False)])
        delivered = dispatcher.dispatch([alert()])
        self.assertEqual(delivered, 1)
        self.assertIn("TICKETS ON SALE", stream.getvalue())

    def test_unexpected_exceptions_are_swallowed_too(self):
        dispatcher = Dispatcher([ExplodingNotifier()])
        self.assertEqual(dispatcher.dispatch([alert()]), 0)

    def test_channel_names(self):
        settings = NotifierSettings(console=True, desktop=False, ntfy_topic="t", webhook_url="https://example.test/h",
                                    command="echo hi", open_browser=True)
        dispatcher = build_dispatcher(settings)
        self.assertEqual(dispatcher.channel_names, ["console", "ntfy", "webhook", "command", "browser"])

    def test_email_needs_both_an_address_and_a_server(self):
        self.assertNotIn("email", build_dispatcher(NotifierSettings(email_to="me@example.test")).channel_names)
        self.assertIn(
            "email",
            build_dispatcher(NotifierSettings(email_to="me@example.test", smtp_host="smtp.test")).channel_names,
        )

    def test_everything_can_be_turned_off(self):
        self.assertEqual(build_dispatcher(NotifierSettings(console=False, desktop=False)).channel_names, [])


if __name__ == "__main__":
    unittest.main()
