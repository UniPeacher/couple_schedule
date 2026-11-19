"""Isolated browser-test fixture for cycle v2. Never loads production data."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

PORT = int(os.environ.get('CYCLE2_BROWSER_PORT', '18797'))
fixture = tempfile.mkdtemp(prefix='cycle2-browser-', dir=os.environ.get('TMPDIR') or None)
app.DATA_DIR = fixture
app.DB_PATH = os.path.join(fixture, 'sched.db')
app.SECRET = 'cycle2-browser-test-secret-not-production'
app.init_db()
for uid in ('a', 'b'):
    app.CONN.execute('UPDATE users SET pw_hash=? WHERE uid=?',
                     (app.hash_pw('cycle2-test-password'), uid))
app.CONN.commit()
server = app.ThreadingHTTPServer(('127.0.0.1', PORT), app.Handler)
print('CYCLE2_BROWSER_FIXTURE_READY', flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
