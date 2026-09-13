"""Shared JSON-over-HTTP plumbing: retries, backoff, and secret redaction.

Every platform we talk to gets the same treatment, so a flaky provider cannot
take the monitor down and no API key ever reaches a log file.
"""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Optional

LOG = logging.getLogger(__name__)

USER_AGENT = "ticketwatch/2.0 (+https://github.com/noahklimczuk/ticketmaster-ticket-tool)"


class HttpError(Exception):
    """Any failure talking to a remote API."""


class HttpAuthError(HttpError):
    """Credentials missing, wrong, or not entitled to this endpoint."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


class HttpRateLimited(HttpError):
    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every known secret with ***."""
    for secret in secrets:
        if secret and len(secret) > 3 and secret in text:
            text = text.replace(secret, "***")
    return text


class JsonHttpClient:
    """A small GET-only JSON client with sane retry behaviour."""

    def __init__(
        self,
        timeout: float = 20.0,
        max_retries: int = 3,
        opener: Optional[Any] = None,
        sleep=time.sleep,
        secrets: Iterable[str] = (),
        user_agent: str = USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self._opener = opener or urllib.request.build_opener()
        self._sleep = sleep
        self.secrets = [s for s in secrets if s]
        self.user_agent = user_agent

    def hide(self, text: str) -> str:
        return redact(text, self.secrets)

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        full = f"{url}?{urllib.parse.urlencode(params, doseq=True)}" if params else url
        safe = self.hide(full)
        request_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        request_headers.update(headers or {})

        attempt = 0
        while True:
            attempt += 1
            try:
                request = urllib.request.Request(full, headers=request_headers)
                with self._opener.open(request, timeout=self.timeout) as response:
                    payload = response.read().decode("utf-8", errors="replace")
                return json.loads(payload) if payload.strip() else {}

            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = self.hide(exc.read().decode("utf-8", errors="replace")[:400])
                except Exception:  # pragma: no cover - body is best effort
                    pass
                if exc.code in (401, 403):
                    raise HttpAuthError(f"{safe} rejected the credentials ({exc.code}): {body}", exc.code) from exc
                if exc.code == 429:
                    raise HttpRateLimited(
                        f"Rate limited: {body or 'quota exceeded'}", _retry_after(exc.headers.get("Retry-After"))
                    ) from exc
                if exc.code >= 500 and attempt <= self.max_retries:
                    self._backoff(attempt, f"HTTP {exc.code}")
                    continue
                raise HttpError(f"HTTP {exc.code} from {safe}: {body}") from exc

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt <= self.max_retries:
                    self._backoff(attempt, str(exc))
                    continue
                raise HttpError(f"Network error calling {safe}: {exc}") from exc

            except json.JSONDecodeError as exc:
                if attempt <= self.max_retries:
                    self._backoff(attempt, "bad JSON")
                    continue
                raise HttpError(f"{safe} returned invalid JSON: {exc}") from exc

    def _backoff(self, attempt: int, reason: str) -> None:
        delay = min(30.0, 2 ** (attempt - 1)) + random.uniform(0, 0.5)
        LOG.debug("Retrying in %.1fs after %s (attempt %d)", delay, reason, attempt)
        self._sleep(delay)


def _retry_after(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None
