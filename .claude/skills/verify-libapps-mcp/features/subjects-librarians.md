# Subjects and librarians

`list_subjects` lists the site's guide subjects. `find_subject_librarians` resolves a subject by name or id and returns librarians who have an enabled public profile for it. It returns only public fields: name, title, pronouns, profile link, photo, and the contact details the profile shows. Login emails never appear.

## Sub-features

- `subjects-list`: subjects with published guides by default, or all with `with_published_guides=false`.
- `librarians-resolve`: exact name, then id, then a unique substring. Ambiguous input returns `invalid_input` with candidates. No match returns `not_found`.
- `librarians-public-only`: only `en_page=1` profiles, with allowlisted fields.

## How to get to it (user POV)

- Ask "who is the librarian for <subject>?". The host calls `find_subject_librarians`, using `list_subjects` if it needs the exact name.

## Driving it with scripts/verify

Preconditions:

- `verify doctor` passes.

- **List subjects.** Run `.claude/skills/verify-libapps-mcp/scripts/verify call list_subjects '{}'`. The result has `total` > 0 and items `{id, name, slug, parent_id}`.
- **Find librarians.** Run `.claude/skills/verify-libapps-mcp/scripts/verify call find_subject_librarians '{"subject": "<subject name>"}'`. The result has `subject` resolved, and each librarian has `name` and `profile_url`, with only the keys `name, title, pronouns, profile_url, image_url, subjects, phone, address, website`. There is no `email`.
- **Ambiguous input.** Run `.claude/skills/verify-libapps-mcp/scripts/verify call find_subject_librarians '{"subject": "stud"}'` or any substring that matches several names. The result has `code: "invalid_input"` and lists candidates.
- **Public-only proof.** Run `.claude/skills/verify-libapps-mcp/scripts/verify check --only live` and read `find_subject_librarians excludes non-public profiles`. It must PASS.

## Gotchas

- Many subjects have no librarian with a public profile, so an empty list can be correct. `check` walks subjects until one has librarians.
- Contact details appear only when the profile's `disp_connect_general` flag is on. The profile email appears only with `LIBAPPS_EXPOSE_EMAIL=1`.
