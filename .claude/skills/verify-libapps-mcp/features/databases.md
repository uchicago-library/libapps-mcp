# A–Z databases

`search_databases` searches the site's A–Z database list by name, alternate names, vendor and description, with optional subject and type filters. `get_database` returns one database. Hidden A–Z items are never returned, and internal notes and library reviews never leave the server.

## Sub-features

- `db-search`: a query matches and ranks results, with an exact name match first.
- `db-filters`: `subject` and `az_type` filters.
- `db-list`: no query lists everything alphabetically, with paging.
- `db-get`: a single database by id from the cached list.
- `db-hidden`: hidden items are excluded from search and are `not_found` in `get_database`.
- `db-private-fields`: no `internal_note` or `library_review`.

## How to get to it (user POV)

- Ask "which databases cover <topic>?" or "is <database> available?". The host calls `search_databases`, then `get_database` for details.

## Driving it with scripts/verify

Preconditions:

- `verify doctor` passes.

- **Search.** Run `.claude/skills/verify-libapps-mcp/scripts/verify call search_databases '{"query": "jstor", "limit": 5}'`. The result has `ok: true`, and the database named exactly "JSTOR" (if the site has one) ranks first. Items have `id, name, url, proxied, vendor, description, subjects, types`.
- **Filter.** Run `.claude/skills/verify-libapps-mcp/scripts/verify call search_databases '{"subject": "<subject name>", "limit": 5}'`. Every item's `subjects` include that subject.
- **Get one.** Run `.claude/skills/verify-libapps-mcp/scripts/verify call get_database '{"database_id": "<id from search>"}'`. The result has `database.id` equal to the id and the full `description`, plus `more_info` when the site has it.
- **Hidden and private fields.** Run `.claude/skills/verify-libapps-mcp/scripts/verify check --only live` and read `search_databases excludes hidden A-Z items` (total equals the raw count minus hidden), `get_database refuses hidden item`, and the privacy line. All must PASS.

## Gotchas

- `/1.2/az/{id}` returns 404 upstream. That is expected and never called. `get_database` reloads the list once for an unknown id.
- Alternate names and the new/trial/popular flags need the `assets_get` scope. Without it, search still works without them.
- The A–Z list is cached for 24 hours (`LIBAPPS_CACHE_TTL_REF`).
