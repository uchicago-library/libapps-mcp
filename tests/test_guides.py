import httpx
import pytest
from conftest import SITE

from uc_libapps_mcp.errors import LibAppsError


def ids(result):
    return [g["id"] for g in result["guides"]]


def test_search_with_query_uses_relevance_and_intersects(fake, make_service):
    result = make_service().search_guides("chemistry", None, None, None, 10, 0)
    assert result["source"] == "server"
    assert ids(result) == ["107", "101"]
    assert [g["rank"] for g in result["guides"]] == [1, 2]
    search_call = [r for r in fake.calls_to("/1.2/guides") if "search_terms" in r.url.params][0]
    assert search_call.url.params["sort_by"] == "relevance"
    list_call = [r for r in fake.calls_to("/1.2/guides") if "search_terms" not in r.url.params][0]
    assert list_call.url.params["status"] == "1"
    assert list_call.url.params["expand"] == "subjects"


def test_search_result_shape_and_owner(make_service):
    guide = make_service().search_guides("chemistry", None, None, None, 10, 0)["guides"][1]
    assert guide == {
        "id": "101",
        "name": "Chemistry Research",
        "url": "https://guides.example.edu/chemistry",
        "description": "Finding chemical literature and data",
        "type": "Subject Guide",
        "subjects": [{"id": "9001", "name": "Chemistry"}],
        "updated": "2026-01-15 10:00:00",
        "owner": {"name": "Ada Example", "profile_url": "https://guides.example.edu/prf.php?id=p501"},
        "rank": 2,
    }


def test_owner_omitted_without_public_profile_or_scope(fake, make_service):
    result = make_service().search_guides(None, "history", None, None, 10, 0)
    assert ids(result) == ["102"]
    assert "owner" not in result["guides"][0]
    fake.scopes = "guides_get"
    result = make_service().search_guides("chemistry", None, None, None, 10, 0)
    assert all("owner" not in g for g in result["guides"])


def test_search_falls_back_to_local_on_upstream_error(fake, make_service):
    def guides(request):
        if "search_terms" in request.url.params:
            return httpx.Response(500)
        return httpx.Response(200, json=fake._guides(request).json())

    fake.routes["/1.2/guides"] = guides
    result = make_service().search_guides("chemistry", None, None, None, 10, 0)
    assert result["source"] == "local"
    assert ids(result) == ["101", "107"]


def test_local_search_weights_and_status_type_filtering(make_service):
    result = make_service().search_guides(None, None, None, None, 50, 0)
    assert result["source"] == "local"
    assert set(ids(result)) == {"101", "102", "107"}
    assert make_service(include_private=True).search_guides(None, None, None, None, 50, 0)["total"] == 4


def test_private_requested_in_list_when_allowed(fake, make_service):
    make_service(include_private=True).search_guides(None, None, None, None, 10, 0)
    assert fake.calls_to("/1.2/guides")[0].url.params["status"] == "1,2"


def test_filters(make_service):
    service = make_service()
    assert ids(service.search_guides(None, None, None, "course", 10, 0)) == ["102"]
    assert ids(service.search_guides(None, None, None, "4", 10, 0)) == ["107"]
    assert ids(service.search_guides(None, "9001", None, None, 10, 0)) == ["101", "107"]
    assert ids(service.search_guides(None, None, "ada", None, 10, 0)) == ["101"]
    assert ids(service.search_guides(None, None, "grace", None, 10, 0)) == []


def test_owner_filter_requires_accounts_scope(fake, make_service):
    fake.scopes = "guides_get"
    with pytest.raises(LibAppsError) as excinfo:
        make_service().search_guides(None, None, "ada", None, 10, 0)
    assert excinfo.value.code == "scope_missing"


def test_pagination(make_service):
    service = make_service()
    first = service.search_guides(None, None, None, None, 2, 0)
    assert (first["total"], first["offset"], first["next_offset"]) == (3, 0, 2)
    second = service.search_guides(None, None, None, None, 2, 2)
    assert len(second["guides"]) == 1
    assert second["next_offset"] is None
    assert second["guides"][0]["rank"] == 3
    with pytest.raises(LibAppsError):
        service.search_guides(None, None, None, None, 0, 0)


def test_scope_gating_skips_endpoint(fake, make_service):
    fake.scopes = "subjects_get"
    with pytest.raises(LibAppsError) as excinfo:
        make_service().search_guides("x", None, None, None, 10, 0)
    assert excinfo.value.code == "scope_missing"
    assert "guides_get" in excinfo.value.message
    assert fake.api_calls == []


