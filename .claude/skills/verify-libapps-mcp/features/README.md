# libapps-mcp verification map

This directory is the maintained source for verifying the user-facing behavior of the LibApps MCP server. The "user" is an MCP client, such as Claude Desktop or another stdio host, calling tools on behalf of a person. Read this index before driving the server, then use the matching feature file as the recipe.

## Baseline preconditions

- The repo checkout is the build under test. `uv sync --extra dev` has run once.
- A read-only LibApps v1.2 env file exists at `~/.config/libapps-mcp/env` (or `--env-file`), and its values are never printed.
- `.claude/skills/verify-libapps-mcp/scripts/verify doctor` prints three PASS lines and the expected git SHA.
- Every command spawns its own stdio server. Never attach to a server an MCP host started.

## Driving conventions

- Drive only through `scripts/verify`:
  - `doctor`
  - `check [--only live|errors] [--guide-id ID]`
  - `call <tool> '<json args>'`
- Pass tool arguments as a JSON object with the exact names from `tools/list`. IDs are strings of digits.
- Read-only always: tools and the driver only issue GETs plus the OAuth token POST.
- Use ids discovered in the same run (from `search_guides`, `get_guide`, `list_subjects`, `search_databases`). Live data changes, so do not hardcode ids from an old run.

## Proof and skip reporting

- Capture the tool call and the JSON result. The driver saves both under the artifact directory it prints.
- Exclusion claims, such as unpublished guides, hidden A–Z items, or non-public librarians, need the raw API cross-check in `check`, not just a clean-looking result.
- Report counts and short snippets, never credential values.
- If a path cannot be reached, report the command and the unmet precondition (no env file, missing scope, network). Do not report a skipped feature as verified through a different tool.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with scripts/verify`, `Gotchas`.

## Features

- [Guide search](./guide-search.md) covers relevance search, local listing and filters, and the published-only rule.
- [Guide pages and sub-page content](./guide-pages.md) covers the outline tree, page and sub-page markdown, paging, the API text fallback, and not-public guides.
- [A–Z databases](./databases.md) covers search, filters, single-database lookup, hidden items, and internal-field stripping.
- [Subjects and librarians](./subjects-librarians.md) covers subject listing and public subject-librarian profiles.
- [Errors and configuration](./errors-config.md) covers missing or bad credentials, invalid input, and leak-free error shapes.
