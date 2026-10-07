# uc-libapps-mcp

Read-only MCP server for **LibGuides** sites, built on the Springshare LibApps
API v1.2. It lets an AI assistant browse published research guides (including
every page and sub-page as clean markdown), guide subjects, the A-Z database
list, and public subject-librarian profiles. Runs over **stdio**.

Nothing institution-specific is built in: the API region host, public site URL,
and credentials are all configuration. Without credentials the server still
starts, and every tool returns `config_missing`.

## What this does **not** do

- Write anything (no create/update tools; the app needs GET scopes only)
- Show unpublished guides, Internal or Template guides, or draft content
- Index content or keep its own search index
- Other Springshare products (LibCal, LibAnswers, LibWizard)
- Hosted / remote access (see [Phase 2](#phase-2-hosted-later))

## Requirements

- A **LibGuides CMS** site
- A **read-only LibApps v1.2 application** with GET scopes for Guides,
  Subjects, A-Z, Accounts, and Assets
- [uv](https://docs.astral.sh/uv/) and Python 3.11+

## Credentials

In LibApps, go to **Tools > API > Applications** and create (or reuse) an
application with only the GET scopes above. That page also shows your
region's API host (for example `lgapi-us.libapps.com`); use it for
`LIBAPPS_API_BASE`.

Keep the client secret out of git, tickets, and chat. Put it in your MCP host
config or a local, git-ignored env file. `.env.example` lists the variables
with placeholder values. Rotate the secret if it is ever exposed.

## Install

```bash
cd /path/to/uc-libapps-mcp
uv sync --extra dev
```

## Claude / MCP host config

Point Claude Desktop (or another stdio MCP host) at the checkout with
`uv run --directory`. Replace `/path/to/uc-libapps-mcp` with your clone path
and the placeholder values with your own:

```json
{
  "mcpServers": {
    "libapps": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/uc-libapps-mcp",
        "python",
        "-m",
        "uc_libapps_mcp"
      ],
      "env": {
        "LIBAPPS_API_BASE": "https://lgapi-us.libapps.com",
        "LIBAPPS_CLIENT_ID": "your-client-id",
        "LIBAPPS_CLIENT_SECRET": "your-client-secret",
        "LIBGUIDES_SITE_URL": "https://guides.lib.uchicago.edu"
      }
    }
  }
}
```

You can also run the console script `uv run uc-libapps-mcp` from a shell for a
quick smoke check.

## Configuration

All settings are environment variables. They are read at the first tool call.

| Variable | Default | Purpose |
|---|---|---|
| `LIBAPPS_CLIENT_ID` / `LIBAPPS_CLIENT_SECRET` | (required) | OAuth client credentials for the v1.2 app |
| `LIBAPPS_API_BASE` | `https://lgapi-us.libapps.com` | Region host: `lgapi-{us,ca,eu,au}.libapps.com`. Other hosts need `LIBAPPS_ALLOW_CUSTOM_API_BASE=1` |
| `LIBGUIDES_SITE_URL` | derived from guide URLs | Public site origin, used for page fetches |
| `LIBGUIDES_ALLOWED_HOSTS` | (none) | Extra comma-separated hosts the page fetcher may use |
| `LIBAPPS_HTML_FETCH` | `1` | Read page content from public guide pages. `0` falls back to unplaced API text |
| `LIBAPPS_INCLUDE_PRIVATE` | `0` | Include Private guides and hidden pages/boxes |
| `LIBAPPS_EXPOSE_EMAIL` | `0` | Include the public-profile contact email for librarians |
| `LIBAPPS_CACHE_TTL_LIST` / `_REF` / `_CONTENT` | `1800` / `86400` / `900` | Cache lifetimes (seconds) for the guide list; subjects, accounts, and A-Z; and guide outlines, pages, and searches |
| `LIBAPPS_TIMEOUT` | `30` | HTTP timeout (seconds) |
| `LIBAPPS_MAX_CONCURRENCY` | `4` | Maximum concurrent upstream requests |
| `LIBAPPS_MAX_CHARS` | `20000` | Default markdown budget per `get_guide_content` call |
| `LIBAPPS_USER_AGENT` | `uc-libapps-mcp/0.1.0 (+repo URL)` | User agent for all requests |
| `LIBAPPS_LOG_LEVEL` | `WARNING` | Log level; logs go to stderr only |

## Tools

Every tool returns `{"ok": true, ...}` or
`{"ok": false, "error": "...", "code": "..."}`. Codes: `config_missing`,
`config_invalid`, `auth_failed`, `scope_missing`, `not_found`, `not_public`,
`upstream_error`, `rate_limited`, `fetch_blocked`, `invalid_input`. List tools
take `limit` (max 50) and `offset` and return `total` and `next_offset`
(`null` when done).

### Reading guides: `get_guide` then `get_guide_content`

1. `search_guides` finds a guide.
2. `get_guide(guide_id)` returns its metadata and the **page tree**: every
   visible page and sub-page with `page_id`, `name`, `url`, and box names.
3. `get_guide_content(guide_id, page_id, include_subpages)` returns that page
   as markdown, one entry per content box in display order. With
   `include_subpages=true` it adds the page's sub-pages. Each response also
   includes the full `outline`, so the assistant can move to any other page.

### `search_guides`

| Argument | Notes |
|---|---|
| `query` | Uses the site's relevance-ranked guide search (`source: "server"`). Without a query, or if that search fails, matches name, subjects, description, and owner locally (`source: "local"`) |
| `subject` | Subject name (substring) or id |
| `owner` | Owner name; only owners with a public profile can match |
| `guide_type` | `subject`, `course`, `topic`, `general`, or a type id |
| `limit` / `offset` | Default 10 / 0 |

Returns `id`, `name`, `url`, `description`, `type`, `subjects`, `updated`,
`rank`, and `owner` (name and profile link) when the owner has a public profile.

### `get_guide`

| Argument | Notes |
|---|---|
| `guide_id` | Numeric guide id |

Pages have `page_id`, `name`, `url`, `parent_id`, `box_count`, `boxes`
(`box_id`, `name`, `column`, `position`), and `subpages`. Link pages carry
`redirect_url` and are never followed.

### `get_guide_content`

| Argument | Notes |
|---|---|
| `guide_id` | Numeric guide id |
| `page_id` | Defaults to the guide's first page |
| `include_subpages` | Add the page's sub-pages (default `false`) |
| `source` | `auto` (default), `html` (public page), or `api` (whole-guide text blocks with `placement: "unknown"`) |
| `max_chars` | Markdown budget (default `LIBAPPS_MAX_CHARS`, max 100000) |
| `offset` | Box index to resume from (use `next_offset`) |

Boxes that are in the outline but not on the page are marked `missing`. A box
cut to fit the budget is marked `truncated`. Embedded widgets become
`[embedded: ...]` placeholders.

### `list_subjects`

`with_published_guides` (default `true`) limits the list to subjects that have
published guides. Returns `id`, `name`, `slug`, and `parent_id`.

### `search_databases`

| Argument | Notes |
|---|---|
| `query` | Matches name, alternate names, vendor, and description |
| `subject` / `az_type` | Name (substring) or id |
| `limit` / `offset` | Default 20 / 0 |

Returns `id`, `name`, `url`, `proxied`, `vendor`, a short `description`,
`subjects`, `types`, and `alt_names` / `new` / `trial` / `popular` when available.

### `get_database`

`database_id`: the same fields with the full description and `more_info`.

### `find_subject_librarians`

`subject`: name or id. Returns librarians with a public profile for that
subject: `name`, `title`, `pronouns`, `profile_url`, `image_url`, `subjects`,
and the contact details (`phone`, `address`, `website`) they chose to show
publicly.

## Privacy and safety

- **Published only.** Unpublished, Internal, and Template guides are never
  returned. Private guides and hidden pages/boxes need `LIBAPPS_INCLUDE_PRIVATE=1`.
- **Allowlisted output.** Every result is built field by field. Login emails,
  account ids, widget code, and A-Z internal notes and reviews are never
  returned. Librarians without a public profile are excluded. Contact details
  appear only when the profile shows them, and the profile email only with
  `LIBAPPS_EXPOSE_EMAIL=1`.
- **No email addresses by default.** Unless `LIBAPPS_EXPOSE_EMAIL=1`, email
  addresses in page text and descriptions are replaced with `[email removed]`.
- **Secrets stay private.** The client secret and access token are never logged
  or returned; settings `repr()` hides the credentials.
- **Safe page fetches.** Only `https` URLs from API data, on the site host or
  `LIBGUIDES_ALLOWED_HOSTS`. Redirects are re-checked (max 3), pages are capped
  at 2 MB and must be `text/html`, and no cookies or auth headers are sent.
- **Polite client.** Cached responses, a concurrency cap, `gzip`, and
  backoff on 429/5xx.

## Development

```bash
uv run pytest
```

Unit tests mock all HTTP with synthetic fixtures. An opt-in live smoke test
reads credentials from the environment and asserts only on shapes and counts:

```bash
LIBAPPS_LIVE_TESTS=1 uv run pytest -m live
```

Notes on the API behaviors this server relies on are in
[`docs/api-notes.md`](docs/api-notes.md).

To verify a running server end to end against the live API (stdio startup,
every tool, privacy and status filters, error shapes), follow
[`.claude/skills/verify-uc-libapps-mcp/SKILL.md`](.claude/skills/verify-uc-libapps-mcp/SKILL.md).

## Phase 2: hosted (later)

A later phase adds a streamable HTTP transport served at `/mcp` behind TLS and
an authenticating proxy, with per-deployment LibApps credentials kept
server-side.

## License

MIT
