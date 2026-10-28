# /// script
# requires-python = ">=3.11"
# dependencies = ["gallery-dl==1.32.17", "pillow>=11,<13"]
# ///

"""Facebook URL -> verified website-source ZIP with photos, business details, and reviews."""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

from fb_photos import DEFAULT_DOC_ID, download_album, safe_error, session
from business_data import build_bundle, write_business_files
from facebook_business import REVIEW_DOC_ID, collect_business, resolve_page


def slugify_name(name):
    plain = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", plain).strip("-").lower()[:80].rstrip("-") or "facebook-photos"


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(archive, expected):
    """Read every archived file and compare its SHA-256 with the verified inputs."""
    with zipfile.ZipFile(archive) as stored:
        names = stored.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise RuntimeError("ZIP contents do not match the downloaded files")
        for member, expected_hash in expected.items():
            digest = hashlib.sha256()
            with stored.open(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected_hash:
                raise RuntimeError(f"ZIP hash verification failed for {member}")


def archive_download(folder, downloads, report, rows, name=None):
    """Publish a verified ZIP before deleting this run's immediate working folder."""
    from PIL import Image
    folder, downloads = Path(folder), Path(downloads)
    if folder.is_symlink() or not folder.is_dir() or folder.parent.resolve() != downloads.resolve():
        raise RuntimeError("Working folder must be a new immediate child of the download directory")
    if not report.get("complete"):
        raise RuntimeError("Download is incomplete; raw folder preserved")
    if not rows and not report.get('exhausted_collection'):
        raise RuntimeError("Photo collection is empty but not verified exhausted; raw folder preserved")
    if len(rows) != report.get("photos") or len(rows) != report.get("discovered_photos"):
        raise RuntimeError("Downloaded photo counts are incomplete; raw folder preserved")

    sources = {}
    for row in rows:
        filename = row["filename"]
        if Path(filename).name != filename or filename in ("", ".", "..") or filename in sources:
            raise RuntimeError("Unexpected photo filename")
        photo = folder / filename
        if photo.is_symlink() or not photo.is_file():
            raise RuntimeError(f"Missing image file: {filename}")
        if photo.stat().st_size != row["bytes"] or _file_hash(photo) != row["sha256"]:
            raise RuntimeError(f"Image hash or size mismatch: {filename}")
        try:
            with Image.open(photo) as image:
                actual_size = image.size
                image.verify()
        except Exception as exc:
            raise RuntimeError(f"Invalid image: {filename}") from exc
        if actual_size != (row["width"], row["height"]):
            raise RuntimeError(f"Image dimensions do not match: {filename}")
        sources[filename] = row["sha256"]
    business_json = folder / 'business.json'
    if business_json.is_file() and not business_json.is_symlink():
        try:
            bundle = json.loads(business_json.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError('Invalid business.json metadata') from exc
        brand = bundle.get('brand_assets') or {}
        candidate = brand.get('profile_picture')
        if brand.get('status') == 'candidate' and candidate:
            filename = candidate.get('path')
            if (not isinstance(filename, str) or Path(filename).name != filename
                    or filename in ('', '.', '..') or filename in sources):
                raise RuntimeError('Unexpected brand image filename')
            photo = folder / filename
            if photo.is_symlink() or not photo.is_file():
                raise RuntimeError(f'Missing brand image file: {filename}')
            if photo.stat().st_size != candidate.get('bytes') or _file_hash(photo) != candidate.get('sha256'):
                raise RuntimeError(f'Brand image hash or size mismatch: {filename}')
            try:
                with Image.open(photo) as image:
                    actual_size = image.size
                    image.verify()
            except Exception as exc:
                raise RuntimeError(f'Invalid brand image: {filename}') from exc
            if actual_size != (candidate.get('width'), candidate.get('height')):
                raise RuntimeError(f'Brand image dimensions do not match: {filename}')
            sources[filename] = candidate['sha256']
    for filename in ("report.json", "photo-index.csv", "business.json", "business.md"):
        metadata = folder / filename
        if not metadata.is_file() or metadata.is_symlink():
            raise RuntimeError(f"Missing or unsafe metadata file: {filename}")
        sources[filename] = _file_hash(metadata)
    if set(child.name for child in folder.iterdir()) != set(sources):
        raise RuntimeError("Working folder has unexpected files; folder preserved")

    business = name or report.get("business_name") or f"facebook-{report.get('page_id') or 'photos'}"
    label = f"{slugify_name(business)}-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(4)}"
    final_zip = downloads / (label + ".zip")
    pending_zip = downloads / (label + ".zip.part")
    expected = {f"{label}/{filename}": digest for filename, digest in sources.items()}
    try:
        # Images are already compressed. Storing avoids spending CPU on tiny gains.
        with zipfile.ZipFile(pending_zip, "x", compression=zipfile.ZIP_STORED) as archive:
            for filename in sorted(sources):
                archive.write(folder / filename, arcname=f"{label}/{filename}")
        verify_archive(pending_zip, expected)
        # Hard-link publication is atomic and refuses to overwrite an existing ZIP.
        os.link(pending_zip, final_zip)
    except BaseException:
        pending_zip.unlink(missing_ok=True)
        raise
    pending_zip.unlink()

    cleanup_error = None
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        cleanup_error = safe_error(exc)
    removed = not folder.exists()
    return {"success": removed, "zip_path": str(final_zip), "business_name": str(business),
            "photos": len(rows), "zip_bytes": final_zip.stat().st_size,
            "archive_verified": True, "raw_folder_removed": removed,
            "cleanup_error": cleanup_error,
            "working_folder": None if removed else str(folder)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Facebook page, numeric profile, or shared page URL")
    parser.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    parser.add_argument("--name", help="Optional business name for the ZIP")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="0 means all available photos")
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--browser", default="chrome", help="Browser with your Facebook login (default: chrome)")
    auth.add_argument("--cookies", type=Path, help="Your Netscape-format Facebook cookie file; no browser access")
    parser.add_argument("--profile", help="Optional browser profile name or path")
    parser.add_argument("--doc-id", default=DEFAULT_DOC_ID)
    parser.add_argument("--review-doc-id", default=REVIEW_DOC_ID)
    args = parser.parse_args()
    if args.cookies and args.profile:
        parser.error("--profile applies to browser login, not --cookies")
    args.downloads = args.downloads.expanduser().resolve()
    args.downloads.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="facebook-photos-", dir=args.downloads))
    started = time.monotonic()
    print(f"Working folder: {workspace}", file=sys.stderr, flush=True)
    try:
        source = session(args.browser, args.profile, args.cookies)
        page_id, canonical_url = resolve_page(source, args.url)
        headers, cookies = dict(source.session.headers), source.session.cookies.copy()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            business_future = pool.submit(collect_business, page_id, headers, cookies, args.review_doc_id)
            report, rows = download_album(source, canonical_url, workspace,
                                           args.workers, args.limit, args.doc_id)
            metadata = business_future.result()
        report['requested_limit'] = args.limit
        report['business_name'] = metadata['profile']['business'].get('name') or report.get('business_name')
        (workspace / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        candidate = metadata['profile'].pop('_profile_picture', None)
        candidate_error = metadata['profile'].pop('_profile_picture_error', None)
        if candidate:
            filename = 'facebook-profile-picture.' + candidate['extension']
            (workspace / filename).write_bytes(candidate['bytes'])
            metadata['profile']['brand_assets'] = {
                'status': 'candidate',
                'profile_picture': {'path': filename,
                                    'photo_url': 'https://www.facebook.com/profile.php?id=' + str(page_id),
                                    'width': candidate['width'], 'height': candidate['height'],
                                    'bytes': candidate['bytes_count'], 'sha256': candidate['sha256'],
                                    'source_kind': 'facebook_profile_picture', 'logo_verified': False},
                'notes': ['Candidate came from the target Facebook profile header; logo identity has not been verified.']}
        elif metadata['profile'].get('profile_picture_candidate_url'):
            metadata['profile']['brand_assets'] = {
                'status': 'unavailable', 'profile_picture': None,
                'notes': ['Facebook returned a profile picture candidate, but it could not be downloaded or validated.'
                          + ((' ' + candidate_error) if candidate_error else '')]}
        else:
            metadata['profile']['brand_assets'] = {
                'status': 'not_returned', 'profile_picture': None,
                'notes': ['Facebook did not return a target profile picture candidate.']}
        bundle = build_bundle(metadata['profile'], metadata['reviews'], report, rows, args.url)
        write_business_files(workspace, bundle)
        result = archive_download(workspace, args.downloads, report, rows, args.name)
        result.update(seconds=round(time.monotonic() - started, 3),
                      exhausted_collection=report["exhausted_collection"],
                      requested_limit=args.limit, business_brief='business.md',
                      structured_data='business.json', reviews=metadata['reviews']['retrieved_count'],
                      business_data_status=metadata['profile']['status'],
                      review_status=metadata['reviews']['status'],
                      collection_status=bundle['collection']['status'],
                      warnings=bundle['collection']['warnings'])
    except Exception as exc:
        result = {"success": False, "error": safe_error(exc),
                  "working_folder": str(workspace), "raw_folder_removed": False}
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
