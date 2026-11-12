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
from datetime import date, timedelta, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import urllib.request
import contextlib
import shutil
import cycle
import settings_service

# 纪念日/节日引擎（special_days.py，移植自 our-little-planet 并适配）。
# 导入失败只降级（无提醒功能），绝不允许拖垮整个服务。
try:
    import special_days
except Exception as _e:  # pragma: no cover
    special_days = None
    sys.stderr.write("[warn] special_days 引擎未加载，纪念日提醒关闭: %s\n" % _e)

PORT = int(os.environ.get("PORT", "8795"))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
PHOTOS_TRASH_DIR = os.path.join(DATA_DIR, "photos_trash")
DB_PATH = os.path.join(DATA_DIR, "sched.db")
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")
COOKIE = "sched_sess"
SESSION_DAYS = 30

TIME_RE = re.compile(r"^\d{2}:\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TLOCAL = threading.local()
LOCK = threading.RLock()
SECRET = None
FAILS = {}  # 登录失败限流：ip -> [次数, 首次时间戳]
APP_DIR = os.path.dirname(os.path.abspath(__file__))

def get_connection():
    """获取当前线程独立的 SQLite 连接，充分利用 WAL 模式多读并发优势"""
    conn = getattr(_TLOCAL, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        conn.execute("PRAGMA synchronous=NORMAL")
        _TLOCAL.conn = conn
    return conn

class DBProxy:
    """线程透明的数据库连接代理，保证各线程使用独立连接"""
    def __getattr__(self, name):
        return getattr(get_connection(), name)

CONN = DBProxy()

@contextlib.contextmanager
def db_transaction():
    """写事务上下文管理器：在排他写入锁保护下提供 BEGIN IMMEDIATE / COMMIT / ROLLBACK 原子保障"""
    with LOCK:
        conn = get_connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
IMG_FILES = {"dogs": "dogs.png", "dogheads": "dogheads.png", "dogrest": "dogrest.png",
             "dog-hero": "dog-hero.png", "dog-add": "dog-add.png", "dog-set": "dog-set.png",
             "dog-wave": "dog-wave.png", "dog-walk1": "dog-walk1.png", "dog-walk2": "dog-walk2.png",
             "dog-walk3": "dog-walk3.png", "dog-walk4": "dog-walk4.png", "dog-couple": "dog-couple.png"}

_IMG_CACHE = {}  # {base: (bytes, etag)}

def get_image_asset(base):
    """内存级图片缓存，避免频繁磁盘 I/O 并计算 ETag 支持 304 极速重用"""
    if base in _IMG_CACHE:
        return _IMG_CACHE[base]
    fn = IMG_FILES.get(base)
    if not fn:
        return None, None
    fp = os.path.join(APP_DIR, fn)
    if os.path.exists(fp):
        with open(fp, "rb") as f:
            data = f.read()
        etag = f'"{hashlib.md5(data).hexdigest()[:16]}"'
        _IMG_CACHE[base] = (data, etag)
        return data, etag
    return None, None

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
  created  TEXT NOT NULL,
  date     TEXT DEFAULT '');
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
  live_video  TEXT DEFAULT '',
  sort_order  INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS summary_cache(
  cache_key   TEXT PRIMARY KEY,
  summary_json TEXT NOT NULL,
  updated_at  TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  target_uid    TEXT NOT NULL,
  title         TEXT NOT NULL,
  content       TEXT NOT NULL,
  target_action TEXT DEFAULT '',
  is_read       INTEGER DEFAULT 0,
  is_pushed     INTEGER DEFAULT 0,
  created_at    TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wishes(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  creator_uid  TEXT NOT NULL,
  title        TEXT NOT NULL,
  category     TEXT DEFAULT 'life',
  priority     INTEGER DEFAULT 0,
  note         TEXT DEFAULT '',
  is_done      INTEGER DEFAULT 0,
  done_at      TEXT DEFAULT '',
  created_at   TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trips(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  title        TEXT NOT NULL,
  destination  TEXT DEFAULT '',
  start_date   TEXT DEFAULT '',
  end_date     TEXT DEFAULT '',
  created_by   TEXT NOT NULL,
  created_at   TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trip_items(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id      INTEGER NOT NULL,
  category     TEXT NOT NULL DEFAULT 'other',
  name         TEXT NOT NULL,
  qty          TEXT DEFAULT '',
  note         TEXT DEFAULT '',
  is_packed    INTEGER NOT NULL DEFAULT 0,
  sort_order   INTEGER DEFAULT 0,
  updated_at   TEXT NOT NULL);
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


def call_llm_api(system_prompt, user_prompt):
    api_url = (get_setting("llm_api_base") or "").strip()
    api_key = (get_setting("llm_api_key") or "").strip()
    model_name = (get_setting("llm_model") or "deepseek-chat").strip()
    if not api_url or not api_key:
        return None

    if not api_url.endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 1600
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "sched-share-doggo/1.0"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            choices = res_json.get("choices") or []
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        sys.stderr.write(f"[LLM API Error]: {e}\n")
    return None


# ---------------------------------------------------------------- 旅行清单

TRIP_CATEGORIES = [
    ("doc", "证件"), ("toilet", "洗漱"), ("skin", "护肤化妆"),
    ("cloth", "衣物"), ("tech", "电子"), ("med", "药品"), ("other", "其他"),
]
TRIP_CATEGORY_KEYS = [k for k, _ in TRIP_CATEGORIES]

# 新建旅行时可选套用的常用清单（不含备注，备注留给人自己写）
TRIP_TEMPLATE = [
    ("doc", "身份证"), ("doc", "学生证"), ("doc", "银行卡"), ("doc", "现金"),
    ("toilet", "牙刷牙膏"), ("toilet", "洗发水/护发素"), ("toilet", "沐浴露"),
    ("toilet", "一次性浴巾"), ("toilet", "洗脸巾"),
    ("skin", "防晒"), ("skin", "水乳"), ("skin", "粉底液"), ("skin", "口红"), ("skin", "面膜"),
    ("cloth", "换洗衣物"), ("cloth", "睡衣"), ("cloth", "外套"), ("cloth", "袜子"),
    ("tech", "充电器"), ("tech", "充电宝"), ("tech", "耳机"), ("tech", "自拍杆"),
    ("med", "创可贴"), ("med", "常用药"),
    ("other", "纸巾"), ("other", "雨伞"), ("other", "卫生巾"),
]


def _trip_status(start, end, today):
    if end and end < today:
        return "done"
    if start and start > today:
        return "upcoming"
    if not start and not end:
        return "upcoming"
    return "ongoing"


def _trip_item_order(trip_id, category):
    row = CONN.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM trip_items WHERE trip_id=? AND category=?",
        (trip_id, category)).fetchone()
    return row["n"] if row else 1


def _insert_trip_item(trip_id, category, name, qty="1", note=""):
    if category not in TRIP_CATEGORY_KEYS:
        category = "other"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    CONN.execute(
        "INSERT INTO trip_items(trip_id, category, name, qty, note, is_packed, sort_order, updated_at) "
        "VALUES(?,?,?,?,?,0,?,?)",
        (trip_id, category, name, qty, note, _trip_item_order(trip_id, category), now))


def trips_overview():
    today = date.today().isoformat()
    users = get_users()
    with LOCK:
        rows = CONN.execute(
            "SELECT t.*, "
            " (SELECT COUNT(*) FROM trip_items i WHERE i.trip_id=t.id) AS total, "
            " (SELECT COUNT(*) FROM trip_items i WHERE i.trip_id=t.id AND i.is_packed=1) AS packed "
            "FROM trips t ORDER BY t.id DESC").fetchall()
    trips = []
    for r in rows:
        item = dict(r)
        item["status"] = _trip_status(item["start_date"], item["end_date"], today)
        item["creator_name"] = users.get(item["created_by"], {}).get("name", "")
        trips.append(item)
    ongoing = [t for t in trips if t["status"] == "ongoing"]
    upcoming = sorted([t for t in trips if t["status"] == "upcoming"],
                      key=lambda t: t["start_date"] or "9999-99-99")
    done = sorted([t for t in trips if t["status"] == "done"],
                  key=lambda t: t["start_date"] or "", reverse=True)
    return ongoing + upcoming + done


def trip_detail(trip_id):
    today = date.today().isoformat()
    users = get_users()
    with LOCK:
        row = CONN.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not row:
            return None
        items = [dict(r) for r in CONN.execute(
            "SELECT * FROM trip_items WHERE trip_id=? ORDER BY category, sort_order, id",
            (trip_id,)).fetchall()]
    trip = dict(row)
    trip["status"] = _trip_status(trip["start_date"], trip["end_date"], today)
    trip["creator_name"] = users.get(trip["created_by"], {}).get("name", "")
    items.sort(key=lambda i: TRIP_CATEGORY_KEYS.index(i["category"])
               if i["category"] in TRIP_CATEGORY_KEYS else 99)
    return {
        "trip": trip,
        "items": items,
        "stats": {"total": len(items), "packed": sum(1 for i in items if i["is_packed"] == 1)},
    }


# ---------------------------------------------------------------- 时光简报（仅月度）

def _month_bounds(ym):
    """'2026-10' -> (2026-10-01, 2026-10-31)"""
    y, m = int(ym[:4]), int(ym[5:7])
    first = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first, nxt - timedelta(days=1)


def _shift_month(ym, delta):
    y, m = int(ym[:4]), int(ym[5:7])
    idx = y * 12 + (m - 1) + delta
    return "%04d-%02d" % (idx // 12, idx % 12 + 1)


def valid_month(ym):
    return bool(re.match(r"^\d{4}-(0[1-9]|1[0-2])$", ym or ""))


def default_summary_month(now=None):
    """月底当天看当月，其余时间看最近一个已经过完的月份。"""
    today = now or date.today()
    month_end = _month_bounds(today.strftime("%Y-%m"))[1]
    anchor = today if today == month_end else today.replace(day=1) - timedelta(days=1)
    return anchor.strftime("%Y-%m")


def _clip(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def month_facts(ym):
    """把这个月的真实材料整理成一份朴素的事实清单，交给模型时不做任何文学加工。"""
    first, last = _month_bounds(ym)
    first_iso, last_iso = first.isoformat(), last.isoformat()
    users = get_users()
    names = {"a": users["a"]["name"], "b": users["b"]["name"]}

    days, seen, cursor = [], set(), first
    while cursor <= last:
        for day in week_payload(cursor.isoformat())["days"]:
            d_iso = day["date"]
            if d_iso in seen or not (first_iso <= d_iso <= last_iso):
                continue
            seen.add(d_iso)
            days.append(day)
        cursor += timedelta(days=7)
    days.sort(key=lambda d: d["date"])

    free_minutes, free_days, conflicts = 0, 0, 0
    longest_free = {"minutes": 0, "date": "", "start": "", "end": ""}
    event_counts = {"a": 0, "b": 0}
    title_counts = {"a": {}, "b": {}}
    busiest_day, busiest_total = "", 0
    for day in days:
        mins = sum(e - s for s, e in day["free"])
        free_minutes += mins
        if mins >= 60:
            free_days += 1
        for s, e in day["free"]:
            if e - s > longest_free["minutes"]:
                longest_free = {"minutes": e - s, "date": day["date"], "start": m2t(s), "end": m2t(e)}
        conflicts += len(day["conflicts"])
        day_total = 0
        for uid in ("a", "b"):
            evs = day["users"][uid]["events"]
            event_counts[uid] += len(evs)
            day_total += len(evs)
            for ev in evs:
                title_counts[uid][ev["title"]] = title_counts[uid].get(ev["title"], 0) + 1
        if day_total > busiest_total:
            busiest_day, busiest_total = day["date"], day_total

    with LOCK:
        diaries = [dict(r) for r in CONN.execute(
            "SELECT * FROM diaries WHERE date >= ? AND date <= ? ORDER BY date, id",
            (first_iso, last_iso)).fetchall()]
        photo_row = CONN.execute(
            "SELECT COUNT(*) c FROM diary_photos dp JOIN diaries d ON d.id=dp.diary_id "
            "WHERE d.date >= ? AND d.date <= ?", (first_iso, last_iso)).fetchone()
        comments = [dict(r) for r in CONN.execute(
            "SELECT c.text, c.created, u.name FROM comments c LEFT JOIN users u ON u.uid=c.uid "
            "WHERE c.created >= ? AND c.created <= ? ORDER BY c.id",
            (first_iso + " 00:00", last_iso + " 23:59")).fetchall()]
        try:
            wishes = [r["title"] for r in CONN.execute(
                "SELECT title FROM wishes WHERE is_done=1 AND done_at LIKE ? ORDER BY done_at",
                (ym + "%",)).fetchall()]
        except Exception:
            wishes = []

    top_titles = {}
    for uid in ("a", "b"):
        top_titles[uid] = sorted(title_counts[uid].items(), key=lambda kv: (-kv[1], kv[0]))[:4]

    # 空档几乎占满 08:00-22:00 观察窗时，说明那天两个人都没安排，别写成"8 点到 22 点"
    try:
        ws, we = t2m(get_setting("window_start", "08:00")), t2m(get_setting("window_end", "22:00"))
        longest_free["full_day"] = longest_free["minutes"] >= max(0, (we - ws) - 60)
    except Exception:
        longest_free["full_day"] = False

    anniversaries = []
    try:
        for item in get_anniversaries():
            if (item.get("date") or "")[5:7] == ym[5:7]:
                anniversaries.append("%s（%s）" % (item.get("title", ""), item.get("date", "")))
    except Exception:
        pass

    for d in diaries:
        d["author_name"] = names.get(d.get("author_uid"), "")

    return {
        "ym": ym,
        "first": first_iso,
        "last": last_iso,
        "month_label": "%d年%d月" % (first.year, first.month),
        "names": names,
        "days_count": len(days),
        "free_hours": round(free_minutes / 60, 1),
        "free_days": free_days,
        "longest_free": longest_free,
        "conflicts": conflicts,
        "event_counts": event_counts,
        "top_titles": top_titles,
        "busiest_day": busiest_day,
        "busiest_total": busiest_total,
        "diaries": diaries,
        "n_photos": photo_row["c"] if photo_row else 0,
        "comments": comments,
        "wishes": wishes,
        "anniversaries": anniversaries,
    }


SUMMARY_SYSTEM_PROMPT = (
    "你在帮一对情侣整理他们这个月的共同记录。材料全部来自他们自己填的日程表、手账和留言，不是你的想象。\n"
    "任务：写一篇这个月的回顾，读起来像有人替他们把本子翻了一遍、随手记下来的，而不是 AI、客服或者情感博主的文案。\n"
    "用第三人称写，称呼他们的名字，不要用「我」冒充其中一方说话。\n"
    "\n"
    "写法：\n"
    "- 只用材料里出现过的东西。可以顺着材料想象当时的情景，但不要编造活动、地点、天气、对话或数字；拿不准就略过。\n"
    "- 只写做过的和说过的；材料里没写的心理活动、谁在乎谁，都不要替他们补。\n"
    "- 用具体的名词和动作，少用形容词。写「周三晚上九点还在实验楼」比写「你总是那么努力」好。\n"
    "- 挑 2 到 3 个具体的点展开：某篇手账、某条留言、某个反复出现的日程、两个人的空档怎么错开的；其余一笔带过。\n"
    "- 最多写三件事，每件一两句。不要把材料逐条过一遍，也不要写「先翻日程」「其余手账翻过去」这类翻页旁白。\n"
    "- 数字最多留两三个，其余删掉；不要报流水账式的次数统计。\n"
    "- 不要比较谁更忙、谁排第一；材料里的「出现最多」只作背景，不要改写成结论。\n"
    "- 可以有温度，但不要煽情、不要升华、不要给建议、不要替他们安排下个月。\n"
    "- 称呼直接用名字，不用「宝贝们」「亲爱的」这类泛称。\n"
    "\n"
    "不要出现：时光荏苒、岁月、点点滴滴、闪闪发光、小确幸、双向奔赴、未来可期、继续加油、愿你们、"
    "陪伴是最长情的告白；也不要「总的来说」「值得一提的是」「这份记录」「数据表明」。\n"
    "不要排比堆叠，不要「不是……而是……」，不要每段都放表情符号，不要用祝福或金句收尾。\n"
    "\n"
    "格式：3 段左右，段间空一行，300~450 字。不要标题、列表、加粗，表情符号最多两个。"
)


def build_month_user_prompt(facts):
    a, b = facts["names"]["a"], facts["names"]["b"]
    lines = ["【%s 的原始记录】" % facts["month_label"], "", "两个人：%s、%s" % (a, b), "", "日程"]
    lines.append("- 两人课表里同时空出来的时间合计 %s 小时，分布在 %d 天。" %
                 (facts["free_hours"], facts["free_days"]))
    lf = facts["longest_free"]
    if lf["minutes"] >= 60:
        if lf.get("full_day"):
            lines.append("- 最长的一段是 %s，那天两个人整天都没有安排。" % lf["date"][5:])
        else:
            lines.append("- 其中最长的一段是 %s %s–%s，%s 小时。" %
                         (lf["date"][5:], lf["start"], lf["end"], round(lf["minutes"] / 60, 1)))
    for uid, name in (("a", a), ("b", b)):
        tops = "、".join("%s×%d" % (t, c) for t, c in facts["top_titles"][uid]) or "无"
        lines.append("- %s：共 %d 次日程，出现最多的是 %s。" % (name, facts["event_counts"][uid], tops))
    if facts["busiest_day"] and facts["busiest_total"] >= 4:
        lines.append("- 最忙的一天是 %s，两个人加起来 %d 件事。" %
                     (facts["busiest_day"], facts["busiest_total"]))
    if facts["conflicts"]:
        lines.append("- 有 %d 次两人的日程时间撞在一起。" % facts["conflicts"])

    lines += ["", "手账（%d 篇，照片 %d 张）" % (len(facts["diaries"]), facts["n_photos"])]
    if facts["diaries"]:
        for d in facts["diaries"][:12]:
            bits = ["%s 《%s》" % (d["date"][5:], d["title"])]
            if d.get("location"):
                bits.append("地点：" + d["location"])
            if d.get("mood"):
                bits.append("心情：" + d["mood"])
            if d.get("content"):
                bits.append("正文：" + _clip(d["content"], 50))
            lines.append("- " + "｜".join(bits))
        if len(facts["diaries"]) > 12:
            lines.append("- 还有 %d 篇没列出来。" % (len(facts["diaries"]) - 12))
    else:
        lines.append("- 这个月没有写手账。")

    lines += ["", "留言（%d 条）" % len(facts["comments"])]
    if facts["comments"]:
        for c in facts["comments"][:8]:
            lines.append("- %s：%s" % (c.get("name") or "对方", _clip(c.get("text"), 60)))
    else:
        lines.append("- 这个月没有留言。")

    lines += ["", "其他"]
    lines.append("- 完成的心愿：%s" % ("、".join(facts["wishes"]) if facts["wishes"] else "无"))
    lines.append("- 这个月的纪念日：%s" % ("、".join(facts["anniversaries"]) if facts["anniversaries"] else "无"))
    lines += ["", "请按前面的要求写这个月的回顾。只写上面出现过的内容。"]
    return "\n".join(lines)


def build_month_fallback(facts):
    """没有配置模型时的兜底文案：只陈述材料，不硬写好听话术。"""
    a, b = facts["names"]["a"], facts["names"]["b"]
    paras = []
    head = "%s过完了。" % facts["month_label"]
    if facts["diaries"]:
        titles = "、".join("《%s》" % d["title"] for d in facts["diaries"][:4])
        head += "记了 %d 篇手账，%s%s。" % (len(facts["diaries"]), titles,
                                       "等" if len(facts["diaries"]) > 4 else "")
    else:
        head += "这个月没有新手账。"
    paras.append(head)

    agenda = "%s的日程有 %d 次，%s有 %d 次。" % (
        a, facts["event_counts"]["a"], b, facts["event_counts"]["b"])
    if facts["free_hours"] > 0:
        agenda += "课表上两个人同时空着的时间一共 %s 小时，分成 %d 天。" % (
            facts["free_hours"], facts["free_days"])
        lf = facts["longest_free"]
        if lf["minutes"] >= 120:
            if lf.get("full_day"):
                agenda += "%s 那天两个人都没安排。" % lf["date"][5:]
            else:
                agenda += "最长的一段在 %s，%s 到 %s。" % (lf["date"][5:], lf["start"], lf["end"])
    else:
        agenda += "这个月两个人的日程几乎没有错开，没有整块的共同空档。"
    if facts["busiest_day"] and facts["busiest_total"] >= 4:
        agenda += "最挤的是 %s，两个人那天加起来 %d 件事。" % (
            facts["busiest_day"][5:], facts["busiest_total"])
    paras.append(agenda)

    tail = []
    if facts["n_photos"]:
        tail.append("留下 %d 张照片" % facts["n_photos"])
    if facts["comments"]:
        tail.append("互相留了 %d 条留言" % len(facts["comments"]))
    if facts["wishes"]:
        tail.append("完成心愿：" + "、".join(facts["wishes"][:3]))
    paras.append(("，".join(tail) + "。") if tail else "这个月没有留下照片和留言。")
    return "\n\n".join(paras)


def _month_nav(ym):
    current = date.today().strftime("%Y-%m")
    with LOCK:
        diary_row = CONN.execute("SELECT MIN(date) AS m FROM diaries").fetchone()
        comment_row = CONN.execute("SELECT MIN(created) AS m FROM comments").fetchone()
    starts = [r["m"][:7] for r in (diary_row, comment_row) if r and r["m"]]
    floor = _shift_month(current, -24)
    earliest = min(starts) if starts else floor
    if earliest < floor:
        earliest = floor
    prev_ym, next_ym = _shift_month(ym, -1), _shift_month(ym, 1)
    return {
        "month": ym,
        "month_label": "%d年%d月" % (int(ym[:4]), int(ym[5:7])),
        "is_current": ym == current,
        "has_prev": prev_ym >= earliest and prev_ym < current,
        "has_next": next_ym <= current,
    }


def generate_month_summary(ym=None, force_refresh=False):
    ym = ym or default_summary_month()
    cache_key = "month_%s" % ym

    res = None
    if not force_refresh:
        with LOCK:
            cached = CONN.execute("SELECT summary_json FROM summary_cache WHERE cache_key=?", (cache_key,)).fetchone()
        if cached:
            try:
                res = json.loads(cached["summary_json"])
            except Exception:
                res = None

    if res is None:
        facts = month_facts(ym)
        stats = [
            {"k": "共同空闲", "v": "%sh" % facts["free_hours"]},
            {"k": "两人日程", "v": "%d项" % (facts["event_counts"]["a"] + facts["event_counts"]["b"])},
            {"k": "手账 / 照片", "v": "%d篇 / %d张" % (len(facts["diaries"]), facts["n_photos"])},
            {"k": "留言", "v": "%d条" % len(facts["comments"])},
        ]
        ai_content = call_llm_api(SUMMARY_SYSTEM_PROMPT, build_month_user_prompt(facts))
        res = {
            "title": "月度胶囊 · %s" % facts["month_label"],
            "content": ai_content or build_month_fallback(facts),
            "stats": stats,
            "is_ai": bool(ai_content),
        }
        try:
            with LOCK:
                CONN.execute(
                    "INSERT INTO summary_cache(cache_key, summary_json, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(cache_key) DO UPDATE SET summary_json=excluded.summary_json, updated_at=excluded.updated_at",
                    (cache_key, json.dumps(res, ensure_ascii=False),
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
        except Exception as e:
            sys.stderr.write("[Cache save error]: %s\n" % e)

    out = dict(res)
    out.update(_month_nav(ym))
    return out


def push_month_summary(ym=None):
    summary = generate_month_summary(ym)
    title = summary["title"]
    content = summary["content"] + "\n\n【这个月的数字】\n" + "\n".join(
        ["· %s：%s" % (s["k"], s["v"]) for s in summary["stats"]])
    users = get_users()
    sent_count = 0
    for uid in ("a", "b"):
        push_system_notice(uid, title, content, "view:summary")
        token = users[uid]["wx_uid"]
        if token:
            send_wechat_notice(token, title, content)
            sent_count += 1
    return summary, sent_count


NTFY_TOPIC_PREFIX = "couple_schedule_uni_"
NTFY_BASE_URL = os.environ.get("NTFY_BASE_URL", "https://push.unipeacher.email").rstrip("/")
# ntfy 实时弹窗开关：设为 False 则完全跳过 ntfy 推送（App 内消息中心与微信简报不受影响）
NTFY_PUSH_ENABLED = os.environ.get("NTFY_PUSH_ENABLED", "0") == "1"

def send_ntfy_push(target_uid, title, content, target_action=""):
    if not target_uid:
        return
    # 实时弹窗已关闭：直接返回，不再发起任何网络请求
    # 消息本身已由 push_system_notice() 写入 notifications 表，App 内仍可见
    if not NTFY_PUSH_ENABLED:
        return
    def _do_send():
        try:
            topic = f"{NTFY_TOPIC_PREFIX}{target_uid}"
            urls_to_try = [f"{NTFY_BASE_URL}/{topic}"]
            if "127.0.0.1" not in NTFY_BASE_URL:
                urls_to_try.append(f"http://127.0.0.1:8075/{topic}")

            headers = {
                "Title": title.encode("utf-8"),
                "Priority": "4",
                "Tags": "dog,heart",
            }
            if target_action:
                headers["X-Click"] = f"schedule://open?action={target_action}"
                headers["Click"] = f"schedule://open?action={target_action}"

            data_bytes = content.encode("utf-8")
            for url in urls_to_try:
                try:
                    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        if resp.status < 400:
                            break
                except Exception:
                    continue
        except Exception as e:
            sys.stderr.write(f"ntfy push error: {e}\n")
    threading.Thread(target=_do_send, daemon=True).start()


def push_system_notice(target_uid, title, content, target_action=""):
    if not target_uid:
        return
    try:
        clean_content = re.sub(r'<[^>]+>', ' ', content).strip()
        clean_content = re.sub(r'\s+', ' ', clean_content)
        created = time.strftime("%Y-%m-%d %H:%M:%S")
        with LOCK:
            CONN.execute(
                "INSERT INTO notifications(target_uid, title, content, target_action, is_read, is_pushed, created_at) VALUES(?,?,?,?,0,0,?)",
                (target_uid, title, clean_content, target_action or "", created)
            )
        # 同步向 ntfy 推送服务发送实时 push
        send_ntfy_push(target_uid, title, clean_content, target_action)
    except Exception as e:
        print("[push_system_notice] error:", e)


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


def get_comment_counts(date_str=None):
    with LOCK:
        if date_str:
            rows = CONN.execute(
                "SELECT event_id, COUNT(*) n FROM comments WHERE date=? OR date='' GROUP BY event_id",
                (date_str,)
            ).fetchall()
        else:
            rows = CONN.execute("SELECT event_id, COUNT(*) n FROM comments GROUP BY event_id").fetchall()
        return {r["event_id"]: r["n"] for r in rows}


def get_anniversaries():
    with LOCK:
        rows = CONN.execute("SELECT * FROM anniversaries ORDER BY date ASC, id ASC").fetchall()
        return [dict(r) for r in rows]


def set_setting(key, value):
    with LOCK:
        CONN.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )

def wal_checkpoint():
    try:
        with LOCK:
            CONN.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:
        pass

def safe_trash_file(fname):
    """安全软删除：将文件移至安全回收站，防误删丢失，保留 30 天自动清理"""
    if not fname:
        return
    try:
        src = os.path.join(PHOTOS_DIR, fname)
        if os.path.exists(src):
            os.makedirs(PHOTOS_TRASH_DIR, exist_ok=True)
            dst = os.path.join(PHOTOS_TRASH_DIR, f"{int(time.time())}_{fname}")
            shutil.move(src, dst)
    except Exception as e:
        sys.stderr.write(f"[safe_trash_file warn]: {e}\n")

def cleanup_trash():
    """定期清理回收站中超过 30 天的废弃照片，控制磁盘体积"""
    if not os.path.exists(PHOTOS_TRASH_DIR):
        return
    now = time.time()
    for fn in os.listdir(PHOTOS_TRASH_DIR):
        fp = os.path.join(PHOTOS_TRASH_DIR, fn)
        try:
            if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > (30 * 86400):
                os.remove(fp)
        except Exception:
            pass

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    # 初始化主线程连接并建表
    conn = get_connection()
    with LOCK:
        conn.executescript(SCHEMA)
        conn.executescript(cycle.SCHEMA)
        try:
            CONN.execute("ALTER TABLE comments ADD COLUMN date TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            CONN.execute("ALTER TABLE notifications ADD COLUMN target_action TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            CONN.execute("ALTER TABLE notifications ADD COLUMN is_pushed INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            CONN.execute("UPDATE notifications SET is_pushed=1 WHERE is_pushed=0 AND is_read=1")
        except Exception:
            pass
        try:
            CONN.execute("ALTER TABLE diary_photos ADD COLUMN live_video TEXT DEFAULT ''")
        except Exception:
            pass
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
        # 创建高性能二级索引，大幅加速高频日程、日记、评论及未读通知轮询
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_events_lookup ON events(uid, repeat, weekday, date)",
            "CREATE INDEX IF NOT EXISTS idx_comments_lookup ON comments(event_id, date, created)",
            "CREATE INDEX IF NOT EXISTS idx_diaries_lookup ON diaries(date, id)",
            "CREATE INDEX IF NOT EXISTS idx_diary_photos_lookup ON diary_photos(diary_id, sort_order)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_lookup ON notifications(target_uid, is_read, id)",
            "CREATE INDEX IF NOT EXISTS idx_wishes_lookup ON wishes(is_done, priority, id)",
            "CREATE INDEX IF NOT EXISTS idx_trips_lookup ON trips(start_date, id)",
            "CREATE INDEX IF NOT EXISTS idx_trip_items_lookup ON trip_items(trip_id, category, sort_order)",
        ]
        for idx_sql in indexes:
            try:
                CONN.execute(idx_sql)
            except Exception as e:
                sys.stderr.write(f"[init_db] index warn: {e}\n")
        # 简报改为仅月度 + 新增提示词：升级时一次性清空旧缓存（含空月份/旧文案）
        if get_setting("summary_prompt_version", "") != "2":
            try:
                CONN.execute("DELETE FROM summary_cache")
                CONN.execute("INSERT INTO meta(key,value) VALUES('summary_prompt_version','2') "
                             "ON CONFLICT(key) DO UPDATE SET value='2'")
            except Exception as e:
                sys.stderr.write(f"[init_db] summary cache reset warn: {e}\n")


# ---------------------------------------------------------------- 周次 / 时间计算

def monday_of(d):
    return d - timedelta(days=d.weekday())


def synthesize_motion_photo_bytes(jpg_bytes, mp4_bytes):
    """将静态 JPEG 与 MP4 视频轨合成为全安卓通用（Google/小米/OPPO/vivo/华为/三星）单文件实况照片 (Motion Photo .jpg)"""
    if not jpg_bytes or not mp4_bytes or not jpg_bytes.startswith(b"\xff\xd8"):
        return jpg_bytes
    # 如果原本就已经是内嵌了 MP4 视频轨与 XMP 头部的原厂实况照片，直接原样返回
    if (b"MotionPhoto" in jpg_bytes[:65536] or b"MicroVideo" in jpg_bytes[:65536]) and b"ftyp" in jpg_bytes[len(jpg_bytes) // 3:]:
        return jpg_bytes
    import struct
    vlen = len(mp4_bytes)
    ts_us = 0
    xmp_str = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 5.1.0-jc003">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:GCamera="http://ns.google.com/photos/1.0/camera/" '
        'xmlns:OpCamera="http://ns.oplus.com/photos/1.0/camera/" '
        'xmlns:MiCamera="http://ns.xiaomi.com/photos/1.0/camera/" '
        'xmlns:Container="http://ns.google.com/photos/1.0/container/" '
        'xmlns:Item="http://ns.google.com/photos/1.0/container/item/" '
        f'GCamera:MotionPhoto="1" GCamera:MotionPhotoVersion="1" GCamera:MotionPhotoPresentationTimestampUs="{ts_us}" '
        f'GCamera:MicroVideo="1" GCamera:MicroVideoVersion="1" GCamera:MicroVideoOffset="{vlen}" GCamera:MicroVideoPresentationTimestampUs="{ts_us}" '
        f'OpCamera:MotionPhotoPrimaryPresentationTimestampUs="{ts_us}" OpCamera:MotionPhotoOwner="oplus" OpCamera:OLivePhotoVersion="2" OpCamera:VideoLength="{vlen}" '
        f'MiCamera:MicroVideo="1" MiCamera:MicroVideoVersion="1" MiCamera:MicroVideoOffset="{vlen}" MiCamera:MicroVideoPresentationTimestampUs="{ts_us}">'
        '<Container:Directory><rdf:Seq>'
        '<rdf:li rdf:parseType="Resource"><Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary" Item:Length="0" Item:Padding="0"/></rdf:li>'
        f'<rdf:li rdf:parseType="Resource"><Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="{vlen}" Item:Padding="0"/></rdf:li>'
        '</rdf:Seq></Container:Directory>'
        '</rdf:Description></rdf:RDF></x:xmpmeta>'
    )
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp_str.encode("utf-8")
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return jpg_bytes[:2] + app1 + jpg_bytes[2:] + mp4_bytes


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


def get_unread_notifications_count(uid):
    if not uid:
        return 0
    try:
        with LOCK:
            row = CONN.execute("SELECT COUNT(*) c FROM notifications WHERE target_uid=? AND is_read=0", (uid,)).fetchone()
            return row["c"] if row else 0
    except Exception:
        return 0


def week_payload(anchor_iso, uid=None):
    anchor = date.fromisoformat(anchor_iso)
    mon = monday_of(anchor)
    days = [mon + timedelta(days=i) for i in range(7)]
    users = get_users()
    events = get_all_events()
    ws = t2m(get_setting("window_start", "08:00"))
    we = t2m(get_setting("window_end", "22:00"))
    if ws >= we:
        ws, we = 480, 1320
    min_gap = int(get_setting("min_gap", "20") or 20)

    out_days = []
    for d in days:
        d_str = d.isoformat()
        cmap = get_comment_counts(d_str)
        per = {}
        for u_id in ("a", "b"):
            u = users[u_id]
            evs, ivs = [], []
            for ev in events:
                if ev["uid"] not in (u_id, "both") or not occurs_on(ev, d, u["week1"]):
                    continue
                evs.append({
                    "id": ev["id"], "uid": ev["uid"], "title": ev["title"], "location": ev["location"],
                    "note": ev["note"], "tstart": ev["tstart"], "tend": ev["tend"],
                    "week_spec": ev["week_spec"], "repeat": ev["repeat"],
                    "n_comments": cmap.get(ev["id"], 0),
                })
                ivs.append([t2m(ev["tstart"]), t2m(ev["tend"])])
            evs.sort(key=lambda x: (x["tstart"], x["tend"]))
            per[u_id] = {"events": evs, "busy": merge_ivs(ivs)}
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
                if ea["id"] == eb["id"]:
                    continue
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
        "users": {u_id: {"name": u["name"], "week1": u["week1"], "wx_configured": bool(u.get("wx_uid", ""))} for u_id, u in users.items()},
        "weeknums": {u_id: week_num(u["week1"], mon) for u_id, u in users.items()},
        "settings": {
            "window_start": m2t(ws), "window_end": m2t(we), "min_gap": min_gap,
            "llm_api_base": get_setting("llm_api_base", ""),
            "llm_key_configured": bool(get_setting("llm_api_key", "")) if uid == "a" else False,
            "llm_model": get_setting("llm_model", "deepseek-chat")
        },
        "days": out_days,
        "anniversaries": get_anniversaries(),
        "unread_count": get_unread_notifications_count(uid),
    }


# ---------------------------------------------------------------- 会话

def make_token(uid):
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = "%s:%d:%s" % (uid, exp, get_setting("session_epoch", "1"))
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + ":" + sig


def parse_token(tok):
    try:
        payload, sig = tok.rsplit(":", 1)
        expect = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        uid, exp, epoch = payload.split(":", 2)
        if int(exp) < time.time() or uid not in ("a", "b"):
            return None
        if epoch != get_setting("session_epoch", "1"):
            return None
        return uid
    except Exception:
        return None


def cleanup_fails():
    """清理超时的登录失败记录，防止内存泄漏"""
    now = time.time()
    for ip, rec in list(FAILS.items()):
        if now - rec[1] > 600:
            FAILS.pop(ip, None)

def rate_ok(ip):
    rec = FAILS.get(ip)
    if not rec:
        return True
    if time.time() - rec[1] > 600:
        FAILS.pop(ip, None)
        return True
    return rec[0] < 10

def rate_fail(ip):
    cleanup_fails()
    rec = FAILS.get(ip)
    if not rec or time.time() - rec[1] > 600:
        FAILS[ip] = [1, time.time()]
    else:
        rec[0] += 1

_TEMPLATE_CACHE = None
_TEMPLATE_MTIME = 0

def get_page_html():
    """加载外部 template_page.html 模板（mtime 内存缓存）；缺失或不可读直接抛错"""
    global _TEMPLATE_CACHE, _TEMPLATE_MTIME
    tpl_file = os.path.join(APP_DIR, "template_page.html")
    try:
        mtime = os.path.getmtime(tpl_file)
        if _TEMPLATE_CACHE is not None and _TEMPLATE_MTIME == mtime:
            return _TEMPLATE_CACHE
        with open(tpl_file, "r", encoding="utf-8") as f:
            content = f.read()
        _TEMPLATE_CACHE = content
        _TEMPLATE_MTIME = mtime
        return content
    except Exception as e:
        raise RuntimeError(f"template_page.html 缺失或不可读: {e}") from e


# ---------------------------------------------------------------- 前端页面



class Handler(BaseHTTPRequestHandler):
    server_version = "sched-share/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 基础工具 ----
    def get_client_ip(self):
        """智能提取真实客户端 IP（优先 X-Forwarded-For 与 X-Real-IP）"""
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            first_ip = xff.split(",")[0].strip()
            if first_ip:
                return first_ip
        xri = self.headers.get("X-Real-IP")
        if xri and xri.strip():
            return xri.strip()
        return self.client_address[0]

    def send_bytes(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 现代 Web 安全防护响应头
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        # Health status may be present in explicitly enabled notification details.
        if self.path.split('?', 1)[0] in ('/api/messages', '/api/notifications', '/api/notifications/poll'):
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("Vary", "Cookie")
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
        # 上传手账多张拍立得图片与实况视频时请求体积会达到几十 MB，放宽限制到 150MB
        if n <= 0 or n > 157286400:
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
    def do_HEAD(self):
        """支持健康探针与 HEAD 探测"""
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.send_bytes(200, b"", "application/json; charset=utf-8")
        if path in ("/", "/index.html"):
            return self.send_bytes(200, b"", "text/html; charset=utf-8")
        return self.send_bytes(200, b"", "text/plain; charset=utf-8")

    def render_index_html(self):
        """直出首屏数据水合（SSR），使前端零延迟秒开课表"""
        raw_html = get_page_html()
        uid = self.current_uid()
        users = get_users()
        meta_data = {"users": [{"uid": u, "name": users[u]["name"]} for u in ("a", "b")]}
        if uid in ("a", "b"):
            today_iso = date.today().isoformat()
            try:
                week_data = week_payload(today_iso, uid=uid)
            except Exception as e:
                sys.stderr.write(f"[Warn] Failed generating initial week payload: {e}\n")
                week_data = None
            me_data = {
                "uid": uid,
                "name": users.get(uid, {}).get("name", uid),
                "unread_count": get_unread_notifications_count(uid)
            }
        else:
            me_data = None
            week_data = None

        ssr_payload = {
            "meta": meta_data,
            "me": me_data,
            "week": week_data,
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        json_str = json.dumps(ssr_payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
        script_tag = f'<script id="__SSR_DATA__">window.__INITIAL_DATA__ = {json_str};</script>'

        if '<script id="__SSR_DATA__">' in raw_html:
            final_html = re.sub(r'<script id="__SSR_DATA__">.*?</script>', script_tag, raw_html, flags=re.DOTALL)
        elif '<div class="toast"' in raw_html:
            final_html = raw_html.replace('<div class="toast"', script_tag + '\n<div class="toast"', 1)
        else:
            final_html = raw_html.replace('</body>', script_tag + '\n</body>', 1)

        return self.send_html(final_html)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self.render_index_html()
            if path in ("/cycle.js", "/cycle.css", "/settings_ui.js", "/settings_ui.css"):
                mime = "text/javascript" if path.endswith(".js") else "text/css"
                with open(os.path.join(APP_DIR, path[1:]), "rb") as asset:
                    return self.send_bytes(200, asset.read(), mime + "; charset=utf-8",
                                           extra=[("Cache-Control", "no-cache")])
            if path == "/api/settings":
                uid = self.authed()
                if uid is None:
                    return
                with LOCK:
                    payload = settings_service.payload(CONN, uid)
                return self.send_json(payload, extra=[("Cache-Control", "no-store, private"), ("Vary", "Cookie")])
            if path == "/api/cycle" or path.startswith("/api/cycle/"):
                uid = self.authed()
                if uid is None:
                    return
                headers = [("Cache-Control", "no-store, private"), ("Vary", "Cookie")]
                try:
                    with LOCK:
                        if path == "/api/cycle/export":
                            payload = cycle.export_data(CONN, uid)
                            headers.append(("Content-Disposition", 'attachment; filename="my-cycle-data.json"'))
                        elif path == "/api/cycle":
                            qs = parse_qs(urlparse(self.path).query)
                            month = qs.get("month", [date.today().strftime("%Y-%m")])[0]
                            payload = cycle.snapshot(CONN, uid, month)
                        else:
                            raise cycle.Error("接口不存在", 404)
                    return self.send_json(payload, extra=headers)
                except cycle.Error as exc:
                    return self.send_json({"error": str(exc)}, exc.status, extra=headers)
            if path.startswith("/img/"):
                base = path[5:].split(".")[0]
                data, etag = get_image_asset(base)
                if data:
                    inm = self.headers.get("If-None-Match")
                    if inm and (inm == etag or inm.strip() == etag):
                        return self.send_bytes(304, b"", "image/png",
                                               extra=[("ETag", etag),
                                                      ("Cache-Control", "public, max-age=2592000, immutable")])
                    return self.send_bytes(
                        200, data, "image/png",
                        extra=[
                            ("ETag", etag),
                            ("Cache-Control", "public, max-age=2592000, immutable")
                        ]
                    )
                return self.send_json({"error": "not found"}, 404)
            if path.startswith("/api/event/"):
                if self.authed() is None:
                    return
                try:
                    eid = int(path.rsplit("/", 1)[-1])
                except ValueError:
                    return self.send_json({"error": "参数错误"}, 400)
                qs = parse_qs(urlparse(self.path).query)
                req_date = (qs.get("date", [""])[0] or "").strip()
                with LOCK:
                    ev = CONN.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
                    if req_date:
                        cmts = CONN.execute(
                            "SELECT c.id, c.uid, c.text, c.created, c.date, u.name FROM comments c "
                            "LEFT JOIN users u ON u.uid=c.uid WHERE c.event_id=? AND (c.date=? OR c.date='') "
                            "ORDER BY c.id",
                            (eid, req_date)).fetchall()
                    else:
                        cmts = CONN.execute(
                            "SELECT c.id, c.uid, c.text, c.created, c.date, u.name FROM comments c "
                            "LEFT JOIN users u ON u.uid=c.uid WHERE c.event_id=? ORDER BY c.id",
                            (eid,)).fetchall()
                if ev is None:
                    return self.send_json({"error": "日程不存在"}, 404)
                users = get_users()
                ev_uid = ev["uid"]
                owner_name = (f"{users['a']['name']} & {users['b']['name']}" if ev_uid == "both"
                              else users.get(ev_uid, {}).get("name", ev_uid))
                return self.send_json({
                    "event": dict(ev),
                    "owner": {"uid": ev_uid, "name": owner_name},
                    "comments": [dict(c) for c in cmts],
                    "filter_date": req_date
                })
            if path == "/api/health":
                return self.send_json({"ok": True, "time": time.strftime("%F %T")})
            if path == "/api/meta":
                users = get_users()
                if not self.current_uid():
                    # 未登录不返回真实昵称，占位名与前端内置默认一致
                    return self.send_json({"users": [{"uid": u, "name": ("小金毛" if u == "a" else "小白狗")} for u in ("a", "b")]})
                return self.send_json({"users": [{"uid": u, "name": users[u]["name"]} for u in ("a", "b")]})
            if path == "/api/me":
                uid = self.current_uid()
                if not uid:
                    return self.send_json({"error": "未登录"}, 401)
                users = get_users()
                return self.send_json({
                    "uid": uid,
                    "name": users[uid]["name"],
                    "unread_count": get_unread_notifications_count(uid)
                })
            if path.startswith("/photos/"):
                if self.authed() is None:
                    return
                fn = os.path.basename(path[8:])
                fp = os.path.join(PHOTOS_DIR, fn)
                if fp and os.path.exists(fp):
                    mime = "image/jpeg"
                    lfn = fn.lower()
                    if lfn.endswith(".png"): mime = "image/png"
                    elif lfn.endswith(".webp"): mime = "image/webp"
                    elif lfn.endswith(".mp4"): mime = "video/mp4"
                    elif lfn.endswith(".mov"): mime = "video/quicktime"
                    elif lfn.endswith(".webm"): mime = "video/webm"
                    file_size = os.path.getsize(fp)
                    range_hdr = self.headers.get("Range")
                    if range_hdr and range_hdr.startswith("bytes="):
                        try:
                            rng = range_hdr[6:].split(",")[0].strip()
                            s_str, e_str = rng.split("-", 1)
                            start = int(s_str) if s_str else 0
                            end = int(e_str) if e_str else (file_size - 1)
                            end = min(end, file_size - 1)
                            if 0 <= start <= end < file_size:
                                with open(fp, "rb") as f:
                                    f.seek(start)
                                    chunk = f.read(end - start + 1)
                                return self.send_bytes(
                                    206, chunk, mime,
                                    extra=[
                                        ("Content-Range", f"bytes {start}-{end}/{file_size}"),
                                        ("Accept-Ranges", "bytes"),
                                        ("Cache-Control", "public, max-age=604800"),
                                    ]
                                )
                        except Exception:
                            pass
                    with open(fp, "rb") as f:
                        return self.send_bytes(200, f.read(), mime,
                                               extra=[("Cache-Control", "public, max-age=604800"),
                                                      ("Accept-Ranges", "bytes")])
                return self.send_json({"error": "not found"}, 404)
            if path == "/api/week":
                uid = self.authed()
                if uid is None:
                    return
                qs = parse_qs(urlparse(self.path).query)
                d = (qs.get("date") or [""])[0]
                if not DATE_RE.match(d):
                    d = date.today().isoformat()
                try:
                    date.fromisoformat(d)
                except ValueError:
                    return self.send_json({"error": "日期格式无效"}, 400)
                return self.send_json(week_payload(d, uid=uid))
            if path == "/api/month":
                uid = self.authed()
                if uid is None:
                    return
                qs = parse_qs(urlparse(self.path).query)
                ym = (qs.get("month") or [""])[0]
                if not re.match(r"^\d{4}-\d{2}$", ym):
                    ym = date.today().strftime("%Y-%m")
                return self.send_json(self.month_payload(ym, uid=uid))
            if path == "/api/diaries":
                if self.authed() is None:
                    return
                qs = parse_qs(urlparse(self.path).query)
                dt = (qs.get("date") or [""])[0]
                return self.send_json(self.get_diaries_by_date(dt))
            if path == "/api/summary":
                if self.authed() is None:
                    return
                qs = parse_qs(urlparse(self.path).query)
                ym = (qs.get("month") or [""])[0]
                if ym and not valid_month(ym):
                    return self.send_json({"ok": False, "error": "month 需为 YYYY-MM"}, 400)
                if ym and ym > date.today().strftime("%Y-%m"):
                    return self.send_json({"ok": False, "error": "不能查看未来的月份"}, 400)
                force = (qs.get("refresh") or ["0"])[0] == "1"
                return self.send_json(generate_month_summary(ym or None, force_refresh=force))
            if path in ("/api/messages", "/api/notifications"):
                uid = self.authed()
                if uid is None:
                    return
                with LOCK:
                    cnt = CONN.execute(
                        "SELECT COUNT(*) c FROM notifications WHERE target_uid=? AND is_read=0",
                        (uid,)
                    ).fetchone()["c"]
                    rows = CONN.execute(
                        "SELECT id, title, content, target_action, is_read, created_at FROM notifications WHERE target_uid=? ORDER BY id DESC LIMIT 100",
                        (uid,)
                    ).fetchall()
                return self.send_json({
                    "ok": True,
                    "unread_count": cnt,
                    "messages": [dict(r) for r in rows]
                })
            if path == "/api/wishes":
                uid = self.authed()
                if uid is None:
                    return
                users = get_users()
                with LOCK:
                    rows = CONN.execute("SELECT * FROM wishes ORDER BY is_done ASC, priority DESC, id DESC").fetchall()
                    wishes = []
                    for r in rows:
                        item = dict(r)
                        c_uid = item.get("creator_uid")
                        item["creator_name"] = users.get(c_uid, {}).get("name", "小狗")
                        wishes.append(item)
                    total = len(wishes)
                    done = sum(1 for w in wishes if w.get("is_done") == 1)
                    pending = total - done
                return self.send_json({
                    "ok": True,
                    "wishes": wishes,
                    "stats": {"total": total, "done": done, "pending": pending}
                })
            if path == "/api/trips":
                uid = self.authed()
                if uid is None:
                    return
                return self.send_json({"ok": True, "trips": trips_overview(),
                                       "categories": TRIP_CATEGORIES})
            if path == "/api/trips/detail":
                uid = self.authed()
                if uid is None:
                    return
                qs = parse_qs(urlparse(self.path).query)
                try:
                    tid = int((qs.get("id") or [""])[0])
                except ValueError:
                    return self.send_json({"error": "旅行不存在"}, 404)
                data = trip_detail(tid)
                if not data:
                    return self.send_json({"error": "旅行不存在"}, 404)
                payload = {"ok": True, "categories": TRIP_CATEGORIES}
                payload.update(data)
                return self.send_json(payload)
            if path == "/api/notifications/poll":
                uid = self.authed()
                if not uid:
                    return
                with LOCK:
                    rows = CONN.execute(
                        "SELECT id, title, content, target_action, is_read, created_at FROM notifications WHERE target_uid=? AND is_pushed=0 ORDER BY id ASC",
                        (uid,)
                    ).fetchall()
                    if rows:
                        ids = [r["id"] for r in rows]
                        q_marks = ",".join("?" * len(ids))
                        CONN.execute(f"UPDATE notifications SET is_pushed=1 WHERE id IN ({q_marks})", ids)
                return self.send_json({"ok": True, "notifications": [dict(r) for r in rows]})
            return self.send_json({"error": "not found"}, 404)
        except Exception:
            self.log_error("GET %s failed: %s", path, sys.exc_info()[1])
            return self.send_json({"error": "服务器内部错误"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        d = self.body_json()
        try:
            if path.startswith("/api/cycle/"):
                uid = self.authed()
                if uid is None:
                    return
                headers = [("Cache-Control", "no-store, private"), ("Vary", "Cookie")]
                # Reject cross-origin writes, including same-site sibling origins.
                origin = self.headers.get("Origin")
                if origin:
                    parsed_origin = urlparse(origin)
                    if parsed_origin.netloc != self.headers.get("Host") or parsed_origin.scheme not in ("http", "https"):
                        return self.send_json({"error": "不允许跨站写入"}, 403, extra=headers)
                if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                    return self.send_json({"error": "须使用 application/json"}, 415, extra=headers)
                try:
                    with db_transaction() as conn:
                        result = cycle.mutate(conn, uid, path[len("/api/cycle/"):], d)
                    return self.send_json(result, extra=headers)
                except cycle.Error as exc:
                    return self.send_json({"error": str(exc)}, exc.status, extra=headers)
            if path == "/api/login":
                ip = self.get_client_ip()
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
                    today_iso = date.today().isoformat()
                    init_week = week_payload(today_iso, uid=uid)
                    return self.send_json({
                        "ok": True,
                        "uid": uid,
                        "name": users[uid]["name"],
                        "unread_count": get_unread_notifications_count(uid),
                        "week": init_week
                    }, extra=[("Set-Cookie", cookie)])
                rate_fail(ip)
                time.sleep(0.4)
                return self.send_json({"error": "身份或密码不正确"}, 401)

            if path == "/api/logout":
                cookie = "%s=deleted; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE
                return self.send_json({"ok": True}, extra=[("Set-Cookie", cookie)])

            if path in ("/api/messages/read", "/api/notifications/read"):
                uid = self.authed()
                if uid is None:
                    return
                mark_all = d.get("all")
                mid = d.get("id")
                with LOCK:
                    if mark_all:
                        CONN.execute("UPDATE notifications SET is_read=1 WHERE target_uid=?", (uid,))
                    elif mid:
                        CONN.execute("UPDATE notifications SET is_read=1 WHERE target_uid=? AND id=?", (uid, int(mid)))
                    cnt = CONN.execute(
                        "SELECT COUNT(*) c FROM notifications WHERE target_uid=? AND is_read=0",
                        (uid,)
                    ).fetchone()["c"]
                return self.send_json({"ok": True, "unread_count": cnt})

            if path in ("/api/messages/delete", "/api/notifications/delete"):
                uid = self.authed()
                if uid is None:
                    return
                del_all = d.get("all")
                clear_read = d.get("clear_read")
                mid = d.get("id")
                with LOCK:
                    if clear_read:
                        CONN.execute("DELETE FROM notifications WHERE target_uid=? AND is_read=1", (uid,))
                    elif del_all:
                        CONN.execute("DELETE FROM notifications WHERE target_uid=?", (uid,))
                    elif mid is not None:
                        CONN.execute("DELETE FROM notifications WHERE target_uid=? AND id=?", (uid, int(mid)))
                    cnt = CONN.execute(
                        "SELECT COUNT(*) c FROM notifications WHERE target_uid=? AND is_read=0",
                        (uid,)
                    ).fetchone()["c"]
                return self.send_json({"ok": True, "unread_count": cnt})

            if path == "/api/trips":
                uid = self.authed()
                if uid is None:
                    return
                origin = self.headers.get("Origin")
                if origin and (urlparse(origin).netloc != self.headers.get("Host")
                               or urlparse(origin).scheme not in ("http", "https")):
                    return self.send_json({"error": "不允许跨站写入"}, 403)
                if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                    return self.send_json({"error": "须使用 application/json"}, 415)
                action = str(d.get("action") or "").strip()
                now = time.strftime("%Y-%m-%d %H:%M:%S")

                def clean_date(key):
                    v = str(d.get(key) or "").strip()
                    if not v:
                        return ""
                    date.fromisoformat(v)
                    return v

                if action == "create":
                    dest = str(d.get("destination") or "").strip()[:40]
                    title = str(d.get("title") or "").strip()[:40] or dest
                    if not dest and not title:
                        return self.send_json({"error": "先写上要去哪儿"}, 400)
                    try:
                        start, end = clean_date("start_date"), clean_date("end_date")
                    except ValueError:
                        return self.send_json({"error": "日期格式不正确"}, 400)
                    if start and end and end < start:
                        return self.send_json({"error": "返程日期不能早于出发日期"}, 400)
                    with LOCK:
                        cur = CONN.execute(
                            "INSERT INTO trips(title, destination, start_date, end_date, created_by, created_at) "
                            "VALUES(?,?,?,?,?,?)",
                            (title or dest, dest, start, end, uid, now))
                        tid = cur.lastrowid
                        if d.get("use_template", True) not in (False, 0, "0", "false"):
                            for cat, name in TRIP_TEMPLATE:
                                _insert_trip_item(tid, cat, name)
                    users = get_users()
                    other_uid = "b" if uid == "a" else "a"
                    push_system_notice(
                        other_uid, "✈️ %s 建了一个旅行清单" % users[uid]["name"],
                        "【%s】%s" % (title or dest, start or "日期还没定"), "view:trips")
                    return self.send_json({"ok": True, "id": tid})

                if action == "delete":
                    try:
                        tid = int(d.get("id"))
                    except (TypeError, ValueError):
                        return self.send_json({"error": "旅行不存在"}, 404)
                    with LOCK:
                        if not CONN.execute("SELECT id FROM trips WHERE id=?", (tid,)).fetchone():
                            return self.send_json({"error": "旅行不存在"}, 404)
                        CONN.execute("DELETE FROM trip_items WHERE trip_id=?", (tid,))
                        CONN.execute("DELETE FROM trips WHERE id=?", (tid,))
                    return self.send_json({"ok": True})

                if action == "copy_last":
                    try:
                        tid = int(d.get("id"))
                    except (TypeError, ValueError):
                        return self.send_json({"error": "旅行不存在"}, 404)
                    with LOCK:
                        if not CONN.execute("SELECT id FROM trips WHERE id=?", (tid,)).fetchone():
                            return self.send_json({"error": "旅行不存在"}, 404)
                        prev = CONN.execute(
                            "SELECT id FROM trips WHERE id<>? AND EXISTS"
                            "(SELECT 1 FROM trip_items i WHERE i.trip_id=trips.id) "
                            "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
                        if not prev:
                            return self.send_json({"error": "还没有可复制的上一份清单"}, 400)
                        copied = 0
                        for it in CONN.execute(
                                "SELECT * FROM trip_items WHERE trip_id=? ORDER BY category, sort_order, id",
                                (prev["id"],)).fetchall():
                            if CONN.execute(
                                    "SELECT 1 FROM trip_items WHERE trip_id=? AND category=? AND name=?",
                                    (tid, it["category"], it["name"])).fetchone():
                                continue
                            _insert_trip_item(tid, it["category"], it["name"], it["qty"], it["note"])
                            copied += 1
                    return self.send_json({"ok": True, "copied": copied})

                if action == "item_add":
                    try:
                        tid = int(d.get("trip_id"))
                    except (TypeError, ValueError):
                        return self.send_json({"error": "旅行不存在"}, 404)
                    name = str(d.get("name") or "").strip()[:40]
                    if not name:
                        return self.send_json({"error": "物品名称不能为空"}, 400)
                    category = str(d.get("category") or "other").strip()
                    if category not in TRIP_CATEGORY_KEYS:
                        category = "other"
                    qty = str(d.get("qty") or "").strip()[:10]
                    note = str(d.get("note") or "").strip()[:60]
                    with LOCK:
                        if not CONN.execute("SELECT id FROM trips WHERE id=?", (tid,)).fetchone():
                            return self.send_json({"error": "旅行不存在"}, 404)
                        _insert_trip_item(tid, category, name, qty, note)
                    return self.send_json({"ok": True})

                if action in ("item_update", "item_toggle", "item_delete"):
                    try:
                        iid = int(d.get("id"))
                    except (TypeError, ValueError):
                        return self.send_json({"error": "物品不存在"}, 404)
                    with LOCK:
                        item = CONN.execute("SELECT * FROM trip_items WHERE id=?", (iid,)).fetchone()
                        if not item:
                            return self.send_json({"error": "物品不存在"}, 404)
                        if action == "item_delete":
                            CONN.execute("DELETE FROM trip_items WHERE id=?", (iid,))
                        elif action == "item_toggle":
                            CONN.execute("UPDATE trip_items SET is_packed=?, updated_at=? WHERE id=?",
                                         (0 if item["is_packed"] else 1, now, iid))
                        else:
                            name = str(d.get("name") or item["name"]).strip()[:40] or item["name"]
                            category = str(d.get("category") or item["category"]).strip()
                            if category not in TRIP_CATEGORY_KEYS:
                                category = item["category"]
                            qty = str(d.get("qty") if "qty" in d else item["qty"]).strip()[:10]
                            note = str(d.get("note") if "note" in d else item["note"]).strip()[:60]
                            CONN.execute(
                                "UPDATE trip_items SET name=?, category=?, qty=?, note=?, updated_at=? WHERE id=?",
                                (name, category, qty, note, now, iid))
                    return self.send_json({"ok": True})

                return self.send_json({"error": "未知操作"}, 400)

            if path == "/api/wishes":
                uid = self.authed()
                if uid is None:
                    return
                title = str(d.get("title") or "").strip()[:100]
                if not title:
                    return self.send_json({"error": "心愿内容不能为空"}, 400)
                category = str(d.get("category") or "life").strip()
                if category not in ("travel", "food", "movie", "life", "other"):
                    category = "life"
                priority = 1 if d.get("priority") in (1, "1", True) else 0
                note = str(d.get("note") or "").strip()[:500]
                creator = d.get("creator_uid")
                if creator not in ("a", "b"):
                    creator = uid
                created = time.strftime("%Y-%m-%d %H:%M")
                with LOCK:
                    cur = CONN.execute(
                        "INSERT INTO wishes(creator_uid, title, category, priority, note, is_done, done_at, created_at) "
                        "VALUES(?,?,?,?,?,0,'',?)",
                        (creator, title, category, priority, note, created)
                    )
                    wid = cur.lastrowid
                users = get_users()
                sender_name = users.get(uid, {}).get("name", "小狗")
                other_uid = "b" if uid == "a" else "a"
                notice_content = f"【{title}】" + (f" · {note[:40]}" if note else "")
                push_system_notice(other_uid, f"✨ {sender_name} 许下了一个新心愿！", notice_content, "view:wishes")
                target_token = users.get(other_uid, {}).get("wx_uid")
                if target_token:
                    send_wechat_notice(target_token, f"✨ {sender_name} 许下了一个新心愿！", notice_content)
                return self.send_json({"ok": True, "id": wid})

            if path == "/api/wishes/toggle":
                uid = self.authed()
                if uid is None:
                    return
                try:
                    wid = int(d.get("id"))
                except (TypeError, ValueError):
                    return self.send_json({"error": "参数错误"}, 400)
                with LOCK:
                    w = CONN.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
                    if not w:
                        return self.send_json({"error": "心愿不存在"}, 404)
                    new_status = 0 if w["is_done"] else 1
                    done_at = time.strftime("%Y-%m-%d %H:%M") if new_status else ""
                    CONN.execute("UPDATE wishes SET is_done=?, done_at=? WHERE id=?", (new_status, done_at, wid))
                if new_status:
                    users = get_users()
                    sender_name = users.get(uid, {}).get("name", "小狗")
                    other_uid = "b" if uid == "a" else "a"
                    push_system_notice(other_uid, "🎉 俩汪共同实现了心愿！", f"【{w['title']}】已打勾完成！快去手账本记录一下吧~ 🐾", "view:wishes")
                    target_token = users.get(other_uid, {}).get("wx_uid")
                    if target_token:
                        send_wechat_notice(target_token, "🎉 俩汪共同实现了心愿！", f"【{w['title']}】已打勾完成！🐾")
                return self.send_json({"ok": True, "is_done": new_status, "done_at": done_at})

            if path == "/api/wishes/delete":
                uid = self.authed()
                if uid is None:
                    return
                try:
                    wid = int(d.get("id"))
                except (TypeError, ValueError):
                    return self.send_json({"error": "参数错误"}, 400)
                with LOCK:
                    CONN.execute("DELETE FROM wishes WHERE id=?", (wid,))
                return self.send_json({"ok": True})

            if path == "/api/wishes/update":
                uid = self.authed()
                if uid is None:
                    return
                try:
                    wid = int(d.get("id"))
                except (TypeError, ValueError):
                    return self.send_json({"error": "参数错误"}, 400)
                title = str(d.get("title") or "").strip()[:100]
                if not title:
                    return self.send_json({"error": "心愿内容不能为空"}, 400)
                category = str(d.get("category") or "life").strip()
                if category not in ("travel", "food", "movie", "life", "other"):
                    category = "life"
                priority = 1 if d.get("priority") in (1, "1", True) else 0
                note = str(d.get("note") or "").strip()[:500]
                with LOCK:
                    CONN.execute("UPDATE wishes SET title=?, category=?, priority=?, note=? WHERE id=?",
                                 (title, category, priority, note, wid))
                return self.send_json({"ok": True})

            if path == "/api/notifications/test":
                uid = self.authed()
                if not uid:
                    return
                users = get_users()
                u_name = users.get(uid, {}).get("name", "小狗")
                push_system_notice(uid, "🐾 线条小狗原生通知测试", f"汪！{u_name}，你的手机通知权限已成功打通！点击此通知可直达小窝设置页面 🐾", "view:settings")
                return self.send_json({"ok": True})

            if path == "/api/events":
                if self.authed() is None:
                    return
                return self.create_event(d)
            if path == "/api/events/update":
                if self.authed() is None:
                    return
                return self.update_event(d)
            if path == "/api/events/delete":
                if self.authed() is None:
                    return
                eid = d.get("id")
                if not isinstance(eid, int):
                    return self.send_json({"error": "参数错误"}, 400)
                with db_transaction():
                    CONN.execute("DELETE FROM events WHERE id=?", (eid,))
                    CONN.execute("DELETE FROM comments WHERE event_id=?", (eid,))
                return self.send_json({"ok": True})
            if path == "/api/comment":
                uid = self.authed()
                if uid is None:
                    return
                eid = d.get("event_id")
                text = str(d.get("text") or "").strip()[:500]
                cmt_date = str(d.get("date") or "").strip()
                if not isinstance(eid, int) or not text:
                    return self.send_json({"error": "参数错误"}, 400)
                created = time.strftime("%Y-%m-%d %H:%M")
                if not cmt_date and DATE_RE.match(created[:10]):
                    cmt_date = created[:10]
                with LOCK:
                    ev_row = CONN.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
                    if ev_row is None:
                        return self.send_json({"error": "日程不存在"}, 404)
                    cur = CONN.execute("INSERT INTO comments(event_id,uid,text,created,date) VALUES(?,?,?,?,?)",
                                       (eid, uid, text, created, cmt_date))
                    cid = cur.lastrowid

                # 微信与系统原生推送给对方狗狗
                users = get_users()
                other_uid = "b" if uid == "a" else "a"
                sender_name = users[uid]["name"]
                ev_title = ev_row["title"]
                notice_title = f"🐾 {sender_name} 给你的日程留了言！"
                notice_content = f"{sender_name} 在【{ev_title}】留言：{text}"
                push_system_notice(other_uid, notice_title, notice_content, f"event:{eid}:{cmt_date}")

                target_token = users[other_uid]["wx_uid"]
                if target_token:
                    title = notice_title
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
            if path == "/api/summary/push":
                if self.authed() is None:
                    return
                ym = d.get("month") or ""
                if ym and not valid_month(ym):
                    return self.send_json({"ok": False, "error": "month 需为 YYYY-MM"}, 400)
                summary, sent_count = push_month_summary(ym or None)
                if sent_count == 0:
                    return self.send_json({
                        "ok": False,
                        "error": "双方均未在【设置】中配置微信推送 Token（如虾推啥/息知），请先前往设置填写！"
                    }, 400)
                return self.send_json({"ok": True, "summary": summary, "sent_count": sent_count})
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

        if uid not in ("a", "b", "both"):
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

        # 微信与系统原生推送新日程提醒给对方狗狗
        users = get_users()
        actor = self.current_uid() or ("a" if uid != "b" else "b")
        other_uid = "b" if actor == "a" else "a"
        author_name = users.get(actor, users["a"])["name"]
        joint_tag = "（💕 两人共同日程）" if uid == "both" else ""
        wd_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        when_str = f"每周{wd_names[weekday]} {tstart}~{tend}" if repeat == "weekly" else f"{ev_date} {tstart}~{tend}"
        loc_str = f" · 📍 {location}" if location else ""
        notice_title = f"🐾 {author_name} 添加了新日程{joint_tag}"
        notice_content = f"【{title}】{loc_str}，时间：{when_str}"
        push_system_notice(other_uid, notice_title, notice_content, f"event:{eid}")

        target_token = users[other_uid]["wx_uid"]
        if target_token:
            note_str = f"<br>📝 备注：{note}" if note else ""
            msg_title = f"🐾 {author_name} 添加了新日程{joint_tag}"
            html_content = (
                f"🐶 <strong>{author_name}</strong> 记下了新日程{joint_tag}：<br>"
                f"📌 <strong>【{title}】</strong>{loc_str}<br>"
                f"⏰ 时间：{when_str}{note_str}"
            )
            send_wechat_notice(target_token, msg_title, html_content)

        return self.send_json({"ok": True, "id": eid})

    def update_event(self, d):
        eid = d.get("id")
        if not isinstance(eid, int):
            return self.send_json({"error": "缺少日程ID"}, 400)
        uid = d.get("uid")
        title = str(d.get("title") or "").strip()
        location = str(d.get("location") or "").strip()[:200]
        note = str(d.get("note") or "").strip()[:200]
        repeat = d.get("repeat")
        week_spec = str(d.get("week_spec") or "").strip()[:60]
        tstart, tend = str(d.get("tstart") or ""), str(d.get("tend") or "")

        if uid not in ("a", "b", "both"):
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
            ev_exists = CONN.execute("SELECT id FROM events WHERE id=?", (eid,)).fetchone()
            if not ev_exists:
                return self.send_json({"error": "日程不存在"}, 404)
            CONN.execute(
                "UPDATE events SET uid=?, title=?, location=?, note=?, repeat=?, weekday=?, date=?, tstart=?, tend=?, week_spec=? WHERE id=?",
                (uid, title, location, note, repeat, weekday, ev_date, tstart, tend, week_spec, eid)
            )

        return self.send_json({"ok": True, "id": eid})

    def save_settings(self, d):
        uid = self.current_uid()
        origin = self.headers.get("Origin")
        if origin and (urlparse(origin).netloc != self.headers.get("Host") or urlparse(origin).scheme not in ("http", "https")):
            return self.send_json({"error": "不允许跨站写入"}, 403)
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            return self.send_json({"error": "须使用 application/json"}, 415)
        try:
            with db_transaction() as conn:
                result = settings_service.apply(conn, uid, d)
            return self.send_json(result, extra=[("Cache-Control", "no-store, private")])
        except settings_service.Error as exc:
            return self.send_json({"error": str(exc)}, exc.status)

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
            # 全局会话纪元 +1：改密后所有已签发会话立即失效（含旧 key 签发的伪造会话）
            CONN.execute("INSERT INTO meta(key,value) VALUES('session_epoch',?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (str(int(get_setting("session_epoch", "1")) + 1),))
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

    def month_payload(self, ym, uid=None):
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
            "users": {u: {"name": users[u]["name"], "wx_configured": bool(users[u].get("wx_uid", ""))} for u in ("a", "b")},
            "unread_count": get_unread_notifications_count(uid),
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

    def _save_diary_media_items(self, dt, photos_input):
        import base64
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        saved_items = []
        for idx, item in enumerate((photos_input or [])[:18]):
            try:
                if isinstance(item, dict):
                    p_b64 = item.get("image") or item.get("photo") or ""
                    v_b64 = item.get("live_video") or item.get("video") or ""
                else:
                    p_b64 = str(item or "")
                    v_b64 = ""
                if not p_b64:
                    continue
                if "," in p_b64:
                    p_b64 = p_b64.split(",", 1)[1]
                data = base64.b64decode(p_b64)
                if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG")):
                    sys.stderr.write("drop non-image upload\n")
                    continue
                token = secrets.token_hex(4)
                fname = f"pic_{dt}_{int(time.time())}_{token}.jpg"
                fpath = os.path.join(PHOTOS_DIR, fname)
                with open(fpath, "wb") as pf:
                    pf.write(data)

                v_fname = ""
                v_fpath = ""
                if v_b64:
                    if "," in v_b64:
                        v_b64 = v_b64.split(",", 1)[1]
                    v_data = base64.b64decode(v_b64)
                    if not (len(v_data) >= 12 and v_data[4:8] == b"ftyp"):
                        sys.stderr.write("drop non-MP4 live video upload\n")
                        v_data = b""
                    if v_data:
                        v_fname = f"live_{dt}_{int(time.time())}_{token}.mp4"
                        v_fpath = os.path.join(PHOTOS_DIR, v_fname)
                        raw_path = v_fpath + ".raw"
                        with open(raw_path, "wb") as vf:
                            vf.write(v_data)
                        try:
                            import subprocess
                            r = subprocess.run(
                                [
                                    "ffmpeg", "-y", "-i", raw_path,
                                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                                    "-vf", "scale='min(1080,iw)':-2", "-pix_fmt", "yuv420p",
                                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                                    v_fpath
                                ],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
                            )
                            if r.returncode == 0 and os.path.exists(v_fpath) and os.path.getsize(v_fpath) > 1000:
                                os.remove(raw_path)
                            else:
                                os.replace(raw_path, v_fpath)
                        except Exception:
                            if os.path.exists(raw_path):
                                os.replace(raw_path, v_fpath)

                    # 将静态封面图与 MP4 实况轨合成为标准单文件实况照片 (Motion Photo .jpg)，使得直接下载 .jpg 也是真·实况照片！
                    try:
                        if v_fpath and os.path.exists(v_fpath):
                            with open(v_fpath, "rb") as vf2:
                                final_mp4 = vf2.read()
                            motion_jpg = synthesize_motion_photo_bytes(data, final_mp4)
                            with open(fpath, "wb") as pf2:
                                pf2.write(motion_jpg)
                    except Exception:
                        pass

                saved_items.append((fname, v_fname))
            except Exception as e:
                self.log_error("保存手账媒体失败: %s", e)
        return saved_items

    def create_diary(self, d):
        uid = self.current_uid()
        title = str(d.get("title") or "").strip()[:80]
        dt = str(d.get("date") or "").strip()
        location = str(d.get("location") or "").strip()[:100]
        mood = str(d.get("mood") or "").strip()[:30]
        weather = str(d.get("weather") or "").strip()[:30]
        content = str(d.get("content") or "").strip()[:4000]
        photos_input = d.get("photos") or []

        if not title:
            return self.send_json({"error": "游玩主题/标题不能为空"}, 400)
        if not dt or not DATE_RE.match(dt):
            return self.send_json({"error": "日期无效"}, 400)

        # 服务端防抖防重机制：检查 15 秒内该用户是否已提交过完全相同的日记（防手速连点/弱网重试）
        recent_dup = CONN.execute(
            "SELECT id FROM diaries WHERE author_uid=? AND date=? AND title=? AND content=? AND created_at >= datetime('now', '+8 hours', '-15 seconds') ORDER BY id DESC LIMIT 1",
            (uid, dt, title, content)
        ).fetchone()
        if recent_dup:
            return self.send_json({"ok": True, "id": recent_dup["id"], "duplicate_prevented": True})

        saved_photos = self._save_diary_media_items(dt, photos_input)

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        with db_transaction():
            cur = CONN.execute(
                "INSERT INTO diaries(date, title, location, mood, weather, content, author_uid, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (dt, title, location, mood, weather, content, uid, now_str)
            )
            diary_id = cur.lastrowid
            for s_idx, (fname, v_fname) in enumerate(saved_photos):
                CONN.execute(
                    "INSERT INTO diary_photos(diary_id, file_name, live_video, sort_order) VALUES(?,?,?,?)",
                    (diary_id, fname, v_fname, s_idx)
                )

        # 微信与系统原生推送新手账通知给对方狗狗
        users = get_users()
        other_uid = "b" if uid == "a" else "a"
        sender_name = users[uid]["name"]
        diary_title = f"📔 {sender_name} 更新了一篇足迹手账！"
        loc_str = f" · 📍 {location}" if location else ""
        mood_str = f" [{mood}]" if mood else ""
        content_snippet = content[:80] if content else "拍下了美好瞬间~ 📷"
        push_system_notice(other_uid, diary_title, f"【{title}{mood_str}】{loc_str}: {content_snippet}", f"diary:{dt}")

        target_token = users[other_uid]["wx_uid"]
        if target_token:
            title = diary_title
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
        photos_input = d.get("photos") or []  # 新增的照片或实况
        keep_photos = d.get("keep_photos") or []  # 保留的原有照片文件名

        if not title:
            return self.send_json({"error": "主题不能为空"}, 400)
        if not dt or not DATE_RE.match(dt):
            return self.send_json({"error": "日期无效"}, 400)

        new_saved_photos = self._save_diary_media_items(dt, photos_input)

        files_to_remove = []
        with db_transaction():
            CONN.execute(
                "UPDATE diaries SET date=?, title=?, location=?, mood=?, content=? WHERE id=?",
                (dt, title, location, mood, content, did)
            )
            # 处理删除旧照片与实况视频
            old_photos = CONN.execute("SELECT file_name, live_video FROM diary_photos WHERE diary_id=?", (did,)).fetchall()
            old_map = {op["file_name"]: (op["live_video"] or "") for op in old_photos}
            for op in old_photos:
                fn = op["file_name"]
                vfn = op["live_video"] or ""
                if fn not in keep_photos:
                    if fn: files_to_remove.append(fn)
                    if vfn: files_to_remove.append(vfn)
            CONN.execute("DELETE FROM diary_photos WHERE diary_id=?", (did,))
            # 重新插入保留的旧照片和新增的照片
            kept_tuples = [(p, old_map[p]) for p in keep_photos if p in old_map]
            all_current = kept_tuples + new_saved_photos
            for s_idx, (fname, v_fname) in enumerate(all_current):
                CONN.execute(
                    "INSERT INTO diary_photos(diary_id, file_name, live_video, sort_order) VALUES(?,?,?,?)",
                    (did, fname, v_fname, s_idx)
                )
        # 事务成功后安全移至回收站（防误删丢失，保留 30 天自动清理）
        for del_fn in files_to_remove:
            safe_trash_file(del_fn)

        return self.send_json({"ok": True, "id": did})

    def delete_diary(self, d):
        try:
            did = int(d.get("id"))
        except (TypeError, ValueError):
            return self.send_json({"error": "参数错误"}, 400)
        files_to_remove = []
        with db_transaction():
            photos = CONN.execute("SELECT file_name, live_video FROM diary_photos WHERE diary_id=?", (did,)).fetchall()
            for p in photos:
                for del_fn in (p["file_name"], p["live_video"] or ""):
                    if del_fn:
                        files_to_remove.append(del_fn)
            CONN.execute("DELETE FROM diary_photos WHERE diary_id=?", (did,))
            CONN.execute("DELETE FROM diaries WHERE id=?", (did,))
        # 事务成功后安全移至回收站（防误删丢失，保留 30 天自动清理）
        for del_fn in files_to_remove:
            safe_trash_file(del_fn)
        return self.send_json({"ok": True})


    def log_message(self, fmt, *args):
        client_ip = self.get_client_ip()
        sys.stdout.write("%s %s %s\n" % (time.strftime("%F %T"), client_ip, fmt % args))


def anniv_reminder_pass(now=None):
    """纪念日/节日提醒：每天检查一次，把临近的重要日子推给两个人。

    提醒窗口（距今天数）：里程碑/生日/节日的 D-7、D-3、D-1、D-0；
    去重：每条提醒以 'anniv_push:<kind>:<date>:<item_id|label>' 记在 meta，
    同一次发生只推一次，服务重启不会重推。
    """
    if special_days is None:
        return 0
    now = now or datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    with LOCK:
        rows = [dict(r) for r in CONN.execute("SELECT * FROM anniversaries").fetchall()]
    if not rows:
        return 0

    sent = 0
    windows = (7, 3, 1, 0)

    def already(key):
        return get_setting("anniv_push:" + key, "") == "1"

    def mark(key):
        set_setting("anniv_push:" + key, "1")

    # 1) 用户自建的纪念日 / 生日 / 倒计时
    for u in special_days.upcoming(rows, today_str, days=max(windows)):
        left = u["days_left"]
        if left not in windows:
            continue
        # key 必须带窗口值：D-7 推过不能吞掉 D-3/D-1/D-0
        key = "item:%s:%s:%s:%d" % (u["target_type"], u["date"], u["id"] or 0, left)
        if already(key):
            continue
        title = "🐾 重要日子提醒" if left else "💖 就是今天！"
        push_system_notice("a", title, u["label"], "view:month")
        push_system_notice("b", title, u["label"], "view:month")
        mark(key)
        sent += 1

    # 2) 恋爱纪念年 + 内置节日：以 love 类型纪念日作锚点（没有就不推）。
    #    扫两个公历年：年末时 D-7 的窗口会跨进下一年（如 12-28 提示情人节）。
    love = [r for r in rows if r.get("target_type") == "love"]
    if love:
        anchor = love[0]["date"]
        seen_fest = set()
        for yv in (now.year, now.year + 1):
            for sp in special_days.special_days_in_year(yv, anchor):
                if sp["kind"] == "milestone":
                    continue  # 天数里程碑已由 upcoming() 覆盖，避免重复
                dedupe = sp["date"] + ":" + sp["label"]
                if dedupe in seen_fest:
                    continue
                seen_fest.add(dedupe)
                d = special_days.parse_ymd(sp["date"])
                if d is None:
                    continue
                left = (d - now.date()).days
                if left not in windows:
                    continue
                key = "fest:%s:%d" % (dedupe, left)
                if already(key):
                    continue
                title = "🐾 快到 %s 啦" % sp["label"] if left else "💖 今天是 %s！" % sp["label"]
                when = "还有 %d 天（%s）" % (left, sp["date"]) if left else sp["date"]
                push_system_notice("a", title, when, "view:month")
                push_system_notice("b", title, when, "view:month")
                mark(key)
                sent += 1
    return sent


def month_push_target(now, last_pushed):
    """月底 21:00 之后推当月；月底整晚离线时 1~3 号补推上个月。已推过则返回 None。"""
    today = now.date()
    month_end = _month_bounds(today.strftime("%Y-%m"))[1]
    if today == month_end and now.hour >= 21:
        target = today.strftime("%Y-%m")
    elif now.day <= 3 and now.hour >= 9:
        target = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    else:
        return None
    return None if last_pushed == target else target


def cron_summary_worker():
    """后台定时任务：只在每月月底推送一次月度胶囊；持久化状态防重启重复推送。"""
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            last_month_pushed = get_setting("cron_last_month_pushed", "")

            # 1. 每月月底（最后一天 21:00 起 / 次月 1~3 号补推）推送一次月度胶囊
            target_month = month_push_target(now, last_month_pushed)
            if target_month:
                push_month_summary(target_month)
                set_setting("cron_last_month_pushed", target_month)
                print(f"[Cron] 月度胶囊推送完成: {target_month}", flush=True)

            # 2. 每天 09:00 纪念日/节日提醒（引擎内部按事件去重，重启不重推）
            last_anniv_pushed = get_setting("cron_last_anniv_pushed", "")
            if now.hour >= 9 and last_anniv_pushed != today_str:
                n = anniv_reminder_pass(now)
                set_setting("cron_last_anniv_pushed", today_str)
                if n:
                    print(f"[Cron] 纪念日提醒推送 {n} 条: {today_str}", flush=True)

            # Owner-only generic private reminder; never forwarded to external push services.
            with db_transaction() as conn:
                cycle.reminder_pass(conn, now)

            # 4. 定期轻量自维护：每 10 分钟回收过期限流字典并执行 SQLite WAL checkpoint，每日凌晨清理回收站
            if now.minute % 10 == 0 and now.second < 30:
                cleanup_fails()
                wal_checkpoint()
            if now.hour == 4 and now.minute == 0 and now.second < 30:
                cleanup_trash()

        except Exception as e:
            sys.stderr.write(f"[Cron Worker Error]: {e}\n")
        time.sleep(30)


def main():
    global SECRET
    SECRET = load_secret()
    get_page_html()  # 启动即校验模板存在，缺失直接拒绝启动而非线上 500
    init_db()
    threading.Thread(target=cron_summary_worker, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("sched-share listening on 0.0.0.0:%d, data dir %s" % (PORT, DATA_DIR), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
