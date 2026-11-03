"""Collaborative cycle records: explicit grants, pending proxy entries and scoped notifications."""
import json
import secrets
import statistics
from datetime import date, datetime, timedelta
import cycle as c


def config(conn, uid):
    r = conn.execute('SELECT * FROM cycle_collab WHERE uid=?', (uid,)).fetchone()
    return dict(r) if r else dict(uid=uid, share_details=0, allow_proxy=0,
                                 allow_notifications=0, allow_notice_details=0)


def preferences(conn, uid):
    r = conn.execute('SELECT * FROM cycle_preferences WHERE uid=?', (uid,)).fetchone()
    # Role is chosen explicitly, not inferred from a/b identity or gender.
    return dict(r) if r else dict(uid=uid, role='recorder', enabled=0, upcoming=1,
                                 started=1, discomfort=1, pending=1, advance_days=2, notice_details=0)


def put(conn, table, data):
    keys = list(data)
    conn.execute('INSERT OR REPLACE INTO '+table+' ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')', tuple(data.values()))


def state(records, today):
    latest = records[0] if records else None
    if not latest:
        return dict(label='暂无已确认记录', day=None, pending_end=False)
    start = date.fromisoformat(latest['start_date'])
    end = latest.get('end_date')
    elapsed = (today-start).days+1
    if not end and 1 <= elapsed <= 10:
        return dict(label=f'本次第 {elapsed} 天 · 结束尚未确认', day=elapsed, pending_end=True)
    if not end:
        return dict(label='开始于 '+start.isoformat()+' · 结束待确认', day=None, pending_end=True)
    return dict(label='最近一次 '+latest['start_date']+' ～ '+end, day=None, pending_end=False)


def trend(records):
    starts = sorted(date.fromisoformat(r['start_date']) for r in records[:7])
    gaps = [(b-a).days for a,b in zip(starts, starts[1:])]
    return dict(intervals=gaps, count=len(records), median=round(statistics.median(gaps)) if gaps else None,
                minimum=min(gaps) if gaps else None, maximum=max(gaps) if gaps else None,
                spread=max(gaps)-min(gaps) if gaps else None)


def proxy_allowed(conn, owner, author):
    p, x = c.settings(conn, owner), config(conn, owner)
    return bool(author == c.other(owner) and p['share_with'] == author and p['share_actual'] and x['allow_proxy'])


def proposals(conn, uid, today):
    rows = conn.execute("SELECT * FROM cycle_proposals WHERE status='pending' AND (uid=? OR author_uid=?) ORDER BY id DESC", (uid,uid))
    return [dict(r) for r in rows if uid == r['uid'] or proxy_allowed(conn, r['uid'], uid)]


def snapshot(conn, uid, month, today=None):
    today = today or date.today()
    s = c._snapshot_base(conn, uid, month, today)
    s.update(collaboration=config(conn, uid), preferences=preferences(conn, uid),
             proposals=proposals(conn, uid, today), status=state(s['periods'], today), trend=trend(s['periods']))
    sources = {r['period_id']:dict(r) for r in conn.execute('SELECT * FROM cycle_record_sources WHERE uid=?',(uid,))}
    for r in s['periods']:
        r['source'] = sources.get(r['id'], dict(author_uid=uid, confirmed_by=uid, proposal_id=None))
    owner = c.other(uid)
    p, x = c.settings(conn, owner), config(conn, owner)
    grant = p['share_with'] == uid
    if grant and (x['share_details'] or x['allow_proxy']):
        if not s['partner']:
            s['partner'] = dict(uid=owner, care=c.care(conn, owner, uid, today))
    if s['partner']:
        partner = s['partner']
        partner['can_proxy'] = proxy_allowed(conn, owner, uid)
        if grant and p['share_actual']:
            shared = partner['periods']
            for r in shared:
                # Start before cutoff must not be presented as an actual new start.
                r['start_clipped'] = any(a['start_date'] < p['share_since'] and a['end_date'] == r['end_date'] for a in c.periods(conn, owner))
            partner['trend'] = trend([r for r in shared if not r['start_clipped']])
            partner['status'] = state(shared, today)
            if shared and shared[0]['start_clipped']:
                partner['status'] = dict(label='分享范围内有记录 · 实际开始早于分享范围',day=None,pending_end=not bool(shared[0]['end_date']))
            visible_ids = {r['id'] for r in c.periods(conn, owner) if r['start_date'] >= p['share_since']}
            partner['editable_periods'] = [dict(id=r['id'],start_date=r['start_date'],end_date=r['end_date']) for r in c.periods(conn,owner) if r['id'] in visible_ids] if partner['can_proxy'] else []
        if grant and p['share_prediction']:
            pred = c.predict(c.periods(conn, owner), p, today)
            partner['prediction'] = {k:pred[k] for k in ('available','start','end','expired','reason') if k in pred}
        if grant and x['share_details']:
            shared_days = []
            for d in c.days(conn, owner):
                if d['date'] < p['share_since']:
                    continue
                item = {k:v for k,v in d.items() if k not in ('note','note_shared')}
                if d['note_shared']:
                    item['note'] = d['note']
                shared_days.append(item)
            partner['days'] = shared_days
    return s


