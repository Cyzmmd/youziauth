import contextlib
import importlib.util
import inspect
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import threading

import dorm_login
from dorm_checkin import CheckinError, Store

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


class SessionLoginTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('interactive', inspect.signature(dorm_login.login).parameters,
                      'Non-interactive session renewal missing')
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name), protector=Mock(protect=lambda b: b[::-1], unprotect=lambda b: b[::-1]))
        self.cookies = [dict(name='SSO', value='session-cookie', domain='idm.swu.edu.cn',
                             path='/', expires=-1, httpOnly=True, secure=True, sameSite='Lax')]
        self.page = Mock(url='https://of.swu.edu.cn/')
        self.page.locator.return_value.first.is_visible.return_value = False
        self.context = Mock(pages=[self.page])
        self.context.cookies.return_value = self.cookies
        self.browser = Mock(contexts=[self.context])
        self.api = Mock()
        self.api.user.return_value = 'student'
        self.capture = None
        self.issue_token = True
        self.navigations = []
        self.restored = []
        self.context.on.side_effect = self.listen
        self.context.add_cookies.side_effect = self.restored.extend
        self.page.goto.side_effect = self.navigate
        driver = MagicMock()
        module = patch.dict(sys.modules, {'playwright.sync_api': driver})
        module.start()
        self.addCleanup(module.stop)
        owned = patch.object(dorm_login, 'owned_browser')
        self.owned = owned.start()
        self.owned.return_value.__enter__.return_value = self.browser
        self.addCleanup(owned.stop)

    def listen(self, name, capture):
        if name == 'response':
            self.capture = capture

    def navigate(self, url, **kwargs):
        self.navigations.append(url)
        if self.issue_token:
            self.capture(Mock(url='https://of.swu.edu.cn/gateway/auth/exchange-token', status=200,
                              headers={'fighter-auth-token': 'new-token'}))
        return Mock(status=200)

    def seed_session(self):
        self.store.save_token('old-token')
        self.store.save_browser_session('old-token', 'student', self.cookies)

    def test_interactive_login_saves_only_school_cookies(self):
        self.context.cookies.return_value = self.cookies + [dict(self.cookies[0], domain='unrelated.test')]
        self.assertEqual(dorm_login.login(self.store, self.api, threading.Event()), 'student')
        self.assertEqual(self.store.token(), 'new-token')
        self.assertEqual(self.store.browser_session('new-token')['cookies'], self.cookies)
        self.assertEqual(self.navigations, [dorm_login.INIT_URL])

    def test_renewal_restores_cookies_and_rotates_bound_token(self):
        self.seed_session()
        self.assertEqual(dorm_login.login(self.store, self.api, threading.Event(), interactive=False), 'student')
        self.assertEqual(self.restored, self.cookies)
        self.assertEqual(self.store.token(), 'new-token')
        self.assertIsNone(self.store.browser_session('old-token'))
        self.assertEqual(self.store.browser_session('new-token')['student'], 'student')
        self.assertTrue(self.owned.call_args.kwargs['headless'])

    def test_token_only_install_does_not_open_a_browser_for_renewal(self):
        self.store.save_token('old-token')
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, threading.Event(), interactive=False)
        self.assertEqual(caught.exception.state, 'login_required')
        self.owned.assert_not_called()
        self.assertEqual(self.store.token(), 'old-token')

    def test_captcha_timeout_discards_unusable_session_not_token(self):
        self.seed_session()
        self.issue_token = False
        self.page.locator.return_value.first.is_visible.return_value = True
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, threading.Event(), timeout=0, interactive=False)
        self.assertEqual(caught.exception.state, 'login_required')
        self.assertIn('手动', str(caught.exception))
        self.assertEqual(self.store.token(), 'old-token')
        self.assertIsNone(self.store.browser_session('old-token'))
        self.owned.reset_mock()
        with self.assertRaises(CheckinError):
            dorm_login.login(self.store, self.api, threading.Event(), interactive=False)
        self.owned.assert_not_called()

    def test_renewal_never_switches_accounts(self):
        self.seed_session()
        self.api.user.return_value = 'another-student'
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, threading.Event(), interactive=False)
        self.assertEqual(caught.exception.state, 'login_required')
        self.assertEqual(self.store.token(), 'old-token')
        self.assertIsNone(self.store.browser_session('old-token'))

    def test_network_failure_preserves_session_for_later(self):
        self.seed_session()
        self.page.goto.side_effect = TimeoutError('private-cookie')
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, threading.Event(), interactive=False)
        self.assertEqual(caught.exception.state, 'network_error')
        self.assertNotIn('private-cookie', str(caught.exception))
        self.assertIsNotNone(self.store.browser_session('old-token'))

    def test_exchange_service_failure_preserves_session(self):
        self.seed_session()
        self.issue_token = False
        def unavailable(url, **kwargs):
            self.capture(Mock(url='https://of.swu.edu.cn/gateway/auth/exchange-token', status=503, headers={}))
            return Mock(status=200)
        self.page.goto.side_effect = unavailable
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, threading.Event(), timeout=0, interactive=False)
        self.assertEqual(caught.exception.state, 'network_error')
        self.assertIsNotNone(self.store.browser_session('old-token'))

    def test_exchange_auth_rejection_clears_session(self):
        self.seed_session()
        self.issue_token = False
        def rejected(url, **kwargs):
            self.capture(Mock(url='https://of.swu.edu.cn/gateway/auth/exchange-token', status=200,
                              headers={}, json=lambda: {'code': 401, 'data': None}))
            return Mock(status=200)
        self.page.goto.side_effect = rejected
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, threading.Event(), timeout=0, interactive=False)
        self.assertEqual(caught.exception.state, 'login_required')
        self.assertIsNone(self.store.browser_session('old-token'))

    def test_silent_exchange_timeout_preserves_session(self):
        self.seed_session()
        self.issue_token = False
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, threading.Event(), timeout=0, interactive=False)
        self.assertEqual(caught.exception.state, 'network_error')
        self.assertIsNotNone(self.store.browser_session('old-token'))

    def test_callback_page_waits_for_delayed_exchange_without_navigating_again(self):
        self.seed_session()
        self.issue_token = False
        self.page.url = 'https://of.swu.edu.cn/#/casLogin?from=%2FappCenter'
        self.page.wait_for_timeout.side_effect = lambda delay: self.capture(Mock(
            url='https://of.swu.edu.cn/gateway/auth/exchange-token', status=200,
            headers={'fighter-auth-token': 'new-token'}))
        self.assertEqual(dorm_login.login(self.store, self.api, threading.Event(), interactive=False), 'student')
        self.assertEqual(self.navigations, [dorm_login.INIT_URL])

    def test_cancellation_on_last_wait_does_not_invalidate_session(self):
        self.seed_session()
        self.issue_token = False
        cancel = threading.Event()
        def stop(delay):
            cancel.set()
            time.sleep(.002)
        self.page.wait_for_timeout.side_effect = stop
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, cancel, timeout=.001, interactive=False)
        self.assertEqual(caught.exception.state, 'cancelled')
        self.assertIsNotNone(self.store.browser_session('old-token'))

    def test_cancellation_during_cookie_capture_does_not_commit_new_session(self):
        self.seed_session()
        cancel = threading.Event()
        def stop():
            cancel.set()
            return self.cookies
        self.context.cookies.side_effect = stop
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, cancel, interactive=False)
        self.assertEqual(caught.exception.state, 'cancelled')
        self.assertEqual(self.store.token(), 'old-token')
        self.assertIsNotNone(self.store.browser_session('old-token'))

    def test_cancelled_renewal_does_not_open_browser_or_clear_session(self):
        self.seed_session()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, cancel, interactive=False)
        self.assertEqual(caught.exception.state, 'cancelled')
        self.owned.assert_not_called()
        self.assertIsNotNone(self.store.browser_session('old-token'))

    def test_expired_cookies_do_not_start_renewal(self):
        self.store.save_token('old-token')
        self.store.save_browser_session('old-token', 'student', [dict(self.cookies[0], expires=1)])
        with self.assertRaises(CheckinError):
            dorm_login.login(self.store, self.api, threading.Event(), interactive=False)
        self.owned.assert_not_called()

    def test_failed_session_save_preserves_previous_matching_credentials(self):
        self.seed_session()
        with patch.object(self.store, 'save_browser_session', side_effect=OSError('disk full')):
            with self.assertRaises(CheckinError):
                dorm_login.login(self.store, self.api, threading.Event(), interactive=False)
        self.assertEqual(self.store.token(), 'old-token')
        self.assertIsNotNone(self.store.browser_session('old-token'))

    def test_interactive_login_recovers_from_unreadable_saved_session(self):
        self.seed_session()
        with patch.object(self.store, 'browser_session', side_effect=CheckinError('login_required', '损坏')):
            self.assertEqual(dorm_login.login(self.store, self.api, threading.Event()), 'student')
        self.assertEqual(self.store.token(), 'new-token')

    def test_cancel_after_identity_check_does_not_replace_saved_session(self):
        self.seed_session()
        cancel = threading.Event()
        def identify(token):
            cancel.set()
            return 'student'
        self.api.user.side_effect = identify
        with self.assertRaises(CheckinError) as caught:
            dorm_login.login(self.store, self.api, cancel, interactive=False)
        self.assertEqual(caught.exception.state, 'cancelled')
        self.assertEqual(self.store.token(), 'old-token')
        self.assertIsNotNone(self.store.browser_session('old-token'))


