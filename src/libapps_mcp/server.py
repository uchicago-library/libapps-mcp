"""stdio MCP server exposing read-only LibGuides tools backed by the LibApps API v1.2."""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__
from .config import load_settings
from .errors import LibAppsError
from .normalize import redact_emails
from .service import Service

logger = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

mcp = MCPServer(
    "libapps-mcp",
    version=__version__,
    instructions=(
        "Read-only access to a LibGuides site: published research guides, their pages "
        "and sub-pages as markdown, subjects, the A-Z database list, and public "
        "subject-librarian profiles. Use get_guide to see a guide's page outline, then "
        "get_guide_content with a page_id to read a page."
    ),
)

_service: Service | None = None
_service_lock = threading.Lock()


def get_service() -> Service:
    global _service
    with _service_lock:
        if _service is None:
            _service = Service(load_settings())
        return _service


def _run(call: Callable[[Service], dict[str, Any]]) -> dict[str, Any]:
    try:
        service = get_service()
        result = call(service)
        return result if service.settings.expose_email else redact_emails(result)
    except LibAppsError as exc:
        return exc.payload()
    except Exception as exc:
        logger.warning("Unexpected %s while handling a tool call.", type(exc).__name__)
        return {"ok": False, "error": "Unexpected error handling the request.", "code": "upstream_error"}


@mcp.tool(
    name="search_guides",
    description=(
        "Search published research guides. With a query, uses the site's relevance-ranked "
        "full-text guide search (source 'server'); without one, or if that search fails, "
        "matches guide names, subjects, descriptions, and owner names locally (source 'local'). "
        "Optional filters: subject (name or id), owner (public profile name), guide_type "
        "(e.g. subject, course, topic, general, or type id). Paginate with limit (max 50) "
        "and offset; next_offset is null when done."
    ),
    annotations=READ_ONLY,
)
def search_guides(
    query: str | None = None,
    subject: str | None = None,
    owner: str | None = None,
    guide_type: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    return _run(lambda s: s.search_guides(query, subject, owner, guide_type, limit, offset))


@mcp.tool(
    name="get_guide",
    description=(
        "Get a guide's metadata and its full page outline: a tree of pages and sub-pages "
        "with page_id, name, url, and box names. Use get_guide_content with a page_id to "
        "read a page."
    ),
    annotations=READ_ONLY,
)
def get_guide(guide_id: str) -> dict[str, Any]:
    return _run(lambda s: s.get_guide(guide_id))


@mcp.tool(
    name="get_guide_content",
    description=(
        "Read a guide page as markdown, one entry per content box in display order. "
        "Defaults to the guide's first page; pass page_id (from get_guide or the returned "
        "outline) for another page, and include_subpages=true to add its sub-pages. "
        "source: auto (default), html (public page), or api (unplaced text blocks for the "
        "whole guide). Output is capped at max_chars; continue with offset=next_offset."
    ),
    annotations=READ_ONLY,
)
def get_guide_content(
    guide_id: str,
    page_id: str | None = None,
    include_subpages: bool = False,
    source: str = "auto",
    max_chars: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    return _run(
        lambda s: s.get_guide_content(guide_id, page_id, include_subpages, source, max_chars, offset)
    )


@mcp.tool(
    name="list_subjects",
    description=(
        "List the site's guide subjects (id, name, slug). By default only subjects that "
        "have published guides."
    ),
    annotations=READ_ONLY,
)
def list_subjects(with_published_guides: bool = True) -> dict[str, Any]:
    return _run(lambda s: s.list_subjects(with_published_guides))


@mcp.tool(
    name="search_databases",
    description=(
        "Search the A-Z database list by name, alternate names, vendor, and description. "
        "Optional filters: subject and az_type (name or id). Without a query, lists "
        "databases alphabetically. Paginate with limit (max 50) and offset."
    ),
    annotations=READ_ONLY,
)
def search_databases(
    query: str | None = None,
    subject: str | None = None,
    az_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    return _run(lambda s: s.search_databases(query, subject, az_type, limit, offset))


@mcp.tool(
    name="get_database",
    description="Get one A-Z database by id, with its full description and access URL.",
    annotations=READ_ONLY,
)
def get_database(database_id: str) -> dict[str, Any]:
    return _run(lambda s: s.get_database(database_id))


@mcp.tool(
    name="find_subject_librarians",
    description=(
        "Find librarians with a public profile for a subject (name or id). Returns names, "
        "titles, profile links, and contact details they chose to show publicly."
    ),
    annotations=READ_ONLY,
)
def find_subject_librarians(subject: str) -> dict[str, Any]:
    return _run(lambda s: s.find_subject_librarians(subject))


def main() -> None:
    level = getattr(logging, os.environ.get("LIBAPPS_LOG_LEVEL", "WARNING").strip().upper(), None)
    logging.basicConfig(
        stream=sys.stderr, level=level if isinstance(level, int) else logging.WARNING, force=True
    )
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
