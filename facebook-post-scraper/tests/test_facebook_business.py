import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from facebook_business import collect_business, fetch_profile_picture, resolve_page
from test_business_data import PAGE, profile_fixture, review_fixture


def html(records):
    calls = [['RelayPrefetchedStreamCache', 'next', [], [record['query'], {
        '__bbox': {'result': {'data': record['data'], 'errors': record.get('errors', [])}}}]] for record in records]
    return '<script type="application/json">' + json.dumps({'require': calls}) + '</script>'


def response(body):
    return SimpleNamespace(text=body, status_code=200, headers={}, history=[], raise_for_status=lambda: None)


class FakeClient:
    def __init__(self, first, second=None, fail_reviews=False):
        self.first, self.second, self.fail_reviews = first, second, fail_reviews
        self.cookies = []
        self.posts = 0

    def get(self, url, **kwargs):
        if '&sk=reviews' in url:
            if self.fail_reviews:
                raise RuntimeError('Blocked https://facebook.com/private?token=SECRET')
            return response(html(self.first))
        return response(html(profile_fixture()))

    def post(self, url, **kwargs):
        self.posts += 1
        return response(self.second)

    def close(self):
        pass


class PageResolutionTests(unittest.TestCase):
    url = 'https://www.facebook.com/ExampleLandscaping'

    def header(self, identifier='123456789012345', url=None, owner_id=None):
        return {'query': 'adp_ProfileCometHeaderQueryRelayPreloader_example', 'data': {
            'user': {'id': identifier, 'profile_header_renderer': {'user': {
                'id': owner_id or identifier, 'url': url or self.url,
                'name': 'Example Landscaping'}}},
            'viewer': {'actor': {'id': '999', 'url': 'https://www.facebook.com/Viewer'}}}}

    def source(self, records, final_url=None):
        result = response(html(records))
        result.url = final_url or self.url
        return SimpleNamespace(request=lambda url: result)

    def test_resolves_vanity_url_from_matching_profile_header_not_viewer(self):
        unrelated = {'query': 'SettingsQuery', 'data': {'userID': '999',
                    'user': {'id': '999'}, 'viewer': {'actor': {'id': '999'}}}}
        source = self.source([unrelated, self.header(url='https://www.facebook.com/Other'), self.header()])
        self.assertEqual(resolve_page(source, self.url),
                         ('123456789012345', 'https://www.facebook.com/profile.php?id=123456789012345'))

    def test_vanity_matching_ignores_case_host_alias_trailing_slash_and_tab(self):
        source = self.source([self.header(url='https://facebook.com/examplelandscaping/')])
        self.assertEqual(resolve_page(source, self.url + '/photos?locale=en_US')[0], '123456789012345')

    def test_refuses_unrelated_header_or_viewer_only_response(self):
        for records in ([self.header(url='https://www.facebook.com/Other')],
                        [{'query': 'SettingsQuery', 'data': {'userID': '999', 'user': {'id': '999'}}}]):
            with self.subTest(records=records), self.assertRaises(RuntimeError):
                resolve_page(self.source(records), self.url)

    def test_refuses_disagreeing_target_identities(self):
        for records in ([self.header(), self.header(identifier='222')],
                        [self.header(owner_id='999')]):
            with self.subTest(records=records), self.assertRaises(RuntimeError):
                resolve_page(self.source(records), self.url)

    def test_foreign_profile_url_cannot_validate_target_identity(self):
        with self.assertRaises(RuntimeError):
            resolve_page(self.source([self.header(url='https://example.com/ExampleLandscaping')]), self.url)

    def test_numeric_and_redirect_links_keep_resolving(self):
        expected = ('123456789012346', 'https://www.facebook.com/profile.php?id=123456789012346')
        self.assertEqual(resolve_page(SimpleNamespace(), expected[1]), expected)
        source = self.source([], final_url=expected[1])
        self.assertEqual(resolve_page(source, 'https://www.facebook.com/share/EXAMPLESHARE/'), expected)


