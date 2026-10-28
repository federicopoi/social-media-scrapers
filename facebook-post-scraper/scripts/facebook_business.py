"""Two-request business lookup, with review pagination when Facebook exposes more."""

import concurrent.futures
import hashlib
import json
import re
import time
from urllib.parse import unquote, urlparse

from business_data import parse_profile, parse_reviews, payloads_from_html
from fb_photos import page_id_from_url, safe_error

REVIEW_DOC_ID = '28282377081445365'  # Verified against Facebook's JS on 2026-10-23.


def make_client(headers, cookies):
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    client = requests.Session()
    client.headers.update(headers)
    client.cookies.update(cookies)
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 502, 503, 504],
                  allowed_methods=['GET', 'POST'], respect_retry_after_header=True)
    client.mount('https://', HTTPAdapter(max_retries=retry))
    return client


def _profile_handle(url):
    parsed = urlparse(url or '')
    host = (parsed.hostname or '').lower()
    if host != 'facebook.com' and not host.endswith('.facebook.com'):
        return None
    parts = [unquote(part).casefold() for part in parsed.path.split('/') if part]
    reserved = {'profile.php', 'people', 'share', 'login', 'login.php', 'checkpoint',
                'me', 'home.php', 'groups', 'watch', 'photo', 'photo.php', 'media'}
    if not parts or parts[0].isdigit() or parts[0] in reserved:
        return None
    return parts[0]


def _page_id_from_profile_header(html, url):
    """Bind the target header's numeric ID to the requested public handle."""
    handle = _profile_handle(url)
    if not handle:
        return None
    identifiers = set()
    for record in payloads_from_html(html):
        if 'ProfileCometHeaderQuery' not in (record.get('query') or ''):
            continue
        user = (record.get('data') or {}).get('user') or {}
        owner = (user.get('profile_header_renderer') or {}).get('user') or {}
        if _profile_handle(owner.get('url')) != handle:
            continue
        identifier = str(user.get('id') or '')
        if not identifier.isdigit() or str(owner.get('id')) != identifier:
            raise RuntimeError('Target profile header identities disagree')
        identifiers.add(identifier)
    if len(identifiers) > 1:
        raise RuntimeError('Facebook returned conflicting target profile identities')
    return next(iter(identifiers), None)


def resolve_page(source, url):
    identifier = page_id_from_url(url)
    if not identifier:
        response = source.request(url)
        response.raise_for_status()
        identifier = page_id_from_url(response.url)
        if not identifier:
            identifier = _page_id_from_profile_header(response.text, url)
    if not identifier:
        raise RuntimeError('Could not safely resolve the target Facebook page ID')
    return identifier, 'https://www.facebook.com/profile.php?id=' + identifier


