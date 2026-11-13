"""M4 tests: v1 -> v2 migration, archive safety, and remote-backup sanitisation.

Run: TMPDIR=/tmp python3 -m unittest discover -s tests -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import cycle
import cycle2
import cycle2_collaboration as collab
import migrate_cycle_v2 as mig


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='cycle2-migrate-', dir=os.environ.get('TMPDIR') or None)
        self.db = os.path.join(self.tmp, 'sched.db')
        self.c = sqlite3.connect(self.db)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(app.SCHEMA + cycle.SCHEMA + cycle2.SCHEMA_V2)
        collab.ensure_schema(self.c)
        self.seed_v1()
        self.c.commit()

    def tearDown(self):
        self.c.close()

    def seed_v1(self):
        rows = [('a', '2026-07-01', '2026-07-05'), ('a', '2026-07-30', '2026-08-03'),
                ('a', '2026-08-28', '2026-09-01'), ('b', '2026-09-10', '2026-09-14')]
        for uid, s, e in rows:
            self.c.execute('INSERT INTO cycle_periods(uid,start_date,end_date,updated_at) '
                           'VALUES(?,?,?,?)', (uid, s, e, '2026-09-01T00:00:00'))
        self.c.execute("INSERT INTO cycle_days(uid,date,flow,pain,symptoms,mood,note) "
                       "VALUES('a','2026-07-02','中','轻','[\"疲惫\"]','平静','旧备注')")
        self.c.execute("INSERT INTO cycle_daily_details(uid,date,activity,relief,medication,"
                       "effectiveness,note_shared,updated_at) "
                       "VALUES('a','2026-07-02','有些影响','热敷','布洛芬','有缓解',1,'x')")
        self.c.execute("INSERT INTO cycle_settings(uid,share_actual,share_prediction,"
                       "share_since,share_with) VALUES('a',1,1,'2026-07-01','b')")
        self.c.execute("INSERT INTO cycle_collab(uid,share_details,allow_proxy) VALUES('a',1,1)")
        self.c.execute("INSERT INTO cycle_preferences(uid,role,enabled) "
                       "VALUES('b','supporter',1)")

    def run_migrate(self, dry_run=False):
        stats = mig.migrate(self.c, dry_run=dry_run)
        self.c.commit()
        return stats

    def test_copies_dates_and_notes_only(self):
        stats = self.run_migrate()
        self.assertEqual(stats['periods'], 4)
        self.assertEqual(stats['notes'], 1)
        got = {(r['uid'], r['start_date'], r['end_date']) for r in
               self.c.execute('SELECT uid,start_date,end_date FROM cycle2_periods')}
        self.assertEqual(got, {('a', '2026-07-01', '2026-07-05'),
                               ('a', '2026-07-30', '2026-08-03'),
                               ('a', '2026-08-28', '2026-09-01'),
                               ('b', '2026-09-10', '2026-09-14')})
        note = self.c.execute("SELECT * FROM cycle2_day_notes WHERE date='2026-07-02'").fetchone()
        self.assertEqual(note['note'], '旧备注')
        self.assertEqual(note['relief'], '热敷')
        self.assertEqual(note['note_shared'], 1)
        self.assertEqual(json.loads(note['symptoms']), ['疲惫'])

    def test_settings_and_grants_not_carried_over(self):
        self.run_migrate()
        self.assertEqual(cycle2.settings(self.c, 'a')['share_actual'], 0)
        self.assertIsNone(self.c.execute(
            'SELECT * FROM cycle2_settings WHERE uid=?', ('a',)).fetchone())

    def test_idempotent(self):
        first = self.run_migrate()
        second = self.run_migrate()
        self.assertEqual(first['periods'], 4)
        self.assertEqual(second['periods'], 0)
        self.assertEqual(second['periods_skipped'], 4)
        self.assertEqual(second['notes'], 0)
        self.assertEqual(second['notes_skipped'], 1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM cycle2_periods').fetchone()[0], 4)

    def test_dry_run_writes_nothing(self):
        stats = self.run_migrate(dry_run=True)
        self.assertEqual(stats['periods'], 4)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM cycle2_periods').fetchone()[0], 0)

    def test_archive_holds_every_v1_row_verbatim(self):
        path, total = mig.archive(self.c, os.path.join(self.tmp, 'arc'))
        self.assertTrue(os.path.isfile(path))
        self.assertGreater(total, 0)
        if os.name != 'nt':
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        data = json.load(open(path, encoding='utf-8'))
        self.assertEqual(len(data['tables']['cycle_periods']), 4)
        self.assertEqual(data['tables']['cycle_settings'][0]['share_with'], 'b')
        self.assertEqual(data['tables']['cycle_collab'][0]['allow_proxy'], 1)
        self.assertIn('archived_at', data)

    def test_v1_tables_left_intact(self):
        self.run_migrate()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM cycle_periods').fetchone()[0], 4)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM cycle_settings').fetchone()[0], 1)

    def test_migrated_data_drives_engine(self):
        self.run_migrate()
        recs = cycle2.periods(self.c, 'a')
        import cycle_engine as engine
        from datetime import date
        pred = engine.predict(recs, cycle2.settings(self.c, 'a'), date(2026, 9, 20))
        self.assertTrue(pred['available'], pred.get('reason'))
        self.assertEqual(pred['cycle_length'], 29)

    def test_cli_dry_run_on_file(self):
        before = self.c.execute('SELECT COUNT(*) FROM cycle2_periods').fetchone()[0]
        mig.main(['--db', self.db, '--archive-dir', os.path.join(self.tmp, 'arc2'), '--dry-run'])
        self.c.close()
        self.c = sqlite3.connect(self.db)
        after = self.c.execute('SELECT COUNT(*) FROM cycle2_periods').fetchone()[0]
        self.assertEqual(before, after)
        self.assertTrue(os.listdir(os.path.join(self.tmp, 'arc2')))


class SanitizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='cycle2-sanitize-', dir=os.environ.get('TMPDIR') or None)
        self.src = os.path.join(self.tmp, 'src.db')
        self.dst = os.path.join(self.tmp, 'dst.db')
        c = sqlite3.connect(self.src)
        c.executescript(app.SCHEMA + cycle.SCHEMA + cycle2.SCHEMA_V2)
        collab.ensure_schema(c)
        c.execute("INSERT INTO cycle2_periods(uid,start_date,updated_at) VALUES('a','2026-10-01','x')")
        c.execute("INSERT INTO cycle2_bbt(uid,date,temp_c,updated_at) VALUES('a','2026-10-02',36.5,'x')")
        c.execute("INSERT INTO cycle2_day_notes(uid,date,note) VALUES('a','2026-10-02','SECRET')")
        c.execute("INSERT INTO cycle2_proposals(uid,author_uid,start_date,created_at) "
                  "VALUES('a','b','2026-10-01','x')")
        c.execute("INSERT INTO cycle2_notice_keys(uid,owner_uid,event_key,kind) "
                  "VALUES('b','a','pms:2026-10-01','pms')")
        c.execute("INSERT INTO cycle_periods(uid,start_date,updated_at) VALUES('a','2026-09-01','x')")
        c.execute("INSERT INTO notifications(target_uid,title,content,target_action,is_read,is_pushed,created_at) "
                  "VALUES('b','t','SECRET_NOTICE','view:cycle2',0,0,'2026-10-01')")
        c.commit()
        c.close()

    def test_every_health_table_emptied(self):
        cycle.prepare_remote_backup(self.src, self.dst)
        d = sqlite3.connect(self.dst)
        names = [r[0] for r in d.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "(name GLOB 'cycle_*' OR name GLOB 'cycle2_*')")]
        self.assertTrue(names, 'expected cycle tables to exist in the copy')
        for table in names:
            self.assertEqual(d.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0], 0,
                             table + ' still holds rows')
        self.assertEqual(d.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], 0)
        d.close()

    def test_no_secret_string_survives(self):
        cycle.prepare_remote_backup(self.src, self.dst)
        raw = open(self.dst, 'rb').read()
        self.assertNotIn(b'SECRET', raw)
        self.assertNotIn(b'SECRET_NOTICE', raw)

    def test_refuses_to_overwrite_source(self):
        with self.assertRaises(ValueError):
            cycle.prepare_remote_backup(self.src, self.src)


if __name__ == '__main__':
    unittest.main()
