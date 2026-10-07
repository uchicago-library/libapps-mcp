"""Opt-in smoke tests against the real LibApps API. Assert on shapes and counts only; never print values."""

import os

import pytest

from libapps_mcp.config import load_settings
from libapps_mcp.service import Service

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("LIBAPPS_LIVE_TESTS") != "1"
        or not os.environ.get("LIBAPPS_CLIENT_ID")
        or not os.environ.get("LIBAPPS_CLIENT_SECRET"),
        reason="set LIBAPPS_LIVE_TESTS=1 and LibApps credentials to run live tests",
    ),
]


@pytest.fixture(scope="module")
def service():
    return Service(load_settings())


def test_live_search_and_guide_content(service):
    result = service.search_guides(None, None, None, None, 5, 0)
    total = result["total"]
    assert total > 0
    guide_id = result["guides"][0]["id"]
    pages_is_list = isinstance(service.get_guide(guide_id)["guide"]["pages"], list)
    assert pages_is_list
    content = service.get_guide_content(guide_id, None, False, "auto", 2000, 0)
    has_outline = isinstance(content.get("outline"), list)
    assert has_outline


def test_live_subjects_databases_librarians(service):
    subjects = service.list_subjects(True)
    subject_count = subjects["total"]
    assert subject_count > 0
    databases = service.search_databases(None, None, None, 5, 0)["databases"]
    database_count = len(databases)
    assert database_count > 0
    leaked = sum(1 for d in databases if "internal_note" in d or "library_review" in d)
    assert leaked == 0
    librarians = service.find_subject_librarians(subjects["subjects"][0]["id"])["librarians"]
    emails = sum(1 for lib in librarians if "email" in lib)
    assert emails == 0 or service.settings.expose_email
