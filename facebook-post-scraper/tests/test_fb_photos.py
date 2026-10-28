import unittest
from fb_photos import parse_page, validate_progress, page_id_from_url, safe_error, business_name_from_album


class PhotoPaginationTests(unittest.TestCase):
    def test_business_name_uses_the_target_page_and_not_an_unrelated_actor(self):
        album = {'title': {'text': "Example Outdoor Services 's Photos"}, 'media': {'edges': [
            {'node': {'creation_story': {'actors': [{'id': '999', 'name': 'Other author'}, {'id': '123', 'name': 'Example Outdoor Services '}]}}}
        ]}}
        self.assertEqual(business_name_from_album(album, '123'), 'Example Outdoor Services')
        self.assertEqual(business_name_from_album({'title': {'text': "Example Outdoor Services 's Photos"}}, '123'), 'Example Outdoor Services')
        self.assertIsNone(business_name_from_album({'title': {'text': 'Photos'}}, '123'))

    def test_reads_streamed_media_without_treating_trailer_as_empty_page(self):
        raw = 'for (;;);{"data":{"node":{"media":{"edges":[{"node":{"__typename":"Photo","id":"123","image":{"uri":"https://scontent.xx.fbcdn.net/a.jpg","width":2048,"height":1536}}},{"node":{"__typename":"Video","id":"999"}}],"page_info":{"has_next_page":true,"end_cursor":"next"}}}}}\n{"extensions":{"is_final":true}}'
        page = parse_page(raw)
        self.assertEqual([p['id'] for p in page['photos']], ['123'])
        self.assertEqual(page['cursor'], 'next')
        self.assertTrue(page['has_next'])

    def test_facebook_error_is_not_reported_as_successful_empty_collection(self):
        with self.assertRaisesRegex(RuntimeError, '1675004'):
            parse_page('{"errors":[{"message":"Rate limit exceeded","code":1675004,"severity":"CRITICAL"}]}')
        with self.assertRaisesRegex(RuntimeError, 'media'):
            parse_page('{"data":{"node":null}}')

    def test_checks_errors_after_a_valid_media_chunk_and_rejects_partial_data(self):
        valid = '{"data":{"node":{"media":{"edges":[],"page_info":{"has_next_page":false,"end_cursor":null}}}}}'
        with self.assertRaisesRegex(RuntimeError, '1675004'):
            parse_page(valid + '\n{"errors":[{"code":1675004,"message":"Rate limit exceeded","severity":"CRITICAL"}]}')
        with self.assertRaisesRegex(RuntimeError, 'edges'):
            parse_page('{"data":{"node":{"media":{"page_info":{"has_next_page":false}}}}}')
        with self.assertRaisesRegex(RuntimeError, 'JSON'):
            parse_page(valid + '\n{"errors":[')

    def test_error_reports_do_not_persist_signed_cdn_urls(self):
        error = RuntimeError('403 Client Error for url: https://scontent.xx.fbcdn.net/a.jpg?token=SECRET')
        rendered = safe_error(error, '123')
        self.assertNotIn('SECRET', rendered)
        self.assertNotIn('https://', rendered)
        self.assertIn('123', rendered)

    def test_missing_or_repeated_cursor_does_not_silently_truncate(self):
        with self.assertRaisesRegex(RuntimeError, 'cursor'):
            validate_progress({'photos': [], 'cursor': 'same', 'has_next': True}, {'same'})
        with self.assertRaisesRegex(RuntimeError, 'cursor'):
            validate_progress({'photos': [], 'cursor': None, 'has_next': True}, set())
        validate_progress({'photos': [], 'cursor': None, 'has_next': False}, set())

    def test_page_identification_does_not_confuse_share_ids_or_foreign_hosts(self):
        self.assertEqual(page_id_from_url('https://www.facebook.com/profile.php?id=123456789012346&sk=photos'), '123456789012346')
        self.assertEqual(page_id_from_url('https://www.facebook.com/people/Example-Outdoor-Services/123456789012346/'), '123456789012346')
        self.assertIsNone(page_id_from_url('https://www.facebook.com/share/123456789/'))
        with self.assertRaises(ValueError):
            page_id_from_url('https://example.com/123456789012346')


if __name__ == '__main__':
    unittest.main()
