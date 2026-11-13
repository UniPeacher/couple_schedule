"""Cycle v2 collaboration: sharing grants, proxy proposals, scoped notifications, PMS reminder.

Reuses v1 `cycle_collab` / `cycle_preferences` (no id references) so a user's
existing role and privacy switches carry over; everything that points at a period
or a notice lives in its own cycle2_ table so v1 rows stay untouched.
"""
import json
import secrets
import sqlite3
from datetime import date, datetime, timedelta

import cycle as c
import cycle2
import cycle_engine as engine
from cycle_collaboration import config, put, purge

SCHEMA_COLLAB2 = """
CREATE TABLE IF NOT EXISTS cycle2_proposals (
 id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, author_uid TEXT NOT NULL,
 period_id INTEGER, base_version TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL,
 end_date TEXT, status TEXT NOT NULL DEFAULT 'pending',
 created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_cycle2_proposals ON cycle2_proposals(uid,status);
CREATE TABLE IF NOT EXISTS cycle2_record_sources (
 period_id INTEGER PRIMARY KEY, uid TEXT NOT NULL, author_uid TEXT NOT NULL,
 confirmed_by TEXT NOT NULL, proposal_id INTEGER);
CREATE TABLE IF NOT EXISTS cycle2_notice_keys (
 id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, owner_uid TEXT NOT NULL,
 event_key TEXT NOT NULL, kind TEXT NOT NULL, notification_id INTEGER,
 UNIQUE(uid,owner_uid,event_key));
"""
SCHEMA = SCHEMA_COLLAB2   # convenience for init_db


def ensure_schema(conn):
    """Create v2 tables and add the PMS switch to the shared preference table once."""
    conn.executescript(SCHEMA_COLLAB2)
    try:
        conn.execute('ALTER TABLE cycle_preferences ADD COLUMN pms INTEGER NOT NULL DEFAULT 1')
    except sqlite3.OperationalError:
        pass


def preferences(conn, uid):
    from cycle_collaboration import preferences as base
    p = base(conn, uid)
    p.setdefault('pms', 1)
    return p


def proxy_allowed(conn, owner, author):
    p, x = cycle2.settings(conn, owner), config(conn, owner)
    return bool(author == c.other(owner) and p['share_with'] == author
                and p['share_actual'] and x['allow_proxy'])


def proposals(conn, uid, today):
    rows = conn.execute("SELECT * FROM cycle2_proposals WHERE status='pending' "
                        "AND (uid=? OR author_uid=?) ORDER BY id DESC", (uid, uid))
    return [dict(r) for r in rows if uid == r['uid'] or proxy_allowed(conn, r['uid'], uid)]


def state(records, today):
    latest = records[0] if records else None
    if not latest:
        return dict(label='暂无已确认记录', day=None, pending_end=False)
    start = date.fromisoformat(latest['start_date'])
    end = latest.get('end_date')
    elapsed = (today - start).days + 1
    if not end and 1 <= elapsed <= 10:
        return dict(label='本次第 %d 天 · 结束尚未确认' % elapsed, day=elapsed, pending_end=True)
    if not end:
        return dict(label='开始于 ' + start.isoformat() + ' · 结束待确认', day=None, pending_end=True)
    return dict(label='最近一次 %s ～ %s' % (latest['start_date'], end), day=None, pending_end=False)


