#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["gallery-dl==1.32.17"]
# ///
"""Download a Facebook page's photo collection through batched HTTP requests.

No browser automation. Reads Facebook cookies from your local browser profile
or Netscape cookie export; image CDN requests do not carry those cookies.
The default persisted query was verified on 2026-10-23 and may need refreshing.
"""

import argparse
import concurrent.futures
import csv
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_DOC_ID = "29184794407817738"
_local = threading.local()


def business_name_from_album(album, page_id):
    for edge in (album.get("media") or {}).get("edges") or []:
        node = edge.get("node") or {}
        candidates = [node.get("owner") or {}] + ((node.get("creation_story") or {}).get("actors") or [])
        for actor in candidates:
            if str(actor.get("id")) == str(page_id) and actor.get("name"):
                return str(actor["name"]).strip()
    title = album.get("title") or ""
    if isinstance(title, dict):
        title = title.get("text") or ""
    if isinstance(title, str) and re.search(r"['’]s\s+Photos\s*$", title, re.I):
        return re.sub(r"\s*['’]s\s+Photos\s*$", "", title, flags=re.I).strip() or None
    return None


def safe_error(exc, photo_id=None):
    message = re.sub(r"https?://[^\s'\"<>]+", "[redacted URL]", str(exc))
    prefix = f"Photo {photo_id}: " if photo_id else ""
    return f"{prefix}{type(exc).__name__}: {message}"


def parse_page(raw):
    raw = re.sub(r"^\s*for\s*\(;;\);", "", raw)
    result = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Incomplete or malformed Facebook JSON stream") from exc
        if not isinstance(obj, dict):
            raise RuntimeError("Unexpected Facebook JSON response")
        errors = obj.get("errors") or []
        critical = [e for e in errors if e.get("severity") != "WARNING"]
        if critical:
            raise RuntimeError("Facebook query error: " + "; ".join(
                f"{e.get('code')}: {e.get('message')}" for e in critical))
        node = (obj.get("data") or {}).get("node") or {}
        media = node.get("media")
        if not isinstance(media, dict):
            continue
        info = media.get("page_info") or {}
        if not isinstance(info.get("has_next_page"), bool):
            raise RuntimeError("Facebook media response has no pagination status")
        if not isinstance(media.get("edges"), list):
            raise RuntimeError("Facebook media response has no edges list")
        photos = []
        for edge in media.get("edges") or []:
            photo = edge.get("node") or {}
            if photo.get("__typename") != "Photo":
                continue
            if not str(photo.get("id", "")).isdigit() or not (photo.get("image") or {}).get("uri"):
                raise RuntimeError("Facebook returned a photo without an image or numeric ID")
            photos.append(photo)
        if result:
            photos = result["photos"] + photos
        result = {"photos": photos, "cursor": info.get("end_cursor"),
                  "has_next": info["has_next_page"]}
    if result is not None:
        return result
    raise RuntimeError("Facebook did not return media data; login or query may be stale")


def validate_progress(page, seen_cursors):
    if page["has_next"] and (not page["cursor"] or page["cursor"] in seen_cursors):
        raise RuntimeError("Facebook returned a missing or repeated pagination cursor")


def page_id_from_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        raise ValueError("Expected a Facebook URL")
    query_id = parse_qs(parsed.query).get("id", [None])[0]
    if query_id and query_id.isdigit():
        return query_id
    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[0].isdigit():
        return parts[0]
    if len(parts) >= 3 and parts[0] == "people" and parts[2].isdigit():
        return parts[2]
    return None