@unittest.skipUnless(os.environ.get('YOUZIAUTH_BROWSER_TESTS') == '1', 'Opt-in native browser test')
class BrowserSessionRuntimeTests(unittest.TestCase):
    def test_native_browser_restores_encrypted_session_after_process_restart(self):
        from urllib.parse import urlsplit
        from dorm_api import SwuApi
        from dorm_panel import DormController
        native_browser = dorm_login.owned_browser
        launches, restored_requests = [], []
        accept_session = True
        reject_old_token = False

        @contextlib.contextmanager
        def offline_browser(playwright, cancel, *, headless=False):
            launches.append(headless)
            generation = len(launches)
            with native_browser(playwright, cancel, headless=True) as browser:
                def respond(route):
                    request = route.request
                    url = urlsplit(request.url)
                    if url.hostname != 'of.swu.edu.cn':
                        route.abort()
                        return
                    if url.path == '/gateway/auth/exchange-token':
                        route.fulfill(status=200, content_type='application/json',
                                      body=json.dumps({'code': 200, 'data': f'offline-token-{generation}'}))
                        return
                    if url.path.startswith('/cas/'):
                        restored = 'SSO=offline-session-cookie' in request.all_headers().get('cookie', '')
                        if generation > 1:
                            restored_requests.append(restored)
                        authenticated = generation == 1 or (accept_session and restored)
                        html = ('<script>history.replaceState(null,"","/#/casLogin");'
                                'setTimeout(() => fetch("/gateway/auth/exchange-token"), 150)</script>' if authenticated else
                                '<form><input name="password" type="password"><input name="captcha"></form>')
                        headers = {'Set-Cookie': 'SSO=offline-session-cookie; Path=/; Secure; HttpOnly; SameSite=Lax'} if authenticated else {}
                        route.fulfill(status=200, content_type='text/html', headers=headers, body=html)
                        return
                    route.abort()
                browser.contexts[0].route('**/*', respond)
                yield browser

        def identity(method, path, token, **kwargs):
            self.assertTrue(token.startswith('offline-token-'))
            if reject_old_token and token == 'offline-token-1':
                raise CheckinError('login_required', '测试令牌已失效')
            if path == '/gateway/fighter-baida/api/cqtj/getTransitionByToday':
                return {'records': [], 'total': 0}
            self.assertEqual(path, '/gateway/fighter-middle/api/auth/user')
            return {'subject': {'username': 'offline-student'}}

        with tempfile.TemporaryDirectory() as directory, patch.object(dorm_login, 'owned_browser', offline_browser):
            store = Store(Path(directory))
            api, cancel = SwuApi(transport=identity), threading.Event()
            self.assertEqual(dorm_login.login(store, api, cancel, timeout=5), 'offline-student')
            self.assertEqual(store.token(), 'offline-token-1')
            restarted = Store(Path(directory))
            reject_old_token = True
            controller = DormController(restarted)
            controller.api = controller.engine.api = api
            try:
                self.assertTrue(controller.start('query'))
                deadline = time.monotonic() + 60
                while controller.busy and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(controller.busy)
                self.assertEqual(controller.drain()[-1].state, 'no_task')
            finally:
                controller.close()
            self.assertEqual(restarted.token(), 'offline-token-2')
            self.assertTrue(restored_requests and all(restored_requests))
            for path in Path(directory).rglob('*'):
                if path.is_file():
                    self.assertNotIn(b'offline-session-cookie', path.read_bytes())
                    self.assertNotIn(b'offline-token-', path.read_bytes())
            accept_session = False
            with self.assertRaises(CheckinError) as caught:
                dorm_login.login(restarted, api, cancel, timeout=.2, interactive=False)
            self.assertEqual(caught.exception.state, 'login_required')
            self.assertIsNone(restarted.browser_session(restarted.token()))
            with self.assertRaises(CheckinError):
                dorm_login.login(restarted, api, cancel, interactive=False)
            self.assertEqual(launches, [False, True, True])
            restarted.clear_token()
            self.assertEqual(restarted.token(), '')


if __name__ == '__main__':
    unittest.main()