def notify(conn, owner, recipient, kind, key, detail, now):
    pref = preferences(conn, recipient)
    owner_p, owner_x = cycle2.settings(conn, owner), config(conn, owner)
    if not pref['enabled'] or not pref.get(kind):
        return 0
    if recipient != owner:
        if owner_p['share_since'] and owner_p['share_since'] > now.date().isoformat():
            return 0
        if owner_p['share_with'] != recipient or not owner_x['allow_notifications']:
            return 0
        # Prediction-derived notices (upcoming / pms / pending) follow the prediction grant.
        if kind in ('upcoming', 'pms', 'pending') and not owner_p['share_prediction']:
            return 0
        if kind == 'started' and not owner_p['share_actual']:
            return 0
        if kind == 'discomfort' and not owner_x['share_details']:
            return 0
    event_key = kind + ':' + key
    inserted = conn.execute('INSERT OR IGNORE INTO cycle2_notice_keys(uid,owner_uid,event_key,kind) '
                            'VALUES(?,?,?,?)', (recipient, owner, event_key, kind)).rowcount
    if not inserted:
        return 0
    detailed = kind != 'pending' and pref['notice_details'] and \
        (recipient == owner or owner_x['allow_notice_details'])
    title = '周期状态更新' if detailed else '私人状态提醒'
    if detailed:
        content = detail
    elif kind == 'pending':
        content = '周期信息待确认；未记录不代表推迟。'
    elif kind == 'pms':
        content = '对方可能临近经前期，打开后查看可提供的照顾。'
    else:
        content = '身体记录有更新，打开后查看。'
    cur = conn.execute(
        "INSERT INTO notifications(target_uid,title,content,target_action,is_read,is_pushed,created_at) "
        "VALUES(?,?,?,'view:cycle2',0,0,?)",
        (recipient, title, content, now.isoformat(timespec='seconds')))
    conn.execute('UPDATE cycle2_notice_keys SET notification_id=? '
                 'WHERE uid=? AND owner_uid=? AND event_key=?',
                 (cur.lastrowid, recipient, owner, event_key))
    return 1


def purge2(conn, owner=None, recipient=None):
    """Drop the notification rows, keep the dedup keys so old events never resend."""
    sql = 'SELECT notification_id FROM cycle2_notice_keys WHERE 1=1'
    args = []
    if owner:
        sql += ' AND owner_uid=?'
        args.append(owner)
    if recipient:
        sql += ' AND uid=?'
        args.append(recipient)
    for (nid,) in conn.execute(sql, args).fetchall():
        if nid:
            conn.execute('DELETE FROM notifications WHERE id=?', (nid,))


def snapshot(conn, uid, month, today=None):
    today = today or date.today()
    s = cycle2.snapshot(conn, uid, month, today)
    s.update(collaboration=config(conn, uid), preferences=preferences(conn, uid),
             proposals=proposals(conn, uid, today),
             status=state(s['periods'], today))
    sources = {r['period_id']: dict(r) for r in
               conn.execute('SELECT * FROM cycle2_record_sources WHERE uid=?', (uid,))}
    for r in s['periods']:
        r['source'] = sources.get(r['id'], dict(author_uid=uid, confirmed_by=uid, proposal_id=None))
    s['partner'] = partner_view(conn, uid, today, s)
    return s


def partner_view(conn, uid, today, own_snapshot):
    """What `uid` may see of the other account; None until an explicit grant exists."""
    owner = c.other(uid)
    p, x = cycle2.settings(conn, owner), config(conn, owner)
    grant = p['share_with'] == uid
    partner_care = c.care(conn, owner, uid, today)
    if not (grant and (p['share_actual'] or p['share_prediction'] or x['share_details'])) and not partner_care:
        return None
    partner = dict(uid=owner, care=partner_care, can_proxy=proxy_allowed(conn, owner, uid))
    if grant and p['share_actual']:
        since = p['share_since']
        shared = [dict(id=r['id'], start_date=max(r['start_date'], since), end_date=r['end_date'],
                       start_clipped=r['start_date'] < since)
                  for r in cycle2.periods(conn, owner) if (r['end_date'] or today.isoformat()) >= since]
        partner['periods'] = shared
        partner['status'] = state([r for r in shared if not r['start_clipped']], today)
        partner['editable_periods'] = ([dict(id=r['id'], start_date=r['start_date'], end_date=r['end_date'])
                                        for r in shared if not r['start_clipped']]
                                       if partner['can_proxy'] else [])
    if grant and p['share_prediction']:
        recs, temps = cycle2.periods(conn, owner), cycle2.bbt(conn, owner)
        pred = engine.predict(recs, p, today, engine.luteal_from_bbt(recs, temps))
        # Recipient never receives history statistics or the calibration trail.
        partner['prediction'] = {k: pred[k] for k in
                                 ('available', 'estimated_start', 'window_start', 'window_end',
                                  'expired', 'reason') if k in pred}
        ph = engine.phase_of(today, recs, pred, temps)
        partner['today'] = ({k: ph[k] for k in ('phase', 'label', 'day', 'cycle_day')} if ph else None)
    if grant and x['share_details']:
        days = []
        for d in cycle2.notes(conn, owner):
            if d['date'] < p['share_since']:
                continue
            item = {k: v for k, v in d.items() if k not in ('note', 'note_shared')}
            if d['note_shared']:
                item['note'] = d['note']
            days.append(item)
        partner['days'] = days
    return partner


