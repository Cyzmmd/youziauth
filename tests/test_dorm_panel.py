import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from dorm_checkin import Store, Result
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


if __name__ == '__main__':
    unittest.main()
