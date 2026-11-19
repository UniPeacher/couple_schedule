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

    def test_grant_shows_the_same_record_to_both(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        mine, other = self.view('a'), self.view('b')
        self.assertEqual(len(other['periods']), len(mine['periods']))
        self.assertEqual(other['today']['phase'], mine['today']['phase'])
        self.assertEqual(other['prediction']['estimated_start'],
                         mine['prediction']['estimated_start'])

    def test_partner_panel_is_gone(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.assertIsNone(self.view('b')['partner'])
        self.assertIsNone(self.view('a')['partner'])

    def test_other_account_can_write_the_shared_record(self):
        self.seed()
        self.let_share(actual=True, prediction=True)
        self.write('period', {'start_date': '2026-10-20', 'end_date': '2026-10-24'}, 'b')
        self.assertIn('2026-10-20', [r['start_date'] for r in self.view('a')['periods']])
        self.assertIn('2026-10-20', [r['start_date'] for r in self.view('b')['periods']])

    def test_shared_record_writes_land_on_one_owner(self):
        self.seed()
        self.let_share()
        self.write('day', {'date': '2026-10-20', 'flow': '中'}, 'b')
        rows = self.c.execute("SELECT uid FROM cycle2_day_notes WHERE date='2026-10-20'").fetchall()
        self.assertEqual([r['uid'] for r in rows], ['a'])

    def test_revoke_returns_each_account_to_its_own_record(self):
        self.seed()
        self.let_share()
        self.write('settings', {'share_actual': False, 'share_prediction': False}, 'a')
        self.assertEqual(self.view('b')['uid'], 'b')
        self.assertEqual(self.view('b')['periods'], [])
        self.assertEqual(len(self.view('a')['periods']), 3)

    def test_proxy_entry_is_gone(self):
        self.seed()
        self.let_share()
        self.grant(allow_proxy=True)
        with self.assertRaises(cycle2.Error):
            self.write('proposal', {'start_date': '2026-10-20'}, 'b')

    def test_own_view_always_complete(self):
        self.seed()
        own = self.view('a')
        self.assertEqual(own['today']['phase'], 'luteal')
        self.assertEqual(len(own['periods']), 3)
        self.assertIn('insights', own)


class ProxyTests(Base):
    """Proxy entry is gone: both accounts edit the one shared record directly."""

    def test_proxy_writes_are_refused(self):
        self.seed()
        self.let_share()
        self.grant(allow_proxy=True)
        for action in ("proposal", "proposal/confirm", "proposal/reject", "proposal/cancel"):
            with self.assertRaises(cycle.Error) as e:
                self.write(action, period("2026-10-20", "2026-10-24"), "b")
            self.assertEqual(e.exception.status, 400)

    def test_either_account_writes_without_approval(self):
        self.seed()
        self.let_share()
        self.write("period", period("2026-10-20", "2026-10-24"), "b")
        starts = [r["start_date"] for r in cycle2.periods(self.c, "a")]
        self.assertIn("2026-10-20", starts)
        self.assertEqual(len(cycle2.periods(self.c, "b")), 0)


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
