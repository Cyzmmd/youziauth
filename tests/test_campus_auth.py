import configparser
import logging
import os
import unittest

try:
    import campus_auth
except ModuleNotFoundError as exc:
    campus_auth = None
    import_error = exc
else:
    import_error = None


class QueryStringParsingTests(unittest.TestCase):
    def test_extracts_query_string_from_eportal_redirect_script(self):
        self.assertIsNotNone(campus_auth, import_error)
        html = (
            "<script>"
            "top.self.location.href='http://222.198.127.170/eportal/index.jsp?"
            "wlanuserip=1.2.3.4&wlanacname=ac01&ssid=&nasip=10.0.0.1&url=http%3A%2F%2Fexample.com'"
            "</script>"
        )

        query = campus_auth.extract_query_string(html)

        self.assertEqual(
            query,
            "wlanuserip=1.2.3.4&wlanacname=ac01&ssid=&nasip=10.0.0.1&url=http%3A%2F%2Fexample.com",
        )

    def test_extracts_query_string_when_html_escapes_ampersands(self):
        self.assertIsNotNone(campus_auth, import_error)
        html = (
            "location.href=\"/eportal/index.jsp?"
            "wlanuserip=1.2.3.4&amp;wlanacname=ac01&amp;ssid=\""
        )

        query = campus_auth.extract_query_string(html)

        self.assertEqual(query, "wlanuserip=1.2.3.4&wlanacname=ac01&ssid=")

    def test_returns_none_when_login_page_has_no_query_string(self):
        self.assertIsNotNone(campus_auth, import_error)

        self.assertIsNone(campus_auth.extract_query_string("<html>already online</html>"))

    def test_ignores_non_login_jsp_query_on_success_page(self):
        self.assertIsNotNone(campus_auth, import_error)

        query = campus_auth.extract_query_string("<a href='success_mab.jsp?ms2g='>ok</a>")

        self.assertIsNone(query)


class LoginResultTests(unittest.TestCase):
    def test_success_json_is_treated_as_login_success(self):
        self.assertIsNotNone(campus_auth, import_error)

        result = campus_auth.parse_login_result('{"result":"success","message":"认证成功"}')

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "认证成功")

    def test_failed_json_keeps_server_message(self):
        self.assertIsNotNone(campus_auth, import_error)

        result = campus_auth.parse_login_result('{"result":"fail","message":"账号或密码错误"}')

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "账号或密码错误")

    def test_non_json_success_text_is_treated_as_success(self):
        self.assertIsNotNone(campus_auth, import_error)

        result = campus_auth.parse_login_result("callback({result:'success'})")

        self.assertTrue(result.ok)


class PasswordEncryptionTests(unittest.TestCase):
    def test_encrypts_password_with_eportal_rsa_public_key(self):
        self.assertIsNotNone(campus_auth, import_error)

        encrypted = campus_auth.rsa_encrypt_password("pw", "11", "10001")

        self.assertEqual(encrypted, "00f043")

    def test_build_login_payload_uses_encrypted_password_when_requested(self):
        self.assertIsNotNone(campus_auth, import_error)
        config = campus_auth.AuthConfig(
            username="student",
            password="plain",
            service="",
            password_encrypt=True,
        )

        payload = campus_auth.build_login_payload(
            config,
            "wlanuserip=1.2.3.4",
            password_value="encrypted-value",
        )

        self.assertEqual(payload["password"], "encrypted-value")
        self.assertEqual(payload["passwordEncrypt"], "true")

    def test_rejects_encryption_when_public_key_is_missing(self):
        self.assertIsNotNone(campus_auth, import_error)

        with self.assertRaisesRegex(ValueError, "publicKeyExponent"):
            campus_auth.extract_rsa_public_key({"publicKeyModulus": "ca1"})


