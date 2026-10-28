import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fb_photos import load_facebook_cookies, session


class CookieAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'login.txt'

    def write(self, extra=''):
        self.path.write_text('# Netscape HTTP Cookie File\n'
                             '.facebook.com\tTRUE\t/\tTRUE\t2147483647\tc_user\t123\n'
                             '#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t0\txs\tFAKE_TEST_SESSION\n' + extra)

    def test_reads_session_and_httponly_cookies_without_modifying_file(self):
        self.write()
        original = self.path.read_bytes()
        jar = load_facebook_cookies(self.path)
        self.assertEqual({c.name for c in jar}, {'c_user', 'xs'})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertIsNone(next(c for c in jar if c.name == 'xs').expires)

    def test_discards_unrelated_domains_and_expired_cookies(self):
        self.write('.evilfacebook.com\tTRUE\t/\tTRUE\t2147483647\tprivate\tFAKE_OTHER\n'
                   '.facebook.com\tTRUE\t/\tTRUE\t1\told\tFAKE_OLD\n')
        self.assertEqual({c.name for c in load_facebook_cookies(self.path)}, {'c_user', 'xs'})

    def test_rejects_missing_login_cookies(self):
        self.path.write_text('# Netscape HTTP Cookie File\n.facebook.com\tTRUE\t/\tTRUE\t0\tdpr\t2\n')
        with self.assertRaisesRegex(RuntimeError, 'c_user.*xs'):
            load_facebook_cookies(self.path)

    def test_malformed_file_does_not_disclose_cookie_values(self):
        self.path.write_text('# Netscape HTTP Cookie File\nFAKE_SENSITIVE_ROW\n')
        output = io.StringIO()
        with contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
            with self.assertRaises(RuntimeError) as raised:
                load_facebook_cookies(self.path)
        self.assertNotIn('FAKE_SENSITIVE_ROW', str(raised.exception) + output.getvalue())

    def test_missing_cookie_file_has_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError, 'cookie file'):
            load_facebook_cookies(self.path)

    def test_cookie_file_session_never_reads_a_browser_profile(self):
        from gallery_dl import config, extractor
        from requests.cookies import RequestsCookieJar
        from types import SimpleNamespace
        self.write()
        fake = SimpleNamespace(session=SimpleNamespace(cookies=RequestsCookieJar()), initialize=lambda: None)
        with patch.object(extractor, 'find', return_value=fake), patch.object(config, 'setg') as setting:
            source = session(cookies=self.path)
        self.assertFalse(any(call.args[0] == 'cookies' for call in setting.call_args_list))
        self.assertEqual({c.name for c in source.session.cookies}, {'c_user', 'xs'})
