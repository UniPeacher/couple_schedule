"""Scoped settings regression tests; synthetic local SQLite only."""
import json
import sqlite3
import unittest
import app
import settings_service as service
import test_cycle as base_tests

class ScopedSettingsTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row;self.c.executescript(app.SCHEMA)
        self.c.executemany('INSERT INTO users(uid,name,pw_hash,week1,wx_uid) VALUES(?,?,?,?,?)', [('a','Alpha','hash','2026-08-31','TOKEN_A_SENTINEL'),('b','Beta','hash','2026-09-07','TOKEN_B_SENTINEL')])
        self.c.executemany('INSERT INTO meta(key,value) VALUES(?,?)',[('window_start','08:00'),('window_end','22:00'),('min_gap','20'),('llm_api_key','KEY_SENTINEL'),('llm_api_base','https://example.invalid/v1'),('llm_model','test-model')])
        self.c.execute("INSERT INTO summary_cache VALUES('x','{}','now')");self.c.commit()
    def tearDown(self):self.c.close()
    def apply(self,uid,body):
        with self.c:return service.apply(self.c,uid,body)
    def raw(self):return dict(self.c.execute('SELECT key,value FROM meta'))
    def test_payload_never_contains_credentials(self):
        for uid in ('a','b'):
            p=service.payload(self.c,uid);raw=json.dumps(p)
            for token in ('TOKEN_A_SENTINEL','TOKEN_B_SENTINEL','KEY_SENTINEL'):self.assertNotIn(token,raw)
            self.assertTrue(p['users'][uid]['wx_configured'])
        self.assertNotIn('llm_api_base',service.payload(self.c,'b')['settings'])
    def test_profile_only_changes_names(self):
        before=self.raw();self.apply('b',dict(scope='profile',name_a='New A',name_b='New B'))
        self.assertEqual(self.raw(),before)
        r=self.c.execute("SELECT * FROM users WHERE uid='a'").fetchone();self.assertEqual(r['name'],'New A');self.assertEqual(r['week1'],'2026-08-31');self.assertEqual(r['wx_uid'],'TOKEN_A_SENTINEL')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM summary_cache').fetchone()[0],1)
    def test_schedule_only_changes_schedule(self):
        self.apply('a',dict(scope='schedule',week1_a='2026-09-07',week1_b='2026-09-14',window_start='09:00',window_end='21:00',min_gap=35))
        self.assertEqual(self.raw()['min_gap'],'35');self.assertEqual(self.raw()['llm_api_key'],'KEY_SENTINEL')
        self.assertEqual(self.c.execute("SELECT name FROM users WHERE uid='a'").fetchone()[0],'Alpha')
    def test_notifications_only_current_user_and_blank_preserves(self):
        self.apply('b',dict(scope='notifications',wx_token='',clear_wx=False))
        self.assertEqual(self.c.execute("SELECT wx_uid FROM users WHERE uid='b'").fetchone()[0],'TOKEN_B_SENTINEL')
        self.apply('b',dict(scope='notifications',wx_token='NEW_TOKEN',clear_wx=False))
        self.assertEqual(self.c.execute("SELECT wx_uid FROM users WHERE uid='a'").fetchone()[0],'TOKEN_A_SENTINEL')
        self.apply('b',dict(scope='notifications',wx_token='',clear_wx=True))
        self.assertEqual(self.c.execute("SELECT wx_uid FROM users WHERE uid='b'").fetchone()[0],'')
    def test_ai_admin_only_key_blank_preserved_and_clear_explicit(self):
        body=dict(scope='ai',llm_api_base='https://example.invalid/v1',llm_model='new',llm_api_key='',clear_llm_key=False)
        with self.assertRaises(service.Error) as exc:self.apply('b',body)
        self.assertEqual(exc.exception.status,403)
        self.apply('a',body);self.assertEqual(self.raw()['llm_api_key'],'KEY_SENTINEL')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM summary_cache').fetchone()[0],0)
        self.apply('a',dict(body,clear_llm_key=True));self.assertEqual(self.raw()['llm_api_key'],'')
    def test_validation_and_scope_isolation(self):
        bad=[dict(scope='profile',name_a='A',name_b='B',wx_token='override'),dict(scope='schedule',week1_a='2026-09-01',week1_b='',window_start='08:00',window_end='22:00',min_gap=20),dict(scope='schedule',week1_a='',week1_b='',window_start='99:00',window_end='22:00',min_gap=20),dict(scope='notifications',wx_token='new',clear_wx=True),dict(scope='ai',llm_api_base='file:///etc/passwd'),dict(scope='profile',name_a='',name_b='B'),dict(scope='unknown'),dict(scope='notifications',clear_wx='false')]
        before=self.raw()
        for b in bad:
            with self.subTest(body=b),self.assertRaises(service.Error):self.apply('a',b)
        self.assertEqual(self.raw(),before)

class HttpSettingsTests(unittest.TestCase):
    setUpClass=classmethod(base_tests.HttpTests.setUpClass.__func__)
    tearDownClass=classmethod(base_tests.HttpTests.tearDownClass.__func__)
    req=base_tests.HttpTests.req
    def test_settings_auth_and_no_store(self):
        self.assertEqual(self.req('/api/settings',uid=None)[0],401)
        status,headers,p=self.req('/api/settings');self.assertEqual(status,200);self.assertIn('no-store',headers['Cache-Control']);self.assertNotIn('llm_api_key',p['settings'])
    def test_scoped_http_writes_readback_and_csrf(self):
        before=self.req('/api/settings')[2]
        status,_,_=self.req('/api/settings',data=dict(scope='profile',name_a='Scoped A',name_b='Scoped B'))
        self.assertEqual(status,200);after=self.req('/api/settings')[2];self.assertEqual(after['users']['a']['name'],'Scoped A');self.assertEqual(before['settings'],after['settings'])
        self.assertEqual(self.req('/api/settings',data=dict(scope='profile',name_a='x',name_b='y'),headers={'Origin':'https://evil.invalid'})[0],403)
        self.assertEqual(self.req('/api/settings','b',dict(scope='ai',llm_api_base='https://example.invalid'))[0],403)
    def test_assets_and_shared_payload_secrets(self):
        for p in ('/settings_ui.js','/settings_ui.css'):
            self.assertEqual(self.req(p)[0],200)
        for p in ('/api/week','/api/month?month=2026-10'):
            r=self.req(p)[2];self.assertFalse(any('wx_uid' in u for u in r['users'].values()));self.assertNotIn('llm_api_key',r.get('settings',{}))

if __name__=='__main__':unittest.main()
