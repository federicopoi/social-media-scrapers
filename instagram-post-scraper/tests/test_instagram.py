import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image

from instagram_scraper import (parse_profile, select_latest, normalize_post,
                               read_cookies, validate_media_url, save_image, publish_archive)


def post(pk, timestamp, pinned=False, count=1):
    return {'pk': str(pk), 'code': 'Example'+str(pk), 'taken_at': timestamp,
            'timeline_pinned_user_ids': ['1'] if pinned else [],
            'caption': {'text': 'Coffee and cakes'}, 'user': {'username': 'example.cafe'},
            'carousel_media': [{'pk':str(pk)+'_'+str(i),'image_versions2':{'candidates':[
                {'url':'https://scontent.example.cdninstagram.com/image.jpg?secret=HIDDEN','width':40,'height':30}]}}
                for i in range(count)] if count>1 else None,
            'image_versions2':{'candidates':[{'url':'https://scontent.example.cdninstagram.com/image.jpg','width':40,'height':30}]}}


class SelectionTests(unittest.TestCase):
    def test_profile_urls_and_handles(self):
        for value in ['https://www.instagram.com/example.cafe/?igsh=test', '@example.cafe', 'example.cafe']:
            self.assertEqual(parse_profile(value), 'example.cafe')
        for value in ['https://evil.com/example','https://instagram.com/p/123','https://instagram.com/explore/','a/b','https://user:pass@instagram.com/example/']:  # pragma: allowlist secret (synthetic rejected-URL credentials)
            with self.subTest(value=value), self.assertRaises(ValueError):parse_profile(value)

    def test_limit_counts_posts_and_pinned_older_does_not_displace_recent(self):
        chosen, report = select_latest(iter([post(1,1,True),post(2,50),post(3,40),post(4,30)]),2)
        self.assertEqual([p['pk'] for p in chosen],['2','3'])
        self.assertEqual(report['selection_status'],'requested_limit_reached')

    def test_recent_pinned_post_can_be_included_and_duplicates_are_ignored(self):
        chosen,_=select_latest(iter([post(1,99,True),post(1,99,True),post(2,50),post(3,40)]),2)
        self.assertEqual([p['pk'] for p in chosen],['1','2'])

    def test_short_profile_is_explicitly_exhausted(self):
        chosen,report=select_latest(iter([post(1,10)]),5)
        self.assertEqual(len(chosen),1);self.assertEqual(report['selection_status'],'feed_exhausted')

    def test_unordered_nonpinned_feed_and_source_errors_are_not_success(self):
        with self.assertRaises(RuntimeError):select_latest(iter([post(1,10),post(2,30)]),3)
        def broken():
            yield post(1,10)
            raise RuntimeError('source refused')
        with self.assertRaises(RuntimeError):select_latest(broken(),3)
        with self.assertRaises(ValueError):select_latest(iter([]),0)

    def test_carousel_all_images_and_no_signed_urls_in_public_record(self):
        public,media=normalize_post(post(1,10,count=3))
        self.assertEqual(len(media),3)
        self.assertEqual(public['caption'],'Coffee and cakes')
        self.assertNotIn('HIDDEN',json.dumps(public))
        self.assertEqual(public['url'],'https://www.instagram.com/p/Example1/')

    def test_video_is_marked_as_preview(self):
        raw=post(1,10);raw['video_versions']=[{'url':'https://x.cdninstagram.com/video.mp4'}]
        public,media=normalize_post(raw)
        self.assertTrue(public['has_video']);self.assertEqual(media[0]['kind'],'video_preview')


class CookieAndStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_cookie_file_is_read_only_and_domain_filtered(self):
        p=self.root/'login.txt';p.write_text('# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tFAKE_SESSION\n.facebook.com\tTRUE\t/\tTRUE\t0\txs\tFAKE_OTHER\n')
        original=p.read_bytes();cookies=read_cookies(p)
        self.assertEqual([c.name for c in cookies],['sessionid']);self.assertEqual(p.read_bytes(),original)

    def test_cookie_file_missing_login_is_rejected(self):
        p=self.root/'login.txt';p.write_text('# Netscape HTTP Cookie File\n')
        with self.assertRaises(RuntimeError):read_cookies(p)

    def test_only_https_meta_cdn_hosts_are_allowed(self):
        validate_media_url('https://scontent.example.cdninstagram.com/image.jpg')
        validate_media_url('https://scontent.xx.fbcdn.net/image.jpg')
        for url in ['http://x.cdninstagram.com/a','https://evilcdninstagram.com/a','https://127.0.0.1/a','https://x.cdninstagram.com:8443/a','https://user:pass@x.cdninstagram.com/a']:  # pragma: allowlist secret (synthetic rejected-URL credentials)
            with self.assertRaises(ValueError):validate_media_url(url)

    def test_image_decoding_and_hashes(self):
        buffer=io.BytesIO();Image.new('RGB',(40,30)).save(buffer,format='JPEG');data=buffer.getvalue()
        record=save_image(data,self.root,'sample')
        self.assertEqual(record['width'],40);self.assertEqual(record['sha256'],hashlib.sha256(data).hexdigest())
        with self.assertRaises(RuntimeError):save_image(b'not an image',self.root,'bad')

    def test_verified_archive_removes_only_owned_folder_and_does_not_overwrite(self):
        folder=self.root/'working';folder.mkdir();(folder/'posts.json').write_text('[]')
        unrelated=self.root/'keep.txt';unrelated.write_text('keep')
        result=publish_archive(folder,self.root,'example.cafe')
        self.assertTrue(Path(result['zip_path']).is_file());self.assertFalse(folder.exists());self.assertTrue(unrelated.exists())
        with zipfile.ZipFile(result['zip_path']) as z:self.assertIsNone(z.testzip())

    def test_symlinks_and_corruption_are_rejected_without_cleanup(self):
        folder=self.root/'working';folder.mkdir();(folder/'private').symlink_to(self.root/'outside')
        with self.assertRaises(RuntimeError):publish_archive(folder,self.root,'example.cafe')
        self.assertTrue(folder.exists())


