import json
import logging

import pytest
from conftest import SECRET, TOKEN

from libapps_mcp import server as server_mod

TOOL_NAMES = {
    "search_guides",
    "get_guide",
    "get_guide_content",
    "list_subjects",
    "search_databases",
    "get_database",
    "find_subject_librarians",
}

CALLS = [
    lambda: server_mod.search_guides(query="chemistry"),
    lambda: server_mod.search_guides(),
    lambda: server_mod.get_guide(guide_id="101"),
    lambda: server_mod.get_guide_content(guide_id="101", include_subpages=True),
    lambda: server_mod.get_guide_content(guide_id="101", source="api"),
    lambda: server_mod.list_subjects(),
    lambda: server_mod.search_databases(),
    lambda: server_mod.get_database(database_id="7001"),
    lambda: server_mod.find_subject_librarians(subject="chemistry"),
]


@pytest.fixture
def use_service(monkeypatch, make_service):
    def install(**overrides):
        monkeypatch.setattr(server_mod, "_service", make_service(**overrides))

    return install


async def test_lists_exactly_seven_read_only_tools():
    tools = await server_mod.mcp.list_tools()
    assert {t.name for t in tools} == TOOL_NAMES
    for tool in tools:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True


def test_every_tool_reports_config_missing(monkeypatch):
    monkeypatch.delenv("LIBAPPS_CLIENT_ID", raising=False)
    monkeypatch.delenv("LIBAPPS_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(server_mod, "_service", None)
    for call in CALLS:
        result = call()
        assert result["ok"] is False
        assert result["code"] == "config_missing"
        assert "LIBAPPS_CLIENT_SECRET" in result["error"]


def test_invalid_config_reported(monkeypatch):
    monkeypatch.setenv("LIBAPPS_CLIENT_ID", "12345")
    monkeypatch.setenv("LIBAPPS_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("LIBAPPS_MAX_CHARS", "lots")
    monkeypatch.setattr(server_mod, "_service", None)
    result = server_mod.list_subjects()
    assert result["code"] == "config_invalid"
    assert SECRET not in json.dumps(result)


def test_tools_return_ok_payloads(use_service):
    use_service()
    for call in CALLS:
        result = call()
        assert result["ok"] is True, result


def test_errors_become_payloads(use_service):
    use_service()
    assert server_mod.get_guide(guide_id="106") == {
        "ok": False,
        "error": "Guide 106 is not publicly available.",
        "code": "not_public",
    }
    assert server_mod.get_database(database_id="x")["code"] == "invalid_input"


def test_unexpected_errors_are_generic(use_service, fake, caplog):
    use_service()

    def broken(request):
        raise RuntimeError(f"boom {SECRET}")

    fake.routes["/1.2/subjects"] = broken
    caplog.set_level(logging.DEBUG)
    result = server_mod.list_subjects()
    assert result["ok"] is False
    assert SECRET not in json.dumps(result) + caplog.text


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


@pytest.mark.parametrize("expose_email", [False, True])
def test_privacy_invariant_across_all_tools(use_service, caplog, expose_email):
    caplog.set_level(logging.DEBUG)
    use_service(expose_email=expose_email)
    for call in CALLS:
        result = call()
        text = json.dumps(result)
        keys = set(_keys(result))
        assert not keys & {"internal_note", "library_review", "customer_id", "account_id", "public_id"}
        if not expose_email:
            assert "email" not in keys
        for forbidden in ("login@example.org", "login2@", "login3@", "INTERNAL-NOTE", "LIBRARY-REVIEW", SECRET, TOKEN):
            assert forbidden not in text
    assert SECRET not in caplog.text and TOKEN not in caplog.text


@pytest.mark.parametrize("expose_email", [False, True])
def test_email_addresses_in_page_text_are_redacted_by_default(use_service, expose_email):
    use_service(expose_email=expose_email)
    result = server_mod.get_guide_content(guide_id="101", page_id="2002")
    markdown = result["pages"][0]["boxes"][0]["markdown"]
    if expose_email:
        assert "mailto:ada.public@example.org" in markdown
    else:
        assert markdown == "Use the spectral database.\n\nQuestions: Ada Example or [email removed]"
