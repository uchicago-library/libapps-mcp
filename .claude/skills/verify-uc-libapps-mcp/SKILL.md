---
name: verify-uc-libapps-mcp
description: >-
  Verify the LibApps MCP server (uc-libapps-mcp) the way an MCP client uses it:
  start it over stdio, list tools, and call all 7 tools against the live,
  read-only LibApps API v1.2 and public LibGuides pages, with privacy, status
  filter, sub-page content and error-shape checks. Use after changing anything
  in src/uc_libapps_mcp/, before calling a change done, or when the server
  misbehaves in an MCP host.
---

# Verify uc-libapps-mcp

The product surface is a **stdio MCP server** (`python -m uc_libapps_mcp`) with 7 read-only tools. Nothing listens on a port. Every verification run spawns its own server process through the `mcp` SDK stdio client, so runs are isolated and can execute side by side. The driver never reuses a server an MCP host started.

Everything here is **read-only**: the OAuth token POST plus GETs to the LibApps API and the public guide site. Never add write calls, and never touch the LibApps admin.

## Credentials (never print them)

- The driver reads `LIBAPPS_CLIENT_ID`, `LIBAPPS_CLIENT_SECRET`, `LIBAPPS_API_BASE` and `LIBGUIDES_SITE_URL` from an env file (default `~/.config/uc-libapps-mcp/env`, mode 600; override with `--env-file` or `LIBAPPS_ENV_FILE`). Values go into the child process environment only.
- Do not `cat`, `echo`, `grep` or `source` that file in a transcript. If you need the values in a shell, use `(set -a; . ~/.config/uc-libapps-mcp/env; set +a; <command>)` so nothing is echoed.
- A missing env file means you cannot run live checks. Report it; do not invent credentials.

## Launch

No build step beyond the uv environment:

```bash
cd <repo>
uv sync --extra dev          # once; installs mcp, httpx, bs4, markdownify, pytest
```

All driving goes through one helper, which runs inside the repo's uv environment:

```bash
.claude/skills/verify-uc-libapps-mcp/scripts/verify doctor
.claude/skills/verify-uc-libapps-mcp/scripts/verify check
.claude/skills/verify-uc-libapps-mcp/scripts/verify call <tool> '<json args>'
```

Each command spawns `uv run --directory <repo> python -m uc_libapps_mcp` as a stdio child, sends `initialize`, and talks MCP to it. The server is ready when `initialize` returns `serverInfo.name == "uc-libapps-mcp"`. The child exits when the command finishes, so there is no separate teardown.

## Doctor

Run this first, and again whenever anything looks off:

```bash
.claude/skills/verify-uc-libapps-mcp/scripts/verify doctor
```

It must print three PASS lines: the server starts (with name, version, repo path and git short SHA, so you know which build you are driving), `tools/list` returns exactly the 7 tools, and `list_subjects` succeeds. That proves credentials, region host and the `subjects_get` scope. `list_subjects` is the cheapest live call, about 6 KB. A FAIL with `code=auth_failed` means bad credentials. `config_missing` means the env file lacks a variable. `scope_missing` means the LibApps app lacks a GET scope.

## Drive

- **Full proof:** `verify check` runs every check below in about 20 s and exits non-zero on any failure.
  - `--only live`: the live tool checks only.
  - `--only errors`: the missing- and bad-credential scenarios only. These need no network beyond one rejected token POST.
  - `--guide-id <id>`: forces the sub-page guide.
  - `--no-cross-check`: skips the independent raw API comparison. This also weakens the status and hidden-item checks.
- **One feature:** `verify call <tool> '<json>'` prints one tool result as JSON, saves it as an artifact, and privacy-scans it. Examples:
  - `verify call get_guide '{"guide_id": "<id>"}'`
  - `verify call get_guide_content '{"guide_id": "<id>", "page_id": "<pid>", "include_subpages": true}'`
- **Feature recipes:** read [`features/README.md`](features/README.md) and the matching feature file before proving a single feature.

`check` asserts:

