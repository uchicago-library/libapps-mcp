# LibApps API v1.2 notes

Behaviors of the Springshare LibApps API v1.2 that this server relies on. Some
differ from the published documentation; where they do, observed behavior wins.

## Auth

- `POST {base}/1.2/oauth/token` with form fields `client_id`, `client_secret`,
  `grant_type=client_credentials`.
- Response: `access_token`, `token_type: "Bearer"`, `expires_in` (3600), and a
  space-separated `scope` such as `az_get subjects_get accounts_get assets_get guides_get`.
- Bad credentials return a 4xx with an OAuth `invalid_client` JSON body.
- Region hosts: `lgapi-us`, `lgapi-ca`, `lgapi-eu`, `lgapi-au` (`*.libapps.com`).
  A site's host is shown on its Tools > API page.

## General

- `limit`, `page`, and `offset` are ignored on list endpoints; full lists come
  back. Lists are cached whole and paged client-side. Send `Accept-Encoding: gzip`.
- No rate-limit or `Retry-After` headers have been observed.
- **Value types are inconsistent.** IDs and flags arrive as ints or strings
  depending on the endpoint and nesting level (for example, page and box fields
  inside a guide are all strings). Compare flags as `str(v).strip() == "1"` and
  convert positions with `int()`.

## Guides

- `GET /1.2/guides?expand=subjects&status=1` lists published guides
  (`status=1,2` adds Private). Unfiltered lists include unpublished guides.
- `status`: 0 Unpublished, 1 Published, 2 Private. `type_id`: 1 General Purpose,
  2 Course, 3 Subject, 4 Topic, 5 Internal, 6 Template (`type_label` has the label).
  Internal and Template guides are always excluded.
- `GET /1.2/guides/{id}?expand=subjects,pages.boxes` returns a **list** with one
  guide (empty if no match).
  - Pages are flat, linked by `parent_id` (`"0"` = top level), ordered by `position`.
  - Hidden pages (`enable_display` 0) are included and may lack `boxes`.
  - Boxes are metadata only (`id`, `name`, `enable_display`, `position`,
    `column_id`); they carry no content.
  - Page `url` is `https://<site>/c.php?g=<guide>&p=<page>`; `friendly_url` may be empty.
  - `redirect_url` marks link pages.
- `expand=owner` includes the owner's login email, so it is never requested.
- `GET /1.2/guides?search_terms=<q>&sort_by=relevance` is a ranked,
  published-only, apparently full-text search. Without `sort_by=relevance`,
  `search_terms` matches names only and includes unpublished guides.

## Guide content

- Public guide pages mark each box `id="s-lg-box-{box_id}"` with the same IDs
  as the API, title in `.s-lib-box-title`, and body in `.s-lib-box-content`,
  inside `#s-lg-guide-main`. Selecting boxes by API ID drops page chrome.
- Private guides render publicly (with `noindex`); unpublished guides return 404;
  hidden pages are reachable by URL.
- `GET /1.2/assets?guide_ids=<id>&asset_types=1` returns a guide's Text assets
  with rich-text HTML in `description`, but no page or box placement. Payloads
  can be large.

## Subjects

- `GET /1.2/subjects?guide_published=2` lists subjects with published guides.
  Fields: `id`, `name`, `parent_id`, `slug`, `slug_id`.

## A-Z

- `GET /1.2/az?expand=subjects,az_types` returns all databases with `subjects`
  and `az_types`. Hidden items (`enable_hidden` `"1"`) are included and must be
  filtered out. `meta.enable_proxy` is a boolean.
- `GET /1.2/az/{id}` returns 404, so single items are read from the cached list.
- `GET /1.2/assets?asset_types=10&expand=az_props` adds `alt_names`,
  `enable_new`, `enable_trial`, `enable_popular`, and the sensitive
  `internal_note` and `library_review`. Only the first four are kept.

## Accounts

- `GET /1.2/accounts?expand=profile,subjects` returns every account, including
  the login `email`.
- `profile.en_page` (1) marks an enabled public profile. Public-looking fields:
  name, `profile.box.title`, `profile.pronouns`, `profile.url`, `profile.image`
  (when `image.show` is 1), and `profile.connect.*` when
  `profile.display.disp_connect_general` is `"1"`. `display` keys may be absent.
- Never public: top-level `email`, `customer_id`, `account_id`, `public_id`,
  widget HTML, timestamps.
