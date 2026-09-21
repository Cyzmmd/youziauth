import unittest
from unittest.mock import MagicMock, patch
from campus_auth_desktop import set_desktop_app_id


class DesktopIdentityTests(unittest.TestCase):
    def test_source_and_packaged_process_use_same_app_identity_as_shortcut(self):
        shell = MagicMock()
        shell.SetCurrentProcessExplicitAppUserModelID.return_value = 0
        with patch('campus_auth_desktop.ctypes.windll') as dll:
            dll.shell32 = shell
            self.assertTrue(set_desktop_app_id())
        shell.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with('youziauth')

    def test_identity_failure_does_not_crash_window_start(self):
        with patch('campus_auth_desktop.ctypes.windll') as dll:
            dll.shell32.SetCurrentProcessExplicitAppUserModelID.return_value = -1
            self.assertFalse(set_desktop_app_id())
