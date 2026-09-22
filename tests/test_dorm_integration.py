"""Offline end-to-end contract tests using a real local HTTP server."""
import datetime as dt
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import dorm_api
from dorm_checkin import Engine, Store, SHANGHAI


class SchoolFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if '/auth/user' in self.path:
            data = {'subject': {'username': 'fixture-student'}}
        else:
            data = dict(self.server.task_data, qdjg='1' if self.server.signed else '0')
        self.reply(data)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.server.calls.append((self.path, raw))
        if 'getTransitionByToday' in self.path:
            self.reply({'records': [dict(id='fixture-task', formId='fixture-form',
                                        tsrq='2026-09-21', cqzmc='测试查寝')], 'total': 1})
        elif 'getDormitory' in self.path:
            self.reply({'columnList': []})
        elif '/verify' in self.path:
            self.reply({'isArea': self.server.in_area})
        elif self.server.reject_save:
            # The school answers, but records nothing and keeps the task unsigned.
            self.reply({'qdjg': '0'})
        else:
            self.server.signed = True
            if self.server.drop_submit_response:
                self.close_connection = True
                return
            self.reply({'qdjg': '1'})

    def reply(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'code': 200, 'data': data}).encode())


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), SchoolFixture)
        self.server.calls = []
        self.server.signed = False
        self.server.in_area = True
        self.server.drop_submit_response = False
        self.server.reject_save = False
        self.server.task_data = dict(xh='fixture-student', cqfbid='fixture-publication',
                                    formId='fixture-form', qdsj=['21:00', '23:30'],
                                    qsqddd='测试宿舍', qdbj='800米', tsrq='2026-09-21')
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.origin = patch.object(dorm_api, 'ORIGIN', 'http://127.0.0.1:' + str(self.server.server_port))
        self.origin.start()
        self.addCleanup(self.origin.stop)
        self.store = Store(Path(self.temp.name), protector=Mock(protect=lambda b:b[::-1], unprotect=lambda b:b[::-1]))
        self.store.save_token('offline-fixture-token')
        self.engine = Engine(self.store, dorm_api.SwuApi(),
                             lambda: dict(latitude=29.8, longitude=106.4, accuracy=50),
                             clock=lambda: dt.datetime(2026, 9, 21, 22, tzinfo=SHANGHAI))

    def test_real_http_query_then_submit_then_duplicate_skip(self):
        self.assertEqual(self.engine.run().state, 'ready')
        self.assertFalse(self.server.signed)
        self.assertEqual(self.engine.run(submit=True).state, 'signed')
        self.assertEqual(self.engine.run(submit=True).state, 'signed')
        saves = [r for r in self.server.calls if '/save' in r[0]]
        self.assertEqual(len(saves), 1)
        self.assertEqual(json.loads(saves[0][1])['cqfbid'], 'fixture-publication')

    def test_lost_save_response_resolves_by_readback(self):
        self.server.drop_submit_response = True
        self.assertEqual(self.engine.run(submit=True).state, 'signed')

    def test_unrecorded_save_is_reported_and_then_retried(self):
        # Regression: one rejected POST used to leave the marker behind, which turned every
        # later attempt into a read-back only and cost the whole night's check-in.
        self.server.reject_save = True
        first = self.engine.run(submit=True)
        self.assertEqual(first.state, 'ready')
        self.assertIn('未生效', first.message)
        self.assertFalse(self.server.signed)
        self.server.reject_save = False
        self.assertEqual(self.engine.run(submit=True).state, 'signed')
        saves = [r for r in self.server.calls if '/save' in r[0]]
        self.assertEqual(len(saves), 2)

    def test_out_of_area_does_not_post_save(self):
        self.server.in_area = False
        self.assertEqual(self.engine.run(submit=True).state, 'location_required')
        self.assertFalse(any('/save' in p for p, _ in self.server.calls))


if __name__ == '__main__':
    unittest.main()
