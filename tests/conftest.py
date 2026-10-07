import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from libapps_mcp.client import LibAppsClient
from libapps_mcp.config import Settings
from libapps_mcp.service import Service

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "s3cr3t-FAKE-value"
TOKEN = "tok-FAKE-access-token"
ALL_SCOPES = "az_get subjects_get accounts_get assets_get guides_get"
SITE = "https://guides.example.edu"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def json_response(data: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data)


class FakeLibApps:
    """Mock LibApps API and public site. `routes` maps an API path to a handler or payload."""

    def __init__(self, scopes: str = ALL_SCOPES) -> None:
        self.scopes = scopes
        self.token_requests = 0
        self.api_calls: list[httpx.Request] = []
        self.html_calls: list[str] = []
        self.routes: dict[str, Any] = {
            "/1.2/guides": self._guides,
            "/1.2/guides/101": load("guide_101.json"),
            "/1.2/subjects": load("subjects.json"),
            "/1.2/az": load("az.json"),
            "/1.2/accounts": load("accounts.json"),
            "/1.2/assets": self._assets,
            "/1.2/guides/999": [],
        }
        for guide in load("guides.json"):
            self.routes.setdefault(f"/1.2/guides/{guide['id']}", [guide])
        self.pages: dict[str, Any] = {
            f"{SITE}/c.php?g=101&p={pid}": (FIXTURES / "html" / f"page_{pid}.html").read_text()
            for pid in ("2001", "2002", "2003")
        }

    def calls_to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.api_calls if r.url.path == path]

    def _guides(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("search_terms"):
            ranked = [g for g in load("guides.json") if g["id"] in (107, 106, 101)]
            ranked.sort(key=lambda g: [107, 106, 101].index(g["id"]))
            return json_response(ranked)
        return json_response(load("guides.json"))

    def _assets(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("expand") == "az_props":
            return json_response(load("az_props.json"))
        if request.url.params.get("guide_ids") == "101":
            return json_response(load("text_assets_101.json"))
        return json_response([])

    def api(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/1.2/oauth/token":
            self.token_requests += 1
            return json_response(
                {
                    "access_token": TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": self.scopes,
                }
            )
        self.api_calls.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        route = self.routes.get(request.url.path)
        if route is None:
            return json_response({"error": "not found"}, 404)
        if callable(route):
            return route(request)
        if isinstance(route, httpx.Response):
            return route
        return json_response(route)

    def html(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.html_calls.append(url)
        page = self.pages.get(url)
        if page is None:
            return httpx.Response(404, text="missing")
        if isinstance(page, httpx.Response):
            return page
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "client_id": "12345",
        "client_secret": SECRET,
        "site_url": SITE,
    }
    values.update(overrides)
    return Settings(**values)


def make_client(
    fake: FakeLibApps, settings: Settings | None = None, **kwargs: Any
) -> LibAppsClient:
    return LibAppsClient(
        settings or make_settings(),
        transport=httpx.MockTransport(fake.api),
        html_transport=httpx.MockTransport(fake.html),
        sleep=kwargs.pop("sleep", lambda seconds: None),
        **kwargs,
    )


@pytest.fixture
def fake() -> FakeLibApps:
    return FakeLibApps()


@pytest.fixture
def make_service(fake: FakeLibApps) -> Callable[..., Service]:
    def build(**overrides: Any) -> Service:
        settings = make_settings(**overrides)
        return Service(settings, client=make_client(fake, settings))

    return build
