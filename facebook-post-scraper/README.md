# Facebook Post Scraper

Turn a Facebook business-page URL into a local ZIP of its available photos, business details and accessible reviews. Runs directly from the command line with **your own Facebook login cookies**. Authentication is cookie-based only: direct HTTP requests, no computer use, browser clicking or automated login. No Apify account, Facebook developer account or API key is required.

This repository contains only the scraper. It does not include a website builder, anyone’s login session, environment configuration, downloaded photos, or live customer run records.

## Run it with Codex or Claude

Open this repository in a **local Codex or Claude Code session** and give the agent a Facebook page URL. Ask it to **find your Facebook login cookies locally and run the scraper without computer use or browser automation**. The included [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md) explain the scraper workflow. You do not need to write or run the download commands yourself; the agent can check the setup, run the scraper and return the ZIP.

If you have not cloned the repo yet, paste this prompt into an agent that can run terminal commands on your computer. Replace `FACEBOOK_PAGE_URL` with the actual business-page URL:

```text
Use https://github.com/federicopoi/social-media-scrapers to download the
available photos and business information for FACEBOOK_PAGE_URL.

Clone the repository into a new folder if needed; otherwise use the existing
checkout. Enter facebook-post-scraper/ within the checkout. Read its README.md
and AGENTS.md before running anything. Check Python
and uv, and help me install any missing requirements using the normal
permission prompts. Use the existing scraper, not a new implementation.

Find my Facebook login cookies locally and use them for direct HTTP requests.
Use the cookie file or browser/profile I specify. If I have not specified one,
try the scraper's default local Chrome cookie reader. If I use another browser
or the profile is unclear, ask me which one. Read only Facebook cookies; do not
search unrelated profiles, print cookie values, or export the cookie jar.

Use cookies only. Do not use computer use, open/control browser windows,
click through Facebook, run Playwright/Selenium, or automate login.
Reading a local browser cookie database is allowed; controlling the browser
is not part of this workflow.

If no valid Facebook login cookies are available, ask me to sign into Facebook
myself in my browser and tell you when I am ready, then retry cookie reading.
If browser-cookie decryption is unsupported, explain how I can supply my own
Netscape cookie export outside the repo and ask only for its local file path.
Do not ask for my password, 2FA code, cookie uploads or raw cookies in chat.
Do not bypass a login challenge or access restriction.

Once authentication is available, run scripts/scrape_to_zip.py for the URL.
Collect all exposed photos unless I request a limit. Read the command's JSON
result and verify that the final ZIP exists. Give me its local path, photo and
review counts, any coverage warnings, and whether its temporary folder was
removed. If blocked, explain the actual error and what I need to do next.
Keep credentials and downloaded output out of Git. Do not build a website.
```

If the repo is already open in your agent, a shorter request is enough:

```text
Download the photos and business information for FACEBOOK_PAGE_URL.
Follow AGENTS.md. Find my Facebook login cookies locally and use direct HTTP
requests only, without computer use or browser automation. If valid cookies
are unavailable, ask me to sign into Facebook myself, then retry and return
the verified ZIP.
```

**The agent reads cookies; it never operates your browser.** You perform any login yourself. The agent needs local terminal/file access and your permission to read the selected browser profile or cookie file. A chat-only or remote/cloud session cannot automatically read the browser on your computer; use a local agent session for this workflow. The scraper itself does not open a login screen or control a browser. On a login failure, the agent should pause for your action and retry after you confirm that the session is ready.

## Requirements

- Python **3.11 or newer**.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended), or pip in a virtual environment.
- Your own Facebook login cookies, supplied by a Netscape-format cookie file or read locally from your browser profile.
- Permission to access and download the target page’s content.

## Quick start: cookie file

```sh
git clone https://github.com/federicopoi/social-media-scrapers.git
cd social-media-scrapers/facebook-post-scraper
uv run scripts/scrape_to_zip.py 'https://www.facebook.com/PAGE_HANDLE' --cookies '/absolute/path/to/facebook-cookies.txt'
```

