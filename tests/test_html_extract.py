from conftest import FIXTURES

from libapps_mcp.html_extract import extract_boxes, html_to_markdown, plain_text

PAGE_URL = "https://guides.example.edu/c.php?g=101&p=2001"


def page(pid):
    return (FIXTURES / "html" / f"page_{pid}.html").read_text()


def test_extract_boxes_in_api_order_with_missing_marker():
    boxes, fallback = extract_boxes(
        page("2001"),
        PAGE_URL,
        [("3002", "Getting Started"), ("3001", "Article Databases"), ("3004", "Ask Us"), ("3008", "Removed Box")],
    )
    assert fallback is False
    assert [b["box_id"] for b in boxes] == ["3002", "3001", "3004", "3008"]
    assert boxes[1]["title"] == "Article Databases"
    assert boxes[3] == {"box_id": "3008", "title": "Removed Box", "missing": True, "markdown": ""}
    assert "[Chem Abstracts Example](https://chem.example.com/)" in boxes[1]["markdown"]
    assert "Chemistry literature database." in boxes[1]["markdown"]
    assert "opens in a new window" not in boxes[1]["markdown"]
    assert "**reference desk**" in boxes[2]["markdown"]


def test_box_markdown_cleanup_and_links():
    boxes, _ = extract_boxes(page("2001"), PAGE_URL, [("3002", "Getting Started")])
    md = boxes[0]["markdown"]
    assert "[spectra page](https://guides.example.edu/chemistry/spectra)" in md
    assert "[handbook](https://cdn.example.org/handbook.pdf)" in md
    assert "[an external site](http://external.example.net/page)" in md
    assert "https://guides.example.edu/images/logo.png" in md
    assert "pixel.gif" not in md
    assert "[embedded: Ask a Librarian chat]" in md
    assert "[embedded: video.example.com]" in md
    for chrome in ("tracker", "Enable JavaScript", "color: red", "Site banner", "Footer chrome", "<input"):
        assert chrome not in md
    assert "\n\n\n" not in md


def test_guide_main_fallback_when_no_boxes_match():
    boxes, fallback = extract_boxes(page("2003"), PAGE_URL, [("3006", "Safety Basics")])
    assert fallback is True
    assert len(boxes) == 1
    assert "Always wear goggles." in boxes[0]["markdown"]
    assert "Banner" not in boxes[0]["markdown"]


def test_html_to_markdown_and_plain_text():
    assert html_to_markdown("<p>See <a href='/x'>x</a></p>", PAGE_URL) == "See [x](https://guides.example.edu/x)"
    assert html_to_markdown("<a href='javascript:void(0)'>do</a>", PAGE_URL) == "do"
    assert plain_text("<p>A <b>bold</b>\n  move</p>") == "A bold move"
    assert plain_text(None) == ""


def test_more_less_toggle_buttons_dropped():
    html = (
        '<div><a href="https://db.example.com/">Example DB</a>'
        '<button class="s-lg-label-more">&amp; more</button>'
        '<button class="s-lg-label-less">less...</button>'
        '<div class="s-lg-database-desc">A database.</div></div>'
    )
    assert html_to_markdown(html, "https://guides.example.edu/x") == "[Example DB](https://db.example.com/)\n\nA database."