class FullWorkflowTests(unittest.TestCase):
    setUp = CookieAndStorageTests.setUp
    def test_full_bundle_and_failure_recovery_without_live_requests(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from instagram_scraper import scrape
        buffer=io.BytesIO();Image.new('RGB',(40,30)).save(buffer,format='JPEG')
        source=SimpleNamespace(api=SimpleNamespace(user=lambda handle:{'username':handle,'full_name':'Example Cafe','media_count':3,'viewer_secret':'NEVER_EXPORT'}),
                               posts=lambda:iter([post(1,1,True),post(2,50,count=3),post(3,40)]))
        def media(client,url):
            self.assertEqual(len(client.cookies),0)
            return buffer.getvalue()
        with patch('instagram_scraper.fetch_image',side_effect=media):
            result=scrape('example.cafe',2,self.root,source=source)
        self.assertTrue(result['success']);self.assertEqual(result['posts'],2);self.assertEqual(result['images'],4)
        with zipfile.ZipFile(result['zip_path']) as z:
            self.assertEqual(len([n for n in z.namelist() if '/media/' in n]),4)
            metadata=''.join(z.read(n).decode() for n in z.namelist() if n.endswith(('.json','.md','.csv')))
            self.assertNotIn('NEVER_EXPORT',metadata);self.assertNotIn('HIDDEN',metadata)
            self.assertNotIn('cdninstagram.com',metadata)
        with patch('instagram_scraper.fetch_image',side_effect=RuntimeError('Download refused')):
            result=scrape('example.cafe',2,self.root,source=source)
        self.assertFalse(result['success']);self.assertTrue(Path(result['working_folder']).is_dir())
        self.assertEqual(len(list(self.root.glob('*.zip'))),1)

    def test_archive_publication_failure_keeps_raw_folder(self):
        from unittest.mock import patch
        folder=self.root/'working';folder.mkdir();(folder/'posts.json').write_text('[]')
        with patch('instagram_scraper.os.link',side_effect=OSError('publish failed')):
            with self.assertRaises(OSError):publish_archive(folder,self.root,'example.cafe')
        self.assertTrue(folder.exists());self.assertFalse(list(self.root.glob('*.zip.part')))


class AdapterTests(unittest.TestCase):
    setUp = CookieAndStorageTests.setUp
    def source(self):
        from instagram_scraper import create_source
        p=self.root/'login.txt';p.write_text('# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tFAKE_OFFLINE_SESSION\n.instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tFAKE_OFFLINE_CSRF\n')
        source=create_source('example.cafe',cookies=p)
        self.addCleanup(source.session.close)
        return source

    def test_dependency_initializes_offline_with_cookie_file(self):
        source=self.source()
        self.assertEqual(source.__class__.__name__,'InstagramPostsExtractor')
        self.assertEqual(source.csrf_token,'FAKE_OFFLINE_CSRF')
        self.assertEqual(source.session.cookies.get('sessionid'),'FAKE_OFFLINE_SESSION')

    def test_missing_pagination_or_api_errors_fail_closed(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        source=self.source()
        for payload in [{'data':{'feed':{'edges':[]}}},{'errors':[{'message':'refused'}]},{'data':{'feed':{'page_info':{'has_next_page':False}}}}]:
            with patch.object(source.api,'_prepare_graphql',return_value=({},{})), patch.object(source,'request',return_value=SimpleNamespace(text=json.dumps(payload))):
                with self.assertRaises(RuntimeError):list(source.api._pagination_graphql('query',{},'feed'))

    def test_optional_location_thumbnail_error_does_not_discard_valid_posts(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        source=self.source()
        field='xdt_api__v1__feed__user_timeline_graphql_connection'
        payload={'data':{field:{'edges':[{'node':post(1,10)}], 'page_info':{'has_next_page':False}}},
                 'errors':[{'severity':'ERROR','path':[field,'edges',0,'node','location','profile_pic_url']}]}
        with patch.object(source.api,'_prepare_graphql',return_value=({},{})), patch.object(source,'request',return_value=SimpleNamespace(text=json.dumps(payload))):
            rows=list(source.api._pagination_graphql('query',{},field))
        self.assertEqual(rows[0]['pk'],'1')
        self.assertTrue(source.coverage_warnings)

    def test_required_post_image_error_is_still_rejected(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        source=self.source();field='feed'
        payload={'data':{field:{'edges':[{'node':post(1,10)}], 'page_info':{'has_next_page':False}}},
                 'errors':[{'path':[field,'edges',0,'node','image_versions2']}]}
        with patch.object(source.api,'_prepare_graphql',return_value=({},{})), patch.object(source,'request',return_value=SimpleNamespace(text=json.dumps(payload))):
            with self.assertRaises(RuntimeError):list(source.api._pagination_graphql('query',{},field))

    def test_repeated_cursor_is_not_reported_as_end_of_feed(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        source=self.source();payload={'data':{'feed':{'edges':[],'page_info':{'has_next_page':True,'end_cursor':'repeat'}}}}
        with patch.object(source.api,'_prepare_graphql',return_value=({},{})), patch.object(source,'request',return_value=SimpleNamespace(text=json.dumps(payload))):
            with self.assertRaisesRegex(RuntimeError,'repeated'):list(source.api._pagination_graphql('query',{},'feed'))


if __name__ == '__main__':
    unittest.main()
