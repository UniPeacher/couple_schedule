"""Private cycle records. Standard-library only; never included in shared event/AI payloads."""
import calendar
import json
import re
import statistics
from datetime import date, datetime, timedelta

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
SYMPTOMS = ('疲惫', '腹胀', '头痛', '腰酸', '乳房胀痛', '恶心')
DISCLAIMER = '预估仅供参考，不用于避孕、诊断或排卵判断；未记录不代表月经推迟。'


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


def settings(conn, uid):
    row = conn.execute('SELECT * FROM cycle_settings WHERE uid=?', (uid,)).fetchone()
    if row:
        return dict(row)
    return dict(uid=uid, typical_cycle=None, typical_duration=None,
                prediction_enabled=1, reminders_enabled=0, reminder_days=2,
                share_actual=0, share_prediction=0, share_since='', share_with='')


def periods(conn, uid):
    return [dict(r) for r in conn.execute(
        'SELECT * FROM cycle_periods WHERE uid=? ORDER BY start_date DESC,id DESC', (uid,))]


def predict(records, prefs, today):
    if not prefs['prediction_enabled']:
        return {'available': False, 'reason': '已关闭预估'}
    if not records:
        return {'available': False, 'reason': '记录经期开始日期后，可逐步形成预估'}
    starts = sorted(date.fromisoformat(r['start_date']) for r in records[:7])
    gaps = [(b-a).days for a, b in zip(starts, starts[1:])]
    # Do not discard inconvenient observations or project through missing cycles.
    if gaps and (any(g < 15 or g > 60 for g in gaps) or max(gaps)-min(gaps) > 10):
        return {'available': False, 'reason': '近期记录间隔差异较大或有缺失，暂不预估'}
    if len(gaps) >= 2:
        length = round(statistics.median(gaps))
        basis = '近期实际记录的周期中位数'
        margin = max(2, (max(gaps)-min(gaps)+1)//2)
    elif prefs['typical_cycle']:
        length = prefs['typical_cycle']
        basis = '你填写的典型周期（历史记录不足）'
        margin = 3
    else:
        return {'available': False, 'reason': '补充更多历史记录，或自愿填写典型周期后再预估'}
    target = starts[-1] + timedelta(days=length)
    start_bound, end_bound = (target-timedelta(days=margin)).isoformat(), (target+timedelta(days=margin)).isoformat()
    if target + timedelta(days=margin) < today:
        return {'available': False, 'expired': True, 'start': start_bound, 'end': end_bound,
                'reason': '上次预估区间已过，周期信息待确认；未记录不代表推迟'}
    durations = [(date.fromisoformat(r['end_date'])-date.fromisoformat(r['start_date'])).days+1
                 for r in records[:6] if r['end_date']]
    duration = round(statistics.median(durations)) if durations else prefs['typical_duration']
    return dict(available=True, start=(target-timedelta(days=margin)).isoformat(),
                end=(target+timedelta(days=margin)).isoformat(),
                estimated_duration=duration, basis=basis, algorithm='median-v1',
                generated_on=today.isoformat())


def days(conn, uid):
    result = []
    for row in conn.execute('SELECT * FROM cycle_days WHERE uid=? ORDER BY date DESC', (uid,)):
        r = dict(row)
        r['symptoms'] = json.loads(r['symptoms'])
        extra = conn.execute('SELECT * FROM cycle_daily_details WHERE uid=? AND date=?', (uid, r['date'])).fetchone()
        r.update(dict(extra) if extra else dict(activity='', relief='', medication='', effectiveness='', note_shared=0, updated_at=''))
        result.append(r)
    return result


def care(conn, owner, viewer, today):
    row = conn.execute('SELECT * FROM cycle_care WHERE uid=? AND expires_on>=?',
                       (owner, today.isoformat())).fetchone()
    if not row or viewer not in (owner, row['recipient']):
        return None
    return dict(row)


def marks(records, prediction, start, end, owner):
    result = []
    for r in records:
        a = max(start, date.fromisoformat(r['start_date']))
        # An open record only confirms its start date, not an inferred duration.
        b = min(end, date.fromisoformat(r['end_date'] or r['start_date']))
        while a <= b:
            result.append(dict(date=a.isoformat(), kind='actual', uid=owner,
                               open_start=not bool(r['end_date'])))
            a += timedelta(days=1)
    if prediction.get('available'):
        a = max(start, date.fromisoformat(prediction['start']))
        b = min(end, date.fromisoformat(prediction['end']))
        while a <= b:
            result.append(dict(date=a.isoformat(), kind='predicted', uid=owner))
            a += timedelta(days=1)
    return result


def _snapshot_base(conn, uid, month, today=None):
    today = today or date.today()
    first = iso(month + '-01', '月份')
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    prefs = settings(conn, uid)
    own = periods(conn, uid)
    prediction = predict(own, prefs, today)
    active = next((r for r in own if not r['end_date']), None)
    partner_uid = other(uid)
    partner_prefs = settings(conn, partner_uid)
    partner = None
    calendar_marks = marks(own, prediction, first, last, uid)
    authorized = partner_prefs['share_with'] == uid
    partner_care = care(conn, partner_uid, uid, today)
    if (authorized and (partner_prefs['share_actual'] or partner_prefs['share_prediction'])) or partner_care:
        partner = dict(uid=partner_uid, care=partner_care)
        partner_records = periods(conn, partner_uid)
        if authorized and partner_prefs['share_actual']:
            # Explicit history cutoff, including a clipped ongoing/historical interval.
            since = partner_prefs['share_since']
            shared = [dict(start_date=max(r['start_date'], since), end_date=r['end_date'])
                      for r in partner_records if (r['end_date'] or today.isoformat()) >= since]
            partner['periods'] = shared
            calendar_marks += marks(shared, {}, first, last, partner_uid)
        if authorized and partner_prefs['share_prediction']:
            p = predict(partner_records, partner_prefs, today)
            # Do not expose historical statistics / basis to the recipient.
            partner['prediction'] = {k: p[k] for k in ('available', 'start', 'end') if k in p}
            calendar_marks += marks([], partner['prediction'], first, last, partner_uid)
    return dict(ok=True, uid=uid, month=month, settings=prefs, periods=own,
                days=days(conn, uid), active=active, prediction=prediction,
                care=care(conn, uid, uid, today), partner=partner,
                calendar=calendar_marks, disclaimer=DISCLAIMER)


def clear_notices(conn, uid):
    # Never send health content through existing shared/third-party push pipelines.
    conn.execute("DELETE FROM notifications WHERE target_uid=? AND target_action='view:cycle'", (uid,))


def _mutate_base(conn, uid, action, body, today=None):
    today = today or date.today()
    if not isinstance(body, dict):
        raise Error('请求须为 JSON 对象')
    if action == 'period':
        start = iso(body.get('start_date'), '开始日期')
        end = iso(body.get('end_date'), '结束日期', optional=True)
        if start > today or (end and end > today):
            raise Error('实际记录不能填写未来日期')
        if end and end < start:
            raise Error('结束日期不能早于开始日期')
        rid = body.get('id')
        if rid is not None:
            integer(rid, 1, 2**63-1, '记录编号')
            if not conn.execute('SELECT 1 FROM cycle_periods WHERE id=? AND uid=?', (rid, uid)).fetchone():
                raise Error('记录不存在', 404)
        overlap = conn.execute('''SELECT 1 FROM cycle_periods WHERE uid=? AND id!=?
            AND start_date<=? AND COALESCE(end_date,'9999-12-31')>=?''',
            (uid, rid or -1, end.isoformat() if end else '9999-12-31', start.isoformat())).fetchone()
        if overlap:
            raise Error('与已有经期重叠，请先确认或结束已有记录', 409)
        args = (start.isoformat(), end.isoformat() if end else None, datetime.now().isoformat(timespec='seconds'))
        if rid:
            conn.execute('UPDATE cycle_periods SET start_date=?,end_date=?,updated_at=? WHERE id=? AND uid=?', args+(rid, uid))
        else:
            rid = conn.execute('INSERT INTO cycle_periods(start_date,end_date,updated_at,uid) VALUES(?,?,?,?)', args+(uid,)).lastrowid
        clear_notices(conn, uid)
        return dict(ok=True, id=rid)
    if action in ('period/delete', 'day/delete'):
        if action == 'period/delete':
            rid = integer(body.get('id'), 1, 2**63-1, '记录编号')
            cur = conn.execute('DELETE FROM cycle_periods WHERE uid=? AND id=?', (uid, rid))
        else:
            dt = iso(body.get('date')).isoformat()
            cur = conn.execute('DELETE FROM cycle_days WHERE uid=? AND date=?', (uid, dt))
        if not cur.rowcount:
            raise Error('记录不存在', 404)
        clear_notices(conn, uid)
        return {'ok': True}
    if action == 'day':
        dt = iso(body.get('date'))
        if dt > today:
            raise Error('不能记录未来的身体感受')
        flow, pain = body.get('flow', ''), body.get('pain', '')
        if flow not in ('', '少', '中', '多') or pain not in ('', '无', '轻', '中', '重'):
            raise Error('经量或疼痛选项无效')
        symptoms = body.get('symptoms', [])
        if not isinstance(symptoms, list) or any(s not in SYMPTOMS for s in symptoms) or len(symptoms) > len(SYMPTOMS):
            raise Error('身体感受选项无效')
        mood = text(body.get('mood', ''), 30, '心情')
        note = text(body.get('note', ''), 1000, '备注')
        conn.execute('''INSERT INTO cycle_days(uid,date,flow,pain,symptoms,mood,note) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(uid,date) DO UPDATE SET flow=excluded.flow,pain=excluded.pain,
            symptoms=excluded.symptoms,mood=excluded.mood,note=excluded.note''',
            (uid, dt.isoformat(), flow, pain, json.dumps(symptoms, ensure_ascii=False), mood, note))
        return {'ok': True}
    if action == 'settings':
        p = settings(conn, uid)
        allowed = {'typical_cycle', 'typical_duration', 'prediction_enabled', 'reminders_enabled',
                   'reminder_days', 'share_actual', 'share_prediction', 'share_since'}
        if set(body)-allowed:
            raise Error('含未知设置项')
        for key in ('prediction_enabled', 'reminders_enabled', 'share_actual', 'share_prediction'):
            if key in body:
                if type(body[key]) is not bool:
                    raise Error('开关须为布尔值')
                p[key] = int(body[key])
        for key, lo, hi, label in [('typical_cycle', 15, 60, '典型周期'), ('typical_duration', 1, 30, '典型经期长度'), ('reminder_days', 0, 7, '提前提醒天数')]:
            if key in body:
                p[key] = integer(body[key], lo, hi, label, optional=key != 'reminder_days')
        if 'share_since' in body:
            p['share_since'] = iso(body['share_since'], '分享起始日期').isoformat()
        if p['share_actual'] or p['share_prediction']:
            p['share_since'] = p['share_since'] or today.isoformat()
            p['share_with'] = other(uid)
        else:
            p['share_with'] = ''
            p['share_since'] = ''
        fields = list(p)
        conn.execute('INSERT OR REPLACE INTO cycle_settings ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')', tuple(p.values()))
        clear_notices(conn, uid)
        return {'ok': True}
    if action == 'care':
        message = text(body.get('message', ''), 200, '关怀需求')
        if not message:
            conn.execute('DELETE FROM cycle_care WHERE uid=?', (uid,))
        else:
            expires = iso(body.get('expires_on'), '有效期')
            if not today <= expires <= today + timedelta(days=7):
                raise Error('有效期须为今天至未来七天内')
            conn.execute('INSERT OR REPLACE INTO cycle_care(uid,recipient,message,expires_on,acknowledged) VALUES(?,?,?,?,0)',
                         (uid, other(uid), message, expires.isoformat()))
        return {'ok': True}
    if action == 'care/ack':
        cur = conn.execute('UPDATE cycle_care SET acknowledged=1 WHERE uid=? AND recipient=? AND expires_on>=?',
                           (other(uid), uid, today.isoformat()))
        if not cur.rowcount:
            raise Error('关怀需求已撤回或过期', 404)
        return {'ok': True}
    if action == 'delete-all':
        if body.get('confirm') != 'DELETE_MY_CYCLE_DATA':
            raise Error('请确认删除本人全部经期数据')
        for table in ('cycle_periods', 'cycle_days', 'cycle_settings', 'cycle_care', 'cycle_reminders'):
            conn.execute('DELETE FROM '+table+' WHERE uid=?', (uid,))
        clear_notices(conn, uid)
        return {'ok': True}
    raise Error('接口不存在', 404)


def export_data(conn, uid):
    from cycle_collaboration import config, preferences, proposals
    return dict(version=2, exported_at=datetime.now().isoformat(timespec='seconds'),
                periods=periods(conn, uid), days=days(conn, uid), settings=settings(conn, uid),
                collaboration=config(conn,uid), preferences=preferences(conn,uid),
                proposals=proposals(conn,uid,date.today()),
                sources=[dict(r) for r in conn.execute('SELECT * FROM cycle_record_sources WHERE uid=?',(uid,))],
                care=care(conn, uid, uid, date.today()))


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


def snapshot(conn, uid, month, today=None):
    from cycle_collaboration import snapshot as collaborative_snapshot
    return collaborative_snapshot(conn, uid, month, today)


def mutate(conn, uid, action, body, today=None):
    from cycle_collaboration import mutate as collaborative_mutate
    return collaborative_mutate(conn, uid, action, body, today)


def reminder_pass(conn, now):
    from cycle_collaboration import reminder_pass as collaborative_reminders
    return collaborative_reminders(conn, now)