def purge(conn, owner=None, recipient=None):
    sql = 'SELECT notification_id FROM cycle_notice_keys WHERE 1=1'
    args = []
    if owner:
        sql += ' AND owner_uid=?'; args.append(owner)
    if recipient:
        sql += ' AND uid=?'; args.append(recipient)
    ids = [r[0] for r in conn.execute(sql,args) if r[0]]
    for nid in ids:
        conn.execute('DELETE FROM notifications WHERE id=?',(nid,))
    # Keep deduplication keys after removal to avoid re-sending historical events.


def notify(conn, owner, recipient, kind, key, detail, now):
    pref = preferences(conn,recipient)
    owner_p, owner_x = c.settings(conn,owner), config(conn,owner)
    if not pref['enabled'] or not pref[kind]:
        return 0
    if recipient != owner:
        if owner_p['share_since'] and owner_p['share_since'] > now.date().isoformat():
            return 0
        if owner_p['share_with'] != recipient or not owner_x['allow_notifications']:
            return 0
        if kind in ('upcoming','pending') and not owner_p['share_prediction']:
            return 0
        if kind == 'started' and not owner_p['share_actual']:
            return 0
        if kind == 'discomfort' and not owner_x['share_details']:
            return 0
    event_key = kind+':'+key
    inserted = conn.execute('INSERT OR IGNORE INTO cycle_notice_keys(uid,owner_uid,event_key,kind) VALUES(?,?,?,?)',
                            (recipient,owner,event_key,kind)).rowcount
    if not inserted:
        return 0
    # Awaiting-information notifications are always neutral, never label someone late.
    detailed = kind != 'pending' and pref['notice_details'] and (recipient == owner or owner_x['allow_notice_details'])
    title = '周期状态更新' if detailed else '私人状态提醒'
    content = detail if detailed else ('周期信息待确认；未记录不代表推迟。' if kind == 'pending' else '身体记录有更新，打开后查看。')
    cur = conn.execute("INSERT INTO notifications(target_uid,title,content,target_action,is_read,is_pushed,created_at) VALUES(?,?,?,'view:cycle',0,0,?)",
                       (recipient,title,content,now.isoformat(timespec='seconds')))
    conn.execute('UPDATE cycle_notice_keys SET notification_id=? WHERE uid=? AND owner_uid=? AND event_key=?',
                 (cur.lastrowid,recipient,owner,event_key))
    return 1


def dates(body,today):
    start = c.iso(body.get('start_date'),'开始日期')
    end = c.iso(body.get('end_date'),'结束日期',optional=True)
    if start > today or end and (end > today or end < start):
        raise c.Error('请填写有效的实际起止日期，不能是未来日期')
    return start.isoformat(),end.isoformat() if end else None


