# Guide search

`search_guides` lets a client find published research guides. With a query it uses the site's relevance-ranked full-text search. Without one it lists or filters the cached guide list locally. Results never include unpublished, private (by default), Internal or Template guides.

## Sub-features

- `search-relevance`: a query returns server-ranked results with `source: "server"`.
- `search-local`: no query returns the full published list alphabetically with `source: "local"`.
- `search-filters`: `subject`, `guide_type` and `owner` narrow results.
- `search-paging`: `limit` (max 50), `offset`, `total`, `next_offset`.
- `search-published-only`: excluded statuses and types never appear.

## How to get to it (user POV)

- Ask the assistant to "find guides about <topic>". The host calls `search_guides` with `query`.
- Ask for "course guides in <subject>". The host calls `search_guides` with `subject` and `guide_type`.

## Driving it with scripts/verify

Preconditions:

- `verify doctor` passes.

- **Relevance search.** Run `.claude/skills/verify-uc-libapps-mcp/scripts/verify call search_guides '{"query": "chemistry", "limit": 5}'`. The JSON has `ok: true`, `source: "server"`, `total` > 0, and `guides[].rank` starting at 1. The top hit's name matches the topic.
- **Local listing.** Run `.claude/skills/verify-uc-libapps-mcp/scripts/verify call search_guides '{"limit": 3}'`. The result has `source: "local"`, alphabetical names, and `next_offset: 3`.
- **Filters.** Run `.claude/skills/verify-uc-libapps-mcp/scripts/verify call search_guides '{"subject": "<subject name from list_subjects>", "limit": 5}'`. Every result's `subjects` include the subject. Then run `... call search_guides '{"guide_type": "course", "limit": 5}'`. Every `type` is "Course Guide". Combined filters can legitimately return `total: 0`, for example when course guides carry no subjects.
- **Paging.** Run the listing again with `"offset": 3`. The first result is the 4th name from the previous run.
- **Published-only proof.** Run `.claude/skills/verify-uc-libapps-mcp/scripts/verify check --only live` and read the lines `search_guides results are published only` and `search_guides listing total == published non-internal guides`. Both must PASS.

## Gotchas

- Bare `search_terms` without relevance would include unpublished guides. The server always sends `sort_by=relevance`, so a result with `source: "local"` for a query means the server search failed and the local fallback ran. Investigate that.
- `owner` needs the `accounts_get` scope and matches only owners with a public profile.
- Results are cached for 15 minutes (`LIBAPPS_CACHE_TTL_CONTENT`), and the guide list for 30 minutes (`LIBAPPS_CACHE_TTL_LIST`). A fresh server process gives fresh data.
