"""Configuration loading for ticketwatch.

Precedence (later wins): built-in defaults -> JSON config file -> environment
variables -> command line flags.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_FILENAMES = ("config.json", "ticketwatch.json")

# Alert kinds that count as "you can spend money right now".
BUYABLE_ALERTS = ("new_event", "on_sale", "presale", "back_in_stock", "low_inventory")
ALL_ALERTS = BUYABLE_ALERTS + ("sold_out", "status_change", "price_change", "gone", "error")


class ConfigError(Exception):
    """Raised when the supplied configuration cannot be used."""


@dataclass
class NotifierSettings:
    """Where alerts get delivered. Every channel is optional and independent."""

    console: bool = True
    desktop: bool = True
    open_browser: bool = False
    ntfy_topic: Optional[str] = None
    ntfy_server: str = "https://ntfy.sh"
    webhook_url: Optional[str] = None
    command: Optional[str] = None
    email_to: Optional[str] = None
    email_from: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_starttls: bool = True


@dataclass
class Config:
    """Everything the monitor needs for one watch."""

    api_key: str = ""

    # --- what to look for -------------------------------------------------
    keyword: str = "Sienna Spiro"
    cities: List[str] = field(default_factory=lambda: ["Toronto"])
    country_code: str = "CA"
    state_code: Optional[str] = None
    attraction_id: Optional[str] = None
    venue_id: Optional[str] = None
    classification_name: Optional[str] = None
    radius: Optional[int] = None
    radius_unit: str = "km"
    strict_artist_match: bool = True
    # Ticketmaster's own city filter drops shows filed under a borough name
    # (a Toronto venue recorded as "North York"), so by default we ask the API
    # for the artist only and narrow the list ourselves in matcher.py.
    use_api_city_filter: bool = False

    # --- how often --------------------------------------------------------
    interval_seconds: int = 60
    jitter: float = 0.15
    timeout_seconds: int = 20
    max_retries: int = 3

    # --- what to shout about ---------------------------------------------
    alert_on: List[str] = field(default_factory=lambda: list(BUYABLE_ALERTS))
    repeat_alert_minutes: int = 0
    check_inventory: bool = True

    # --- plumbing ---------------------------------------------------------
    # Overridable so the test suite (or a mock) can point somewhere else.
    discovery_base_url: str = "https://app.ticketmaster.com/discovery/v2"
    inventory_base_url: str = "https://app.ticketmaster.com/inventory-status/v1"
    state_file: str = "state.json"
    log_level: str = "INFO"
    log_file: Optional[str] = None
    notifiers: NotifierSettings = field(default_factory=NotifierSettings)

    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        if not self.api_key:
            raise ConfigError(
                "No Ticketmaster API key. Get a free one at "
                "https://developer.ticketmaster.com/ then set TICKETMASTER_API_KEY "
                "or put \"api_key\" in your config file."
            )
        if self.interval_seconds < 5:
            raise ConfigError("interval_seconds must be at least 5")
        if not 0 <= self.jitter < 1:
            raise ConfigError("jitter must be between 0 and 1")
        unknown = sorted(set(self.alert_on) - set(ALL_ALERTS))
        if unknown:
            raise ConfigError(
                "Unknown alert kind(s): %s. Valid kinds: %s"
                % (", ".join(unknown), ", ".join(ALL_ALERTS))
            )
        if not self.keyword and not self.attraction_id:
            raise ConfigError("Set a keyword or an attraction_id - otherwise there is nothing to watch")

    @property
    def daily_request_estimate(self) -> int:
        """Rough calls/day, so we can warn before blowing the free quota (5000/day)."""
        per_check = 2 if self.check_inventory else 1
        return int(86400 / max(self.interval_seconds, 1)) * per_check

    def redacted(self) -> Dict[str, Any]:
        data = to_dict(self)
        if data.get("api_key"):
            data["api_key"] = "***"
        notifiers = data.get("notifiers", {})
        if notifiers.get("smtp_password"):
            notifiers["smtp_password"] = "***"
        return data


def to_dict(cfg: Config) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in fields(cfg):
        value = getattr(cfg, f.name)
        if isinstance(value, NotifierSettings):
            out[f.name] = {nf.name: getattr(value, nf.name) for nf in fields(value)}
        elif isinstance(value, list):
            out[f.name] = list(value)
        else:
            out[f.name] = value
    return out


def _coerce(target: Any, key: str, raw: Any) -> Any:
    """Convert a string (env/JSON) into the type the dataclass field expects."""
    current = getattr(target, key)
    if isinstance(raw, str):
        if isinstance(current, bool):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(current, int) and not isinstance(current, bool):
            return int(raw)
        if isinstance(current, float):
            return float(raw)
        if isinstance(current, list):
            return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(current, list) and isinstance(raw, (list, tuple)):
        return list(raw)
    return raw


def _apply_mapping(cfg: Config, data: Dict[str, Any], source: str) -> None:
    known = {f.name for f in fields(cfg)}
    notifier_known = {f.name for f in fields(NotifierSettings)}
    for key, value in data.items():
        if key == "notifiers":
            if not isinstance(value, dict):
                raise ConfigError(f'"notifiers" in {source} must be an object')
            for nkey, nvalue in value.items():
                if nkey not in notifier_known:
                    raise ConfigError(f'Unknown notifier option "{nkey}" in {source}')
                setattr(cfg.notifiers, nkey, _coerce(cfg.notifiers, nkey, nvalue))
        elif key in known:
            setattr(cfg, key, _coerce(cfg, key, value))
        elif key.startswith("_") or key == "$schema":
            continue  # comment-ish keys are allowed in the JSON file
        else:
            raise ConfigError(f'Unknown option "{key}" in {source}')


def load_config_file(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def find_default_config(start: Optional[Path] = None) -> Optional[Path]:
    base = start or Path.cwd()
    for name in DEFAULT_CONFIG_FILENAMES:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


# Environment variables are the same names as the config keys, upper-cased and
# prefixed, e.g. TICKETWATCH_INTERVAL_SECONDS, TICKETWATCH_NTFY_TOPIC.
ENV_PREFIX = "TICKETWATCH_"


def env_overrides(environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    environ = dict(os.environ if environ is None else environ)
    top_level = {f.name for f in fields(Config)} - {"notifiers"}
    notifier_level = {f.name for f in fields(NotifierSettings)}

    out: Dict[str, Any] = {}
    notifiers: Dict[str, Any] = {}
    for key, value in environ.items():
        if key == "TICKETMASTER_API_KEY" and value:
            out["api_key"] = value
            continue
        if not key.startswith(ENV_PREFIX):
            continue
        name = key[len(ENV_PREFIX):].lower()
        if name in top_level:
            out[name] = value
        elif name in notifier_level:
            notifiers[name] = value
    if notifiers:
        out["notifiers"] = notifiers
    return out


def build_config(
    config_path: Optional[Path] = None,
    cli_overrides: Optional[Dict[str, Any]] = None,
    environ: Optional[Dict[str, str]] = None,
) -> Config:
    """Assemble a Config from file + environment + CLI, in that order."""
    cfg = Config()
    if config_path is not None:
        _apply_mapping(cfg, load_config_file(config_path), str(config_path))
    _apply_mapping(cfg, env_overrides(environ), "environment")
    if cli_overrides:
        clean = {k: v for k, v in cli_overrides.items() if v is not None}
        _apply_mapping(cfg, clean, "command line")

    # Relative paths in a config file are resolved next to that file, so the
    # monitor can be launched from anywhere (cron, systemd, launchd).
    if config_path is not None:
        base = config_path.resolve().parent
        if not Path(cfg.state_file).is_absolute():
            cfg.state_file = str(base / cfg.state_file)
        if cfg.log_file and not Path(cfg.log_file).is_absolute():
            cfg.log_file = str(base / cfg.log_file)
    return cfg