def mutate(conn, uid, action, body, today=None):
    today = today or date.today()
    now = datetime.combine(today,datetime.now().time())
    if not isinstance(body,dict):
        raise c.Error('请求须为 JSON 对象')
    if action == 'sharing':
        if not isinstance(body.get('settings'), dict) or not isinstance(body.get('collaboration'), dict):
            raise c.Error('分享设置无效')
        mutate(conn, uid, 'settings', body['settings'], today)
        return mutate(conn, uid, 'collaboration', body['collaboration'], today)
    if action == 'preferences':
        p = preferences(conn,uid)
        if set(body)-set(p) or 'uid' in body:
            raise c.Error('含未知提醒设置')
        for k,v in body.items():
            if k == 'role':
                if v not in ('recorder','supporter'):
                    raise c.Error('角色无效')
                p[k]=v
            elif k == 'advance_days':
                p[k]=c.integer(v,0,7,'提前天数')
            else:
                if type(v) is not bool:
                    raise c.Error('开关须为布尔值')
                p[k]=int(v)
        put(conn,'cycle_preferences',p)
        purge(conn,recipient=uid)
        return dict(ok=True)
    if action == 'collaboration':
        x = config(conn,uid)
        if set(body)-set(x) or 'uid' in body:
            raise c.Error('含未知协作设置')
        for k,v in body.items():
            if type(v) is not bool:
                raise c.Error('开关须为布尔值')
            x[k]=int(v)
        if any(x[k] for k in ('share_details','allow_proxy','allow_notifications','allow_notice_details')):
            p = c.settings(conn,uid)
            if not p['share_with']:
                raise c.Error('请先选择日期分享范围，再授权身体信息与协作')
        put(conn,'cycle_collab',x)
        purge(conn,owner=uid)
        if not x['allow_proxy']:
            conn.execute("UPDATE cycle_proposals SET status='revoked' WHERE uid=? AND status='pending'",(uid,))
        return dict(ok=True)
    if action == 'proposal':
        owner = c.other(uid)
        if not proxy_allowed(conn,owner,uid):
            raise c.Error('对方尚未授权代记，或授权已撤回',403)
        start,end=dates(body,today)
        p = c.settings(conn,owner)
        if start < p['share_since']:
            raise c.Error('代记日期不能早于对方的分享范围')
        rid=body.get('period_id')
        version=''
        if rid is not None:
            c.integer(rid,1,2**63-1,'记录编号')
            row=conn.execute('SELECT * FROM cycle_periods WHERE id=? AND uid=? AND start_date>=?',(rid,owner,p['share_since'])).fetchone()
            if not row:
                raise c.Error('记录不存在或不在授权范围',404)
            version=row['updated_at']
        pending=conn.execute("SELECT COUNT(*) FROM cycle_proposals WHERE uid=? AND status='pending'",(owner,)).fetchone()[0]
        if pending >= 20:
            raise c.Error('待确认记录较多，请先处理已有记录',409)
        cur=conn.execute('INSERT INTO cycle_proposals(uid,author_uid,period_id,base_version,start_date,end_date,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
                         (owner,uid,rid,version,start,end,'pending',now.isoformat(timespec='microseconds')))
        return dict(ok=True,id=cur.lastrowid)
    if action in ('proposal/confirm','proposal/reject','proposal/cancel'):
        pid=c.integer(body.get('id'),1,2**63-1,'代记编号')
        row=conn.execute("SELECT * FROM cycle_proposals WHERE id=? AND status='pending'",(pid,)).fetchone()
        if not row or (action=='proposal/cancel' and row['author_uid']!=uid) or (action!='proposal/cancel' and row['uid']!=uid):
            raise c.Error('待确认记录不存在',404)
        if not proxy_allowed(conn,row['uid'],row['author_uid']):
            raise c.Error('代记授权已撤回',403)
        if action == 'proposal/confirm':
            if row['period_id']:
                current=conn.execute('SELECT * FROM cycle_periods WHERE id=? AND uid=?',(row['period_id'],uid)).fetchone()
                if not current or current['updated_at'] != row['base_version']:
                    raise c.Error('原记录已更改，请拒绝旧代记或重新提交',409)
            data=dict(start_date=body.get('start_date',row['start_date']),end_date=body.get('end_date',row['end_date']))
            if row['period_id']:
                data['id']=row['period_id']
            result=mutate(conn,uid,'period',data,today)
            put(conn,'cycle_record_sources',dict(period_id=result['id'],uid=uid,author_uid=row['author_uid'],confirmed_by=uid,proposal_id=pid))
            conn.execute("UPDATE cycle_proposals SET status='confirmed' WHERE id=?",(pid,))
            return result
        conn.execute('UPDATE cycle_proposals SET status=? WHERE id=?',('cancelled' if action.endswith('cancel') else 'rejected',pid))
        return dict(ok=True)
    if action == 'day':
        extra={k:body.get(k,'') for k in ('activity','relief','medication','effectiveness')}
        if extra['activity'] not in ('','不影响','有些影响','明显影响') or extra['effectiveness'] not in ('','未尝试','无改善','有缓解','明显缓解'):
            raise c.Error('活动影响或缓解效果选项无效')
        for k in ('relief','medication'):
            extra[k]=c.text(extra[k],300,'缓解方式或用药')
        shared=body.get('note_shared',False)
        if type(shared) is not bool:
            raise c.Error('备注分享须为布尔值')
        result=c._mutate_base(conn,uid,action,body,today)
        dt=c.iso(body.get('date')).isoformat()
        put(conn,'cycle_daily_details',dict(uid=uid,date=dt,**extra,note_shared=int(shared),updated_at=now.isoformat(timespec='microseconds')))
        if dt==today.isoformat() and (body.get('pain') in ('中','重') or extra['activity']=='明显影响'):
            notify(conn,uid,c.other(uid),'discomfort',dt,'对方今天记录了中度或以上疼痛，或明显影响日常活动。',now)
        else:
            for r in conn.execute("SELECT notification_id FROM cycle_notice_keys WHERE owner_uid=? AND event_key=?",(uid,'discomfort:'+dt)):
                if r[0]:
                    conn.execute('DELETE FROM notifications WHERE id=?',(r[0],))
        return result
    old = None
    if action=='period' and body.get('id'):
        old=conn.execute('SELECT * FROM cycle_periods WHERE uid=? AND id=?',(uid,body['id'])).fetchone()
    result=c._mutate_base(conn,uid,action,body,today)
    if action=='period':
        # Distinct edit versions protect proxy confirmation from lost updates.
        conn.execute('UPDATE cycle_periods SET updated_at=? WHERE id=?',(now.isoformat(timespec='microseconds')+'-'+secrets.token_hex(4),result['id']))
        if not old:
            put(conn,'cycle_record_sources',dict(period_id=result['id'],uid=uid,author_uid=uid,confirmed_by=uid,proposal_id=None))
        if not old and body.get('start_date')==today.isoformat():
            notify(conn,uid,c.other(uid),'started',str(result['id']),'对方已确认今天经期开始。',now)
    elif action=='period/delete':
        conn.execute('DELETE FROM cycle_record_sources WHERE uid=? AND period_id=?',(uid,body['id']))
        purge(conn,owner=uid)
    elif action=='day/delete':
        conn.execute('DELETE FROM cycle_daily_details WHERE uid=? AND date=?',(uid,body['date']))
        purge(conn,owner=uid)
    elif action=='settings':
        purge(conn,owner=uid)
        p=c.settings(conn,uid)
        if not p['share_with']:
            conn.execute('DELETE FROM cycle_collab WHERE uid=?',(uid,))
        if not p['share_actual'] or not p['share_with']:
            conn.execute("UPDATE cycle_proposals SET status='revoked' WHERE uid=? AND status='pending'",(uid,))
    elif action=='delete-all':
        purge(conn,owner=uid); purge(conn,recipient=uid)
        for table in ('cycle_daily_details','cycle_collab','cycle_preferences','cycle_record_sources'):
            conn.execute('DELETE FROM '+table+' WHERE uid=?',(uid,))
        conn.execute('DELETE FROM cycle_proposals WHERE uid=? OR author_uid=?',(uid,uid))
        conn.execute('DELETE FROM cycle_notice_keys WHERE uid=? OR owner_uid=?',(uid,uid))
    return result