`uv` installs the script’s declared dependencies automatically. Replace the example page and cookie-file path with your own. Quote the URL, especially when it contains `?` or `&`.

The command prints a JSON result with the final `zip_path`, image/review counts, source-data warnings, archive verification and cleanup status. The default destination is your account’s `Downloads` directory. `--downloads` chooses another folder.

### Getting your login cookies

1. Sign into **your own** Facebook account in your browser. Resolve any login challenge there first.
2. Either use browser-profile mode below (no export required), or use a trusted local cookie-export tool to export **only `facebook.com` cookies**, in **Netscape cookies.txt format**. A JSON export, copied `Cookie:` header or Facebook password is not accepted.
3. Save that file **outside this repository**, then pass its path with `--cookies`. It must contain valid, unexpired `c_user` and `xs` login cookies. Export all Facebook cookies for best compatibility, rather than manually pasting individual values. HttpOnly cookie rows are supported.
4. If the login expires, sign in again and replace your local export. Cookie presence alone cannot guarantee Facebook will accept the session or expose a particular page.

A cookie file grants access to your account. **Never send it to someone else, commit it, attach it to an issue, paste it into a chat, or upload it to a cookie-conversion website.** If possible, restrict the file to your user account (`chmod 600 /path/to/facebook-cookies.txt` on macOS/Linux). Review any export tool’s permissions before using it. See [SECURITY.md](SECURITY.md).

In cookie-file mode, the script reads only Facebook-domain cookies into memory, excludes expired cookies and unrelated domains, does not open a browser profile, and does not rewrite/export the cookie file. Photo CDN downloads use a separate session without your Facebook cookies.

## Find Facebook cookies locally (no computer use)

```sh
uv run scripts/scrape_to_zip.py 'FACEBOOK_URL' --browser chrome
uv run scripts/scrape_to_zip.py 'FACEBOOK_URL' --browser edge --profile 'Default'
uv run scripts/scrape_to_zip.py 'FACEBOOK_URL' --browser firefox
```

These commands read saved Facebook cookies from a local browser profile into memory and make direct HTTP requests. They do **not** open or control Chrome, Edge or Firefox. This is cookie-based authentication, not browser automation.

Chrome is the default cookie source; there is **no hard-coded personal browser profile**. Omit `--profile` to use the browser reader’s default, or choose your own profile name/path. `--browser` and `--cookies` are alternative authentication sources; do not combine them. `--profile` applies only to browser login.

Browser-cookie extraction depends on your operating system, browser encryption and profile permissions. macOS may ask for Keychain access. Some browser/OS combinations prevent decryption; use your own Netscape cookie export in that case. The scraper does not automate login or bypass session restrictions.

## Without uv

```sh
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/scrape_to_zip.py 'FACEBOOK_URL' --cookies '/path/to/facebook-cookies.txt'
```

On Windows, `py` may be your Python launcher. Pass a Windows cookie path such as `C:\Users\YOUR_NAME\facebook-cookies.txt` rather than the Unix examples above. Scripts use Python paths; no Bash script is required. Tests are offline and portable; a live run on every OS/browser combination is not guaranteed.

## Options

```sh
# All available photos (default); Facebook can still limit visibility
uv run scripts/scrape_to_zip.py 'FACEBOOK_URL' --cookies '/path/to/facebook-cookies.txt'

# Deliberately request a smaller collection
uv run scripts/scrape_to_zip.py 'FACEBOOK_URL' --cookies '/path/to/facebook-cookies.txt' --limit 20

# Choose output location or archive name
uv run scripts/scrape_to_zip.py 'FACEBOOK_URL' --cookies '/path/to/facebook-cookies.txt' --downloads './output' --name 'Example Business'

uv run scripts/scrape_to_zip.py --help
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--cookies PATH` | Unset | Netscape-format Facebook cookie file; skips browser reading |
| `--browser NAME` | `chrome` | Browser to read when no cookie file is supplied |
| `--profile NAME_OR_PATH` | Browser default | Your local browser profile |
| `--downloads PATH` | `~/Downloads` | Destination for the verified ZIP |
| `--limit N` | `0` | Maximum photos; zero requests all exposed photos |
| `--name TEXT` | Detected business name | Override the ZIP’s business-name prefix |
| `--workers N` | `6` | Concurrent image downloads |
| `--doc-id ID` | Code default | Override the photo query ID after a Facebook schema change |
| `--review-doc-id ID` | Code default | Override the independent review query ID |