class ConfigTests(unittest.TestCase):
    def test_loads_required_config_values(self):
        self.assertIsNotNone(campus_auth, import_error)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_dict(
            {
                "auth": {
                    "portal_url": "http://222.198.127.170/",
                    "username": "student",
                    "password": "secret",
                    "login_url": "http://222.198.127.170/eportal/index.jsp?wlanuserip=1.2.3.4",
                    "service": "%E9%BB%98%E8%AE%A4",
                    "check_interval_seconds": "30",
                    "request_timeout_seconds": "5",
                    "password_encrypt": "true",
                }
            }
        )

        config = campus_auth.load_config_from_parser(parser)

        self.assertEqual(config.portal_url, "http://222.198.127.170")
        self.assertEqual(config.username, "student")
        self.assertEqual(config.password, "secret")
        self.assertEqual(
            config.login_url,
            "http://222.198.127.170/eportal/index.jsp?wlanuserip=1.2.3.4",
        )
        self.assertEqual(config.service, "%E9%BB%98%E8%AE%A4")
        self.assertEqual(config.check_interval_seconds, 30)
        self.assertEqual(config.request_timeout_seconds, 5)
        self.assertIs(config.password_encrypt, True)

    def test_loads_auto_password_encryption_mode(self):
        self.assertIsNotNone(campus_auth, import_error)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_dict(
            {
                "auth": {
                    "portal_url": "http://222.198.127.170/",
                    "username": "student",
                    "password": "secret",
                    "password_encrypt": "auto",
                }
            }
        )

        config = campus_auth.load_config_from_parser(parser)

        self.assertIsNone(config.password_encrypt)

    def test_loads_password_from_environment_variable(self):
        self.assertIsNotNone(campus_auth, import_error)
        old_value = os.environ.get("CAMPUS_AUTH_TEST_PASSWORD")
        os.environ["CAMPUS_AUTH_TEST_PASSWORD"] = "secret-from-env"
        try:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_dict(
                {
                    "auth": {
                        "portal_url": "http://222.198.127.170/",
                        "username": "student",
                        "password": "",
                        "password_env": "CAMPUS_AUTH_TEST_PASSWORD",
                    }
                }
            )

            config = campus_auth.load_config_from_parser(parser)

            self.assertEqual(config.password, "secret-from-env")
        finally:
            if old_value is None:
                os.environ.pop("CAMPUS_AUTH_TEST_PASSWORD", None)
            else:
                os.environ["CAMPUS_AUTH_TEST_PASSWORD"] = old_value

    def test_rejects_missing_password_environment_variable(self):
        self.assertIsNotNone(campus_auth, import_error)
        os.environ.pop("CAMPUS_AUTH_MISSING_PASSWORD", None)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_dict(
            {
                "auth": {
                    "portal_url": "http://222.198.127.170/",
                    "username": "student",
                    "password": "",
                    "password_env": "CAMPUS_AUTH_MISSING_PASSWORD",
                }
            }
        )

        with self.assertRaisesRegex(ValueError, "CAMPUS_AUTH_MISSING_PASSWORD"):
            campus_auth.load_config_from_parser(parser)

    def test_rejects_missing_credentials(self):
        self.assertIsNotNone(campus_auth, import_error)
        parser = configparser.ConfigParser()
        parser.read_dict({"auth": {"portal_url": "http://222.198.127.170/"}})

        with self.assertRaisesRegex(ValueError, "username"):
            campus_auth.load_config_from_parser(parser)


class StatusParsingTests(unittest.TestCase):
    def test_online_user_info_success_means_authenticated(self):
        self.assertIsNotNone(campus_auth, import_error)

        status = campus_auth.parse_online_user_info('{"result":"success","userId":"student"}')

        self.assertEqual(status, campus_auth.AuthStatus.AUTHENTICATED)

    def test_online_user_info_fail_means_unauthenticated(self):
        self.assertIsNotNone(campus_auth, import_error)

        status = campus_auth.parse_online_user_info('{"result":"fail","message":"not online"}')

        self.assertEqual(status, campus_auth.AuthStatus.UNAUTHENTICATED)

    def test_unparseable_online_user_info_is_unknown(self):
        self.assertIsNotNone(campus_auth, import_error)

        status = campus_auth.parse_online_user_info("<html>bad gateway</html>")

        self.assertEqual(status, campus_auth.AuthStatus.UNKNOWN)


