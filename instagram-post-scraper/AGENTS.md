# Instagram Post Scraper

A profile URL plus --posts N requests the latest N exposed profile posts (default 12). Run the existing local script, not a website builder:

```sh
uv run scripts/scrape_to_zip.py 'INSTAGRAM_PROFILE_URL' --posts 12
```

Read README.md. Use supplied Instagram cookie file or selected local browser/profile; default Chrome only if unspecified. Read only Instagram cookies locally. No computer use, browser control/clicking, automated login, Playwright/Selenium or API key. Never ask for passwords, 2FA codes, raw cookies or cookie uploads into chat. If login is missing/expired/challenged, ask the user to sign in themselves, then retry after confirmation. If decryption is unsupported, guide them to a Netscape cookie export outside this folder and ask only for its local path. Do not scan unrelated profiles or bypass restrictions.

--posts counts posts, not images. Collect all returned carousel photos; video posts use preview images and must be labelled as such. Keep canonical post URLs and captions, UTC timestamps and source provenance. Never invent location, hours, reviews or contact fields from missing profile metadata. No raw API dumps, session values or signed CDN URLs in handoff files.

Inspect the JSON result. Verify the ZIP exists, then report path, requested and actual post count, total image count, preview warnings, selection status and cleanup outcome. A smaller exhausted feed is different from a failed/incomplete lookup. Preserve recovery folders on failure; never manually delete source data to make a run appear successful. The script removes only its own extracted working folder after ZIP verification.

For code changes, run the documented offline tests. A historical live collection is not a guarantee of unrestricted coverage of every account. Website building and deployment are separate tasks.
