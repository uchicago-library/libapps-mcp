"""LibApps API v1.2 client (OAuth client credentials) and safe public HTML fetcher."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Iterable
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .config import Settings
from .errors import LibAppsError

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_BASE = 0.5
MAX_RETRY_AFTER = 30.0
TOKEN_SKEW = 60
MAX_REDIRECTS = 3
MAX_HTML_BYTES = 2 * 1024 * 1024


def _no_cookies() -> CookieJar:
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


class LibAppsClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        html_transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._clock = clock
        self._api = httpx.Client(
            timeout=settings.timeout,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
            transport=transport,
            cookies=_no_cookies(),
        )
        self._html = httpx.Client(
            timeout=settings.timeout,
            headers={"User-Agent": settings.user_agent, "Accept": "text/html"},
            transport=html_transport,
            follow_redirects=False,
            cookies=_no_cookies(),
        )
        self._semaphore = threading.BoundedSemaphore(settings.max_concurrency)
        self._token_lock = threading.Lock()
        self._token: str | None = None
        self._token_expiry = 0.0
        self._scopes: frozenset[str] = frozenset()

    def close(self) -> None:
        self._api.close()
        self._html.close()

    def _backoff(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = response.headers.get("retry-after", "").strip()
            if retry_after.isdigit():
                return min(float(retry_after), MAX_RETRY_AFTER)
        return BACKOFF_BASE * 2**attempt + random.uniform(0, BACKOFF_BASE / 2)

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = urlsplit(url).hostname
        error: LibAppsError | None = None
        for attempt in range(MAX_ATTEMPTS):
            response = None
            try:
                with self._semaphore:
                    response = self._api.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                error = LibAppsError(
                    f"Request to {host} failed ({type(exc).__name__}).", "upstream_error"
                )
            else:
                if response.status_code != 429 and response.status_code < 500:
                    return response
                code = "rate_limited" if response.status_code == 429 else "upstream_error"
                error = LibAppsError(
                    f"LibApps API returned HTTP {response.status_code}.",
                    code,
                    status_code=response.status_code,
                )
            if attempt < MAX_ATTEMPTS - 1:
                self._sleep(self._backoff(attempt, response))
        assert error is not None
        raise error

    def _refresh_token(self) -> None:
        response = self._send(
            "POST",
            f"{self.settings.api_base}/1.2/oauth/token",
            data={
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "grant_type": "client_credentials",
            },
        )
        if response.status_code != 200:
            raise LibAppsError(
                f"LibApps authentication failed (HTTP {response.status_code}).",
                "auth_failed",
                status_code=response.status_code,
            )
        try:
            data = response.json()
            token = data["access_token"]
            expires_in = int(data.get("expires_in") or 3600)
        except (ValueError, KeyError, TypeError):
            raise LibAppsError(
                "LibApps token response was not understood.", "auth_failed"
            ) from None
        self._token = str(token)
        self._token_expiry = self._clock() + max(expires_in - TOKEN_SKEW, 0)
        self._scopes = frozenset(str(data.get("scope") or "").split())
        logger.info("LibApps token refreshed; scopes: %s", " ".join(sorted(self._scopes)))

    def _access_token(self) -> str:
        with self._token_lock:
            if self._token is None or self._clock() >= self._token_expiry:
                self._refresh_token()
            assert self._token is not None
            return self._token

    def _drop_token(self, token: str) -> None:
        with self._token_lock:
            if self._token == token:
                self._token = None

    def has_scope(self, scope: str) -> bool:
        self._access_token()
        return scope in self._scopes

    def require_scope(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise LibAppsError(
                f"The LibApps application is missing the '{scope}' scope needed for this tool.",
                "scope_missing",
            )

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{self.settings.api_base}/1.2/{path}"
        for _ in range(2):
            token = self._access_token()
            response = self._send(
                "GET", url, params=params, headers={"Authorization": f"Bearer {token}"}
            )
            logger.debug("GET /1.2/%s -> %s", path, response.status_code)
            if response.status_code != 401:
                break
            self._drop_token(token)
        else:
            raise LibAppsError(
                "LibApps authentication failed (HTTP 401).", "auth_failed", status_code=401
            )
        if response.status_code == 404:
            raise LibAppsError("Not found.", "not_found", status_code=404)
        if response.status_code >= 400:
            raise LibAppsError(
                f"LibApps API returned HTTP {response.status_code}.",
                "upstream_error",
                status_code=response.status_code,
            )
        try:
            return response.json()
        except ValueError:
            raise LibAppsError("LibApps API returned invalid JSON.", "upstream_error") from None

    def fetch_html(self, url: str, allowed_hosts: Iterable[str]) -> str:
        allowed = {h.lower() for h in allowed_hosts}
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            if parts.scheme != "https" or host not in allowed or parts.username:
                raise LibAppsError(
                    f"Fetching from host '{host}' is not allowed.", "fetch_blocked"
                )
            try:
                with self._semaphore, self._html.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    return self._read_html(response, host)
            except httpx.HTTPError as exc:
                raise LibAppsError(
                    f"Request to {host} failed ({type(exc).__name__}).", "upstream_error"
                ) from None
        raise LibAppsError("Too many redirects.", "fetch_blocked")

    def _read_html(self, response: httpx.Response, host: str) -> str:
        if response.status_code == 404:
            raise LibAppsError("Page not found.", "not_found", status_code=404)
        if response.status_code >= 400:
            raise LibAppsError(
                f"{host} returned HTTP {response.status_code}.",
                "upstream_error",
                status_code=response.status_code,
            )
        if "text/html" not in response.headers.get("content-type", "").lower():
            raise LibAppsError("Response was not HTML.", "fetch_blocked")
        length = response.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_HTML_BYTES:
            raise LibAppsError("Page exceeds the size limit.", "fetch_blocked")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_HTML_BYTES:
                raise LibAppsError("Page exceeds the size limit.", "fetch_blocked")
        return body.decode(response.charset_encoding or "utf-8", errors="replace")
