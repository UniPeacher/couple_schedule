"""Run: python3 -m unittest discover -s tests -v. All data is isolated from production."""
import contextlib
import http.client
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cycle
import app

TODAY = date(2026, 10, 25)


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript(app.SCHEMA + cycle.SCHEMA)

    def tearDown(self):
        self.c.close()

    def write(self, action, data, uid='a'):
        return cycle.mutate(self.c, uid, action, data, TODAY)

    def view(self, uid='a'):
        return cycle.snapshot(self.c, uid, '2026-10', TODAY)

    def period(self, start='2026-10-01', end='2026-10-05', uid='a'):
        return self.write('period', dict(start_date=start, end_date=end), uid)['id']

    def history(self):
        self.period('2026-08-06', '2026-08-10')
        self.period('2026-09-03', '2026-09-07')
        self.period()

    def test_private_by_default_even_for_account_a(self):
        self.period(uid='b')
        self.write('day', dict(date='2026-10-02', note='secret', pain='重'), 'b')
        self.assertIsNone(self.view('a')['partner'])
        self.assertEqual(self.view('a')['calendar'], [])
        self.assertNotIn('secret', json.dumps(self.view('a')))

    def test_open_period_does_not_auto_end_or_fill_days(self):
        self.period('2026-10-01', None)
        s = self.view()
        self.assertIsNone(s['active']['end_date'])
        actual = [m for m in s['calendar'] if m['kind'] == 'actual']
        self.assertEqual([m['date'] for m in actual], ['2026-10-01'])

    def test_finish_and_update_period(self):
        rid = self.period('2026-10-01', None)
        self.write('period', dict(id=rid, start_date='2026-10-01', end_date='2026-10-06'))
        self.assertIsNone(self.view()['active'])
        self.assertEqual(self.view()['periods'][0]['end_date'], '2026-10-06')

    def test_cannot_edit_or_delete_partners_period(self):
        rid = self.period(uid='b')
        for action, data in [('period', dict(id=rid, start_date='2026-10-01')), ('period/delete', dict(id=rid))]:
            with self.assertRaises(cycle.Error) as e:
                self.write(action, data)
            self.assertEqual(e.exception.status, 404)
        self.assertEqual(len(self.view('b')['periods']), 1)

    def test_overlapping_and_multiple_open_records_rejected(self):
        self.period('2026-10-01', None)
        with self.assertRaises(cycle.Error):
            self.period('2026-10-10', None)
        with self.assertRaises(cycle.Error):
            self.period('2026-09-29', '2026-10-01')

    def test_invalid_dates_and_future_dates_rejected(self):
        for start, end in [('2026-2-01', None), ('2026-02-30', None), ('2026-10-26', None), ('2026-10-10', '2026-10-09'), ('2026-10-10', '2026-10-26')]:
            with self.subTest(start=start, end=end), self.assertRaises(cycle.Error):
                self.period(start, end)

    def test_prediction_insufficient_data(self):
        self.period()
        self.assertFalse(self.view()['prediction']['available'])

    def test_prediction_from_explicit_typical_cycle(self):
        self.period()
        self.write('settings', dict(typical_cycle=28))
        p = self.view()['prediction']
        self.assertTrue(p['available'])
        self.assertIn('历史记录不足', p['basis'])
        self.assertEqual(p['start'], '2026-10-26')

    def test_prediction_from_median_and_actuals_unchanged(self):
        self.history()
        s = self.view()
        self.assertEqual(s['prediction']['start'], '2026-10-27')
        self.assertEqual(s['prediction']['end'], '2026-10-31')
        self.assertEqual(s['prediction']['estimated_duration'], 5)
        self.assertEqual(len(s['periods']), 3)

    def test_prediction_recalculates_after_edit(self):
        self.history()
        rid = self.view()['periods'][0]['id']
        self.write('period', dict(id=rid, start_date='2026-10-03', end_date='2026-10-07'))
        self.assertEqual(self.view()['prediction']['start'], '2026-10-30')

    def test_irregular_and_missing_history_suppresses_prediction(self):
        self.period('2026-07-01', '2026-07-05')
        self.period()
        self.write('settings', dict(typical_cycle=28))
        self.assertFalse(self.view()['prediction']['available'])

    def test_expired_prediction_not_rolled_forward(self):
        self.period('2026-08-01', '2026-08-05')
        self.write('settings', dict(typical_cycle=28))
        p = self.view()['prediction']
        self.assertFalse(p['available'])
        self.assertIn('不代表推迟', p['reason'])

    def test_prediction_can_be_disabled(self):
        self.history()
        self.write('settings', dict(prediction_enabled=False))
        self.assertFalse(self.view()['prediction']['available'])
        self.assertFalse(any(m['kind'] == 'predicted' for m in self.view()['calendar']))

    def test_actual_sharing_cutoff_no_private_fields(self):
        self.period()
        self.write('day', dict(date='2026-10-03', note='private note', mood='private mood', pain='重'))
        self.write('settings', dict(share_actual=True, share_since='2026-10-03'))
        partner = self.view('b')['partner']
        self.assertEqual(partner['periods'], [dict(start_date='2026-10-03', end_date='2026-10-05', start_clipped=True)])
        self.assertNotIn('prediction', partner)
        self.assertNotIn('private', json.dumps(partner))

    def test_prediction_share_independent_of_actual(self):
        self.history()
        self.write('settings', dict(share_prediction=True))
        p = self.view('b')['partner']
        self.assertNotIn('periods', p)
        self.assertNotIn('basis', p['prediction'])
        self.assertNotIn('estimated_duration', p['prediction'])

    def test_revoke_sharing_immediately_removes_partner_payload(self):
        self.period()
        self.write('settings', dict(share_actual=True, share_since='2026-01-01'))
        self.assertIsNotNone(self.view('b')['partner'])
        self.write('settings', dict(share_actual=False, share_prediction=False))
        self.assertIsNone(self.view('b')['partner'])
        self.assertEqual(self.view('b')['calendar'], [])

    def test_daily_missing_vs_explicit_none_and_delete(self):
        self.assertEqual(self.view()['days'], [])
        self.write('day', dict(date='2026-10-02', pain='无', symptoms=['疲惫'], note='<script>'))
        day = self.view()['days'][0]
        self.assertEqual(day['pain'], '无')
        self.assertEqual(day['symptoms'], ['疲惫'])
        self.write('day/delete', dict(date='2026-10-02'))
        self.assertEqual(self.view()['days'], [])

    def test_care_independent_revocable_acknowledged_and_expires(self):
        self.write('care', dict(message='想要陪伴', expires_on='2026-10-25'))
        self.assertIsNone(self.view('b')['partner'].get('periods'))
        self.write('care/ack', {}, 'b')
        self.assertEqual(self.view()['care']['acknowledged'], 1)
        tomorrow = cycle.snapshot(self.c, 'b', '2026-10', TODAY + timedelta(days=1))
        self.assertIsNone(tomorrow['partner'])
        self.write('care', dict(message=''))
        self.assertIsNone(self.view('b')['partner'])
        with self.assertRaises(cycle.Error):
            self.write('care/ack', {}, 'b')

    def test_export_and_delete_only_owner(self):
        self.period()
        self.period(uid='b')
        self.write('day', dict(date='2026-10-02', note='private'))
        self.write('care', dict(message='rest', expires_on='2026-10-25'))
        self.write('settings', dict(share_actual=True))
        data = cycle.export_data(self.c, 'a')
        self.assertEqual(data['days'][0]['note'], 'private')
        self.assertTrue(all(p['uid'] == 'a' for p in data['periods']))
        with self.assertRaises(cycle.Error):
            self.write('delete-all', {})
        self.write('delete-all', dict(confirm='DELETE_MY_CYCLE_DATA'))
        self.assertEqual(self.view()['periods'], [])
        self.assertEqual(self.view()['days'], [])
        self.assertEqual(len(self.view('b')['periods']), 1)
        self.assertIsNone(self.view('b')['partner'])

    def test_generic_reminder_owner_only_deduplicated_and_cleared(self):
        self.history()
        self.write('settings', dict(reminders_enabled=True, reminder_days=2))
        now = datetime(2026, 10, 25, 10)
        self.assertEqual(cycle.reminder_pass(self.c, now), 1)
        self.assertEqual(cycle.reminder_pass(self.c, now), 0)
        r = self.c.execute('SELECT * FROM notifications').fetchone()
        self.assertEqual(r['target_uid'], 'a')
        self.assertNotIn('经期', r['content'])
        self.assertNotIn('2026', r['content'])
        self.write('settings', dict(reminders_enabled=False))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], 0)

    def test_input_validation(self):
        bad = [('settings', {'share_actual': 'false'}), ('settings', {'typical_cycle': True}),
               ('settings', {'uid': 'b'}), ('day', {'date': '2026-10-01', 'symptoms': ['unknown']}),
               ('care', {'message': 'a', 'expires_on': '2026-11-30'}), ('period', [])]
        for action, data in bad:
            with self.subTest(action=action, data=data), self.assertRaises(cycle.Error):
                self.write(action, data)

    def test_remote_backup_strips_health_data_and_free_pages(self):
        self.period()
        self.write('day', dict(date='2026-10-02', note='HEALTH_SECRET_SENTINEL'))
        self.write('care', dict(message='CARE_SECRET_SENTINEL', expires_on='2026-10-25'))
        self.c.execute("INSERT INTO events(uid,title,tstart,tend) VALUES('a','ordinary schedule','10:00','11:00')")
        self.c.commit()
        with tempfile.TemporaryDirectory(prefix='cycle-backup-', dir=os.environ['TMPDIR']) as folder:
            src_path, dst_path = os.path.join(folder, 'source.db'), os.path.join(folder, 'safe.db')
            with contextlib.closing(sqlite3.connect(src_path)) as src:
                self.c.backup(src)
            cycle.prepare_remote_backup(src_path, dst_path)
            with contextlib.closing(sqlite3.connect(dst_path)) as dst:
                self.assertEqual(dst.execute('SELECT COUNT(*) FROM cycle_days').fetchone()[0], 0)
                self.assertEqual(dst.execute('SELECT COUNT(*) FROM cycle_care').fetchone()[0], 0)
                self.assertEqual(dst.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
            self.assertNotIn(b'HEALTH_SECRET_SENTINEL', Path(dst_path).read_bytes())
            self.assertNotIn(b'CARE_SECRET_SENTINEL', Path(dst_path).read_bytes())
            with contextlib.closing(sqlite3.connect(src_path)) as src:
                self.assertEqual(src.execute('SELECT COUNT(*) FROM cycle_days').fetchone()[0], 1)
            with self.assertRaises(ValueError):
                cycle.prepare_remote_backup(src_path, src_path)

    def test_migration_is_idempotent(self):
        self.period()
        self.c.executescript(cycle.SCHEMA)
        self.assertEqual(len(self.view()['periods']), 1)


class QuietHandler(app.Handler):
    def log_message(self, *args):
        pass


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='cycle-http-', dir=os.environ['TMPDIR'])
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

    def test_http_auth_cache_and_export(self):
        self.assertEqual(self.req('/api/cycle', uid=None)[0], 401)
        code, headers, payload = self.req('/api/cycle')
        self.assertEqual(code, 200)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertEqual(payload['uid'], 'a')
        code, headers, payload = self.req('/api/cycle/export')
        self.assertEqual(code, 200)
        self.assertIn('attachment', headers['Content-Disposition'])

    def test_http_crud_authorization_and_readback(self):
        today = date.today().isoformat()
        code, _, r = self.req('/api/cycle/period', data={'start_date': today})
        self.assertEqual(code, 200)
        rid = r['id']
        self.assertTrue(any(p['id'] == rid for p in self.req('/api/cycle')[2]['periods']))
        self.assertEqual(self.req('/api/cycle/period/delete', 'b', {'id': rid})[0], 404)
        self.assertEqual(self.req('/api/cycle/period/delete', data={'id': rid})[0], 200)
        self.assertFalse(any(p['id'] == rid for p in self.req('/api/cycle')[2]['periods']))

    def test_http_cross_origin_content_type_and_errors(self):
        self.assertEqual(self.req('/api/cycle/settings', data={}, headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.req('/api/cycle/settings', data={}, headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.req('/api/cycle/settings', data=[])[0], 400)
        self.assertEqual(self.req('/api/cycle?month=bad')[0], 400)
        self.assertEqual(self.req('/api/cycle/unknown')[0], 404)

    def test_http_collaboration_atomic_rollback(self):
        before = self.req('/api/cycle', 'b')[2]['settings']
        status, _, _ = self.req('/api/cycle/sharing', 'b', dict(settings=dict(share_actual=True), collaboration=dict(allow_proxy='yes')))
        self.assertEqual(status, 400)
        after = self.req('/api/cycle', 'b')[2]['settings']
        self.assertEqual(before, after)

    def test_http_proxy_roundtrip_and_owner_confirmation(self):
        self.assertEqual(self.req('/api/cycle/sharing', 'b', dict(settings=dict(share_actual=True, share_prediction=True, share_since='2026-01-01'), collaboration=dict(allow_proxy=True, share_details=True)))[0], 200)
        today = date.today().isoformat()
        status, _, proposal = self.req('/api/cycle/proposal', 'a', dict(start_date=today))
        self.assertEqual(status, 200)
        self.assertEqual(self.req('/api/cycle', 'b')[2]['periods'], [])
        self.assertEqual(self.req('/api/cycle/proposal/confirm', 'a', dict(id=proposal['id']))[0], 404)
        self.assertEqual(self.req('/api/cycle/proposal/confirm', 'b', dict(id=proposal['id']))[0], 200)
        own = self.req('/api/cycle', 'b')[2]
        self.assertEqual(own['periods'][0]['source']['author_uid'], 'a')
        self.assertEqual(own['periods'][0]['source']['confirmed_by'], 'b')
        self.assertEqual(self.req('/api/cycle/delete-all', 'b', dict(confirm='DELETE_MY_CYCLE_DATA'))[0], 200)
        self.assertIsNone(self.req('/api/cycle', 'a')[2]['partner'])

    def test_notification_payloads_not_cached(self):
        for route in ('/api/messages', '/api/notifications', '/api/notifications/poll'):
            status, headers, _ = self.req(route)
            self.assertEqual(status, 200)
            self.assertIn('no-store', headers['Cache-Control'])

    def test_frontend_assets_and_existing_routes(self):
        for path in ('/', '/cycle.js', '/cycle.css', '/api/health', '/api/week', '/api/month?month=2026-10'):
            with self.subTest(path=path):
                code, _, data = self.req(path)
                self.assertEqual(code, 200)
                if path.startswith('/api/week') or path.startswith('/api/month'):
                    self.assertNotIn('cycle_periods', json.dumps(data))
                    self.assertNotIn('cycle_days', json.dumps(data))


if __name__ == '__main__':
    unittest.main()