def dates(body, today):
    start = c.iso(body.get('start_date'), '开始日期')
    end = c.iso(body.get('end_date'), '结束日期', optional=True)
    if start > today or end and (end > today or end < start):
        raise c.Error('请填写有效的实际起止日期，不能是未来日期')
    return start.isoformat(), end.isoformat() if end else None


def mutate(conn, uid, action, body, today=None):
    today = today or date.today()
    now = datetime.combine(today, datetime.now().time())
    if not isinstance(body, dict):
        raise c.Error('请求须为 JSON 对象')
    if action == 'sharing':
        if not isinstance(body.get('settings'), dict) or not isinstance(body.get('collaboration'), dict):
            raise c.Error('分享设置无效')
        mutate(conn, uid, 'settings', body['settings'], today)
        return mutate(conn, uid, 'collaboration', body['collaboration'], today)
    if action == 'preferences':
        p = preferences(conn, uid)
        if set(body) - set(p) or 'uid' in body:
            raise c.Error('含未知提醒设置')
        for k, v in body.items():
            if k == 'role':
                if v not in ('recorder', 'supporter'):
                    raise c.Error('角色无效')
                p[k] = v
            elif k == 'advance_days':
                p[k] = c.integer(v, 0, 7, '提前天数')
            else:
                if type(v) is not bool:
                    raise c.Error('开关须为布尔值')
                p[k] = int(v)
        put(conn, 'cycle_preferences', p)
        purge2(conn, recipient=uid)
        return dict(ok=True)
    if action == 'collaboration':
        x = config(conn, uid)
        if set(body) - set(x) or 'uid' in body:
            raise c.Error('含未知协作设置')
        for k, v in body.items():
            if type(v) is not bool:
                raise c.Error('开关须为布尔值')
            x[k] = int(v)
        if any(x[k] for k in ('share_details', 'allow_proxy', 'allow_notifications', 'allow_notice_details')):
            if not cycle2.settings(conn, uid)['share_with']:
                raise c.Error('请先选择日期分享范围，再授权身体信息与协作')
        put(conn, 'cycle_collab', x)
        purge2(conn, owner=uid)
        if not x['allow_proxy']:
            conn.execute("UPDATE cycle2_proposals SET status='revoked' "
                         "WHERE uid=? AND status='pending'", (uid,))
        return dict(ok=True)
    if action == 'proposal':
        owner = c.other(uid)
        if not proxy_allowed(conn, owner, uid):
            raise c.Error('对方尚未授权代记，或授权已撤回', 403)
        start, end = dates(body, today)
        p = cycle2.settings(conn, owner)
        if start < p['share_since']:
            raise c.Error('代记日期不能早于对方的分享范围')
        rid = body.get('period_id')
        version = ''
        if rid is not None:
            c.integer(rid, 1, 2**63 - 1, '记录编号')
            row = conn.execute('SELECT * FROM cycle2_periods WHERE id=? AND uid=? AND start_date>=?',
                               (rid, owner, p['share_since'])).fetchone()
            if not row:
                raise c.Error('记录不存在或不在授权范围', 404)
            version = row['updated_at']
        pending = conn.execute("SELECT COUNT(*) FROM cycle2_proposals WHERE uid=? AND status='pending'",
                               (owner,)).fetchone()[0]
        if pending >= 20:
            raise c.Error('待确认记录较多，请先处理已有记录', 409)
        cur = conn.execute('INSERT INTO cycle2_proposals(uid,author_uid,period_id,base_version,'
                           'start_date,end_date,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
                           (owner, uid, rid, version, start, end, 'pending',
                            now.isoformat(timespec='microseconds')))
        return dict(ok=True, id=cur.lastrowid)
    if action in ('proposal/confirm', 'proposal/reject', 'proposal/cancel'):
        pid = c.integer(body.get('id'), 1, 2**63 - 1, '代记编号')
        row = conn.execute("SELECT * FROM cycle2_proposals WHERE id=? AND status='pending'",
                           (pid,)).fetchone()
        if not row or (action == 'proposal/cancel' and row['author_uid'] != uid) or \
                (action != 'proposal/cancel' and row['uid'] != uid):
            raise c.Error('待确认记录不存在', 404)
        if not proxy_allowed(conn, row['uid'], row['author_uid']):
            raise c.Error('代记授权已撤回', 403)
        if action == 'proposal/confirm':
            if row['period_id']:
                current = conn.execute('SELECT * FROM cycle2_periods WHERE id=? AND uid=?',
                                       (row['period_id'], uid)).fetchone()
                if not current or current['updated_at'] != row['base_version']:
                    raise c.Error('原记录已更改，请拒绝旧代记或重新提交', 409)
            data = dict(start_date=body.get('start_date', row['start_date']),
                        end_date=body.get('end_date', row['end_date']))
            if row['period_id']:
                data['id'] = row['period_id']
            result = cycle2.mutate(conn, uid, 'period', data, today)
            put(conn, 'cycle2_record_sources',
                dict(period_id=result['id'], uid=uid, author_uid=row['author_uid'],
                     confirmed_by=uid, proposal_id=pid))
            conn.execute("UPDATE cycle2_proposals SET status='confirmed' WHERE id=?", (pid,))
            return result
        conn.execute('UPDATE cycle2_proposals SET status=? WHERE id=?',
                     ('cancelled' if action.endswith('cancel') else 'rejected', pid))
        return dict(ok=True)
    if action == 'period':
        body = dict(body)
        rid = body.get('id')
        # Clear scoped notices first: the purge must not delete what this edit sends.
        purge2(conn, owner=uid)
        result = cycle2.mutate(conn, uid, action, body, today)
        # Unique version per write: a same-day edit must still invalidate pending proposals.
        conn.execute('UPDATE cycle2_periods SET updated_at=? WHERE id=?',
                     (now.isoformat(timespec='microseconds') + '-' + secrets.token_hex(4),
                      result['id']))
        if rid is None:
            put(conn, 'cycle2_record_sources',
                dict(period_id=result['id'], uid=uid, author_uid=uid, confirmed_by=uid, proposal_id=None))
            if body.get('start_date') == today.isoformat():
                notify(conn, uid, c.other(uid), 'started', str(result['id']),
                       '对方已确认今天经期开始。', now)
        return result
    if action == 'period/delete':
        purge2(conn, owner=uid)
        result = cycle2.mutate(conn, uid, action, body, today)
        conn.execute('DELETE FROM cycle2_record_sources WHERE period_id=? AND uid=?',
                     (body.get('id'), uid))
        return result
    if action == 'day':
        extra = {k: body.get(k, '') for k in ('activity', 'relief', 'medication', 'effectiveness')}
        shared = body.get('note_shared', False)
        if type(shared) is not bool:
            raise c.Error('备注分享须为布尔值')
        result = cycle2.mutate(conn, uid, 'day', dict(body, note_shared=int(shared)), today)
        dt = c.iso(body.get('date')).isoformat()
        # Replacing today's discomfort notice keeps the panel truthful; old days never notify.
        purge2_event(conn, uid, 'discomfort:' + dt)
        if dt == today.isoformat() and (body.get('pain') in ('中', '重')
                                        or extra['activity'] in ('明显影响', '严重影响')):
            notify(conn, uid, c.other(uid), 'discomfort', dt,
                   '对方今天记录了中度或以上疼痛，或明显影响日常活动。', now)
        return result
    if action in ('day/delete', 'bbt', 'bbt/delete', 'settings'):
        result = cycle2.mutate(conn, uid, action, body, today)
        if action == 'settings':
            purge2(conn, owner=uid)
            p = cycle2.settings(conn, uid)
            if not p['share_with']:
                conn.execute('DELETE FROM cycle_collab WHERE uid=?', (uid,))
            if not p['share_actual'] or not p['share_with']:
                conn.execute("UPDATE cycle2_proposals SET status='revoked' "
                             "WHERE uid=? AND status='pending'", (uid,))
        else:
            purge2(conn, owner=uid)
        return result
    if action == 'delete-all':
        result = cycle2.mutate(conn, uid, action, body, today)
        purge2(conn, owner=uid)
        purge2(conn, recipient=uid)
        conn.execute('DELETE FROM cycle2_proposals WHERE uid=? OR author_uid=?', (uid, uid))
        conn.execute('DELETE FROM cycle2_notice_keys WHERE uid=? OR owner_uid=?', (uid, uid))
        return result
    raise c.Error('未知操作', 400)


