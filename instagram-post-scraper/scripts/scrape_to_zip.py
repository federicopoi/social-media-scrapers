# /// script
# requires-python = ">=3.11"
# dependencies = ["gallery-dl==1.32.18", "pillow>=11,<13"]
# ///
"""Collect the latest N Instagram profile posts into a verified local ZIP."""
import argparse
import json
from pathlib import Path
from instagram_scraper import scrape


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='Instagram profile URL, @handle or handle')
    parser.add_argument('--posts', type=int, default=12, help='Number of latest distinct posts (default 12; 1–1000)')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument('--cookies', type=Path, help='Your Netscape-format Instagram cookie export')
    auth.add_argument('--browser', default='chrome', help='Local Instagram cookie source (default chrome); no browser automation')
    parser.add_argument('--profile', help='Your chosen local browser cookie profile')
    args = parser.parse_args()
    if not 1 <= args.posts <= 1000:
        parser.error('--posts must be between 1 and 1000')
    if args.cookies and args.profile:
        parser.error('--profile applies to local browser cookies, not --cookies')
    try:
        result = scrape(args.url, args.posts, args.downloads, args.browser, args.profile, args.cookies)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
