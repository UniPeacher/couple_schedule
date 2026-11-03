"""Isolated browser-test fixture server. Never loads production data or cron jobs."""
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app

fixture = tempfile.TemporaryDirectory(prefix='cycle-browser-', dir=os.environ['TMPDIR'])
app.DATA_DIR = fixture.name
app.DB_PATH = os.path.join(fixture.name, 'sched.db')
app.SECRET = 'browser-test-secret-not-production'
app.init_db()
for uid in ('a', 'b'):
    app.CONN.execute('UPDATE users SET pw_hash=? WHERE uid=?', (app.hash_pw('cycle-test-password'), uid))
server = app.ThreadingHTTPServer(('127.0.0.1', 18796), app.Handler)
print('CYCLE_BROWSER_FIXTURE_READY', flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
    fixture.cleanup()