def purge2_event(conn, owner, event_key):
    for (nid,) in conn.execute('SELECT notification_id FROM cycle2_notice_keys '
                               'WHERE owner_uid=? AND event_key=?',
                               (owner, event_key)).fetchall():
        if nid:
            conn.execute('DELETE FROM notifications WHERE id=?', (nid,))
    conn.execute('DELETE FROM cycle2_notice_keys WHERE owner_uid=? AND event_key=?',
                 (owner, event_key))


def reminder_pass(conn, now):
    """Upcoming / PMS / pending notices, at most once per recorded cycle, from 09:00."""
    if now.hour < 9:
        return 0
    today = now.date()
    count = 0
    for owner in ('a', 'b'):
        records = cycle2.periods(conn, owner)
        if not records:
            continue
        p = cycle2.settings(conn, owner)
        temps = cycle2.bbt(conn, owner)
        pred = engine.predict(records, p, today, engine.luteal_from_bbt(records, temps))
        key = records[0]['start_date']
        for recipient in (owner, c.other(owner)):
            pref = preferences(conn, recipient)
            if not (pref['enabled'] or (recipient == owner and p['reminders_enabled'])):
                continue
            if recipient == owner and p['reminders_enabled'] and not pref['enabled']:
                if pred.get('available'):
                    due = date.fromisoformat(pred['window_start']) - timedelta(days=p['reminder_days'])
                    if due <= today <= date.fromisoformat(pred['window_end']):
                        if conn.execute('INSERT OR IGNORE INTO cycle_reminders(uid,day) '
                                        'VALUES(?,?)', (owner, 'upcoming:' + key)).rowcount:
                            conn.execute(
                                "INSERT INTO notifications(target_uid,title,content,target_action,"
                                "is_read,is_pushed,created_at) VALUES(?,?,?,'view:cycle2',0,0,?)",
                                (owner, '私人提醒', '你有一条私人提醒，请打开个人记录查看。',
                                 now.isoformat(timespec='seconds')))
                            count += 1
                continue
            if pred.get('available'):
                start = date.fromisoformat(pred['window_start'])
                end = date.fromisoformat(pred['window_end'])
                if start - timedelta(days=pref['advance_days']) <= today <= end:
                    count += notify(conn, owner, recipient, 'upcoming', key,
                                    '根据已有记录，近期可能进入经期；预估仅供参考。', now)
                # Luteal-phase notice: starts `advance_days` before the predicted period,
                # capped by the luteal length so it never fires mid-follicular.
                luteal = pred.get('luteal_length') or engine.LUTEAL_DEFAULT
                pms_lead = min(max(pref['advance_days'], 2), max(luteal - 3, 2))
                pms_day = start - timedelta(days=pms_lead)
                if pms_day <= today < start:
                    count += notify(conn, owner, recipient, 'pms', key,
                                    '对方可能进入经前期，情绪或精力可能有变化；这是基于周期的估计。', now)
            elif pred.get('expired'):
                count += notify(conn, owner, recipient, 'pending', key,
                                '周期信息待确认；未记录不代表推迟。', now)
    return count
