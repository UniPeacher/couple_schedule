"""Shared persistence helpers for the cycle modules: per-user collaboration
config and reminder-preferences lookup, generic table upsert and notice-key purge."""


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
