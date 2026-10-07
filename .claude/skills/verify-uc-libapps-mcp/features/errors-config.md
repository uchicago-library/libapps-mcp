# Errors and configuration

Every tool returns `{"ok": false, "error", "code", "statusCode"?}` on failure, with no credential, token or stack trace in the output or in server stderr. Without credentials the server still starts and lists its tools, and each call returns `config_missing`.

## Sub-features

- `err-config-missing`: no client id or secret returns `config_missing`, naming the variables but not their values.
- `err-auth-failed`: rejected credentials return `auth_failed` with only the HTTP status.
- `err-invalid-input`: non-numeric ids, bad `source`, or a bad `limit`/`offset` return `invalid_input`.
- `err-not-found`: unknown guide, page or database returns `not_found`.

## How to get to it (user POV)

- Install the server in an MCP host without credentials, or with a wrong secret, and ask anything.
- Ask about a guide id that does not exist.

## Driving it with scripts/verify

Preconditions:

- None for the missing-credential case. One rejected token POST for the bad-secret case.

- **Both credential cases.** Run `.claude/skills/verify-uc-libapps-mcp/scripts/verify check --only errors`. Two PASS lines: every tool returns `config_missing` with no credentials and `auth_failed` with a bogus secret, and nothing leaks into outputs or `server-stderr-*.log`.
- **Invalid input.** Run `.claude/skills/verify-uc-libapps-mcp/scripts/verify call get_guide '{"guide_id": "abc"}'`. The result is exactly `{"ok": false, "error": "guide_id must be a numeric id.", "code": "invalid_input"}`.
- **Not found.** Run `.claude/skills/verify-uc-libapps-mcp/scripts/verify call get_guide '{"guide_id": "1"}'`. The result has `code: "not_found"`.

## Gotchas

- `check --only errors` builds the child environment without any `LIBAPPS_*`/`LIBGUIDES_*` variables from your shell, so exported credentials cannot mask the missing case.
- Bad config values (for example `LIBAPPS_MAX_CHARS=lots`, or a non-Springshare `LIBAPPS_API_BASE` without `LIBAPPS_ALLOW_CUSTOM_API_BASE=1`) return `config_invalid`.
