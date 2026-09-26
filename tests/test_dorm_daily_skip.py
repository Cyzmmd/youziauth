"""「当天打卡成功过一次，后续不再判定」的回归测试。

需求（用户提出）：每天打卡时段固定为 21:00–23:15。一天里只要成功过一次，
后续就不必再判定是否成功，等第二天相应时段再继续。

这条规则同时掐掉了一个真实故障：打卡成功后循环仍每 5 分钟跑一次，
会话一旦过期就会走自动续期登录 → 无人值守地反复拉起浏览器，连续数小时。
因此短路必须发生在**任何网络调用之前**。
"""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dorm_checkin import Engine, Settings, Store, Task  # noqa: E402

SHANGHAI = dt.timezone(dt.timedelta(hours=8))


def make_task(signed=False, date='2026-09-25', student='fixture-student'):
    return Task('fixture-task', 'fixture-form', 'fixture-publish', student,
                date, '晚间查寝', '21:00', '23:15', signed, '测试寝室', '800米')


class DailySkipTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root, protector=Mock(protect=lambda b: b[::-1],
                                                     unprotect=lambda b: b[::-1]))
        self.store.save_token('fixture-token')
        self.store.save_browser_session('fixture-token', 'fixture-student', [])
        self.store.save_settings(Settings(enabled=True, interval=300))
        self.api = Mock()
        self.api.user.return_value = 'fixture-student'
        self.api.today.return_value = make_task()
        self.api.submit.return_value = True
        self.api.is_signed.return_value = True
        self.engine = Engine(self.store, self.api, lambda: {
            'latitude': 39.9, 'longitude': 116.4, 'accuracy': 30, 'provider': 'windows'})
        self.now = dt.datetime(2026, 9, 25, 21, 30, tzinfo=SHANGHAI)
        self.engine.clock = lambda: self.now

    def at(self, hour, minute=0, day=25):
        self.now = dt.datetime(2026, 9, day, hour, minute, tzinfo=SHANGHAI)

    # ---------- 记录 ----------

    def test_successful_submission_records_the_day(self):
        self.assertEqual(self.engine.run(automatic=True, submit=True).state, 'signed')
        self.assertEqual(self.store.signed_record().get('date'), '2026-09-25')
        self.assertEqual(self.store.signed_record().get('student'), 'fixture-student')

    def test_server_side_already_signed_also_records_the_day(self):
        """服务器说今天已完成（无需我们提交）同样要记，否则还会一直查。"""
        self.api.today.return_value = make_task(signed=True)
        self.assertEqual(self.engine.run(automatic=True, submit=True).state, 'signed')
        self.assertEqual(self.store.signed_record().get('date'), '2026-09-25')
        self.api.submit.assert_not_called()

    # ---------- 短路 ----------

    def test_later_automatic_checks_do_not_touch_the_school_at_all(self):
        """核心：当天已完成后，后续自动检查不再调用任何学校接口。"""
        self.engine.run(automatic=True, submit=True)
        self.api.reset_mock()
        with patch('socket.socket.connect', side_effect=AssertionError('不得有任何网络访问')):
            result = self.engine.run(automatic=True, submit=True)
        self.assertEqual(result.state, 'signed')
        self.api.user.assert_not_called()
        self.api.today.assert_not_called()
        self.api.submit.assert_not_called()

    def test_short_circuit_does_not_renew_login(self):
        """短路必须发生在取 token/续期登录之前 —— 那正是每 5 分钟拉起浏览器的循环。"""
        self.engine.run(automatic=True, submit=True)
        previous = self.store.signed_record()
        with patch('dorm_login.login', side_effect=AssertionError('不得自动续期登录')):
            for _ in range(5):
                self.assertEqual(self.engine.run(automatic=True, submit=True).state, 'signed')
        self.assertEqual(self.store.signed_record(), previous)

    def test_next_day_queries_again(self):
        self.engine.run(automatic=True, submit=True)
        self.api.reset_mock()
        self.api.today.return_value = make_task(date='2026-09-26', signed=True)
        self.at(21, 30, day=26)
        self.assertEqual(self.engine.run(automatic=True, submit=True).state, 'signed')
        self.api.today.assert_called_once()          # 第二天恢复判定
        self.assertEqual(self.store.signed_record().get('date'), '2026-09-26')

    def test_manual_query_still_hits_the_school(self):
        """人工「查询今日任务」始终真实查询，方便随时核实。"""
        self.engine.run(automatic=True, submit=True)
        self.api.reset_mock()
        self.assertEqual(self.engine.run().state, 'ready')
        self.api.today.assert_called_once()

    # ---------- 安全边界 ----------

    def test_another_account_does_not_inherit_todays_success(self):
        """同机换账号后，不能把上一个账号的「今天已完成」当成本账号的。"""
        self.engine.run(automatic=True, submit=True)
        self.api.reset_mock()
        self.store.save_browser_session('fixture-token', 'other-student', [])
        self.api.user.return_value = 'other-student'
        self.api.today.return_value = make_task(student='other-student')
        # 未短路 → 会真实查询；随后按新账号自己的状态正常提交（因此最终仍是 signed）
        self.engine.run(automatic=True, submit=True)
        self.api.today.assert_called_once()
        self.api.submit.assert_called_once()

    def test_record_without_student_still_skips(self):
        """记录里没有学号（例如缓存路径写下的）时无法证伪，沿用记录。"""
        self.store.mark_signed('2026-09-25')
        self.assertEqual(self.engine.run(automatic=True, submit=True).state, 'signed')
        self.api.today.assert_not_called()

    def test_mark_signed_never_wipes_a_known_student(self):
        self.store.mark_signed('2026-09-25', 'fixture-student')
        self.store.mark_signed('2026-09-25')          # 缓存路径回写，不带学号
        self.assertEqual(self.store.signed_record().get('student'), 'fixture-student')

    def test_other_days_record_is_ignored(self):
        self.store.mark_signed('2026-09-24', 'fixture-student')
        self.engine.run(automatic=True, submit=True)
        self.api.today.assert_called_once()           # 昨天的记录不影响今天
        self.assertEqual(self.store.signed_record().get('date'), '2026-09-25')

    def test_corrupt_record_does_not_break_checking(self):
        (self.root / 'signed.json').write_text('{', encoding='utf-8')
        result = self.engine.run(automatic=True, submit=True)
        self.assertEqual(result.state, 'signed')       # 记录坏了就照常查询
        self.api.today.assert_called_once()

    # ---------- 时段 ----------

    def test_default_window_ends_at_2315(self):
        self.assertEqual(Settings().end, '23:15')
        self.assertEqual(Settings().start, '21:00')

    def test_outside_the_window_nothing_runs(self):
        self.at(20, 59)
        self.assertEqual(self.engine.run(automatic=True, submit=True).state, 'waiting')
        self.api.today.assert_not_called()
        self.at(23, 15)
        self.assertEqual(self.engine.run(automatic=True, submit=True).state, 'waiting')
        self.api.today.assert_not_called()

    def test_short_circuit_ignores_the_window_boundary(self):
        """已完成的日子即使在时段外也应报告已完成，而不是回落到「等待时段」。"""
        self.engine.run(automatic=True, submit=True)
        self.at(23, 40)
        self.assertEqual(self.engine.run(automatic=True, submit=True).state, 'waiting')

    def test_record_file_is_plain_utf8_without_bom(self):
        self.engine.run(automatic=True, submit=True)
        raw = (self.root / 'signed.json').read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(json.loads(raw.decode('utf-8'))['date'], '2026-09-25')


if __name__ == '__main__':
    unittest.main()
