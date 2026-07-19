import configparser
import tempfile
import unittest
from pathlib import Path

import campus_auth_web


class WebUiRenderingTests(unittest.TestCase):
    def test_render_page_contains_required_login_settings_and_log_regions(self):
        html = campus_auth_web.render_page()

        self.assertIn("账号", html)
        self.assertIn("密码", html)
        self.assertIn("检测间隔", html)
        self.assertIn("日志", html)
        self.assertIn('data-testid="username"', html)
        self.assertIn('data-testid="password"', html)
        self.assertIn('data-testid="interval"', html)
        self.assertIn('data-testid="logs"', html)

    def test_render_page_does_not_promise_windows_tray_behavior(self):
        html = campus_auth_web.render_page()

        self.assertNotIn("托盘", html)
        self.assertNotIn("隐藏图标", html)


class WebUiSettingsTests(unittest.TestCase):
    def write_config(self, path: Path) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser["auth"] = {
            "portal_url": "http://222.198.127.170/",
            "username": "student",
            "password": "secret",
            "check_interval_seconds": "60",
            "log_file": "campus_auth.log",
        }
        with path.open("w", encoding="utf-8") as file:
            parser.write(file)

    def test_settings_payload_never_returns_password(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            self.write_config(config_path)
            state = campus_auth_web.WebAppState(config_path=config_path)

            payload = state.settings_payload()

            self.assertEqual(payload["username"], "student")
            self.assertEqual(payload["check_interval_seconds"], 60)
            self.assertNotIn("password", payload)

    def test_settings_from_payload_validates_positive_interval(self):
        with self.assertRaisesRegex(ValueError, "check_interval_seconds"):
            campus_auth_web.settings_from_payload(
                {"username": "student", "password": "", "check_interval_seconds": "0"}
            )


if __name__ == "__main__":
    unittest.main()
