import httpx
import pytest
from conftest import load

from libapps_mcp.errors import LibAppsError


def test_search_drops_hidden_and_strips_sensitive_fields(fake, make_service):
    result = make_service().search_databases(None, None, None, 20, 0)
    assert [d["id"] for d in result["databases"]] == ["7001", "7002"]
    text = repr(result)
    for forbidden in ("internal_note", "library_review", "INTERNAL-NOTE", "LIBRARY-REVIEW", "Hidden Chemistry"):
        assert forbidden not in text
    az_call = fake.calls_to("/1.2/az")[0]
    assert az_call.url.params["expand"] == "subjects,az_types"


def test_search_item_allowlist(make_service):
    item = make_service().search_databases("chemistry", None, None, 20, 0)["databases"][0]
    assert item == {
        "id": "7001",
        "name": "Chem Abstracts Example",
        "url": "https://chem.example.com/",
        "proxied": True,
        "vendor": "Example Chemical Society",
        "description": "Chemistry literature and substance database.",
        "alt_names": "SciChem Finder",
        "new": True,
        "popular": True,
        "subjects": [{"id": "9001", "name": "Chemistry"}],
        "types": ["Article Index"],
    }


def test_search_matches_alt_names_vendor_and_more_info(make_service):
    service = make_service()
    assert [d["id"] for d in service.search_databases("scichem", None, None, 20, 0)["databases"]] == ["7001"]
    assert [d["id"] for d in service.search_databases("press", None, None, 20, 0)["databases"]] == ["7002"]
    assert [d["id"] for d in service.search_databases("reaction", None, None, 20, 0)["databases"]] == ["7001"]
    assert service.search_databases("hidden", None, None, 20, 0)["databases"] == []


def test_search_filters_and_description_truncated(fake, make_service):
    az = load("az.json")
    az[1]["description"] = "<p>" + "word " * 200 + "</p>"
    fake.routes["/1.2/az"] = az
    service = make_service()
    by_subject = service.search_databases(None, "history", None, 20, 0)["databases"]
    assert [d["id"] for d in by_subject] == ["7002"]
    assert by_subject[0]["trial"] is True
    assert len(by_subject[0]["description"]) <= 303
    assert by_subject[0]["description"].endswith("...")
    assert [d["id"] for d in service.search_databases(None, None, "1", 20, 0)["databases"]] == ["7001"]


def test_enrichment_skipped_without_assets_scope(fake, make_service):
    fake.scopes = "az_get"
    item = make_service().search_databases("chem", None, None, 20, 0)["databases"][0]
    assert "alt_names" not in item and "new" not in item
    assert fake.calls_to("/1.2/assets") == []


def test_enrichment_failure_non_fatal(fake, make_service):
    fake.routes["/1.2/assets"] = httpx.Response(403)
    result = make_service().search_databases(None, None, None, 20, 0)
    assert result["total"] == 2


def test_get_database_full(make_service):
    database = make_service().get_database("7001")["database"]
    assert database["more_info"] == "Includes reaction search."
    assert database["description"] == "Chemistry literature and substance database."
    assert "internal_note" not in database and "library_review" not in database


def test_get_database_never_calls_item_endpoint(fake, make_service):
    make_service().get_database("7002")
    assert fake.calls_to("/1.2/az/7002") == []


def test_get_database_refreshes_once_on_miss(fake, make_service):
    service = make_service()
    service.search_databases(None, None, None, 20, 0)
    az = load("az.json")
    az.append({**az[0], "id": 7004, "name": "New Database"})
    fake.routes["/1.2/az"] = az
    assert service.get_database("7004")["database"]["name"] == "New Database"
    assert len(fake.calls_to("/1.2/az")) == 2
    with pytest.raises(LibAppsError) as excinfo:
        service.get_database("7999")
    assert excinfo.value.code == "not_found"
    assert len(fake.calls_to("/1.2/az")) == 3


def test_get_database_hidden_not_found_without_refresh(fake, make_service):
    with pytest.raises(LibAppsError) as excinfo:
        make_service().get_database("7003")
    assert excinfo.value.code == "not_found"
    assert len(fake.calls_to("/1.2/az")) == 1


def test_database_scope_gating(fake, make_service):
    fake.scopes = "guides_get"
    with pytest.raises(LibAppsError) as excinfo:
        make_service().search_databases(None, None, None, 20, 0)
    assert excinfo.value.code == "scope_missing"
    assert "az_get" in excinfo.value.message


def test_exact_name_match_ranks_first(fake, make_service):
    base = load("az.json")[0]
    fake.routes["/1.2/az"] = [
        {**base, "id": 1, "name": "Example Index for Alumni"},
        {**base, "id": 2, "name": "Archive via Example Index"},
        {**base, "id": 3, "name": "Example Index"},
    ]
    result = make_service().search_databases("example index", None, None, 10, 0)
    assert [d["name"] for d in result["databases"]] == [
        "Example Index",
        "Example Index for Alumni",
        "Archive via Example Index",
    ]
