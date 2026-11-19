"""Isolated browser-test fixture for the wishes page. Never loads production data."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

PORT = 18798
fixture = tempfile.mkdtemp(prefix='wishes-browser-', dir=os.environ.get('TMPDIR') or None)
app.DATA_DIR = fixture
app.DB_PATH = os.path.join(fixture, 'sched.db')
app.SECRET = 'wishes-browser-test-secret-not-production'
app.init_db()
for uid in ('a', 'b'):
    app.CONN.execute('UPDATE users SET pw_hash=? WHERE uid=?',
                     (app.hash_pw('wishes-test-password'), uid))
app.CONN.commit()
server = app.ThreadingHTTPServer(('127.0.0.1', PORT), app.Handler)
print('WISHES_BROWSER_FIXTURE_READY', flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()