"""旅行清单接口回归测试；隔离临时数据库，不接触生产数据。"""
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


class TripHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="trips-http-", dir=os.environ.get("TMPDIR"))
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
            c.execute("DELETE FROM trip_items")
            c.execute("DELETE FROM trips")
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

    def post(self, payload, uid="a", **kwargs):
        return self.req("/api/trips", uid=uid, data=payload, **kwargs)

    def create(self, dest="青岛", start="2026-12-20", end="2026-12-23", template=True):
        status, payload = self.post(dict(action="create", destination=dest,
                                         start_date=start, end_date=end,
                                         use_template=template))
        self.assertEqual(status, 200, payload)
        return payload["id"]

    def detail(self, tid):
        status, payload = self.req("/api/trips/detail?id=%d" % tid)
        self.assertEqual(status, 200, payload)
        return payload

    def test_requires_login(self):
        self.assertEqual(self.req("/api/trips", uid=None)[0], 401)
        self.assertEqual(self.post(dict(action="create", destination="x"), uid=None)[0], 401)

    def test_create_with_template_and_basic_stats(self):
        tid = self.create()
        d = self.detail(tid)
        self.assertEqual(d["trip"]["destination"], "青岛")
        self.assertEqual(d["stats"]["total"], len(app.TRIP_TEMPLATE))
        self.assertEqual(d["stats"]["packed"], 0)
        cats = [c[0] for c in d["categories"]]
        self.assertEqual(cats, app.TRIP_CATEGORY_KEYS)
        self.assertTrue(all(i["category"] in cats for i in d["items"]))

    def test_create_without_template_is_empty(self):
        tid = self.create(template=False)
        self.assertEqual(self.detail(tid)["stats"]["total"], 0)

    def test_item_add_update_toggle_delete(self):
        tid = self.create(template=False)
        self.assertEqual(self.post(dict(action="item_add", trip_id=tid, category="tech",
                                        name="充电器", qty="2", note="用你的"))[0], 200)
        item = self.detail(tid)["items"][0]
        self.assertEqual((item["name"], item["qty"], item["note"]), ("充电器", "2", "用你的"))

        self.assertEqual(self.post(dict(action="item_toggle", id=item["id"]))[0], 200)
        self.assertEqual(self.detail(tid)["stats"]["packed"], 1)
        self.assertEqual(self.post(dict(action="item_toggle", id=item["id"]))[0], 200)
        self.assertEqual(self.detail(tid)["stats"]["packed"], 0)

        self.assertEqual(self.post(dict(action="item_update", id=item["id"], name="充电宝",
                                        qty="1", note="刷我的卡", category="tech"))[0], 200)
        item = self.detail(tid)["items"][0]
        self.assertEqual((item["name"], item["note"]), ("充电宝", "刷我的卡"))

        self.assertEqual(self.post(dict(action="item_delete", id=item["id"]))[0], 200)
        self.assertEqual(self.detail(tid)["stats"]["total"], 0)

    def test_item_validation(self):
        tid = self.create(template=False)
        self.assertEqual(self.post(dict(action="item_add", trip_id=tid, name="   "))[0], 400)
        self.assertEqual(self.post(dict(action="item_add", trip_id=99999, name="x"))[0], 404)
        self.assertEqual(self.post(dict(action="item_toggle", id=99999))[0], 404)
        # 非法分组回落到 other，不报错
        self.assertEqual(self.post(dict(action="item_add", trip_id=tid, category="bogus", name="伞"))[0], 200)
        self.assertEqual(self.detail(tid)["items"][0]["category"], "other")

    def test_copy_last_only_fills_missing(self):
        old = self.create(template=False)
        self.post(dict(action="item_add", trip_id=old, category="doc", name="身份证"))
        self.post(dict(action="item_add", trip_id=old, category="doc", name="现金"))
        new = self.create(dest="成都", template=False)
        self.post(dict(action="item_add", trip_id=new, category="doc", name="身份证"))

        status, payload = self.post(dict(action="copy_last", id=new))
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["copied"], 1)
        names = [i["name"] for i in self.detail(new)["items"]]
        self.assertEqual(names, ["身份证", "现金"])

    def test_copy_last_without_previous_trip(self):
        tid = self.create(template=False)
        self.assertEqual(self.post(dict(action="copy_last", id=tid))[0], 400)

    def test_delete_trip_removes_items(self):
        tid = self.create()
        self.assertEqual(self.post(dict(action="delete", id=tid))[0], 200)
        self.assertEqual(self.req("/api/trips/detail?id=%d" % tid)[0], 404)
        with app.LOCK:
            left = app.get_connection().execute("SELECT COUNT(*) c FROM trip_items").fetchone()["c"]
        self.assertEqual(left, 0)

    def test_create_validation(self):
        self.assertEqual(self.post(dict(action="create", destination=""))[0], 400)
        self.assertEqual(self.post(dict(action="create", destination="x", start_date="2026-2-1"))[0], 400)
        self.assertEqual(self.post(dict(action="create", destination="x",
                                        start_date="2026-12-23", end_date="2026-12-20"))[0], 400)
        self.assertEqual(self.post(dict(action="nope"))[0], 400)

    def test_status_and_ordering(self):
        today = app.date.today()
        past = (today.replace(day=1) - app.timedelta(days=40)).isoformat()
        past_end = (today.replace(day=1) - app.timedelta(days=35)).isoformat()
        future = (today + app.timedelta(days=30)).isoformat()
        done = self.create(dest="去年", start=past, end=past_end, template=False)
        soon = self.create(dest="下个月", start=future, end=future, template=False)
        ongoing = self.create(dest="现在", start=today.isoformat(), end=today.isoformat(), template=False)

        trips = self.req("/api/trips")[1]["trips"]
        self.assertEqual([t["id"] for t in trips], [ongoing, soon, done])
        self.assertEqual([t["status"] for t in trips], ["ongoing", "upcoming", "done"])

    def test_cross_origin_and_content_type(self):
        self.assertEqual(self.post(dict(action="create", destination="x"),
                                   headers={"Origin": "https://evil.invalid"})[0], 403)
        self.assertEqual(self.post(dict(action="create", destination="x"),
                                   headers={"Content-Type": "text/plain"})[0], 415)

    def test_partner_gets_notice_on_create(self):
        self.create(dest="厦门")
        unread = app.get_connection().execute(
            "SELECT title, content, target_action FROM notifications WHERE target_uid='b' AND is_read=0").fetchall()
        self.assertTrue(any("旅行清单" in r["title"] and "厦门" in r["content"] for r in unread))
        self.assertTrue(any(r["target_action"] == "view:trips" for r in unread))


if __name__ == "__main__":
    unittest.main()
