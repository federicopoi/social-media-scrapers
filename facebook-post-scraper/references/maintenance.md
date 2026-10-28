# Repairing the downloader

Use this reference when a normal invocation fails. The ordinary run command and output contract are in [AGENTS.md](../AGENTS.md).

## Authentication

An expired login or denied Keychain access needs the user's existing Facebook session restored. Use `--cookies` with the user's own Netscape cookie export, or `--browser` and `--profile` to select their authorized local browser. There is no machine-specific profile default. Read only Facebook-domain cookies. Do not export or print them, and do not use computer control to sign in.

The gallery-dl cookie reader can report a malformed database while a live browser is writing to it. A normal read-only SQLite connection may still work even when an immutable read does not. Recheck the specific cookie database read-only before proposing a repair. Do not alter the original database or close browser processes to work around this.

## Page resolution

Shared page links are resolved by their normal HTTP redirects. Numeric profile links, `profile.php?id=...`, and `/people/name/ID/` links expose the target ID directly. For a vanity handle that remains a handle, `facebook_business.resolve_page` reads the HTTP page's `ProfileCometHeaderQuery` payload: `data.user.id` must match `data.user.profile_header_renderer.user.id`, and that header owner's Facebook URL must match the requested handle. Case, host aliases, trailing slashes and tab suffixes are normalized. Conflicting matching headers fail instead of choosing an arbitrary ID. Never use a generic `userID` match from the page shell: it can identify the logged-in viewer instead of the business. Stop rather than download the wrong person's collection.

## Stale GraphQL query

The operation is `CometAlbumPhotoCollagePaginationQuery`. Its persisted ID changes over time. The current code accepts `--doc-id NEW_ID` so an updated ID can be tested without rewriting the workflow.

1. Use the authorized HTTP session to fetch the album page at `/media/set/?set=pb.PAGE_ID.-2207520000`.
2. Read its referenced public JavaScript resources through HTTP. Find `CometAlbumPhotoCollagePaginationQuery_facebookRelayOperation` and its exported query ID.
3. Inspect `CometAlbumPhotoCollagePaginationQuery.graphql` in those resources. Compare its argument definitions and provided variables with `scripts/fb_photos.py`; an ID change alone may not be enough.
4. Test a small requested subset in a temporary destination, then a full run. Validate image decoding and dimensions, archive contents and hashes, and removal of the raw folder. Do not label an error response or repeated cursor as an empty, complete collection.

The query currently returns about 14 photos per paginated response despite requesting 100. The initial album response supplies 24 photos. These are observed source behaviors, not guarantees or configurable caps. The in-memory `dpr=4` request and `scale=4` query request high-density renditions; Facebook may choose different JPEG encodings. Do not claim original or byte-identical quality without comparing actual downloads.

## ZIP failures

The final ZIP is published only after every member's SHA-256 matches the verified source. A failed build or check leaves the raw folder intact and removes the unfinished `.zip.part`. A cleanup failure after publication leaves a verified ZIP and reports the surviving folder. Preserve those recovery files; do not delete them merely to achieve a tidy success message.

## Business details and reviews

`scripts/facebook_business.py` normally needs the profile URL and `&sk=reviews`, read concurrently. `scripts/business_data.py` parses only top-level target user/node objects whose IDs match the requested business; account/viewer objects and unrelated suggestions are excluded. The profile supplies name, bio, category, and typed directory tiles for contact and basic information. Native About collection links use names such as `directory_intro`, `directory_contact_info`, and `directory_basic_info`. An older About-tab alias can redirect to the signed-in viewer's profile, so always verify the returned target ID before extracting anything.

The review page streams data under queries including `ProfileCometReviewsTabRootQuery` and `ProfileCometReviewsFeedQuery`. The displayed count and final `page_info` can arrive in separate result chunks. Keep their query labels: a timeline cursor is not a review cursor. Preserve critical error chunks even when their `data` is null, and keep failed fields or review coverage explicitly partial/unavailable.

The separate pagination operation is `ProfileCometReviewsFeedRefetchQuery`; its current ID is in `REVIEW_DOC_ID` and can be overridden with `--review-doc-id`. Its variables include the page ID, cursor, provider flags, `feedLocation=PAGE_SURFACE_RECOMMENDATIONS`, and `renderLocation=timeline`. The operation ID and shape were inspected in Facebook's JavaScript on 2026-10-23; the backend can still return a null or refused feed for a session. Do not treat that as an empty, complete review history. The collector retains the initial reviews and reports partial coverage when subsequent requests cannot advance. Test provider/schema repairs against a page that actually has multiple review pages before claiming broad history coverage.

The output contract is [bundle-schema.md](bundle-schema.md). `business.md` and `business.json` are both mandatory archive members, alongside `report.json`, `photo-index.csv`, and every verified image. An archive may be valid while its source data is partial: report the bundle's coverage warnings rather than silently equating archive integrity with complete Facebook data.
