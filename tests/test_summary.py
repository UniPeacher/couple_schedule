"""月度胶囊（时光简报）回归测试；全部数据在隔离的临时目录中生成。"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


def seed_month(c):
    c.execute("DELETE FROM diary_photos")
    c.execute("DELETE FROM diaries")
    c.execute("DELETE FROM comments")
    c.execute("DELETE FROM wishes")
    c.execute("DELETE FROM summary_cache")
    c.executemany(
        "INSERT INTO diaries(date,title,location,mood,weather,content,author_uid,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        [
            ("2026-10-03", "第一次一起做饭", "出租屋", "🥰 幸福贴贴", "晴",
             "番茄炒蛋咸了，米饭也硬，最后一起下楼吃了碗面。", "a", "2026-10-03 22:10:00"),
            ("2026-10-15", "图书馆占座", "图书馆四楼", "😪 有点累", "",
             "抢到了靠窗的位置。", "b", "2026-10-15 20:00:00"),
        ],
    )
    ids = [r[0] for r in c.execute("SELECT id FROM diaries ORDER BY date, id")]
    c.execute("INSERT INTO diary_photos(diary_id,file_name) VALUES(?,'p1.jpg')", (ids[0],))
    c.execute("INSERT INTO comments(event_id,uid,text,created,date) VALUES(1,'b',?,?,?)",
              ("明天我给你带早饭", "2026-10-03 23:02:00", "2026-10-03"))
    c.execute("INSERT INTO wishes(creator_uid,title,category,priority,note,is_done,done_at,created_at) "
              "VALUES('a','一起看日落','life',0,'','1','2026-10-20 18:00:00','2026-10-01 10:00:00')")


class SummaryUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="summary-unit-", dir=os.environ.get("TMPDIR"))
        app.DATA_DIR = cls.temp.name
        app.DB_PATH = os.path.join(cls.temp.name, "sched.db")
        app.SECRET = "isolated-test-secret"
        app.init_db()

    @classmethod
    def tearDownClass(cls):
        app.get_connection().close()
        app._TLOCAL.conn = None
        cls.temp.cleanup()

    def setUp(self):
        with app.LOCK:
            seed_month(app.get_connection())

    def test_month_facts_use_real_records(self):
        facts = app.month_facts("2026-10")
        self.assertEqual([d["title"] for d in facts["diaries"]], ["第一次一起做饭", "图书馆占座"])
        self.assertEqual(facts["n_photos"], 1)
        self.assertEqual(len(facts["comments"]), 1)
        self.assertEqual(facts["wishes"], ["一起看日落"])
        self.assertEqual(facts["names"]["a"], "小金毛")
        self.assertEqual(facts["month_label"], "2026年10月")
        self.assertGreater(facts["event_counts"]["a"] + facts["event_counts"]["b"], 0)

    def test_prompt_carries_material_and_bans_ai_tone(self):
        facts = app.month_facts("2026-10")
        prompt = app.build_month_user_prompt(facts)
        self.assertIn("第一次一起做饭", prompt)
        self.assertIn("明天我给你带早饭", prompt)
        self.assertIn("只写上面出现过的内容", prompt)
        system = app.SUMMARY_SYSTEM_PROMPT
        self.assertNotIn("AI爱情管家", system)
        self.assertNotIn("字数在 200~320", system)
        for banned in ("不要编造", "时光荏苒", "不要煽情"):
            self.assertIn(banned, system)

    def test_fallback_is_plain_and_keeps_numbers(self):
        res = app.generate_month_summary("2026-10")
        self.assertFalse(res["is_ai"])
        self.assertIn("第一次一起做饭", res["content"])
        self.assertIn("2 篇手账", res["content"])
        self.assertNotIn("🐾", res["content"])
        self.assertNotIn("长情", res["content"])
        self.assertEqual(len(res["stats"]), 4)
        self.assertEqual(res["month"], "2026-10")

    def test_empty_month_fallback_and_navigation(self):
        res = app.generate_month_summary("2026-09")
        self.assertIn("没有新手账", res["content"])
        self.assertFalse(res["has_prev"])       # 手账最早只到 2026-10
        self.assertTrue(res["has_next"])        # 可以往后翻到有数据的月份
        self.assertEqual(res["month_label"], "2026年9月")

    def test_cache_and_force_refresh(self):
        calls = []

        def fake_llm(system_prompt, user_prompt):
            calls.append(user_prompt)
            return "十月第一版"

        original = app.call_llm_api
        app.call_llm_api = fake_llm
        try:
            first = app.generate_month_summary("2026-10", force_refresh=True)
            second = app.generate_month_summary("2026-10")
            self.assertTrue(first["is_ai"])
            self.assertEqual(second["content"], "十月第一版")
            self.assertEqual(len(calls), 1)
            third = app.generate_month_summary("2026-10", force_refresh=True)
            self.assertEqual(third["content"], "十月第一版")
            self.assertEqual(len(calls), 2)
        finally:
            app.call_llm_api = original

    def test_push_target_only_month_end(self):
        cases = [
            (datetime(2026, 10, 31, 20, 59), "", None),
            (datetime(2026, 10, 31, 21, 0), "", "2026-10"),
            (datetime(2026, 10, 31, 23, 30), "2026-10", None),
            (datetime(2026, 11, 1, 9, 0), "2026-10", None),
            (datetime(2026, 11, 2, 10, 0), "2026-09", "2026-10"),
            (datetime(2026, 11, 12, 10, 0), "", None),
            (datetime(2026, 12, 31, 22, 0), "", "2026-12"),
            (datetime(2027, 1, 1, 9, 0), "2026-12", None),
        ]
        for now, last, expect in cases:
            with self.subTest(now=now, last=last):
                self.assertEqual(app.month_push_target(now, last), expect)

    def test_default_month_switches_at_month_end(self):
        self.assertEqual(app.default_summary_month(date(2026, 11, 12)), "2026-10")
        self.assertEqual(app.default_summary_month(date(2026, 11, 30)), "2026-11")
        self.assertEqual(app.default_summary_month(date(2027, 1, 5)), "2026-12")


class QuietHandler(app.Handler):
    def log_message(self, fmt, *args):
        pass


class SummaryHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="summary-http-", dir=os.environ.get("TMPDIR"))
        app.DATA_DIR = cls.temp.name
        app.DB_PATH = os.path.join(cls.temp.name, "sched.db")
        app.SECRET = "isolated-test-secret"
        app.init_db()
        with app.LOCK:
            seed_month(app.get_connection())
        cls.srv = app.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join()
        app.get_connection().close()
        app._TLOCAL.conn = None
        cls.temp.cleanup()

    def req(self, path, uid="a", data=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if uid:
            headers["Cookie"] = app.COOKIE + "=" + app.make_token(uid)
        c.request("POST" if data is not None else "GET", path, json.dumps(data) if data is not None else None, headers)
        r = c.getresponse()
        body = r.read()
        c.close()
        try:
            return r.status, json.loads(body)
        except ValueError:
            return r.status, body.decode()

    def test_requires_login(self):
        self.assertEqual(self.req("/api/summary", uid=None)[0], 401)
        self.assertEqual(self.req("/api/summary/push", data={"month": "2026-10"}, uid=None)[0], 401)

    def test_month_param_validation(self):
        for bad in ("2026-13", "2026-1", "abc", "2999-01"):
            with self.subTest(bad=bad):
                self.assertEqual(self.req("/api/summary?month=" + bad)[0], 400)
        self.assertEqual(self.req("/api/summary/push", data={"month": "2026-13"})[0], 400)

    def test_default_and_explicit_month(self):
        status, payload = self.req("/api/summary")
        self.assertEqual(status, 200)
        self.assertEqual(payload["month"], app.default_summary_month())
        status, payload = self.req("/api/summary?month=2026-10")
        self.assertEqual(status, 200)
        self.assertEqual(payload["month"], "2026-10")
        self.assertIn("月度胶囊", payload["title"])
        self.assertEqual(len(payload["stats"]), 4)
        self.assertEqual(payload["month_label"], "2026年10月")

    def test_push_without_wechat_tokens_is_rejected(self):
        status, payload = self.req("/api/summary/push", data={"month": "2026-10"})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
