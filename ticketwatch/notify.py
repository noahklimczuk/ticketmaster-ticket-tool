"""Delivering alerts: terminal, desktop, phone, chat, email, or your own script.

Every channel is best effort. A broken webhook must never take the monitor down,
so each notifier swallows and logs its own failures.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import smtplib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape
from typing import Any, Dict, List, Optional, Sequence

from .alerts import Alert
from .config import NotifierSettings

LOG = logging.getLogger(__name__)

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"


def _colour_enabled(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


class Notifier:
    name = "notifier"

    def send(self, alert: Alert) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    name = "console"

    def __init__(self, stream=None, bell: bool = True) -> None:
        self.stream = stream or sys.stdout
        self.bell = bell

    def send(self, alert: Alert) -> None:
        colour = _colour_enabled(self.stream)
        if alert.urgent:
            tint = GREEN
        elif alert.kind in ("sold_out", "gone", "error"):
            tint = RED
        else:
            tint = YELLOW
        head = f"{BOLD}{tint}{alert.title}{RESET}" if colour else alert.title
        self.stream.write("\n" + head + "\n")
        for line in alert.body.splitlines():
            self.stream.write(f"  {line}\n")
        if alert.url:
            self.stream.write(f"  {alert.url}\n" if not colour else f"  {DIM}{alert.url}{RESET}\n")
        if self.bell and alert.urgent:
            self.stream.write("\a")
        self.stream.flush()


class DesktopNotifier(Notifier):
    """Native notification centre popups. Silently skipped where unsupported."""

    name = "desktop"

    def __init__(self, system: Optional[str] = None, runner=None) -> None:
        self.system = system or platform.system()
        self._run = runner or (lambda cmd: subprocess.run(cmd, check=False, capture_output=True, timeout=10))

    def send(self, alert: Alert) -> None:
        title = alert.title[:120]
        body = alert.body.replace("\n", " | ")[:240]
        if self.system == "Darwin":
            sound = "Glass" if alert.urgent else "Pop"
            script = (
                f'display notification {json.dumps(body)} with title {json.dumps(title)} '
                f'sound name "{sound}"'
            )
            self._run(["osascript", "-e", script])
        elif self.system == "Linux":
            if not shutil.which("notify-send"):
                LOG.debug("notify-send not installed; skipping desktop notification")
                return
            urgency = "critical" if alert.urgent else "normal"
            self._run(["notify-send", "-u", urgency, title, body])
        elif self.system == "Windows":
            script = (
                "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null; "
                "$n = New-Object System.Windows.Forms.NotifyIcon; "
                "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
                f"$n.ShowBalloonTip(10000, {json.dumps(title)}, {json.dumps(body)}, 'Info')"
            )
            self._run(["powershell", "-NoProfile", "-Command", script])
        else:  # pragma: no cover - exotic platforms
            LOG.debug("No desktop notification support for %s", self.system)


class BrowserNotifier(Notifier):
    """Pop the Ticketmaster page open the moment tickets are buyable."""

    name = "browser"

    def __init__(self, opener=None) -> None:
        self._open = opener or webbrowser.open

    def send(self, alert: Alert) -> None:
        if alert.urgent and alert.url and not alert.repeat:
            self._open(alert.url)


class HttpNotifier(Notifier):
    """Shared POST helper for the webhook-ish channels."""

    def __init__(self, opener=None, timeout: float = 10.0) -> None:
        self._opener = opener or urllib.request.build_opener()
        self.timeout = timeout

    def _post(self, url: str, data: bytes, headers: Dict[str, str]) -> None:
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with self._opener.open(request, timeout=self.timeout) as response:
            response.read()


class NtfyNotifier(HttpNotifier):
    """Push to a phone via ntfy.sh - no account, just pick a topic name."""

    name = "ntfy"

    def __init__(self, topic: str, server: str = "https://ntfy.sh", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.topic = topic.strip().strip("/")
        self.server = server.rstrip("/")

    def send(self, alert: Alert) -> None:
        headers = {
            "Title": alert.title.encode("ascii", "replace").decode("ascii")[:200],
            "Priority": "urgent" if alert.urgent else "default",
            "Tags": "tickets" if alert.urgent else "information_source",
            "Content-Type": "text/plain; charset=utf-8",
        }
        if alert.url:
            headers["Click"] = alert.url
            headers["Actions"] = f"view, Open Ticketmaster, {alert.url}"
        self._post(f"{self.server}/{self.topic}", alert.body.encode("utf-8"), headers)


class WebhookNotifier(HttpNotifier):
    """Slack, Discord, or any endpoint that accepts a JSON POST."""

    name = "webhook"

    def __init__(self, url: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.url = url

    def _payload(self, alert: Alert) -> Dict[str, Any]:
        host = urllib.parse.urlparse(self.url).netloc.lower()
        text = alert.as_text()
        if "slack.com" in host:
            return {"text": text}
        if "discord" in host:
            return {"content": text[:1900]}
        payload: Dict[str, Any] = {
            "kind": alert.kind,
            "title": alert.title,
            "body": alert.body,
            "url": alert.url,
            "urgent": alert.urgent,
            "repeat": alert.repeat,
        }
        if alert.event:
            payload["event"] = alert.event.to_dict()
        return payload

    def send(self, alert: Alert) -> None:
        data = json.dumps(self._payload(alert)).encode("utf-8")
        self._post(self.url, data, {"Content-Type": "application/json"})


# Accent colours per alert mood, used in the HTML mail.
_ACCENTS = {
    "urgent": "#0b8a3d",
    "bad": "#b91c1c",
    "neutral": "#334155",
}


def _accent(alert: Alert) -> str:
    if alert.urgent:
        return _ACCENTS["urgent"]
    if alert.kind in ("sold_out", "gone", "error"):
        return _ACCENTS["bad"]
    return _ACCENTS["neutral"]


def email_subject(alert: Alert) -> str:
    """Readable on a phone lock screen without opening anything."""
    return f"\U0001F39F\uFE0F {alert.title}" if alert.urgent else alert.title


def email_text(alert: Alert) -> str:
    lines = [alert.title, "", alert.body]
    if alert.url:
        lines += ["", f"Buy now: {alert.url}"]
    lines += ["", "-- ", "Sent by ticketwatch, which is watching Ticketmaster for you."]
    return "\n".join(lines)


def email_html(alert: Alert) -> str:
    """A plain, table-based card - the kind of HTML mail clients render properly.

    The point of the whole thing is the button: one tap from the notification to
    the Ticketmaster page.
    """
    accent = _accent(alert)
    event = alert.event
    rows = []
    if event:
        label = event.availability()
        status = label.replace("_", " ").capitalize()
        # "Few left - Ticketmaster says few tickets left" says it twice; only add
        # the raw status when the label was not derived from it.
        if event.inventory_status and label not in ("few_left", "sold_out"):
            status += f" - Ticketmaster says {event.inventory_status.replace('_', ' ').lower()}"
        detail_pairs = [
            ("Where", event.where),
            ("Status", status),
            ("Price", event.price_range),
            ("Limit", event.ticket_limit),
        ]
        for label, value in detail_pairs:
            if not value:
                continue
            rows.append(
                f'<tr>'
                f'<td style="padding:4px 16px 4px 0;color:#64748b;font-size:13px;'
                f'white-space:nowrap;vertical-align:top;">{escape(label)}</td>'
                f'<td style="padding:4px 0;color:#0f172a;font-size:14px;">{escape(value)}</td>'
                f'</tr>'
            )
    details = f'<table role="presentation" cellpadding="0" cellspacing="0">{"".join(rows)}</table>' if rows else ""

    button = ""
    if alert.url:
        button = (
            f'<a href="{escape(alert.url, quote=True)}" '
            f'style="display:inline-block;background:{accent};color:#ffffff;text-decoration:none;'
            f'font-size:16px;font-weight:bold;padding:14px 28px;border-radius:6px;">'
            f'Buy on Ticketmaster &rarr;</a>'
            f'<div style="margin-top:12px;font-size:12px;color:#94a3b8;word-break:break-all;">'
            f'{escape(alert.url)}</div>'
        )

    # The subject line carries the full headline; the body leads with the event
    # itself so it does not read as the same sentence twice.
    heading = escape(event.name) if event else escape(alert.title)
    subheading = escape(" - ".join(p for p in ((event.city if event else ""), (event.when if event else "")) if p))
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#f1f5f9;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
    style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:10px;
           border:1px solid #e2e8f0;border-top:4px solid {accent};">
    <tr><td style="padding:28px;">
      <div style="font-size:12px;font-weight:bold;letter-spacing:.08em;text-transform:uppercase;color:{accent};">
        {escape(alert.kind.replace('_', ' '))}
      </div>
      <h1 style="margin:8px 0 4px;font-size:22px;line-height:1.3;color:#0f172a;">{heading}</h1>
      <div style="margin-bottom:22px;font-size:15px;color:#475569;">{subheading}</div>
      {details}
      <div style="margin-top:26px;">{button}</div>
    </td></tr>
  </table>
  <div style="max-width:520px;margin:16px auto 0;font-size:12px;color:#94a3b8;text-align:center;">
    Sent by ticketwatch, which is watching Ticketmaster for you.
  </div>
</body></html>"""


