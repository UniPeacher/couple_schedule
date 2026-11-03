"""Collaborative acceptance tests; real SQLite, isolated synthetic data."""
import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
import app
import cycle
import cycle_collaboration as cc

TODAY=date(2026,10,25)
NOW=datetime(2026,10,25,10)

class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:')
        self.c.row_factory=sqlite3.Row
        self.c.executescript(app.SCHEMA+cycle.SCHEMA)

    def tearDown(self):
        self.c.close()

    def w(self,uid,action,body):
        return cycle.mutate(self.c,uid,action,body,TODAY)

    def v(self,uid):
        return cycle.snapshot(self.c,uid,'2026-10',TODAY)

    def grant(self):
        self.w('b','sharing',dict(settings=dict(share_actual=True,share_prediction=True,share_since='2026-01-01'),
                                 collaboration=dict(share_details=True,allow_proxy=True,allow_notifications=True)))
        self.w('a','preferences',dict(role='supporter',enabled=True))

    def record(self,start,end=None):
        return self.w('b','period',dict(start_date=start,end_date=end))['id']

    def history(self):
        self.record('2026-08-06','2026-08-10');self.record('2026-09-03','2026-09-07');self.record('2026-10-01','2026-10-05')

    def notices(self,uid='a'):
        return [dict(r) for r in self.c.execute('SELECT * FROM notifications WHERE target_uid=?',(uid,))]

    def test_no_role_or_grant_inferred_from_account(self):
        self.assertEqual(self.v('a')['preferences']['role'],'recorder')
        self.assertFalse(self.v('a')['preferences']['enabled'])
        with self.assertRaises(cycle.Error):self.w('a','proposal',dict(start_date='2026-10-25'))
        self.assertIsNone(self.v('a')['partner'])

    def test_proxy_pending_excluded_from_actual_prediction_and_notifications(self):
        self.grant();self.history()
        before=self.v('a')['partner']
        pid=self.w('a','proposal',dict(start_date='2026-10-25'))['id']
        self.assertEqual(len(self.v('b')['periods']),3)
        self.assertEqual(self.v('a')['partner']['prediction'],before['prediction'])
        self.assertEqual(len(self.v('a')['proposals']),1)
        self.assertEqual(len(self.v('b')['proposals']),1)
        self.assertEqual(self.notices(),[])
        self.assertEqual(self.v('b')['proposals'][0]['id'],pid)

    def test_proxy_confirm_with_correction_retains_authorship(self):
        self.grant()
        pid=self.w('a','proposal',dict(start_date='2026-10-24'))['id']
        r=self.w('b','proposal/confirm',dict(id=pid,start_date='2026-10-25'))
        formal=self.v('b')['periods'][0]
        self.assertEqual(formal['start_date'],'2026-10-25')
        self.assertEqual(formal['source']['author_uid'],'a')
        self.assertEqual(formal['source']['confirmed_by'],'b')
        self.assertEqual(formal['id'],r['id'])
        self.assertEqual(self.v('b')['proposals'],[])
        self.assertEqual(len(self.notices()),1)
        self.assertNotIn('经期',self.notices()[0]['content'])

    def test_author_cannot_confirm_own_proposal_or_overwrite_formal(self):
        self.grant();pid=self.w('a','proposal',dict(start_date='2026-10-25'))['id']
        with self.assertRaises(cycle.Error):self.w('a','proposal/confirm',dict(id=pid))
        rid=self.record('2026-10-01','2026-10-05')
        with self.assertRaises(cycle.Error):self.w('a','period',dict(id=rid,start_date='2026-10-01'))

    def test_rejection_and_cancellation_never_make_actual_record(self):
        self.grant()
        pid=self.w('a','proposal',dict(start_date='2026-10-25'))['id']
        self.w('b','proposal/reject',dict(id=pid))
        self.assertEqual(self.v('b')['periods'],[])
        pid=self.w('a','proposal',dict(start_date='2026-10-25'))['id']
        self.w('a','proposal/cancel',dict(id=pid))
        self.assertEqual(self.v('b')['proposals'],[])

    def test_proxy_end_waits_and_optimistic_concurrency(self):
        self.grant();rid=self.record('2026-10-22')
        pid=self.w('a','proposal',dict(period_id=rid,start_date='2026-10-22',end_date='2026-10-25'))['id']
        self.assertIsNone(self.v('b')['active']['end_date'])
        self.w('b','period',dict(id=rid,start_date='2026-10-21'))
        with self.assertRaises(cycle.Error) as e:self.w('b','proposal/confirm',dict(id=pid))
        self.assertEqual(e.exception.status,409)
        self.assertEqual(self.v('b')['active']['start_date'],'2026-10-21')
        self.w('b','proposal/reject',dict(id=pid))
        pid=self.w('a','proposal',dict(period_id=rid,start_date='2026-10-21',end_date='2026-10-25'))['id']
        self.w('b','proposal/confirm',dict(id=pid))
        self.assertIsNone(self.v('b')['active'])

    def test_sharing_structured_fields_and_per_note_privacy(self):
        self.grant()
        self.w('b','day',dict(date='2026-10-25',flow='多',pain='中',symptoms=['疲惫'],mood='想休息',
                              relief='热敷',medication='按医嘱记录',effectiveness='有缓解',activity='有些影响',note='PRIVATE_SENTINEL'))
        d=self.v('a')['partner']['days'][0]
        for k in ('flow','pain','symptoms','mood','relief','medication','effectiveness','activity','updated_at'):
            self.assertIn(k,d)
        self.assertNotIn('note',d)
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(self.v('a')))
        self.w('b','day',dict(date='2026-10-25',note='SHARED_NOTE',note_shared=True))
        self.assertEqual(self.v('a')['partner']['days'][0]['note'],'SHARED_NOTE')
        self.w('b','day',dict(date='2026-10-25',note='PRIVATE_AGAIN',note_shared=False))
        self.assertNotIn('note',self.v('a')['partner']['days'][0])

    def test_no_automatic_expansion_of_existing_date_grants(self):
        self.w('b','settings',dict(share_actual=True,share_since='2026-01-01'))
        self.w('b','day',dict(date='2026-10-25',pain='中',note_shared=True,note='secret'))
        self.assertNotIn('days',self.v('a')['partner'])
        self.assertFalse(self.v('a')['partner']['can_proxy'])

    def test_share_cutoff_applies_to_details_and_proxy(self):
        self.grant();self.w('b','settings',dict(share_since='2026-10-20'))
        self.w('b','day',dict(date='2026-10-19',pain='中'))
        self.assertEqual(self.v('a')['partner']['days'],[])
        with self.assertRaises(cycle.Error):self.w('a','proposal',dict(start_date='2026-10-19'))

    def test_moderate_discomfort_notification_deduplicates_same_day(self):
        self.grant()
        self.w('b','day',dict(date='2026-10-25',pain='轻'))
        self.assertEqual(self.notices(),[])
        self.w('b','day',dict(date='2026-10-25',pain='中'))
        self.assertEqual(len(self.notices()),1)
        nid=self.notices()[0]['id']
        self.w('b','day',dict(date='2026-10-25',pain='重',note='edit'))
        self.assertEqual([r['id'] for r in self.notices()],[nid])
        self.w('b','day',dict(date='2026-10-24',pain='重'))
        self.assertEqual(len(self.notices()),1)

    def test_significant_activity_impact_also_notifies(self):
        self.grant();self.w('b','day',dict(date='2026-10-25',activity='明显影响'))
        self.assertEqual(len(self.notices()),1)

    def test_notification_details_require_both_sides_and_default_neutral(self):
        self.grant();self.w('a','preferences',dict(notice_details=True))
        self.w('b','day',dict(date='2026-10-25',pain='中'))
        self.assertNotIn('疼痛',self.notices()[0]['content'])
        self.w('b','collaboration',dict(allow_notice_details=True))
        self.record('2026-10-25')
        self.assertIn('经期开始',self.notices()[0]['content'])
        self.w('b','collaboration',dict(allow_notice_details=False))
        self.assertEqual(self.notices(),[])

    def test_owner_and_recipient_notification_permission_both_required(self):
        self.grant();self.w('b','collaboration',dict(allow_notifications=False))
        self.w('b','day',dict(date='2026-10-25',pain='中'))
        self.assertEqual(self.notices(),[])
        self.w('b','collaboration',dict(allow_notifications=True))
        self.w('a','preferences',dict(enabled=False))
        self.record('2026-10-25')
        self.assertEqual(self.notices(),[])

    def test_upcoming_once_and_expired_waiting_once_per_recorded_cycle(self):
        self.grant();self.history()
        self.assertEqual(cycle.reminder_pass(self.c,NOW),1)
        self.assertEqual(cycle.reminder_pass(self.c,NOW+timedelta(days=1)),0)
        self.assertEqual(cycle.reminder_pass(self.c,NOW+timedelta(days=8)),1)
        self.assertEqual(cycle.reminder_pass(self.c,NOW+timedelta(days=9)),0)
        self.assertEqual(len(self.notices()),2)
        self.assertTrue(any('信息待确认' in r['content'] for r in self.notices()))

    def test_revocation_removes_access_pending_proposals_and_queued_notices(self):
        self.grant();self.w('a','proposal',dict(start_date='2026-10-25'))
        self.w('b','day',dict(date='2026-10-25',pain='中'))
        self.assertTrue(self.notices())
        self.w('b','sharing',dict(settings=dict(share_actual=False,share_prediction=False),collaboration=dict(share_details=False,allow_proxy=False,allow_notifications=False,allow_notice_details=False)))
        self.assertIsNone(self.v('a')['partner'])
        self.assertEqual(self.v('a')['proposals'],[])
        self.assertEqual(self.notices(),[])

    def test_status_counts_only_recent_open_record(self):
        rid=self.record('2026-10-23')
        self.assertEqual(self.v('b')['status']['day'],3)
        self.w('b','period',dict(id=rid,start_date='2026-10-01'))
        self.assertIsNone(self.v('b')['status']['day'])
        self.assertIn('结束待确认',self.v('b')['status']['label'])
        self.assertIsNone(self.v('b')['active']['end_date'])

    def test_trend_uses_confirmed_history_only(self):
        self.grant();self.history();self.w('a','proposal',dict(start_date='2026-10-25'))
        t=self.v('a')['partner']['trend']
        self.assertEqual(t['intervals'],[28,28]);self.assertEqual(t['spread'],0)

    def test_delete_all_removes_details_pending_metadata_and_preserves_partner(self):
        self.grant();self.record('2026-10-01','2026-10-05')
        self.w('a','period',dict(start_date='2026-10-02',end_date='2026-10-03'))
        self.w('a','proposal',dict(start_date='2026-10-25'))
        self.w('b','day',dict(date='2026-10-25',pain='中'))
        self.w('b','delete-all',dict(confirm='DELETE_MY_CYCLE_DATA'))
        self.assertIsNone(self.v('a')['partner']);self.assertEqual(self.v('a')['proposals'],[])
        self.assertEqual(len(self.v('a')['periods']),1)
        for table in ('cycle_daily_details','cycle_collab','cycle_record_sources','cycle_proposals'):
            self.assertEqual(self.c.execute('SELECT COUNT(*) FROM '+table+' WHERE uid=?',('b',)).fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM cycle_notice_keys WHERE owner_uid=?',('b',)).fetchone()[0],0)
        self.assertEqual(self.notices(),[])

    def test_migration_keeps_existing_dates_and_private_notes(self):
        self.c.execute("INSERT INTO cycle_periods(uid,start_date,end_date,updated_at) VALUES('b','2026-10-01','2026-10-05','old')")
        self.c.execute("INSERT INTO cycle_days(uid,date,note) VALUES('b','2026-10-02','old-private')")
        self.c.executescript(cycle.SCHEMA)
        self.assertEqual(self.v('b')['periods'][0]['start_date'],'2026-10-01')
        self.assertEqual(self.v('b')['days'][0]['note'],'old-private')
        self.assertFalse(self.v('b')['days'][0]['note_shared'])

    def test_remote_backup_removes_new_sensitive_tables(self):
        self.grant();self.w('a','proposal',dict(start_date='2026-10-25'))
        self.w('b','day',dict(date='2026-10-25',medication='MEDICATION_SECRET',note_shared=True))
        self.c.commit()
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as d:
            source,target=Path(d)/'source.db',Path(d)/'remote.db'
            with contextlib.closing(sqlite3.connect(source)) as dst:self.c.backup(dst)
            cycle.prepare_remote_backup(source,target)
            with contextlib.closing(sqlite3.connect(target)) as dst:
                for table in ('cycle_preferences','cycle_daily_details','cycle_proposals','cycle_collab'):
                    self.assertEqual(dst.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
            self.assertNotIn(b'MEDICATION_SECRET',target.read_bytes())

    def test_invalid_collaboration_and_preference_types(self):
        for action,body in [('preferences',dict(enabled='false')),('preferences',dict(role='male')),('collaboration',dict(share_details=True)),('preferences',dict(advance_days=True)),('day',dict(date='2026-10-25',note_shared='false'))]:
            with self.subTest(action=action,body=body),self.assertRaises(cycle.Error):self.w('a',action,body)

if __name__=='__main__':unittest.main()
