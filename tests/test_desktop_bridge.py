import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from desktop_bridge import DesktopBridge, PreviewBridge
from campus_auth_gui import GuiSettings
from dorm_checkin import Result, Settings, Store


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.controller = MagicMock()
        self.controller.latest = Result('idle', '尚未查询今日任务')
        self.controller.busy = False
        self.controller.store.settings.return_value = Settings()
        self.controller.store.root = Path(self.tmp.name) / 'dorm'
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

    def test_update_snapshot_and_actions_are_isolated_from_school_operations(self):
        state = self.bridge.snapshot()
        self.assertIn('update', state)
        self.assertEqual(state['update']['state'], 'idle')
        with patch.object(self.bridge._updates, 'check', return_value='正在检查') as check:
            self.assertTrue(self.bridge.dispatch('update_check')['ok'])
            check.assert_called_once_with()
        self.controller.start.assert_not_called()
        self.controller.poll.assert_not_called()

    def test_update_install_requires_explicit_confirmation_and_no_active_operations(self):
        self.assertFalse(self.bridge.dispatch('update_install', {'version': '1.5.0'})['ok'])
        with patch.object(self.bridge._updates, 'install', return_value='打开安装向导') as install:
            self.controller.busy = True
            self.assertFalse(self.bridge.dispatch('update_install', {'confirmed': True, 'version': '1.5.0'})['ok'])
            install.assert_not_called()
            self.controller.busy = False
            self.assertTrue(self.bridge.dispatch('update_install', {'confirmed': True, 'version': '1.5.0'})['ok'])
            install.assert_called_once_with(True, '1.5.0')

    def test_preview_update_download_is_synthetic_and_install_never_opens_windows(self):
        with patch('desktop_bridge.UpdateController') as controller:
            preview = PreviewBridge()
            self.assertEqual(preview.snapshot()['update']['state'], 'idle')
            with patch('desktop_bridge.time.monotonic', return_value=100):
                self.assertTrue(preview.dispatch('update_check')['ok'])
                self.assertEqual(preview.snapshot()['update']['state'], 'downloading')
            with patch('desktop_bridge.time.monotonic', return_value=110):
                update = preview.snapshot()['update']
                self.assertEqual(update['state'], 'ready')
            self.assertFalse(preview.dispatch('update_install', {'version': update['latest_version']})['ok'])
            self.assertTrue(preview.dispatch('update_install', {'confirmed': True, 'version': update['latest_version']})['ok'])
            self.assertEqual(preview.snapshot()['update']['state'], 'launched')
            controller.assert_not_called()

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
        probe.assert_called_once_with(ui_dispatch=self.bridge._ui_dispatch, source='windows',
                                      sample_path=self.controller.store.root / 'location-sample.json')
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

    def test_saved_source_resets_previous_probe_and_applies_to_detection(self):
        self.controller.store = Store(Path(self.tmp.name) / 'dorm')
        self.controller.save.side_effect = self.controller.store.save_settings
        self.bridge._location_state = dict(state='ready', message='旧检测', accuracy=50, checked='21:00')
        payload = dict(enabled=False, start='21:00', end='23:30', interval=300, location_source='simulation')
        self.assertTrue(self.bridge.dispatch('dorm_save', payload)['ok'])
        self.assertEqual(getattr(self.controller.store.settings(), 'location_source', None), 'simulation')
        self.assertEqual(self.bridge.snapshot()['location']['state'], 'idle')
        sample_path = self.controller.store.root / 'location-sample.json'
        sample_path.write_text(json.dumps(dict(latitude=39.908823, longitude=116.397470,
                                               accuracy=141, timestamp=1700000000, source='WI_FI')), encoding='utf-8')
        with (patch('dorm_location.request_permission', side_effect=AssertionError('No simulated permission')),
              patch('dorm_location.read_position', side_effect=AssertionError('No simulated live position'))):
            self.assertTrue(self.bridge.dispatch('location_authorize')['ok'])
            self.bridge._location_worker.join(2)
        state = self.bridge.snapshot()['location']
        self.assertEqual(state['state'], 'ready')
        self.assertAlmostEqual(state['accuracy'], 141, delta=141 * 0.15)
        self.assertIn('模拟', state['message'])
        self.assertNotIn('latitude', state)
        self.controller.start.assert_not_called()

    def test_source_cannot_change_during_location_probe(self):
        self.bridge._location_gate.acquire()
        try:
            payload = dict(enabled=False, start='21:00', end='23:30', interval=300, location_source='simulation')
            self.assertFalse(self.bridge.dispatch('dorm_save', payload)['ok'])
            self.controller.save.assert_not_called()
        finally:
            self.bridge._location_gate.release()

    def test_unknown_location_source_does_not_save(self):
        payload = dict(enabled=False, start='21:00', end='23:30', interval=300, location_source='unknown')
        self.assertFalse(self.bridge.dispatch('dorm_save', payload)['ok'])
        self.controller.save.assert_not_called()

    def test_location_source_save_keeps_the_saved_schedule(self):
        self.controller.store = Store(Path(self.tmp.name) / 'dorm')
        self.controller.save.side_effect = self.controller.store.save_settings
        self.controller.store.save_settings(Settings(enabled=True, start='22:00', end='23:00', interval=600))
        result = self.bridge.dispatch('location_source_save', {'location_source': 'simulation'})
        self.assertTrue(result['ok'], result['message'])
        saved = self.controller.store.settings()
        self.assertEqual(saved.location_source, 'simulation')
        self.assertEqual((saved.enabled, saved.start, saved.end, saved.interval),
                         (True, '22:00', '23:00', 600))
        self.assertEqual(self.bridge.snapshot()['location']['state'], 'idle')

    def test_location_source_save_rejects_unknown_sources(self):
        self.controller.store = Store(Path(self.tmp.name) / 'dorm')
        self.controller.save.side_effect = self.controller.store.save_settings
        self.assertFalse(self.bridge.dispatch('location_source_save', {'location_source': 'unknown'})['ok'])
        self.controller.save.assert_not_called()
        self.assertEqual(self.controller.store.settings().location_source, 'windows')

    def test_location_source_save_is_rejected_while_a_probe_runs(self):
        self.bridge._location_gate.acquire()
        try:
            self.assertFalse(self.bridge.dispatch('location_source_save', {'location_source': 'simulation'})['ok'])
            self.controller.save.assert_not_called()
        finally:
            self.bridge._location_gate.release()

    def test_preview_location_source_save_switches_without_touching_the_schedule(self):
        preview = PreviewBridge()
        payload = dict(enabled=False, start='21:00', end='23:30', interval=300, location_source='windows')
        self.assertTrue(preview.dispatch('dorm_save', payload)['ok'])
        self.assertTrue(preview.dispatch('location_source_save', {'location_source': 'simulation'})['ok'])
        settings = preview.snapshot()['dorm']['settings']
        self.assertEqual(settings['location_source'], 'simulation')
        self.assertEqual(settings['interval'], 300)
        self.assertIn('模拟', preview.snapshot()['location']['message'])
        self.assertFalse(preview.dispatch('location_source_save', {'location_source': 'unknown'})['ok'])
        self.assertEqual(preview.snapshot()['dorm']['settings']['location_source'], 'simulation')

    def test_valid_save_can_recover_corrupt_settings(self):
        self.controller.store = Store(Path(self.tmp.name))
        self.controller.save.side_effect = self.controller.store.save_settings
        (self.controller.store.root / 'settings.json').write_text('{', encoding='utf-8')
        payload = dict(enabled=False, start='21:00', end='23:30', interval=300, location_source='windows')
        self.assertTrue(self.bridge.dispatch('dorm_save', payload)['ok'])
        self.assertEqual(self.controller.store.settings().location_source, 'windows')

    def test_preview_uses_saved_source_without_reading_real_samples(self):
        preview = PreviewBridge(real_location=True)
        payload = dict(enabled=False, start='21:00', end='23:30', interval=300, location_source='simulation')
        self.assertTrue(preview.dispatch('dorm_save', payload)['ok'])
        self.assertEqual(preview.snapshot()['dorm']['settings'].get('location_source'), 'simulation')
        with patch('desktop_bridge.probe_location', side_effect=AssertionError('Preview cannot read local samples')):
            self.assertTrue(preview.dispatch('location_authorize')['ok'])
        self.assertIn('模拟', preview.snapshot()['location']['message'])
        payload['location_source'] = 'windows'
        self.assertTrue(preview.dispatch('dorm_save', payload)['ok'])
        self.assertEqual(preview.snapshot()['location']['state'], 'idle')


if __name__ == '__main__':
    unittest.main()