def load_facebook_cookies(path):
    """Read a Netscape cookie export without saving it or disclosing its contents."""
    import http.cookiejar
    import warnings

    jar = http.cookiejar.MozillaCookieJar()
    try:
        # The stdlib warns with raw input for malformed rows; suppress those warnings.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            jar.load(str(Path(path).expanduser()), ignore_discard=True, ignore_expires=True)
    except (OSError, ValueError, http.cookiejar.LoadError):
        raise RuntimeError("Could not read cookie file; supply a readable Netscape cookies.txt export") from None
    filtered = http.cookiejar.CookieJar()
    for cookie in jar:
        domain = cookie.domain.lstrip(".").lower()
        if domain != "facebook.com" and not domain.endswith(".facebook.com"):
            continue
        # Netscape exports commonly use 0 for session cookies, rather than an expiry.
        if cookie.expires == 0:
            cookie.expires = None
            cookie.discard = True
        if not cookie.is_expired():
            filtered.set_cookie(cookie)
    names = {cookie.name for cookie in filtered if cookie.value}
    if not {"c_user", "xs"}.issubset(names):
        raise RuntimeError("Cookie file needs unexpired Facebook c_user and xs login cookies; sign in and export again")
    return filtered


def session(browser="chrome", profile=None, cookies=None):
    from gallery_dl import config, extractor
    jar = load_facebook_cookies(cookies) if cookies is not None else None
    config.clear()
    if jar is None:
        config.setg("cookies", [browser, profile, None, None, "facebook.com"])
    config.setg("sleep-request", 0)
    source = extractor.find("https://www.facebook.com/1/photos")
    source.initialize()
    if jar is not None:
        source.session.cookies.update(jar)
    return source


def _album_object(html):
    def walk(obj):
        if isinstance(obj, dict):
            album = obj.get("album")
            if isinstance(album, dict) and "media" in album and album.get("id"):
                return album
            for value in obj.values():
                result = walk(value)
                if result:
                    return result
        elif isinstance(obj, list):
            for value in obj:
                result = walk(value)
                if result:
                    return result
        return None

    for raw in re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S):
        try:
            result = walk(json.loads(raw))
        except json.JSONDecodeError:
            continue
        if result:
            return result
    raise RuntimeError("Facebook did not expose the photo collection to this session")


def _cdn_session():
    if not hasattr(_local, "cdn"):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        _local.cdn = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 502, 503, 504],
                      respect_retry_after_header=True)
        _local.cdn.mount("https://", HTTPAdapter(max_retries=retry))
    return _local.cdn