class MetadataNetworkTests(unittest.TestCase):
    def test_fetches_profile_and_reviews_without_changing_their_data(self):
        with patch('facebook_business.make_client', side_effect=lambda *a: FakeClient(review_fixture())):
            result = collect_business(PAGE, {}, [])
        self.assertEqual(result['profile']['business']['emails'], ['office@example.com'])
        self.assertEqual(result['reviews']['status'], 'complete')
        self.assertEqual(result['reviews']['retrieved_count'], 1)

    def test_target_picture_download_is_cookie_free_and_preserves_decoded_bytes(self):
        from io import BytesIO
        from PIL import Image
        import hashlib

        stream = BytesIO()
        Image.new('RGB', (11, 9), (10, 20, 30)).save(stream, format='PNG')
        raw = stream.getvalue()

        class CDNResponse:
            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield raw

        class CDNClient:
            def __init__(self):
                self.cookies = {}

            def get(self, url, **kwargs):
                self.url, self.kwargs = url, kwargs
                return CDNResponse()

            def close(self):
                pass

        client = CDNClient()
        with patch.dict('sys.modules', {'requests': SimpleNamespace(Session=lambda: client)}):
            result = fetch_profile_picture('https://scontent.xx.fbcdn.net/photo')
        self.assertEqual(client.cookies, {})
        self.assertFalse(client.kwargs.get('allow_redirects'))
        self.assertEqual(result['bytes'], raw)
        self.assertEqual((result['width'], result['height']), (11, 9))
        self.assertEqual(result['extension'], 'png')
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())

    def test_picture_download_failure_keeps_profile_result_available(self):
        with patch('facebook_business.make_client', side_effect=lambda *a: FakeClient(review_fixture())), \
                patch('facebook_business.parse_profile', return_value={
                    'status': 'retrieved', 'business': {'name': 'Example Landscaping'},
                    'field_status': {'name': 'found'}, 'errors': [],
                    'profile_picture_candidate_url': 'https://scontent.xx.fbcdn.net/profile.jpg?token=PRIVATE'}), \
                patch('facebook_business.fetch_profile_picture', side_effect=RuntimeError('CDN unavailable')):
            result = collect_business(PAGE, {}, [])
        self.assertEqual(result['profile']['status'], 'retrieved')
        self.assertEqual(result['profile']['business']['name'], 'Example Landscaping')
        self.assertIn('CDN unavailable', result['profile']['_profile_picture_error'])

    def test_follows_review_pagination_and_merges_all_pages(self):
        first = review_fixture(True)
        first[1]['data']['profile_rating_reviews']['title']['text'] = '2 reviews'
        second_user = copy.deepcopy(review_fixture()[0]['data']['user'])
        node = second_user['ratings_list_feed_units']['reviews_feed']['edges'][0]['node']
        node['comet_sections']['content']['story']['post_id'] = '457'
        node['comet_sections']['content']['story']['comet_sections']['message']['story']['message']['text'] = 'Second review.'
        second_user['ratings_list_feed_units']['reviews_feed']['page_info'] = {'has_next_page': False, 'end_cursor': None}
        second = json.dumps({'data': {'node': second_user}})
        with patch('facebook_business.make_client', side_effect=lambda *a: FakeClient(first, second)):
            result = collect_business(PAGE, {}, [])
        self.assertEqual(result['reviews']['status'], 'complete')
        self.assertEqual(result['reviews']['retrieved_count'], 2)

    def test_repeated_review_cursor_is_partial_instead_of_infinite_or_complete(self):
        first = review_fixture(True)
        user = copy.deepcopy(first[0]['data']['user'])
        user['ratings_list_feed_units']['reviews_feed']['page_info'] = {'has_next_page': True, 'end_cursor': 'next'}
        with patch('facebook_business.make_client', side_effect=lambda *a: FakeClient(first, json.dumps({'data': {'node': user}}))):
            result = collect_business(PAGE, {}, [])
        self.assertEqual(result['reviews']['status'], 'partial')
        self.assertTrue(result['reviews']['errors'])

    def test_review_failure_preserves_business_fields_and_redacts_urls(self):
        with patch('facebook_business.make_client', side_effect=lambda *a: FakeClient([], fail_reviews=True)):
            result = collect_business(PAGE, {}, [])
        self.assertEqual(result['profile']['status'], 'retrieved')
        self.assertEqual(result['reviews']['status'], 'unavailable')
        self.assertNotIn('SECRET', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
