import json
import tempfile
import unittest
from pathlib import Path

from business_data import parse_profile, parse_reviews, build_bundle, write_business_files, payloads_from_html


PAGE = '123'


def records(user, query='ProfileCometHeaderQuery'):
    return [{'query': query, 'data': {'user': user, 'viewer': {
        'name': 'Private viewer', 'email': 'SECRET@example.com'}}}]


def profile_fixture():
    def tile(kind, value):
        return {'directory_tile_item_renderer': {'tile_item': {
            'pressable_profile_field_type': kind,
            'item_subtitle': {'text': {'text': value}}}}}
    return records({'id': PAGE, 'profile_header_renderer': {'user': {
        'id': PAGE, 'name': 'Example Landscaping', 'header_top_row': {'profile_user': {
            'name': 'Example Landscaping',
            'profile_status': {'profile_status_text': {'text': 'Lawn care and tree pruning.\nServing Middle Tennessee.'}},
            'profile_intro_card': {'context_items': {'edges': [{'node': {
                'profile_field_type': 'category', 'short_title': {'text': 'Landscape Company'}}}]}}
        }}}}, 'profile_tile_sections': {'edges': [{'node': {
            'profile_tile_views': {'nodes': [{'view_style_renderer': {'view': {
                'title': {'text': 'Contact info'}, 'profile_tile_items': {'nodes': [
                    tile('BUSINESS_PHONE', '(931) 205-5925'),
                    tile('BUSINESS_EMAIL', 'office@example.com'),
                    tile('CONSOLIDATED_ADDRESS', '100 Main St, Example, TN'),
                    tile('RATINGS', '1 review'), tile(None, 'Always open')
                ]}}}}]}}}]}})


def review_fixture(has_next=False, query='ProfileCometReviewsFeedQuery'):
    story = {'post_id': '456', 'actors': [{'id': '555', 'name': 'A Customer'}],
             'comet_sections': {'message': {'story': {
                 'message': {'text': 'They did excellent work.'},
                 'url': 'https://www.facebook.com/customer/posts/456'}}}}
    result = records({'id': PAGE, 'ratings_list_feed_units': {'reviews_feed': {'edges': [
        {'node': {'comet_sections': {'content': {'story': story},
         'context_layout': {'story': {'comet_sections': {
             'metadata': [{'story': {'creation_time': 1780173226}}],
             'title': {'story': {'title': {'text': 'A Customer recommends Example Landscaping.'}}}
         }}}}}}
    ]}}}, query)
    result += [{'query': 'ProfileCometReviewsTabRootQuery', 'data': {
        'profile_rating_reviews': {'title': {'text': '1 review'}}}},
        {'query': query, 'data': {'page_info': {
            'has_next_page': has_next, 'end_cursor': 'next' if has_next else None}}}]
    return result


