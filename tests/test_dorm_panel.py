import datetime as dt
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dorm_checkin import CheckinError, Settings, Store, Result, Task, SHANGHAI
import windows_tray

PRESENT = importlib.util.find_spec('dorm_panel') is not None
if PRESENT:
    from dorm_panel import DormController


class FeatureExists(unittest.TestCase):
    def test_panel_exists(self):
        self.assertTrue(PRESENT, 'Dormitory UI controller missing')

    def test_tray_has_dorm_entry(self):
        self.assertIn('dorm', [x.command for x in windows_tray.build_tray_menu_items(windows_tray.TrayStatus.ONLINE)])

    def test_packaged_smoke_entry_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('dorm_selftest'), 'Packaged smoke check missing')


@unittest.skipUnless(PRESENT, 'Controller missing')
class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.engine = Mock()
        self.engine.cancel = __import__('threading').Event()
        self.engine.run.return_value = Result('ready', '待完成')
        self.controller = DormController(self.store, engine=self.engine)
        self.addCleanup(self.controller.close)

    def wait(self):
        for _ in range(100):
            if not self.controller.busy:
                return
            time.sleep(.01)
        self.fail('Worker did not stop')

    def test_query_dispatch_is_read_only(self):
        self.controller.start('query')
        self.wait()
        self.engine.run.assert_called_once_with(submit=False)
        self.assertEqual(self.controller.drain()[-1].state, 'ready')

    def test_submit_is_explicit_action(self):
        self.controller.start('submit')
        self.wait()
        self.engine.run.assert_called_once_with(submit=True)

    def test_disable_does_not_schedule_requests(self):
        self.controller.poll()
        self.wait()
        self.engine.tick.assert_not_called()

    def test_exit_prevents_further_jobs(self):
        self.controller.close()
        self.assertFalse(self.controller.start('query'))
        self.engine.run.assert_not_called()

    def test_only_one_worker_and_cancel_does_not_touch_network_agent(self):
        gate = __import__('threading').Event()
        self.engine.run.side_effect = lambda **kw: (gate.wait(1), Result('ready', '待完成'))[1]
        self.controller.start('query')
        self.assertFalse(self.controller.start('submit'))
        self.controller.cancel()
        self.assertTrue(self.engine.cancel.is_set())
        gate.set()
        self.wait()

    def test_logout_preserves_global_location_source(self):
        self.assertIn('location_source', Settings.__dataclass_fields__)
        self.store.save_settings(Settings(enabled=True, location_source='simulation'))
        self.controller.start('logout')
        self.wait()
        settings = self.store.settings()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.location_source, 'simulation')

    def test_legacy_settings_save_preserves_global_location_source(self):
        from dorm_panel import DormPanel
        self.assertIn('location_source', Settings.__dataclass_fields__)
        self.store.save_settings(Settings(location_source='simulation'))
        panel = DormPanel.__new__(DormPanel)
        panel.controller = self.controller
        panel.enabled = Mock(get=lambda: False)
        panel.start = Mock(get=lambda: '20:00')
        panel.end = Mock(get=lambda: '23:00')
        panel.interval = Mock(get=lambda: '600')
        panel.state, panel.refresh = Mock(), Mock()
        panel.save()
        self.assertEqual(self.store.settings().location_source, 'simulation')
        self.assertEqual(self.store.settings().interval, 600)

    def test_legacy_refresh_displays_the_current_location_source(self):
        from dorm_panel import DormPanel
        panel = DormPanel.__new__(DormPanel)
        panel.controller = self.controller
        panel.schedule, panel.location_text = Mock(), Mock()
        panel.buttons = []
        panel._history_text = '暂无打卡记录'
        self.store.save_settings(Settings(location_source='simulation'))
        panel.refresh()
        value = panel.location_text.set.call_args.args[0] if panel.location_text.set.called else ''
        self.assertIn('模拟', value)
        self.assertIn('非实时', value)
        self.store.save_settings(Settings(location_source='windows'))
        panel.refresh()
        self.assertIn('Windows', panel.location_text.set.call_args.args[0])
        self.assertNotIn('模拟', panel.location_text.set.call_args.args[0])

    def test_legacy_valid_save_recovers_corrupt_settings(self):
        from dorm_panel import DormPanel
        path = self.store.root / 'settings.json'
        path.write_text('{', encoding='utf-8')
        panel = DormPanel.__new__(DormPanel)
        panel.controller, panel.window = self.controller, None
        panel.enabled = Mock(get=lambda: False)
        panel.start, panel.end = Mock(get=lambda: '21:00'), Mock(get=lambda: '23:30')
        panel.interval = Mock(get=lambda: '300')
        panel.state, panel.refresh = Mock(), Mock()
        with patch('tkinter.messagebox.showerror'):
            panel.save()
        self.assertNotEqual(path.read_text(encoding='utf-8'), '{')
        self.assertEqual(self.store.settings().location_source, 'windows')


