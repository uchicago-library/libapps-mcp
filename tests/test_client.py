import logging

import httpx
import pytest
from conftest import SECRET, SITE, TOKEN, FakeLibApps, json_response, make_client

from libapps_mcp.errors import LibAppsError


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_token_cached_and_headers(fake):
    client = make_client(fake)
    client.get("subjects")
    client.get("subjects")
    assert fake.token_requests == 1
    request = fake.api_calls[0]
    assert request.headers["accept"] == "application/json"
    assert request.headers["accept-encoding"] == "gzip"
    assert request.headers["user-agent"].startswith("libapps-mcp/")


def test_token_request_form(fake):
    seen = {}
    original = fake.api

    def capture(request):
        if request.url.path == "/1.2/oauth/token":
            seen["method"] = request.method
            seen["body"] = request.content.decode()
        return original(request)

    client = make_client(fake)
    client._api._transport = httpx.MockTransport(capture)
    client.get("subjects")
    assert seen["method"] == "POST"
    assert "grant_type=client_credentials" in seen["body"]
    assert "client_id=12345" in seen["body"]


def test_token_refreshed_before_expiry(fake):
    clock = Clock()
    client = make_client(fake, clock=clock)
    client.get("subjects")
    clock.now += 3600 - 61
    client.get("subjects")
    assert fake.token_requests == 1
    clock.now += 2
    client.get("subjects")
    assert fake.token_requests == 2


def test_401_refreshes_and_retries_once(fake):
    responses = iter([httpx.Response(401), json_response([{"id": "1"}])])
    fake.routes["/1.2/subjects"] = lambda request: next(responses)
    client = make_client(fake)
    assert client.get("subjects") == [{"id": "1"}]
    assert fake.token_requests == 2


def test_second_401_is_auth_failed(fake):
    fake.routes["/1.2/subjects"] = httpx.Response(401)
    client = make_client(fake)
    with pytest.raises(LibAppsError) as excinfo:
        client.get("subjects")
    assert excinfo.value.code == "auth_failed"
    assert len(fake.calls_to("/1.2/subjects")) == 2


def test_bad_credentials_auth_failed_without_secret(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        return httpx.Response(400, json={"error": "invalid_client", "error_description": "bad"})

    client = make_client(FakeLibApps())
    client._api._transport = httpx.MockTransport(handler)
    with pytest.raises(LibAppsError) as excinfo:
        client.get("guides")
    assert excinfo.value.code == "auth_failed"
    assert excinfo.value.status_code == 400
    assert SECRET not in str(excinfo.value)
    assert SECRET not in caplog.text


def test_secret_and_token_never_logged_or_returned(fake, caplog):
    caplog.set_level(logging.DEBUG)
    client = make_client(fake)
    client.get("subjects")
    fake.routes["/1.2/az"] = httpx.Response(500)
    with pytest.raises(LibAppsError) as excinfo:
        client.get("az")
    text = caplog.text + str(excinfo.value) + repr(excinfo.value.payload())
    assert SECRET not in text
    assert TOKEN not in text


def test_transport_error_message_is_sanitized(fake):
    def boom(request):
        raise httpx.ConnectError(f"failed for {request.url} with {SECRET}")

    client = make_client(fake)
    client._api._transport = httpx.MockTransport(boom)
    with pytest.raises(LibAppsError) as excinfo:
        client.get("guides")
    assert excinfo.value.code == "upstream_error"
    assert "ConnectError" in excinfo.value.message
    assert "lgapi-us.libapps.com" in excinfo.value.message
    assert SECRET not in excinfo.value.message
    assert excinfo.value.__cause__ is None


@pytest.mark.parametrize("status, code", [(429, "rate_limited"), (503, "upstream_error")])
def test_retries_with_backoff_then_error(fake, status, code):
    sleeps = []
    fake.routes["/1.2/az"] = httpx.Response(status)
    client = make_client(fake, sleep=sleeps.append)
    with pytest.raises(LibAppsError) as excinfo:
        client.get("az")
    assert excinfo.value.code == code
    assert excinfo.value.status_code == status
    assert len(fake.calls_to("/1.2/az")) == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]