1. `initialize` works and `tools/list` returns exactly `search_guides, get_guide, get_guide_content, list_subjects, search_databases, get_database, find_subject_librarians`.
2. `search_guides(query="chemistry")` returns `source: "server"`. Every id is published and of a non-Internal/Template type, cross-checked against one raw unfiltered `/1.2/guides` call.
3. `search_guides()` reports `total` equal to the raw count of published, non-Internal/Template guides.
4. `get_guide` returns `not_public` for an unpublished, a private, and an internal/template guide id taken from the raw list, without naming them.
5. On a guide with visible sub-pages, `get_guide` returns the tree. That guide is picked from one raw `guides?expand=pages&status=1` call, the one with the most sub-pages under a single parent. On the same guide, `get_guide_content(page_id=<parent>, include_subpages=true)` returns non-empty box markdown for sub-pages. `source="api"` returns `placement: "unknown"` blocks.
6. `list_subjects` is non-empty.
7. `search_databases(query="jstor")` returns results. `search_databases()` reports `total` equal to the raw `/az` count minus hidden items. `get_database` works for a listed id and returns `not_found` for a hidden id.
8. `find_subject_librarians` returns librarians with `profile_url`, and none of them belongs to an account without a public profile (raw `/accounts`, held in memory only).
9. `get_guide(guide_id="abc")` returns exactly `{ok: false, error, code: "invalid_input"}`.
10. **Privacy:** no output anywhere contains the keys `email`, `internal_note`, `library_review`, `customer_id` or `account_id`, any email-like string, the client secret, or the driver's access token. The server's stderr contains neither the secret nor the token.
11. **Errors:** with no credentials, all 7 tools return `config_missing`. With a bogus secret, all 7 return `auth_failed`. Nothing leaks in either case.

## Evidence

- Every run writes to `--out`, default `/tmp/uc-libapps-mcp-verify/<YYYYmmdd-HHMMSS>/`, outside the repo so artifacts never get committed. It contains:
  - `NN-<label>.json`: each tool result, exactly as the client received it.
  - `server-stderr*.log`: server stderr for each scenario.
  - `summary.json`: one entry per command run into that directory, listing every check with pass/fail and an evidence string. Reusing `--out` for several `call`s appends; artifacts keep counting up and are never overwritten.
- The PASS/FAIL lines on stdout carry counts and short snippets: totals, first names, a sub-page box excerpt. Quote those in reports. Never quote env values.
- Proof standards:
  - Exercise the real stdio path, not `Service` methods. The unit tests already cover those with mocks.
  - Pair each action (the tool call) with its resulting state (the JSON).
  - For exclusion claims, the cross-check against raw API data is the side-effect proof. A result that merely looks clean is not proof.
- Unit tests are a separate gate: `uv run pytest -q`. They are mocked and offline. The opt-in live test is `(set -a; . ~/.config/uc-libapps-mcp/env; set +a; LIBAPPS_LIVE_TESTS=1 uv run pytest -m live -q)`.

## Cleanup

- Each command waits for its own server child to exit. If a run is interrupted, find only the processes it started: `pgrep -af "uc_libapps_mcp"`. Kill those PIDs, and never kill by name a server an MCP host launched.
- Keep the artifact directory. It is the proof. Delete old runs under `/tmp/uc-libapps-mcp-verify/` only when asked.

## Gotchas

- The API ignores `limit`, `page` and `offset`, so every list is a full download (guides about 0.4 MB, A–Z about 2.6 MB). The server caches them, and `check` makes only a handful of raw calls. Do not loop `check`.
- Most guides have no sub-pages. Do not assume the top search hit has any. Use `--guide-id` or let `check` pick one.
- A few guides are published but of type Internal or Template, so the published count from the raw API is slightly higher than `search_guides` `total`. That gap is expected.
- `/1.2/az/{id}` returns 404 by design, which is why `get_database` serves from the cached list.
- Email addresses inside page text are replaced with `[email removed]` unless `LIBAPPS_EXPOSE_EMAIL=1`. The privacy scan expects the default.
- `uv sync` without `--extra dev` removes pytest from the environment. The driver itself uses `uv run` and does not sync.