def test_get_guide_page_tree(fake, make_service):
    guide = make_service().get_guide("101")["guide"]
    assert guide["id"] == "101"
    assert guide["owner"]["name"] == "Ada Example"
    assert "owner" not in fake.calls_to("/1.2/guides/101")[0].url.params["expand"]
    pages = guide["pages"]
    assert [p["page_id"] for p in pages] == ["2001", "2005"]
    home = pages[0]
    assert home["parent_id"] == "0"
    assert "hidden" not in home
    assert home["box_count"] == 4
    assert [b["box_id"] for b in home["boxes"]] == ["3002", "3001", "3004", "3008"]
    assert home["boxes"][0] == {"box_id": "3002", "name": "Getting Started", "column": 1, "position": 0}
    assert [p["page_id"] for p in home["subpages"]] == ["2003", "2002", "2006"]
    assert home["subpages"][1]["url"] == "https://guides.example.edu/chemistry/spectra"
    assert home["subpages"][2]["redirect_url"] == "https://catalog.example.org/"
    assert home["subpages"][0]["subpages"] == []


def test_get_guide_include_private_shows_hidden(make_service):
    pages = make_service(include_private=True).get_guide("101")["guide"]["pages"]
    assert [p["page_id"] for p in pages] == ["2001", "2004", "2005"]
    assert pages[1]["hidden"] is True
    assert pages[1]["box_count"] == 0
    assert [p["page_id"] for p in pages[1]["subpages"]] == ["2007"]
    assert pages[0]["box_count"] == 5


@pytest.mark.parametrize("guide_id", ["103", "104", "105", "106"])
def test_get_guide_not_public(make_service, guide_id):
    with pytest.raises(LibAppsError) as excinfo:
        make_service().get_guide(guide_id)
    assert excinfo.value.code == "not_public"
    assert "Chemistry" not in excinfo.value.message


def test_private_guide_allowed_with_opt_in(make_service):
    assert make_service(include_private=True).get_guide("105")["guide"]["id"] == "105"


@pytest.mark.parametrize("guide_id, code", [("999", "not_found"), ("12345", "not_found"), ("abc", "invalid_input")])
def test_get_guide_errors(make_service, guide_id, code):
    with pytest.raises(LibAppsError) as excinfo:
        make_service().get_guide(guide_id)
    assert excinfo.value.code == code


def content(service, **kwargs):
    args = {"page_id": None, "include_subpages": False, "source": "auto", "max_chars": None, "offset": 0}
    args.update(kwargs)
    return service.get_guide_content("101", **args)


def test_content_default_page(fake, make_service):
    result = content(make_service())
    assert result["guide"] == {"id": "101", "name": "Chemistry Research", "url": "https://guides.example.edu/chemistry"}
    assert result["source"] == "html"
    assert fake.html_calls == [f"{SITE}/c.php?g=101&p=2001"]
    (page,) = result["pages"]
    assert page["page_id"] == "2001"
    assert [b["box_id"] for b in page["boxes"]] == ["3002", "3001", "3004", "3008"]
    assert page["boxes"][1]["title"] == "Article Databases"
    assert page["boxes"][3]["missing"] is True
    assert "[embedded: Ask a Librarian chat]" in page["boxes"][0]["markdown"]
    assert "(https://guides.example.edu/chemistry/spectra)" in page["boxes"][0]["markdown"]
    assert result["next_offset"] is None
    assert [p["page_id"] for p in result["outline"]] == ["2001", "2003", "2002", "2006", "2005"]


def test_content_with_subpages(fake, make_service):
    result = content(make_service(), page_id="2001", include_subpages=True)
    assert [p["page_id"] for p in result["pages"]] == ["2001", "2003", "2002"]
    assert sorted(fake.html_calls) == sorted(f"{SITE}/c.php?g=101&p={p}" for p in ("2001", "2002", "2003"))
    safety = result["pages"][1]
    assert safety["fallback"] == "guide_main"
    assert "Always wear goggles." in safety["boxes"][0]["markdown"]
    assert result["pages"][2]["boxes"][0]["markdown"].startswith("Use the spectral database.")


def test_content_subpage_fetch_failure_is_reported_per_page(fake, make_service):
    fake.pages[f"{SITE}/c.php?g=101&p=2002"] = httpx.Response(503, text="down")
    result = content(make_service(), page_id="2001", include_subpages=True)
    failed = result["pages"][2]
    assert failed["error"]["code"] == "upstream_error"
    assert failed["boxes"] == []
    assert result["pages"][0]["boxes"]