class EmailNotifier(Notifier):
    """Sends an HTML mail whose whole purpose is the buy button."""

    name = "email"

    def __init__(self, settings: NotifierSettings, sender=None) -> None:
        self.settings = settings
        self._send = sender or self._smtp_send

    def build_message(self, alert: Alert) -> EmailMessage:
        settings = self.settings
        message = EmailMessage()
        message["Subject"] = email_subject(alert)
        message["From"] = settings.email_from or settings.smtp_user or "ticketwatch@localhost"
        message["To"] = settings.email_to or ""
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain="ticketwatch.local")
        message.set_content(email_text(alert))
        message.add_alternative(email_html(alert), subtype="html")
        return message

    def send(self, alert: Alert) -> None:
        self._send(self.build_message(alert))

    def _smtp_send(self, message: EmailMessage) -> None:  # pragma: no cover - needs a server
        settings = self.settings
        host = settings.smtp_host or "localhost"
        try:
            with smtplib.SMTP(host, settings.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                if settings.smtp_starttls:
                    smtp.starttls()
                    smtp.ehlo()
                if settings.smtp_user and settings.smtp_password:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            hint = ""
            if settings.needs_app_password:
                hint = (
                    " This provider rejects your normal password: create an app password"
                    " (Gmail: myaccount.google.com/apppasswords, needs 2-Step Verification on)"
                    " and use that instead."
                )
            raise smtplib.SMTPException(f"{host} refused the login.{hint}") from exc


class CommandNotifier(Notifier):
    """Run your own shell command; the alert arrives as TICKETWATCH_* env vars."""

    name = "command"

    def __init__(self, command: str, runner=None) -> None:
        self.command = command
        self._run = runner or (
            lambda cmd, env: subprocess.run(cmd, shell=True, env=env, check=False, timeout=30)
        )

    def send(self, alert: Alert) -> None:
        env = dict(os.environ)
        env.update(
            {
                "TICKETWATCH_KIND": alert.kind,
                "TICKETWATCH_TITLE": alert.title,
                "TICKETWATCH_BODY": alert.body,
                "TICKETWATCH_URL": alert.url,
                "TICKETWATCH_URGENT": "1" if alert.urgent else "0",
            }
        )
        if alert.event:
            env.update(
                {
                    "TICKETWATCH_EVENT_ID": alert.event.id,
                    "TICKETWATCH_EVENT_NAME": alert.event.name,
                    "TICKETWATCH_VENUE": alert.event.venue,
                    "TICKETWATCH_CITY": alert.event.city,
                    "TICKETWATCH_DATE": alert.event.when,
                    "TICKETWATCH_JSON": json.dumps(alert.event.to_dict()),
                }
            )
        self._run(self.command, env)


class Dispatcher:
    """Fans one alert out to every configured channel, tolerating breakage."""

    def __init__(self, notifiers: Sequence[Notifier]) -> None:
        self.notifiers = list(notifiers)

    @property
    def channel_names(self) -> List[str]:
        return [n.name for n in self.notifiers]

    def dispatch(self, alerts: Sequence[Alert]) -> int:
        delivered = 0
        for alert in alerts:
            for notifier in self.notifiers:
                try:
                    notifier.send(alert)
                    delivered += 1
                except (urllib.error.URLError, OSError, smtplib.SMTPException, subprocess.SubprocessError) as exc:
                    LOG.error("Notifier %s failed: %s", notifier.name, exc)
                except Exception as exc:  # pragma: no cover - never kill the loop
                    LOG.exception("Notifier %s raised unexpectedly: %s", notifier.name, exc)
        return delivered


def build_dispatcher(settings: NotifierSettings, stream=None) -> Dispatcher:
    settings.apply_email_defaults()
    notifiers: List[Notifier] = []
    if settings.console:
        notifiers.append(ConsoleNotifier(stream=stream))
    if settings.desktop:
        notifiers.append(DesktopNotifier())
    if settings.ntfy_topic:
        notifiers.append(NtfyNotifier(settings.ntfy_topic, settings.ntfy_server))
    if settings.webhook_url:
        notifiers.append(WebhookNotifier(settings.webhook_url))
    if settings.email_ready:
        notifiers.append(EmailNotifier(settings))
    if settings.command:
        notifiers.append(CommandNotifier(settings.command))
    if settings.open_browser:
        notifiers.append(BrowserNotifier())
    return Dispatcher(notifiers)