def fetch_profile_picture(url):
    """Fetch a target-header image without forwarding Facebook session cookies."""
    parsed = urlparse(url or '')
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not (host.endswith('.fbcdn.net') and host != 'fbcdn.net'):
        raise RuntimeError('Profile picture candidate did not use a safe Facebook CDN host')
    import requests
    from PIL import Image
    from io import BytesIO

    client = requests.Session()
    try:
        response = client.get(url, timeout=30, allow_redirects=False, stream=True)
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(64 * 1024):
            content.extend(chunk)
            if len(content) > 25 * 1024 * 1024:
                raise RuntimeError('Profile picture candidate exceeded the 25 MiB limit')
        raw = bytes(content)
    finally:
        client.close()
    try:
        with Image.open(BytesIO(raw)) as image:
            width, height = image.size
            image_format = image.format
            image.verify()
    except Exception as exc:
        raise RuntimeError('Profile picture candidate was not a valid image') from exc
    extensions = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}
    if image_format not in extensions:
        raise RuntimeError('Profile picture candidate used an unsupported image format')
    return {'bytes': raw, 'extension': extensions[image_format], 'width': width, 'height': height,
            'bytes_count': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def review_variables(page_id, cursor):
    flags = [
        'GHLShouldChangeAdIdFieldName', 'GHLShouldChangeSponsoredDataFieldName',
        'CometFeedStory_enable_reactor_facepile', 'CometFeedStory_enable_social_bubbles',
        'CometFeedStory_enable_post_permalink_white_space_click',
        'CometUFICommentActionLinksRewriteEnabled', 'CometUFICommentAvatarStickerAnimatedImage',
        'IsWorkUser', 'TestPilotShouldIncludeDemoAdUseCase',
        'FBReels_deprecate_short_form_video_context_gk', 'FBReels_enable_view_dubbed_audio_type_gk',
        'CometFeedShareMedia_shouldPrefetchShareImage', 'CometImmersivePhotoCanUserDisable3DMotion',
        'WorkCometIsEmployeeGKProvider', 'IsMergQAPolls', 'FBReelsMediaFooter_comet_enable_reels_ads_gk',
        'CometUFIReactionsEnableShortName', 'CometUFIShareActionMigration', 'CometUFISingleLineUFI',
        'relay_provider_comet_ufi_ssr_seo_defer', 'CometUFI_dedicated_comment_routable_dialog_gk',
        'FBReelsIFUTileContent_reelsIFUPlayOnHover', 'StoriesShouldEnablePhotosensitiveContentWarning',
        'ShouldEnableBakedInTextStories', 'StoriesShouldIncludeFbNotes']
    variables = {'__relay_internal__pv__' + flag + 'relayprovider': False for flag in flags}
    for flag in ('FBReels_deprecate_short_form_video_context_gk', 'FBReels_enable_view_dubbed_audio_type_gk',
                 'FBReelsMediaFooter_comet_enable_reels_ads_gk', 'CometUFIShareActionMigration',
                 'relay_provider_comet_ufi_ssr_seo_defer', 'FBReelsIFUTileContent_reelsIFUPlayOnHover',
                 'StoriesShouldIncludeFbNotes'):
        variables['__relay_internal__pv__' + flag + 'relayprovider'] = True
    variables.update({'__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider': 'ORIGINAL',
                      '__relay_internal__pv__GroupsCometGYSJFeedItemHeightrelayprovider': 206,
                      'count': 50, 'cursor': cursor, 'id': str(page_id), 'feedbackSource': 0,
                      'feedLocation': 'PAGE_SURFACE_RECOMMENDATIONS', 'focusCommentID': None,
                      'privacySelectorRenderLocation': 'COMET_STREAM', 'renderLocation': 'timeline',
                      'referringStoryRenderLocation': None, 'scale': 2, 'useDefaultActor': False})
    return variables


def fetch_review_page(client, page_id, html, cursor, doc_id=REVIEW_DOC_ID):
    lsd = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', html)
    dtsg = re.search(r'"DTSGInit(?:ial)?Data",\[\],\{[^}]*?"token":"([^"]+)"', html)
    uid = next((cookie.value for cookie in client.cookies if getattr(cookie, 'name', None) == 'c_user'), '0')
    form = {'av': uid, '__user': uid, '__a': '1',
            'lsd': lsd.group(1) if lsd else '', 'fb_dtsg': dtsg.group(1) if dtsg else '',
            'doc_id': doc_id, 'variables': json.dumps(review_variables(page_id, cursor)),
            'fb_api_caller_class': 'RelayModern', 'server_timestamps': 'true',
            'fb_api_req_friendly_name': 'ProfileCometReviewsFeedRefetchQuery'}
    response = client.post('https://www.facebook.com/api/graphql/', data=form, timeout=30)
    response.raise_for_status()
    raw = re.sub(r'^\s*for\s*\(;;\);', '', response.text)
    records = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError('Incomplete Facebook review JSON response') from exc
        if not isinstance(obj, dict):
            raise RuntimeError('Unexpected Facebook review response')
        records.append({'query': 'ProfileCometReviewsFeedRefetchQuery',
                        'data': obj.get('data') or {}, 'errors': obj.get('errors') or []})
    if not records:
        raise RuntimeError('Facebook did not return a review page')
    return records


def collect_business(page_id, headers, cookies, review_doc_id=REVIEW_DOC_ID):
    started = time.monotonic()
    canonical = 'https://www.facebook.com/profile.php?id=' + str(page_id)

    def profile_job():
        client = make_client(headers, cookies)
        try:
            response = client.get(canonical, timeout=30)
            response.raise_for_status()
            result = parse_profile(payloads_from_html(response.text), page_id)
            candidate_url = result.get('profile_picture_candidate_url')
            if candidate_url:
                try:
                    result['_profile_picture'] = fetch_profile_picture(candidate_url)
                except Exception as exc:
                    result['_profile_picture_error'] = safe_error(exc)
            return result
        except Exception as exc:
            result = parse_profile([{'data': {'user': {'id': str(page_id)}}}], page_id)
            result.update(status='unavailable', errors=[safe_error(exc)])
            result['field_status'] = {key: 'unavailable' for key in result['field_status']}
            return result
        finally:
            client.close()

    def reviews_job():
        client = make_client(headers, cookies)
        result = None
        try:
            response = client.get(canonical + '&sk=reviews', timeout=30)
            response.raise_for_status()
            records = payloads_from_html(response.text)
            result = parse_reviews(records, page_id)
            cursors, empty_pages = set(), 0
            while result['has_next_page'] is True and not result['errors']:
                cursor = result['end_cursor']
                if not cursor or cursor in cursors:
                    raise RuntimeError('Missing or repeated Facebook review pagination cursor')
                cursors.add(cursor)
                previous_count = result['retrieved_count']
                records.extend(fetch_review_page(client, page_id, response.text, cursor, review_doc_id))
                result = parse_reviews(records, page_id)
                empty_pages = empty_pages + 1 if result['retrieved_count'] == previous_count else 0
                if empty_pages >= 3:
                    raise RuntimeError('Review pagination repeatedly returned no additional readable reviews')
            return result
        except Exception as exc:
            if result is None:
                result = parse_reviews([{'data': {'user': {'id': str(page_id)}}}], page_id)
                result['status'] = 'unavailable'
            else:
                result['status'] = 'partial'
            result['errors'].append(safe_error(exc))
            return result
        finally:
            client.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        profile = pool.submit(profile_job)
        reviews = pool.submit(reviews_job)
        result = {'profile': profile.result(), 'reviews': reviews.result()}
    result['seconds'] = round(time.monotonic() - started, 3)
    return result