class GlobalLocationTests(unittest.TestCase):
    def setUp(self):
        self.assertIn('location_source', Settings.__dataclass_fields__)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name), protector=Mock(protect=lambda b: b[::-1], unprotect=lambda b: b[::-1]))
        self.store.save_token('offline-fixture-token')
        self.sample = dict(latitude=39.908823, longitude=116.397470, accuracy=141,
                           timestamp=1700000000, source='WI_FI')
        (self.store.root / 'location-sample.json').write_text(json.dumps(self.sample), encoding='utf-8')
        self.controller = DormController(self.store)
        self.addCleanup(self.controller.close)
        self.api = Mock()
        self.api.user.return_value = 'fixture-student'
        self.api.today.return_value = Task('fixture-task', 'fixture-form', 'fixture-publish', 'fixture-student',
                                          '2026-09-21', '测试查寝', '21:00', '23:30', False, '测试寝室', '800米')
        self.api.is_signed.return_value = True
        self.controller.engine.api = self.api
        self.controller.engine.clock = lambda: dt.datetime(2026, 9, 21, 22, tzinfo=SHANGHAI)
        guard = patch('socket.socket.connect', side_effect=AssertionError('Unexpected network access'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_saved_source_applies_to_manual_and_automatic_operations(self):
        raw = dict(latitude=51.5, longitude=-0.1, accuracy=50, timestamp=time.time(), source='WI_FI')
        with patch('dorm_location.read_position', return_value=raw):
            self.assertEqual(self.controller.engine.run(submit=True).state, 'signed')
        self.assertEqual(self.api.submit.call_args.args[2]['provider'], 'windows')
        self.controller.save(Settings(enabled=True, location_source='simulation'))
        with patch('dorm_location.read_position', side_effect=AssertionError('Simulation must not locate')):
            self.assertEqual(self.controller.engine.tick().state, 'signed')
        position = self.api.submit.call_args.args[2]
        self.assertEqual(position['provider'], 'windows')  # same wire label as a live fix
        self.assertNotIn('simulation', position.values())
        self.assertAlmostEqual(position['latitude'], 39.910226, delta=0.0004)   # ≤25 m drift
        self.assertAlmostEqual(position['longitude'], 116.403714, delta=0.0004)
        self.assertAlmostEqual(position['accuracy'], 141, delta=141 * 0.15)
        self.assertEqual(self.api.verify.call_args.args[2], position)

    def test_simulation_still_requires_school_range_approval(self):
        self.controller.save(Settings(location_source='simulation'))
        self.api.verify.side_effect = CheckinError('location_required', '范围未通过')
        with patch('dorm_location.read_position', side_effect=AssertionError('Simulation must not locate')):
            result = self.controller.engine.run(submit=True)
        self.assertEqual(result.state, 'location_required')
        self.api.submit.assert_not_called()

    def test_missing_sample_stops_without_falling_back_to_windows(self):
        self.controller.save(Settings(location_source='simulation'))
        (self.store.root / 'location-sample.json').unlink()
        with patch('dorm_location.read_position', side_effect=AssertionError('No silent fallback')):
            result = self.controller.engine.run(submit=True)
        self.assertEqual(result.state, 'location_required')
        self.api.verify.assert_not_called()
        self.api.submit.assert_not_called()

    def test_saving_while_busy_does_not_change_source(self):
        self.controller.busy = True
        with self.assertRaises(RuntimeError):
            self.controller.save(Settings(location_source='simulation'))
        self.assertEqual(self.store.settings().location_source, 'windows')

    def test_bad_simulation_quality_points_to_the_sample_not_windows(self):
        self.controller.save(Settings(location_source='simulation'))
        for change in ({'accuracy': 600}, {'latitude': float('nan')}, {'source': 'DEFAULT'}):
            with self.subTest(change=change):
                (self.store.root / 'location-sample.json').write_text(json.dumps(dict(self.sample, **change)), encoding='utf-8')
                result = self.controller.engine.run(submit=True)
                self.assertEqual(result.state, 'location_required')
                self.assertIn('模拟', result.message)
                self.assertNotIn('打开 Wi-Fi', result.message)
                self.api.verify.assert_not_called()
                self.api.submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
