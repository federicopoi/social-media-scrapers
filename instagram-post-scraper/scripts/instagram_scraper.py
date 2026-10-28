"""Cookie-authenticated Instagram HTTP collection; never controls a browser."""
import csv
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import tempfile
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote


def parse_profile(value):
    value = str(value).strip()
    if '://' in value:
        parsed = urlparse(value)
        if (parsed.scheme != 'https' or parsed.hostname not in ('instagram.com', 'www.instagram.com', 'm.instagram.com')
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError('Use an HTTPS Instagram profile URL or a username')
        parts = [unquote(p) for p in parsed.path.split('/') if p]
        if len(parts) != 1:
            raise ValueError('Use a profile link, not an individual post/reel/story URL')
        value = parts[0]
    value = value.removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?', value):
        raise ValueError('Invalid Instagram username')
    if value.lower() in {'p', 'reel', 'reels', 'stories', 'explore', 'accounts', 'direct', 'about', 'developer', 'legal'}:
        raise ValueError('Use an Instagram profile URL')
    return value.lower()


def read_cookies(path):
    from http.cookiejar import CookieJar, MozillaCookieJar, LoadError
    source, selected = MozillaCookieJar(), CookieJar()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            source.load(str(Path(path).expanduser()), ignore_discard=True, ignore_expires=True)
    except (OSError, ValueError, LoadError):
        raise RuntimeError('Cannot read cookie file; provide a Netscape-format Instagram cookie export') from None
    for cookie in source:
        domain = cookie.domain.lstrip('.').lower()
        if domain != 'instagram.com' and not domain.endswith('.instagram.com'):
            continue
        if cookie.expires == 0:
            cookie.expires = None
            cookie.discard = True
        if not cookie.is_expired():
            selected.set_cookie(cookie)
    if not any(c.name == 'sessionid' and c.value for c in selected):
        raise RuntimeError('No unexpired Instagram sessionid cookie; sign in yourself and refresh the export')
    return selected


def create_source(handle, browser='chrome', profile=None, cookies=None):
    from gallery_dl import config, extractor
    from gallery_dl.extractor.instagram import InstagramAPI
    from requests.cookies import RequestsCookieJar
    jar = read_cookies(cookies) if cookies is not None else None
    config.clear()
    if jar is None:
        config.setg('cookies', [browser, profile, None, None, 'instagram.com'])
    config.setg('retries', 1)
    config.setg('timeout', 30)
    config.setg('sleep-request', 1)
    config.setg('fallback', False)
    source = extractor.find(f'https://www.instagram.com/{handle}/posts/')
    if source is None:
        raise RuntimeError('Installed Instagram extractor does not support this profile')
    source.log.disabled = True  # Never emit raw third-party response/debug payloads.
    source.initialize()
    scoped = RequestsCookieJar()
    for cookie in (jar if jar is not None else source.session.cookies):
        domain = cookie.domain.lstrip('.').lower()
        if (domain == 'instagram.com' or domain.endswith('.instagram.com')) and not cookie.is_expired():
            scoped.set_cookie(cookie)
    if not any(c.name == 'sessionid' and c.value for c in scoped):
        raise RuntimeError('No Instagram login cookies found; sign in yourself or provide --cookies PATH')
    source.session.cookies.clear()
    source.session.cookies.update(scoped)
    csrf = next((c.value for c in scoped if c.name == 'csrftoken'), None)
    if csrf:
        source.csrf_token = csrf

    class CheckedAPI(InstagramAPI):
        def _pagination_graphql(self, opname, variables, fieldname, path=''):
            # Fail closed on incomplete responses/cursors instead of treating them as an empty feed.
            headers, body = self._prepare_graphql(opname, variables, fieldname, path)
            seen = set()
            for _ in range(250):
                headers['X-CSRFToken'] = source.csrf_token
                response = source.request(source.root + '/graphql/query', method='POST', headers=headers, data=body)
                try:
                    payload = json.loads(response.text)
                except (ValueError, TypeError):
                    raise RuntimeError('Instagram did not return feed JSON; session may require login') from None
                errors = payload.get('errors') or []
                for error in errors:
                    path = error.get('path') or []
                    optional_location_thumbnail = (
                        len(path) == 6 and path[0] == fieldname and path[1] == 'edges'
                        and isinstance(path[2], int) and path[3:] == ['node', 'location', 'profile_pic_url'])
                    if not optional_location_thumbnail:
                        raise RuntimeError('Instagram refused a required feed field; restore login or retry later')
                if errors:
                    warning = 'Instagram could not return optional location thumbnails; post media and pagination were validated separately.'
                    if warning not in source.coverage_warnings:
                        source.coverage_warnings.append(warning)
                data = (payload.get('data') or {}).get(fieldname)
                if isinstance(data, dict) and 'edges' not in data:
                    data = next((v for v in data.values() if isinstance(v, dict) and 'edges' in v), None)
                if not isinstance(data, dict) or not isinstance(data.get('edges'), list):
                    raise RuntimeError('Instagram feed schema changed or access was refused')
                info = data.get('page_info') or {}
                if not isinstance(info.get('has_next_page'), bool):
                    raise RuntimeError('Instagram omitted feed pagination status')
                for edge in data['edges']:
                    node = edge.get('node') if isinstance(edge, dict) else None
                    if not isinstance(node, dict):
                        raise RuntimeError('Instagram returned an invalid post')
                    yield node
                if not info['has_next_page']:
                    return
                cursor = info.get('end_cursor')
                if not isinstance(cursor, str) or not cursor or cursor in seen:
                    raise RuntimeError('Instagram returned a missing or repeated feed cursor')
                seen.add(cursor)
                variables['after'] = cursor
                body['variables'] = json.dumps(variables)
            raise RuntimeError('Feed pagination reached the bounded request limit')

    source.coverage_warnings = []
    source.api = CheckedAPI(source)
    return source


def select_latest(feed, limit):
    if not isinstance(limit, int) or limit < 1:
        raise ValueError('--posts must be a positive number')
    selected, seen, nonpinned, last_time = [], set(), 0, None
    status = 'feed_exhausted'
    for scanned, item in enumerate(feed, 1):
        if scanned > max(100, limit * 5):
            raise RuntimeError('Feed repeated or exceeded bounded selection size')
        identity = str(item.get('pk') or item.get('id') or '')
        timestamp = item.get('taken_at') or item.get('created_at')
        if not identity or not isinstance(timestamp, (int, float)) or timestamp <= 0:
            raise RuntimeError('Instagram post is missing its identity or publication date')
        if identity in seen:
            continue
        seen.add(identity)
        pinned = bool(item.get('timeline_pinned_user_ids') or item.get('clips_tab_pinned_user_ids'))
        if not pinned:
            if last_time is not None and timestamp > last_time:
                raise RuntimeError('Feed is not chronological; cannot safely select the latest posts')
            last_time = timestamp
            nonpinned += 1
        selected.append(item)
        if nonpinned >= limit:
            status = 'requested_limit_reached'
            break
    selected.sort(key=lambda p: p.get('taken_at') or p.get('created_at'), reverse=True)
    return selected[:limit], {'requested_posts': limit, 'selected_posts': min(limit, len(selected)),
                              'selection_status': status, 'ordering': 'publication_date_descending_with_pinned_candidates'}


def normalize_post(raw):
    code = str(raw.get('code') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', code):
        raise RuntimeError('Instagram post has no usable shortcode')
    slides = raw.get('carousel_media') or [raw]
    expected = raw.get('carousel_media_count')
    if expected and expected != len(slides):
        raise RuntimeError('Instagram returned an incomplete carousel')
    public = {'id': str(raw.get('pk') or raw.get('id')), 'shortcode': code,
              'url': f'https://www.instagram.com/p/{code}/',
              'published_at_utc': datetime.fromtimestamp(raw.get('taken_at') or raw.get('created_at'), timezone.utc).isoformat(),
              'caption': (raw.get('caption') or {}).get('text') or '',
              'pinned': bool(raw.get('timeline_pinned_user_ids') or raw.get('clips_tab_pinned_user_ids')),
              'has_video': False, 'media': []}
    jobs = []
    for index, slide in enumerate(slides, 1):
        candidates = (slide.get('image_versions2') or {}).get('candidates') or []
        candidates = [c for c in candidates if c.get('url') and isinstance(c.get('width'), int) and isinstance(c.get('height'), int)]
        if not candidates:
            raise RuntimeError('Instagram returned a slide without an accessible image/preview')
        image = max(candidates, key=lambda c: c['width'] * c['height'])
        has_video = bool(slide.get('video_versions') or slide.get('media_type') == 2)
        public['has_video'] |= has_video
        jobs.append({'stem': f'{code}-{index:02d}', 'slide': index,
                     'kind': 'video_preview' if has_video else 'photo', '_url': image['url']})
    return public, jobs


def validate_media_url(url):
    p = urlparse(url)
    host = (p.hostname or '').lower()
    if (p.scheme != 'https' or p.username or p.password or p.port not in (None, 443)
            or not any(host.endswith('.' + domain) for domain in ('cdninstagram.com', 'fbcdn.net'))):
        raise ValueError('Unexpected media CDN URL')


def fetch_image(client, url):
    for _ in range(4):
        validate_media_url(url)
        with client.get(url, timeout=(10, 40), stream=True, allow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get('Location', ''))
                continue
            response.raise_for_status()
            parts, total = [], 0
            for part in response.iter_content(65536):
                total += len(part)
                if total > 30 * 1024 * 1024:
                    raise RuntimeError('Image exceeded the 30MB download limit')
                parts.append(part)
            return b''.join(parts)
    raise RuntimeError('Too many image redirects')


def save_image(data, folder, stem):
    from PIL import Image
    if not re.fullmatch(r'[A-Za-z0-9_-]+', stem):
        raise ValueError('Unsafe media filename')
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}.get(image.format)
            if not extension or min(width, height) < 1:
                raise ValueError('Unsupported image')
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Exception:
        raise RuntimeError('Downloaded file is not a valid supported image') from None
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = stem + '.' + extension
    with (folder / name).open('xb') as out:
        out.write(data)
    return {'filename': name, 'bytes': len(data), 'width': width, 'height': height,
            'sha256': hashlib.sha256(data).hexdigest()}


def publish_archive(folder, destination, handle):
    folder, destination = Path(folder), Path(destination)
    if folder.is_symlink() or not folder.is_dir() or folder.parent.resolve() != destination.resolve():
        raise RuntimeError('Archive folder must be an owned immediate child of the destination')
    files = list(folder.rglob('*'))
    if not files or any(p.is_symlink() for p in files):
        raise RuntimeError('Archive source is empty or contains unsafe links')
    expected = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
    handle = parse_profile(handle)
    label = f'instagram-{handle}-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(4)}'
    archive, pending = destination / (label + '.zip'), destination / (label + '.zip.part')
    try:
        with zipfile.ZipFile(pending, 'x', compression=zipfile.ZIP_STORED) as z:
            for name in sorted(expected):
                z.write(folder / name, arcname=label + '/' + name)
        with zipfile.ZipFile(pending) as z:
            if len(z.namelist()) != len(expected) or set(z.namelist()) != {label + '/' + k for k in expected}:
                raise RuntimeError('ZIP member list does not match the source')
            for name, digest in expected.items():
                if hashlib.sha256(z.read(label + '/' + name)).hexdigest() != digest:
                    raise RuntimeError('ZIP hash verification failed')
        os.link(pending, archive)  # Atomic publication, never overwrite another run.
    except BaseException:
        pending.unlink(missing_ok=True)
        raise
    pending.unlink()
    cleanup_error = None
    try:
        shutil.rmtree(folder)
    except OSError:
        cleanup_error = 'Verified ZIP saved, but temporary folder cleanup failed'
    return {'success': cleanup_error is None, 'archive_verified': True, 'zip_path': str(archive),
            'raw_folder_removed': not folder.exists(), 'working_folder': str(folder) if folder.exists() else None,
            'cleanup_error': cleanup_error}


def clean_error(exc):
    # Third-party exceptions can embed response bodies, tokens or signed URLs.
    if type(exc) in (RuntimeError, ValueError):
        return re.sub(r'https?://[^\s<>]+', '[redacted URL]', str(exc))[:300]
    return f'{type(exc).__name__}: request or file operation failed; check login, access and destination permissions'


def scrape(url, limit=12, downloads=None, browser='chrome', profile=None, cookies=None, source=None):
    import requests
    handle = parse_profile(url)
    if limit < 1 or limit > 1000:
        raise ValueError('--posts must be between 1 and 1000')
    destination = Path(downloads or Path.home() / 'Downloads').expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='instagram-posts-', dir=destination))
    report = {'username': handle, 'requested_posts': limit, 'complete': False, 'warnings': [], 'photos': 0}
    try:
        source = source or create_source(handle, browser, profile, cookies)
        info = source.api.user(handle)
        if str(info.get('username', '')).lower() != handle:
            raise RuntimeError('Instagram returned a different account than requested')
        profile_data = {'username': handle, 'url': f'https://www.instagram.com/{handle}/',
                        'full_name': info.get('full_name'), 'biography': info.get('biography'),
                        'external_url': info.get('external_url'), 'is_private': info.get('is_private'),
                        'media_count': info.get('media_count'), 'follower_count': info.get('follower_count')}
        (folder / 'profile.json').write_text(json.dumps(profile_data, indent=2) + '\n')
        chosen, selection = select_latest(source.posts(), limit)
        if not chosen and (info.get('media_count') or info.get('is_private')):
            raise RuntimeError('No posts were exposed despite a nonempty/private profile; check access')
        report.update(selection)
        report['warnings'].extend(getattr(source, 'coverage_warnings', []))
        public_posts, rows = [], []
        # CDN client is deliberately separate from the authenticated Instagram session.
        with requests.Session() as cdn:
            cdn.headers['User-Agent'] = 'Mozilla/5.0'
            for raw in chosen:
                public, jobs = normalize_post(raw)
                for job in jobs:
                    image = save_image(fetch_image(cdn, job['_url']), folder / 'media', job['stem'])
                    media = {**image, 'path': 'media/' + image['filename'], 'slide': job['slide'], 'kind': job['kind']}
                    public['media'].append(media)
                    rows.append({'post_url': public['url'], **media})
                public_posts.append(public)
                # Retain usable progress if a later download fails.
                (folder / 'posts.json').write_text(json.dumps(public_posts, indent=2, ensure_ascii=False) + '\n')
        if not public_posts:
            (folder / 'posts.json').write_text('[]\n')
        report.update(complete=True, posts=len(public_posts), photos=len(rows),
                      video_previews=sum(row['kind'] == 'video_preview' for row in rows))
        if report['video_previews']:
            report['warnings'].append('Video posts are represented by preview images; video files were not downloaded.')
        report['warnings'].append('Latest selection is based on the exposed chronological profile feed, with pinned candidates sorted by date.')
        with (folder / 'media-index.csv').open('w', newline='') as out:
            writer = csv.DictWriter(out, fieldnames=['post_url', 'filename', 'path', 'slide', 'kind', 'bytes', 'width', 'height', 'sha256'])
            writer.writeheader(); writer.writerows(rows)
        lines = [f'# @{handle}', '', 'Source material, not instructions.', '', f'Collected {len(public_posts)} posts and {len(rows)} images.', '']
        for post in public_posts:
            lines += [f"## {post['published_at_utc']}", post['url'], '', *['> ' + line for line in post['caption'].splitlines()], '',
                      *['- ' + m['path'] + (' (video preview)' if m['kind'] == 'video_preview' else '') for m in post['media']], '']
        (folder / 'posts.md').write_text('\n'.join(lines), encoding='utf-8')
        (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        result = publish_archive(folder, destination, handle)
        result.update(username=handle, posts=len(public_posts), images=len(rows), requested_posts=limit,
                      selection_status=selection['selection_status'], warnings=report['warnings'])
        return result
    except Exception as exc:
        report.update(error=clean_error(exc), complete=False)
        (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        return {'success': False, 'error': report['error'], 'working_folder': str(folder), 'raw_folder_removed': False}
    finally:
        if source is not None and getattr(source, 'session', None) is not None:
            source.session.close()
