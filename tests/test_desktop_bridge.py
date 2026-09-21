import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from desktop_bridge import DesktopBridge, PreviewBridge
from campus_auth_gui import GuiSettings
from dorm_checkin import Result, Settings


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.controller = MagicMock()
        self.controller.latest = Result('idle', '尚未查询今日任务')
        self.controller.busy = False
        self.controller.store.settings.return_value = Settings()
        self.controller.store.history.return_value = ''
        self.controller.schedule_text.return_value = '自动打卡：关闭'
        self.controller.drain.return_value = []
        self.patches = [
            patch('desktop_bridge.gui.ensure_user_config', side_effect=lambda p: p),
            patch('desktop_bridge.gui.load_gui_settings', return_value=GuiSettings('student', 'never-expose', 60)),
            patch('desktop_bridge.gui.is_startup_enabled', return_value=False),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.bridge = DesktopBridge(Path(self.tmp.name)/'config.ini', dorm=self.controller)
        self.addCleanup(self.bridge._close)

    def test_snapshot_excludes_secrets(self):
        state = self.bridge.snapshot()
        self.assertEqual(state['network']['username'], 'student')
        self.assertNotIn('password', state['network'])
        self.assertNotIn('never-expose', str(state))
        self.controller.store.token.assert_not_called()

    def test_blank_password_is_preserved_by_existing_storage_contract(self):
        with patch('desktop_bridge.gui.save_gui_settings') as save:
            result = self.bridge.dispatch('network_save', {'username':'student', 'password':'', 'interval':90, 'startup':False})
        self.assertTrue(result['ok'])
        self.assertEqual(save.call_args.args[1].password, '')
        self.assertEqual(save.call_args.args[1].check_interval_seconds, 90)

    def test_unknown_action_is_rejected(self):
        self.assertFalse(self.bridge.dispatch('__dict__', {})['ok'])
        self.controller.start.assert_not_called()

    def test_invalid_dorm_schedule_does_not_save(self):
        result = self.bridge.dispatch('dorm_save', {'enabled':True,'start':'23:30','end':'21:00','interval':300})
        self.assertFalse(result['ok'])
        self.controller.save.assert_not_called()

    def test_navigation_snapshot_does_not_submit_or_poll(self):
        self.bridge.snapshot()
        self.controller.start.assert_not_called()
        self.controller.poll.assert_not_called()

    def test_concurrent_network_operation_is_rejected(self):
        self.bridge._network_gate.acquire()
        try:
            result = self.bridge.dispatch('network_check', {})
            self.assertFalse(result['ok'])
        finally:
            self.bridge._network_gate.release()

    def test_dorm_busy_rejection_reaches_frontend(self):
        self.controller.start.return_value = False
        self.assertFalse(self.bridge.dispatch('dorm_query', {})['ok'])

    def test_preview_uses_no_real_controller_and_no_external_side_effects(self):
        with patch('desktop_bridge.DormController') as real, patch('desktop_bridge.gui.save_gui_settings') as save:
            preview = PreviewBridge()
            self.assertTrue(preview.snapshot()['preview'])
            self.assertTrue(preview.dispatch('dorm_submit', {})['ok'])
            self.assertEqual(preview.snapshot()['dorm']['state'], 'signed')
            real.assert_not_called()
            save.assert_not_called()

    def test_location_authorization_does_not_start_school_operation(self):
        self.bridge._ui_dispatch = MagicMock()
        result = {'state':'ready','message':'定位正常','accuracy':141,'checked':'21:00:00'}
        with patch('desktop_bridge.probe_location', return_value=result) as probe:
            self.assertTrue(self.bridge.dispatch('location_authorize')['ok'])
            self.bridge._location_worker.join(2)
        probe.assert_called_once_with(ui_dispatch=self.bridge._ui_dispatch)
        self.controller.start.assert_not_called()
        self.assertEqual(self.bridge.snapshot()['location']['state'], 'ready')

    def test_location_authorization_requires_foreground_desktop_dispatcher(self):
        self.assertFalse(self.bridge.dispatch('location_authorize')['ok'])

    def test_regular_preview_never_calls_real_location(self):
        with patch('desktop_bridge.probe_location') as probe:
            preview = PreviewBridge()
            self.assertTrue(preview.dispatch('location_authorize')['ok'])
            self.assertEqual(preview.snapshot()['location']['state'], 'ready')
            probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