def test_retry_after_honored_then_success(fake):
    sleeps = []
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), json_response([])])
    fake.routes["/1.2/az"] = lambda request: next(responses)
    client = make_client(fake, sleep=sleeps.append)
    assert client.get("az") == []
    assert sleeps == [7.0]


def test_404_is_not_found(fake):
    client = make_client(fake)
    with pytest.raises(LibAppsError) as excinfo:
        client.get("nope")
    assert excinfo.value.code == "not_found"


def test_scope_parsing(fake):
    fake.scopes = "guides_get subjects_get"
    client = make_client(fake)
    assert client.has_scope("guides_get")
    assert not client.has_scope("accounts_get")
    with pytest.raises(LibAppsError) as excinfo:
        client.require_scope("accounts_get")
    assert excinfo.value.code == "scope_missing"
    assert "accounts_get" in excinfo.value.message


PAGE = f"{SITE}/c.php?g=101&p=2001"
HOSTS = {"guides.example.edu"}


def test_fetch_html_ok_without_auth_or_cookies(fake):
    seen = []
    original = fake.html

    def capture(request):
        seen.append(request)
        return original(request)

    client = make_client(fake)
    client._html._transport = httpx.MockTransport(capture)
    assert "s-lg-box-3001" in client.fetch_html(PAGE, HOSTS)
    assert "authorization" not in seen[0].headers
    assert "cookie" not in seen[0].headers


@pytest.mark.parametrize(
    "url",
    ["https://evil.example.com/page", "http://guides.example.edu/c.php?g=1", "file:///etc/passwd"],
)
def test_fetch_html_host_allowlist(fake, url):
    client = make_client(fake)
    with pytest.raises(LibAppsError) as excinfo:
        client.fetch_html(url, HOSTS)
    assert excinfo.value.code == "fetch_blocked"
    assert fake.html_calls == []


def test_fetch_html_redirect_to_foreign_host_blocked(fake):
    fake.pages[PAGE] = httpx.Response(302, headers={"location": "https://evil.example.com/x"})
    client = make_client(fake)
    with pytest.raises(LibAppsError) as excinfo:
        client.fetch_html(PAGE, HOSTS)
    assert excinfo.value.code == "fetch_blocked"
    assert fake.html_calls == [PAGE]


def test_fetch_html_follows_allowed_redirect(fake):
    target = f"{SITE}/chemistry"
    fake.pages[PAGE] = httpx.Response(301, headers={"location": "/chemistry"})
    fake.pages[target] = httpx.Response(200, text="<p>ok</p>", headers={"content-type": "text/html"})
    client = make_client(fake)
    assert client.fetch_html(PAGE, HOSTS) == "<p>ok</p>"


def test_fetch_html_redirect_limit(fake):
    fake.pages[PAGE] = httpx.Response(302, headers={"location": PAGE})
    client = make_client(fake)
    with pytest.raises(LibAppsError) as excinfo:
        client.fetch_html(PAGE, HOSTS)
    assert excinfo.value.code == "fetch_blocked"
    assert len(fake.html_calls) == 4


def test_fetch_html_oversize_blocked(fake):
    fake.pages[PAGE] = httpx.Response(
        200, content=b"x" * (2 * 1024 * 1024 + 10), headers={"content-type": "text/html"}
    )
    client = make_client(fake)
    with pytest.raises(LibAppsError) as excinfo:
        client.fetch_html(PAGE, HOSTS)
    assert excinfo.value.code == "fetch_blocked"


def test_fetch_html_oversize_stream_without_length_blocked(fake):
    def chunks():
        for _ in range(3):
            yield b"x" * (1024 * 1024)

    fake.pages[PAGE] = httpx.Response(200, content=chunks(), headers={"content-type": "text/html"})
    client = make_client(fake)
    with pytest.raises(LibAppsError) as excinfo:
        client.fetch_html(PAGE, HOSTS)
    assert excinfo.value.code == "fetch_blocked"


def test_fetch_html_non_html_blocked(fake):
    fake.pages[PAGE] = httpx.Response(200, json={"a": 1})
    client = make_client(fake)
    with pytest.raises(LibAppsError) as excinfo:
        client.fetch_html(PAGE, HOSTS)
    assert excinfo.value.code == "fetch_blocked"

