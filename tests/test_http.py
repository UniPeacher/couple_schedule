"""Run: python3 -m unittest discover -s tests -v. All data is isolated from production."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class QuietHandler(app.Handler):
    def log_message(self, *args):
        pass


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='http-tests-', dir=os.environ['TMPDIR'])
        app.DATA_DIR = cls.temp.name
        app.DB_PATH = os.path.join(cls.temp.name, 'sched.db')
        app.SECRET = 'isolated-test-secret'
        app.init_db()
        cls.srv = app.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join()
        app.get_connection().close()
        app._TLOCAL.conn = None
        cls.temp.cleanup()

    def req(self, path, uid='a', data=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.srv.server_port, timeout=10)
        hdr = {'Content-Type': 'application/json'}
        if uid:
            hdr['Cookie'] = app.COOKIE + '=' + app.make_token(uid)
        hdr.update(headers or {})
        c.request('POST' if data is not None else 'GET', path, json.dumps(data) if data is not None else None, hdr)
        r = c.getresponse()
        status, response_headers, body = r.status, dict(r.getheaders()), r.read()
        c.close()
        try:
            payload = json.loads(body)
        except ValueError:
            payload = body.decode()
        return status, response_headers, payload

    def test_http_request_validation(self):
        self.assertEqual(self.req('/api/cycle2/settings', data={}, headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.req('/api/cycle2/settings', data=[])[0], 400)
        self.assertEqual(self.req('/api/cycle2/unknown')[0], 404)

    def test_notification_payloads_not_cached(self):
        for route in ('/api/messages', '/api/notifications', '/api/notifications/poll'):
            status, headers, _ = self.req(route)
            self.assertEqual(status, 200)
            self.assertIn('no-store', headers['Cache-Control'])

    def test_frontend_assets_and_existing_routes(self):
        for path in ('/', '/app.js', '/app.css', '/api/health', '/api/week', '/api/month?month=2026-10'):
            with self.subTest(path=path):
                code, _, data = self.req(path)
                self.assertEqual(code, 200)
                if path.startswith('/api/week') or path.startswith('/api/month'):
                    self.assertNotIn('cycle_periods', json.dumps(data))
                    self.assertNotIn('cycle_days', json.dumps(data))


if __name__ == '__main__':
    unittest.main()
