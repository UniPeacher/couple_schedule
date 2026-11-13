#!/usr/bin/env python3
"""One-shot, idempotent migration from v1 cycle tables to the v2 model.

Copies only what is objectively factual: confirmed period start/end dates and
day notes. v1 prediction settings, sharing grants and pending proxy proposals are
deliberately not carried over — the v2 UI asks for them again under its own model.

Also writes a timestamped JSON archive of every v1 cycle row before touching
anything, so the original records remain recoverable even after v1 is retired.

Usage:
    python3 migrate_cycle_v2.py --db /path/sched.db [--archive-dir DIR] [--dry-run]
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cycle2
import cycle2_collaboration

V1_TABLES = ('cycle_periods', 'cycle_days', 'cycle_settings', 'cycle_care',
             'cycle_reminders', 'cycle_daily_details', 'cycle_collab',
             'cycle_preferences', 'cycle_proposals', 'cycle_record_sources',
             'cycle_notice_keys')


def archive(conn, folder):
    """Dump every v1 cycle row verbatim; nothing is deleted here."""
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    path = os.path.join(folder, 'cycle-v1-archive-%s.json' % stamp)
    payload = {'archived_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'tables': {}}
    for table in V1_TABLES:
        try:
            rows = [dict(r) for r in conn.execute('SELECT * FROM ' + table)]
        except sqlite3.OperationalError:
            continue
        payload['tables'][table] = rows
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)
    return path, sum(len(v) for v in payload['tables'].values())


def migrate(conn, dry_run=False):
    """Insert v1 periods/notes into v2 tables unless an equivalent row already exists."""
    stats = {'periods': 0, 'periods_skipped': 0, 'notes': 0, 'notes_skipped': 0}
    periods = [dict(r) for r in conn.execute(
        'SELECT uid, start_date, end_date FROM cycle_periods ORDER BY start_date')]
    for row in periods:
        exists = conn.execute(
            'SELECT 1 FROM cycle2_periods WHERE uid=? AND start_date=?',
            (row['uid'], row['start_date'])).fetchone()
        if exists:
            stats['periods_skipped'] += 1
            continue
        stats['periods'] += 1
        if not dry_run:
            conn.execute(
                'INSERT INTO cycle2_periods(uid,start_date,end_date,updated_at) '
                'VALUES(?,?,?,?)',
                (row['uid'], row['start_date'], row['end_date'],
                 time.strftime('%Y-%m-%dT%H:%M:%S')))
    notes = [dict(r) for r in conn.execute(
        'SELECT uid, date, flow, pain, symptoms, mood, note FROM cycle_days')]
    details = {(r['uid'], r['date']): dict(r) for r in conn.execute(
        'SELECT uid, date, activity, relief, medication, effectiveness, note_shared '
        'FROM cycle_daily_details')} if _has(conn, 'cycle_daily_details') else {}
    for row in notes:
        exists = conn.execute(
            'SELECT 1 FROM cycle2_day_notes WHERE uid=? AND date=?',
            (row['uid'], row['date'])).fetchone()
        if exists:
            stats['notes_skipped'] += 1
            continue
        stats['notes'] += 1
        if dry_run:
            continue
        extra = details.get((row['uid'], row['date']), {})
        conn.execute(
            'INSERT INTO cycle2_day_notes(uid,date,flow,pain,symptoms,mood,note,'
            'activity,relief,medication,effectiveness,note_shared,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (row['uid'], row['date'], row['flow'] or '', row['pain'] or '',
             row['symptoms'] or '[]', row['mood'] or '', row['note'] or '',
             extra.get('activity') or '', extra.get('relief') or '',
             extra.get('medication') or '', extra.get('effectiveness') or '',
             int(extra.get('note_shared') or 0), time.strftime('%Y-%m-%dT%H:%M:%S')))
    return stats


def _has(conn, table):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--archive-dir', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    if not os.path.isfile(args.db):
        sys.exit('database not found: ' + args.db)
    folder = args.archive_dir or os.path.join(os.path.dirname(os.path.abspath(args.db)),
                                              'cycle-v1-archive')
    with closing(sqlite3.connect(args.db)) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(cycle2.SCHEMA_V2)
        cycle2_collaboration.ensure_schema(conn)
        path, total = archive(conn, folder)
        print('archived %d v1 rows -> %s' % (total, path))
        conn.execute('BEGIN')
        stats = migrate(conn, dry_run=args.dry_run)
        if args.dry_run:
            conn.execute('ROLLBACK')
        else:
            conn.execute('COMMIT')
    print(('dry-run ' if args.dry_run else '') + json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