class StatusFlowTests(unittest.TestCase):
    def test_check_status_trusts_success_page_before_online_user_info_fail(self):
        self.assertIsNotNone(campus_auth, import_error)

        class FakeClient(campus_auth.CampusAuthClient):
            def __init__(self):
                super().__init__(campus_auth.AuthConfig(username="student", password="pw"))

            def request(self, url, data=None, allow_redirects=True, referer=None):
                if url == self.config.portal_url:
                    return campus_auth.HttpResponse(
                        200,
                        {},
                        "<html><title>login success</title></html>",
                        self.portal_url("/eportal/success_mab.jsp"),
                    )
                if url.endswith("method=getOnlineUserInfo"):
                    return campus_auth.HttpResponse(
                        200, {}, '{"result":"fail","message":"not online"}', url
                    )
                return campus_auth.HttpResponse(200, {}, "", url)

        status = FakeClient().check_status()

        self.assertEqual(status, campus_auth.AuthStatus.AUTHENTICATED)

    def test_success_page_url_wins_over_non_login_jsp_query(self):
        self.assertIsNotNone(campus_auth, import_error)

        class FakeClient(campus_auth.CampusAuthClient):
            def __init__(self):
                super().__init__(campus_auth.AuthConfig(username="student", password="pw"))

            def request(self, url, data=None, allow_redirects=True, referer=None):
                if url == self.config.portal_url:
                    return campus_auth.HttpResponse(
                        200,
                        {},
                        "<a href='success_mab.jsp?ms2g='>ok</a>",
                        self.portal_url("/eportal/success_mab.jsp"),
                    )
                return campus_auth.HttpResponse(200, {}, "", url)

        status = FakeClient().check_login_page_status()

        self.assertEqual(status, campus_auth.AuthStatus.AUTHENTICATED)


class LoginFlowTests(unittest.TestCase):
    def test_get_query_string_prefers_configured_login_url(self):
        self.assertIsNotNone(campus_auth, import_error)

        class FakeClient(campus_auth.CampusAuthClient):
            def request(self, url, data=None, allow_redirects=True, referer=None):
                raise AssertionError("configured login_url should avoid probing portal home")

        config = campus_auth.AuthConfig(
            username="student",
            password="pw",
            login_url=(
                "http://222.198.127.170/eportal/index.jsp?"
                "wlanuserip=10.135.155.137&wlanacname=NAS&ssid=Ruijie"
            ),
        )

        query = FakeClient(config).get_query_string()

        self.assertEqual(
            query,
            "wlanuserip=10.135.155.137&wlanacname=NAS&ssid=Ruijie",
        )

    def test_login_fetches_page_info_before_encrypted_login(self):
        self.assertIsNotNone(campus_auth, import_error)

        class FakeClient(campus_auth.CampusAuthClient):
            def __init__(self):
                super().__init__(
                    campus_auth.AuthConfig(
                        username="student",
                        password="pw",
                        password_encrypt=True,
                    )
                )
                self.requests = []

            def get_query_string(self):
                return "wlanuserip=1.2.3.4"

            def request(self, url, data=None, allow_redirects=True, referer=None):
                self.requests.append((url, data))
                if url.endswith("method=pageInfo"):
                    return campus_auth.HttpResponse(
                        200,
                        {},
                        '{"publicKeyExponent":"11","publicKeyModulus":"10001"}',
                        url,
                    )
                if url.endswith("method=login"):
                    self.login_payload = dict(data)
                    return campus_auth.HttpResponse(
                        200, {}, '{"result":"success","message":"ok"}', url
                    )
                raise AssertionError(f"unexpected url: {url}")

        client = FakeClient()

        result = client.login()

        self.assertTrue(result.ok)
        self.assertEqual(client.login_payload["password"], "00f043")
        self.assertEqual(client.login_payload["passwordEncrypt"], "true")
        self.assertTrue(any(url.endswith("method=pageInfo") for url, _ in client.requests))

    def test_auto_encryption_keeps_plain_password_when_page_info_disables_it(self):
        self.assertIsNotNone(campus_auth, import_error)

        class FakeClient(campus_auth.CampusAuthClient):
            def __init__(self):
                super().__init__(
                    campus_auth.AuthConfig(
                        username="student",
                        password="pw",
                        password_encrypt=None,
                    )
                )
                self.requests = []

            def get_query_string(self):
                return "wlanuserip=1.2.3.4"

            def request(self, url, data=None, allow_redirects=True, referer=None):
                self.requests.append((url, data))
                if url.endswith("method=pageInfo"):
                    return campus_auth.HttpResponse(
                        200,
                        {},
                        (
                            '{"passwordEncrypt":"false",'
                            '"publicKeyExponent":"10001",'
                            '"publicKeyModulus":"94dd"}'
                        ),
                        url,
                    )
                if url.endswith("method=login"):
                    self.login_payload = dict(data)
                    return campus_auth.HttpResponse(
                        200, {}, '{"result":"success","message":"ok"}', url
                    )
                raise AssertionError(f"unexpected url: {url}")

        client = FakeClient()

        result = client.login()

        self.assertTrue(result.ok)
        self.assertEqual(client.login_payload["password"], "pw")
        self.assertEqual(client.login_payload["passwordEncrypt"], "false")
        self.assertTrue(any(url.endswith("method=pageInfo") for url, _ in client.requests))

    def test_auto_encryption_sets_payload_flag_when_page_info_enables_it(self):
        self.assertIsNotNone(campus_auth, import_error)

        class FakeClient(campus_auth.CampusAuthClient):
            def __init__(self):
                super().__init__(
                    campus_auth.AuthConfig(
                        username="student",
                        password="pw",
                        password_encrypt=None,
                    )
                )
                self.requests = []

            def get_query_string(self):
                return "wlanuserip=1.2.3.4"

            def request(self, url, data=None, allow_redirects=True, referer=None):
                self.requests.append((url, data))
                if url.endswith("method=pageInfo"):
                    return campus_auth.HttpResponse(
                        200,
                        {},
                        (
                            '{"passwordEncrypt":"true",'
                            '"publicKeyExponent":"11",'
                            '"publicKeyModulus":"10001"}'
                        ),
                        url,
                    )
                if url.endswith("method=login"):
                    self.login_payload = dict(data)
                    return campus_auth.HttpResponse(
                        200, {}, '{"result":"success","message":"ok"}', url
                    )
                raise AssertionError(f"unexpected url: {url}")

        client = FakeClient()

        result = client.login()

        self.assertTrue(result.ok)
        self.assertEqual(client.login_payload["password"], "00f043")
        self.assertEqual(client.login_payload["passwordEncrypt"], "true")
        self.assertTrue(any(url.endswith("method=pageInfo") for url, _ in client.requests))

    def test_forced_plaintext_login_skips_page_info(self):
        self.assertIsNotNone(campus_auth, import_error)

        class FakeClient(campus_auth.CampusAuthClient):
            def __init__(self):
                super().__init__(
                    campus_auth.AuthConfig(
                        username="student",
                        password="pw",
                        password_encrypt=False,
                    )
                )
                self.requests = []

            def get_query_string(self):
                return "wlanuserip=1.2.3.4"

            def request(self, url, data=None, allow_redirects=True, referer=None):
                self.requests.append((url, data))
                if url.endswith("method=pageInfo"):
                    raise AssertionError("pageInfo should not be requested")
                if url.endswith("method=login"):
                    self.login_payload = dict(data)
                    return campus_auth.HttpResponse(
                        200, {}, '{"result":"success","message":"ok"}', url
                    )
                raise AssertionError(f"unexpected url: {url}")

        client = FakeClient()

        result = client.login()

        self.assertTrue(result.ok)
        self.assertEqual(client.login_payload["password"], "pw")
        self.assertEqual(client.login_payload["passwordEncrypt"], "false")


