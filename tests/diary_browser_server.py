"""手账浏览器验收：独立数据库、合成照片与视频、固定测试账号。"""
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

app.SECRET = 'diary-browser-test-secret'
app.init_db()
os.makedirs(app.PHOTOS_DIR, exist_ok=True)
for uid in ('a', 'b'):
    app.CONN.execute('UPDATE users SET pw_hash=? WHERE uid=?', (app.hash_pw('diary-test-password'), uid))
for index, color in enumerate(('pink', 'lightblue')):
    photo = f'sample{index}.jpg'
    video = f'sample{index}.mp4'
    for filename, options in ((photo, ['-frames:v', '1']),
                              (video, ['-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart'])):
        subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i',
                        f'color=c={color}:s=640x480:r=24', *options,
                        os.path.join(app.PHOTOS_DIR, filename)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    app.images.ensure_thumb(os.path.join(app.PHOTOS_DIR, photo), os.path.join(app.PHOTOS_DIR, 'tb_' + photo))
    app.images.ensure_view_image(os.path.join(app.PHOTOS_DIR, photo), os.path.join(app.PHOTOS_DIR, 'vw_' + photo))
    app.images.ensure_live_preview(os.path.join(app.PHOTOS_DIR, video))
    diary_id = app.CONN.execute(
        'INSERT INTO diaries(date,title,location,mood,content,author_uid,created_at) VALUES(?,?,?,?,?,?,?)',
        ('2026-11-16', '实况播放验收 ' + str(index + 1), '', '', '隔离样例', 'a', '2026-11-16 12:00:00')).lastrowid
    app.CONN.execute('INSERT INTO diary_photos(diary_id,file_name,live_video,sort_order) VALUES(?,?,?,?)',
                     (diary_id, photo, video, index))
print('DIARY_BROWSER_FIXTURE_READY', flush=True)
app.MediaHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '18796'))), app.Handler).serve_forever()
