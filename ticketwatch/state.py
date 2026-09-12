"""Remember what we saw last time, so every alert is about something new."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence

from .alerts import Alert, EventRecord
from .events import EventSnapshot, now_utc

LOG = logging.getLogger(__name__)
STATE_VERSION = 1


class StateStore:
    """A tiny JSON document on disk: event id -> what we knew about it."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------ #
    def load(self) -> Dict[str, EventRecord]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("Ignoring unreadable state file %s (%s); starting fresh", self.path, exc)
            return {}
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            LOG.warning("State file %s has an unexpected format; starting fresh", self.path)
            return {}

        records: Dict[str, EventRecord] = {}
        for event_id, raw in (data.get("events") or {}).items():
            if not isinstance(raw, dict):
                continue
            snapshot_data = raw.get("snapshot")
            if not isinstance(snapshot_data, dict):
                continue
            try:
                snapshot = EventSnapshot.from_dict(snapshot_data)
            except (TypeError, ValueError) as exc:
                LOG.debug("Skipping unreadable state entry %s: %s", event_id, exc)
                continue
            records[str(event_id)] = EventRecord(
                snapshot=snapshot,
                first_seen=str(raw.get("first_seen") or ""),
                last_seen=str(raw.get("last_seen") or ""),
                last_alert_at=raw.get("last_alert_at"),
                last_alert_kind=raw.get("last_alert_kind"),
            )
        return records

    # ------------------------------------------------------------------ #
    def save(self, records: Dict[str, EventRecord]) -> None:
        payload = {
            "version": STATE_VERSION,
            "updated_at": now_utc().isoformat(),
            "events": {
                event_id: {
                    "snapshot": record.snapshot.to_dict(),
                    "first_seen": record.first_seen,
                    "last_seen": record.last_seen,
                    "last_alert_at": record.last_alert_at,
                    "last_alert_kind": record.last_alert_kind,
                }
                for event_id, record in records.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file then rename, so a crash mid-write cannot
        # leave a half-written state file behind.
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp", delete=False
        )
        try:
            with handle as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ #
    def merge(
        self,
        previous: Dict[str, EventRecord],
        current: Sequence[EventSnapshot],
        alerts: Iterable[Alert],
        timestamp: Optional[str] = None,
    ) -> Dict[str, EventRecord]:
        """Build the next generation of records. Vanished events are dropped."""
        stamp = timestamp or now_utc().isoformat()
        alerted = {a.event.id: a.kind for a in alerts if a.event and a.event.id}

        records: Dict[str, EventRecord] = {}
        for snapshot in current:
            old = previous.get(snapshot.id)
            record = EventRecord(
                snapshot=snapshot,
                first_seen=(old.first_seen if old and old.first_seen else stamp),
                last_seen=stamp,
                last_alert_at=(old.last_alert_at if old else None),
                last_alert_kind=(old.last_alert_kind if old else None),
            )
            if snapshot.id in alerted:
                record.last_alert_at = stamp
                record.last_alert_kind = alerted[snapshot.id]
            records[snapshot.id] = record
        return records