class StructuredAttemptTests(unittest.TestCase):
    def test_already_authenticated_returns_structured_success(self):
        class FakeClient:
            def check_status(self):
                return campus_auth.AuthStatus.AUTHENTICATED

        attempt = campus_auth.attempt_authentication(FakeClient(), logging.getLogger("test"))

        self.assertEqual(attempt.kind.value, "already_online")

    def test_server_rejection_returns_rejected_without_raw_response(self):
        class FakeClient:
            def check_status(self):
                return campus_auth.AuthStatus.UNAUTHENTICATED

            def login(self):
                return campus_auth.LoginResult(
                    ok=False,
                    message="账号或密码错误",
                    raw='{"password":"should-not-leak"}',
                )

        attempt = campus_auth.attempt_authentication(FakeClient(), logging.getLogger("test"))

        self.assertEqual(attempt.kind.value, "rejected")
        self.assertEqual(attempt.message, "账号或密码错误")
        self.assertNotIn("password", attempt.message)

    def test_pre_response_exception_returns_transient_error(self):
        class FakeClient:
            def check_status(self):
                raise OSError("network unavailable")

        attempt = campus_auth.attempt_authentication(FakeClient(), logging.getLogger("test"))

        self.assertEqual(attempt.kind.value, "transient_error")
        self.assertEqual(attempt.message, "network unavailable")


if __name__ == "__main__":
    unittest.main()
