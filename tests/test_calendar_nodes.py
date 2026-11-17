"""手账日历日期规则、接口与提醒隔离验证。"""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import special_days as sd


class CalendarNodesTests(unittest.TestCase):
    def nodes(self, ym, items=()):
        return sd.calendar_nodes_in_month(ym, items)

    def test_six_day_milestones(self):
        start = date(2024, 1, 1)
        items = [{'id': 1, 'title': '在一起', 'date': start.isoformat(), 'target_type': 'love'}]
        found = []
        for year in range(2024, 2028):
            for month in range(1, 13):
                for dt, nodes in self.nodes(f'{year}-{month:02}', items).items():
                    for node in nodes:
                        if node['kind'] == 'milestone':
                            n = (date.fromisoformat(dt) - start).days + 1
                            found.append(n)
                            self.assertEqual(node['label'], f'第{n}天')
        self.assertEqual(sorted(found), [99, 100, 520, 999, 1000, 1314])

    def test_anniversary_leap_and_no_duplicate(self):
        item = {'id': 1, 'title': '在一起', 'date': '2024-02-29', 'target_type': 'love'}
        nodes = self.nodes('2025-02', [item])['2025-02-28']
        self.assertEqual([n['label'] for n in nodes], ['一周年'])
        self.assertNotIn('2023-02-28', self.nodes('2023-02', [item]))
        self.assertEqual(self.nodes('2024-02', [item])['2024-02-29'][0]['detail'], '在一起的第 1 天')

    def test_festival_dates_2026(self):
        expected = {
            '元旦': '2026-01-01', '情人节': '2026-02-14', '除夕': '2026-02-16',
            '春节': '2026-02-17', '元宵节': '2026-03-03', '白色情人节': '2026-03-14',
            '清明': '2026-04-05', '劳动节': '2026-05-01', '520': '2026-05-20',
            '端午节': '2026-06-19', '七夕': '2026-08-19', '中秋节': '2026-09-25',
            '国庆节': '2026-10-01', '冬至': '2026-12-22', '圣诞节': '2026-12-25',
            '跨年夜': '2026-12-31',
        }
        actual = {n['label']: dt for month in range(1, 13)
                  for dt, nodes in self.nodes(f'2026-{month:02}').items() for n in nodes}
        self.assertEqual(actual, expected)

    def test_terms_boundaries_and_variable_days(self):
        self.assertEqual(len(sd._QINGMING_DAYS), 201)
        self.assertEqual(len(sd._DONGZHI_DAYS), 201)
        for year, qingming, dongzhi in [(1900, 5, 22), (1901, 5, 22), (1931, 6, 23),
                                       (2000, 4, 21), (2024, 4, 21), (2025, 4, 21),
                                       (2026, 5, 22), (2100, 5, 22)]:
            self.assertIn(f'{year}-04-{qingming:02}', self.nodes(f'{year}-04'))
            self.assertIn(f'{year}-12-{dongzhi:02}', self.nodes(f'{year}-12'))

    def test_overlap_sort_and_birthday_countdown(self):
        items = [
            {'id': 1, 'title': '在一起', 'date': '2025-05-20', 'target_type': 'love'},
            {'id': 2, 'title': '生日', 'date': '2000-05-20', 'target_type': 'birthday'},
            {'id': 3, 'title': '约会', 'date': '2026-05-20', 'target_type': 'countdown'},
        ]
        nodes = self.nodes('2026-05', items)['2026-05-20']
        self.assertEqual([n['kind'] for n in nodes], ['anniversary', 'birthday', 'countdown', 'romantic'])
        self.assertEqual(len([n for n in nodes if n['kind'] == 'anniversary']), 1)
        self.assertFalse(any(n['kind'] == 'countdown' for ns in self.nodes('2027-05', items).values() for n in ns))

    def test_520_day_and_520_festival_are_distinct(self):
        start = date(2026, 5, 20) - timedelta(days=519)
        item = {'id': 1, 'title': '在一起', 'date': start.isoformat(), 'target_type': 'love'}
        self.assertEqual([n['label'] for n in self.nodes('2026-05', [item])['2026-05-20']], ['第520天', '520'])

    def test_invalid_month_and_no_anchor(self):
        self.assertEqual(self.nodes('2026-13'), {})
        self.assertEqual(self.nodes('bad'), {})
        self.assertIn('2026-02-14', self.nodes('2026-02', [{'date': 'invalid', 'target_type': 'love'}]))

    def test_reminder_engine_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        baseline = subprocess.check_output(['git', '-C', str(root), 'show', 'HEAD:special_days.py'], text=True, encoding='utf-8')
        namespace = {'__name__': 'baseline_special_days'}
        exec(compile(baseline, 'baseline_special_days.py', 'exec'), namespace)
        items = [{'id': 1, 'title': '在一起', 'date': '2024-12-17', 'target_type': 'love'},
                 {'id': 2, 'title': '生日', 'date': '2000-05-20', 'target_type': 'birthday'}]
        for year in range(2024, 2029):
            self.assertEqual(sd.special_days_in_year(year, items[0]['date']),
                             namespace['special_days_in_year'](year, items[0]['date']))
            for month in range(1, 13):
                today = f'{year}-{month:02}-01'
                self.assertEqual(sd.upcoming(items, today, 31), namespace['upcoming'](items, today, 31))


class QuietHandler(app.Handler):
    def log_message(self, *args):
        pass


class CalendarHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.mkdtemp(prefix='calendar-tests-')
        app.DATA_DIR = cls.directory
        app.DB_PATH = os.path.join(cls.directory, 'sched.db')
        app.SECRET = 'calendar-isolated-test-secret'
        app.init_db()
        app.CONN.execute('DELETE FROM anniversaries')
        app.CONN.execute('INSERT INTO anniversaries(title,date,target_type) VALUES(?,?,?)',
                         ('在一起', '2025-05-20', 'love'))
        app.CONN.execute('INSERT INTO diaries(date,title,author_uid,created_at) VALUES(?,?,?,?)',
                         ('2026-05-20', '一起散步', 'a', '2026-05-20 12:00:00'))
        cls.srv = app.MediaHTTPServer(('127.0.0.1', 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join()
        app.get_connection().close()
        app._TLOCAL.conn = None

    def req(self, path):
        conn = http.client.HTTPConnection('127.0.0.1', self.srv.server_port, timeout=10)
        conn.request('GET', path, headers={'Cookie': app.COOKIE + '=' + app.make_token('a')})
        response = conn.getresponse()
        status, payload = response.status, json.loads(response.read())
        conn.close()
        self.assertEqual(status, 200)
        return payload

    def test_month_and_day_agree(self):
        month = self.req('/api/month?month=2026-05')
        day = self.req('/api/diaries?date=2026-05-20')
        self.assertEqual(month['nodes_by_date']['2026-05-20'], day['nodes'])
        self.assertEqual([n['label'] for n in day['nodes']], ['一周年', '520'])
        self.assertEqual(day['diaries'][0]['title'], '一起散步')
        self.assertEqual(month['total_count'], 1)

    def test_empty_diary_day_still_has_nodes(self):
        day = self.req('/api/diaries?date=2026-02-14')
        self.assertEqual(day['diaries'], [])
        self.assertEqual(day['nodes'][0]['label'], '情人节')

    def test_both_accounts_share_month_nodes(self):
        month = self.req('/api/month?month=2026-05')
        self.assertIn('anniversaries', month)
        self.assertIn('diaries_by_date', month)
        self.assertEqual(month['nodes_by_date']['2026-05-20'][0]['kind'], 'anniversary')


if __name__ == '__main__':
    unittest.main()