def reminder_pass(conn,now):
    if now.hour<9:
        return 0
    today=now.date(); count=0
    for owner in ('a','b'):
        records=c.periods(conn,owner)
        if not records:
            continue
        p=c.settings(conn,owner); pred=c.predict(records,p,today)
        for recipient in (owner,c.other(owner)):
            pref=preferences(conn,recipient)
            enabled=pref['enabled'] or recipient==owner and p['reminders_enabled']
            if not enabled:
                continue
            key=records[0]['start_date']
            # Preserve original own reminder switch during the migration.
            if recipient==owner and p['reminders_enabled'] and not pref['enabled']:
                if pred.get('available') and date.fromisoformat(pred['start'])-timedelta(days=p['reminder_days'])<=today<=date.fromisoformat(pred['end']):
                    if conn.execute('INSERT OR IGNORE INTO cycle_reminders(uid,day) VALUES(?,?)',(owner,'upcoming:'+key)).rowcount:
                        conn.execute("INSERT INTO notifications(target_uid,title,content,target_action,is_read,is_pushed,created_at) VALUES(?,?,?,'view:cycle',0,0,?)",(owner,'私人提醒','你有一条私人提醒，请打开个人记录查看。',now.isoformat(timespec='seconds')))
                        count+=1
                continue
            if pred.get('available'):
                due=date.fromisoformat(pred['start'])-timedelta(days=pref['advance_days'])
                if due<=today<=date.fromisoformat(pred['end']):
                    count+=notify(conn,owner,recipient,'upcoming',key,'根据已有记录，近期可能进入经期；预估仅供参考。',now)
            elif pred.get('expired'):
                count+=notify(conn,owner,recipient,'pending',key,'周期信息待确认；未记录不代表推迟。',now)
    return count
