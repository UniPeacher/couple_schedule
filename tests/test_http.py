"""Run: python3 -m unittest discover -s tests -v. All data is isolated from production."""
import http.client
import json
import os
from pathlib import Path
import re
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
        cls.temp = tempfile.TemporaryDirectory(prefix='http-tests-')
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
            payload = body if response_headers['Content-Type'].startswith('image/') else body.decode()
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
        for path in ('/', '/app.js', '/app.css', '/trips.js', '/api/health', '/api/week', '/api/month?month=2026-10'):
            with self.subTest(path=path):
                code, _, data = self.req(path)
                self.assertEqual(code, 200)
                if path.startswith('/api/week') or path.startswith('/api/month'):
                    self.assertNotIn('cycle_periods', json.dumps(data))
                    self.assertNotIn('cycle_days', json.dumps(data))

    def test_static_asset_routes_and_conditional_requests(self):
        for path, mime in [('/trips.js', 'text/javascript'), ('/manifest.webmanifest', 'application/manifest+json'),
                           ('/img/dog-couple.png', 'image/png'), ('/img/dog-couple.webp', 'image/webp')]:
            with self.subTest(path=path):
                code, headers, body = self.req(path, uid=None)
                self.assertEqual(code, 200)
                self.assertTrue(headers['Content-Type'].startswith(mime))
                self.assertTrue(body)
                code, cached, body = self.req(path, uid=None, headers={'If-None-Match': headers['ETag']})
                self.assertEqual(code, 304)
                self.assertEqual(cached['ETag'], headers['ETag'])
                self.assertFalse(body)
        self.assertEqual(self.req('/img/not-an-asset.webp', uid=None)[0], 404)

    def test_trips_script_loads_before_app_and_is_precached(self):
        _, _, page = self.req('/', uid=None)
        trips = re.search(r'<script src="(/trips\.js\?v=[^"]+)">', page)
        self.assertIsNotNone(trips)
        self.assertLess(trips.start(), page.index('<script src="/app.js?'))
        self.assertIn('"' + trips[1] + '"', self.req('/sw.js', uid=None)[2])


if __name__ == '__main__':
    unittest.main()
