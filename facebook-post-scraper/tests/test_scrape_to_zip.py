import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from scrape_to_zip import archive_download, slugify_name, verify_archive
from business_data import build_bundle, write_business_files


class ArchiveWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.downloads = Path(self.temporary.name)

    def collection(self, name="working"):
        folder = self.downloads / name
        folder.mkdir()
        rows = []
        for i in range(2):
            photo = folder / f"{i + 1}.jpg"
            Image.new("RGB", (8, 6), (i * 80, 80, 120)).save(photo)
            rows.append({"id": str(i + 1), "filename": photo.name,
                         "width": 8, "height": 6,
                         "bytes": photo.stat().st_size,
                         "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()})
        report = {"complete": True, "exhausted_collection": True, "page_id": "123",
                  "photos": 2, "discovered_photos": 2,
                  "business_name": "Example Outdoor Services"}
        (folder / "report.json").write_text(json.dumps(report))
        (folder / "photo-index.csv").write_text("filename\n1.jpg\n2.jpg\n")
        profile = {'status': 'retrieved', 'business': {'name': report['business_name'], 'emails': ['office@example.com']},
                   'field_status': {'name': 'found', 'emails': 'found'}, 'errors': []}
        reviews = {'status': 'no_reviews', 'retrieved_count': 0, 'visible_count': 0, 'items': [],
                   'errors': [], 'coverage_note': 'No reviews returned in this test fixture.'}
        write_business_files(folder, build_bundle(profile, reviews, report, rows, 'https://www.facebook.com/123'))
        return folder, report, rows

    def test_success_leaves_only_a_verified_zip_with_business_name(self):
        folder, report, rows = self.collection()
        result = archive_download(folder, self.downloads, report, rows)
        archive = Path(result["zip_path"])
        self.assertTrue(archive.is_file())
        self.assertIn("example-outdoor-services", archive.name)
        self.assertEqual(result["photos"], 2)
        self.assertTrue(result["archive_verified"])
        self.assertTrue(result["raw_folder_removed"])
        self.assertFalse(folder.exists())
        self.assertEqual(list(self.downloads.iterdir()), [archive])
        with zipfile.ZipFile(archive) as stored:
            self.assertEqual(len([n for n in stored.namelist() if n.endswith('.jpg')]), 2)
            self.assertIsNone(stored.testzip())
            data_file = next(n for n in stored.namelist() if n.endswith('/business.json'))
            markdown_file = next(n for n in stored.namelist() if n.endswith('/business.md'))
            self.assertEqual(json.loads(stored.read(data_file))['business']['emails'], ['office@example.com'])
            self.assertIn(b'office@example.com', stored.read(markdown_file))

    def test_optional_brand_candidate_is_verified_and_does_not_change_gallery_count(self):
        folder, report, rows = self.collection()
        candidate = folder / 'facebook-profile-picture.png'
        Image.new('RGBA', (5, 7), (20, 30, 40, 255)).save(candidate)
        raw = candidate.read_bytes()
        profile = {'status': 'retrieved', 'business': {'name': report['business_name']},
                   'field_status': {'name': 'found'}, 'errors': [], 'brand_assets': {
                       'status': 'candidate', 'profile_picture': {
                           'path': candidate.name, 'photo_url': 'https://www.facebook.com/profile.php?id=123',
                           'width': 5, 'height': 7, 'bytes': len(raw),
                           'sha256': hashlib.sha256(raw).hexdigest(),
                           'source_kind': 'facebook_profile_picture', 'logo_verified': False}, 'notes': []}}
        reviews = {'status': 'no_reviews', 'retrieved_count': 0, 'visible_count': 0, 'items': [],
                   'errors': [], 'coverage_note': 'No reviews returned in this test fixture.'}
        write_business_files(folder, build_bundle(profile, reviews, report, rows, 'https://www.facebook.com/123'))
        result = archive_download(folder, self.downloads, report, rows)
        self.assertEqual(result['photos'], len(rows))
        with zipfile.ZipFile(result['zip_path']) as stored:
            data = json.loads(stored.read(next(n for n in stored.namelist() if n.endswith('/business.json'))))
            self.assertEqual(data['photos']['count'], 2)
            self.assertEqual(data['brand_assets']['status'], 'candidate')
            self.assertFalse(data['brand_assets']['profile_picture']['logo_verified'])
            self.assertEqual(data['brand_assets']['profile_picture']['photo_url'],
                             'https://www.facebook.com/profile.php?id=123')
            self.assertNotIn('fbcdn.net', json.dumps(data))
            self.assertTrue(any(n.endswith('/facebook-profile-picture.png') for n in stored.namelist()))

    def test_unavailable_optional_candidate_does_not_block_business_bundle(self):
        folder, report, rows = self.collection()
        profile = {'status': 'retrieved', 'business': {'name': report['business_name']},
                   'field_status': {'name': 'found'}, 'errors': [], 'brand_assets': {
                       'status': 'unavailable', 'profile_picture': None,
                       'notes': ['Candidate download failed.']}}
        reviews = {'status': 'no_reviews', 'retrieved_count': 0, 'visible_count': 0, 'items': [],
                   'errors': [], 'coverage_note': 'No reviews returned in this test fixture.'}
        write_business_files(folder, build_bundle(profile, reviews, report, rows, 'https://www.facebook.com/123'))
        result = archive_download(folder, self.downloads, report, rows)
        self.assertTrue(result['archive_verified'])
        with zipfile.ZipFile(result['zip_path']) as stored:
            data = json.loads(stored.read(next(n for n in stored.namelist() if n.endswith('/business.json'))))
            self.assertEqual(data['brand_assets']['status'], 'unavailable')
            self.assertEqual(data['photos']['count'], 2)

    def test_corrupt_optional_brand_candidate_is_rejected_and_preserved(self):
        folder, report, rows = self.collection()
        candidate = folder / 'facebook-profile-picture.jpg'
        Image.new('RGB', (5, 7), (20, 30, 40)).save(candidate)
        raw = candidate.read_bytes()
        profile = {'status': 'retrieved', 'business': {'name': report['business_name']},
                   'field_status': {'name': 'found'}, 'errors': [], 'brand_assets': {
                       'status': 'candidate', 'profile_picture': {
                           'path': candidate.name, 'photo_url': 'https://www.facebook.com/profile.php?id=123',
                           'width': 5, 'height': 7, 'bytes': len(raw),
                           'sha256': hashlib.sha256(raw).hexdigest(),
                           'source_kind': 'facebook_profile_picture', 'logo_verified': False}, 'notes': []}}
        reviews = {'status': 'no_reviews', 'retrieved_count': 0, 'visible_count': 0, 'items': [],
                   'errors': [], 'coverage_note': 'No reviews returned in this test fixture.'}
        write_business_files(folder, build_bundle(profile, reviews, report, rows, 'https://www.facebook.com/123'))
        candidate.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'Brand image hash or size mismatch'):
            archive_download(folder, self.downloads, report, rows)
        self.assertTrue(folder.exists())
        self.assertEqual(list(self.downloads.glob('*.zip')), [])

    def test_incomplete_or_empty_download_is_preserved(self):
        folder, report, rows = self.collection()
        report["complete"] = False
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            archive_download(folder, self.downloads, report, rows)
        self.assertTrue(folder.exists())
        self.assertEqual(list(self.downloads.glob('*.zip')), [])
        report.update(complete=True, photos=0, discovered_photos=0, exhausted_collection=False)
        with self.assertRaisesRegex(RuntimeError, "empty"):
            archive_download(folder, self.downloads, report, [])

    def test_verified_empty_photo_collection_still_returns_the_business_brief(self):
        folder, report, rows = self.collection()
        for row in rows:
            (folder / row['filename']).unlink()
        report.update(photos=0, discovered_photos=0)
        (folder / 'report.json').write_text(json.dumps(report))
        (folder / 'photo-index.csv').write_text('filename\n')
        profile = {'status': 'retrieved', 'business': {'name': report['business_name']},
                   'field_status': {'name': 'found'}, 'errors': []}
        reviews = {'status': 'no_reviews', 'retrieved_count': 0, 'visible_count': 0, 'items': [],
                   'errors': [], 'coverage_note': 'No reviews in this test fixture.'}
        write_business_files(folder, build_bundle(profile, reviews, report, [], 'https://www.facebook.com/123'))
        result = archive_download(folder, self.downloads, report, [])
        self.assertEqual(result['photos'], 0)
        self.assertTrue(result['raw_folder_removed'])
        with zipfile.ZipFile(result['zip_path']) as stored:
            self.assertEqual(len(stored.namelist()), 4)
            self.assertTrue(any(n.endswith('/business.json') for n in stored.namelist()))

    def test_changed_image_is_not_archived_or_deleted(self):
        folder, report, rows = self.collection()
        (folder / rows[0]['filename']).write_bytes(b'not the image')
        with self.assertRaisesRegex(RuntimeError, "image|hash|size"):
            archive_download(folder, self.downloads, report, rows)
        self.assertTrue(folder.exists())
        self.assertEqual(list(self.downloads.glob('*.zip')), [])

    def test_failed_zip_verification_never_deletes_the_raw_folder(self):
        folder, report, rows = self.collection()
        with patch('scrape_to_zip.verify_archive', side_effect=RuntimeError('ZIP verification failed')):
            with self.assertRaisesRegex(RuntimeError, 'ZIP verification failed'):
                archive_download(folder, self.downloads, report, rows)
        self.assertTrue(folder.exists())
        self.assertTrue((folder / '1.jpg').is_file())
        self.assertEqual(list(self.downloads.glob('*.zip')), [])

    def test_missing_metadata_prevents_publication_and_cleanup(self):
        for missing in ('report.json', 'photo-index.csv', 'business.json', 'business.md'):
            with self.subTest(missing=missing):
                folder, report, rows = self.collection(missing.replace('.', '-'))
                (folder / missing).unlink()
                with self.assertRaisesRegex(RuntimeError, 'metadata'):
                    archive_download(folder, self.downloads, report, rows)
                self.assertTrue((folder / '1.jpg').is_file())
                self.assertEqual(list(self.downloads.glob('*.zip')), [])

    def test_same_business_gets_fresh_archives_without_overwriting(self):
        first, report, rows = self.collection('first')
        one = Path(archive_download(first, self.downloads, report, rows)['zip_path'])
        first_hash = hashlib.sha256(one.read_bytes()).hexdigest()
        second, report, rows = self.collection('second')
        two = Path(archive_download(second, self.downloads, report, rows)['zip_path'])
        self.assertNotEqual(one, two)
        self.assertEqual(hashlib.sha256(one.read_bytes()).hexdigest(), first_hash)

    def test_archive_validation_detects_wrong_contents(self):
        archive = self.downloads / 'incorrect.zip'
        with zipfile.ZipFile(archive, 'w') as stored:
            stored.writestr('photos/1.jpg', b'wrong')
        with self.assertRaisesRegex(RuntimeError, 'hash|contents'):
            verify_archive(archive, {'photos/1.jpg': hashlib.sha256(b'right').hexdigest()})

    def test_file_names_cannot_escape_the_working_folder(self):
        folder, report, rows = self.collection()
        rows[0]['filename'] = '../elsewhere.jpg'
        with self.assertRaisesRegex(RuntimeError, 'filename'):
            archive_download(folder, self.downloads, report, rows)
        self.assertTrue(folder.exists())

    def test_slug_is_safe_and_has_a_fallback(self):
        self.assertEqual(slugify_name('Example Outdoor Services'), 'example-outdoor-services')
        self.assertEqual(slugify_name('../../'), 'facebook-photos')


if __name__ == '__main__':
    unittest.main()
