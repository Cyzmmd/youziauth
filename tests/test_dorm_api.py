import datetime as dt
import importlib.util
import unittest

from dorm_checkin import CheckinError, SHANGHAI

PRESENT = importlib.util.find_spec('dorm_api') is not None
if PRESENT:
    from dorm_api import SwuApi


class FeatureExists(unittest.TestCase):
    def test_api_exists(self):
        self.assertTrue(PRESENT, 'School API adapter missing')


@unittest.skipUnless(PRESENT, 'API adapter missing')
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.record = dict(id='id-1', formId='form-1', tsrq='2026-09-21', cqzmc='晚间查寝',
                           qdkssj='21:00', qdjssj='23:30')
        self.selected = dict(xh='student', cqfbid='pub-1', qdjg='0', formId='dorm-1',
                             qsqddd='宿舍', qdbj='800米', qdsj=['21:00', '23:30'])
        self.replies = [dict(records=[self.record], total=1), self.selected,
                        dict(columnList=[])]
        self.api = SwuApi(transport=self.transport)
        self.current = dt.datetime(2026, 9, 21, 22, tzinfo=SHANGHAI)

    def transport(self, method, path, token, **kwargs):
        self.calls.append((method, path, token, kwargs))
        return self.replies.pop(0)

    def test_discovers_task_without_historical_fallback(self):
        task = self.api.today('secret', 'student', self.current)
        self.assertEqual(task.id, 'id-1')
        self.assertEqual(task.publish_id, 'pub-1')
        self.assertEqual(task.start, '21:00')
        self.assertEqual(self.calls[0][3]['form']['pageNum'], '1')

    def test_ambiguous_tasks_are_not_chosen_by_score(self):
        self.replies[0]['records'].append(dict(self.record, id='id-2'))
        with self.assertRaisesRegex(CheckinError, '多条'):
            self.api.today('secret', 'student', self.current)

    def test_old_tasks_are_ignored(self):
        self.record['tsrq'] = '2026-09-20'
        self.assertIsNone(self.api.today('secret', 'student', self.current))

    def test_missing_publish_id_stops_instead_of_defaulting(self):
        del self.selected['cqfbid']
        with self.assertRaises(CheckinError):
            self.api.today('secret', 'student', self.current)

    def test_mismatched_student_stops(self):
        self.selected['xh'] = 'another-student'
        with self.assertRaises(CheckinError):
            self.api.today('secret', 'student', self.current)

    def test_verify_false_stops(self):
        task = self.api.today('secret', 'student', self.current)
        self.replies = [dict(isArea=False)]
        with self.assertRaises(CheckinError):
            self.api.verify('secret', task, {})

    def test_verify_string_false_is_not_truthy_success(self):
        task = self.api.today('secret', 'student', self.current)
        self.replies = [dict(isArea='false')]
        with self.assertRaises(CheckinError):
            self.api.verify('secret', task, {})

    def test_readback_does_not_accept_save_message(self):
        task = self.api.today('secret', 'student', self.current)
        self.replies = [dict(self.selected, msg='保存成功')]
        self.assertFalse(self.api.is_signed('secret', task))
        self.replies = [dict(self.selected, qdjg='1')]
        self.assertTrue(self.api.is_signed('secret', task))

    def test_submit_reports_the_confirmation_the_school_writes_back(self):
        task = self.api.today('secret', 'student', self.current)
        self.replies = [dict(qdjg='1')]
        self.assertTrue(self.api.submit('secret', task, {}))
        self.replies = [dict(qdjg='0'), {}]
        self.assertFalse(self.api.submit('secret', task, {}))
        self.assertFalse(self.api.submit('secret', task, {}))

    def test_submit_uses_current_task_identifiers(self):
        task = self.api.today('secret', 'student', self.current)
        self.replies = [{}]
        self.api.submit('secret', task, {'latitude': 29.8, 'longitude': 106.4})
        body = self.calls[-1][3]['body']
        self.assertEqual(body['businessKey'], 'id-1')
        self.assertEqual(body['formId'], 'form-1')
        self.assertEqual(body['cqfbid'], 'pub-1')

    def test_user_response_must_contain_identity(self):
        self.replies = [{}]
        with self.assertRaises(CheckinError) as caught:
            self.api.user('secret')
        self.assertEqual(caught.exception.state, 'login_required')


if __name__ == '__main__':
    unittest.main()
