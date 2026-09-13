"""Configuration loading for ticketwatch.

Precedence (later wins): built-in defaults -> JSON config file -> environment
variables -> command line flags.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_FILENAMES = ("config.json", "ticketwatch.json")

# Mail servers we can work out from the address, so the only thing you have to
# supply is your address and an app password.
SMTP_PROVIDERS = {
    "gmail.com": ("smtp.gmail.com", 587),
    "googlemail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp-mail.outlook.com", 587),
    "hotmail.com": ("smtp-mail.outlook.com", 587),
    "live.com": ("smtp-mail.outlook.com", 587),
    "msn.com": ("smtp-mail.outlook.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 587),
    "yahoo.ca": ("smtp.mail.yahoo.com", 587),
    "icloud.com": ("smtp.mail.me.com", 587),
    "me.com": ("smtp.mail.me.com", 587),
    "mac.com": ("smtp.mail.me.com", 587),
    "fastmail.com": ("smtp.fastmail.com", 587),
    # Proton needs Bridge running locally; these are its defaults.
    "proton.me": ("127.0.0.1", 1025),
    "protonmail.com": ("127.0.0.1", 1025),
}

# Providers that will reject your normal password outright.
APP_PASSWORD_REQUIRED = {"smtp.gmail.com", "smtp.mail.yahoo.com", "smtp.mail.me.com"}


# Alert kinds that count as "you can spend money right now".
BUYABLE_ALERTS = ("new_event", "on_sale", "presale", "back_in_stock", "low_inventory", "cheaper")
ALL_ALERTS = BUYABLE_ALERTS + (
    "new_platform", "sold_out", "status_change", "price_change", "gone", "error",
)


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

    def apply_email_defaults(self) -> None:
        """Fill in the boring SMTP bits from the address. Safe to call twice.

        Give it `email_to` and a password and everything else follows: you send
        to yourself, from yourself, through your provider's server.
        """
        if not self.email_to:
            return
        if not self.smtp_user:
            self.smtp_user = self.email_to
        if not self.email_from:
            self.email_from = self.smtp_user
        if not self.smtp_host:
            domain = (self.smtp_user or "").rpartition("@")[2].lower()
            provider = SMTP_PROVIDERS.get(domain)
            if provider:
                self.smtp_host, port = provider
                if self.smtp_port == 587:  # only override a port nobody chose
                    self.smtp_port = port

    @property
    def needs_app_password(self) -> bool:
        return (self.smtp_host or "") in APP_PASSWORD_REQUIRED

    @property
    def email_ready(self) -> bool:
        return bool(self.email_to and self.smtp_host)


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

    # --- other ticket platforms ------------------------------------------
    # Each is optional; the tool works with Ticketmaster alone and lights up
    # more price comparisons as you add credentials.
    seatgeek_client_id: Optional[str] = None
    seatgeek_currency: str = "USD"
    bandsintown_app_id: str = "ticketwatch"

    # --- how often --------------------------------------------------------
    interval_seconds: int = 60
    jitter: float = 0.15
    timeout_seconds: int = 20
    max_retries: int = 3

    # --- what to shout about ---------------------------------------------
    alert_on: List[str] = field(default_factory=lambda: list(BUYABLE_ALERTS))
    repeat_alert_minutes: int = 0
    check_inventory: bool = True
    #: How far the cheapest price must fall before it is worth telling you.
    price_drop_percent: float = 5.0

    # --- plumbing ---------------------------------------------------------
    # Overridable so the test suite (or a mock) can point somewhere else.
    discovery_base_url: str = "https://app.ticketmaster.com/discovery/v2"
    inventory_base_url: str = "https://app.ticketmaster.com/inventory-status/v1"
    seatgeek_base_url: str = "https://api.seatgeek.com/2/events"
    bandsintown_base_url: str = "https://rest.bandsintown.com"
    state_file: str = "state.json"
    log_level: str = "INFO"
    log_file: Optional[str] = None
    notifiers: NotifierSettings = field(default_factory=NotifierSettings)

    # ------------------------------------------------------------------ #
    @property
    def enabled_platforms(self) -> List[str]:
        names = []
        if self.api_key:
            names.append("ticketmaster")
        if self.seatgeek_client_id:
            names.append("seatgeek")
        if self.bandsintown_app_id:
            names.append("bandsintown")
        return names

    @property
    def primary_platforms(self) -> List[str]:
        """Platforms that can actually tell us about buying a ticket.

        Bandsintown is a discovery supplement with a self-chosen app id, so it
        does not on its own count as having configured the tool.
        """
        return [name for name in self.enabled_platforms if name != "bandsintown"]

    def validate(self) -> None:
        if not self.primary_platforms:
            raise ConfigError(
                "No ticket platform is configured. Get a free Ticketmaster API key at "
                "https://developer.ticketmaster.com/ then set TICKETMASTER_API_KEY "
                "or put \"api_key\" in your config file. "
                "SeatGeek (seatgeek_client_id) and Bandsintown (bandsintown_app_id) "
                "are optional extras for price comparison and new dates."
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
        if data.get("seatgeek_client_id"):
            data["seatgeek_client_id"] = "***"
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
                if nkey.startswith("_"):
                    continue  # comment-ish keys are allowed here too
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


def write_config_file(path: Path, data: Dict[str, Any]) -> None:
    """Write a config file and lock it down - it can hold a key and a password."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:  # pragma: no cover - unusual filesystems
        pass


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

    cfg.notifiers.apply_email_defaults()

    # Relative paths in a config file are resolved next to that file, so the
    # monitor can be launched from anywhere (cron, systemd, launchd).
    if config_path is not None:
        base = config_path.resolve().parent
        if not Path(cfg.state_file).is_absolute():
            cfg.state_file = str(base / cfg.state_file)
        if cfg.log_file and not Path(cfg.log_file).is_absolute():
            cfg.log_file = str(base / cfg.log_file)
    return cfg
