# Guide pages and sub-page content

`get_guide` returns a guide's metadata and its visible page tree, including sub-pages and their box names. `get_guide_content` returns any page, optionally with its sub-pages, as markdown, one entry per content box in display order. Content comes from the public page HTML, matched by the API's box ids. `source="api"` returns unplaced whole-guide text blocks instead.

## Sub-features

- `guide-outline`: page tree with `page_id`, `url`, `box_count`, `boxes` and nested `subpages`. Hidden pages and boxes are dropped.
- `content-page`: default first page, or a given `page_id`, rendered as box markdown.
- `content-subpages`: `include_subpages=true` adds the visible child pages.
- `content-paging`: `max_chars` budget, `truncated` boxes, and `offset`/`next_offset` over boxes.
- `content-api`: `source="api"` returns `placement: "unknown"` text blocks.
- `guide-not-public`: unpublished, private (by default), Internal and Template guides return `not_public`.

## How to get to it (user POV)

- Ask "what's on the <guide> guide?". The host calls `get_guide`.
- Ask "show me the <page> page and its sub-pages". The host calls `get_guide_content` with `page_id` and `include_subpages`.

## Driving it with scripts/verify

Preconditions:

- `verify doctor` passes.
- You know a guide with visible sub-pages. `verify check --only live` prints one in the `get_guide returns page tree with sub-pages` line (guide id and parent page name). Or get one from the `get_guide-<id>.json` artifact.

- **Outline.** Run `.claude/skills/verify-libapps-mcp/scripts/verify call get_guide '{"guide_id": "<id>"}'`. `guide.pages[]` is a tree, and at least one page has non-empty `subpages` with `box_count` > 0.
- **Page with sub-pages.** Run `.claude/skills/verify-libapps-mcp/scripts/verify call get_guide_content '{"guide_id": "<id>", "page_id": "<parent page_id>", "include_subpages": true, "max_chars": 60000}'`. `source` is "html". `pages[0]` is the parent, and the following pages have `parent_id` equal to it. Their `boxes[].markdown` hold real text and links. `missing` is absent or rare.
- **Paging.** Repeat with `"max_chars": 500`. One or more boxes come back, possibly with `truncated: true`, and `next_offset` is a number. Call again with `"offset": <next_offset>` and the next boxes come back.
- **API text.** Run `.claude/skills/verify-libapps-mcp/scripts/verify call get_guide_content '{"guide_id": "<id>", "source": "api", "max_chars": 4000}'`. The result has `placement: "unknown"` and non-empty `blocks[].markdown`.
- **Not public.** Run `.claude/skills/verify-libapps-mcp/scripts/verify check --only live` and read the three `get_guide refuses ...` lines. Each must show `code=not_public` with a message that names only the id.

## Gotchas

- Most guides have no sub-pages. The top search hit is usually a flat guide.
- Link pages (`redirect_url`) are listed but never fetched. In content they appear as `{page_id, name, redirect_url}`.
- If one sub-page fetch fails, that page carries `error` and the others still render. If every page fails, the tool returns the error.
- Email addresses in page text become `[email removed]` unless `LIBAPPS_EXPOSE_EMAIL=1`.
- Page HTML is fetched only from the site host (`LIBGUIDES_SITE_URL`, or the host derived from guide URLs) plus `LIBGUIDES_ALLOWED_HOSTS`. A different host gives `fetch_blocked`.
