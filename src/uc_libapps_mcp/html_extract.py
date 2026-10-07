"""LibGuides page HTML to markdown: box extraction by API box id."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

_DROP_SELECTOR = "script, style, form, button, noscript, .sr-only, i.fa"
_EMBED_SELECTOR = "iframe, embed, object"


def _absolute(href: str, base_url: str) -> str | None:
    href = href.strip()
    if not href or href.lower().startswith("javascript:"):
        return None
    if href.startswith("//"):
        return "https:" + href
    if urlsplit(href).scheme or href.startswith("#"):
        return href
    return urljoin(base_url, href)


def _is_tracking_pixel(img: Tag) -> bool:
    return str(img.get("width", "")).strip() in {"0", "1"} and str(
        img.get("height", "")
    ).strip() in {"0", "1"}


def to_markdown(node: Tag, base_url: str) -> str:
    for el in node.select(_DROP_SELECTOR):
        el.decompose()
    for img in node.find_all("img"):
        if _is_tracking_pixel(img):
            img.decompose()
            continue
        src = _absolute(str(img.get("src", "")), base_url)
        if src:
            img["src"] = src
    for el in node.select(_EMBED_SELECTOR):
        src = str(el.get("src") or el.get("data") or "")
        label = str(el.get("title") or "").strip() or urlsplit(_absolute(src, base_url) or "").hostname
        el.replace_with(f"[embedded: {label or 'content'}]")
    for a in node.find_all("a"):
        href = _absolute(str(a.get("href", "")), base_url)
        if href:
            a.attrs = {"href": href}
        else:
            a.unwrap()
    markdown = markdownify(str(node), heading_style="ATX", bullets="-")
    markdown = "\n".join(line.rstrip() for line in markdown.splitlines())
    return re.sub(r"\n{3,}", "\n\n", markdown).strip()


def html_to_markdown(html: str, base_url: str) -> str:
    return to_markdown(BeautifulSoup(html, "html.parser"), base_url)


def plain_text(html: str | None) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def extract_boxes(
    html: str, page_url: str, boxes: list[tuple[str, str]]
) -> tuple[list[dict[str, Any]], bool]:
    """Return markdown for each (box_id, api_name) in order, plus whether the guide_main fallback was used."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    for box_id, api_name in boxes:
        node = soup.find(id=f"s-lg-box-{box_id}")
        if node is None:
            out.append({"box_id": box_id, "title": api_name, "missing": True, "markdown": ""})
            continue
        title_el = node.select_one(".s-lib-box-title")
        title = title_el.get_text(" ", strip=True) if title_el else api_name
        content = node.select_one(".s-lib-box-content") or node
        out.append({"box_id": box_id, "title": title, "markdown": to_markdown(content, page_url)})
    if any(not box.get("missing") for box in out):
        return out, False
    main = soup.find(id="s-lg-guide-main")
    if main is None:
        return out, False
    return [{"box_id": None, "title": None, "markdown": to_markdown(main, page_url)}], True
