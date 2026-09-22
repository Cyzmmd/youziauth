import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

PRESENT = importlib.util.find_spec('dorm_checkin') is not None
if PRESENT:
    from dorm_checkin import Settings, Store, Engine, Task, Result, SHANGHAI


class FeatureExists(unittest.TestCase):
    def test_feature_exists(self):
        self.assertTrue(PRESENT, 'Independent dorm check-in engine is not implemented')


@unittest.skipUnless(PRESENT, 'Engine is not implemented')
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name), protector=Mock(
            protect=lambda b: b[::-1], unprotect=lambda b: b[::-1]))
        self.now = dt.datetime(2026, 9, 21, 22, 0, tzinfo=SHANGHAI)
        self.task = Task('task-1', 'form-1', 'publish-1', 'student',
                         '2026-09-21', '今日查寝', '21:00', '23:30', False,
                         '宿舍', '800米', 'dorm-form')
        self.api = Mock()
        self.api.user.return_value = 'student'
        self.api.today.return_value = self.task
        self.api.is_signed.return_value = True
        self.position = Mock(return_value={'latitude': 29.8, 'longitude': 106.4})
        self.engine = Engine(self.store, self.api, self.position, clock=lambda: self.now)
        self.store.save_token('private-token')

    def test_default_disabled_and_no_school_requests(self):
        self.assertFalse(self.store.settings().enabled)
        self.assertEqual(self.engine.run(automatic=True).state, 'disabled')
        self.api.user.assert_not_called()

    def test_location_source_defaults_to_windows_for_existing_settings(self):
        self.store._write('settings.json', {'enabled': False, 'start': '21:00', 'end': '23:30', 'interval': 300})
        self.assertEqual(getattr(Store(self.store.root).settings(), 'location_source', None), 'windows')

    def test_location_source_is_persisted_and_validated(self):
        self.assertIn('location_source', Settings.__dataclass_fields__)
        self.store.save_settings(Settings(location_source='simulation'))
        self.assertEqual(Store(self.store.root).settings().location_source, 'simulation')
        for source in ('unknown', '', None):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.store.save_settings(Settings(location_source=source))

    def test_query_never_submits_or_reads_position(self):
        result = self.engine.run()
        self.assertEqual(result.state, 'ready')
        self.api.submit.assert_not_called()
        self.position.assert_not_called()

    def test_query_with_an_old_pending_task_never_submits_a_new_task(self):
        import dataclasses
        previous = dataclasses.replace(self.task, id='previous-task', date='2026-09-20')
        self.store.set_pending(previous.key)
        self.assertEqual(self.engine.run(submit=False).state, 'ready')
        self.assertTrue(self.store.pending(previous.key))
        self.api.submit.assert_not_called()
        self.position.assert_not_called()

    def test_signed_task_is_skipped(self):
        import dataclasses
        self.api.today.return_value = dataclasses.replace(self.task, signed=True)
        self.assertEqual(self.engine.run(submit=True).state, 'signed')
        self.api.submit.assert_not_called()

    def test_expired_task_never_submitted(self):
        self.now = self.now.replace(hour=23, minute=45)
        self.assertEqual(self.engine.run(submit=True).state, 'expired')
        self.api.submit.assert_not_called()

    def test_previous_day_never_submitted(self):
        import dataclasses
        self.api.today.return_value = dataclasses.replace(self.task, date='2026-09-20')
        self.assertEqual(self.engine.run(submit=True).state, 'error')
        self.api.submit.assert_not_called()

    def test_success_requires_readback(self):
        self.api.is_signed.return_value = False
        self.assertEqual(self.engine.run(submit=True).state, 'uncertain')
        self.api.is_signed.assert_called_once()
        self.assertEqual(self.store.pending(), self.task.key)

    def test_timeout_is_read_back_and_not_resubmitted_even_after_restart(self):
        self.api.submit.side_effect = TimeoutError()
        self.api.is_signed.return_value = False
        self.assertEqual(self.engine.run(submit=True).state, 'uncertain')
        other = Engine(self.store, self.api, self.position, clock=lambda: self.now)
        self.assertEqual(other.run(submit=True).state, 'uncertain')
        self.assertEqual(self.api.submit.call_count, 1)

    def test_readback_can_resolve_a_timeout(self):
        self.api.submit.side_effect = TimeoutError()
        self.assertEqual(self.engine.run(submit=True).state, 'signed')
        self.assertEqual(self.store.pending(), '')

    def test_location_error_cannot_submit(self):
        from dorm_checkin import CheckinError
        self.position.side_effect = CheckinError('location_required', '无法定位')
        self.assertEqual(self.engine.run(submit=True).state, 'location_required')
        self.api.submit.assert_not_called()
        self.assertEqual(self.store.pending(), '')

    def test_missing_token_requires_login_without_request(self):
        self.store.clear_token()
        self.assertEqual(self.engine.run().state, 'login_required')
        self.api.user.assert_not_called()

    def test_automatic_execution_is_throttled(self):
        self.store.save_settings(Settings(enabled=True))
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.api.today.call_count, 1)

    def test_login_failure_does_not_leak_exception_or_token(self):
        self.api.user.side_effect = ValueError('private-token')
        result = self.engine.run()
        self.assertNotIn('private-token', result.message)
        self.assertNotIn('private-token', self.store.history())

    def test_corrupt_settings_fail_closed(self):
        (self.store.root / 'settings.json').write_text('{')
        self.assertEqual(self.engine.run(automatic=True).state, 'error')
        self.api.user.assert_not_called()

    def test_token_is_not_saved_as_plaintext(self):
        self.assertEqual(self.store.token(), 'private-token')
        for f in self.store.root.rglob('*'):
            if f.is_file():
                self.assertNotIn(b'private-token', f.read_bytes())

    def test_cancel_before_submit(self):
        self.engine.cancel.set()
        self.assertEqual(self.engine.run(submit=True).state, 'cancelled')
        self.api.submit.assert_not_called()

    def test_invalid_schedule_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save_settings(Settings(start='23:00', end='21:00'))

    def test_account_switch_preserves_each_uncertain_submission(self):
        import dataclasses
        self.api.is_signed.return_value = False
        self.assertEqual(self.engine.run(submit=True).state, 'uncertain')
        original = self.task
        self.api.user.return_value = 'other'
        self.api.today.return_value = dataclasses.replace(original, student='other', id='task-2')
        self.assertEqual(self.engine.run(submit=True).state, 'uncertain')
        self.api.user.return_value = 'student'
        self.api.today.return_value = original
        self.assertEqual(self.engine.run(submit=True).state, 'uncertain')
        self.assertEqual(self.api.submit.call_count, 2)

    def test_cancel_during_marker_write_does_not_submit_or_leave_pending(self):
        write = self.store.set_pending
        def cancel_after_write(key):
            write(key)
            self.engine.cancel.set()
        self.store.set_pending = cancel_after_write
        self.assertEqual(self.engine.run(submit=True).state, 'cancelled')
        self.api.submit.assert_not_called()
        self.assertEqual(self.store.pending(), '')

    def test_window_change_during_marker_write_does_not_submit(self):
        self.store.save_settings(Settings(enabled=True))
        write = self.store.set_pending
        def change_after_write(key):
            write(key)
            self.store.save_settings(Settings(enabled=True, start='22:30'))
        self.store.set_pending = change_after_write
        self.assertEqual(self.engine.run(submit=True, automatic=True).state, 'waiting')
        self.api.submit.assert_not_called()
        self.assertEqual(self.store.pending(), '')

    def test_expiry_during_marker_write_does_not_submit(self):
        write = self.store.set_pending
        def expire_after_write(key):
            write(key)
            self.now = self.now.replace(hour=23, minute=31)
        self.store.set_pending = expire_after_write
        self.assertEqual(self.engine.run(submit=True).state, 'expired')
        self.api.submit.assert_not_called()
        self.assertEqual(self.store.pending(), '')


if __name__ == '__main__':
    unittest.main()