Vanity page URLs, numeric `profile.php?id=...` URLs, `/people/name/ID/` URLs and resolvable shared page links are supported. This is a business-page photo collector, not a general private-profile or group scraper.

### Photos and manifest only

The lower-level command keeps the downloaded folder and produces `photo-index.csv` and `report.json`, without business metadata, reviews, ZIP creation or raw-folder cleanup:

```sh
uv run scripts/fb_photos.py 'https://www.facebook.com/profile.php?id=PAGE_ID' --cookies '/path/to/facebook-cookies.txt' --output './output'
```

Use a numeric page URL for this lower-level command; use the main ZIP command for vanity/share-link resolution.

## What is in the ZIP?

Each run creates a unique ZIP with one business folder containing:

- Available photos and an optional profile-picture candidate (not automatically a verified logo).
- `business.md`: readable contact/About details, attributed reviews and coverage warnings.
- `business.json`: structured business fields, sources, relative image paths and availability flags.
- `photo-index.csv`: image filenames, public source links, sizes, dimensions and SHA-256 hashes.
- `report.json`: collection status, pagination and download diagnostics.

The tool validates image decoding, dimensions, byte counts and hashes, then reads and verifies every ZIP member before removing **only that run’s** extracted folder. Existing archives are never overwritten. If a photo download or ZIP check fails, it preserves the working folder and reports failure. If only cleanup fails, a verified ZIP can exist alongside the remaining raw folder; inspect the JSON result.

A verified ZIP does not imply all Facebook fields were available. Missing/blocked contacts and partial reviews remain explicitly marked. A verified empty photo collection can still return business data. [Full bundle schema](references/bundle-schema.md).

## Limitations and troubleshooting

- **Login/permission problems:** restore your Facebook login and refresh your cookie export. Check that you can view the target page in that account. Do not share login material when asking for help.
- **Missing business fields or reviews:** Facebook may not return them to your session. The tool records missing/partial coverage instead of inventing values.
- **Throttling or refused queries:** bounded retries are built in. Stop rather than repeatedly retrying a refused collection.
- **Facebook changes:** this uses Facebook’s internal web responses and persisted GraphQL queries, not a stable public API. Query IDs and response schemas can change. The underlying collection queries were checked in October 2026; future operation is not guaranteed. [Maintenance guide](references/maintenance.md).
- **Photo scope:** collects exposed page photos. It does not download videos, post comments, arbitrary full post timelines, restricted content, or Google reviews. Downloaded dimensions do not prove original-upload quality.
- **Storage:** temporary uncompressed media and the final ZIP both need disk space. A failed run intentionally leaves its recovery folder.
- **Reuse:** respect the content owner’s rights, Facebook’s terms, and the access granted to your account. Downloaded business information and reviews may contain personal data; review a bundle before sharing it.

## Development

```sh
PYTHONPATH=scripts uv run --with pillow --with 'gallery-dl==1.32.17' python -m unittest discover -s tests -v
```

For PowerShell, first set `$env:PYTHONPATH = "scripts"`, then run the same `uv run ...` command without the `PYTHONPATH=scripts` prefix. Tests use synthetic fixtures and do not need login cookies or make live Facebook requests. Keep downloaded media and live-run reports outside the repo. A contribution must not contain secrets or account data.

`AGENTS.md`, `CODEX.md`, `CLAUDE.md` and `SKILL.md` provide optional agent instructions for this scraper only. No website-building tools are bundled.

## License

[MIT](LICENSE). This covers the scraper code, not Facebook content or third-party dependencies.
