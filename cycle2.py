"""Cycle v2 data layer: v2 tables on top of cycle_engine. Standard-library only.

v1 tables (cycle_*) stay untouched and readable; collaboration/proposal tables are
reused from v1 in M2. New tables all carry the cycle2_ prefix so remote_backup's
'cycle%' wildcard stripping keeps covering them.
"""
import json
from datetime import date, timedelta

import cycle
import cycle_engine as engine

Error = cycle.Error

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS cycle2_periods (
 id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL,
 start_date TEXT NOT NULL, end_date TEXT, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_cycle2_periods ON cycle2_periods(uid,start_date);
CREATE TABLE IF NOT EXISTS cycle2_bbt (
 uid TEXT NOT NULL, date TEXT NOT NULL, temp_c REAL NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(uid,date));
CREATE TABLE IF NOT EXISTS cycle2_day_notes (
 uid TEXT NOT NULL, date TEXT NOT NULL, flow TEXT NOT NULL DEFAULT '',
 pain TEXT NOT NULL DEFAULT '', symptoms TEXT NOT NULL DEFAULT '[]',
 mood TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
 activity TEXT NOT NULL DEFAULT '', relief TEXT NOT NULL DEFAULT '',
 medication TEXT NOT NULL DEFAULT '', effectiveness TEXT NOT NULL DEFAULT '',
 note_shared INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(uid,date));
CREATE TABLE IF NOT EXISTS cycle2_settings (
 uid TEXT PRIMARY KEY, typical_cycle INTEGER, typical_duration INTEGER,
 prediction_enabled INTEGER NOT NULL DEFAULT 1,
 reminders_enabled INTEGER NOT NULL DEFAULT 0, reminder_days INTEGER NOT NULL DEFAULT 2,
 share_actual INTEGER NOT NULL DEFAULT 0, share_prediction INTEGER NOT NULL DEFAULT 0,
 share_since TEXT NOT NULL DEFAULT '', share_with TEXT NOT NULL DEFAULT '');
"""

FLOW = ('', '极少', '少', '中', '多', '很多')
PAIN = ('', '无', '轻', '中', '重')
ACTIVITY = ('', '几乎无影响', '轻度影响', '明显影响', '严重影响')
EFFECTIVENESS = ('', '有效', '部分有效', '无效')


def iso(value, label='日期', optional=False):
    return cycle.iso(value, label, optional)


def settings(conn, uid):
    row = conn.execute('SELECT * FROM cycle2_settings WHERE uid=?', (uid,)).fetchone()
    if row:
        return dict(row)
    return dict(uid=uid, typical_cycle=None, typical_duration=None,
                prediction_enabled=1, reminders_enabled=0, reminder_days=2,
                share_actual=0, share_prediction=0, share_since='', share_with='')


def put_settings(conn, uid, data, today=None):
    today = today or date.today()
    cur = settings(conn, uid)
    row = {k: data.get(k, cur[k]) for k in cur}
    row['typical_cycle'] = cycle.integer(row['typical_cycle'], 15, 60, '典型周期', True)
    row['typical_duration'] = cycle.integer(row['typical_duration'], 1, 30, '典型经期', True)
    for flag in ('prediction_enabled', 'reminders_enabled', 'share_actual', 'share_prediction'):
        if flag in row:
            row[flag] = 1 if row[flag] else 0
    row['reminder_days'] = cycle.integer(row['reminder_days'], 0, 7, '提醒天数')
    if 'share_since' in data:
        row['share_since'] = iso(data['share_since'], '分享起始日期').isoformat()
    # Sharing is always with the fixed partner account; clearing both switches ends it.
    if row['share_actual'] or row['share_prediction']:
        row['share_since'] = row['share_since'] or today.isoformat()
        row['share_with'] = cycle.other(uid)
    else:
        row['share_with'] = ''
        row['share_since'] = ''
    conn.execute('INSERT OR REPLACE INTO cycle2_settings(uid,typical_cycle,typical_duration,'
                 'prediction_enabled,reminders_enabled,reminder_days,share_actual,'
                 'share_prediction,share_since,share_with) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (uid, row['typical_cycle'], row['typical_duration'], row['prediction_enabled'],
                  row['reminders_enabled'], row['reminder_days'], row['share_actual'],
                  row['share_prediction'], row.get('share_since', '') or '',
                  row.get('share_with', '') or ''))
    return row


def periods(conn, uid):
    return [dict(r) for r in conn.execute(
        'SELECT * FROM cycle2_periods WHERE uid=? ORDER BY start_date DESC,id DESC', (uid,))]


def bbt(conn, uid):
    return [dict(r) for r in conn.execute(
        'SELECT date,temp_c FROM cycle2_bbt WHERE uid=? ORDER BY date', (uid,))]


def notes(conn, uid):
    result = []
    for r in conn.execute('SELECT * FROM cycle2_day_notes WHERE uid=? ORDER BY date DESC', (uid,)):
        d = dict(r)
        d['symptoms'] = json.loads(d['symptoms'] or '[]')
        result.append(d)
    return result


def luteal_from_data(conn, uid):
    return engine.luteal_from_bbt(periods(conn, uid), bbt(conn, uid))


def mutate(conn, uid, action, body, today=None):
    today = today or date.today()
    if not isinstance(body, dict):
        raise Error('请求须为 JSON 对象')
    now = today.isoformat()
    if action == 'period':
        start = iso(body.get('start_date'), '开始日期')
        end = iso(body.get('end_date'), '结束日期', True)
        if start > today:
            raise Error('不能记录未来日期')
        if end and end > today:
            raise Error('结束日期不能是未来')
        if end and end < start:
            raise Error('结束日期不能早于开始日期')
        rid = body.get('id')
        if rid is not None:
            rid = cycle.integer(rid, 1, 2**63 - 1, '记录编号')
            if not conn.execute('SELECT 1 FROM cycle2_periods WHERE id=? AND uid=?',
                                 (rid, uid)).fetchone():
                raise Error('记录不存在', 404)
        overlap = conn.execute(
            "SELECT 1 FROM cycle2_periods WHERE uid=? AND id IS NOT ? "
            "AND start_date<=COALESCE(?, '9999-12-31') "
            "AND COALESCE(end_date,'9999-12-31')>=?",
            (uid, rid, end.isoformat() if end else None, start.isoformat())).fetchone()
        if overlap:
            raise Error('与已有记录重叠')
        if rid is None:
            cur = conn.execute(
                'INSERT INTO cycle2_periods(uid,start_date,end_date,updated_at) VALUES(?,?,?,?)',
                (uid, start.isoformat(), end.isoformat() if end else None, now))
            rid = cur.lastrowid
        else:
            conn.execute('UPDATE cycle2_periods SET start_date=?,end_date=?,updated_at=? '
                         'WHERE id=? AND uid=?',
                         (start.isoformat(), end.isoformat() if end else None, now, rid, uid))
        return {'ok': True, 'id': rid}
    if action == 'period/delete':
        rid = cycle.integer(body.get('id'), 1, 2**63 - 1, '记录编号')
        cur = conn.execute('DELETE FROM cycle2_periods WHERE id=? AND uid=?', (rid, uid))
        if not cur.rowcount:
            raise Error('记录不存在', 404)
        return {'ok': True}
    if action == 'day':
        day = iso(body.get('date'), '日期')
        if day > today:
            raise Error('不能记录未来日期')
        flow = cycle.text(body.get('flow', ''), 10, '经量')
        if flow not in FLOW:
            raise Error('经量取值无效')
        pain = cycle.text(body.get('pain', ''), 10, '疼痛')
        if pain not in PAIN:
            raise Error('疼痛取值无效')
        symptoms = body.get('symptoms', [])
        if not isinstance(symptoms, list) or len(symptoms) > 12 or \
                not all(isinstance(s, str) and len(s) <= 12 for s in symptoms):
            raise Error('症状须为至多 12 项短文本')
        mood = cycle.text(body.get('mood', ''), 20, '心情')
        note = cycle.text(body.get('note', ''), 500, '备注')
        activity = cycle.text(body.get('activity', ''), 20, '影响')
        if activity and activity not in ACTIVITY:
            raise Error('影响取值无效')
        relief = cycle.text(body.get('relief', ''), 50, '缓解')
        medication = cycle.text(body.get('medication', ''), 50, '用药')
        effectiveness = cycle.text(body.get('effectiveness', ''), 10, '效果')
        if effectiveness and effectiveness not in EFFECTIVENESS:
            raise Error('效果取值无效')
        note_shared = 1 if body.get('note_shared') else 0
        existing = conn.execute('SELECT note_shared FROM cycle2_day_notes WHERE uid=? AND date=?',
                                (uid, day.isoformat())).fetchone()
        shared = note_shared if existing is None else (note_shared or existing['note_shared'])
        conn.execute(
            'INSERT OR REPLACE INTO cycle2_day_notes(uid,date,flow,pain,symptoms,mood,note,'
            'activity,relief,medication,effectiveness,note_shared,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (uid, day.isoformat(), flow, pain, json.dumps(symptoms, ensure_ascii=False),
             mood, note, activity, relief, medication, effectiveness, shared, now))
        return {'ok': True}
    if action == 'day/delete':
        day = iso(body.get('date'), '日期')
        conn.execute('DELETE FROM cycle2_day_notes WHERE uid=? AND date=?', (uid, day.isoformat()))
        return {'ok': True}
    if action == 'bbt':
        day = iso(body.get('date'), '日期')
        if day > today:
            raise Error('不能记录未来日期')
        temp = body.get('temp_c')
        if type(temp) not in (int, float) or not 35.0 <= float(temp) <= 38.5:
            raise Error('体温须为 35.0～38.5 之间的数字')
        conn.execute('INSERT OR REPLACE INTO cycle2_bbt(uid,date,temp_c,updated_at) '
                     'VALUES(?,?,?,?)', (uid, day.isoformat(), round(float(temp), 2), now))
        return {'ok': True}
    if action == 'bbt/delete':
        day = iso(body.get('date'), '日期')
        conn.execute('DELETE FROM cycle2_bbt WHERE uid=? AND date=?', (uid, day.isoformat()))
        return {'ok': True}
    if action == 'settings':
        row = put_settings(conn, uid, body, today)
        return {'ok': True, 'settings': row}
    if action == 'delete-all':
        if body.get('confirm') != 'DELETE_MY_CYCLE_DATA':
            raise Error('请确认删除本人全部经期数据')
        for table in ('cycle2_periods', 'cycle2_bbt', 'cycle2_day_notes', 'cycle2_settings'):
            conn.execute('DELETE FROM ' + table + ' WHERE uid=?', (uid,))
        return {'ok': True}
    raise Error('未知操作', 400)


def snapshot(conn, uid, month, today=None):
    """v2 view: today card (phase ring data), month calendar (phase marks), raw lists."""
    today = today or date.today()
    first = iso(month + '-01', '月份')
    last = (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    own = periods(conn, uid)
    prefs = settings(conn, uid)
    temps = bbt(conn, uid)
    prediction = engine.predict(own, prefs, today, engine.luteal_from_bbt(own, temps))
    phase = engine.phase_of(today, own, prediction, temps)
    marks = engine.month_phases(first, last, own, prediction, temps)
    active = next((r for r in own if not r['end_date']
                   and date.fromisoformat(r['start_date']) + timedelta(days=10) >= today), None)
    return dict(ok=True, uid=uid, month=month, today=phase, prediction=prediction, calendar=marks,
                periods=own, notes=notes(conn, uid), bbt=temps,
                insights=engine.insights(own, prediction, temps),
                active=active, settings={k: prefs[k] for k in
                                         ('typical_cycle', 'typical_duration',
                                          'prediction_enabled')},
                disclaimer=engine.DISCLAIMER)


def export_data(conn, uid):
    return dict(periods=periods(conn, uid), bbt=bbt(conn, uid), notes=notes(conn, uid),
                settings=settings(conn, uid))
