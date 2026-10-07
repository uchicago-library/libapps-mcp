"""Settings loaded from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import __version__
from .errors import LibAppsError

DEFAULT_API_BASE = "https://lgapi-us.libapps.com"
API_HOSTS = frozenset(
    f"lgapi-{region}.libapps.com" for region in ("us", "ca", "eu", "au")
)
DEFAULT_USER_AGENT = (
    f"uc-libapps-mcp/{__version__} (+https://github.com/uchicago-library/uc-libapps-mcp)"
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    api_base: str = DEFAULT_API_BASE
    site_url: str | None = None
    allowed_hosts: frozenset[str] = frozenset()
    html_fetch: bool = True
    include_private: bool = False
    expose_email: bool = False
    ttl_list: int = 1800
    ttl_ref: int = 86400
    ttl_content: int = 900
    timeout: float = 30.0
    max_concurrency: int = 4
    max_chars: int = 20000
    user_agent: str = DEFAULT_USER_AGENT


def _invalid(name: str, expected: str) -> LibAppsError:
    return LibAppsError(f"{name} is invalid: expected {expected}.", "config_invalid")


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise _invalid(name, "1 or 0")


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise _invalid(name, "a positive integer") from None
    if value < 1:
        raise _invalid(name, "a positive integer")
    return value


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise _invalid(name, "a positive number") from None
    if value <= 0:
        raise _invalid(name, "a positive number")
    return value


def _api_base(env: Mapping[str, str]) -> str:
    raw = env.get("LIBAPPS_API_BASE", "").strip() or DEFAULT_API_BASE
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise _invalid("LIBAPPS_API_BASE", "an https URL")
    if host not in API_HOSTS and not _bool(env, "LIBAPPS_ALLOW_CUSTOM_API_BASE", False):
        raise _invalid(
            "LIBAPPS_API_BASE",
            "one of " + ", ".join(sorted(API_HOSTS)) + " (or set LIBAPPS_ALLOW_CUSTOM_API_BASE=1)",
        )
    path = parts.path.rstrip("/")
    if path.endswith("/1.2"):
        path = path[: -len("/1.2")]
    return f"https://{parts.netloc}{path}".rstrip("/")


def _site_url(env: Mapping[str, str]) -> str | None:
    raw = env.get("LIBGUIDES_SITE_URL", "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise _invalid("LIBGUIDES_SITE_URL", "an https URL")
    return f"https://{parts.netloc.lower()}"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    client_id = env.get("LIBAPPS_CLIENT_ID", "").strip()
    client_secret = env.get("LIBAPPS_CLIENT_SECRET", "").strip()
    missing = [
        name
        for name, value in (
            ("LIBAPPS_CLIENT_ID", client_id),
            ("LIBAPPS_CLIENT_SECRET", client_secret),
        )
        if not value
    ]
    if missing:
        raise LibAppsError(
            "LibApps credentials are not configured. Set " + " and ".join(missing) + ".",
            "config_missing",
        )
    allowed = frozenset(
        h.strip().lower()
        for h in env.get("LIBGUIDES_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    )
    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        api_base=_api_base(env),
        site_url=_site_url(env),
        allowed_hosts=allowed,
        html_fetch=_bool(env, "LIBAPPS_HTML_FETCH", True),
        include_private=_bool(env, "LIBAPPS_INCLUDE_PRIVATE", False),
        expose_email=_bool(env, "LIBAPPS_EXPOSE_EMAIL", False),
        ttl_list=_int(env, "LIBAPPS_CACHE_TTL_LIST", 1800),
        ttl_ref=_int(env, "LIBAPPS_CACHE_TTL_REF", 86400),
        ttl_content=_int(env, "LIBAPPS_CACHE_TTL_CONTENT", 900),
        timeout=_float(env, "LIBAPPS_TIMEOUT", 30.0),
        max_concurrency=_int(env, "LIBAPPS_MAX_CONCURRENCY", 4),
        max_chars=_int(env, "LIBAPPS_MAX_CHARS", 20000),
        user_agent=env.get("LIBAPPS_USER_AGENT", "").strip() or DEFAULT_USER_AGENT,
    )
