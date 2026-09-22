import importlib.util
import unittest
from unittest.mock import Mock, patch, MagicMock
import threading

PRESENT = importlib.util.find_spec('dorm_login') is not None
if PRESENT:
    from dorm_login import extract_token


class FeatureExists(unittest.TestCase):
    def test_login_exists(self):
        self.assertTrue(PRESENT, 'Dorm login module missing')


@unittest.skipUnless(PRESENT, 'Login module missing')
class LoginTests(unittest.TestCase):
    def test_owned_browser_uses_native_explicit_port_and_closes_only_owned_process(self):
        import dorm_login
        from pathlib import Path
        process = Mock()
        process.poll.return_value = None
        playwright = Mock()
        browser = playwright.chromium.connect_over_cdp.return_value
        with patch.object(dorm_login, 'browser_executable', return_value=Path('C:/Browser/chrome.exe')), \
             patch.object(dorm_login.subprocess, 'Popen', return_value=process) as popen, \
             patch.object(dorm_login.socket, 'create_connection', return_value=MagicMock()):
            with dorm_login.owned_browser(playwright, threading.Event()) as actual:
                self.assertIs(actual, browser)
            command = popen.call_args.args[0]
            port_arg = next(x for x in command if x.startswith('--remote-debugging-port='))
            self.assertGreater(int(port_arg.split('=')[1]), 0)
            self.assertIn('--remote-debugging-address=127.0.0.1', command)
            self.assertTrue(any(x.startswith('--user-data-dir=') and 'youziauth-login-' in x for x in command))
            browser.close.assert_called_once()
            process.terminate.assert_called_once()

    def test_login_navigation_primes_cas_then_follows_federation(self):
        import dorm_login
        self.assertTrue(hasattr(dorm_login, 'open_login_page'), 'Federation navigation missing')
        page = Mock()
        page.url = 'https://uaaap.swu.edu.cn/cas/login?service=example'
        urls = []
        def navigate(url, **kwargs):
            urls.append(url)
            if 'federalEnable=true' in url:
                page.url = 'https://idm.swu.edu.cn/am/UI/Login'
            return Mock(status=200)
        page.goto.side_effect = navigate
        dorm_login.open_login_page(page, threading.Event())
        self.assertEqual(urls[0], dorm_login.INIT_URL)
        self.assertEqual(urls[1], dorm_login.LOGIN_URL)
        self.assertIn('federalEnable=true', urls[2])

    def test_navigation_http_error_is_reported_not_swallowed(self):
        import dorm_login
        self.assertTrue(hasattr(dorm_login, 'open_login_page'))
        page = Mock()
        page.goto.return_value = Mock(status=400)
        from dorm_checkin import CheckinError
        with self.assertRaisesRegex(CheckinError, 'HTTP 400'):
            dorm_login.open_login_page(page, threading.Event())

    def test_federation_preserves_original_encoded_query_exactly(self):
        import dorm_login
        original = 'https://uaaap.swu.edu.cn/cas/login?service=https%3a%2f%2fexample&state=a%20b%2f~'
        page = Mock(url=original)
        urls = []
        def navigate(url, **kwargs):
            urls.append(url)
            if 'federalEnable=true' in url:
                page.url = 'https://idm.swu.edu.cn/am/UI/Login'
            return Mock(status=200)
        page.goto.side_effect = navigate
        dorm_login.open_login_page(page, threading.Event())
        self.assertEqual(urls[-1], original + '&federalEnable=true')

    def test_navigation_does_not_modify_untrusted_redirect(self):
        import dorm_login
        self.assertTrue(hasattr(dorm_login, 'open_login_page'))
        page = Mock(url='https://other.test/cas/login')
        page.goto.return_value = Mock(status=200)
        from dorm_checkin import CheckinError
        with self.assertRaises(CheckinError):
            dorm_login.open_login_page(page, threading.Event())
        self.assertEqual(page.goto.call_count, 2)

    def test_cancelled_navigation_does_not_open_school_page(self):
        import dorm_login
        self.assertTrue(hasattr(dorm_login, 'open_login_page'))
        cancel = threading.Event()
        cancel.set()
        page = Mock()
        from dorm_checkin import CheckinError
        with self.assertRaises(CheckinError):
            dorm_login.open_login_page(page, cancel)
        page.goto.assert_not_called()

    def response(self, url='https://of.swu.edu.cn/gateway/auth/exchange-token', **kwargs):
        return Mock(url=url, status=200, headers={}, json=lambda: {'code': 200, 'data': 'secret'}, **kwargs)

    def test_only_school_https_exchange_response_is_accepted(self):
        self.assertEqual(extract_token(self.response()), 'secret')
        for url in ['https://of.swu.edu.cn.evil.test/gateway/auth/exchange-token',
                    'http://of.swu.edu.cn/gateway/auth/exchange-token',
                    'https://of.swu.edu.cn:8443/gateway/auth/exchange-token',
                    'https://other.test/?exchange-token',
                    'https://of.swu.edu.cn/gateway/other?exchange-token']:
            self.assertIsNone(extract_token(self.response(url)))

    def test_dict_body_is_not_a_token(self):
        r = self.response()
        r.json = lambda: {'code': 200, 'data': {'token': 'secret'}}
        self.assertIsNone(extract_token(r))

    def test_header_token_supported(self):
        r = self.response()
        r.headers = {'fighter-auth-token': 'header-token'}
        self.assertEqual(extract_token(r), 'header-token')

    def test_newline_token_rejected(self):
        r = self.response()
        r.headers = {'fighter-auth-token': 'bad\r\ntoken'}
        r.json = lambda: {}
        self.assertIsNone(extract_token(r))


if __name__ == '__main__':
    unittest.main()
