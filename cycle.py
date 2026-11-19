"""Cycle module shared pieces: DB schema, input validation helpers, care-message
lookup and the remote-backup sanitizer. Standard-library only; never included in
shared event/AI payloads."""
import re
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycle_periods (
 id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL,
 start_date TEXT NOT NULL, end_date TEXT, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_cycle_periods ON cycle_periods(uid,start_date);
CREATE TABLE IF NOT EXISTS cycle_days (
 uid TEXT NOT NULL, date TEXT NOT NULL, flow TEXT NOT NULL DEFAULT '',
 pain TEXT NOT NULL DEFAULT '', symptoms TEXT NOT NULL DEFAULT '[]',
 mood TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(uid,date));
CREATE TABLE IF NOT EXISTS cycle_settings (
 uid TEXT PRIMARY KEY, typical_cycle INTEGER, typical_duration INTEGER,
 prediction_enabled INTEGER NOT NULL DEFAULT 1,
 reminders_enabled INTEGER NOT NULL DEFAULT 0, reminder_days INTEGER NOT NULL DEFAULT 2,
 share_actual INTEGER NOT NULL DEFAULT 0, share_prediction INTEGER NOT NULL DEFAULT 0,
 share_since TEXT NOT NULL DEFAULT '', share_with TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS cycle_care (
 uid TEXT PRIMARY KEY, recipient TEXT NOT NULL, message TEXT NOT NULL,
 expires_on TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS cycle_reminders (
 uid TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY(uid,day));
CREATE TABLE IF NOT EXISTS cycle_daily_details (
 uid TEXT NOT NULL, date TEXT NOT NULL, activity TEXT NOT NULL DEFAULT '',
 relief TEXT NOT NULL DEFAULT '', medication TEXT NOT NULL DEFAULT '',
 effectiveness TEXT NOT NULL DEFAULT '', note_shared INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL DEFAULT '', PRIMARY KEY(uid,date));
CREATE TABLE IF NOT EXISTS cycle_collab (
 uid TEXT PRIMARY KEY, share_details INTEGER NOT NULL DEFAULT 0,
 allow_proxy INTEGER NOT NULL DEFAULT 0, allow_notifications INTEGER NOT NULL DEFAULT 0,
 allow_notice_details INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS cycle_preferences (
 uid TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'recorder',
 enabled INTEGER NOT NULL DEFAULT 0, upcoming INTEGER NOT NULL DEFAULT 1,
 started INTEGER NOT NULL DEFAULT 1, discomfort INTEGER NOT NULL DEFAULT 1,
 pending INTEGER NOT NULL DEFAULT 1, advance_days INTEGER NOT NULL DEFAULT 2,
 notice_details INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS cycle_proposals (
 id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, author_uid TEXT NOT NULL,
 period_id INTEGER, base_version TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL,
 end_date TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_cycle_proposals ON cycle_proposals(uid,status);
CREATE TABLE IF NOT EXISTS cycle_record_sources (
 period_id INTEGER PRIMARY KEY, uid TEXT NOT NULL, author_uid TEXT NOT NULL,
 confirmed_by TEXT NOT NULL, proposal_id INTEGER);
CREATE TABLE IF NOT EXISTS cycle_notice_keys (
 uid TEXT NOT NULL, owner_uid TEXT NOT NULL, event_key TEXT NOT NULL,
 kind TEXT NOT NULL, notification_id INTEGER, PRIMARY KEY(uid,owner_uid,event_key));
"""


class Error(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def iso(value, label='日期', optional=False):
    if optional and value in (None, ''):
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise Error(label + '须为 YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Error(label + '无效')


def integer(value, lo, hi, label, optional=False):
    if optional and value in (None, ''):
        return None
    if type(value) is not int or not lo <= value <= hi:
        raise Error(f'{label}须为 {lo}～{hi} 的整数')
    return value


def text(value, limit, label):
    if not isinstance(value, str) or len(value) > limit:
        raise Error(f'{label}须为不超过 {limit} 字的文本')
    return value.strip()


def other(uid):
    return 'b' if uid == 'a' else 'a'


def care(conn, owner, viewer, today):
    row = conn.execute('SELECT * FROM cycle_care WHERE uid=? AND expires_on>=?',
                       (owner, today.isoformat())).fetchone()
    if not row or viewer not in (owner, row['recipient']):
        return None
    return dict(row)


def prepare_remote_backup(source_path, target_path):
    """Produce a compact remote-safe copy; local full backups retain private data.

    Operates on a copy only. VACUUM removes deleted health payloads from free pages.
    Never falls back to exporting the original database on failure.
    """
    import os
    import sqlite3
    from contextlib import closing
    if os.path.realpath(source_path) == os.path.realpath(target_path):
        raise ValueError('Remote-safe copy must not overwrite the source database')
    with closing(sqlite3.connect(source_path)) as src, closing(sqlite3.connect(target_path)) as dst:
        src.backup(dst)
        dst.execute('PRAGMA secure_delete=ON')
        tables = [row[0] for row in dst.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND (name GLOB 'cycle_*' OR name GLOB 'cycle2_*')")]
        for table in tables:
            dst.execute('DELETE FROM "' + table.replace('"', '""') + '"')
        dst.execute("DELETE FROM notifications WHERE target_action IN ('view:cycle', 'view:cycle2')")
        dst.commit()
        dst.execute('VACUUM')
        dst.commit()
    os.chmod(target_path, 0o600)
