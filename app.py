#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sched-share —— 双人日程共享服务
- 两个固定账号（默认「小金毛」「小白狗」），登录后互相可见对方全部日程
- 日程支持 每周重复 / 单次，可选周次规格（如 2-17、2-16双、1-4,9-12）
- 自动计算每天两人的 共同空闲时段 与 时间重叠（冲突）
- 仅用 Python 标准库，SQLite 存储，数据目录由环境变量 DATA_DIR 指定
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import urllib.request

PORT = int(os.environ.get("PORT", "8795"))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
DB_PATH = os.path.join(DATA_DIR, "sched.db")
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")
COOKIE = "sched_sess"
SESSION_DAYS = 30

TIME_RE = re.compile(r"^\d{2}:\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

LOCK = threading.Lock()
CONN = None
SECRET = None
FAILS = {}  # 登录失败限流：ip -> [次数, 首次时间戳]
APP_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_FILES = {"dogs": "dogs.png", "dogheads": "dogheads.png", "dogrest": "dogrest.png",
             "dog-hero": "dog-hero.png", "dog-add": "dog-add.png", "dog-set": "dog-set.png",
             "dog-wave": "dog-wave.png", "dog-walk1": "dog-walk1.png", "dog-walk2": "dog-walk2.png",
             "dog-walk3": "dog-walk3.png", "dog-walk4": "dog-walk4.png", "dog-couple": "dog-couple.png"}

# ---------------------------------------------------------------- 数据库

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  uid       TEXT PRIMARY KEY,
  name      TEXT NOT NULL,
  pw_hash   TEXT NOT NULL,
  week1     TEXT DEFAULT '',
  wx_uid    TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS events(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  uid       TEXT NOT NULL,
  title     TEXT NOT NULL,
  location  TEXT DEFAULT '',
  note      TEXT DEFAULT '',
  repeat    TEXT NOT NULL DEFAULT 'weekly',
  weekday   INTEGER,
  date      TEXT DEFAULT '',
  tstart    TEXT NOT NULL,
  tend      TEXT NOT NULL,
  week_spec TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS comments(
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL,
  uid      TEXT NOT NULL,
  text     TEXT NOT NULL,
  created  TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS anniversaries(
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  title    TEXT NOT NULL,
  date     TEXT NOT NULL,
  target_type TEXT NOT NULL DEFAULT 'love');
CREATE TABLE IF NOT EXISTS diaries(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,
  title       TEXT NOT NULL,
  location    TEXT DEFAULT '',
  mood        TEXT DEFAULT '',
  weather     TEXT DEFAULT '',
  content     TEXT DEFAULT '',
  author_uid  TEXT NOT NULL,
  created_at  TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS diary_photos(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  diary_id    INTEGER NOT NULL,
  file_name   TEXT NOT NULL,
  sort_order  INTEGER DEFAULT 0);
"""

# 首次初始化时预置的两人示例日程（uid, 标题, 地点, 备注, repeat, 星期0-6, 单次日期, 开始, 结束, 周次）
SEED_EVENTS = [
    ("a", "高等数学", "第一教学楼101", "示例课程", "weekly", 0, "", "10:00", "11:40", "1-16"),
    ("a", "大学英语", "外国语学院202", "示例课程", "weekly", 1, "", "14:00", "15:40", "1-16"),
    ("a", "线性代数", "第一教学楼103", "示例课程", "weekly", 2, "", "10:00", "11:40", "1-16"),
    ("a", "程序设计实践", "信息实验楼301", "上机课", "weekly", 3, "", "14:00", "16:00", "1-16"),
    ("a", "羽毛球", "风雨操场", "运动打卡", "weekly", 4, "", "10:00", "11:40", "1-16"),
    ("b", "现代文学选读", "文科楼102", "示例课程", "weekly", 0, "", "14:00", "15:40", "1-16"),
    ("b", "传播学概论", "文科楼205", "示例课程", "weekly", 1, "", "08:00", "09:40", "1-16"),
    ("b", "艺术设计史", "艺术楼301", "示例课程", "weekly", 2, "", "14:00", "16:00", "1-16"),
    ("b", "学术英语", "外国语学院204", "示例课程", "weekly", 3, "", "08:00", "09:40", "1-16"),
    ("b", "摄影与视觉表达", "艺术实验楼101", "示例课程", "weekly", 4, "", "10:00", "12:00", "1-16"),
]

DEFAULT_WEEK1 = "2026-09-01"  # 默认校历第1周周一；可在设置中根据各自学校校历修改


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120000).hex()
    return salt + "$" + h


def check_pw(pw, stored):
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_pw(pw, salt), stored)


def load_secret():
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "r") as f:
            return f.read().strip()
    s = secrets.token_hex(32)
    with open(SECRET_FILE, "w") as f:
        f.write(s)
    os.chmod(SECRET_FILE, 0o600)
    return s


def get_setting(key, default=""):
    with LOCK:
        row = CONN.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def get_users():
    with LOCK:
        rows = CONN.execute("SELECT uid,name,week1,pw_hash,wx_uid FROM users ORDER BY uid").fetchall()
        return {r["uid"]: dict(name=r["name"], week1=r["week1"], pw_hash=r["pw_hash"], wx_uid=r["wx_uid"] or "") for r in rows}


def send_wechat_notice(token, title, content):
    if not token or not token.strip():
        return
    token = token.strip()
    def _do_send():
        try:
            # 1. 优先使用 虾推啥 (xtuis, 纯免费免认证且微信卡片直显标题)
            # 虾推啥 Token 为 25 位左右的字母数字组合（如 N2gMZytstbsWdeShGQNS9mPC2）
            if not token.lower().startswith("xz") and not token.lower().startswith("sct") and len(token) >= 20:
                url = f"https://wx.xtuis.cn/{token}.send"
                post_data = urllib.parse.urlencode({"text": title, "desp": content}).encode("utf-8")
                req = urllib.request.Request(
                    url, data=post_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Mozilla/5.0"}
                )
                urllib.request.urlopen(req, timeout=8)
                return

            # 2. 息知 (xizhi)
            if token.lower().startswith("xz"):
                actual_key = token
                if "xizhi.qqoq.net/" in token or "xz.qqoq.net/" in token:
                    actual_key = token.split("/")[-1].replace(".send", "")
                url = f"https://xizhi.qqoq.net/{actual_key}.send"
                payload = json.dumps({"title": title, "content": content}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
                urllib.request.urlopen(req, timeout=8)
                return

            # 3. Server酱 (以 sct 开头)
            if token.lower().startswith("sct"):
                url = f"https://sctapi.ftqq.com/{token}.send"
                payload = json.dumps({"title": title, "desp": content}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=8)
                return
        except Exception as e:
            sys.stderr.write(f"WeChat push error: {e}\n")

    threading.Thread(target=_do_send, daemon=True).start()


def get_all_events():
    with LOCK:
        rows = CONN.execute("SELECT * FROM events ORDER BY tstart, id").fetchall()
        return [dict(r) for r in rows]


def get_comment_counts():
    with LOCK:
        rows = CONN.execute("SELECT event_id, COUNT(*) n FROM comments GROUP BY event_id").fetchall()
        return {r["event_id"]: r["n"] for r in rows}


def get_anniversaries():
    with LOCK:
        rows = CONN.execute("SELECT * FROM anniversaries ORDER BY date ASC, id ASC").fetchall()
        return [dict(r) for r in rows]


def init_db():
    global CONN
    os.makedirs(DATA_DIR, exist_ok=True)
    CONN = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False, isolation_level=None)
    CONN.row_factory = sqlite3.Row
    CONN.execute("PRAGMA journal_mode=WAL")
    CONN.execute("PRAGMA busy_timeout=15000")
    with LOCK:
        CONN.executescript(SCHEMA)
        n_user = CONN.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        if n_user == 0:
            pw_a = secrets.token_urlsafe(6)
            pw_b = secrets.token_urlsafe(6)
            CONN.execute("INSERT INTO users(uid,name,pw_hash,week1) VALUES(?,?,?,?)",
                         ("a", "小金毛", hash_pw(pw_a), DEFAULT_WEEK1))
            CONN.execute("INSERT INTO users(uid,name,pw_hash,week1) VALUES(?,?,?,?)",
                         ("b", "小白狗", hash_pw(pw_b), DEFAULT_WEEK1))
            info = os.path.join(DATA_DIR, "initial-passwords.txt")
            with open(info, "w") as f:
                f.write("初始密码（登录后请在「设置」中修改；确认改完后可删除此文件）\n")
                f.write("小金毛 (uid=a): %s\n" % pw_a)
                f.write("小白狗 (uid=b): %s\n" % pw_b)
                f.write("生成时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            print("[init] created users a/b, passwords written to " + info, flush=True)
        n_ev = CONN.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        if n_ev == 0:
            for row in SEED_EVENTS:
                CONN.execute(
                    "INSERT INTO events(uid,title,location,note,repeat,weekday,date,tstart,tend,week_spec) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)", row)
            print("[init] seeded %d schedule events" % len(SEED_EVENTS), flush=True)
        for k, v in (("window_start", "08:00"), ("window_end", "22:00"), ("min_gap", "20")):
            CONN.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)", (k, v))


# ---------------------------------------------------------------- 周次 / 时间计算

def monday_of(d):
    return d - timedelta(days=d.weekday())


def week_num(week1_str, d):
    """该日期处于第几教学周；week1 为空或早于第1周返回 None"""
    if not week1_str:
        return None
    try:
        w1 = date.fromisoformat(week1_str)
    except ValueError:
        return None
    delta = (monday_of(d) - w1).days
    if delta < 0:
        return None
    return delta // 7 + 1


def parse_week_spec(spec):
    """'2-17' / '2-16双' / '1-4,9-12' → [(lo,hi,parity)]；parity 0=每周 1=单周 2=双周"""
    parts = []
    for raw in spec.replace("，", ",").replace("周", "").replace(" ", "").split(","):
        if not raw:
            continue
        parity = 0
        if raw.endswith("单"):
            parity, raw = 1, raw[:-1]
        elif raw.endswith("双"):
            parity, raw = 2, raw[:-1]
        m = re.match(r"^(\d+)(?:-(\d+))?$", raw)
        if not m:
            return None
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if lo > hi:
            lo, hi = hi, lo
        parts.append((lo, hi, parity))
    return parts or None


def spec_matches(parts, n):
    for lo, hi, parity in parts:
        if lo <= n <= hi and (parity == 0 or (parity == 1 and n % 2 == 1) or (parity == 2 and n % 2 == 0)):
            return True
    return False


def occurs_on(ev, d, week1):
    """事件在某天是否发生"""
    if ev["repeat"] == "once":
        return ev["date"] == d.isoformat()
    if ev["weekday"] is None or ev["weekday"] != d.weekday():
        return False
    spec = (ev["week_spec"] or "").strip()
    if not spec:
        return True
    n = week_num(week1, d)
    if not n:
        return False
    parts = parse_week_spec(spec)
    return bool(parts) and spec_matches(parts, n)


def t2m(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def m2t(m):
    return "%02d:%02d" % (m // 60, m % 60)


def merge_ivs(ivs):
    ivs = sorted(ivs)
    out = []
    for s, e in ivs:
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def week_payload(anchor_iso):
    anchor = date.fromisoformat(anchor_iso)
    mon = monday_of(anchor)
    days = [mon + timedelta(days=i) for i in range(7)]
    users = get_users()
    events = get_all_events()
    cmap = get_comment_counts()
    ws = t2m(get_setting("window_start", "08:00"))
    we = t2m(get_setting("window_end", "22:00"))
    if ws >= we:
        ws, we = 480, 1320
    min_gap = int(get_setting("min_gap", "20") or 20)

    out_days = []
    for d in days:
        per = {}
        for uid in ("a", "b"):
            u = users[uid]
            evs, ivs = [], []
            for ev in events:
                if ev["uid"] != uid or not occurs_on(ev, d, u["week1"]):
                    continue
                evs.append({
                    "id": ev["id"], "title": ev["title"], "location": ev["location"],
                    "note": ev["note"], "tstart": ev["tstart"], "tend": ev["tend"],
                    "week_spec": ev["week_spec"], "repeat": ev["repeat"],
                    "n_comments": cmap.get(ev["id"], 0),
                })
                ivs.append([t2m(ev["tstart"]), t2m(ev["tend"])])
            evs.sort(key=lambda x: (x["tstart"], x["tend"]))
            per[uid] = {"events": evs, "busy": merge_ivs(ivs)}
        both = merge_ivs(per["a"]["busy"] + per["b"]["busy"])
        free, cur = [], ws
        for s, e in both:
            if s > cur:
                free.append([cur, min(s, we)])
            cur = max(cur, e)
        if cur < we:
            free.append([cur, we])
        free = [[s, e] for s, e in free if e - s >= min_gap]

        conflicts = []
        for ea in per["a"]["events"]:
            for eb in per["b"]["events"]:
                s = max(t2m(ea["tstart"]), t2m(eb["tstart"]))
                e = min(t2m(ea["tend"]), t2m(eb["tend"]))
                if s < e:
                    conflicts.append({"a": ea["title"], "b": eb["title"], "s": m2t(s), "e": m2t(e)})

        out_days.append({
            "date": d.isoformat(), "weekday": d.weekday(),
            "users": per, "free": free, "conflicts": conflicts,
        })

    return {
        "week_start": days[0].isoformat(),
        "week_end": days[-1].isoformat(),
        "users": {uid: {"name": u["name"], "week1": u["week1"]} for uid, u in users.items()},
        "weeknums": {uid: week_num(u["week1"], mon) for uid, u in users.items()},
        "settings": {"window_start": m2t(ws), "window_end": m2t(we), "min_gap": min_gap},
        "days": out_days,
        "anniversaries": get_anniversaries(),
    }


# ---------------------------------------------------------------- 会话

def make_token(uid):
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = "%s:%d" % (uid, exp)
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + ":" + sig


def parse_token(tok):
    try:
        payload, sig = tok.rsplit(":", 1)
        expect = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        uid, exp = payload.split(":", 1)
        if int(exp) < time.time() or uid not in ("a", "b"):
            return None
        return uid
    except Exception:
        return None


def rate_ok(ip):
    rec = FAILS.get(ip)
    if not rec:
        return True
    if time.time() - rec[1] > 600:
        FAILS.pop(ip, None)
        return True
    return rec[0] < 10


def rate_fail(ip):
    rec = FAILS.get(ip)
    if not rec or time.time() - rec[1] > 600:
        FAILS[ip] = [1, time.time()]
    else:
        rec[0] += 1


# ---------------------------------------------------------------- 前端页面

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>线条小狗 · 两人日程 🐾</title>
<link rel="icon" type="image/png" href="/img/dogheads.png">
<style>
:root{
  --font-main: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  --bg-page: #faf6f0;
  --bg-card: #fffefc;
  --line-strong: #eddcc8;
  --line-subtle: #f4e8dc;
  --ink-primary: #3d2f25;
  --ink-muted: #877464;
  --ink-light: #b4a394;

  /* 小金毛 Golden Puppy: 垂垂耳、焦糖布丁 & 暖阳蜂蜜黄 */
  --dog-a: #ea8a15;
  --dog-a-hover: #d97706;
  --dog-a-bg: #fff8eb;
  --dog-a-bd: #fed7aa;
  --dog-a-badge: #fef0db;
  --dog-a-text: #964805;

  /* 小白狗 Maltese: 软软波浪毛、草莓牛奶粉 & 纯真浪漫 */
  --dog-b: #ff6088;
  --dog-b-hover: #ff4774;
  --dog-b-bg: #fff1f4;
  --dog-b-bd: #fecad6;
  --dog-b-badge: #ffe0e8;
  --dog-b-text: #a61c3c;

  /* 俩汪贴贴时间 (共同空闲) */
  --free-accent: #10b981;
  --free-bg: #eefcf4;
  --free-bd: #86efac;
  --free-text: #065f46;

  /* 阴影与圆角 */
  --shadow-sm: 0 2px 8px rgba(139, 92, 44, 0.06);
  --shadow-md: 0 8px 24px rgba(139, 92, 44, 0.09);
  --shadow-lg: 0 16px 40px rgba(139, 92, 44, 0.14);
  --radius-sm: 10px;
  --radius-md: 16px;
  --radius-lg: 22px;
  --radius-pill: 999px;
}

*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:var(--font-main);
  background-color:var(--bg-page);
  background-image:radial-gradient(#edd8c4 1.2px, transparent 1.2px);
  background-size:22px 22px;
  color:var(--ink-primary);
  line-height:1.55;
  padding-bottom:100px;
  -webkit-tap-highlight-color:transparent;
  min-height:100vh;
}
button,input,select,textarea{font-family:inherit}

/* 动效 */
@keyframes heartBeat{0%,100%{transform:scale(1)}50%{transform:scale(1.22)}}
@keyframes puppyWag{0%,100%{transform:rotate(0)}25%{transform:rotate(-4deg)}75%{transform:rotate(4deg)}}
@keyframes trot1{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-7px) rotate(2deg)}}
@keyframes trot2{0%,100%{transform:translateY(-6px) rotate(2deg)}50%{transform:translateY(0) rotate(-2deg)}}
@keyframes zzzFloat{
  0%{opacity:0;transform:translate(0,0) scale(0.7)}
  40%{opacity:1}
  100%{opacity:0;transform:translate(12px,-24px) scale(1.2)}
}

/* 顶部固定大容器 */
.header-box{
  position:sticky;top:0;z-index:25;
  background:#fffefc;
  border-bottom:1.5px solid var(--line-strong);
  box-shadow:var(--shadow-sm);
  contain:layout style paint;
}

/* 顶部导航 */
.topbar{
  padding:6px 14px;
  display:flex;align-items:center;gap:8px;
  flex-wrap:nowrap;white-space:nowrap;
}
.icon-btn{
  flex-shrink:0;padding:4px 8px;font-size:12px;
}
.brand{
  display:inline-flex;align-items:center;gap:6px;cursor:pointer;user-select:none;
  flex-shrink:0;
}
.brand-img{
  height:26px;width:auto;flex:none;
}
.brand:hover .brand-img{animation:puppyWag .6s ease infinite}
.brand-title{
  font-size:15px;font-weight:800;letter-spacing:.2px;
  color:var(--ink-primary);display:inline-flex;align-items:center;gap:2px;
  white-space:nowrap;
}
.spacer{flex:1;min-width:4px}

/* 用户状态徽章与按钮 */
.user-pill{
  display:inline-flex;align-items:center;gap:4px;
  font-size:11.5px;font-weight:700;
  padding:3px 10px;border-radius:var(--radius-pill);
  border:1px solid var(--line-strong);
  background:#fff;
}
.user-pill.a{background:var(--dog-a-bg);border-color:var(--dog-a-bd);color:var(--dog-a-text)}
.user-pill.b{background:var(--dog-b-bg);border-color:var(--dog-b-bd);color:var(--dog-b-text)}

.p-btn{
  border:1.5px solid var(--line-strong);background:#fff;
  border-radius:var(--radius-pill);padding:4px 11px;
  font-size:12px;font-weight:600;color:var(--ink-primary);
  cursor:pointer;display:inline-flex;align-items:center;gap:3px;
}
.p-btn:hover{background:#fff8f0;border-color:#e4cdb5}
.p-btn:active{transform:scale(0.96)}
.p-btn.pri{
  background:linear-gradient(135deg, var(--dog-a) 0%, #ff7ea1 100%);
  color:#fff;border-color:var(--dog-a);
}
.p-btn.pri:hover{filter:brightness(1.05)}
.p-btn.pri-b{
  background:linear-gradient(135deg, var(--dog-b) 0%, #fbb042 100%);
  color:#fff;border-color:var(--dog-b);
}
.p-btn.danger{color:#b91c1c;border-color:#fecaca}
.p-btn.danger:hover{background:#fff1f2}

/* 周导航控制器 */
.weekbar-wrap{
  max-width:1120px;margin:0 auto;padding:0 10px 5px;
}
.weekbar-card{
  background:#fffefc;border:1px solid var(--line-subtle);
  border-radius:14px;padding:4px 10px;box-shadow:var(--shadow-sm);
  display:flex;align-items:center;justify-content:space-between;
  flex-wrap:nowrap;gap:6px;
}
.week-nav{
  display:flex;align-items:center;gap:3px;flex-shrink:0;
}
.week-nav .p-btn{
  font-size:11px;padding:3px 7px;white-space:nowrap;
}
.week-info{
  text-align:right;display:flex;align-items:center;gap:4px;flex-shrink:0;
}
.wl-date{
  font-size:12px;font-weight:800;color:var(--ink-primary);
  letter-spacing:.1px;white-space:nowrap;
}
.wl-schools{
  display:flex;align-items:center;gap:5px;flex-wrap:wrap;
}
.school-badge{
  font-size:11px;font-weight:700;padding:1px 8px;
  border-radius:var(--radius-pill);border:1px solid;
}
.school-badge.a{background:var(--dog-a-bg);border-color:var(--dog-a-bd);color:var(--dog-a-text)}
.school-badge.b{background:var(--dog-b-bg);border-color:var(--dog-b-bd);color:var(--dog-b-text)}

.legend-card{
  display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  font-size:11.5px;color:var(--ink-muted);font-weight:600;
}
.legend-item{display:inline-flex;align-items:center;gap:4px}
.legend-item .dot{
  width:9px;height:9px;border-radius:50%;display:inline-block;
}
.dot.a{background:var(--dog-a);box-shadow:0 0 0 2px var(--dog-a-bd)}
.dot.b{background:var(--dog-b);box-shadow:0 0 0 2px var(--dog-b-bd)}
.dot.free{background:var(--free-accent);box-shadow:0 0 0 2px var(--free-bd)}

/* 甜蜜纪念日与倒计时条 */
.anniv-bar{
  max-width:1120px;margin:2px auto 6px;padding:0 10px;
  display:flex;align-items:center;gap:8px;overflow-x:auto;-webkit-overflow-scrolling:touch;
  scrollbar-width:none;
}
.anniv-bar::-webkit-scrollbar{display:none}
.anniv-capsule{
  display:inline-flex;align-items:center;gap:6px;flex-shrink:0;
  background:#fff;border:1.5px solid var(--line-strong);
  border-radius:var(--radius-pill);padding:3px 10px;
  font-size:11.5px;font-weight:700;color:var(--ink-primary);
  box-shadow:var(--shadow-sm);cursor:pointer;user-select:none;
  transition:all .15s ease;
}
.anniv-capsule:hover{transform:translateY(-1px);background:#fff9f2;border-color:var(--accent)}
.anniv-capsule.love{background:#fff1f4;border-color:#ffccd5;color:#e11d48}
.anniv-capsule.birthday{background:#fff8eb;border-color:#fed7aa;color:#d97706}
.anniv-capsule.countdown{background:#f0fdf4;border-color:#bbf7d0;color:#16a34a}
.anniv-add-btn{
  display:inline-flex;align-items:center;gap:4px;flex-shrink:0;
  border:1px dashed var(--line-strong);border-radius:var(--radius-pill);
  padding:3px 9px;font-size:11px;font-weight:600;color:var(--ink-muted);
  background:transparent;cursor:pointer;transition:all .15s ease;
}
.anniv-add-btn:hover{background:#fff;color:var(--ink-primary);border-color:var(--dog-a)}

/* 模式切换胶囊 (课表 / 手账) */
.view-seg{
  display:inline-flex;align-items:center;background:#f5eee6;
  padding:2px;border-radius:var(--radius-pill);border:1px solid var(--line-subtle);
  margin-left:4px;
}
.view-seg-opt{
  padding:3px 9px;border-radius:var(--radius-pill);font-size:11.5px;font-weight:700;
  cursor:pointer;color:var(--ink-muted);transition:all .15s ease;user-select:none;
}
.view-seg-opt.on{
  background:#fff;color:var(--dog-a);box-shadow:0 1px 3px rgba(0,0,0,0.08);
}

/* 月历手账视图 */
.month-cal-wrap{
  max-width:1120px;margin:0 auto;padding:6px 12px 24px;
}
.month-topcard{
  background:#fffefc;border:1.5px solid var(--line-strong);border-radius:20px;
  padding:10px 16px;box-shadow:var(--shadow-sm);margin-bottom:12px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;
}
.month-grid{
  display:grid;grid-template-columns:repeat(7, 1fr);gap:8px;
}
.month-head-cell{
  text-align:center;font-size:12px;font-weight:800;color:var(--ink-muted);
  padding:6px 0;letter-spacing:.5px;
}
.month-cell{
  background:#fff;border:1.5px solid var(--line-strong);border-radius:16px;
  min-height:92px;padding:6px 8px;display:flex;flex-direction:column;
  box-shadow:var(--shadow-sm);cursor:pointer;transition:all .18s ease;
  position:relative;overflow:hidden;
}
.month-cell:hover{
  border-color:var(--accent);transform:translateY(-2px);box-shadow:var(--shadow-md);
}
.month-cell.other-month{
  opacity:0.4;background:#fdfbf7;
}
.month-cell.today{
  border:2px solid var(--dog-a);background:#fffbfb;
}
.month-cell.today .mday-num{
  background:var(--dog-a);color:#fff;border-radius:50%;width:20px;height:20px;
  display:inline-flex;align-items:center;justify-content:center;
}
.mday-head{
  display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;
}
.mday-num{
  font-size:12.5px;font-weight:800;color:var(--ink-primary);
}
.mday-badges{
  display:flex;align-items:center;gap:3px;
}
.mday-badge{
  font-size:11px;line-height:1;
}
.mday-polaroid{
  margin-top:auto;display:flex;align-items:center;gap:6px;
  background:#fffef9;border:1px solid #ebd8c8;border-radius:10px;padding:3px;
  box-shadow:0 1px 3px rgba(0,0,0,0.06);
}
.mday-thumb{
  width:32px;height:32px;border-radius:6px;object-fit:cover;flex-shrink:0;
}
.mday-info{
  overflow:hidden;font-size:10.5px;line-height:1.2;font-weight:700;
  color:var(--ink-primary);text-overflow:ellipsis;white-space:nowrap;
}

/* 拍立得照片墙与日记弹窗 */
.polaroid-gallery{
  display:flex;flex-wrap:wrap;gap:12px;margin:12px 0;
}
.polaroid-card{
  background:#fff;padding:6px 6px 14px;border:1px solid #e2d3c3;
  border-radius:6px;box-shadow:0 3px 8px rgba(0,0,0,0.08);
  width:calc(33.333% - 8px);max-width:130px;transform:rotate(-1deg);
  transition:all .2s ease;
}
.polaroid-card:nth-child(even){transform:rotate(1.5deg)}
.polaroid-card:hover{transform:rotate(0deg) scale(1.05);z-index:2}
.polaroid-img{
  width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:4px;display:block;
}
.diary-item-card{
  background:#fffefc;border:1.5px solid var(--line-strong);border-radius:16px;
  padding:12px;margin-bottom:12px;box-shadow:var(--shadow-sm);
}

/* 日历区域 */
main{max-width:1120px;margin:0 auto;padding:6px 12px 16px}
.calwrap{
  overflow-x:auto;-webkit-overflow-scrolling:touch;
  border-radius:24px;border:2px solid var(--line-strong);
  box-shadow:var(--shadow-md);background:#fff;
}
.cal{
  min-width:1020px;display:grid;grid-template-columns:58px repeat(7,minmax(136px,1fr));
  position:relative;background:#fff;
}

/* 左上角与时间轴 */
.corner{
  background:#fdf9f4;border-right:1.5px solid var(--line-strong);
  border-bottom:2px solid var(--line-strong);
  position:sticky;left:0;top:0;z-index:10;
  display:flex;align-items:center;justify-content:center;
  font-size:11px;font-weight:700;color:var(--ink-light);
}
.axcol{
  position:sticky;left:0;z-index:8;
  background:#fdf9f4;border-right:1.5px solid var(--line-strong);
}
.axh{
  position:absolute;right:8px;transform:translateY(-50%);
  font-size:11px;font-weight:700;color:var(--ink-light);
  font-variant-numeric:tabular-nums;user-select:none;
}

/* 星期表头 */
.dh{
  background:#fffdfa;border-right:1px solid var(--line-subtle);
  border-bottom:2px solid var(--line-strong);
  padding:10px 4px 8px;text-align:center;
  display:flex;flex-direction:column;align-items:center;gap:3px;
  position:relative;
}
.dh:last-child{border-right:none}
.dh-title{display:flex;align-items:baseline;gap:5px}
.dh-weekday{font-size:14px;font-weight:800;color:var(--ink-primary)}
.dh-date{font-size:12px;font-weight:600;color:var(--ink-muted);font-variant-numeric:tabular-nums}
.dh.today{
  background:linear-gradient(180deg, #fff2f5 0%, #fffbfc 100%);
  border-bottom-color:var(--dog-a);
}
.dh.today .dh-weekday{color:var(--dog-a)}
.today-badge{
  font-size:10.5px;font-weight:800;
  background:linear-gradient(135deg, var(--dog-a) 0%, #ff85a2 100%);
  color:#fff;padding:1px 8px;border-radius:var(--radius-pill);
  box-shadow:0 2px 6px rgba(255,96,136,0.3);
}
.dh-split{
  display:flex;align-items:center;justify-content:center;
  gap:12px;width:100%;font-size:10.5px;font-weight:700;
  color:var(--ink-muted);margin-top:2px;
}
.dh-split .part-a{color:var(--dog-a-text);display:inline-flex;align-items:center;gap:2px}
.dh-split .part-b{color:var(--dog-b-text);display:inline-flex;align-items:center;gap:2px}

/* 日期列 */
.dcol{
  position:relative;overflow:hidden;
  border-right:1px solid var(--line-subtle);
  background-image:repeating-linear-gradient(
    to bottom,
    transparent 0,
    transparent calc(var(--pxh) - 1px),
    #f8eee4 calc(var(--pxh) - 1px),
    #f8eee4 var(--pxh)
  );
}
.dcol:last-child{border-right:none}
.divider{
  position:absolute;top:0;bottom:0;left:50%;width:1px;
  border-left:1.5px dashed #ebdcd0;z-index:2;pointer-events:none;
}

/* 休息日（无日程）线条小狗插画 */
.sleepy{
  position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  z-index:1;pointer-events:none;text-align:center;width:90%;
}
.sleep-box{
  display:inline-flex;flex-direction:column;align-items:center;
  background:rgba(255,255,255,0.92);backdrop-filter:blur(4px);
  padding:12px 14px 10px;border-radius:20px;
  border:1.5px dashed #ebdcd0;box-shadow:var(--shadow-sm);
}
.sleep-box img{
  width:100px;height:auto;display:block;
  mix-blend-mode:multiply;
}
.sleep-zs{
  font-family:sans-serif;font-size:12px;font-weight:800;
  color:#ff8fab;margin-bottom:-4px;letter-spacing:2px;
  animation:zzzFloat 2.4s ease-in-out infinite;
}
.sleep-text{
  font-size:11px;font-weight:700;color:var(--ink-muted);
  margin-top:4px;line-height:1.3;
}

/* 共同空闲时段 (俩汪贴贴) */
.fband{
  position:absolute;left:2px;right:2px;
  background:linear-gradient(135deg, rgba(209, 250, 229, 0.78) 0%, rgba(236, 253, 245, 0.92) 100%);
  border-top:1.5px dashed var(--free-bd);
  border-bottom:1.5px dashed var(--free-bd);
  border-radius:8px;z-index:1;
  display:flex;align-items:center;justify-content:center;
  pointer-events:none;box-sizing:border-box;
}
.fdur-tag{
  font-size:10px;font-weight:800;color:var(--free-text);
  background:rgba(255,255,255,0.9);padding:2px 7px;
  border-radius:var(--radius-pill);border:1px solid var(--free-bd);
  box-shadow:0 1px 3px rgba(16,185,129,0.15);
  display:inline-flex;align-items:center;gap:3px;
  white-space:nowrap;line-height:1;
}

/* 日程卡片 */
.ev{
  position:absolute;z-index:3;
  border-radius:12px;padding:4px 6px;
  font-size:11px;line-height:1.35;
  overflow:hidden;cursor:pointer;
  transition:transform .18s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow .18s ease;
  box-sizing:border-box;
}
.ev:hover{
  transform:translateY(-1px) scale(1.02);
  z-index:5;box-shadow:var(--shadow-md);
}
.ev.a{
  left:3px;width:calc(50% - 5px);
  background:var(--dog-a-bg);border:1.5px solid var(--dog-a-bd);
  border-left:4px solid var(--dog-a);color:var(--dog-a-text);
  box-shadow:0 2px 6px rgba(255,96,136,0.08);
}
.ev.b{
  left:calc(50% + 2px);width:calc(50% - 5px);
  background:var(--dog-b-bg);border:1.5px solid var(--dog-b-bd);
  border-left:4px solid var(--dog-b);color:var(--dog-b-text);
  box-shadow:0 2px 6px rgba(234,138,21,0.08);
}
.ev .et-pill{
  display:inline-block;font-size:10px;font-weight:800;
  font-variant-numeric:tabular-nums;line-height:1.2;
  margin-bottom:2px;
}
.ev.a .et-pill{color:var(--dog-a)}
.ev.b .et-pill{color:var(--dog-b)}
.ev .etitle{
  display:block;font-size:11.5px;font-weight:700;
  color:var(--ink-primary);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;
}
.ev .em{
  display:block;font-size:10px;color:var(--ink-muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.ev .cbadge{
  position:absolute;bottom:2px;right:3px;
  font-size:9.5px;font-weight:800;
  background:#fff;border:1px solid var(--line-strong);
  border-radius:var(--radius-pill);padding:0 4px;
  color:var(--ink-muted);line-height:1.3;
  box-shadow:0 1px 2px rgba(0,0,0,0.05);
}
.ev .del{
  position:absolute;top:-5px;right:-3px;
  width:18px;height:18px;border-radius:50%;
  border:1px solid #fecaca;background:#fff;
  color:#b91c1c;font-size:12px;font-weight:bold;
  line-height:16px;cursor:pointer;display:none;padding:0;
  box-shadow:0 1px 4px rgba(0,0,0,0.1);
}
.ev:hover .del{display:block}
@media (hover:none){.ev .del{display:block}}

/* 底部线条小狗散步小分队 */
.dogwalk-section{
  max-width:1120px;margin:24px auto 10px;padding:0 12px;
  text-align:center;
}
.dogwalk-banner{
  display:inline-flex;align-items:center;gap:8px;
  font-size:12.5px;font-weight:700;color:var(--ink-muted);
  background:rgba(255,255,255,0.85);padding:6px 18px;
  border-radius:var(--radius-pill);border:1.5px solid var(--line-strong);
  box-shadow:var(--shadow-sm);margin-bottom:14px;
}
.heart-pulse{display:inline-block;animation:heartBeat 1.8s ease infinite}
.dogwalk{
  display:flex;justify-content:center;align-items:flex-end;
  gap:16px;flex-wrap:wrap;
}
.walker{
  background:#fff;border:1.5px solid var(--line-strong);
  border-radius:18px;padding:8px 10px;
  box-shadow:0 4px 12px rgba(139,92,44,0.08);
  transition:transform .2s ease;
}
.walker img{
  width:58px;height:auto;display:block;
  mix-blend-mode:multiply;
}
.walker.step-1{animation:trot1 1.6s ease-in-out infinite}
.walker.step-2{animation:trot2 1.6s ease-in-out infinite}
.walker.step-3{animation:trot1 1.6s ease-in-out infinite .4s}
.walker.step-4{animation:trot2 1.6s ease-in-out infinite .4s}
.walker:hover{transform:scale(1.1) rotate(0deg)!important}

/* FAB 悬浮添加按钮 */
.fab{
  position:fixed;right:22px;bottom:24px;
  background:linear-gradient(135deg, var(--dog-a) 0%, #ff85a2 100%);
  color:#fff;border:none;border-radius:var(--radius-pill);
  padding:12px 22px;font-size:14.5px;font-weight:800;
  box-shadow:0 8px 24px rgba(255,96,136,0.4);
  cursor:pointer;z-index:30;
  display:inline-flex;align-items:center;gap:6px;
  transition:all .2s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.fab:hover{
  transform:translateY(-2px) scale(1.04);
  box-shadow:0 12px 28px rgba(255,96,136,0.5);
}
.fab:active{transform:scale(0.95)}

/* 弹窗遮罩与对话框 */
.overlay{
  position:fixed;inset:0;
  background:rgba(61, 47, 37, 0.45);
  backdrop-filter:blur(5px);
  display:none;align-items:flex-start;justify-content:center;
  z-index:50;padding:26px 12px;overflow-y:auto;
}
.overlay.show{display:flex}
.modal{
  background:#fffefc;border:2px solid var(--line-strong);
  border-radius:24px;max-width:460px;width:100%;
  padding:22px 24px;box-shadow:var(--shadow-lg);
  position:relative;animation:modalPop .22s cubic-bezier(0.34, 1.56, 0.64, 1);
}
@keyframes modalPop{from{opacity:0;transform:scale(0.92)}to{opacity:1;transform:scale(1)}}

.mhead{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:14px;border-bottom:1.5px dashed var(--line-strong);
  padding-bottom:12px;
}
.mhead-left{display:flex;align-items:center;gap:8px}
.mhead h3{font-size:17px;font-weight:800;color:var(--ink-primary)}
.mhead-img{width:46px;height:auto;flex:none;mix-blend-mode:multiply}

.field{margin-bottom:12px}
.field label{
  display:block;font-size:12.5px;font-weight:700;
  color:var(--ink-muted);margin-bottom:5px;
}
.field input,.field select{
  width:100%;border:1.5px solid var(--line-strong);
  border-radius:12px;padding:9px 12px;
  font-size:14px;font-family:inherit;background:#fff;
  color:var(--ink-primary);transition:border-color .2s, box-shadow .2s;
}
.field input:focus,.field select:focus{
  outline:none;border-color:var(--dog-a);
  box-shadow:0 0 0 3px rgba(255,96,136,0.15);
}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}

/* 分段选项卡 */
.seg{display:flex;gap:8px}
.seg .opt{
  flex:1;border:1.5px solid var(--line-strong);
  border-radius:14px;padding:8px 6px;text-align:center;
  cursor:pointer;font-size:13.5px;font-weight:700;
  user-select:none;background:#fff;color:var(--ink-muted);
  transition:all .18s ease;display:flex;align-items:center;justify-content:center;gap:4px;
}
.seg .opt:hover{background:#fffaf3;border-color:#e4cdb5}
.seg .opt.on{
  border-color:var(--dog-a);background:var(--dog-a-bg);
  color:var(--dog-a-text);box-shadow:0 2px 8px rgba(234,138,21,0.15);
}
.seg .opt.on.b-side{
  border-color:var(--dog-b);background:var(--dog-b-bg);
  color:var(--dog-b-text);box-shadow:0 2px 8px rgba(255,96,136,0.15);
}

.modal .foot{
  display:flex;gap:10px;justify-content:flex-end;margin-top:14px;
}

/* 日程详情与评论展示 */
.d-meta{
  display:grid;gap:8px;font-size:13.5px;
  background:#fffaf3;border:1.5px solid var(--line-strong);
  border-radius:16px;padding:12px 14px;
}
.d-meta .row{display:flex;align-items:baseline}
.d-meta .k{
  color:var(--ink-muted);font-size:12px;font-weight:700;
  width:46px;flex:none;
}
.d-meta .v{color:var(--ink-primary);font-weight:600}

/* 快捷情侣贴纸短语 */
.quick-phrases{
  display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 6px;
}
.phrase-pill{
  font-size:11.5px;font-weight:700;padding:3px 9px;
  border-radius:var(--radius-pill);border:1px solid var(--line-strong);
  background:#fff;color:var(--ink-muted);cursor:pointer;
  transition:all .15s ease;user-select:none;
}
.phrase-pill:hover{
  background:var(--dog-a-bg);border-color:var(--dog-a-bd);
  color:var(--dog-a-text);transform:scale(1.03);
}

/* 留言区 */
.cmts{
  display:grid;gap:8px;max-height:240px;overflow-y:auto;
  margin:8px 0 10px;padding:4px;
}
.cmt{
  background:#fff;border:1.5px solid var(--line-strong);
  border-radius:14px;padding:9px 12px;font-size:13px;
  box-shadow:0 1px 4px rgba(139,92,44,0.04);
}
.cmt .ch{display:flex;gap:6px;align-items:center;margin-bottom:3px}
.cmt .cname{
  font-size:11px;font-weight:800;border-radius:var(--radius-pill);
  padding:1px 8px;border:1px solid;
}
.cmt .cname.a{background:var(--dog-a-bg);border-color:var(--dog-a-bd);color:var(--dog-a-text)}
.cmt .cname.b{background:var(--dog-b-bg);border-color:var(--dog-b-bd);color:var(--dog-b-text)}
.cmt .ct{font-size:10.5px;color:var(--ink-light);font-variant-numeric:tabular-nums}
.cmt .cdel{
  margin-left:auto;border:none;background:none;
  color:var(--ink-light);cursor:pointer;font-size:11px;padding:0;
}
.cmt .cdel:hover{color:#b91c1c}
.cmt .ctxt{white-space:pre-wrap;word-break:break-word;color:var(--ink-primary);font-weight:500}
.empty-cmt{
  color:var(--ink-light);font-size:12.5px;text-align:center;
  padding:12px 0;font-weight:600;
}
.crow{display:flex;gap:8px;margin-top:6px}
.crow input{
  flex:1;border:1.5px solid var(--line-strong);
  border-radius:12px;padding:9px 12px;font-size:13.5px;
}

/* 登录界面 (线条小狗治愈卡片) */
.login-wrap{
  min-height:85vh;display:flex;align-items:center;
  justify-content:center;padding:16px;
}
.login{
  background:#fffefc;border:2px solid var(--line-strong);
  border-radius:28px;padding:32px 28px;max-width:390px;
  width:100%;position:relative;box-shadow:var(--shadow-lg);
  text-align:center;
}
.login-hero{
  margin:0 auto 12px;position:relative;display:inline-block;
}
.login-hero img{
  width:190px;height:auto;display:block;
  mix-blend-mode:multiply;
  transition:transform .3s ease;
}
.login-hero:hover img{transform:scale(1.04)}
.login-wave-corner{
  position:absolute;right:8px;top:8px;
  transform:rotate(10deg);pointer-events:none;
}
.login-wave-corner img{
  width:58px;display:block;mix-blend-mode:multiply;
}
.login h2{
  font-size:20px;font-weight:800;color:var(--ink-primary);
  margin-bottom:4px;letter-spacing:.3px;
}
.login .sub{
  color:var(--ink-muted);font-size:13px;margin-bottom:18px;font-weight:600;
}
.login-cards{
  display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px;
}
.login-card{
  border:2px solid var(--line-strong);border-radius:18px;
  padding:12px 8px;cursor:pointer;background:#fff;
  transition:all .2s ease;display:flex;flex-direction:column;
  align-items:center;gap:4px;user-select:none;
}
.login-card .ico{font-size:24px}
.login-card .name{font-size:13.5px;font-weight:800}
.login-card .desc{font-size:11px;color:var(--ink-light);font-weight:600}
.login-card:hover{background:#fffaf3;border-color:#e4cdb5}
.login-card.on.side-a{
  border-color:var(--dog-a);background:var(--dog-a-bg);
  box-shadow:0 4px 14px rgba(234,138,21,0.22);
}
.login-card.on.side-a .name{color:var(--dog-a-text)}
.login-card.on.side-b{
  border-color:var(--dog-b);background:var(--dog-b-bg);
  box-shadow:0 4px 14px rgba(255,96,136,0.22);
}
.login-card.on.side-b .name{color:var(--dog-b-text)}

/* 轻提示 Toast */
.toast{
  position:fixed;bottom:28px;left:50%;transform:translateX(-50%);
  background:rgba(61, 47, 37, 0.94);backdrop-filter:blur(6px);
  color:#fff;padding:10px 20px;border-radius:var(--radius-pill);
  font-size:13.5px;font-weight:700;opacity:0;
  transition:all .25s ease;z-index:99;pointer-events:none;
  box-shadow:0 8px 24px rgba(0,0,0,0.2);max-width:88vw;
  display:inline-flex;align-items:center;gap:6px;
}
.toast.show{opacity:1;transform:translateX(-50%) translateY(-4px)}

/* 响应式调整 */
@media (max-width:640px){
  .topbar{padding:5px 8px;gap:5px}
  .brand-img{height:20px}
  .brand-title{font-size:13.5px}
  .view-seg{margin-left:2px;padding:1.5px}
  .view-seg-opt{padding:2px 6px;font-size:10.5px}
  .icon-btn{padding:3px 6px;font-size:11px}
  .btn-txt{display:none} /* 移动端仅保留图标 ⚙️ / 💖，省出横向空间 */
  .spacer{min-width:0}
  .user-pill{padding:2px 7px;font-size:11px}
  .p-btn{padding:3px 7px;font-size:11.5px}
  .week-nav .p-btn{padding:2.5px 6px;font-size:10.5px}
  .weekbar-wrap{padding:0 6px 3px}
  .weekbar-card{padding:3px 8px;gap:4px}
  .wl-date{font-size:11px}
  .school-badge{font-size:10px;padding:1px 5px}
  .legend-card{font-size:10.5px;gap:6px}
  .modal{padding:18px 16px}
  .fab{right:16px;bottom:18px;padding:10px 18px;font-size:13.5px}
  .anniv-bar{padding:0 6px;margin:2px auto 4px}
  .anniv-capsule{font-size:11px;padding:2px 8px}
}
</style>
</head>
<body>
<div id="app"></div>
<div class="toast" id="toast"></div>
<script>
"use strict";
var WD = ["周一","周二","周三","周四","周五","周六","周日"];
var ME = null, DATA = null, ANCHOR = null, META = null;
var CURRENT_VIEW = "week"; // "week" | "month"
var MONTH_ANCHOR = todayISO().slice(0, 7); // YYYY-MM
var MONTH_DATA = null;
var diaryPhotosToUpload = []; // base64 list
var curEditingDiary = null;
var activeDiaryDate = "";
var addUid = "a", addRep = "weekly", loginUid = "a", annivType = "love";

function $(s){ return document.querySelector(s); }
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g, function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; }); }
function pad(n){ return n<10 ? "0"+n : ""+n; }
function todayISO(){ var d=new Date(); return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate()); }
function shiftISO(iso, days){ var p=iso.split("-"), t=new Date(+p[0], +p[1]-1, +p[2]);
  t.setDate(t.getDate()+days); return t.getFullYear()+"-"+pad(t.getMonth()+1)+"-"+pad(t.getDate()); }
function t2m(t){ var p=t.split(":"); return (+p[0])*60 + (+p[1]); }
function m2t(m){ return pad(Math.floor(m/60))+":"+pad(m%60); }
function durTxt(a,b){ var m=b-a, h=Math.floor(m/60);
  return h>0 ? h+"小时"+(m%60?(m%60)+"分":"") : m+"分钟"; }

function calcAnniv(item, todayStr){
  var today = new Date(todayStr + "T00:00:00");
  var tdate = new Date(item.date + "T00:00:00");
  var diffDays = Math.round((today - tdate) / 86400000);
  var icon = "💕", text = "";
  if (item.target_type === "love"){
    icon = "💖";
    if (diffDays >= 0){
      text = esc(item.title) + " 第 " + (diffDays + 1) + " 天";
    } else {
      text = esc(item.title) + " 还有 " + (-diffDays) + " 天";
    }
  } else if (item.target_type === "birthday"){
    icon = "🎂";
    var curYear = today.getFullYear();
    var nextBday = new Date(curYear, tdate.getMonth(), tdate.getDate());
    if (nextBday < today){
      nextBday = new Date(curYear + 1, tdate.getMonth(), tdate.getDate());
    }
    var daysLeft = Math.round((nextBday - today) / 86400000);
    text = (daysLeft === 0) ? (esc(item.title) + " 今天生日快乐！🎉") : (esc(item.title) + " 还有 " + daysLeft + " 天");
  } else {
    icon = "🎯";
    if (diffDays < 0){
      text = esc(item.title) + " 倒计时 " + (-diffDays) + " 天";
    } else if (diffDays === 0){
      text = esc(item.title) + " 就是今天啦！✨";
    } else {
      text = esc(item.title) + " 已过去 " + diffDays + " 天";
    }
  }
  return { icon: icon, text: text, type: item.target_type, diffDays: diffDays };
}
function toast(msg){ var t=$("#toast"); t.textContent=msg; t.classList.add("show");
  clearTimeout(t._h); t._h=setTimeout(function(){ t.classList.remove("show"); }, 2600); }

function api(path, opts){
  opts = opts || {};
  opts.headers = Object.assign({"Content-Type":"application/json"}, opts.headers||{});
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  return fetch(path, opts).then(function(r){
    return r.json().catch(function(){ return {}; }).then(function(j){
      if (!r.ok){
        if (r.status === 401 && path !== "/api/login"){ ME = null; render(); }
        throw new Error(j.error || ("请求失败 " + r.status));
      }
      return j;
    });
  });
}

function load(){
  if (CURRENT_VIEW === "month"){
    return api("/api/month?month=" + MONTH_ANCHOR).then(function(j){ MONTH_DATA = j; render(); })
      .catch(function(e){ if (e.message !== "unauth") toast(e.message); });
  } else {
    return api("/api/week?date=" + ANCHOR).then(function(j){ DATA = j; render(); })
      .catch(function(e){ if (e.message !== "unauth") toast(e.message); });
  }
}

/* ---------------- 渲染 ---------------- */
function render(){
  if (!ME){ renderLogin(); } else {
    if (CURRENT_VIEW === "month") renderMonthApp();
    else renderApp();
  }
}

function renderLogin(){
  var users = (META && META.users) || [];
  $("#app").innerHTML =
  '<div class="login-wrap">' +
    '<form class="login" id="loginForm">' +
      '<div class="login-wave-corner"><img src="/img/dog-wave.png" alt=""></div>' +
      '<div class="login-hero"><img src="/img/dog-couple.png" alt="线条小狗贴贴"></div>' +
      '<h2>线条小狗 · 两人日程 🐾</h2>' +
      '<div class="sub">汪！选好狗狗身份，输入密码进入小窝~</div>' +
      '<div class="login-cards" id="loginSeg">' +
        users.map(function(u){
          var isA = u.uid === "a";
          var ico = isA ? "🐶" : "🐾";
          var sub = isA ? "暖暖小金毛" : "软软小白狗";
          var cls = "login-card" + (u.uid === loginUid ? (" on " + (isA ? "side-a" : "side-b")) : "");
          return '<div class="' + cls + '" data-act="pick-login" data-uid="' + u.uid + '">' +
            '<span class="ico">' + ico + '</span>' +
            '<span class="name">' + esc(u.name) + '</span>' +
            '<span class="desc">' + sub + '</span>' +
          '</div>';
        }).join("") +
      '</div>' +
      '<div class="field" style="text-align:left"><label>小窝密码 🔑</label>' +
        '<input type="password" id="loginPw" placeholder="请输入密码" autocomplete="current-password" required>' +
      '</div>' +
      '<button class="p-btn pri" style="width:100%;padding:11px;font-size:15px;justify-content:center;margin-top:6px">' +
        '🐾 汪！立即进入' +
      '</button>' +
    '</form>' +
  '</div>';
}

var PXH = 50, WSTART = 480, WEND = 1320;
function gpx(m){ return Math.round((m - WSTART) * PXH / 60); }

function eventBlock(ev, uid, date){
  var bits = [];
  if (ev.location) bits.push('📍 ' + esc(ev.location));
  if (ev.week_spec) bits.push('🗓️ ' + esc(ev.week_spec) + '周');
  var tip = ev.title + (ev.note ? '：' + ev.note : '');
  var top = gpx(t2m(ev.tstart)), h = Math.max(gpx(t2m(ev.tend)) - top, 18);
  return '<div class="ev ' + uid + '" data-act="detail" data-id="' + ev.id + '" data-date="' + date +
    '" style="top:' + top +
    'px;height:' + h + 'px" title="' + esc(tip) + '">' +
    '<span class="et-pill">' + ev.tstart + '–' + ev.tend + '</span>' +
    '<span class="etitle">' + esc(ev.title) + '</span>' +
    (bits.length ? '<span class="em">' + bits.join(' · ') + '</span>' : '') +
    (ev.n_comments ? '<span class="cbadge" title="' + ev.n_comments + ' 条评论">💬 ' + ev.n_comments + '</span>' : '') +
    '<button class="del" data-act="del" data-id="' + ev.id + '" title="删除此日程">×</button></div>';
}

/* ---------------- 月历手账视图渲染 ---------------- */
function renderMonthApp(){
  var ym = MONTH_ANCHOR; // "YYYY-MM"
  var parts = ym.split("-");
  var year = +parts[0], month = +parts[1];
  var u = (META && META.users) || [{uid:"a",name:"小金毛"},{uid:"b",name:"小白狗"}];
  var myIsA = ME.uid === "a";
  var myPill = '<span class="user-pill ' + ME.uid + '">' + (myIsA ? '🐶 ' : '🐾 ') + esc(ME.name) + '</span>';

  // 计算本月日历格子 (周一到周日)
  var firstDay = new Date(year, month - 1, 1);
  var lastDay = new Date(year, month, 0);
  var startDayOfWeek = (firstDay.getDay() + 6) % 7; // 0=周一, 6=周日
  var totalDays = lastDay.getDate();

  var cellsHtml = "";
  // 周标题
  WD.forEach(function(w){
    cellsHtml += '<div class="month-head-cell">' + w + '</div>';
  });

  // 上月填充
  var prevMonthLastDay = new Date(year, month - 1, 0).getDate();
  for (var i = startDayOfWeek - 1; i >= 0; i--){
    var pDay = prevMonthLastDay - i;
    cellsHtml += '<div class="month-cell other-month"><div class="mday-head"><span class="mday-num">' + pDay + '</span></div></div>';
  }

  var byDate = (MONTH_DATA && MONTH_DATA.diaries_by_date) || {};
  var annivList = (MONTH_DATA && MONTH_DATA.anniversaries) || (DATA && DATA.anniversaries) || [];
  var todayStr = todayISO();

  // 本月各天
  for (var d = 1; d <= totalDays; d++){
    var dtStr = year + "-" + pad(month) + "-" + pad(d);
    var isToday = (dtStr === todayStr);
    var dayDiaries = byDate[dtStr] || [];
    
    // 纪念日判定
    var dayBadges = [];
    annivList.forEach(function(item){
      if (item.target_type === "birthday"){
        var bp = item.date.split("-");
        if (+bp[1] === month && +bp[2] === d){
          dayBadges.push('<span class="mday-badge" title="' + esc(item.title) + ' 生日">🎂</span>');
        }
      } else if (item.target_type === "love"){
        var lp = item.date.split("-");
        if (+lp[1] === month && +lp[2] === d){
          dayBadges.push('<span class="mday-badge" title="' + esc(item.title) + ' 纪念日">💖</span>');
        }
      } else {
        if (item.date === dtStr){
          dayBadges.push('<span class="mday-badge" title="' + esc(item.title) + '">🎯</span>');
        }
      }
    });

    var diaryPreview = "";
    if (dayDiaries.length > 0){
      var firstD = dayDiaries[0];
      var thumb = firstD.cover_photo ? ('/photos/' + firstD.cover_photo) : '/img/dog-add.png';
      diaryPreview = '<div class="mday-polaroid">' +
        '<img src="' + thumb + '" class="mday-thumb" alt="">' +
        '<div class="mday-info">' + esc(firstD.title) + '</div>' +
      '</div>';
    }

    cellsHtml += '<div class="month-cell' + (isToday ? ' today' : '') + '" data-act="open-day-diary" data-date="' + dtStr + '">' +
      '<div class="mday-head">' +
        '<span class="mday-num">' + d + '</span>' +
        '<div class="mday-badges">' + dayBadges.join("") + '</div>' +
      '</div>' +
      diaryPreview +
    '</div>';
  }

  // 下月填充对齐 7 的倍数
  var currentCellCount = startDayOfWeek + totalDays;
  var remain = (7 - (currentCellCount % 7)) % 7;
  for (var n = 1; n <= remain; n++){
    cellsHtml += '<div class="month-cell other-month"><div class="mday-head"><span class="mday-num">' + n + '</span></div></div>';
  }

  var count = (MONTH_DATA && MONTH_DATA.total_count) || 0;

  var html =
  '<div class="header-box" id="headerBox">' +
    '<header class="topbar">' +
      '<div class="brand">' +
        '<img src="/img/dogheads.png" alt="线条小狗" class="brand-img">' +
        '<span class="brand-title">两人日程 🐾</span>' +
        '<div class="view-seg">' +
          '<span class="view-seg-opt' + (CURRENT_VIEW==="week"?" on":"") + '" data-act="switch-view" data-v="week">🗓️ 课表</span>' +
          '<span class="view-seg-opt' + (CURRENT_VIEW==="month"?" on":"") + '" data-act="switch-view" data-v="month">📔 手账</span>' +
        '</div>' +
      '</div>' +
      '<span class="spacer"></span>' +
      '<button class="p-btn icon-btn" data-act="open-anniv" title="纪念日与倒计时">💖<span class="btn-txt"> 纪念日</span></button>' +
      '<button class="p-btn icon-btn" data-act="open-settings" title="设置">⚙️<span class="btn-txt"> 设置</span></button>' +
    '</header>' +
  '</div>' +

  '<div class="month-cal-wrap">' +
    '<div class="month-topcard">' +
      '<div class="week-nav">' +
        '<button class="p-btn" data-act="month-prev">‹ 上月</button>' +
        '<button class="p-btn pri" data-act="month-cur">🐾 本月</button>' +
        '<button class="p-btn" data-act="month-next">下月 ›</button>' +
      '</div>' +
      '<div style="font-size:15px;font-weight:800;color:var(--ink-primary)">' +
        '🗓️ ' + year + ' 年 ' + month + ' 月 · 俩汪足迹手账 🐾' +
      '</div>' +
      '<div style="font-size:12px;font-weight:700;color:var(--dog-a)">' +
        '✨ 本月已记录 ' + count + ' 篇出游美好回忆' +
      '</div>' +
    '</div>' +

    '<div class="month-grid">' + cellsHtml + '</div>' +
  '</div>' +

  '<button class="fab" data-act="open-add-diary" title="记下今天去哪玩啦">🐾 记手账 +</button>' +
  modalsHTML();

  $("#app").innerHTML = html;
}

function renderApp(){
  var u = DATA.users, wn = DATA.weeknums;
  var st0 = DATA.settings;
  WSTART = t2m(st0.window_start); WEND = t2m(st0.window_end);
  var CALH = Math.round((WEND - WSTART) * PXH / 60);
  var weekLabel = DATA.week_start + ' ~ ' + DATA.week_end.slice(5);

  var head = '<div class="corner">时间</div>';
  var body = '<div class="axcol" style="height:' + CALH + 'px">';
  for (var m = WSTART; m <= WEND; m += 60){
    var st = 'top:' + gpx(m) + 'px;';
    if (m === WSTART) st += 'transform:none;';
    else if (m === WEND) st += 'transform:translateY(-100%);';
    body += '<span class="axh" style="' + st + '">' + m2t(m) + '</span>';
  }
  body += '</div>';

  DATA.days.forEach(function(d){
    var isToday = d.date === todayISO();
    head += '<div class="dh' + (isToday ? ' today' : '') + '">' +
      '<div class="dh-title">' +
        '<span class="dh-weekday">' + WD[d.weekday] + '</span>' +
        '<span class="dh-date">' + d.date.slice(5) + '</span>' +
      '</div>' +
      (isToday ? '<span class="today-badge">✨ 今天 ✨</span>' : '') +
      '<div class="dh-split">' +
        '<span class="part-a">🐶 ' + esc(u.a.name) + '</span>' +
        '<span class="part-b">🐾 ' + esc(u.b.name) + '</span>' +
      '</div>' +
    '</div>';

    var emptyDay = d.users.a.events.length === 0 && d.users.b.events.length === 0;
    body += '<div class="dcol" style="height:' + CALH + 'px"><div class="divider"></div>' +
      (emptyDay ?
        '<div class="sleepy" title="今天俩汪都没安排，睡大觉~">' +
          '<div class="sleep-box">' +
            '<div class="sleep-zs">z Z Z 💤</div>' +
            '<img src="/img/dogrest.png" alt="睡大觉">' +
            '<div class="sleep-text">今天俩汪都放假<br>窝着睡大觉~ 💤</div>' +
          '</div>' +
        '</div>' : '');

    d.free.forEach(function(iv){
      var top = gpx(iv[0]), h = gpx(iv[1]) - top;
      body += '<div class="fband" style="top:' + top + 'px;height:' + h + 'px" title="共同空闲 ' +
        m2t(iv[0]) + '–' + m2t(iv[1]) + ' ' + durTxt(iv[0], iv[1]) + '">' +
        (h >= 24 ? '<span class="fdur-tag">🐾 贴贴 · ' + durTxt(iv[0], iv[1]) + ' 💕</span>' : '') +
      '</div>';
    });

    ['a','b'].forEach(function(uid){
      d.users[uid].events.forEach(function(ev){ body += eventBlock(ev, uid, d.date); });
    });
    body += '</div>';
  });

  var myIsA = ME.uid === 'a';
  var myPill = '<span class="user-pill ' + ME.uid + '">' +
    (myIsA ? '🐶 ' : '🐾 ') + esc(ME.name) +
  '</span>';

  var annivList = DATA.anniversaries || [];
  var todayStr = todayISO();
  var annivHtml = "";
  if (annivList.length > 0){
    annivHtml = '<div class="anniv-bar">' +
      annivList.map(function(item){
        var calc = calcAnniv(item, todayStr);
        return '<span class="anniv-capsule ' + calc.type + '" data-act="open-anniv-list" title="点击查看/管理纪念日">' +
          calc.icon + ' ' + calc.text +
        '</span>';
      }).join("") +
      '<button class="anniv-add-btn" data-act="open-anniv" title="添加纪念日/倒计时">＋ 记一个</button>' +
    '</div>';
  } else {
    annivHtml = '<div class="anniv-bar">' +
      '<button class="anniv-add-btn" data-act="open-anniv" style="margin:2px 0">' +
        '💖 记录第一个相恋纪念日 / 生日倒计时 🐾' +
      '</button>' +
    '</div>';
  }

  var html =
  '<div class="header-box" id="headerBox">' +
    '<header class="topbar">' +
      '<div class="brand">' +
        '<img src="/img/dogheads.png" alt="线条小狗" class="brand-img">' +
        '<span class="brand-title">两人日程 🐾</span>' +
        '<div class="view-seg">' +
          '<span class="view-seg-opt' + (CURRENT_VIEW==="week"?" on":"") + '" data-act="switch-view" data-v="week">🗓️ 课表</span>' +
          '<span class="view-seg-opt' + (CURRENT_VIEW==="month"?" on":"") + '" data-act="switch-view" data-v="month">📔 手账</span>' +
        '</div>' +
      '</div>' +
      '<span class="spacer"></span>' +
      '<button class="p-btn icon-btn" data-act="open-anniv" title="纪念日与倒计时">💖<span class="btn-txt"> 纪念日</span></button>' +
      '<button class="p-btn icon-btn" data-act="open-settings" title="设置">⚙️<span class="btn-txt"> 设置</span></button>' +
    '</header>' +

    '<div class="weekbar-wrap">' +
      '<div class="weekbar-card">' +
        '<div class="week-nav">' +
          '<button class="p-btn" data-act="prev">‹ 上周</button>' +
          '<button class="p-btn pri" data-act="today">🐾 今天</button>' +
          '<button class="p-btn" data-act="next">下周 ›</button>' +
        '</div>' +
        '<div class="week-info">' +
          '<span class="wl-date">🗓️ ' + weekLabel + '</span>' +
        '</div>' +
      '</div>' +
    '</div>' +
    annivHtml +
  '</div>' +

  '<main><div class="calwrap"><div class="cal" style="--pxh:' + PXH + 'px">' + head + body + '</div></div></main>' +

  '<div class="dogwalk-section">' +
    '<div class="dogwalk-banner">' +
      '<span class="heart-pulse">💕</span>' +
      '<span>今天也在认真生活，努力奔向你</span>' +
      '<span class="heart-pulse">🐾</span>' +
    '</div>' +
    '<div class="dogwalk">' +
      '<div class="walker step-1"><img src="/img/dog-walk1.png" alt="线条小狗"></div>' +
      '<div class="walker step-2"><img src="/img/dog-walk2.png" alt="线条小狗"></div>' +
      '<div class="walker step-3"><img src="/img/dog-walk3.png" alt="线条小狗"></div>' +
      '<div class="walker step-4"><img src="/img/dog-walk4.png" alt="线条小狗"></div>' +
    '</div>' +
  '</div>' +

  '<button class="fab" data-act="open-add">🐾 记新日程 ＋</button>' +
  modalsHTML();

  $("#app").innerHTML = html;
}

function modalsHTML(){
  var u = (DATA && DATA.users) || (MONTH_DATA && MONTH_DATA.users) || {a:{name:"小金毛"},b:{name:"小白狗"}};
  var uidOpts = ["a","b"].map(function(uid){
    var isA = uid === "a";
    var sideCls = isA ? "" : " b-side";
    var ico = isA ? "🐶 小金毛 · " : "🐾 小白狗 · ";
    return '<div class="opt' + (uid===addUid?(" on" + sideCls):"") + '" data-act="pick-add" data-uid="' + uid + '">' +
      ico + esc(u[uid] ? u[uid].name : uid) +
    '</div>';
  }).join("");
  var wdOpts = WD.map(function(w,i){ return '<option value="' + i + '">' + w + '</option>'; }).join("");
  var s = (DATA && DATA.settings) || {window_start:"08:00",window_end:"22:00",min_gap:20};
  var wStart = (DATA && DATA.week_start) || todayISO();

  return '<div class="overlay" id="ovAdd"><div class="modal">' +
    '<div class="mhead">' +
      '<div class="mhead-left"><h3>添加日程 🐾</h3></div>' +
      '<img src="/img/dog-add.png" alt="" class="mhead-img">' +
    '</div>' +
    '<form id="addForm">' +
      '<div class="field"><label>谁的日程？</label><div class="seg" id="addSeg">' + uidOpts + '</div></div>' +
      '<div class="field"><label>重复类型</label><div class="seg" id="repSeg">' +
        '<div class="opt' + (addRep==="weekly"?" on":"") + '" data-act="rep" data-v="weekly">🔄 每周重复</div>' +
        '<div class="opt' + (addRep==="once"?" on":"") + '" data-act="rep" data-v="once">📅 单次日程</div></div></div>' +
      '<div class="field" id="fldWd"><label>星期几</label><select id="f_wd">' + wdOpts + '</select></div>' +
      '<div class="field" id="fldDt" style="display:none"><label>具体日期</label><input type="date" id="f_date" value="' + wStart + '"></div>' +
      '<div class="row2">' +
        '<div class="field"><label>开始时间</label><input type="time" id="f_ts" required value="19:00"></div>' +
        '<div class="field"><label>结束时间</label><input type="time" id="f_te" required value="21:00"></div></div>' +
      '<div class="field"><label>事项名称</label><input id="f_title" required maxlength="120" placeholder="如：组会 / 跑步 / 一起吃火锅 🍲"></div>' +
      '<div class="field"><label>地点（可选）</label><input id="f_loc" maxlength="200" placeholder="如：教学楼A101 / 图书馆"></div>' +
      '<div class="field"><label>备注（可选）</label><input id="f_note" maxlength="200" placeholder="给 TA 留个小提醒~"></div>' +
      '<div class="field"><label>周次范围（可选：2-17 / 2-16双 / 1-4,9-12；留空=每周）</label><input id="f_ws" maxlength="60" placeholder="留空代表全学期每周"></div>' +
      '<div class="foot">' +
        '<button type="button" class="p-btn" data-act="close">取消</button>' +
        '<button class="p-btn pri">🐾 记在小本本上！</button>' +
      '</div>' +
    '</form></div></div>' +

    '<div class="overlay" id="ovSet"><div class="modal">' +
      '<div class="mhead">' +
        '<div class="mhead-left"><h3>小狗日程设置 🛠️</h3></div>' +
        '<img src="/img/dog-set.png" alt="" class="mhead-img">' +
      '</div>' +
      '<form id="setForm">' +
        '<div class="row2">' +
          '<div class="field"><label>🐶 a 显示名（小金毛）</label><input id="s_na" maxlength="30" value="' + esc(u.a.name) + '"></div>' +
          '<div class="field"><label>🐾 b 显示名（小白狗）</label><input id="s_nb" maxlength="30" value="' + esc(u.b.name) + '"></div>' +
          '<div class="field"><label>🐶 a 第1周周一</label><input type="date" id="s_w1a" value="' + (u.a.week1||"") + '"></div>' +
          '<div class="field"><label>🐾 b 第1周周一</label><input type="date" id="s_w1b" value="' + (u.b.week1||"") + '"></div>' +
          '<div class="field"><label>每天统计窗口 开始</label><input type="time" id="s_ws" value="' + s.window_start + '"></div>' +
          '<div class="field"><label>每天统计窗口 结束</label><input type="time" id="s_we" value="' + s.window_end + '"></div>' +
          '<div class="field"><label>最短空闲（分钟）</label><input type="number" id="s_mg" min="5" max="180" value="' + s.min_gap + '"></div>' +
        '</div>' +
        '<p style="font-size:11.5px;color:var(--ink-muted);line-height:1.45;margin-top:4px">' +
          '💡 周次与单双周换算基于各自「第1周周一」。两校可分别设定校历起始日期。' +
        '</p>' +
        '<div class="field" style="margin-top:14px"><label>📲 微信消息提醒（虾推啥/息知/Server酱）</label></div>' +
        '<div class="row2">' +
          '<div class="field"><label>🐶 a 微信 Token</label><input id="s_wxa" placeholder="贴入 a 的微信推送Token" value="' + esc((u.a.wx_uid)||"") + '"></div>' +
          '<div class="field"><label>🐾 b 微信 Token</label><input id="s_wxb" placeholder="贴入 b 的微信推送Token" value="' + esc((u.b.wx_uid)||"") + '"></div>' +
        '</div>' +
        '<p style="font-size:11px;color:var(--ink-muted);line-height:1.4;margin:2px 0 10px">' +
          '💡 支持 <strong>虾推啥 (xtuis.cn)</strong>，对方留言或新手账时，微信卡片秒弹并直接显示对方说的话。' +
        '</p>' +
        '<div class="field" style="margin-top:14px"><label>修改当前身份（' + esc(ME.name) + '）密码</label></div>' +
        '<div class="row2">' +
          '<div class="field"><input type="password" id="s_old" placeholder="旧密码（不改留空）" autocomplete="current-password"></div>' +
          '<div class="field"><input type="password" id="s_new" placeholder="新密码 ≥ 6 位" autocomplete="new-password"></div>' +
        '</div>' +
        '<div style="margin-top:16px;padding-top:14px;border-top:1px dashed var(--line-strong);display:flex;align-items:center;justify-content:space-between">' +
          '<span style="font-size:12px;color:var(--ink-muted)">当前狗狗：<strong>' + esc(ME.name) + '</strong></span>' +
          '<button type="button" class="p-btn danger" data-act="logout">🚪 退出当前登录</button>' +
        '</div>' +
        '<div class="foot">' +
          '<button type="button" class="p-btn" data-act="close">取消</button>' +
          '<button class="p-btn pri">保存设置</button>' +
        '</div>' +
      '</form></div></div>' +

    '<div class="overlay" id="ovAnniv"><div class="modal">' +
      '<div class="mhead">' +
        '<div class="mhead-left"><h3>情侣纪念日与倒计时 💖</h3></div>' +
        '<img src="/img/dog-couple.png" alt="" class="mhead-img">' +
      '</div>' +
      '<div id="annivListContainer" style="margin-bottom:14px;max-height:160px;overflow-y:auto"></div>' +
      '<form id="annivForm" style="border-top:1.5px dashed var(--line-strong);padding-top:12px">' +
        '<div class="field" style="font-weight:700;font-size:12.5px;color:var(--ink-primary);margin-bottom:6px">＋ 记录新纪念日 / 倒计时</div>' +
        '<div class="field"><label>类型</label>' +
          '<div class="seg" id="annivTypeSeg">' +
            '<div class="opt on" data-act="anniv-type" data-v="love">💖 相恋/相遇纪念日</div>' +
            '<div class="opt" data-act="anniv-type" data-v="birthday">🎂 TA 的生日</div>' +
            '<div class="opt" data-act="anniv-type" data-v="countdown">🎯 考试/旅行倒计时</div>' +
          '</div>' +
        '</div>' +
        '<div class="field"><label>名称</label><input id="an_title" required maxlength="60" placeholder="如：恋爱纪念日 / 楚菁生日 / 寒假去海边 🌊"></div>' +
        '<div class="field"><label>日期（相恋起始日 / 生日 / 目标日）</label><input type="date" id="an_date" required value="' + wStart + '"></div>' +
        '<div class="foot">' +
          '<button type="button" class="p-btn" data-act="close">关闭</button>' +
          '<button class="p-btn pri">💖 记下这一天！</button>' +
        '</div>' +
      '</form>' +
    '</div></div>' +

    '<div class="overlay" id="ovDetail"><div class="modal" id="ovDetailBox"></div></div>' +

    '<div class="overlay" id="ovDiaryDay"><div class="modal" id="ovDiaryDayBox"></div></div>' +

    '<div class="overlay" id="ovAddDiary"><div class="modal">' +
      '<div class="mhead">' +
        '<div class="mhead-left"><h3 id="df_modal_title">记下今天去哪玩啦 🐾</h3></div>' +
        '<img src="/img/dog-add.png" alt="" class="mhead-img">' +
      '</div>' +
      '<form id="addDiaryForm">' +
        '<input type="hidden" id="df_edit_id" value="">' +
        '<div class="field"><label>游玩日期</label><input type="date" id="df_date" required></div>' +
        '<div class="field"><label>游玩主题 / 事项</label><input id="df_title" required maxlength="80" placeholder="如：迪士尼一日游 🎡 / 武康路散步吃冰淇淋 🍦"></div>' +
        '<div class="row2">' +
          '<div class="field"><label>地点</label><input id="df_loc" maxlength="100" placeholder="如：上海迪士尼 / 外滩"></div>' +
          '<div class="field"><label>心情小贴纸</label>' +
            '<div style="display:flex;gap:4px">' +
              '<select id="df_mood" style="flex:1">' +
                '<option value="🥰 幸福贴贴">🥰 幸福贴贴</option>' +
                '<option value="🥳 快乐狂欢">🥳 快乐狂欢</option>' +
                '<option value="😋 撑成小猪">😋 撑成小猪</option>' +
                '<option value="🥱 累并快乐">🥱 累并快乐</option>' +
                '<option value="✨ 仪式感满满">✨ 仪式感满满</option>' +
                '<option value="💖 浪漫约会">💖 浪漫约会</option>' +
                '<option value="🍰 甜品治愈">🍰 甜品治愈</option>' +
                '<option value="🏕️ 户外露营">🏕️ 户外露营</option>' +
                '<option value="__custom__">✏️ 自定义心情...</option>' +
              '</select>' +
            '</div>' +
            '<input id="df_mood_custom" maxlength="30" placeholder="输入自定义心情（如：🐱 吸猫满足）" style="display:none;margin-top:4px">' +
          '</div>' +
        '</div>' +
        '<div class="field"><label>手账碎碎念 / 美好回忆</label><textarea id="df_content" rows="3" placeholder="今天遇到了什么好玩的事，拍了什么照片..." style="width:100%;border:1.5px solid var(--line-strong);border-radius:12px;padding:8px;font-family:inherit;font-size:13px"></textarea></div>' +
        '<div class="field"><label>拍立得照片（支持多张，自动压缩秒传 📷）</label>' +
          '<div style="margin:4px 0 6px">' +
            '<button type="button" class="p-btn" data-act="trigger-upload-photo" style="display:inline-flex;align-items:center;gap:4px;padding:5px 12px;font-size:12px;background:#fef3c7;border-color:#f59e0b">' +
              '📷 从手机相册选照片' +
            '</button>' +
            '<span id="df_photo_status" style="font-size:11px;color:var(--ink-muted);margin-left:8px"></span>' +
          '</div>' +
          '<input type="file" id="df_files" accept="image/*" multiple style="display:none">' +
          '<div id="df_preview" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px"></div>' +
        '</div>' +
        '<div class="foot">' +
          '<button type="button" class="p-btn" data-act="close">取消</button>' +
          '<button class="p-btn pri" id="df_submit_btn">✨ 贴在手账本上！</button>' +
        '</div>' +
      '</form>' +
    '</div></div>';
}

/* ---------------- 日程详情与情侣留言 ---------------- */
var curDetail = null;

function renderAnnivList(){
  var box = $("#annivListContainer");
  if (!box) return;
  var list = (DATA && DATA.anniversaries) || [];
  if (list.length === 0){
    box.innerHTML = '<div style="font-size:12px;color:var(--ink-muted);text-align:center;padding:12px 0">' +
      '还没有添加纪念日哦，在下方记一个吧 🐾</div>';
    return;
  }
  var todayStr = todayISO();
  box.innerHTML = list.map(function(item){
    var c = calcAnniv(item, todayStr);
    return '<div style="display:flex;align-items:center;justify-content:space-between;padding:6px 10px;background:#fff8eb;border:1px solid #fed7aa;border-radius:12px;margin-bottom:6px;font-size:12px">' +
      '<div><span style="font-size:14px;margin-right:4px">' + c.icon + '</span><strong>' + c.text + '</strong>' +
      '<span style="color:var(--ink-muted);font-size:11px;margin-left:6px">(' + esc(item.date) + ')</span></div>' +
      '<button class="p-btn danger" style="padding:2px 8px;font-size:11px" data-act="del-anniv" data-id="' + item.id + '">删除</button>' +
    '</div>';
  }).join("");
}

function openDayDiaries(dtStr){
  activeDiaryDate = dtStr;
  api("/api/diaries?date=" + dtStr).then(function(res){
    var list = res.diaries || [];
    var html = '<div class="mhead">' +
      '<div class="mhead-left"><h3>' + dtStr + ' 俩汪足迹 🐾</h3></div>' +
      '<img src="/img/dog-wave.png" alt="" class="mhead-img">' +
    '</div>';

    if (list.length === 0){
      html += '<div style="text-align:center;padding:24px 0;color:var(--ink-muted);font-size:13px">' +
        '这一天还没有记录足迹哦~<br>' +
        '<button class="p-btn pri" style="margin-top:12px" data-act="open-add-diary" data-date="' + dtStr + '">🐾 记下今天的快乐！</button>' +
      '</div>';
    } else {
      list.forEach(function(d){
        var photosHtml = "";
        if (d.photos && d.photos.length > 0){
          photosHtml = '<div class="polaroid-gallery">' +
            d.photos.map(function(p){
              return '<div class="polaroid-card">' +
                '<a href="/photos/' + p.file_name + '" target="_blank">' +
                  '<img src="/photos/' + p.file_name + '" class="polaroid-img" alt="">' +
                '</a>' +
              '</div>';
            }).join("") +
          '</div>';
        }

        var dJson = encodeURIComponent(JSON.stringify(d));
        html += '<div class="diary-item-card">' +
          '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">' +
            '<div>' +
              '<span style="font-size:15px;font-weight:800;color:var(--ink-primary)">' + esc(d.title) + '</span>' +
              (d.mood ? (' <span style="font-size:12px;background:#fff8eb;border-radius:10px;padding:2px 6px;margin-left:4px">' + esc(d.mood) + '</span>') : '') +
            '</div>' +
            '<div style="display:flex;gap:4px">' +
              '<button class="p-btn" style="padding:2px 8px;font-size:11px" data-act="edit-diary" data-diary="' + dJson + '">编辑</button>' +
              '<button class="p-btn danger" style="padding:2px 8px;font-size:11px" data-act="del-diary" data-id="' + d.id + '">删除</button>' +
            '</div>' +
          '</div>' +
          (d.location ? ('<div style="font-size:12px;color:var(--accent);font-weight:700;margin-bottom:6px">📍 ' + esc(d.location) + '</div>') : '') +
          (d.content ? ('<div style="font-size:13px;line-height:1.5;color:var(--ink-secondary);white-space:pre-wrap;margin-bottom:8px">' + esc(d.content) + '</div>') : '') +
          photosHtml +
          '<div style="font-size:11px;color:var(--ink-muted);text-align:right">由 ' + esc(d.author_name) + ' 记录于 ' + esc(d.created_at) + '</div>' +
        '</div>';
      });

      html += '<div style="text-align:center;margin-top:12px">' +
        '<button class="p-btn pri" data-act="open-add-diary" data-date="' + dtStr + '">＋ 追加一篇手账</button>' +
      '</div>';
    }

    html += '<div class="foot" style="margin-top:14px"><button type="button" class="p-btn" data-act="close">关闭</button></div>';
    $("#ovDiaryDayBox").innerHTML = html;
    $("#ovDiaryDay").classList.add("show");
  }).catch(function(err){ if (err.message !== "unauth") toast(err.message); });
}

function openDetail(id, date){
  curDetail = {id: +id, date: date};
  api("/api/event/" + id).then(function(j){
    $("#ovDetailBox").innerHTML = detailHTML(j);
    $("#ovDetail").classList.add("show");
  }).catch(function(err){ if (err.message !== "unauth") toast(err.message); });
}

function detailHTML(j){
  var ev = j.event, o = j.owner;
  var isA = o.uid === "a";
  var dateLine = "";
  if (curDetail && curDetail.date){
    var p = curDetail.date.split("-");
    var dt = new Date(+p[0], +p[1]-1, +p[2]);
    dateLine = curDetail.date.slice(5) + " " + WD[(dt.getDay() + 6) % 7];
  }
  var rows = '<div class="d-meta">' +
    '<div class="row"><span class="k">归属</span><span class="v">' +
      '<span class="school-badge ' + o.uid + '">' + (isA ? "🐶 小金毛 · " : "🐾 小白狗 · ") + esc(o.name) + '</span>' +
    '</span></div>' +
    '<div class="row"><span class="k">时间</span><span class="v">' + ev.tstart + "–" + ev.tend +
      (ev.repeat === "weekly" ? "（每周" + WD[ev.weekday] + "）" : "（单次 " + esc(ev.date) + "）") + '</span></div>' +
    (ev.location ? '<div class="row"><span class="k">地点</span><span class="v">📍 ' + esc(ev.location) + '</span></div>' : "") +
    (ev.week_spec ? '<div class="row"><span class="k">周次</span><span class="v">🗓️ ' + esc(ev.week_spec) + '周</span></div>' : "") +
    (ev.note ? '<div class="row"><span class="k">备注</span><span class="v">📝 ' + esc(ev.note) + '</span></div>' : "") +
    '</div>';

  var cmts = j.comments.length ? j.comments.map(function(c){
    var cIsA = c.uid === "a";
    return '<div class="cmt"><div class="ch">' +
      '<span class="cname ' + c.uid + '">' + (cIsA ? "🐶 " : "🐾 ") + esc(c.name || c.uid) + '</span>' +
      '<span class="ct">' + esc(c.created) + '</span>' +
      (ME && c.uid === ME.uid ? '<button class="cdel" data-act="del-cmt" data-id="' + c.id + '">删除</button>' : "") +
      '</div><div class="ctxt">' + esc(c.text) + '</div></div>';
  }).join("") : '<div class="empty-cmt">还没有情侣留言，发句甜甜的话吧~ 🐾</div>';

  var quickPhrases = [
    "🐾 收到汪！",
    "🥰 贴贴想你啦",
    "🍜 等你一起吃饭！",
    "🏃 准备出门啦",
    "❤️ 宝贝辛苦啦！",
    "✨ 准时到！"
  ].map(function(t){
    return '<span class="phrase-pill" data-act="quick-cmt" data-text="' + esc(t) + '">' + esc(t) + '</span>';
  }).join("");

  return '<div class="mhead">' +
      '<div class="mhead-left"><h3>' + esc(ev.title) + '</h3></div>' +
      '<img src="/img/dog-add.png" alt="" class="mhead-img">' +
    '</div>' +
    (dateLine ? '<div style="font-size:12px;color:var(--ink-muted);margin:-6px 0 10px;font-weight:600">当前查看 ' + dateLine + ' 的安排</div>' : "") +
    rows +
    '<div style="font-weight:800;font-size:13.5px;margin:14px 0 4px;color:var(--ink-primary);display:flex;align-items:center;gap:6px">' +
      '<span>情侣留言板 💕</span><span style="font-size:12px;color:var(--ink-light)">(' + j.comments.length + ')</span>' +
    '</div>' +
    '<div class="quick-phrases">' + quickPhrases + '</div>' +
    '<div class="cmts">' + cmts + '</div>' +
    '<div class="crow">' +
      '<input id="cmtText" maxlength="500" placeholder="和 TA 说点什么…（回车发送）">' +
      '<button class="p-btn pri" data-act="send-cmt" style="padding:8px 16px">🐾 发送</button>' +
    '</div>' +
    '<div class="foot" style="justify-content:space-between;margin-top:14px">' +
      '<button class="p-btn danger" data-act="del-event" data-id="' + ev.id + '">删除此日程</button>' +
      '<button class="p-btn" data-act="close">关闭</button>' +
    '</div>';
}

function refreshDetail(){
  if (curDetail) openDetail(curDetail.id, curDetail.date);
}

/* ---------------- 交互 ---------------- */
document.addEventListener("click", function(e){
  var el = e.target.closest("[data-act]");
  if (!el) return;
  var act = el.getAttribute("data-act");
  if (act === "pick-login"){
    loginUid = el.getAttribute("data-uid");
    document.querySelectorAll("#loginSeg .login-card").forEach(function(o){
      var uid = o.getAttribute("data-uid");
      var isA = uid === "a";
      o.className = "login-card" + (uid === loginUid ? (" on " + (isA ? "side-a" : "side-b")) : "");
    });
  } else if (act === "pick-add"){
    addUid = el.getAttribute("data-uid");
    document.querySelectorAll("#addSeg .opt").forEach(function(o){
      var uid = o.getAttribute("data-uid");
      var isA = uid === "a";
      o.className = "opt" + (uid === addUid ? (" on " + (isA ? "" : "b-side")) : "");
    });
  } else if (act === "rep"){
    addRep = el.getAttribute("data-v");
    document.querySelectorAll("#repSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === addRep);
    });
    $("#fldWd").style.display = addRep === "weekly" ? "" : "none";
    $("#fldDt").style.display = addRep === "once" ? "" : "none";
  } else if (act === "quick-cmt"){
    var phrase = el.getAttribute("data-text");
    var inp = $("#cmtText");
    if (inp){
      inp.value = phrase;
      inp.focus();
    }
  } else if (act === "prev"){ ANCHOR = shiftISO(ANCHOR, -7); load(); }
  else if (act === "next"){ ANCHOR = shiftISO(ANCHOR, 7); load(); }
  else if (act === "today"){ ANCHOR = todayISO(); load(); }
  else if (act === "open-add"){ $("#ovAdd").classList.add("show"); }
  else if (act === "open-settings"){ $("#ovSet").classList.add("show"); }
  else if (act === "switch-view"){
    CURRENT_VIEW = el.getAttribute("data-v");
    load();
  }
  else if (act === "month-prev"){
    var p = MONTH_ANCHOR.split("-"), y = +p[0], m = +p[1] - 1;
    if (m < 1){ m = 12; y--; }
    MONTH_ANCHOR = y + "-" + pad(m);
    load();
  }
  else if (act === "month-next"){
    var p = MONTH_ANCHOR.split("-"), y = +p[0], m = +p[1] + 1;
    if (m > 12){ m = 1; y++; }
    MONTH_ANCHOR = y + "-" + pad(m);
    load();
  }
  else if (act === "month-cur"){
    MONTH_ANCHOR = todayISO().slice(0, 7);
    load();
  }
  else if (act === "open-day-diary"){
    openDayDiaries(el.getAttribute("data-date"));
  }
  else if (act === "open-add-diary"){
    curEditingDiary = null;
    var dt = el.getAttribute("data-date") || activeDiaryDate || todayISO();
    if ($("#df_edit_id")) $("#df_edit_id").value = "";
    if ($("#df_modal_title")) $("#df_modal_title").textContent = "记下今天去哪玩啦 🐾";
    if ($("#df_submit_btn")) $("#df_submit_btn").textContent = "✨ 贴在手账本上！";
    if ($("#df_date")) $("#df_date").value = dt;
    if ($("#df_title")) $("#df_title").value = "";
    if ($("#df_loc")) $("#df_loc").value = "";
    if ($("#df_mood")) $("#df_mood").value = "🥰 幸福贴贴";
    if ($("#df_mood_custom")){ $("#df_mood_custom").value = ""; $("#df_mood_custom").style.display = "none"; }
    if ($("#df_content")) $("#df_content").value = "";
    if ($("#df_files")) $("#df_files").value = "";
    diaryPhotosToUpload = [];
    var prev = $("#df_preview");
    if (prev) prev.innerHTML = "";
    $("#ovAddDiary").classList.add("show");
  }
  else if (act === "trigger-upload-photo"){
    var fi = $("#df_files");
    if (fi) fi.click();
  }
  else if (act === "edit-diary"){
    try {
      var d = JSON.parse(decodeURIComponent(el.getAttribute("data-diary")));
      curEditingDiary = d;
      if ($("#df_edit_id")) $("#df_edit_id").value = d.id;
      if ($("#df_modal_title")) $("#df_modal_title").textContent = "修改手账记录 ✏️";
      if ($("#df_submit_btn")) $("#df_submit_btn").textContent = "💾 保存手账修改";
      if ($("#df_date")) $("#df_date").value = d.date;
      if ($("#df_title")) $("#df_title").value = d.title;
      if ($("#df_loc")) $("#df_loc").value = d.location || "";
      var standardMoods = ["🥰 幸福贴贴", "🥳 快乐狂欢", "😋 撑成小猪", "🥱 累并快乐", "✨ 仪式感满满", "💖 浪漫约会", "🍰 甜品治愈", "🏕️ 户外露营"];
      if (standardMoods.indexOf(d.mood) !== -1){
        if ($("#df_mood")) $("#df_mood").value = d.mood;
        if ($("#df_mood_custom")){ $("#df_mood_custom").value = ""; $("#df_mood_custom").style.display = "none"; }
      } else {
        if ($("#df_mood")) $("#df_mood").value = "__custom__";
        if ($("#df_mood_custom")){ $("#df_mood_custom").value = d.mood || ""; $("#df_mood_custom").style.display = "block"; }
      }
      if ($("#df_content")) $("#df_content").value = d.content || "";
      if ($("#df_files")) $("#df_files").value = "";
      diaryPhotosToUpload = [];
      var prev = $("#df_preview");
      if (prev){
        prev.innerHTML = (d.photos || []).map(function(p){
          return '<div class="edit-photo-thumb" data-file="' + p.file_name + '" style="position:relative;display:inline-block">' +
            '<img src="/photos/' + p.file_name + '" style="width:48px;height:48px;object-fit:cover;border-radius:6px;border:1px solid #ddd">' +
            '<span class="del-photo-btn" data-act="remove-edit-photo" data-file="' + p.file_name + '" style="position:absolute;top:-5px;right:-5px;background:#ef4444;color:#fff;border-radius:50%;width:16px;height:16px;line-height:16px;text-align:center;font-size:11px;cursor:pointer;font-weight:bold">×</span>' +
          '</div>';
        }).join("");
      }
      $("#ovAddDiary").classList.add("show");
    } catch(e){}
  }
  else if (act === "remove-edit-photo"){
    var pwrap = el.closest(".edit-photo-thumb");
    if (pwrap) pwrap.remove();
  }
  else if (act === "del-diary"){
    if (!confirm("确定删除这篇手账与照片？")) return;
    api("/api/diaries/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
      .then(function(){
        toast("已删除 🐾");
        load();
        if (activeDiaryDate) openDayDiaries(activeDiaryDate);
      })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  }
  else if (act === "open-anniv" || act === "open-anniv-list"){
    renderAnnivList();
    $("#ovAnniv").classList.add("show");
  }
  else if (act === "anniv-type"){
    annivType = el.getAttribute("data-v");
    document.querySelectorAll("#annivTypeSeg .opt").forEach(function(o){ o.classList.toggle("on", o.getAttribute("data-v") === annivType); });
  }
  else if (act === "del-anniv"){
    if (!confirm("确定删除这个纪念日？")) return;
    api("/api/anniversaries/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
      .then(function(){ toast("已删除 🐾"); return load(); })
      .then(function(){ renderAnnivList(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  }
  else if (act === "detail"){ openDetail(el.getAttribute("data-id"), el.getAttribute("data-date")); }
  else if (act === "send-cmt"){
    var inp = $("#cmtText");
    var text = inp ? inp.value.trim() : "";
    if (!text || !curDetail) return;
    api("/api/comment", {method:"POST", body:{event_id:curDetail.id, text:text}})
      .then(function(){ return load(); })
      .then(function(){ refreshDetail(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  }
  else if (act === "del-cmt"){
    if (!confirm("删除这条留言？")) return;
    api("/api/comment/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
      .then(function(){ return load(); })
      .then(function(){ refreshDetail(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  }
  else if (act === "del-event"){
    if (!confirm("确定删除这条日程（连同留言）？")) return;
    api("/api/events/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
      .then(function(){
        document.querySelectorAll(".overlay").forEach(function(o){ o.classList.remove("show"); });
        curDetail = null; toast("已删除日程 🐾"); load();
      })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  }
  else if (act === "close"){ document.querySelectorAll(".overlay").forEach(function(o){ o.classList.remove("show"); }); }
  else if (act === "logout"){
    api("/api/logout", {method:"POST"}).catch(function(){}).then(function(){
      document.querySelectorAll(".overlay").forEach(function(o){ o.classList.remove("show"); });
      ME = null; render();
    });
  } else if (act === "del"){
    if (!confirm("确定删除这条日程？")) return;
    api("/api/events/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
      .then(function(){ toast("已删除日程 🐾"); load(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  }
});

document.addEventListener("submit", function(e){
  var f = e.target;
  if (f.id === "loginForm"){
    e.preventDefault();
    api("/api/login", {method:"POST", body:{uid:loginUid, password:$("#loginPw").value}})
      .then(function(j){ ME = {uid:j.uid, name:j.name}; ANCHOR = todayISO(); toast("欢迎回家，" + j.name + " 🐾"); load(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  } else if (f.id === "addForm"){
    e.preventDefault();
    var body = {
      uid: addUid, repeat: addRep,
      title: $("#f_title").value.trim(), location: $("#f_loc").value.trim(),
      note: $("#f_note").value.trim(), week_spec: $("#f_ws").value.trim(),
      tstart: $("#f_ts").value, tend: $("#f_te").value,
      weekday: addRep === "weekly" ? +$("#f_wd").value : null,
      date: addRep === "once" ? $("#f_date").value : ""
    };
    api("/api/events", {method:"POST", body:body})
      .then(function(){
        document.querySelectorAll(".overlay").forEach(function(o){ o.classList.remove("show"); });
        toast("已记下啦 🐾"); load();
      })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  } else if (f.id === "setForm"){
    e.preventDefault();
    var body = {
      name_a: $("#s_na").value.trim(), name_b: $("#s_nb").value.trim(),
      week1_a: $("#s_w1a").value, week1_b: $("#s_w1b").value,
      window_start: $("#s_ws").value, window_end: $("#s_we").value,
      min_gap: +$("#s_mg").value,
      wx_a: $("#s_wxa") ? $("#s_wxa").value.trim() : "",
      wx_b: $("#s_wxb") ? $("#s_wxb").value.trim() : ""
    };
    var pwOld = $("#s_old").value, pwNew = $("#s_new").value;
    api("/api/settings", {method:"POST", body:body}).then(function(){
      var p = (pwOld && pwNew) ? api("/api/password", {method:"POST", body:{old:pwOld, new:pwNew}}) : Promise.resolve();
      return p.then(function(){
        document.querySelectorAll(".overlay").forEach(function(o){ o.classList.remove("show"); });
        toast("设置已保存 🐾");
        return api("/api/me").then(function(j){ ME = j; load(); });
      });
    }).catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  } else if (f.id === "annivForm"){
    e.preventDefault();
    var body = {
      title: $("#an_title").value.trim(),
      date: $("#an_date").value,
      target_type: annivType
    };
    api("/api/anniversaries", {method:"POST", body:body})
      .then(function(){
        $("#an_title").value = "";
        toast("已记下这一天啦 💖");
        return load();
      })
      .then(function(){ renderAnnivList(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  } else if (f.id === "addDiaryForm"){
    e.preventDefault();
    var editId = $("#df_edit_id") ? $("#df_edit_id").value : "";
    var keepPhotos = [];
    document.querySelectorAll(".edit-photo-thumb").forEach(function(el){
      var fn = el.getAttribute("data-file");
      if (fn) keepPhotos.push(fn);
    });
    var mVal = $("#df_mood").value;
    if (mVal === "__custom__"){
      mVal = ($("#df_mood_custom") ? $("#df_mood_custom").value.trim() : "") || "✨ 特别的心情";
    }
    var body = {
      date: $("#df_date").value,
      title: $("#df_title").value.trim(),
      location: $("#df_loc").value.trim(),
      mood: mVal,
      content: $("#df_content").value.trim(),
      photos: diaryPhotosToUpload
    };
    var url = "/api/diaries";
    if (editId){
      body.id = +editId;
      body.keep_photos = keepPhotos;
      url = "/api/diaries/update";
    }
    api(url, {method:"POST", body:body})
      .then(function(){
        document.querySelectorAll(".overlay").forEach(function(o){ o.classList.remove("show"); });
        toast(editId ? "手账修改已保存 🐾" : "手账已贴在小窝啦 🐾");
        diaryPhotosToUpload = [];
        load();
        if (activeDiaryDate) openDayDiaries(activeDiaryDate);
      })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  }
});

document.addEventListener("change", function(e){
  if (e.target && e.target.id === "df_mood"){
    var custInput = $("#df_mood_custom");
    if (custInput){
      if (e.target.value === "__custom__"){
        custInput.style.display = "block";
        custInput.focus();
      } else {
        custInput.style.display = "none";
      }
    }
  }
  if (e.target && e.target.id === "df_files"){
    var files = Array.from(e.target.files);
    var prev = $("#df_preview");
    files.forEach(function(file){
      if (!file.type.startsWith("image/")) return;
      var reader = new FileReader();
      reader.onload = function(evt){
        var img = new Image();
        img.onload = function(){
          // 手机端自动等比压缩为轻量长边 1200px 拍立得照片
          var canvas = document.createElement("canvas");
          var maxSide = 1200;
          var w = img.width, h = img.height;
          if (w > maxSide || h > maxSide){
            if (w > h){ h = Math.round(h * maxSide / w); w = maxSide; }
            else { w = Math.round(w * maxSide / h); h = maxSide; }
          }
          canvas.width = w;
          canvas.height = h;
          var ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0, w, h);
          var compB64 = canvas.toDataURL("image/jpeg", 0.82);
          diaryPhotosToUpload.push(compB64);

          var pWrap = document.createElement("div");
          pWrap.style.position = "relative";
          pWrap.style.display = "inline-block";

          var thumbEl = document.createElement("img");
          thumbEl.src = compB64;
          thumbEl.style.width = "48px";
          thumbEl.style.height = "48px";
          thumbEl.style.objectFit = "cover";
          thumbEl.style.borderRadius = "6px";
          thumbEl.style.border = "1px solid #ddd";

          var delX = document.createElement("span");
          delX.textContent = "×";
          delX.style.cssText = "position:absolute;top:-5px;right:-5px;background:#ef4444;color:#fff;border-radius:50%;width:16px;height:16px;line-height:16px;text-align:center;font-size:11px;cursor:pointer;font-weight:bold";
          delX.onclick = function(){
            var idx = diaryPhotosToUpload.indexOf(compB64);
            if (idx !== -1) diaryPhotosToUpload.splice(idx, 1);
            pWrap.remove();
          };

          pWrap.appendChild(thumbEl);
          pWrap.appendChild(delX);
          if (prev) prev.appendChild(pWrap);
        };
        img.src = evt.target.result;
      };
      reader.readAsDataURL(file);
    });
  }
});

document.addEventListener("keydown", function(e){
  if (e.target && e.target.id === "cmtText" && e.key === "Enter"){
    e.preventDefault();
    var btn = document.querySelector('[data-act="send-cmt"]');
    if (btn) btn.click();
  }
});

ANCHOR = todayISO();
api("/api/meta").then(function(j){ META = j; }).catch(function(){})
  .then(function(){ return api("/api/me").then(function(j){ ME = j; }); })
  .catch(function(){})
  .then(function(){ if (ME) { load(); } else { render(); } });
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "sched-share/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 基础工具 ----
    def send_bytes(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200, extra=None):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode(),
                        "application/json; charset=utf-8", extra)

    def send_html(self, text):
        self.send_bytes(200, text.encode(), "text/html; charset=utf-8")

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        # 上传手账多张拍立得图片时请求体积会达到几 MB，放宽限制到 30MB
        if n <= 0 or n > 31457280:
            return {}
        raw = self.rfile.read(n)
        try:
            try:
                return json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError:
                return json.loads(raw.decode("gbk"))
        except Exception:
            return {}

    def current_uid(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(COOKIE + "="):
                return parse_token(part[len(COOKIE) + 1:])
        return None

    def authed(self):
        uid = self.current_uid()
        if not uid:
            self.send_json({"error": "未登录"}, 401)
            return None
        return uid

    # ---- 路由 ----
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self.send_html(PAGE)
            if path.startswith("/img/"):
                base = path[5:].split(".")[0]
                fn = IMG_FILES.get(base)
                fp = os.path.join(APP_DIR, fn) if fn else None
                if fp and os.path.exists(fp):
                    with open(fp, "rb") as f:
                        return self.send_bytes(200, f.read(), "image/png",
                                               extra=[("Cache-Control", "public, max-age=86400")])
                return self.send_json({"error": "not found"}, 404)
            if path.startswith("/api/event/"):
                if self.authed() is None:
                    return
                try:
                    eid = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    return self.send_json({"error": "参数错误"}, 400)
                with LOCK:
                    ev = CONN.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
                    cmts = CONN.execute(
                        "SELECT c.id, c.uid, c.text, c.created, u.name FROM comments c "
                        "LEFT JOIN users u ON u.uid=c.uid WHERE c.event_id=? ORDER BY c.id",
                        (eid,)).fetchall()
                if ev is None:
                    return self.send_json({"error": "日程不存在"}, 404)
                users = get_users()
                return self.send_json({
                    "event": dict(ev),
                    "owner": {"uid": ev["uid"], "name": users[ev["uid"]]["name"]},
                    "comments": [dict(c) for c in cmts],
                })
            if path == "/api/health":
                return self.send_json({"ok": True, "time": time.strftime("%F %T")})
            if path == "/api/meta":
                users = get_users()
                return self.send_json({"users": [{"uid": u, "name": users[u]["name"]} for u in ("a", "b")]})
            if path == "/api/me":
                uid = self.current_uid()
                if not uid:
                    return self.send_json({"error": "未登录"}, 401)
                users = get_users()
                return self.send_json({"uid": uid, "name": users[uid]["name"]})
            if path.startswith("/photos/"):
                fn = os.path.basename(path[8:])
                fp = os.path.join(PHOTOS_DIR, fn)
                if fp and os.path.exists(fp):
                    mime = "image/jpeg"
                    if fn.endswith(".png"): mime = "image/png"
                    elif fn.endswith(".webp"): mime = "image/webp"
                    with open(fp, "rb") as f:
                        return self.send_bytes(200, f.read(), mime,
                                               extra=[("Cache-Control", "public, max-age=604800")])
                return self.send_json({"error": "not found"}, 404)
            if path == "/api/week":
                if self.authed() is None:
                    return
                qs = parse_qs(urlparse(self.path).query)
                d = (qs.get("date") or [""])[0]
                if not DATE_RE.match(d):
                    d = date.today().isoformat()
                try:
                    date.fromisoformat(d)
                except ValueError:
                    return self.send_json({"error": "日期格式无效"}, 400)
                return self.send_json(week_payload(d))
            if path == "/api/month":
                if self.authed() is None:
                    return
                qs = parse_qs(urlparse(self.path).query)
                ym = (qs.get("month") or [""])[0]
                if not re.match(r"^\d{4}-\d{2}$", ym):
                    ym = date.today().strftime("%Y-%m")
                return self.send_json(self.month_payload(ym))
            if path == "/api/diaries":
                if self.authed() is None:
                    return
                qs = parse_qs(urlparse(self.path).query)
                dt = (qs.get("date") or [""])[0]
                return self.send_json(self.get_diaries_by_date(dt))
            return self.send_json({"error": "not found"}, 404)
        except Exception:
            self.log_error("GET %s failed: %s", path, sys.exc_info()[1])
            return self.send_json({"error": "服务器内部错误"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        d = self.body_json()
        try:
            if path == "/api/login":
                ip = self.client_address[0]
                if not rate_ok(ip):
                    return self.send_json({"error": "尝试过多，请 10 分钟后再试"}, 429)
                uid = d.get("uid")
                pw = str(d.get("password") or "")
                users = get_users()
                if uid in users and check_pw(pw, users[uid]["pw_hash"]):
                    FAILS.pop(ip, None)
                    token = make_token(uid)
                    secure = (self.headers.get("X-Forwarded-Proto") == "https")
                    cookie = "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s" % (
                        COOKIE, token, SESSION_DAYS * 86400, "; Secure" if secure else "")
                    return self.send_json({"ok": True, "uid": uid, "name": users[uid]["name"]},
                                          extra=[("Set-Cookie", cookie)])
                rate_fail(ip)
                time.sleep(0.4)
                return self.send_json({"error": "身份或密码不正确"}, 401)

            if path == "/api/logout":
                cookie = "%s=deleted; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE
                return self.send_json({"ok": True}, extra=[("Set-Cookie", cookie)])

            if path == "/api/events":
                if self.authed() is None:
                    return
                return self.create_event(d)
            if path == "/api/events/delete":
                if self.authed() is None:
                    return
                eid = d.get("id")
                if not isinstance(eid, int):
                    return self.send_json({"error": "参数错误"}, 400)
                with LOCK:
                    CONN.execute("DELETE FROM events WHERE id=?", (eid,))
                    CONN.execute("DELETE FROM comments WHERE event_id=?", (eid,))
                return self.send_json({"ok": True})
            if path == "/api/comment":
                uid = self.authed()
                if uid is None:
                    return
                eid = d.get("event_id")
                text = str(d.get("text") or "").strip()[:500]
                if not isinstance(eid, int) or not text:
                    return self.send_json({"error": "参数错误"}, 400)
                created = time.strftime("%Y-%m-%d %H:%M")
                with LOCK:
                    ev_row = CONN.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
                    if ev_row is None:
                        return self.send_json({"error": "日程不存在"}, 404)
                    cur = CONN.execute("INSERT INTO comments(event_id,uid,text,created) VALUES(?,?,?,?)",
                                       (eid, uid, text, created))
                    cid = cur.lastrowid

                # 微信推送给对方狗狗
                users = get_users()
                other_uid = "b" if uid == "a" else "a"
                target_token = users[other_uid]["wx_uid"]
                if target_token:
                    sender_name = users[uid]["name"]
                    ev_title = ev_row["title"]
                    title = f"🐾 {sender_name} 给你的日程留了言！"
                    html_content = (
                        f"<p>🐶 <strong>{sender_name}</strong> 在日程 <strong>【{ev_title}】</strong> 下留言：</p>"
                        f"<blockquote style='background:#f7f7f7;padding:10px;border-left:4px solid #f6ad55;border-radius:4px;margin:10px 0;'>"
                        f"{text}</blockquote>"
                        f"<p style='color:#888;font-size:12px;'>时间：{created} · 来自线条小狗日程小窝 🐾</p>"
                    )
                    send_wechat_notice(target_token, title, html_content)

                return self.send_json({"ok": True, "comment": {
                    "id": cid, "uid": uid, "name": users[uid]["name"], "text": text, "created": created}})

            if path == "/api/comment/delete":
                uid = self.authed()
                if uid is None:
                    return
                cid = d.get("id")
                if not isinstance(cid, int):
                    return self.send_json({"error": "参数错误"}, 400)
                with LOCK:
                    cur = CONN.execute("DELETE FROM comments WHERE id=? AND uid=?", (cid, uid))
                if cur.rowcount == 0:
                    return self.send_json({"error": "只能删除自己的评论"}, 400)
                return self.send_json({"ok": True})

            if path == "/api/settings":
                if self.authed() is None:
                    return
                return self.save_settings(d)
            if path == "/api/password":
                if self.authed() is None:
                    return
                return self.change_password(d)
            if path == "/api/anniversaries":
                if self.authed() is None:
                    return
                return self.create_anniversary(d)
            if path == "/api/anniversaries/delete":
                if self.authed() is None:
                    return
                return self.delete_anniversary(d)
            if path == "/api/diaries":
                if self.authed() is None:
                    return
                return self.create_diary(d)
            if path == "/api/diaries/delete":
                if self.authed() is None:
                    return
                return self.delete_diary(d)
            if path == "/api/diaries/update":
                if self.authed() is None:
                    return
                return self.update_diary(d)
            return self.send_json({"error": "not found"}, 404)
        except Exception:
            self.log_error("POST %s failed: %s", path, sys.exc_info()[1])
            return self.send_json({"error": "服务器内部错误"}, 500)

    # ---- POST 子逻辑 ----
    def create_event(self, d):
        uid = d.get("uid")
        title = str(d.get("title") or "").strip()
        location = str(d.get("location") or "").strip()[:200]
        note = str(d.get("note") or "").strip()[:200]
        repeat = d.get("repeat")
        week_spec = str(d.get("week_spec") or "").strip()[:60]
        tstart, tend = str(d.get("tstart") or ""), str(d.get("tend") or "")

        if uid not in ("a", "b"):
            return self.send_json({"error": "归属用户无效"}, 400)
        if not title:
            return self.send_json({"error": "标题不能为空"}, 400)
        if not TIME_RE.match(tstart) or not TIME_RE.match(tend) or t2m(tstart) >= t2m(tend):
            return self.send_json({"error": "时间无效（需 开始 < 结束）"}, 400)
        if repeat == "weekly":
            try:
                weekday = int(d.get("weekday"))
            except (TypeError, ValueError):
                return self.send_json({"error": "缺少星期"}, 400)
            if not 0 <= weekday <= 6:
                return self.send_json({"error": "星期无效"}, 400)
            ev_date = ""
        elif repeat == "once":
            weekday = None
            ev_date = str(d.get("date") or "")
            if not DATE_RE.match(ev_date):
                return self.send_json({"error": "日期无效"}, 400)
            try:
                date.fromisoformat(ev_date)
            except ValueError:
                return self.send_json({"error": "日期无效"}, 400)
        else:
            return self.send_json({"error": "类型无效"}, 400)
        if week_spec and parse_week_spec(week_spec) is None:
            return self.send_json({"error": "周次格式无法识别（示例：2-17、2-16双、1-4,9-12）"}, 400)

        with LOCK:
            cur = CONN.execute(
                "INSERT INTO events(uid,title,location,note,repeat,weekday,date,tstart,tend,week_spec) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (uid, title, location, note, repeat, weekday, ev_date, tstart, tend, week_spec))
            eid = cur.lastrowid

        # 微信推送新日程提醒给对方狗狗
        users = get_users()
        other_uid = "b" if uid == "a" else "a"
        target_token = users[other_uid]["wx_uid"]
        if target_token:
            author_name = users[uid]["name"]
            wd_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            when_str = f"每周{wd_names[weekday]} {tstart}~{tend}" if repeat == "weekly" else f"{ev_date} {tstart}~{tend}"
            loc_str = f" · 📍 {location}" if location else ""
            note_str = f"<br>📝 备注：{note}" if note else ""
            msg_title = f"🐾 {author_name} 添加了新日程"
            html_content = (
                f"🐶 <strong>{author_name}</strong> 记下了新日程：<br>"
                f"📌 <strong>【{title}】</strong>{loc_str}<br>"
                f"⏰ 时间：{when_str}{note_str}"
            )
            send_wechat_notice(target_token, msg_title, html_content)

        return self.send_json({"ok": True, "id": eid})

    def save_settings(self, d):
        users = get_users()

        def w1(v):
            v = str(v or "")
            if v and DATE_RE.match(v):
                try:
                    date.fromisoformat(v)
                    return v
                except ValueError:
                    pass
            return ""
        ws, we = str(d.get("window_start") or ""), str(d.get("window_end") or "")
        if not TIME_RE.match(ws) or not TIME_RE.match(we) or t2m(ws) >= t2m(we):
            return self.send_json({"error": "统计窗口无效"}, 400)
        try:
            mg = int(d.get("min_gap"))
            if not 5 <= mg <= 180:
                raise ValueError
        except (TypeError, ValueError):
            return self.send_json({"error": "最短空闲分钟数无效"}, 400)
        na = str(d.get("name_a") or "").strip()[:30] or users["a"]["name"]
        nb = str(d.get("name_b") or "").strip()[:30] or users["b"]["name"]
        wx_a = str(d.get("wx_a") or "").strip()[:100]
        wx_b = str(d.get("wx_b") or "").strip()[:100]
        with LOCK:
            CONN.execute("UPDATE users SET name=?, week1=?, wx_uid=? WHERE uid='a'", (na, w1(d.get("week1_a")), wx_a))
            CONN.execute("UPDATE users SET name=?, week1=?, wx_uid=? WHERE uid='b'", (nb, w1(d.get("week1_b")), wx_b))
            for k, v in (("window_start", ws), ("window_end", we), ("min_gap", str(mg))):
                CONN.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        return self.send_json({"ok": True})

    def change_password(self, d):
        uid = self.current_uid()
        old, new = str(d.get("old") or ""), str(d.get("new") or "")
        if len(new) < 6:
            return self.send_json({"error": "新密码至少 6 位"}, 400)
        users = get_users()
        if not check_pw(old, users[uid]["pw_hash"]):
            return self.send_json({"error": "旧密码不正确"}, 400)
        with LOCK:
            CONN.execute("UPDATE users SET pw_hash=? WHERE uid=?", (hash_pw(new), uid))
        return self.send_json({"ok": True})

    def create_anniversary(self, d):
        title = str(d.get("title") or "").strip()[:60]
        dt = str(d.get("date") or "").strip()
        target_type = str(d.get("target_type") or "love").strip()
        if not title:
            return self.send_json({"error": "纪念日名称不能为空"}, 400)
        if not DATE_RE.match(dt):
            return self.send_json({"error": "日期格式无效"}, 400)
        try:
            date.fromisoformat(dt)
        except ValueError:
            return self.send_json({"error": "日期无效"}, 400)
        if target_type not in ("love", "birthday", "countdown"):
            target_type = "love"
        with LOCK:
            CONN.execute("INSERT INTO anniversaries(title, date, target_type) VALUES(?,?,?)",
                         (title, dt, target_type))
        return self.send_json({"ok": True})

    def delete_anniversary(self, d):
        try:
            aid = int(d.get("id"))
        except (TypeError, ValueError):
            return self.send_json({"error": "参数错误"}, 400)
        with LOCK:
            CONN.execute("DELETE FROM anniversaries WHERE id=?", (aid,))
        return self.send_json({"ok": True})

    def month_payload(self, ym):
        # ym: YYYY-MM
        users = get_users()
        with LOCK:
            # 找到当月的 diaries
            d_rows = CONN.execute(
                "SELECT d.*, "
                "(SELECT p.file_name FROM diary_photos p WHERE p.diary_id=d.id ORDER BY p.sort_order, p.id LIMIT 1) as cover_photo, "
                "(SELECT COUNT(*) FROM diary_photos p WHERE p.diary_id=d.id) as photo_count "
                "FROM diaries d WHERE d.date LIKE ? ORDER BY d.date ASC, d.id ASC",
                (f"{ym}-%",)
            ).fetchall()
            diaries = [dict(r) for r in d_rows]
            annivs = [dict(r) for r in CONN.execute("SELECT * FROM anniversaries").fetchall()]

        # 按天分组
        by_date = {}
        for item in diaries:
            by_date.setdefault(item["date"], []).append(item)

        return {
            "month": ym,
            "diaries_by_date": by_date,
            "total_count": len(diaries),
            "anniversaries": annivs,
            "users": {u: {"name": users[u]["name"]} for u in ("a", "b")}
        }

    def get_diaries_by_date(self, dt):
        if not dt or not DATE_RE.match(dt):
            return {"diaries": []}
        users = get_users()
        with LOCK:
            rows = CONN.execute("SELECT * FROM diaries WHERE date=? ORDER BY id ASC", (dt,)).fetchall()
            res = []
            for r in rows:
                item = dict(r)
                photos = CONN.execute("SELECT * FROM diary_photos WHERE diary_id=? ORDER BY sort_order, id", (item["id"],)).fetchall()
                item["photos"] = [dict(p) for p in photos]
                item["author_name"] = users.get(item["author_uid"], {}).get("name", "小狗")
                res.append(item)
        return {"date": dt, "diaries": res}

    def create_diary(self, d):
        uid = self.current_uid()
        title = str(d.get("title") or "").strip()[:80]
        dt = str(d.get("date") or "").strip()
        location = str(d.get("location") or "").strip()[:100]
        mood = str(d.get("mood") or "").strip()[:30]
        weather = str(d.get("weather") or "").strip()[:30]
        content = str(d.get("content") or "").strip()[:4000]
        photos_base64 = d.get("photos") or []  # list of base64 strings

        if not title:
            return self.send_json({"error": "游玩主题/标题不能为空"}, 400)
        if not dt or not DATE_RE.match(dt):
            return self.send_json({"error": "日期无效"}, 400)

        os.makedirs(PHOTOS_DIR, exist_ok=True)
        saved_photos = []

        import base64
        for idx, p_b64 in enumerate(photos_base64[:9]):  # 最多支持9张
            try:
                if "," in p_b64:
                    p_b64 = p_b64.split(",", 1)[1]
                data = base64.b64decode(p_b64)
                fname = f"pic_{dt}_{int(time.time())}_{secrets.token_hex(4)}.jpg"
                fpath = os.path.join(PHOTOS_DIR, fname)
                with open(fpath, "wb") as pf:
                    pf.write(data)
                saved_photos.append(fname)
            except Exception as e:
                self.log_error("保存图片失败: %s", e)

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOCK:
            cur = CONN.execute(
                "INSERT INTO diaries(date, title, location, mood, weather, content, author_uid, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (dt, title, location, mood, weather, content, uid, now_str)
            )
            diary_id = cur.lastrowid
            for s_idx, fname in enumerate(saved_photos):
                CONN.execute(
                    "INSERT INTO diary_photos(diary_id, file_name, sort_order) VALUES(?,?,?)",
                    (diary_id, fname, s_idx)
                )

        # 微信推送新手账通知给对方狗狗
        users = get_users()
        other_uid = "b" if uid == "a" else "a"
        target_token = users[other_uid]["wx_uid"]
        if target_token:
            sender_name = users[uid]["name"]
            title = f"📔 {sender_name} 更新了一篇足迹手账！"
            loc_str = f" · 📍 {location}" if location else ""
            mood_str = f" [{mood}]" if mood else ""
            html_content = (
                f"<p>🐶 <strong>{sender_name}</strong> 记下了 <strong>【{title}{mood_str}】</strong>{loc_str}：</p>"
                f"<blockquote style='background:#f7f7f7;padding:10px;border-left:4px solid #38bdf8;border-radius:4px;margin:10px 0;'>"
                f"{content or '拍下了美好瞬间~ 📷'}</blockquote>"
                f"<p style='color:#888;font-size:12px;'>游玩日期：{dt} · 来自线条小狗日程小窝 🐾</p>"
            )
            send_wechat_notice(target_token, title, html_content)

        return self.send_json({"ok": True, "id": diary_id})

    def update_diary(self, d):
        try:
            did = int(d.get("id"))
        except (TypeError, ValueError):
            return self.send_json({"error": "参数错误"}, 400)
        title = str(d.get("title") or "").strip()[:80]
        dt = str(d.get("date") or "").strip()
        location = str(d.get("location") or "").strip()[:100]
        mood = str(d.get("mood") or "").strip()[:30]
        content = str(d.get("content") or "").strip()[:4000]
        photos_base64 = d.get("photos") or []  # 新增的照片
        keep_photos = d.get("keep_photos") or []  # 保留的原有照片文件名

        if not title:
            return self.send_json({"error": "主题不能为空"}, 400)
        if not dt or not DATE_RE.match(dt):
            return self.send_json({"error": "日期无效"}, 400)

        os.makedirs(PHOTOS_DIR, exist_ok=True)
        import base64
        new_saved_photos = []
        for idx, p_b64 in enumerate(photos_base64[:9]):
            try:
                if "," in p_b64:
                    p_b64 = p_b64.split(",", 1)[1]
                data = base64.b64decode(p_b64)
                fname = f"pic_{dt}_{int(time.time())}_{secrets.token_hex(4)}.jpg"
                fpath = os.path.join(PHOTOS_DIR, fname)
                with open(fpath, "wb") as pf:
                    pf.write(data)
                new_saved_photos.append(fname)
            except Exception as e:
                self.log_error("保存图片失败: %s", e)

        with LOCK:
            CONN.execute(
                "UPDATE diaries SET date=?, title=?, location=?, mood=?, content=? WHERE id=?",
                (dt, title, location, mood, content, did)
            )
            # 处理删除旧照片
            old_photos = CONN.execute("SELECT file_name FROM diary_photos WHERE diary_id=?", (did,)).fetchall()
            for op in old_photos:
                fn = op["file_name"]
                if fn not in keep_photos:
                    try:
                        fp = os.path.join(PHOTOS_DIR, fn)
                        if os.path.exists(fp):
                            os.remove(fp)
                    except Exception:
                        pass
            CONN.execute("DELETE FROM diary_photos WHERE diary_id=?", (did,))
            # 重新插入保留的旧照片和新增的照片
            all_current = [p for p in keep_photos if any(op["file_name"] == p for op in old_photos)] + new_saved_photos
            for s_idx, fname in enumerate(all_current):
                CONN.execute(
                    "INSERT INTO diary_photos(diary_id, file_name, sort_order) VALUES(?,?,?)",
                    (did, fname, s_idx)
                )

        return self.send_json({"ok": True, "id": did})

    def delete_diary(self, d):
        try:
            did = int(d.get("id"))
        except (TypeError, ValueError):
            return self.send_json({"error": "参数错误"}, 400)
        with LOCK:
            photos = CONN.execute("SELECT file_name FROM diary_photos WHERE diary_id=?", (did,)).fetchall()
            for p in photos:
                try:
                    fp = os.path.join(PHOTOS_DIR, p["file_name"])
                    if os.path.exists(fp):
                        os.remove(fp)
                except Exception:
                    pass
            CONN.execute("DELETE FROM diary_photos WHERE diary_id=?", (did,))
            CONN.execute("DELETE FROM diaries WHERE id=?", (did,))
        return self.send_json({"ok": True})


    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s %s\n" % (time.strftime("%F %T"), self.client_address[0], fmt % args))


def main():
    global SECRET
    SECRET = load_secret()
    init_db()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("sched-share listening on 0.0.0.0:%d, data dir %s" % (PORT, DATA_DIR), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
