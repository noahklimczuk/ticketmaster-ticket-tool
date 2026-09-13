from __future__ import annotations

import io
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from tests.support import MockServer, MockSMTPServer, event_payload
from ticketwatch.alerts import error_alert, make_alert
from ticketwatch.config import NotifierSettings
from ticketwatch.events import EventSnapshot
from ticketwatch.notify import (
    BrowserNotifier,
    CommandNotifier,
    ConsoleNotifier,
    DesktopNotifier,
    Dispatcher,
    EmailNotifier,
    email_html,
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
    def settings(self, **kwargs) -> NotifierSettings:
        kwargs.setdefault("email_to", "someone@gmail.test")
        kwargs.setdefault("smtp_host", "smtp.test")
        return NotifierSettings(**kwargs)

    def send(self, alert_obj=None, **kwargs):
        sent = []
        EmailNotifier(self.settings(**kwargs), sender=sent.append).send(alert_obj or alert())
        return sent[0]

    def part(self, message, subtype: str) -> str:
        return message.get_body(preferencelist=(subtype,)).get_content()

    def test_headers(self):
        message = self.send(email_from="bot@example.test")
        self.assertEqual(message["To"], "someone@gmail.test")
        self.assertEqual(message["From"], "bot@example.test")
        self.assertIn("TICKETS ON SALE", message["Subject"])
        self.assertTrue(message["Date"])
        self.assertTrue(message["Message-ID"])

    def test_urgent_subjects_are_marked_for_a_lock_screen(self):
        self.assertTrue(self.send().get("Subject").startswith("\U0001F39F"))
        self.assertFalse(self.send(alert("sold_out")).get("Subject").startswith("\U0001F39F"))

    def test_it_is_sent_as_both_plain_text_and_html(self):
        message = self.send()
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        self.assertIn("Buy now: https://www.ticketmaster.ca/event/", self.part(message, "plain"))
        self.assertIn("<html>", self.part(message, "html"))

    def test_the_html_carries_a_working_buy_button(self):
        html = self.part(self.send(), "html")
        self.assertIn('href="https://www.ticketmaster.ca/event/G5vYZ9abc123"', html)
        self.assertIn("Buy on Ticketmaster", html)

    def test_the_html_shows_the_details_that_matter(self):
        html = self.part(self.send(), "html")
        for expected in ("Sienna Spiro", "2026-10-25 19:00", "History, Toronto, ON", "59.50-149.00 CAD",
                         "says available"):
            self.assertIn(expected, html)

    def test_the_html_leads_with_the_event_not_a_repeat_of_the_subject(self):
        html = self.part(self.send(), "html")
        self.assertEqual(html.count("Sienna Spiro"), 1)
        self.assertIn("Toronto - 2026-10-25 19:00", html)

    def test_the_status_line_does_not_say_the_same_thing_twice(self):
        low = alert()
        low.event.inventory_status = "FEW_TICKETS_LEFT"
        html = email_html(low)
        self.assertIn("Few left", html)
        self.assertNotIn("says few tickets left", html)

    def test_inventory_is_shown_when_it_adds_something(self):
        self.assertIn("says available", email_html(alert()))

    def test_html_is_escaped(self):
        nasty = alert()
        nasty.event.venue = '<script>alert("x")</script>'
        nasty.event.name = "Tickets & <b>more</b>"
        html = email_html(nasty)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Tickets &amp; &lt;b&gt;more&lt;/b&gt;", html)

    def test_a_title_only_alert_is_escaped_too(self):
        rude = error_alert("boom")
        rude.title = "Trouble & <b>strife</b>"
        self.assertIn("Trouble &amp; &lt;b&gt;strife&lt;/b&gt;", email_html(rude))

    def test_an_alert_without_a_link_still_renders(self):
        no_link = alert()
        no_link.url = ""
        html = email_html(no_link)
        self.assertNotIn("Buy on Ticketmaster", html)
        self.assertIn("Sienna Spiro", html)

    def test_an_error_alert_has_no_event_block(self):
        html = email_html(error_alert("Ticketmaster is down"))
        self.assertIn("error", html)


class EmailDeliveryTests(unittest.TestCase):
    """Drive the real SMTP path against a local server."""

    def settings(self, server: MockSMTPServer) -> NotifierSettings:
        settings = NotifierSettings(
            email_to="noah@example.test",
            smtp_host=server.host,
            smtp_port=server.port,
            smtp_password="app-password",
            smtp_starttls=False,  # the mock server speaks plain SMTP
        )
        settings.apply_email_defaults()
        return settings

    def test_a_message_really_goes_out(self):
        with MockSMTPServer() as server:
            EmailNotifier(self.settings(server)).send(alert())
            self.assertEqual(len(server.messages), 1)
            wire = server.messages[0]
        self.assertIn("To: noah@example.test", wire)
        self.assertIn("From: noah@example.test", wire)
        self.assertIn("multipart/alternative", wire)
        self.assertIn("Buy on Ticketmaster", _decoded(wire))

    def test_it_authenticates(self):
        with MockSMTPServer() as server:
            EmailNotifier(self.settings(server)).send(alert())
            self.assertTrue(server.auth_attempts)

    def test_a_rejected_password_explains_app_passwords(self):
        with MockSMTPServer(reject_auth=True) as server:
            # Treat the mock host as one of the providers that demands an app password.
            with mock.patch("ticketwatch.config.APP_PASSWORD_REQUIRED", {server.host}):
                with self.assertRaises(Exception) as ctx:
                    EmailNotifier(self.settings(server)).send(alert())
        message = str(ctx.exception)
        self.assertIn("refused the login", message)
        self.assertIn("app password", message)

    def test_a_rejected_password_is_still_reported_without_the_hint(self):
        with MockSMTPServer(reject_auth=True) as server:
            with self.assertRaises(Exception) as ctx:
                EmailNotifier(self.settings(server)).send(alert())
        self.assertIn("refused the login", str(ctx.exception))

    def test_the_dispatcher_reports_a_bad_password_without_dying(self):
        with MockSMTPServer(reject_auth=True) as server:
            dispatcher = Dispatcher([EmailNotifier(self.settings(server))])
            self.assertEqual(dispatcher.dispatch([alert()]), 0)


def _decoded(wire: str) -> str:
    """Undo quoted-printable so assertions can read the HTML part."""
    import quopri

    return quopri.decodestring(wire.encode("utf-8", "replace")).decode("utf-8", "replace")


class EmailDefaultsTests(unittest.TestCase):
    def test_a_gmail_address_is_enough(self):
        settings = NotifierSettings(email_to="someone@gmail.com")
        settings.apply_email_defaults()
        self.assertEqual(settings.smtp_host, "smtp.gmail.com")
        self.assertEqual(settings.smtp_port, 587)
        self.assertEqual(settings.smtp_user, "someone@gmail.com")
        self.assertEqual(settings.email_from, "someone@gmail.com")
        self.assertTrue(settings.email_ready)
        self.assertTrue(settings.needs_app_password)

    def test_other_providers(self):
        for address, host in [
            ("a@outlook.com", "smtp-mail.outlook.com"),
            ("a@yahoo.com", "smtp.mail.yahoo.com"),
            ("a@icloud.com", "smtp.mail.me.com"),
            ("a@fastmail.com", "smtp.fastmail.com"),
        ]:
            settings = NotifierSettings(email_to=address)
            settings.apply_email_defaults()
            self.assertEqual(settings.smtp_host, host, address)

    def test_explicit_settings_are_never_overwritten(self):
        settings = NotifierSettings(email_to="me@gmail.com", smtp_host="smtp.work.test",
                                    smtp_port=2525, smtp_user="bot", email_from="bot@work.test")
        settings.apply_email_defaults()
        self.assertEqual(settings.smtp_host, "smtp.work.test")
        self.assertEqual(settings.smtp_port, 2525)
        self.assertEqual(settings.smtp_user, "bot")
        self.assertEqual(settings.email_from, "bot@work.test")

    def test_an_unknown_domain_needs_a_host_spelled_out(self):
        settings = NotifierSettings(email_to="me@some-company.test")
        settings.apply_email_defaults()
        self.assertIsNone(settings.smtp_host)
        self.assertFalse(settings.email_ready)

    def test_calling_it_twice_changes_nothing(self):
        settings = NotifierSettings(email_to="me@gmail.com")
        settings.apply_email_defaults()
        first = (settings.smtp_host, settings.smtp_port, settings.smtp_user, settings.email_from)
        settings.apply_email_defaults()
        self.assertEqual(first, (settings.smtp_host, settings.smtp_port, settings.smtp_user, settings.email_from))

    def test_no_address_means_no_guessing(self):
        settings = NotifierSettings()
        settings.apply_email_defaults()
        self.assertIsNone(settings.smtp_host)
        self.assertFalse(settings.email_ready)

    def test_the_dispatcher_picks_up_an_inferred_server(self):
        self.assertIn("email", build_dispatcher(NotifierSettings(email_to="me@gmail.com")).channel_names)


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
