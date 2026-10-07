"""Output allowlists. Every tool result is built here field by field, never by copying API dicts."""

from __future__ import annotations

import re
from typing import Any

from .html_extract import plain_text

SEARCH_DESCRIPTION_CHARS = 300
EMAIL_REDACTED = "[email removed]"
_MAILTO_LINK = re.compile(r"\[([^\]]*)\]\(mailto:[^)]*\)", re.IGNORECASE)
_EMAIL = re.compile(r"(?:mailto:)?[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip() == "1"


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_id(value: Any) -> str:
    return str(value).strip()


def subject_refs(raw: Any) -> list[dict[str, str]]:
    return [
        {"id": as_id(s.get("id")), "name": s.get("name") or ""}
        for s in raw or []
        if isinstance(s, dict)
    ]


def full_name(account: dict[str, Any]) -> str:
    return " ".join(
        part for part in (account.get("first_name"), account.get("last_name")) if part
    )


def owner_ref(account: dict[str, Any]) -> dict[str, Any]:
    return {"name": full_name(account), "profile_url": (account.get("profile") or {}).get("url")}


def guide_summary(raw: dict[str, Any], owner: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": as_id(raw.get("id")),
        "name": raw.get("name") or "",
        "url": raw.get("friendly_url") or raw.get("url"),
        "description": raw.get("description") or "",
        "type": raw.get("type_label"),
        "subjects": subject_refs(raw.get("subjects")),
        "updated": raw.get("updated"),
    }
    if owner:
        out["owner"] = owner
    return out


def box_ref(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "box_id": as_id(raw.get("id")),
        "name": raw.get("name") or "",
        "column": as_int(raw.get("column_id")),
        "position": as_int(raw.get("position")),
    }


def page_ref(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_id": as_id(raw.get("id")),
        "name": raw.get("name") or "",
        "parent_id": as_id(raw.get("parent_id") or "0"),
        "url": raw.get("friendly_url") or raw.get("url"),
    }


def subject_item(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_id(raw.get("id")),
        "name": raw.get("name") or "",
        "slug": raw.get("slug"),
        "parent_id": as_id(raw.get("parent_id") or "0"),
    }


def az_props(raw: dict[str, Any]) -> dict[str, Any]:
    alt_names = raw.get("alt_names")
    if isinstance(alt_names, list):
        alt_names = ", ".join(str(a) for a in alt_names if a)
    return {
        "alt_names": str(alt_names or "").strip(),
        "new": flag(raw.get("enable_new")),
        "trial": flag(raw.get("enable_trial")),
        "popular": flag(raw.get("enable_popular")),
    }


def database_item(
    raw: dict[str, Any], props: dict[str, Any] | None = None, *, full: bool = False
) -> dict[str, Any]:
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    description = plain_text(raw.get("description"))
    if not full and len(description) > SEARCH_DESCRIPTION_CHARS:
        description = description[:SEARCH_DESCRIPTION_CHARS].rstrip() + "..."
    out: dict[str, Any] = {
        "id": as_id(raw.get("id")),
        "name": raw.get("name") or "",
        "url": raw.get("url"),
        "proxied": flag(meta.get("enable_proxy")),
        "vendor": raw.get("az_vendor_name") or None,
        "description": description,
    }
    if props:
        if props["alt_names"]:
            out["alt_names"] = props["alt_names"]
        for key in ("new", "trial", "popular"):
            if props[key]:
                out[key] = True
    out["subjects"] = subject_refs(raw.get("subjects"))
    out["types"] = [t.get("name") for t in raw.get("az_types") or [] if isinstance(t, dict)]
    if full:
        more_info = plain_text(meta.get("more_info"))
        if more_info:
            out["more_info"] = more_info
    return out


def _https(url: Any) -> str | None:
    url = str(url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    return url if url.startswith("https://") else None


def librarian_item(account: dict[str, Any], *, expose_email: bool = False) -> dict[str, Any]:
    profile = account.get("profile") or {}
    image = profile.get("image") or {}
    connect = profile.get("connect") or {}
    display = profile.get("display") or {}
    out: dict[str, Any] = {
        "name": full_name(account),
        "title": (profile.get("box") or {}).get("title") or None,
        "pronouns": profile.get("pronouns") or None,
        "profile_url": profile.get("url"),
    }
    image_url = _https(image.get("url")) if flag(image.get("show")) else None
    if image_url:
        out["image_url"] = image_url
    out["subjects"] = subject_refs(account.get("subjects"))
    if flag(display.get("disp_connect_general")):
        fields = ["phone", "address", "website"] + (["email"] if expose_email else [])
        for key in fields:
            value = str(connect.get(key) or "").strip()
            if value:
                out[key] = value
    return out


def redact_emails(value: Any) -> Any:
    """Remove email addresses from every string in a tool result (page text, descriptions)."""
    if isinstance(value, str):
        return _EMAIL.sub(EMAIL_REDACTED, _MAILTO_LINK.sub(r"\1", value))
    if isinstance(value, dict):
        return {key: redact_emails(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_emails(item) for item in value]
    return value
