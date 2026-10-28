---
name: facebook-post-scraper
description: Use when a Facebook business page URL should become a website-source ZIP with photos, contact/About details, reviews, and a Markdown/JSON handoff using this local project, without browser automation or an API key.
---

# Facebook business data and photos to ZIP

Read [AGENTS.md](AGENTS.md) for the shared workflow. Find the user’s Facebook login cookies locally using the selected cookie file or local browser cookie reader. Make direct HTTP requests only: no computer use, browser control or automated login. If cookies are missing/expired, ask the user to sign in themselves, then retry. Run the existing command from this project directory:

```sh
uv run scripts/scrape_to_zip.py 'FACEBOOK_URL'
```

The default collects photos, the business profile, and accessible reviews; writes `business.md` and `business.json` for the website agent; verifies a uniquely named business ZIP in Downloads; then removes its raw working folder. Return the ZIP path, actual image/review counts, and coverage warnings. Incomplete image downloads or failed archive checks retain the working folder. Source fields that are missing or blocked stay explicitly unavailable. Read [references/bundle-schema.md](references/bundle-schema.md) for the output contract. Do not replace the optimized scripts with a browser-driven scrape or build a website during a collection-only request.
