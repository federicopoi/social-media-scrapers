# Facebook & Instagram Scrapers

Two independent, cookie-based command-line scrapers in one repository. Give either tool a profile/page URL and receive a verified local ZIP of the available photos and source information. No API key, Apify account, website builder, or browser automation is required.

| Folder | Collects |
| --- | --- |
| [facebook-post-scraper](facebook-post-scraper/README.md) | Available page photos, business/contact/About details and accessible reviews |
| [instagram-post-scraper](instagram-post-scraper/README.md) | Latest **N posts**, captions, dates, profile information and all returned carousel photos; video posts contribute preview images, not video files |

## Run it with Codex or Claude Code

Use a **local** agent session that can run terminal commands and access your filesystem. Clone/open this repository, then paste the prompt below with your URL. If you only have the GitHub link, give the agent the link and prompt together. A chat-only or remote agent cannot read cookies stored on your computer.

```text
Use https://github.com/federicopoi/social-media-scrapers.
Clone it into a new folder if needed. Read the root README.md and AGENTS.md,
then the README.md and AGENTS.md in the matching platform subfolder.

Collect from: PASTE_FACEBOOK_OR_INSTAGRAM_URL_HERE
For Instagram, collect the latest 100 posts. For Facebook, collect all
available photos unless I specify a photo limit.

Check Python 3.11+ and uv. Help me install missing requirements through the
normal permission prompts. Run the existing scraper from its own subfolder;
do not write a replacement or build a website.

Find my Facebook or Instagram login cookies locally for the matching platform.
Use the cookie file or browser/profile I specify. If unspecified, try the
scraper's default local Chrome cookie reader. If I use a different browser or
the profile is unclear, ask which browser/profile to use. Read only the target
platform's cookies into memory. Do not search unrelated profiles, print cookies,
export a cookie jar, or copy login material into the repository.

Use cookies and direct HTTP only. Do not use computer use, control a browser,
use Playwright/Selenium, automate login, or ask for passwords or two-factor codes.
Reading the selected browser's local cookie database is allowed.

If no valid login cookies are available, ask me to sign into the matching
platform myself in my browser and confirm when ready; then retry. If cookie
decryption is unsupported, explain how I can export my own Netscape cookie
file outside the checkout and ask only for its local path. Never ask me to
paste or upload cookies into chat. Stop on access restrictions or challenges.

Run the scraper, inspect its JSON result and verify the ZIP exists. Return
the local ZIP path, actual photo/post/review counts, coverage warnings and
cleanup outcome. Preserve recovery folders on failure. Keep all credentials
and downloaded output out of Git and GitHub.
```

Codex instructions are in [AGENTS.md](AGENTS.md); Claude Code uses [CLAUDE.md](CLAUDE.md). Each platform folder has its own detailed instructions. Agents still need your environment's normal filesystem, network and credential-store permissions.

## Install

Requires **Python 3.11+**, Git and [uv](https://docs.astral.sh/uv/getting-started/installation/). `uv run` installs each script's declared dependencies automatically. The tools pin different gallery-dl versions, so run them separately rather than combining their environments.

```sh
git clone https://github.com/federicopoi/social-media-scrapers.git
cd social-media-scrapers
```

### Facebook

```sh
cd facebook-post-scraper
uv run scripts/scrape_to_zip.py 'https://www.facebook.com/YOUR_PAGE'
# Optional photo limit:
uv run scripts/scrape_to_zip.py 'https://www.facebook.com/YOUR_PAGE' --limit 100
```

### Instagram

From the repository root:

```sh
cd instagram-post-scraper
uv run scripts/scrape_to_zip.py 'https://www.instagram.com/YOUR_PROFILE/' --posts 100
```

`--posts` counts posts, not individual images. A carousel can produce several images. Default: 12 posts; supported request range: 1–1000. The result depends on what Instagram exposes to your session. This does not crawl stories, highlights or comments separately.

## Your own login cookies

Sign into the relevant platform **yourself** first. The default reader accesses saved cookies from your local Chrome profile without opening/controlling the browser. Use a selected profile or a Netscape cookie export when needed:

```sh
# Run in the matching platform folder; replace the URL and profile as needed:
uv run scripts/scrape_to_zip.py 'PROFILE_OR_PAGE_URL' --browser chrome --profile 'Default'
uv run scripts/scrape_to_zip.py 'PROFILE_OR_PAGE_URL' --browser firefox
uv run scripts/scrape_to_zip.py 'PROFILE_OR_PAGE_URL' --cookies '/absolute/path/outside-the-repo/cookies.txt'
```

Facebook requires Facebook-domain login cookies; Instagram requires Instagram-domain cookies, including a valid `sessionid`. They are not interchangeable. Export cookies in **Netscape format**, not JSON or a copied `Cookie:` header. On macOS, cookie reading may require a Keychain permission prompt. Decryption support varies by browser/OS; a local cookie file is the fallback. If login expires or a challenge appears, restore the session manually and retry. No restrictions are bypassed.

**Cookie files are login credentials.** Store them outside the checkout, restrict access, and never commit, upload, paste, or share them. No real cookies, keys, environment files, browser databases or personal run artifacts are included here. See [SECURITY.md](SECURITY.md).

## Output and limits

By default each tool writes a uniquely named ZIP to `~/Downloads`; `--downloads PATH` changes the destination. Images are decoded and checked, and ZIP member hashes are verified before only that run's temporary folder is removed. Failures preserve the recovery folder. A verified ZIP can still contain clearly reported business-data or review coverage warnings.

Read each platform README for the exact output schema and flags. Use `uv run scripts/scrape_to_zip.py --help` inside its folder. No live source account is bundled, and there is no guarantee of complete history, original-upload resolution, or access to hidden/private content. Use content you own or have permission to download. Platform responses and internal query schemas can change.

## Offline checks

Run each command from its platform folder. These tests use synthetic fixtures and do not read your real cookies or contact Facebook/Instagram.

```sh
# facebook-post-scraper/
PYTHONPATH=scripts uv run --with pillow --with 'gallery-dl==1.32.17' python -m unittest discover -s tests -v

# instagram-post-scraper/
PYTHONPATH=scripts uv run --with pillow --with 'gallery-dl==1.32.18' python -m unittest discover -s tests -v
```

The repository includes scraper code only. Website templates, the local Website Builder integration, downloaded media, personal browser settings and live-run records are excluded.
