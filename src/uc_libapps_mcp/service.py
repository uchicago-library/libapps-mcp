"""Tool logic over cached LibApps data: guides, guide content, subjects, A-Z databases, librarians."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

from .cache import TTLCache
from .client import LibAppsClient
from .config import Settings
from .errors import LibAppsError
from .html_extract import extract_boxes, html_to_markdown, plain_text
from .normalize import (
    as_id,
    as_int,
    az_props,
    box_ref,
    database_item,
    flag,
    guide_summary,
    librarian_item,
    owner_ref,
    page_ref,
    subject_item,
)

logger = logging.getLogger(__name__)

HIDDEN_GUIDE_TYPES = {5, 6}
MAX_LIMIT = 50
MAX_CONTENT_CHARS = 100000
SOURCES = {"auto", "html", "api"}


def _digits(value: Any, name: str) -> str:
    value = str(value or "").strip()
    if not value.isdigit():
        raise LibAppsError(f"{name} must be a numeric id.", "invalid_input")
    return value


def _window_args(limit: Any, offset: Any, default: int) -> tuple[int, int]:
    limit, offset = as_int(limit, default), as_int(offset, -1)
    if limit < 1 or offset < 0:
        raise LibAppsError("limit must be at least 1 and offset at least 0.", "invalid_input")
    return min(limit, MAX_LIMIT), offset


def _paginate(items: list[Any], limit: int, offset: int) -> tuple[dict[str, Any], list[Any]]:
    end = offset + limit
    info = {"total": len(items), "offset": offset, "next_offset": end if end < len(items) else None}
    return info, items[offset:end]


def _ref_matches(needle: str, ref: Any) -> bool:
    if not isinstance(ref, dict):
        return False
    needle = needle.strip().lower()
    return needle == as_id(ref.get("id")) or needle in (ref.get("name") or "").lower()


def _score(tokens: list[str], fields: list[tuple[int, str]]) -> int:
    """Sum of field weights per token; 0 unless every token matches some field."""
    total = 0
    for token in tokens:
        weights = [weight for weight, text in fields if token in text]
        if not weights:
            return 0
        total += sum(weights)
    return total


def _name_bonus(name: str, query: str) -> int:
    """Rank an exact name match first and a name that starts with the query next."""
    if name == query:
        return 100
    return 20 if name.startswith(query) else 0


def _content_window(
    blocks: list[dict[str, Any]], budget: int, offset: int
) -> tuple[list[tuple[int, dict[str, Any]]], int | None]:
    selected: list[tuple[int, dict[str, Any]]] = []
    remaining = budget
    for index in range(offset, len(blocks)):
        block = dict(blocks[index])
        size = len(block["markdown"])
        if size > remaining:
            if remaining == 0:
                return selected, index
            block["markdown"] = block["markdown"][:remaining]
            block["truncated"] = True
            selected.append((index, block))
            return selected, index + 1 if index + 1 < len(blocks) else None
        selected.append((index, block))
        remaining -= size
    return selected, None


class Service:
    def __init__(
        self,
        settings: Settings,
        client: LibAppsClient | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or LibAppsClient(settings)
        self.cache = cache or TTLCache()

    def _is_public(self, guide: dict[str, Any]) -> bool:
        if as_int(guide.get("type_id")) in HIDDEN_GUIDE_TYPES:
            return False
        status = as_int(guide.get("status"), -1)
        return status == 1 or (status == 2 and self.settings.include_private)

    def _guides(self) -> dict[str, dict[str, Any]]:
        status = "1,2" if self.settings.include_private else "1"

        def load() -> dict[str, dict[str, Any]]:
            data = self.client.get("guides", {"expand": "subjects", "status": status})
            return {
                as_id(g.get("id")): g
                for g in data or []
                if isinstance(g, dict) and self._is_public(g)
            }

        return self.cache.get_or_load("guides", self.settings.ttl_list, load)

    def _guide_detail(self, guide_id: str) -> dict[str, Any]:
        def load() -> dict[str, Any] | None:
            try:
                data = self.client.get(f"guides/{guide_id}", {"expand": "subjects,pages.boxes"})
            except LibAppsError as exc:
                if exc.code == "not_found":
                    return None
                raise
            if isinstance(data, list):
                data = data[0] if data else None
            return data if isinstance(data, dict) else None

        detail = self.cache.get_or_load(("guide", guide_id), self.settings.ttl_content, load)
        if detail is None:
            raise LibAppsError(f"Guide {guide_id} not found.", "not_found")
        if not self._is_public(detail):
            raise LibAppsError(f"Guide {guide_id} is not publicly available.", "not_public")
        return detail

    def _public_accounts(self) -> dict[str, dict[str, Any]]:
        def load() -> dict[str, dict[str, Any]]:
            data = self.client.get("accounts", {"expand": "profile,subjects"})
            return {
                as_id(a.get("id")): a
                for a in data or []
                if isinstance(a, dict) and as_int((a.get("profile") or {}).get("en_page")) == 1
            }

        return self.cache.get_or_load("accounts", self.settings.ttl_ref, load)

    def _owners(self) -> dict[str, dict[str, Any]]:
        try:
            if not self.client.has_scope("accounts_get"):
                return {}
            return {aid: owner_ref(a) for aid, a in self._public_accounts().items()}
        except LibAppsError as exc:
            logger.warning("Guide owner lookup unavailable (%s).", exc.code)
            return {}

    def _visible(self, item: dict[str, Any]) -> bool:
        return self.settings.include_private or flag(item.get("enable_display"))

    def _children(self, detail: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        children: dict[str, list[dict[str, Any]]] = {}
        for page in detail.get("pages") or []:
            if isinstance(page, dict) and self._visible(page):
                children.setdefault(as_id(page.get("parent_id") or "0"), []).append(page)
        for siblings in children.values():
            siblings.sort(key=lambda p: as_int(p.get("position")))
        return children

    def _walk(
        self, children: dict[str, list[dict[str, Any]]], parent: str = "0", seen: set[str] | None = None
    ) -> Iterator[dict[str, Any]]:
        seen = set() if seen is None else seen
        for page in children.get(parent, []):
            page_id = as_id(page.get("id"))
            if page_id in seen:
                continue
            seen.add(page_id)
            yield page
            yield from self._walk(children, page_id, seen)

    def _visible_boxes(self, page: dict[str, Any]) -> list[dict[str, Any]]:
        boxes = [b for b in page.get("boxes") or [] if isinstance(b, dict) and self._visible(b)]
        return sorted(boxes, key=lambda b: (as_int(b.get("column_id")), as_int(b.get("position"))))

    def _page_node(
        self, page: dict[str, Any], children: dict[str, list[dict[str, Any]]], seen: set[str]
    ) -> dict[str, Any]:
        page_id = as_id(page.get("id"))
        seen.add(page_id)
        boxes = self._visible_boxes(page)
        node = page_ref(page)
        if self.settings.include_private:
            node["hidden"] = not flag(page.get("enable_display"))
        if page.get("redirect_url"):
            node["redirect_url"] = page["redirect_url"]
        node["box_count"] = len(boxes)
        node["boxes"] = [box_ref(b) for b in boxes]
        node["subpages"] = [
            self._page_node(child, children, seen)
            for child in children.get(page_id, [])
            if as_id(child.get("id")) not in seen
        ]
        return node

    def _derived_site(self) -> str | None:
        for guide in self._guides().values():
            parts = urlsplit(guide.get("url") or "")
            if parts.scheme == "https" and parts.hostname:
                return f"https://{parts.hostname}"
        return None

    def _allowed_hosts(self) -> set[str]:
        hosts = set(self.settings.allowed_hosts)
        site = self.settings.site_url or self._derived_site()
        if site:
            hosts.add(urlsplit(site).hostname or "")
        return hosts

    def _server_search(self, query: str) -> list[str]:
        def load() -> list[str]:
            data = self.client.get("guides", {"search_terms": query, "sort_by": "relevance"})
            return [as_id(g.get("id")) for g in data or [] if isinstance(g, dict)]

        return self.cache.get_or_load(("search", query.lower()), self.settings.ttl_content, load)

    def _local_search(
        self, guides: list[dict[str, Any]], query: str, owners: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        def name(g: dict[str, Any]) -> str:
            return (g.get("name") or "").lower()

        tokens = query.lower().split()
        if not tokens:
            return sorted(guides, key=name)
        scored = []
        for g in guides:
            owner = owners.get(as_id(g.get("owner_id")), {})
            score = _score(
                tokens,
                [
                    (8, name(g)),
                    (4, " ".join(s.get("name") or "" for s in g.get("subjects") or []).lower()),
                    (2, (g.get("description") or "").lower()),
                    (1, (owner.get("name") or "").lower()),
                ],
            )
            if score:
                scored.append((-score - _name_bonus(name(g), query.lower()), name(g), g))
        scored.sort(key=lambda item: item[:2])
        return [g for _, _, g in scored]

    def search_guides(
        self,
        query: str | None,
        subject: str | None,
        owner: str | None,
        guide_type: str | None,
        limit: Any,
        offset: Any,
    ) -> dict[str, Any]:
        limit, offset = _window_args(limit, offset, 10)
        self.client.require_scope("guides_get")
        if owner:
            self.client.require_scope("accounts_get")
        guides = self._guides()
        owners = self._owners()
        query = " ".join((query or "").split())
        ranked = None
        source = "local"
        if query:
            try:
                ranked = [guides[gid] for gid in self._server_search(query) if gid in guides]
                source = "server"
            except LibAppsError as exc:
                if exc.code != "upstream_error":
                    raise
                logger.warning("Server guide search failed; using local match.")
        if ranked is None:
            ranked = self._local_search(list(guides.values()), query, owners)

        def keep(g: dict[str, Any]) -> bool:
            if subject and not any(_ref_matches(subject, s) for s in g.get("subjects") or []):
                return False
            if guide_type:
                wanted = guide_type.strip().lower()
                if wanted != as_id(g.get("type_id")) and wanted not in (g.get("type_label") or "").lower():
                    return False
            if owner:
                ref = owners.get(as_id(g.get("owner_id")))
                if not ref or owner.strip().lower() not in ref["name"].lower():
                    return False
            return True

        info, window = _paginate([g for g in ranked if keep(g)], limit, offset)
        results = []
        for rank, g in enumerate(window, start=offset + 1):
            item = guide_summary(g, owners.get(as_id(g.get("owner_id"))))
            item["rank"] = rank
            results.append(item)
        return {"ok": True, "source": source, **info, "guides": results}

    def get_guide(self, guide_id: Any) -> dict[str, Any]:
        guide_id = _digits(guide_id, "guide_id")
        self.client.require_scope("guides_get")
        detail = self._guide_detail(guide_id)
        children = self._children(detail)
        guide = guide_summary(detail, self._owners().get(as_id(detail.get("owner_id"))))
        seen: set[str] = set()
        guide["pages"] = [self._page_node(p, children, seen) for p in children.get("0", [])]
        return {"ok": True, "guide": guide}

    def _render_page(self, page: dict[str, Any], allowed: set[str]) -> tuple[list[dict[str, Any]], bool]:
        url = page.get("url") or ""
        boxes = [(as_id(b.get("id")), b.get("name") or "") for b in self._visible_boxes(page)]
        return self.cache.get_or_load(
            ("page", url, tuple(boxes)),
            self.settings.ttl_content,
            lambda: extract_boxes(self.client.fetch_html(url, allowed), url, boxes),
        )

    def _html_content(
        self,
        guide_id: str,
        page_id: str | None,
        children: dict[str, list[dict[str, Any]]],
        visible: list[dict[str, Any]],
        include_subpages: bool,
        budget: int,
        offset: int,
    ) -> dict[str, Any]:
        if page_id:
            page = next((p for p in visible if as_id(p.get("id")) == page_id), None)
            if page is None:
                raise LibAppsError(
                    f"Page {page_id} is not a visible page of guide {guide_id}.", "not_found"
                )
        else:
            top = children.get("0", [])
            if not top:
                raise LibAppsError(f"Guide {guide_id} has no visible pages.", "not_found")
            page = top[0]
        targets = [page]
        if include_subpages and not page.get("redirect_url"):
            targets += [c for c in children.get(as_id(page.get("id")), []) if not c.get("redirect_url")]
        fetchable = [p for p in targets if not p.get("redirect_url")]
        allowed = self._allowed_hosts()

        def render(page: dict[str, Any]) -> tuple[list[dict[str, Any]], bool] | LibAppsError:
            try:
                return self._render_page(page, allowed)
            except LibAppsError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=self.settings.max_concurrency) as pool:
            rendered = list(pool.map(render, fetchable))
        failures = [r for r in rendered if isinstance(r, LibAppsError)]
        if failures and len(failures) == len(fetchable):
            raise failures[0]
        by_page = {as_id(p.get("id")): r for p, r in zip(fetchable, rendered, strict=True)}

        flat = [
            (pid, box)
            for pid, r in by_page.items()
            if not isinstance(r, LibAppsError)
            for box in r[0]
        ]
        selected, next_offset = _content_window([box for _, box in flat], budget, offset)
        boxes_by_page: dict[str, list[dict[str, Any]]] = {}
        for index, box in selected:
            boxes_by_page.setdefault(flat[index][0], []).append(box)

        pages = []
        for p in targets:
            pid = as_id(p.get("id"))
            if p.get("redirect_url"):
                pages.append({"page_id": pid, "name": p.get("name") or "", "redirect_url": p["redirect_url"]})
                continue
            entry = page_ref(p)
            outcome = by_page[pid]
            if isinstance(outcome, LibAppsError):
                entry["error"] = {"code": outcome.code, "message": outcome.message}
            elif outcome[1]:
                entry["fallback"] = "guide_main"
            entry["boxes"] = boxes_by_page.get(pid, [])
            pages.append(entry)
        return {"source": "html", "pages": pages, "next_offset": next_offset}

    def _api_text(self, guide_id: str, base_url: str, budget: int, offset: int) -> dict[str, Any]:
        self.client.require_scope("assets_get")

        def load() -> list[dict[str, Any]]:
            data = self.client.get("assets", {"guide_ids": guide_id, "asset_types": "1"})
            blocks = []
            for asset in data or []:
                if not isinstance(asset, dict) or flag(asset.get("enable_hidden")):
                    continue
                markdown = html_to_markdown(asset.get("description") or "", base_url)
                if markdown:
                    blocks.append({"asset_id": as_id(asset.get("id")), "markdown": markdown})
            return blocks

        blocks = self.cache.get_or_load(("text", guide_id), self.settings.ttl_content, load)
        selected, next_offset = _content_window(blocks, budget, offset)
        return {
            "source": "api",
            "placement": "unknown",
            "blocks": [block for _, block in selected],
            "next_offset": next_offset,
        }

    def get_guide_content(
        self,
        guide_id: Any,
        page_id: Any,
        include_subpages: bool,
        source: str | None,
        max_chars: Any,
        offset: Any,
    ) -> dict[str, Any]:
        guide_id = _digits(guide_id, "guide_id")
        page_id = _digits(page_id, "page_id") if page_id not in (None, "") else None
        source = (source or "auto").strip().lower()
        if source not in SOURCES:
            raise LibAppsError("source must be one of auto, html, api.", "invalid_input")
        budget = self.settings.max_chars if max_chars is None else as_int(max_chars, 0)
        offset = as_int(offset, -1)
        if budget < 1 or offset < 0:
            raise LibAppsError("max_chars must be at least 1 and offset at least 0.", "invalid_input")
        budget = min(budget, MAX_CONTENT_CHARS)
        if source == "html" and not self.settings.html_fetch:
            raise LibAppsError("HTML fetching is disabled (LIBAPPS_HTML_FETCH=0).", "fetch_blocked")

        self.client.require_scope("guides_get")
        detail = self._guide_detail(guide_id)
        children = self._children(detail)
        visible = list(self._walk(children))
        guide_url = detail.get("friendly_url") or detail.get("url")
        result: dict[str, Any] = {
            "ok": True,
            "guide": {"id": guide_id, "name": detail.get("name") or "", "url": guide_url},
        }
        if source == "api" or not self.settings.html_fetch:
            result.update(self._api_text(guide_id, detail.get("url") or "", budget, offset))
            if page_id:
                result["note"] = "API text blocks have no page placement; page_id was ignored."
        else:
            result.update(
                self._html_content(guide_id, page_id, children, visible, include_subpages, budget, offset)
            )
        result["outline"] = [page_ref(p) for p in visible]
        return result

    def _subjects(self, published: bool) -> list[dict[str, Any]]:
        params = {"guide_published": "2"} if published else None
        return self.cache.get_or_load(
            ("subjects", published),
            self.settings.ttl_ref,
            lambda: [s for s in self.client.get("subjects", params) or [] if isinstance(s, dict)],
        )

    def list_subjects(self, with_published_guides: bool) -> dict[str, Any]:
        self.client.require_scope("subjects_get")
        subjects = [subject_item(s) for s in self._subjects(bool(with_published_guides))]
        return {"ok": True, "total": len(subjects), "subjects": subjects}

    def _az(self) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            data = [a for a in self.client.get("az", {"expand": "subjects,az_types"}) or [] if isinstance(a, dict)]
            visible = {as_id(a.get("id")): a for a in data if not flag(a.get("enable_hidden"))}
            text = {}
            for aid, a in visible.items():
                meta = a.get("meta") if isinstance(a.get("meta"), dict) else {}
                text[aid] = f"{plain_text(a.get('description'))} {plain_text(meta.get('more_info'))}".lower()
            hidden = {as_id(a.get("id")) for a in data if flag(a.get("enable_hidden"))}
            return {"visible": visible, "hidden": hidden, "text": text}

        return self.cache.get_or_load("az", self.settings.ttl_ref, load)

    def _az_props(self) -> dict[str, dict[str, Any]]:
        def load() -> dict[str, dict[str, Any]]:
            data = self.client.get("assets", {"asset_types": "10", "expand": "az_props"})
            return {as_id(a.get("id")): az_props(a) for a in data or [] if isinstance(a, dict)}

        try:
            if not self.client.has_scope("assets_get"):
                return {}
            return self.cache.get_or_load("az_props", self.settings.ttl_ref, load)
        except LibAppsError as exc:
            logger.warning("A-Z enrichment unavailable (%s).", exc.code)
            return {}

    def search_databases(
        self,
        query: str | None,
        subject: str | None,
        az_type: str | None,
        limit: Any,
        offset: Any,
    ) -> dict[str, Any]:
        limit, offset = _window_args(limit, offset, 20)
        self.client.require_scope("az_get")
        az = self._az()
        props = self._az_props()

        def name(a: dict[str, Any]) -> str:
            return (a.get("name") or "").lower()

        candidates = [
            a
            for a in az["visible"].values()
            if (not subject or any(_ref_matches(subject, s) for s in a.get("subjects") or []))
            and (not az_type or any(_ref_matches(az_type, t) for t in a.get("az_types") or []))
        ]
        tokens = (query or "").lower().split()
        if tokens:
            scored = []
            for a in candidates:
                aid = as_id(a.get("id"))
                score = _score(
                    tokens,
                    [
                        (8, name(a)),
                        (4, props.get(aid, {}).get("alt_names", "").lower()),
                        (2, (a.get("az_vendor_name") or "").lower()),
                        (1, az["text"].get(aid, "")),
                    ],
                )
                if score:
                    scored.append((-score - _name_bonus(name(a), " ".join(tokens)), name(a), a))
            scored.sort(key=lambda item: item[:2])
            ranked = [a for _, _, a in scored]
        else:
            ranked = sorted(candidates, key=name)
        info, window = _paginate(ranked, limit, offset)
        databases = [database_item(a, props.get(as_id(a.get("id")))) for a in window]
        return {"ok": True, **info, "databases": databases}

    def get_database(self, database_id: Any) -> dict[str, Any]:
        database_id = _digits(database_id, "database_id")
        self.client.require_scope("az_get")
        az = self._az()
        if database_id not in az["visible"] and database_id not in az["hidden"]:
            self.cache.invalidate("az")
            az = self._az()
        raw = az["visible"].get(database_id)
        if raw is None:
            raise LibAppsError(f"Database {database_id} not found.", "not_found")
        return {"ok": True, "database": database_item(raw, self._az_props().get(database_id), full=True)}

    def _resolve_subject(self, needle: str) -> dict[str, Any]:
        subjects = self._subjects(False)
        for s in subjects:
            if (s.get("name") or "").strip().lower() == needle:
                return s
        for s in subjects:
            if as_id(s.get("id")) == needle:
                return s
        partial = [s for s in subjects if needle in (s.get("name") or "").lower()]
        if len(partial) == 1:
            return partial[0]
        if partial:
            names = ", ".join(s.get("name") or "" for s in partial[:10])
            raise LibAppsError(f"Subject is ambiguous; candidates: {names}.", "invalid_input")
        raise LibAppsError("No subject matches that name or id.", "not_found")

    def find_subject_librarians(self, subject: str) -> dict[str, Any]:
        needle = " ".join(str(subject or "").split()).lower()
        if not needle:
            raise LibAppsError("subject is required.", "invalid_input")
        self.client.require_scope("subjects_get")
        self.client.require_scope("accounts_get")
        match = self._resolve_subject(needle)
        subject_id = as_id(match.get("id"))
        accounts = [
            a
            for a in self._public_accounts().values()
            if any(isinstance(s, dict) and as_id(s.get("id")) == subject_id for s in a.get("subjects") or [])
        ]
        accounts.sort(key=lambda a: ((a.get("last_name") or "").lower(), (a.get("first_name") or "").lower()))
        librarians = [librarian_item(a, expose_email=self.settings.expose_email) for a in accounts]
        return {
            "ok": True,
            "subject": {"id": subject_id, "name": match.get("name") or ""},
            "total": len(librarians),
            "librarians": librarians,
        }