def test_content_redirect_page_not_fetched(fake, make_service):
    result = content(make_service(), page_id="2006")
    assert result["pages"] == [
        {"page_id": "2006", "name": "Library Catalog", "redirect_url": "https://catalog.example.org/"}
    ]
    assert fake.html_calls == []


@pytest.mark.parametrize("page_id", ["2004", "2007", "424242"])
def test_content_hidden_or_foreign_page_not_found(make_service, page_id):
    with pytest.raises(LibAppsError) as excinfo:
        content(make_service(), page_id=page_id)
    assert excinfo.value.code == "not_found"


def test_content_html_cached(fake, make_service):
    service = make_service()
    content(service)
    content(service)
    assert len(fake.html_calls) == 1
    assert len(fake.calls_to("/1.2/guides/101")) == 1


def test_content_truncation_and_offset(make_service):
    service = make_service()
    full = content(service, page_id="2001", include_subpages=True)
    sizes = [len(b["markdown"]) for p in full["pages"] for b in p["boxes"]]
    assert len(sizes) == 6

    budget = sizes[0] + 5
    first = content(service, page_id="2001", include_subpages=True, max_chars=budget)
    boxes = first["pages"][0]["boxes"]
    assert [b["box_id"] for b in boxes] == ["3002", "3001"]
    assert boxes[1]["truncated"] is True
    assert len(boxes[1]["markdown"]) == 5
    assert first["next_offset"] == 2

    rest = content(service, page_id="2001", include_subpages=True, offset=2)
    assert [b["box_id"] for b in rest["pages"][0]["boxes"]] == ["3004", "3008"]
    assert "Always wear goggles." in rest["pages"][1]["boxes"][0]["markdown"]
    assert rest["next_offset"] is None
    assert "truncated" not in full["pages"][0]["boxes"][1]


def test_content_api_text_fallback(fake, make_service):
    result = content(make_service(html_fetch=False), page_id="2002")
    assert result["source"] == "api"
    assert result["placement"] == "unknown"
    assert "page_id was ignored" in result["note"]
    assert [b["asset_id"] for b in result["blocks"]] == ["8001", "8003"]
    assert result["blocks"][0]["markdown"] == "Welcome to the [spectra page](https://guides.example.edu/chemistry/spectra)."
    assert "HIDDEN-ASSET" not in repr(result)
    assert fake.html_calls == []
    call = fake.calls_to("/1.2/assets")[0]
    assert call.url.params["guide_ids"] == "101"
    assert call.url.params["asset_types"] == "1"


def test_content_api_source_explicit_and_capped(make_service):
    result = content(make_service(), source="api", max_chars=10)
    assert result["blocks"][0]["truncated"] is True
    assert len(result["blocks"][0]["markdown"]) == 10
    assert result["next_offset"] == 1


def test_content_api_requires_assets_scope(fake, make_service):
    fake.scopes = "guides_get"
    with pytest.raises(LibAppsError) as excinfo:
        content(make_service(), source="api")
    assert excinfo.value.code == "scope_missing"


def test_content_html_blocked_when_disabled(make_service):
    with pytest.raises(LibAppsError) as excinfo:
        content(make_service(html_fetch=False), source="html")
    assert excinfo.value.code == "fetch_blocked"


def test_content_site_derived_from_guide_list(fake, make_service):
    result = content(make_service(site_url=None))
    assert result["pages"][0]["boxes"][0]["box_id"] == "3002"
    assert fake.calls_to("/1.2/guides")


def test_content_page_on_disallowed_host_blocked(fake, make_service):
    with pytest.raises(LibAppsError) as excinfo:
        content(make_service(site_url="https://other.example.edu"))
    assert excinfo.value.code == "fetch_blocked"
    assert fake.html_calls == []


def test_content_input_validation(make_service):
    for kwargs in ({"source": "pdf"}, {"max_chars": 0}, {"offset": -1}, {"page_id": "x"}):
        with pytest.raises(LibAppsError) as excinfo:
            content(make_service(), **kwargs)
        assert excinfo.value.code == "invalid_input"


def test_content_not_public(make_service):
    with pytest.raises(LibAppsError) as excinfo:
        make_service().get_guide_content("106", None, False, "auto", None, 0)
    assert excinfo.value.code == "not_public"
