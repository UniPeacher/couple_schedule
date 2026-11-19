"""日历浏览器验收：独立数据库与固定测试账号。"""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='calendar-browser-')
import app
import web_assets

app.SECRET = 'calendar-browser-test-secret'
app.init_db()
for uid in ('a', 'b'):
    app.CONN.execute('UPDATE users SET pw_hash=? WHERE uid=?', (app.hash_pw('calendar-test-password'), uid))
app.CONN.execute('DELETE FROM anniversaries')
app.CONN.execute('INSERT INTO anniversaries(title,date,target_type) VALUES(?,?,?)',
                 ('在一起', '2025-05-20', 'love'))
app.CONN.execute('INSERT INTO anniversaries(title,date,target_type) VALUES(?,?,?)',
                 ('生日', '2000-05-20', 'birthday'))
for title in ('一起去看海', '傍晚的散步'):
    diary_id = app.CONN.execute(
        'INSERT INTO diaries(date,title,content,author_uid,created_at) VALUES(?,?,?,?,?)',
        ('2026-05-20', title, '今天也一起记录生活。', 'a', '2026-05-20 12:00:00')).lastrowid
    if title == '一起去看海':
        os.makedirs(app.PHOTOS_DIR, exist_ok=True)
        photo, _ = web_assets.get_image_asset('dog-couple')
        if photo:
            Path(app.PHOTOS_DIR, 'tb_sample.png').write_bytes(photo)
            Path(app.PHOTOS_DIR, 'sample.png').write_bytes(photo)
            app.CONN.execute('INSERT INTO diary_photos(diary_id,file_name,sort_order) VALUES(?,?,?)',
                             (diary_id, 'sample.png', 0))
print('CALENDAR_BROWSER_READY http://127.0.0.1:18797', flush=True)
app.MediaHTTPServer(('127.0.0.1', 18797), app.Handler).serve_forever()