def _download(photo, destination):
    image = photo["image"]
    uri = image["uri"]
    host = (urlparse(uri).hostname or "").lower()
    if urlparse(uri).scheme != "https" or not host.endswith(".fbcdn.net"):
        raise RuntimeError("Unexpected image CDN URL")
    response = _cdn_session().get(uri, timeout=30)
    response.raise_for_status()
    data = response.content
    if not response.headers.get("Content-Type", "").startswith("image/"):
        raise RuntimeError("CDN response is not an image")
    extension = ".png" if data.startswith(b"\x89PNG") else ".webp" if data[:4] == b"RIFF" else ".jpg"
    target = destination / (photo["id"] + extension)
    temporary = target.with_suffix(target.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(target)
    story = photo.get("creation_story") or {}
    return {"id": photo["id"], "filename": target.name,
            "photo_url": "https://www.facebook.com/photo/?fbid=" + photo["id"],
            "width": image.get("width"), "height": image.get("height"),
            "timestamp": story.get("creation_time"), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def download_album(source, url, destination, workers=6, limit=0, doc_id=DEFAULT_DOC_ID):
    """Use sequential cursor queries and overlap them with concurrent CDN downloads."""
    if workers < 1 or limit < 0:
        raise ValueError("workers must be positive and limit must be nonnegative")
    started = time.monotonic()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    page_id = page_id_from_url(url)
    resolution_requests = 0
    if not page_id:
        response = source.request(url)
        resolution_requests = 1 + len(response.history)
        page_id = page_id_from_url(response.url)
    if not page_id:
        raise RuntimeError("Could not resolve the Facebook page's numeric ID")
    # Ask the album bootstrap for the same high-density image rendition as the
    # subsequent queries. This changes only this script's in-memory cookie jar.
    source.session.cookies.set("dpr", "4", domain=".facebook.com", path="/")
    album_response = source.request(
        f"https://www.facebook.com/media/set/?set=pb.{page_id}.-2207520000")
    html = album_response.text
    album = _album_object(html)
    bootstrap_page = parse_page(json.dumps({"data": {"node": album}}))
    lsd = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
    dtsg = re.search(r'"DTSGInit(?:ial)?Data",\[\],\{[^}]*?"token":"([^"]+)"', html)
    uid = next((c.value for c in source.session.cookies if c.name == "c_user"), "0")
    cursor = None
    seen_cursors, seen_ids, futures = set(), set(), {}
    rows, errors, request_count = [], [], 1 + len(album_response.history) + resolution_requests
    discovery_error = None
    exhausted = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        try:
            while True:
                variables = {"count": 100, "cursor": cursor, "id": album["id"],
                             "renderLocation": "permalink", "scale": 4,
                             "__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider": True}
                form = {"av": uid, "__user": uid, "__a": "1",
                        "fb_dtsg": dtsg.group(1) if dtsg else "",
                        "lsd": lsd.group(1) if lsd else "", "doc_id": doc_id,
                        "variables": json.dumps(variables), "fb_api_caller_class": "RelayModern",
                        "fb_api_req_friendly_name": "CometAlbumPhotoCollagePaginationQuery",
                        "server_timestamps": "true"}
                page = bootstrap_page
                bootstrap_page = None
                for attempt in range(0 if page is not None else 3):
                    response = source.session.post("https://www.facebook.com/api/graphql/", data=form, timeout=30)
                    request_count += 1 + len(response.history)
                    if response.status_code in (429, 502, 503, 504) and attempt < 2:
                        time.sleep(2 ** (attempt + 1))
                        continue
                    response.raise_for_status()
                    try:
                        page = parse_page(response.text)
                    except RuntimeError as exc:
                        if "1675004" in str(exc) and attempt < 2:
                            time.sleep(2 ** (attempt + 1))
                            continue
                        raise
                    break
                if page is None:
                    raise RuntimeError("Facebook did not provide a media page")
                validate_progress(page, seen_cursors)
                for photo in page["photos"]:
                    if photo["id"] not in seen_ids:
                        seen_ids.add(photo["id"])
                        futures[pool.submit(_download, photo, destination)] = photo["id"]
                    if limit and len(seen_ids) >= limit:
                        break
                if not page["has_next"]:
                    exhausted = True
                    break
                if limit and len(seen_ids) >= limit:
                    break
                cursor = page["cursor"]
                seen_cursors.add(cursor)
        except Exception as exc:
            discovery_error = safe_error(exc)
        discovery_seconds = time.monotonic() - started
        for future in concurrent.futures.as_completed(futures):
            try:
                rows.append(future.result())
            except Exception as exc:
                errors.append(safe_error(exc, futures[future]))
    rows.sort(key=lambda row: int(row["id"]), reverse=True)
    report = {"page_id": page_id, "business_name": business_name_from_album(album, page_id),
              "workers": workers, "photos": len(rows),
              "discovered_photos": len(seen_ids), "exhausted_collection": exhausted,
              "complete": discovery_error is None and not errors and (exhausted or bool(limit)),
              "seconds": round(time.monotonic() - started, 3),
              "discovery_seconds": round(discovery_seconds, 3),
              "facebook_requests": request_count,
              "total_bytes": sum(row["bytes"] for row in rows),
              "unique_hashes": len({row["sha256"] for row in rows}),
              "discovery_error": discovery_error, "download_errors": errors}
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    with (destination / "photo-index.csv").open("w", newline="") as out:
        fields = ["id", "filename", "photo_url", "width", "height", "timestamp", "bytes", "sha256"]
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return report, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="0 means the entire exposed photo collection")
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--browser", default="chrome", help="Browser with your Facebook login (default: chrome)")
    auth.add_argument("--cookies", type=Path, help="Your Netscape-format Facebook cookie file; no browser access")
    parser.add_argument("--profile", help="Optional browser profile name or path")
    parser.add_argument("--doc-id", default=DEFAULT_DOC_ID)
    args = parser.parse_args()
    if args.cookies and args.profile:
        parser.error("--profile applies to browser login, not --cookies")
    report, _ = download_album(session(args.browser, args.profile, args.cookies), args.url,
                               args.output, args.workers, args.limit, args.doc_id)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["complete"] else 1)


if __name__ == "__main__":
    main()
