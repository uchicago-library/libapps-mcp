import pytest

from uc_libapps_mcp.errors import LibAppsError


def test_list_subjects(fake, make_service):
    result = make_service().list_subjects(True)
    assert result["total"] == 3
    assert result["subjects"][2] == {"id": "9003", "name": "History of Science", "slug": "history-science", "parent_id": "0"}
    assert fake.calls_to("/1.2/subjects")[0].url.params["guide_published"] == "2"
    make_service().list_subjects(False)
    assert "guide_published" not in fake.calls_to("/1.2/subjects")[1].url.params


def test_librarians_public_profiles_only(make_service):
    result = make_service().find_subject_librarians("chemistry")
    assert result["subject"] == {"id": "9001", "name": "Chemistry"}
    assert [lib["name"] for lib in result["librarians"]] == ["Ada Example", "Alan Placeholder"]
    assert result["total"] == 2


def test_librarian_allowlist_and_display_gating(make_service):
    ada, alan = make_service().find_subject_librarians("Chemistry")["librarians"]
    assert ada == {
        "name": "Ada Example",
        "title": "Chemistry Librarian",
        "pronouns": "she/her",
        "profile_url": "https://guides.example.edu/prf.php?id=p501",
        "image_url": "https://images.example.org/ada.jpg",
        "subjects": [{"id": "9001", "name": "Chemistry"}],
        "phone": "555-0100",
        "address": "Room 101, Example Library",
        "website": "https://example.org/ada",
    }
    assert "image_url" not in alan
    assert "phone" not in alan
    assert "email" not in alan


def test_login_email_never_present_even_with_opt_in(make_service):
    result = make_service(expose_email=True).find_subject_librarians("chemistry")
    text = repr(result)
    for forbidden in ("login@example.org", "login2@", "login3@", "customer_id", "public_id", "widget", "account_id", "alan.public"):
        assert forbidden not in text
    assert result["librarians"][0]["email"] == "ada.public@example.org"


def test_subject_resolution(make_service):
    service = make_service()
    assert service.find_subject_librarians("history")["subject"]["id"] == "9002"
    assert service.find_subject_librarians("9003")["subject"]["name"] == "History of Science"
    assert service.find_subject_librarians("science")["subject"]["id"] == "9003"
    assert service.find_subject_librarians("history")["librarians"] == []
    with pytest.raises(LibAppsError) as excinfo:
        service.find_subject_librarians("hist")
    assert excinfo.value.code == "invalid_input"
    assert "History of Science" in excinfo.value.message
    with pytest.raises(LibAppsError) as excinfo:
        service.find_subject_librarians("astronomy")
    assert excinfo.value.code == "not_found"


def test_librarians_scope_gating(fake, make_service):
    fake.scopes = "subjects_get guides_get"
    with pytest.raises(LibAppsError) as excinfo:
        make_service().find_subject_librarians("chemistry")
    assert excinfo.value.code == "scope_missing"
    assert "accounts_get" in excinfo.value.message
    assert fake.calls_to("/1.2/accounts") == []
