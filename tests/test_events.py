"""日程增删改接口回归测试；隔离临时数据库，不接触生产数据。"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class QuietHandler(app.Handler):
    def log_message(self, fmt, *args):
        pass


class EventHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="events-http-", dir=os.environ.get("TMPDIR"))
        app.DATA_DIR = cls.temp.name
        app.DB_PATH = os.path.join(cls.temp.name, "sched.db")
        app.SECRET = "isolated-test-secret"
        app.init_db()
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

    def setUp(self):
        with app.LOCK:
            c = app.get_connection()
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM comments")
            c.execute("DELETE FROM notifications")

    def req(self, path, uid="a", data=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        hdr = {"Content-Type": "application/json"}
        if uid:
            hdr["Cookie"] = app.COOKIE + "=" + app.make_token(uid)
        hdr.update(headers or {})
        body = json.dumps(data) if data is not None else None
        c.request("POST" if data is not None else "GET", path, body, hdr)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw.decode()

    def valid_weekly(self, **over):
        p = dict(uid="a", title="高数课", repeat="weekly", weekday=2,
                 tstart="08:00", tend="09:40", location="A101", note="", week_spec="2-17")
        p.update(over)
        return p

    @staticmethod
    def week_titles(week):
        return [ev["title"] for day in week["days"]
                for u in day["users"].values() for ev in u["events"]]

    def test_requires_login(self):
        self.assertEqual(self.req("/api/events", uid=None, data=self.valid_weekly())[0], 401)
        self.assertEqual(self.req("/api/events/delete", uid=None, data={"id": 1})[0], 401)

    def test_create_weekly_ok(self):
        status, payload = self.req("/api/events", data=self.valid_weekly())
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload.get("ok"))
        eid = payload["id"]
        status, week = self.req("/api/week")
        self.assertEqual(status, 200)
        self.assertIn("高数课", self.week_titles(week))
        return eid

    def test_create_once_ok(self):
        status, payload = self.req("/api/events", data=self.valid_weekly(
            repeat="once", weekday=None, date="2026-12-25", week_spec=""))
        self.assertEqual(status, 200, payload)

    def test_create_validation_errors(self):
        cases = [
            (self.valid_weekly(uid="x"), "归属用户无效"),
            (self.valid_weekly(title="  "), "标题不能为空"),
            (self.valid_weekly(tstart="8:00"), "时间无效"),
            (self.valid_weekly(tstart="10:00", tend="09:00"), "时间无效"),
            (self.valid_weekly(weekday=7), "星期无效"),
            (self.valid_weekly(weekday=None), "缺少星期"),
            (self.valid_weekly(repeat="daily"), "类型无效"),
            (self.valid_weekly(week_spec="2-abc"), "周次格式无法识别"),
            (self.valid_weekly(repeat="once", weekday=None, date="2026/12/25", week_spec=""), "日期无效"),
        ]
        for data, msg in cases:
            with self.subTest(msg=msg):
                status, payload = self.req("/api/events", data=data)
                self.assertEqual(status, 400)
                self.assertIn(msg, payload.get("error", ""))

    def test_update_and_delete_flow(self):
        eid = self.test_create_weekly_ok()
        status, payload = self.req("/api/events/update", data=dict(
            id=eid, uid="both", title="高数课改", repeat="weekly", weekday=3,
            tstart="10:00", tend="11:40", location="B202", note="带课本", week_spec=""))
        self.assertEqual(status, 200, payload)
        status, week = self.req("/api/week")
        self.assertIn("高数课改", self.week_titles(week))

        self.assertEqual(self.req("/api/events/delete", data={"id": eid})[0], 200)
        status, week = self.req("/api/week")
        self.assertNotIn("高数课改", self.week_titles(week))
        # 删除日程同时清掉其留言
        self.assertEqual(self.req("/api/events/delete", data={"id": eid})[0], 200)

    def test_update_validation(self):
        self.assertEqual(self.req("/api/events/update", data={"id": "x"})[0], 400)
        self.assertEqual(self.req("/api/events/update", data=self.valid_weekly(id=9999))[0], 404)


if __name__ == "__main__":
    unittest.main()
