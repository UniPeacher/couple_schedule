"""手账照片提交回归测试：照片字节必须随响应落盘，ffmpeg 处理不得阻塞响应。

所有数据在隔离的临时目录中生成，不碰生产库。
"""
import base64
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import images


class QuietHandler(app.Handler):
    def log_message(self, *args):
        pass


# 1x1 PNG，足够通过服务端 image 校验
_PNG_1PX = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\x0d\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
).decode()


class DiaryMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="diary-media-")
        app.DATA_DIR = cls.temp.name
        app.PHOTOS_DIR = os.path.join(cls.temp.name, "photos")
        app.PHOTOS_TRASH_DIR = os.path.join(cls.temp.name, "photos_trash")
        app.DB_PATH = os.path.join(cls.temp.name, "sched.db")
        app.SECRET = "diary-media-test-secret"
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

    def req(self, path, data=None, uid="a"):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=30)
        hdr = {"Content-Type": "application/json",
               "Cookie": app.COOKIE + "=" + app.make_token(uid)}
        c.request("POST" if data is not None else "GET", path,
                  json.dumps(data) if data is not None else None, hdr)
        r = c.getresponse()
        body = r.read()
        c.close()
        try:
            return r.status, json.loads(body)
        except ValueError:
            return r.status, body.decode()

    def test_photo_suffix_range(self):
        os.makedirs(app.PHOTOS_DIR, exist_ok=True)
        Path(app.PHOTOS_DIR, "range_sample.mp4").write_bytes(b"0123456789")
        for requested, expected, content_range in (
                ("bytes=0-1", b"01", "bytes 0-1/10"),
                ("bytes=-2", b"89", "bytes 8-9/10"),
                ("bytes=7-", b"789", "bytes 7-9/10"),
                ("bytes=-20", b"0123456789", "bytes 0-9/10")):
            with self.subTest(requested=requested):
                c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port)
                c.request("GET", "/photos/range_sample.mp4", headers={
                    "Cookie": app.COOKIE + "=" + app.make_token("a"), "Range": requested})
                r = c.getresponse()
                self.assertEqual(r.status, 206)
                self.assertEqual(r.getheader("Content-Range"), content_range)
                self.assertEqual(r.read(), expected)
                c.close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires ffmpeg")
    def test_hevc_preview_is_h264_and_preserves_source(self):
        video = os.path.join(app.PHOTOS_DIR, "compatibility.mp4")
        os.makedirs(app.PHOTOS_DIR, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=10",
                        "-t", "0.5", "-c:v", "libx265", "-x265-params", "pools=1:frame-threads=1",
                        "-tag:v", "hvc1", video], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        original = Path(video).read_bytes()
        self.assertTrue(images.ensure_live_preview(video))
        preview = os.path.join(app.PHOTOS_DIR, "pv_compatibility.mp4")
        self.assertEqual(Path(video).read_bytes(), original)
        codec = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                         "-show_entries", "stream=codec_name", "-of", "csv=p=0", preview])
        self.assertEqual(codec.strip(), b"h264")
        self.assertTrue(images._moov_is_front(preview))

    def test_preview_link_requires_completed_file(self):
        status, payload = self.req("/api/diaries", {"date": "2026-10-04", "title": "播放副本"})
        self.assertEqual(status, 200)
        app.CONN.execute("INSERT INTO diary_photos(diary_id,file_name,live_video,sort_order) VALUES(?,?,?,?)",
                         (payload["id"], "preview_test.jpg", "preview_test.mp4", 0))
        _, detail = self.req("/api/diaries?date=2026-10-04")
        self.assertNotIn("live_preview", detail["diaries"][0]["photos"][0])
        os.makedirs(app.PHOTOS_DIR, exist_ok=True)
        Path(app.PHOTOS_DIR, "pv_preview_test.mp4").write_bytes(b"completed preview")
        _, detail = self.req("/api/diaries?date=2026-10-04")
        photo = detail["diaries"][0]["photos"][0]
        self.assertEqual(photo["live_preview"], "pv_preview_test.mp4")
        self.assertEqual(photo["live_video"], "preview_test.mp4")

    def test_photos_saved_before_response(self):
        """响应返回时照片文件必须已在磁盘上：前端立刻刷新就能看到图。"""
        status, payload = self.req("/api/diaries", {
            "date": "2026-11-16", "title": "落盘时机",
            "photos": [{"image": "data:image/png;base64," + _PNG_1PX}],
        })
        self.assertEqual(status, 200, payload)
        photos = payload["id"]
        _, detail = self.req("/api/diaries?date=2026-11-16")
        files = [p["file_name"] for d in detail["diaries"] if d["id"] == photos
                 for p in d["photos"]]
        self.assertEqual(len(files), 1)
        self.assertTrue(os.path.exists(os.path.join(app.PHOTOS_DIR, files[0])),
                        "响应前照片必须已落盘")

    def test_media_job_runs_off_request_path(self):
        """ffmpeg 处理排到后台：请求立即返回，处理在响应之后完成。"""
        calls = []
        real = images.finalize_media
        images.finalize_media = lambda *a, **kw: calls.append(a)
        try:
            t0 = time.time()
            status, payload = self.req("/api/diaries", {
                "date": "2026-11-17", "title": "后台处理",
                "photos": [{"image": "data:image/png;base64," + _PNG_1PX}],
            })
            elapsed = time.time() - t0
            self.assertEqual(status, 200, payload)
            self.assertLess(elapsed, 5, "提交不应等待媒体处理")
            # 队列串行消费，给后台线程一点时间取走任务
            deadline = time.time() + 10
            while not calls and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(len(calls), 1, "每张照片都应有对应的后台处理任务")
        finally:
            images.finalize_media = real


if __name__ == "__main__":
    unittest.main()