class BusinessDataTests(unittest.TestCase):
    def test_extracts_only_the_target_business_fields(self):
        profile = parse_profile(profile_fixture(), PAGE)
        data = profile['business']
        self.assertEqual(data['name'], 'Example Landscaping')
        self.assertEqual(data['phones'], ['(931) 205-5925'])
        self.assertEqual(data['emails'], ['office@example.com'])
        self.assertEqual(data['categories'], ['Landscape Company'])
        self.assertEqual(data['address'], '100 Main St, Example, TN')
        self.assertEqual(data['hours_text'], 'Always open')
        self.assertIn('tree pruning', data['description'])
        self.assertNotIn('SECRET', json.dumps(profile))

    def test_rejects_redirects_to_the_viewers_profile(self):
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            parse_profile(records({'id': '999', 'name': 'Private viewer'}), PAGE)
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            parse_reviews(records({'id': '999', 'name': 'Private viewer'}), PAGE)

    def test_profile_picture_candidate_comes_only_from_target_header_fields(self):
        user = {'id': PAGE, 'profile_header_renderer': {'user': {
            'id': PAGE, 'profilePicLarge': {'uri': 'https://scontent.xx.fbcdn.net/a.jpg?token=SECRET'},
            'header_top_row': {'profile_user': {'profilePhoto': {'image': {
                'uri': 'https://scontent.xx.fbcdn.net/b.jpg?token=SECRET'}}}}}},
            'viewer': {'profilePicLarge': {'uri': 'https://scontent.xx.fbcdn.net/viewer.jpg'}},
            'suggestions': [{'profilePicLarge': {'uri': 'https://scontent.xx.fbcdn.net/other.jpg'}}]}
        parsed = parse_profile(records(user), PAGE)
        self.assertEqual(parsed['profile_picture_candidate_url'], 'https://scontent.xx.fbcdn.net/b.jpg?token=SECRET')
        self.assertNotIn('SECRET', json.dumps(parsed['business']))
        self.assertNotIn('viewer.jpg', json.dumps(parsed))
        self.assertNotIn('other.jpg', json.dumps(parsed))

    def test_missing_target_picture_is_explicit(self):
        parsed = parse_profile(records({'id': PAGE}), PAGE)
        self.assertIsNone(parsed['profile_picture_candidate_url'])

    def test_absent_fields_are_not_guessed(self):
        profile = parse_profile(records({'id': PAGE}), PAGE)
        self.assertEqual(profile['business']['phones'], [])
        self.assertIsNone(profile['business']['address'])
        self.assertEqual(profile['field_status']['emails'], 'not_returned')

    def test_extracts_review_body_author_date_and_source_without_inventing_stars(self):
        reviews = parse_reviews(review_fixture(), PAGE)
        self.assertEqual(reviews['status'], 'complete')
        self.assertEqual(reviews['retrieved_count'], 1)
        review = reviews['items'][0]
        self.assertEqual(review['text'], 'They did excellent work.')
        self.assertEqual(review['author_name'], 'A Customer')
        self.assertEqual(review['published_at_utc'], '2026-05-30T20:33:46+00:00')
        self.assertEqual(review['url'], 'https://www.facebook.com/customer/posts/456')
        self.assertIsNone(review['rating'])

    def test_review_coverage_is_partial_when_more_pages_exist(self):
        reviews = parse_reviews(review_fixture(has_next=True), PAGE)
        self.assertEqual(reviews['status'], 'partial')
        self.assertTrue(reviews['has_next_page'])

    def test_timeline_cursor_is_not_mistaken_for_review_pagination(self):
        fixture = review_fixture()
        fixture[-1]['query'] = 'ProfileCometTimelineFeedQuery'
        reviews = parse_reviews(fixture, PAGE)
        self.assertEqual(reviews['status'], 'partial')
        self.assertIsNone(reviews['has_next_page'])

    def test_review_errors_remain_visible_when_some_reviews_were_returned(self):
        fixture = review_fixture()
        fixture.append({'query': 'ProfileCometReviewsFeedQuery', 'data': {},
                        'errors': [{'message': 'Blocked', 'code': 1675004, 'severity': 'CRITICAL'}]})
        reviews = parse_reviews(fixture, PAGE)
        self.assertEqual(reviews['status'], 'partial')
        self.assertTrue(reviews['errors'])

    def test_html_error_trailers_with_null_data_cannot_be_lost(self):
        source = {'require': [['RelayPrefetchedStreamCache', 'next', [], [
            'ProfileCometReviewsFeedQuery', {'__bbox': {'result': {
                'data': None, 'errors': [{'message': 'Blocked', 'code': 1675004, 'severity': 'CRITICAL'}]
            }}}]]]}
        extracted = payloads_from_html('<script type="application/json">' + json.dumps(source) + '</script>')
        self.assertTrue(extracted)
        parsed = parse_reviews(review_fixture() + extracted, PAGE)
        self.assertEqual(parsed['status'], 'partial')
        self.assertIn('1675004', str(parsed['errors']))

    def test_failed_profile_field_query_does_not_look_like_a_missing_published_field(self):
        fixture = records({'id': PAGE, 'profile_header_renderer': {'user': {'id': PAGE, 'name': 'Example Landscaping'}}})
        fixture.append({'query': 'ProfileCometProfileTileSectionsQuery', 'data': {},
                        'errors': [{'message': 'Blocked', 'code': 1675004, 'severity': 'CRITICAL'}]})
        parsed = parse_profile(fixture, PAGE)
        self.assertEqual(parsed['status'], 'partial')
        self.assertEqual(parsed['field_status']['name'], 'found')
        self.assertEqual(parsed['field_status']['emails'], 'unavailable')
        self.assertTrue(parsed['errors'])

    def test_bundle_is_machine_readable_and_has_relative_photo_paths_and_provenance(self):
        report = {'page_id': PAGE, 'business_name': 'Example Landscaping',
                  'photos': 1, 'complete': True, 'exhausted_collection': True}
        rows = [{'id': '789', 'filename': '789.jpg', 'photo_url': 'https://www.facebook.com/photo/?fbid=789',
                 'width': 2048, 'height': 1536, 'sha256': 'abc', 'bytes': 10}]
        bundle = build_bundle(parse_profile(profile_fixture(), PAGE), parse_reviews(review_fixture(), PAGE),
                              report, rows, 'https://www.facebook.com/share/example/')
        self.assertEqual(bundle['schema_version'], '1.0')
        self.assertEqual(bundle['photos']['assets'][0]['path'], '789.jpg')
        self.assertEqual(bundle['reviews']['items'][0]['author_name'], 'A Customer')
        self.assertIn('profile', bundle['sources'])
        self.assertEqual(bundle['collection']['status'], 'complete')
        self.assertEqual(bundle['brand_assets'], {'status': 'not_returned', 'profile_picture': None,
                                                  'notes': ['Facebook did not return a target profile picture candidate.']})
        with tempfile.TemporaryDirectory() as directory:
            write_business_files(Path(directory), bundle)
            self.assertEqual(json.loads((Path(directory)/'business.json').read_text()), bundle)
            markdown = (Path(directory)/'business.md').read_text()
            self.assertIn('office@example.com', markdown)
            self.assertIn('They did excellent work.', markdown)
            self.assertIn('A Customer', markdown)
            self.assertIn('business.json', markdown)


if __name__ == '__main__':
    unittest.main()
