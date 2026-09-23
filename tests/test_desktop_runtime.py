import unittest
from unittest.mock import MagicMock, patch

from campus_auth_desktop import DesktopRuntime
from desktop_bridge import PreviewBridge


class DesktopRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = PreviewBridge()
        self.window = MagicMock()
        self.runtime = DesktopRuntime(self.bridge, self.window, preview=True)

    def test_real_startup_checks_updates_once_and_preview_does_not(self):
        for preview in (False, True):
            bridge = MagicMock()
            runtime = DesktopRuntime(bridge, MagicMock(), preview=preview)
            runtime.events.put('show')
            bridge._tick.side_effect = runtime.closed.set
            with patch('campus_auth_desktop.threading.Thread'), patch('campus_auth_desktop.windows_tray.WindowsTrayIcon') as tray:
                tray.return_value.start.return_value = False
                runtime.run()
            if preview:
                bridge._start_updates.assert_not_called()
            else:
                bridge._start_updates.assert_called_once_with()

    def test_close_hides_but_does_not_exit_or_stop_background(self):
        self.bridge._close = MagicMock()
        self.runtime.tray = MagicMock()
        self.assertFalse(self.runtime.closing())
        self.runtime._handle(self.runtime.events.get_nowait())
        self.window.hide.assert_called_once()
        self.bridge._close.assert_not_called()
        self.assertFalse(self.runtime.closed.is_set())

    def test_missing_tray_minimizes_instead_of_losing_window(self):
        self.runtime._handle('hide')
        self.window.minimize.assert_called_once()
        self.window.hide.assert_not_called()

    def test_dorm_tray_action_reuses_the_same_window(self):
        self.runtime._handle('dorm')
        self.window.show.assert_called_once()
        self.window.restore.assert_called_once()
        self.window.evaluate_js.assert_called_once_with('window.desktopNavigate("dorm")')

    def test_explicit_exit_stops_workers_and_tray_and_destroys_window(self):
        self.bridge._close = MagicMock()
        tray = self.runtime.tray = MagicMock()
        self.runtime._handle('quit')
        self.bridge._close.assert_called_once()
        tray.stop.assert_called_once()
        self.window.destroy.assert_called_once()
        self.assertTrue(self.runtime.closed.is_set())
        self.assertTrue(self.runtime.closing())


if __name__ == '__main__':
    unittest.main()
