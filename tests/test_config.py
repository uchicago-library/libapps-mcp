import pytest
from conftest import SECRET

from uc_libapps_mcp.config import DEFAULT_API_BASE, load_settings
from uc_libapps_mcp.errors import LibAppsError

CREDS = {"LIBAPPS_CLIENT_ID": "12345", "LIBAPPS_CLIENT_SECRET": SECRET}


def test_defaults():
    settings = load_settings(CREDS)
    assert settings.api_base == DEFAULT_API_BASE
    assert settings.site_url is None
    assert settings.html_fetch is True
    assert settings.include_private is False
    assert settings.expose_email is False
    assert (settings.ttl_list, settings.ttl_ref, settings.ttl_content) == (1800, 86400, 900)
    assert (settings.timeout, settings.max_concurrency, settings.max_chars) == (30.0, 4, 20000)
    assert settings.user_agent.startswith("uc-libapps-mcp/0.1.0")


@pytest.mark.parametrize(
    "missing, names",
    [
        ({}, ["LIBAPPS_CLIENT_ID", "LIBAPPS_CLIENT_SECRET"]),
        ({"LIBAPPS_CLIENT_ID": "12345"}, ["LIBAPPS_CLIENT_SECRET"]),
        ({"LIBAPPS_CLIENT_SECRET": SECRET}, ["LIBAPPS_CLIENT_ID"]),
    ],
)
def test_missing_credentials(missing, names):
    with pytest.raises(LibAppsError) as excinfo:
        load_settings(missing)
    assert excinfo.value.code == "config_missing"
    for name in names:
        assert name in excinfo.value.message
    assert SECRET not in excinfo.value.message


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://lgapi-eu.libapps.com", "https://lgapi-eu.libapps.com"),
        ("https://lgapi-ca.libapps.com/", "https://lgapi-ca.libapps.com"),
        ("https://lgapi-au.libapps.com/1.2/", "https://lgapi-au.libapps.com"),
    ],
)
def test_api_base_normalized(value, expected):
    assert load_settings({**CREDS, "LIBAPPS_API_BASE": value}).api_base == expected


@pytest.mark.parametrize(
    "value",
    ["http://lgapi-us.libapps.com", "https://api.example.com", "https://user:pw@lgapi-us.libapps.com"],
)
def test_api_base_rejected(value):
    with pytest.raises(LibAppsError) as excinfo:
        load_settings({**CREDS, "LIBAPPS_API_BASE": value})
    assert excinfo.value.code == "config_invalid"


def test_custom_api_base_allowed_with_opt_in():
    settings = load_settings(
        {**CREDS, "LIBAPPS_API_BASE": "https://api.example.com/1.2", "LIBAPPS_ALLOW_CUSTOM_API_BASE": "1"}
    )
    assert settings.api_base == "https://api.example.com"


def test_site_and_hosts():
    settings = load_settings(
        {
            **CREDS,
            "LIBGUIDES_SITE_URL": "https://Guides.Example.edu/some/path",
            "LIBGUIDES_ALLOWED_HOSTS": " alias.libguides.com, Other.Example.org ,",
        }
    )
    assert settings.site_url == "https://guides.example.edu"
    assert settings.allowed_hosts == {"alias.libguides.com", "other.example.org"}


@pytest.mark.parametrize(
    "name, value",
    [
        ("LIBGUIDES_SITE_URL", "http://guides.example.edu"),
        ("LIBAPPS_HTML_FETCH", "maybe"),
        ("LIBAPPS_CACHE_TTL_LIST", "soon"),
        ("LIBAPPS_MAX_CONCURRENCY", "0"),
        ("LIBAPPS_TIMEOUT", "-1"),
    ],
)
def test_invalid_values(name, value):
    with pytest.raises(LibAppsError) as excinfo:
        load_settings({**CREDS, name: value})
    assert excinfo.value.code == "config_invalid"
    assert name in excinfo.value.message
    assert value not in excinfo.value.message


def test_flags_and_numbers():
    settings = load_settings(
        {
            **CREDS,
            "LIBAPPS_HTML_FETCH": "0",
            "LIBAPPS_INCLUDE_PRIVATE": "true",
            "LIBAPPS_EXPOSE_EMAIL": "1",
            "LIBAPPS_MAX_CHARS": "500",
            "LIBAPPS_TIMEOUT": "2.5",
        }
    )
    assert settings.html_fetch is False
    assert settings.include_private is True
    assert settings.expose_email is True
    assert settings.max_chars == 500
    assert settings.timeout == 2.5


def test_repr_redacts_credentials():
    settings = load_settings({"LIBAPPS_CLIENT_ID": "client-id-FAKE", "LIBAPPS_CLIENT_SECRET": SECRET})
    for text in (repr(settings), str(settings)):
        assert SECRET not in text
        assert "client-id-FAKE" not in text
