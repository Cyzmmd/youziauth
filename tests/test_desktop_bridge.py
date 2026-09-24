import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import dorm_points
from desktop_bridge import DesktopBridge, PreviewBridge
from campus_auth_gui import GuiSettings
from dorm_checkin import Result, Settings, Store, Task
from dorm_location import map_pick_sample


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
                                      sample_path=self.controller.store.root / 'location-sample.json',
                                      label='')
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

class SimulationMapTests(unittest.TestCase):
    """The map picker end of the bridge: model, save, undo and the coordinate-exposure rule."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'dorm',
                           protector=MagicMock(protect=lambda b: b[::-1], unprotect=lambda b: b[::-1]))
        self.store.save_settings(Settings(location_source='simulation'))
        task = Task('task-1', 'form-1', 'publish-1', 'student', '2026-09-21', '每日查寝', '21:00',
                    '23:30', False, '重庆市北碚区天生街道2号', '800米', 'dorm-form',
                    '29.821186', '106.426239')
        self.controller = MagicMock()
        self.controller.latest = Result('ready', '今日任务待完成', task)
        self.controller.busy = False
        self.controller.store = self.store
        self.controller.schedule_text.return_value = '自动打卡：关闭'
        self.controller.drain.return_value = []
        # An established store: the list exists, so the shipped starter positions are not seeded
        # (that behaviour has its own test) and these tests see a clean slate.
        self.store.save_points(dorm_points.empty())
        self.patches = [
            patch('desktop_bridge.gui.ensure_user_config', side_effect=lambda p: p),
            patch('desktop_bridge.gui.load_gui_settings', return_value=GuiSettings('student', '', 60)),
            patch('desktop_bridge.gui.is_startup_enabled', return_value=False),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.bridge = DesktopBridge(Path(self.tmp.name) / 'config.ini', dorm=self.controller)
        self.addCleanup(self.bridge._close)
        self.sample_path = self.store.root / 'location-sample.json'

    def test_the_model_reports_the_saved_point_and_the_school_reference(self):
        self.store.save_sample(map_pick_sample(29.823693, 106.422310))
        model = self.bridge.simulation_map()
        self.assertTrue(model['ok'])
        self.assertAlmostEqual(model['point']['latitude'], 29.823693, places=6)
        self.assertTrue(model['point']['picked'])
        self.assertEqual(model['reference']['address'], '重庆市北碚区天生街道2号')
        self.assertEqual(model['reference']['radius_m'], 800.0)
        self.assertLess(model['distance_m'], 40.0)
        self.assertTrue(model['in_range'])
        self.assertIn('openstreetmap.org', model['tile_url'])
        self.assertIn('OpenStreetMap', model['attribution'])

    def test_the_model_is_refused_while_the_saved_source_is_real_location(self):
        self.store.save_settings(Settings(location_source='windows'))
        model = self.bridge.simulation_map()
        self.assertFalse(model['ok'])
        self.assertIn('模拟定位', model['message'])

    def test_a_captured_sample_is_adopted_into_the_list_and_kept(self):
        """The original Windows sample must stay visible and selectable, never be overwritten."""
        self.store.save_sample(map_pick_sample(29.800000, 106.400000, accuracy=141.0))
        model = self.bridge.simulation_map()
        self.assertEqual([point['name'] for point in model['points']], ['本机采样样本'])
        self.assertEqual(model['points'][0]['source'], 'SAMPLE')
        self.assertEqual(model['active_id'], model['points'][0]['id'])
        self.assertAlmostEqual(model['point']['latitude'], 29.800000, places=6)

        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '李园一舍'})
        model = self.bridge.simulation_map()
        self.assertEqual([point['name'] for point in model['points']], ['本机采样样本', '李园一舍'])
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.823693, places=6)

        # Switching back restores the original sample, so nothing was destroyed by saving a pick.
        original = model['points'][0]['id']
        self.assertTrue(self.bridge.dispatch('simulation_point_select', {'id': original})['ok'])
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.800000, places=6)
        self.assertEqual(self.store.sample()['source'], 'SAMPLE',
                         'the adopted point keeps its provenance, and the payload still says windows')
        from dorm_location import locate
        self.assertEqual(locate('simulation', self.sample_path)['provider'], 'windows')

    def test_the_list_heals_a_sample_that_disagrees_with_it(self):
        """1.5.2 could leave the sample and the list out of step; the list must win."""
        self.store.save_points(dorm_points.add(
            dorm_points.empty(), name='李园一舍', latitude=29.827439, longitude=106.422612,
            accuracy=100.0, saved_at='2026-09-24T10:01:02+08:00'))
        self.store.save_sample(map_pick_sample(29.823693, 106.422310))
        (self.store.root / 'location-sample.previous.json').write_text('{}', encoding='utf-8')
        model = self.bridge.simulation_map()
        names = [point['name'] for point in model['points']]
        self.assertIn('李园一舍', names)
        self.assertIn('本机采样样本', names, 'the sample the program actually replays is adopted')
        self.assertEqual(names[model['points'].index(next(p for p in model['points'] if p['active']))],
                         '本机采样样本')
        self.assertFalse((self.store.root / 'location-sample.previous.json').exists(),
                         'the legacy undo file is dropped')
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.823693, places=6)

    def test_the_legacy_undo_file_is_adopted_instead_of_deleted(self):
        """1.5.2's 恢复上一个样本 file may hold the only copy of the original position."""
        self.store.save_points(dorm_points.add(
            dorm_points.empty(), name='李园一舍', latitude=29.827409, longitude=106.422742,
            accuracy=100.0, saved_at='2026-09-24T10:03:54+08:00'))
        self.store.save_sample(map_pick_sample(29.827409, 106.422742))
        (self.store.root / 'location-sample.previous.json').write_text(
            json.dumps(map_pick_sample(29.823693, 106.422310, accuracy=141.0)), encoding='utf-8')
        model = self.bridge.simulation_map()
        self.assertEqual([point['name'] for point in model['points']], ['李园一舍', '上一个样本'])
        adopted = model['points'][1]
        self.assertAlmostEqual(adopted['latitude'], 29.823693, places=6)
        self.assertEqual(adopted['source'], 'SAMPLE')
        self.assertEqual(model['active_id'], model['points'][0]['id'],
                         'the sample being replayed stays active')
        self.assertFalse((self.store.root / 'location-sample.previous.json').exists())
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.827409, places=6)

    def test_the_mirror_always_matches_the_active_point(self):
        self.store.save_points(dorm_points.add(
            dorm_points.empty(), name='李园一舍', latitude=29.827439, longitude=106.422612,
            accuracy=100.0, saved_at='2026-09-24T10:01:02+08:00'))
        self.assertIsNone(self.store.sample(), 'nothing to replay yet')
        model = self.bridge.simulation_map()
        self.assertEqual(model['active_id'], model['points'][0]['id'])
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.827439, places=6)
        self.assertAlmostEqual(model['point']['latitude'], 29.827439, places=6)

    def test_a_pick_is_saved_without_destroying_the_previous_position(self):
        self.store.save_sample(map_pick_sample(29.800000, 106.400000))
        result = self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693,
                                                                 'longitude': 106.422310,
                                                                 'name': '宿舍楼下'})
        self.assertTrue(result['ok'])
        self.assertIn('已新建选点「宿舍楼下」', result['message'])
        self.assertIn('距学校基准点', result['message'])
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.823693, places=6)
        self.assertEqual(self.store.sample()['source'], 'MAP_PICK')
        self.assertFalse((self.store.root / 'location-sample.previous.json').exists())

    def test_saving_the_same_name_moves_that_point_instead_of_adding_one(self):
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '宿舍楼下'})
        result = self.bridge.dispatch('simulation_point_save', {'latitude': 29.825000,
                                                                 'longitude': 106.424000,
                                                                 'name': '宿舍楼下'})
        self.assertTrue(result['ok'])
        self.assertIn('已更新选点「宿舍楼下」的位置', result['message'])
        model = self.bridge.simulation_map()
        self.assertEqual(len(model['points']), 1)
        self.assertAlmostEqual(model['points'][0]['latitude'], 29.825000, places=6)

    def test_a_pick_outside_the_radius_is_reported_as_such(self):
        result = self.bridge.dispatch('simulation_point_save', {'latitude': 29.850000, 'longitude': 106.422310})
        self.assertTrue(result['ok'])
        self.assertIn('超出', result['message'])
        self.assertIn('800 米', result['message'])

    def test_a_pick_is_saved_even_before_the_school_point_is_known(self):
        self.controller.latest = Result('idle', '尚未查询今日任务')
        result = self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310})
        self.assertTrue(result['ok'])
        self.assertIn('查询今日任务', result['message'])
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.823693, places=6)
        model = self.bridge.simulation_map()
        self.assertIsNone(model['reference'])
        self.assertIsNone(model['distance_m'])
        self.assertIsNone(model['in_range'])

    def test_a_pick_survives_the_replay_path(self):
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310})
        from dorm_location import locate
        position = locate('simulation', self.sample_path)
        self.assertEqual(position['provider'], 'windows')
        self.assertGreater(position['latitude'], 29.82)

    def test_unusable_picks_never_touch_the_stored_sample(self):
        self.store.save_sample(map_pick_sample(29.800000, 106.400000))
        for latitude, longitude in (('north', 106.4), (None, None), (91.0, 106.4), (29.8, 181.0)):
            with self.subTest(pick=(latitude, longitude)):
                result = self.bridge.dispatch('simulation_point_save',
                                              {'latitude': latitude, 'longitude': longitude})
                self.assertFalse(result['ok'])
                self.assertIn('选点坐标无效', result['message'])
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.800000, places=6)
        self.assertEqual(self.bridge.simulation_map()['points'][0]['name'], '本机采样样本')

    def test_a_pick_is_refused_while_a_check_in_is_running(self):
        self.controller.busy = True
        result = self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310})
        self.assertFalse(result['ok'])
        self.assertFalse(self.sample_path.exists())

    def test_renaming_the_adopted_sample_works_and_survives_switching(self):
        self.store.save_sample(map_pick_sample(29.800000, 106.400000))
        adopted = self.bridge.simulation_map()['points'][0]['id']
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '李园一舍'})
        result = self.bridge.dispatch('simulation_point_rename', {'id': adopted, 'name': '原始采样'})
        self.assertTrue(result['ok'])
        self.assertIn('原始采样', result['message'])
        model = self.bridge.simulation_map()
        self.assertEqual([point['name'] for point in model['points']], ['原始采样', '李园一舍'])
        self.assertEqual(model['active_id'], model['points'][1]['id'], 'renaming does not switch')

    def test_deleting_the_active_point_switches_to_the_next_one(self):
        self.store.save_sample(map_pick_sample(29.800000, 106.400000))
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '李园一舍'})
        model = self.bridge.simulation_map()
        active = next(point for point in model['points'] if point['active'])
        result = self.bridge.dispatch('simulation_point_delete', {'id': active['id']})
        self.assertTrue(result['ok'])
        self.assertIn('已自动切换到「本机采样样本」', result['message'])
        remaining = self.bridge.simulation_map()['points']
        self.assertEqual([point['name'] for point in remaining], ['本机采样样本'])
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.800000, places=6)

    def test_deleting_the_last_point_leaves_no_location_to_replay(self):
        self.store.save_sample(map_pick_sample(29.800000, 106.400000))
        adopted = self.bridge.simulation_map()['points'][0]['id']
        result = self.bridge.dispatch('simulation_point_delete', {'id': adopted})
        self.assertTrue(result['ok'])
        self.assertIn('没有可用位置', result['message'])
        self.assertFalse(self.sample_path.exists(), 'no point means nothing to replay')
        self.assertEqual(self.bridge.simulation_map()['points'], [])

    def test_named_points_are_listed_and_the_newest_becomes_active(self):
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '宿舍楼下'})
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.825000, 'longitude': 106.424000,
                                                        'name': '图书馆北门'})
        model = self.bridge.simulation_map()
        self.assertEqual([point['name'] for point in model['points']], ['宿舍楼下', '图书馆北门'])
        self.assertEqual([point['active'] for point in model['points']], [False, True])
        self.assertEqual(model['active_id'], model['points'][1]['id'])
        self.assertAlmostEqual(model['point']['latitude'], 29.825000, places=6)

    def test_an_unnamed_pick_gets_a_generated_name(self):
        result = self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310})
        self.assertTrue(result['ok'])
        self.assertIn('选点 1', result['message'])
        self.assertEqual(self.bridge.simulation_map()['points'][0]['name'], '选点 1')

    def test_switching_a_point_rewrites_the_replayed_sample(self):
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '宿舍楼下'})
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.825000, 'longitude': 106.424000,
                                                        'name': '图书馆北门'})
        model = self.bridge.simulation_map()
        first = model['points'][0]['id']
        result = self.bridge.dispatch('simulation_point_select', {'id': first})
        self.assertTrue(result['ok'])
        self.assertIn('宿舍楼下', result['message'])
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.823693, places=6)
        self.assertEqual(self.bridge.simulation_map()['active_id'], first)
        from dorm_location import locate
        position = locate('simulation', self.sample_path)
        self.assertEqual(position['provider'], 'windows')

    def test_renaming_a_point(self):
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '宿舍楼下'})
        point_id = self.bridge.simulation_map()['points'][0]['id']
        result = self.bridge.dispatch('simulation_point_rename', {'id': point_id, 'name': '宿舍东门'})
        self.assertTrue(result['ok'])
        self.assertIn('宿舍东门', result['message'])
        self.assertEqual(self.bridge.simulation_map()['points'][0]['name'], '宿舍东门')
        self.assertFalse(self.bridge.dispatch('simulation_point_rename', {'id': point_id, 'name': ''})['ok'])
        self.assertFalse(self.bridge.dispatch('simulation_point_rename', {'id': 'ghost', 'name': 'x'})['ok'])

    def test_deleting_an_idle_point_keeps_the_active_sample(self):
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '宿舍楼下'})
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.825000, 'longitude': 106.424000,
                                                        'name': '图书馆北门'})
        model = self.bridge.simulation_map()
        idle = model['points'][0]['id']
        self.assertTrue(self.bridge.dispatch('simulation_point_delete', {'id': idle})['ok'])
        after = self.bridge.simulation_map()
        self.assertEqual([point['name'] for point in after['points']], ['图书馆北门'])
        self.assertAlmostEqual(self.store.sample()['latitude'], 29.825000, places=6)

    def test_point_actions_are_refused_while_a_check_in_is_running(self):
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '宿舍楼下'})
        point_id = self.bridge.simulation_map()['points'][0]['id']
        self.controller.busy = True
        for action, payload in (('simulation_point_save', {'latitude': 29.82, 'longitude': 106.42}),
                                ('simulation_point_select', {'id': point_id}),
                                ('simulation_point_rename', {'id': point_id, 'name': 'x'}),
                                ('simulation_point_delete', {'id': point_id})):
            with self.subTest(action=action):
                self.assertFalse(self.bridge.dispatch(action, payload)['ok'])

    def test_the_snapshot_never_exposes_coordinates(self):
        self.store.save_sample(map_pick_sample(29.823693, 106.422310))
        self.bridge.dispatch('simulation_point_save', {'latitude': 29.823693, 'longitude': 106.422310,
                                                        'name': '宿舍楼下'})
        state = json.dumps(self.bridge.snapshot(), ensure_ascii=False)
        self.assertNotIn('latitude', state)
        self.assertNotIn('29.82', state)
        self.assertNotIn('宿舍楼下', state)

    def test_the_location_check_names_the_point_it_used(self):
        self.store.save_points(dorm_points.add(
            dorm_points.empty(), name='杏园三舍', latitude=29.827439, longitude=106.422612,
            accuracy=100.0, saved_at='2026-09-24T10:03:54+08:00'))
        self.store.save_sample(map_pick_sample(29.827439, 106.422612))
        self.assertTrue(self.bridge.dispatch('location_authorize')['ok'])
        state = {'state': 'checking'}
        for _ in range(100):
            state = self.bridge.snapshot()['location']
            if state['state'] != 'checking':
                break
            time.sleep(0.05)
        self.assertEqual(state['state'], 'ready')
        self.assertIn('当前使用选点「杏园三舍」', state['message'])
        self.assertIn('未提交打卡', state['message'])

    def test_a_fresh_store_starts_with_the_shipped_positions(self):
        """A new install already has positions to choose from; 模拟定位 itself stays opt-in."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'dorm', protector=MagicMock(protect=lambda b: b[::-1],
                                                                        unprotect=lambda b: b[::-1]))
            store.save_settings(Settings(location_source='simulation'))
            controller = MagicMock()
            controller.latest = Result('idle', '尚未查询今日任务')
            controller.busy = False
            controller.store = store
            controller.schedule_text.return_value = ''
            controller.drain.return_value = []
            with patch('desktop_bridge.gui.ensure_user_config', side_effect=lambda p: p), \
                 patch('desktop_bridge.gui.load_gui_settings', return_value=GuiSettings('student', '', 60)), \
                 patch('desktop_bridge.gui.is_startup_enabled', return_value=False):
                bridge = DesktopBridge(Path(directory) / 'config.ini', dorm=controller)
                try:
                    model = bridge.simulation_map()
                finally:
                    bridge._close()
            self.assertEqual([point['name'] for point in model['points']],
                             [entry['name'] for entry in dorm_points.DEFAULT_POINTS])
            self.assertEqual(model['active_id'], model['points'][0]['id'],
                             'the first shipped position is ready to use')
            self.assertAlmostEqual(store.sample()['latitude'],
                                   dorm_points.DEFAULT_POINTS[0]['latitude'], places=6)
            self.assertEqual(Settings().location_source, 'windows',
                             'a fresh install still defaults to real location')

    def test_shipped_positions_survive_being_deleted(self):
        """Built-ins are ordinary points: deleting them must not resurrect them."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'dorm', protector=MagicMock(protect=lambda b: b[::-1],
                                                                        unprotect=lambda b: b[::-1]))
            store.save_settings(Settings(location_source='simulation'))
            controller = MagicMock()
            controller.latest = Result('idle', '尚未查询今日任务')
            controller.busy = False
            controller.store = store
            controller.schedule_text.return_value = ''
            controller.drain.return_value = []
            with patch('desktop_bridge.gui.ensure_user_config', side_effect=lambda p: p), \
                 patch('desktop_bridge.gui.load_gui_settings', return_value=GuiSettings('student', '', 60)), \
                 patch('desktop_bridge.gui.is_startup_enabled', return_value=False):
                bridge = DesktopBridge(Path(directory) / 'config.ini', dorm=controller)
                try:
                    for point in bridge.simulation_map()['points']:
                        bridge.dispatch('simulation_point_delete', {'id': point['id']})
                    model = bridge.simulation_map()
                finally:
                    bridge._close()
            self.assertEqual(model['points'], [])
            self.assertEqual(model['active_id'], '')

    def test_the_preview_picker_uses_demo_data_only(self):
        preview = PreviewBridge()
        self.assertTrue(preview.dispatch('location_source_save', {'location_source': 'simulation'})['ok'])
        model = preview.simulation_map()
        self.assertTrue(model['ok'])
        self.assertIn('示例宿舍', model['reference']['address'])
        self.assertTrue(preview.dispatch('simulation_point_save',
                                        {'latitude': 29.823000, 'longitude': 106.422000,
                                         'name': '演示·图书馆'})['ok'])
        self.assertEqual([point['name'] for point in preview.simulation_map()['points']],
                         ['演示·宿舍楼下', '演示·图书馆'])
        self.assertTrue(preview.dispatch('simulation_point_delete',
                                         {'id': preview.simulation_map()['active_id']})['ok'])
        self.assertEqual(preview.simulation_map()['active_id'],
                         preview.simulation_map()['points'][0]['id'])
        self.assertFalse(preview.dispatch('simulation_point_save', {'latitude': 'x'})['ok'])
        self.assertFalse(preview.simulation_map()['ok'] if
                         preview.dispatch('location_source_save', {'location_source': 'windows'})['ok']
                         else True)


if __name__ == '__main__':
    unittest.main()
