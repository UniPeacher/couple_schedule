"""M2 tests: v2 collaboration semantics, notifications and HTTP wiring.

Run: TMPDIR=/tmp python3 -m unittest discover -s tests -v
"""
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import cycle
import cycle2
import cycle2_collaboration as collab

TODAY = date(2026, 10, 25)


def period(start, end=None):
    return {'start_date': start, 'end_date': end}


class Base(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript(app.SCHEMA + cycle.SCHEMA + cycle2.SCHEMA_V2)
        collab.ensure_schema(self.c)

    def tearDown(self):
        self.c.close()

    def write(self, action, data, uid='a'):
        return collab.mutate(self.c, uid, action, data, TODAY)

    def seed(self):
        """Recorder `a` with three confirmed periods. Grants nothing."""
        for s, e in [('2026-10-03', '2026-10-07'), ('2026-09-05', '2026-09-09'),
                     ('2026-08-08', '2026-08-12')]:
            cycle2.mutate(self.c, 'a', 'period', period(s, e), TODAY)

    def let_share(self, since='2026-08-01', actual=True, prediction=False):
        """Open the date range from `a` to `b` (prerequisite for any collaboration grant)."""
        body = {'share_actual': actual, 'share_prediction': prediction}
        if since:
            body['share_since'] = since
        return self.write('settings', body, 'a')

    def grant(self, **flags):
        return self.write('collaboration', flags, 'a')

    def follow(self, uid='b', **over):
        body = dict(enabled=True, role='supporter', upcoming=True, pms=True)
        body.update(over)
        return collab.mutate(self.c, uid, 'preferences', body, TODAY)

    def view(self, uid='a', month='2026-10'):
        return collab.snapshot(self.c, uid, month, TODAY)

    def notices(self, uid='b'):
        return [dict(r) for r in self.c.execute(
            'SELECT * FROM notifications WHERE target_uid=? ORDER BY id', (uid,))]


class SharingTests(Base):
    def test_private_until_granted(self):
        self.seed()
        self.assertIsNone(self.view('b')['partner'])
        self.assertEqual(self.view('b')['calendar'], [])

    def test_granted_actual_only(self):
        self.seed()
        self.let_share(actual=True, prediction=False)
        self.grant()
        partner = self.view('b')['partner']
        self.assertIsNotNone(partner)
        self.assertEqual(len(partner['periods']), 3)
        self.assertNotIn('prediction', partner)
        self.assertNotIn('today', partner)

    def test_prediction_grant_reveals_window_not_statistics(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.grant()
        partner = self.view('b')['partner']
        pred = partner['prediction']
        self.assertTrue(pred['available'])
        for leaked in ('basis', 'cycle_length', 'confidence', 'luteal_length'):
            self.assertNotIn(leaked, pred)
        self.assertIn('window_start', pred)

    def test_phase_shared_only_with_prediction_grant(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.grant()
        self.assertIn('today', self.view('b')['partner'])
        # revoke prediction: phase must disappear with it
        self.let_share(actual=True, prediction=False)
        self.assertNotIn('today', self.view('b')['partner'])

    def test_share_since_clips_history(self):
        self.seed()
        self.let_share(since='2026-09-01')
        shared = self.view('b')['partner']['periods']
        clipped = [r for r in shared if r['start_clipped']]
        self.assertTrue(clipped == [] or all(r['start_clipped'] for r in clipped))
        self.assertTrue(all(r['start_date'] >= '2026-09-01' for r in shared))

    def test_revoke_hides_immediately(self):
        self.seed()
        self.let_share()
        self.grant()
        self.assertIsNotNone(self.view('b')['partner'])
        self.write('settings', {'share_actual': False, 'share_prediction': False}, 'a')
        self.assertIsNone(self.view('b')['partner'])

    def test_own_view_always_complete(self):
        self.seed()
        own = self.view('a')
        self.assertEqual(own['today']['phase'], 'luteal')
        self.assertEqual(len(own['periods']), 3)
        self.assertIn('insights', own)


class ProxyTests(Base):
    def test_proxy_requires_explicit_grant(self):
        self.seed()
        self.let_share()   # share dates but not allow_proxy
        self.grant()
        with self.assertRaises(cycle.Error) as e:
            self.write('proposal', period('2026-10-20', '2026-10-24'), 'b')
        self.assertEqual(e.exception.status, 403)

    def test_proxy_flow_confirm(self):
        self.seed()
        self.let_share()
        self.grant(allow_proxy=True)
        r = self.write('proposal', period('2026-10-20', '2026-10-24'), 'b')
        self.assertTrue(r['ok'])
        before = len(cycle2.periods(self.c, 'a'))
        self.assertEqual(len(cycle2.periods(self.c, 'a')), before)  # pending counts nothing
        self.write('proposal/confirm', {'id': r['id']}, 'a')
        after = cycle2.periods(self.c, 'a')
        self.assertEqual(len(after), before + 1)
        src = self.c.execute('SELECT * FROM cycle2_record_sources WHERE period_id=?',
                             (after[0]['id'],)).fetchone()
        self.assertEqual(src['author_uid'], 'b')
        self.assertEqual(src['confirmed_by'], 'a')

    def test_proxy_cancel_and_reject(self):
        self.seed()
        self.let_share()
        self.grant(allow_proxy=True)
        r = self.write('proposal', period('2026-10-20', '2026-10-24'), 'b')
        self.write('proposal/cancel', {'id': r['id']}, 'b')
        self.assertEqual(len(cycle2.periods(self.c, 'a')), 3)
        r2 = self.write('proposal', period('2026-10-21', '2026-10-24'), 'b')
        self.write('proposal/reject', {'id': r2['id']}, 'a')
        self.assertEqual(len(cycle2.periods(self.c, 'a')), 3)

    def test_revoking_proxy_revokes_pending(self):
        self.seed()
        self.let_share()
        self.grant(allow_proxy=True)
        self.write('proposal', period('2026-10-20', '2026-10-24'), 'b')
        self.grant(allow_proxy=False)
        row = self.c.execute("SELECT status FROM cycle2_proposals").fetchone()
        self.assertEqual(row['status'], 'revoked')

    def test_stale_proposal_cannot_overwrite_edited_record(self):
        self.seed()
        self.let_share()
        self.grant(allow_proxy=True)
        rid = cycle2.periods(self.c, 'a')[0]['id']
        p = self.write('proposal', {'period_id': rid, 'start_date': '2026-10-03',
                                    'end_date': '2026-10-06'}, 'b')
        # Edit through the collaboration layer, which assigns a fresh version stamp.
        self.write('period', {'id': rid, 'start_date': '2026-10-03',
                              'end_date': '2026-10-08'}, 'a')
        with self.assertRaises(cycle.Error) as e:
            self.write('proposal/confirm', {'id': p['id']}, 'a')
        self.assertEqual(e.exception.status, 409)

    def test_proposal_outside_share_range_rejected(self):
        self.seed()
        self.let_share(since='2026-09-01')
        self.grant(allow_proxy=True)
        with self.assertRaises(cycle.Error):
            self.write('proposal', period('2026-08-20'), 'b')


class NoticeTests(Base):
    def run_pass(self, day=None):
        when = datetime.combine(day or TODAY, datetime.min.time()).replace(hour=10)
        return collab.reminder_pass(self.c, when)

    def window(self):
        return collab.snapshot(self.c, 'a', '2026-10', TODAY)['prediction']

    def in_window(self, offset=0):
        """A day inside the predicted window, where the upcoming notice is due."""
        return date.fromisoformat(self.window()['window_start']) + timedelta(days=offset)

    def test_upcoming_notice_once_per_cycle(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.grant(allow_notifications=True)
        self.follow()
        day = self.in_window()
        n1 = self.run_pass(day)
        self.assertGreaterEqual(n1, 1)
        n2 = self.run_pass(day)
        self.assertEqual(n2, 0)
        self.assertEqual(len({x['content'] for x in self.notices()}), 1)

    def test_pms_notice_fires_before_period(self):
        """Luteal tail before the predicted window must produce a PMS notice."""
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.grant(allow_notifications=True)
        self.follow()
        start = date.fromisoformat(self.window()['window_start'])
        # The lead is capped by the luteal length, so scan the whole pre-period tail.
        for back in range(1, 8):
            self.run_pass(start - timedelta(days=back))
        kinds = [r['content'] for r in self.notices()]
        self.assertTrue(any('经前期' in k for k in kinds), kinds)

    def test_pms_requires_prediction_grant(self):
        self.seed()
        self.let_share(actual=True, prediction=False)
        self.grant(allow_notifications=True)
        self.follow()
        for back in range(1, 8):
            self.run_pass(self.in_window(-back))
        self.assertFalse(any('经前期' in r['content'] for r in self.notices()))

    def test_no_notice_without_following_role(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.grant(allow_notifications=True)
        self.run_pass(self.in_window())
        self.assertEqual(self.notices(), [])

    def test_started_notice_on_today_record(self):
        self.let_share(actual=True, prediction=True)
        self.grant(allow_notifications=True)
        self.follow()
        self.write('period', period('2026-10-25'), 'a')
        # Neutral by default: the notice must arrive without disclosing detail text.
        self.assertTrue(self.notices())
        self.assertEqual(self.notices()[0]['target_uid'], 'b')
        self.assertEqual(self.notices()[0]['target_action'], 'view:cycle2')

    def test_discomfort_notice_dedup_and_clear(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.grant(allow_notifications=True, share_details=True)
        self.follow()
        self.write('day', {'date': '2026-10-25', 'pain': '重'}, 'a')
        # Without a detail grant the partner sees one neutral notice, not the symptom.
        self.assertEqual(len(self.notices()), 1)
        self.assertNotIn('疼痛', self.notices()[0]['content'])
        # Editing down clears the pending notice instead of leaving a stale one.
        self.write('day', {'date': '2026-10-25', 'pain': '轻'}, 'a')
        self.assertEqual(self.notices(), [])

    def test_backfilled_old_day_does_not_notify(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.grant(allow_notifications=True, share_details=True)
        self.follow()
        self.write('day', {'date': '2026-10-20', 'pain': '重'}, 'a')
        self.assertEqual(self.notices(), [])

    def test_neutral_text_without_detail_grant(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.grant(allow_notifications=True)
        self.follow()
        self.run_pass(self.in_window())
        self.assertTrue(self.notices())
        for n in self.notices():
            self.assertEqual(n['title'], '私人状态提醒')


class HttpTests(Base):
    """End-to-end over the real handler: auth, no-store, CRUD, isolation."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='cycle2-http-',
                                               dir=os.environ.get('TMPDIR') or None)
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

    def req(self, path, uid='a', data=None):
        c = http.client.HTTPConnection('127.0.0.1', self.srv.server_port, timeout=10)
        hdr = {'Content-Type': 'application/json'}
        if uid:
            hdr['Cookie'] = app.COOKIE + '=' + app.make_token(uid)
        c.request('POST' if data is not None else 'GET', path,
                  json.dumps(data) if data is not None else None, hdr)
        r = c.getresponse()
        status, headers, body = r.status, dict(r.getheaders()), r.read()
        c.close()
        try:
            payload = json.loads(body)
        except ValueError:
            payload = body.decode()
        return status, headers, payload

    def test_auth_and_no_store(self):
        self.assertEqual(self.req('/api/cycle2', uid=None)[0], 401)
        code, headers, payload = self.req('/api/cycle2')
        self.assertEqual(code, 200)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertEqual(payload['uid'], 'a')

    def test_crud_readback_and_isolation(self):
        today = date.today().isoformat()
        code, _, r = self.req('/api/cycle2/period', data={'start_date': today})
        self.assertEqual(code, 200)
        rid = r['id']
        self.assertTrue(any(p['id'] == rid for p in self.req('/api/cycle2')[2]['periods']))
        self.assertEqual(self.req('/api/cycle2/period/delete', 'b', {'id': rid})[0], 404)
        self.assertEqual(self.req('/api/cycle2/period/delete', data={'id': rid})[0], 200)

    def test_bbt_endpoint(self):
        today = date.today().isoformat()
        self.assertEqual(self.req('/api/cycle2/bbt',
                                  data={'date': today, 'temp_c': 36.6})[0], 200)
        self.assertEqual(self.req('/api/cycle2/bbt',
                                  data={'date': today, 'temp_c': 42})[0], 400)
        snap = self.req('/api/cycle2')[2]
        self.assertEqual(snap['bbt'][0]['temp_c'], 36.6)

    def test_cross_origin_write_rejected(self):
        c = http.client.HTTPConnection('127.0.0.1', self.srv.server_port, timeout=10)
        c.request('POST', '/api/cycle2/period',
                  json.dumps({'start_date': date.today().isoformat()}),
                  {'Content-Type': 'application/json',
                   'Cookie': app.COOKIE + '=' + app.make_token('a'),
                   'Origin': 'https://evil.example'})
        r = c.getresponse()
        status = r.status
        r.read()
        c.close()
        self.assertEqual(status, 403)

    def test_export_attachment(self):
        code, headers, _ = self.req('/api/cycle2/export')
        self.assertEqual(code, 200)
        self.assertIn('attachment', headers['Content-Disposition'])


class QuietHandler(app.Handler):
    def log_message(self, *args):
        pass


if __name__ == '__main__':
    unittest.main()
