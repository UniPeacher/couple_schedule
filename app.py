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
        "temperature": 0.8,
        "max_tokens": 800
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
        with urllib.request.urlopen(req, timeout=25) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            choices = res_json.get("choices") or []
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        sys.stderr.write(f"[LLM API Error]: {e}\n")
    return None


def generate_period_summary(summary_type="day", force_refresh=False):
    today = date.today()
    users = get_users()
    name_a = users["a"]["name"]
    name_b = users["b"]["name"]

    # 缓存键：按日/周/月为单位
    if summary_type == "day":
        cache_key = f"day_{today.isoformat()}"
    elif summary_type == "week":
        mon = monday_of(today)
        cache_key = f"week_{mon.isoformat()}"
    else:
        cache_key = f"month_{today.strftime('%Y-%m')}"

    if not force_refresh:
        with LOCK:
            cached = CONN.execute("SELECT summary_json FROM summary_cache WHERE cache_key=?", (cache_key,)).fetchone()
            if cached:
                try:
                    return json.loads(cached["summary_json"])
                except Exception:
                    pass

    system_prompt = (
        "你是一对恩爱甜蜜的年轻情侣专属的AI爱情管家，你的角色是'线条小狗'（小金毛与小白狗的化身）。\n"
        "你的语气极其温暖、俏皮、可爱、治愈，喜欢用小狗的口吻（如摇尾巴、汪汪、贴贴、小狗爪印、小零食）表达细腻的关心。\n"
        "请根据下面提供的今日/本周/本月情侣两人的真实生活轨迹数据，写一篇富有陪伴感、深情又生动的时光小信或总结。\n"
        "要求：\n"
        "1. 结合两人各自的具体日程与打卡，夸夸各自的认真努力；\n"
        "2. 抓住两人的空闲贴贴时光、手账细节或留言小纸条，写出专属于他们的小温馨；\n"
        "3. 分段自然（2~3段），适度搭配小狗和爱心 Emoji，字数在 200~320 字左右，不要写得像机械公文，要像手写的温度情书！"
    )

    if summary_type == "day":
        target_date = today
        dt_str = target_date.isoformat()
        w_payload = week_payload(dt_str)
        day_info = None
        for d in w_payload["days"]:
            if d["date"] == dt_str:
                day_info = d
                break
        
        free_minutes = sum((e - s) for s, e in (day_info["free"] if day_info else []))
        free_hours = round(free_minutes / 60, 1)
        evs_a = day_info["users"]["a"]["events"] if day_info else []
        evs_b = day_info["users"]["b"]["events"] if day_info else []

        with LOCK:
            diaries = CONN.execute("SELECT * FROM diaries WHERE date=?", (dt_str,)).fetchall()
            comments = CONN.execute(
                "SELECT c.*, u.name FROM comments c LEFT JOIN users u ON u.uid=c.uid "
                "WHERE c.created LIKE ? ORDER BY c.id", (f"{dt_str}%",)
            ).fetchall()

        title = f"🌅 俩汪今日晚安总结 · {dt_str}"
        stats = [
            {"k": "共同空闲时长", "v": f"{free_hours}h"},
            {"k": "今日日程总数", "v": f"{len(evs_a) + len(evs_b)}项"},
            {"k": "手账足迹", "v": f"{len(diaries)}篇"},
            {"k": "贴心留言", "v": f"{len(comments)}条"},
        ]

        # 尝试调用 LLM 生成深度情感小信
        user_prompt = (
            f"【今日数据档案 ({dt_str})】\n"
            f"- 伴侣：{name_a}（小金毛）与 {name_b}（小白狗）\n"
            f"- 今日两人重合空闲时长：{free_hours} 小时\n"
            f"- {name_a} 的日程安排：{[e['title'] for e in evs_a] or '今天放假/自主安排'}\n"
            f"- {name_b} 的日程安排：{[e['title'] for e in evs_b] or '今天放假/自主安排'}\n"
            f"- 今日手账游玩记录：{[d['title'] + '（' + (d['mood'] or '') + '）' for d in diaries] or '暂无新足迹'}\n"
            f"- 今日双方互动留言：{[c['name'] + '说:' + c['text'] for c in comments] or '今日都在专注各自事务，心里挂念着对方'}\n\n"
            f"请以线条小狗第一人称，为他们写一封今晚温暖治愈的晚安心语。"
        )
        ai_content = call_llm_api(system_prompt, user_prompt)

        if ai_content:
            res_content = ai_content
        else:
            lines = [f"亲爱的小窝主人，今天也辛苦啦！🐾"]
            if free_hours > 0:
                lines.append(f"💕 今日默契贴贴时长：{free_hours} 小时（共同空闲）！")
            else:
                lines.append("💤 今天各自都有在认真奔波，晚上记得早点贴贴休息哦！")
            if evs_a or evs_b:
                lines.append(f"📋 今日足迹：{name_a} 完成了 {len(evs_a)} 项日程，{name_b} 完成了 {len(evs_b)} 项日程。")
            if diaries:
                d_titles = "、".join([d["title"] for d in diaries])
                lines.append(f"📔 今日手账更新了：《{d_titles}》，留下了美好的瞬间！✨")
            if comments:
                lines.append(f"💬 今天小纸条留言互动了 {len(comments)} 次，心里都在挂念着对方呢～")
            lines.append("🌙 无论今天遇到什么，小狗都最喜欢你啦。晚安，明天继续加油！🦴")
            res_content = "\n\n".join(lines)

        res = {"title": title, "content": res_content, "stats": stats, "is_ai": bool(ai_content)}

    elif summary_type == "week":
        # 本周统计
        w_payload = week_payload(today.isoformat())
        total_free_mins = 0
        total_evs = 0
        mon_str = w_payload["days"][0]["date"]
        sun_str = w_payload["days"][-1]["date"]
        for d in w_payload["days"]:
            total_free_mins += sum((e - s) for s, e in d["free"])
            total_evs += len(d["users"]["a"]["events"]) + len(d["users"]["b"]["events"])
        total_free_hours = round(total_free_mins / 60, 1)

        with LOCK:
            diaries = CONN.execute("SELECT * FROM diaries WHERE date >= ? AND date <= ?", (mon_str, sun_str)).fetchall()
            comments = CONN.execute(
                "SELECT count(*) as cnt FROM comments WHERE created >= ? AND created <= ?",
                (f"{mon_str} 00:00", f"{sun_str} 23:59")
            ).fetchone()
            n_cmts = comments["cnt"] if comments else 0

        title = f"💌 俩汪本周心动周报 · ({mon_str} ~ {sun_str})"
        stats = [
            {"k": "本周贴贴总长", "v": f"{total_free_hours}h"},
            {"k": "共同日程打卡", "v": f"{total_evs}项"},
            {"k": "新增游玩手账", "v": f"{len(diaries)}篇"},
            {"k": "互动留言小纸条", "v": f"{n_cmts}条"},
        ]

        user_prompt = (
            f"【本周爱情周报档案 ({mon_str} ~ {sun_str})】\n"
            f"- 伴侣：{name_a}（小金毛）与 {name_b}（小白狗）\n"
            f"- 整周两人重合贴贴总时长：{total_free_hours} 小时\n"
            f"- 两人并肩完成的课业与日程打卡总计：{total_evs} 项\n"
            f"- 本周手账本记录数：{len(diaries)} 篇\n"
            f"- 纸条留言互动次数：{n_cmts} 条\n\n"
            f"请以线条小狗第一人称，为他们写一封充满爱意、总结这一周并展望下一周的周报小情信。"
        )
        ai_content = call_llm_api(system_prompt, user_prompt)

        if ai_content:
            res_content = ai_content
        else:
            lines = [
                f"叮咚！这一周俩汪的默契生活报告出炉啦~ 🐾",
                f"💖 本周俩人共同重叠贴贴空闲时间高达 {total_free_hours} 小时！陪伴是最长情的告白。",
                f"🎒 这一周两人一共并肩完成了 {total_evs} 项课业与日程，每一个努力的瞬间都闪闪发光。",
                f"📷 手账本里新增了 {len(diaries)} 篇游玩回忆，互动留言小纸条 {n_cmts} 条。",
                "✨ 下周又是全新的七天，俩汪继续认真生活，努力奔向彼此吧！💕"
            ]
            res_content = "\n\n".join(lines)

        res = {"title": title, "content": res_content, "stats": stats, "is_ai": bool(ai_content)}

    elif summary_type == "month":
        # 本月胶囊
        ym = today.strftime("%Y-%m")
        with LOCK:
            diaries = CONN.execute("SELECT * FROM diaries WHERE date LIKE ? ORDER BY date", (f"{ym}%",)).fetchall()
            photos = CONN.execute(
                "SELECT count(*) as cnt FROM diary_photos dp JOIN diaries d ON d.id=dp.diary_id WHERE d.date LIKE ?",
                (f"{ym}%",)
            ).fetchone()
            n_photos = photos["cnt"] if photos else 0

            moods = {}
            for d in diaries:
                m = d["mood"] or "💖 幸福贴贴"
                moods[m] = moods.get(m, 0) + 1
            top_mood = sorted(moods.items(), key=lambda x: x[1], reverse=True)[0][0] if moods else "🥰 幸福贴贴"

        title = f"📔 俩汪月度时光胶囊 · {today.year}年{today.month}月"
        stats = [
            {"k": "本月手账日记", "v": f"{len(diaries)}篇"},
            {"k": "拍立得照片", "v": f"{n_photos}张"},
            {"k": "本月代表心情", "v": top_mood.split(" ")[0]},
            {"k": "相伴日子", "v": "30天+"},
        ]

        user_prompt = (
            f"【月度时光胶囊档案 ({ym})】\n"
            f"- 伴侣：{name_a}（小金毛）与 {name_b}（小白狗）\n"
            f"- 整月留下的手账日记：{len(diaries)} 篇\n"
            f"- 拍下的拍立得合照回忆：{n_photos} 张\n"
            f"- 本月最频繁的心情贴纸：{top_mood}\n\n"
            f"请以线条小狗第一人称，为他们写一封感动浪漫、纪念这个月点点滴滴的时光胶囊序言。"
        )
        ai_content = call_llm_api(system_prompt, user_prompt)

        if ai_content:
            res_content = ai_content
        else:
            lines = [
                f"岁月漫漫，有你常在。这里是属于你们的 {today.month} 月时光胶囊！🐾",
                f"✨ 这个月你们一共踏出了足迹，写下了 {len(diaries)} 篇手账日记，定格了 {n_photos} 张拍立得照片！",
                f"🌤️ 本月最高频心动贴纸是【{top_mood}】，充满着甜蜜与治愈。",
                "🐾 时光会走远，但爱与照片永远留在小窝里。下个月也要创造更多回忆呀！💖"
            ]
            res_content = "\n\n".join(lines)

        res = {"title": title, "content": res_content, "stats": stats, "is_ai": bool(ai_content)}

    # 存入缓存
    try:
        with LOCK:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            CONN.execute(
                "INSERT INTO summary_cache(cache_key, summary_json, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(cache_key) DO UPDATE SET summary_json=excluded.summary_json, updated_at=excluded.updated_at",
                (cache_key, json.dumps(res, ensure_ascii=False), now_str)
            )
    except Exception as e:
        sys.stderr.write(f"[Cache save error]: {e}\n")

    return res

def push_summary_to_both(summary_type="day"):
    summary = generate_period_summary(summary_type)
    title = summary["title"]
    content = summary["content"] + "\n\n【数据概览】\n" + "\n".join([f"· {s['k']}: {s['v']}" for s in summary["stats"]])
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

def send_ntfy_push(target_uid, title, content, target_action=""):
    if not target_uid:
        return
    def _do_send():
        try:
            topic = f"{NTFY_TOPIC_PREFIX}{target_uid}"
            url = f"http://127.0.0.1:8075/{topic}"
            headers = {
                "Title": title.encode("utf-8"),
                "Priority": "4",
                "Tags": "dog,heart",
            }
            if target_action:
                headers["X-Click"] = f"schedule://open?action={target_action}"
                headers["Click"] = f"schedule://open?action={target_action}"
            req = urllib.request.Request(url, data=content.encode("utf-8"), headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=5)
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


def init_db():
    global CONN
    os.makedirs(DATA_DIR, exist_ok=True)
    CONN = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False, isolation_level=None)
    CONN.row_factory = sqlite3.Row
    CONN.execute("PRAGMA journal_mode=WAL")
    CONN.execute("PRAGMA busy_timeout=15000")
    with LOCK:
        CONN.executescript(SCHEMA)
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
            CONN.execute("""
            CREATE TABLE IF NOT EXISTS wishes(
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              creator_uid  TEXT NOT NULL,
              title        TEXT NOT NULL,
              category     TEXT DEFAULT 'life',
              priority     INTEGER DEFAULT 0,
              note         TEXT DEFAULT '',
              is_done      INTEGER DEFAULT 0,
              done_at      TEXT DEFAULT '',
              created_at   TEXT NOT NULL)
            """)
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
                if ev["uid"] != u_id or not occurs_on(ev, d, u["week1"]):
                    continue
                evs.append({
                    "id": ev["id"], "title": ev["title"], "location": ev["location"],
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
        "users": {u_id: {"name": u["name"], "week1": u["week1"], "wx_uid": u.get("wx_uid", "")} for u_id, u in users.items()},
        "weeknums": {u_id: week_num(u["week1"], mon) for u_id, u in users.items()},
        "settings": {
            "window_start": m2t(ws), "window_end": m2t(we), "min_gap": min_gap,
            "llm_api_base": get_setting("llm_api_base", ""),
            "llm_api_key": get_setting("llm_api_key", ""),
            "llm_model": get_setting("llm_model", "deepseek-chat")
        },
        "days": out_days,
        "anniversaries": get_anniversaries(),
        "unread_count": get_unread_notifications_count(uid),
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
  --bg-dot: #edd8c4;
  --bg-card: #fffefc;
  --bg-card-subtle: #fff8f0;
  --bg-modal: #fffefc;
  --line-strong: #eddcc8;
  --line-subtle: #f4e8dc;
  --ink-primary: #3d2f25;
  --ink-secondary: #5a4638;
  --ink-muted: #877464;
  --ink-light: #b4a394;
  --input-bg: #ffffff;
  --input-border: #eddcc8;

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

/* 🌙 深色模式 / 黑夜模式 */
html.dark{
  --bg-page: #15181e;
  --bg-dot: #252b36;
  --bg-card: #1e232d;
  --bg-card-subtle: #242a35;
  --bg-modal: #1e232d;
  --line-strong: #333c4c;
  --line-subtle: #28303e;
  --ink-primary: #f1f4f8;
  --ink-secondary: #c9d2de;
  --ink-muted: #8d9bb0;
  --ink-light: #5d6b80;
  --input-bg: #181c24;
  --input-border: #3b4557;

  /* 暗色调下保持小金毛与小白狗的高对比可读性 */
  --dog-a: #fbbf24;
  --dog-a-hover: #f59e0b;
  --dog-a-bg: #2d2417;
  --dog-a-bd: #684a1e;
  --dog-a-badge: #382c1b;
  --dog-a-text: #fde68a;

  --dog-b: #fb7185;
  --dog-b-hover: #f43f5e;
  --dog-b-bg: #2d1822;
  --dog-b-bd: #6e2439;
  --dog-b-badge: #3d1b27;
  --dog-b-text: #fecdd3;

  --free-accent: #34d399;
  --free-bg: #142a22;
  --free-bd: #1f543f;
  --free-text: #a7f3d0;

  --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.4);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.5);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.65);
}

*{box-sizing:border-box;margin:0;padding:0}
html,body{
  -webkit-tap-highlight-color: transparent !important;
  -webkit-focus-ring-color: transparent !important;
  outline: none !important;
}
body{
  font-family:var(--font-main);
  background-color:var(--bg-page);
  background-image:radial-gradient(var(--bg-dot) 1.2px, transparent 1.2px);
  background-size:22px 22px;
  color:var(--ink-primary);
  line-height:1.55;
  padding-bottom:100px;
  -webkit-tap-highlight-color:transparent;
  min-height:100vh;
  overscroll-behavior-y: none;
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
  background:var(--bg-card);
  border-bottom:1.5px solid var(--line-strong);
  box-shadow:var(--shadow-sm);
  contain:layout style paint;
}

/* 底部萌系导航栏 */
.bottom-nav{
  position:fixed;bottom:12px;left:50%;transform:translateX(-50%);
  z-index:45;width:calc(100% - 24px);max-width:440px;
  background:var(--bg-card);border:1.5px solid var(--line-strong);
  border-radius:28px;box-shadow:var(--shadow-lg);
  display:flex;align-items:center;justify-content:space-around;
  padding:6px 8px;backdrop-filter:blur(10px);
}
.b-tab{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  flex:1;cursor:pointer;padding:4px 0;border-radius:18px;
  color:var(--ink-muted);transition:all .18s ease;user-select:none;gap:2px;
}
.b-tab .b-ico{font-size:18px;line-height:1;position:relative;display:inline-flex;align-items:center;justify-content:center}
.b-tab .b-txt{font-size:11px;font-weight:700;line-height:1}
.b-tab:hover{color:var(--ink-primary);background:var(--bg-card-subtle)}
.b-tab.on{
  color:var(--dog-a-text);background:var(--dog-a-bg);
}
.b-tab.on .b-txt{font-weight:800}

/* 底部导航未读角标 */
.b-badge{
  position:absolute;top:-6px;right:-10px;
  background:#ff4757;color:#ffffff;
  font-size:9.5px;font-weight:900;line-height:1;
  padding:2.5px 4.5px;border-radius:10px;
  min-width:15px;height:14px;
  box-sizing:border-box;
  display:inline-flex;align-items:center;justify-content:center;
  box-shadow:0 2px 5px rgba(255,71,87,0.4);
  border:1.5px solid var(--bg-card);
  pointer-events:none;
}

/* 消息中心卡片样式 */
.msg-card{
  background:var(--bg-card);border:1.5px solid var(--line-strong);border-radius:16px;
  padding:12px 14px;margin-bottom:10px;box-shadow:var(--shadow-sm);
  cursor:pointer;transition:all .18s ease;display:flex;flex-direction:column;gap:6px;
  position:relative;
}
.msg-card:hover{
  border-color:var(--dog-a-bd);transform:translateY(-1px);box-shadow:var(--shadow-md);
}
.msg-card.unread{
  border-color:var(--dog-a-bd);background:var(--dog-a-bg);
  box-shadow:0 2px 10px rgba(234,138,21,0.08);
}
.msg-card-top{
  display:flex;align-items:center;justify-content:space-between;gap:8px;
}
.msg-card-title{
  font-size:13.5px;font-weight:800;color:var(--ink-primary);display:flex;align-items:center;gap:6px;
}
.msg-unread-tag{
  background:#ff4757;color:#fff;font-size:9px;font-weight:800;padding:1px 5px;border-radius:6px;
}
.msg-card-time{
  font-size:11px;color:var(--ink-muted);font-weight:600;flex-shrink:0;
}
.msg-card-body{
  font-size:12.5px;color:var(--ink-secondary);line-height:1.5;word-break:break-word;
}
.msg-card-footer{
  display:flex;align-items:center;justify-content:space-between;margin-top:2px;
}
.msg-action-hint{
  font-size:11px;color:var(--dog-a-text);font-weight:700;
}
.msg-btn-sm{
  border:1px solid var(--line-strong);background:var(--bg-card);border-radius:8px;
  font-size:10.5px;font-weight:700;color:var(--ink-secondary);padding:2px 7px;
  cursor:pointer;transition:all .15s ease;
}
.msg-btn-sm:hover{
  background:var(--bg-card-subtle);border-color:var(--dog-a-bd);color:var(--dog-a-text);
}
.msg-btn-sm.del:hover{
  border-color:#ff4757;color:#ff4757;
}

/* 心愿备忘录样式 */
.wish-card{
  background:var(--bg-card);border:1.5px solid var(--line-strong);border-radius:18px;
  padding:14px 16px;margin-bottom:12px;box-shadow:var(--shadow-sm);
  transition:all .18s ease;display:flex;align-items:flex-start;gap:12px;position:relative;
}
.wish-card:hover{
  border-color:var(--dog-a-bd);box-shadow:var(--shadow-md);transform:translateY(-1px);
}
.wish-card.priority{
  border-color:var(--dog-a-bd);background:var(--bg-card-subtle);
  box-shadow:0 3px 12px rgba(234,138,21,0.09);
}
.wish-card.done{
  opacity:0.75;background:var(--free-bg,#f0fdf4);border-color:var(--free-bd,#bbf7d0);
}
.wish-card.done .wish-title{
  text-decoration:line-through;color:var(--ink-muted);
}
.wish-check-btn{
  width:28px;height:28px;border-radius:50%;border:2px solid var(--line-strong);
  background:var(--bg-card);display:flex;align-items:center;justify-content:center;
  font-size:13px;cursor:pointer;flex-shrink:0;margin-top:2px;transition:all .18s ease;user-select:none;
}
.wish-check-btn:hover{
  border-color:var(--free-accent,#10b981);transform:scale(1.1);
}
.wish-card.done .wish-check-btn{
  background:var(--free-accent,#10b981);border-color:var(--free-accent,#10b981);color:#fff;
}
.wish-content{
  flex:1;min-width:0;display:flex;flex-direction:column;gap:5px;
}
.wish-title-row{
  display:flex;align-items:center;gap:6px;flex-wrap:wrap;
}
.wish-title{
  font-size:14.5px;font-weight:800;color:var(--ink-primary);line-height:1.4;
}
.wish-cat-tag{
  font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:8px;
  background:var(--bg-card-subtle);color:var(--ink-secondary);border:1px solid var(--line-subtle);
}
.wish-fire-tag{
  font-size:10px;font-weight:800;padding:1px 6px;border-radius:6px;
  background:#fee2e2;color:#ef4444;border:1px solid #fca5a5;
}
.wish-note{
  font-size:12px;color:var(--ink-muted);line-height:1.45;word-break:break-word;
}
.wish-meta-row{
  display:flex;align-items:center;justify-content:space-between;margin-top:4px;
  font-size:11px;color:var(--ink-muted);flex-wrap:wrap;gap:6px;
}
.wish-progress-box{
  background:var(--bg-card);border:1.5px solid var(--line-strong);border-radius:18px;
  padding:14px 18px;margin-bottom:14px;box-shadow:var(--shadow-sm);
}
.wish-progress-bar{
  height:10px;border-radius:6px;background:var(--line-subtle);overflow:hidden;margin-top:8px;
}
.wish-progress-fill{
  height:100%;border-radius:6px;background:linear-gradient(90deg, var(--dog-a), var(--free-accent,#10b981));
  transition:width .4s ease;
}

/* 顶部导航 */
.topbar{
  padding:8px 14px;
  display:flex;align-items:center;justify-content:space-between;
  flex-wrap:nowrap;white-space:nowrap;
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
.top-actions{
  display:flex;align-items:center;gap:6px;flex-shrink:0;
}

/* 用户状态徽章与按钮 */
.user-pill{
  display:inline-flex;align-items:center;gap:4px;
  font-size:11.5px;font-weight:700;
  padding:3px 10px;border-radius:var(--radius-pill);
  border:1px solid var(--line-strong);
  background:var(--bg-card);
}
.user-pill.a{background:var(--dog-a-bg);border-color:var(--dog-a-bd);color:var(--dog-a-text)}
.user-pill.b{background:var(--dog-b-bg);border-color:var(--dog-b-bd);color:var(--dog-b-text)}

.p-btn{
  border:1.5px solid var(--line-strong);background:var(--bg-card);
  border-radius:var(--radius-pill);padding:4px 11px;
  font-size:12px;font-weight:600;color:var(--ink-primary);
  cursor:pointer;display:inline-flex;align-items:center;gap:3px;
}
.p-btn:hover{background:var(--bg-card-subtle);border-color:var(--line-strong)}
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
.p-btn.danger{color:#ef4444;border-color:#fca5a5;background:transparent}
.p-btn.danger:hover{background:rgba(239,68,68,0.15)}

/* 周导航控制器 */
.weekbar-wrap{
  max-width:1120px;margin:0 auto;padding:0 10px 5px;
}
.weekbar-card{
  background:var(--bg-card);border:1px solid var(--line-subtle);
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
.anniv-capsule.love{background:var(--dog-b-bg);border-color:var(--dog-b-bd);color:var(--dog-b-text)}
.anniv-capsule.birthday{background:var(--dog-a-bg);border-color:var(--dog-a-bd);color:var(--dog-a-text)}
.anniv-capsule.countdown{background:var(--free-bg);border-color:var(--free-bd);color:var(--free-text)}
.anniv-add-btn{
  display:inline-flex;align-items:center;gap:4px;flex-shrink:0;
  border:1px dashed var(--line-strong);border-radius:var(--radius-pill);
  padding:3px 9px;font-size:11px;font-weight:600;color:var(--ink-muted);
  background:transparent;cursor:pointer;transition:all .15s ease;
}
.anniv-add-btn:hover{background:#fff;color:var(--ink-primary);border-color:var(--dog-a)}

/* 模式切换胶囊 (课表 / 手账) */
.view-seg{
  display:inline-flex;align-items:center;background:var(--bg-card-subtle);
  padding:2px;border-radius:var(--radius-pill);border:1px solid var(--line-subtle);
  margin-left:4px;
}
.view-seg-opt{
  padding:3px 9px;border-radius:var(--radius-pill);font-size:11.5px;font-weight:700;
  cursor:pointer;color:var(--ink-muted);transition:all .15s ease;user-select:none;
}
.view-seg-opt.on{
  background:var(--bg-card);color:var(--dog-a);box-shadow:0 1px 3px rgba(0,0,0,0.08);
}

/* 统一主视图页面容器 */
.page-container{
  max-width:800px;margin:0 auto;padding:12px 14px 100px;
}
.page-header-card{
  background:var(--bg-card);border:1.5px solid var(--line-strong);border-radius:22px;
  padding:14px 18px;box-shadow:var(--shadow-sm);margin-bottom:14px;
  display:flex;align-items:center;justify-content:space-between;gap:12px;
}
.page-header-title{
  font-size:17px;font-weight:900;color:var(--ink-primary);display:flex;align-items:center;gap:6px;
}
.page-header-desc{
  font-size:12px;font-weight:700;color:var(--dog-a);margin-top:2px;
}
.page-main-card{
  background:var(--bg-card);border:1.5px solid var(--line-strong);border-radius:22px;
  padding:18px 20px;box-shadow:var(--shadow-sm);margin-bottom:14px;
}

/* 月历手账视图 */
.month-cal-wrap{
  max-width:1120px;margin:0 auto;padding:6px 12px 24px;
}
.month-topcard{
  background:var(--bg-card);border:1.5px solid var(--line-strong);border-radius:20px;
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
  background:var(--bg-card);border:1.5px solid var(--line-strong);border-radius:16px;
  min-height:92px;padding:6px 8px;display:flex;flex-direction:column;
  box-shadow:var(--shadow-sm);cursor:pointer;transition:all .18s ease;
  position:relative;overflow:hidden;
}
.month-cell:hover{
  border-color:var(--dog-a);transform:translateY(-2px);box-shadow:var(--shadow-md);
}
.month-cell.other-month{
  opacity:0.35;background:var(--bg-page);
}
.month-cell.today{
  border:2px solid var(--dog-a);background:var(--dog-a-bg);
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
  background:var(--bg-card);padding:6px 6px 14px;border:1px solid var(--line-strong);
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
  background:var(--bg-card);border:1.5px solid var(--line-strong);border-radius:16px;
  padding:12px;margin-bottom:12px;box-shadow:var(--shadow-sm);
  color:var(--ink-primary);
}

/* 日历区域 */
main{max-width:1120px;margin:0 auto;padding:6px 12px 16px}
.calwrap{
  overflow-x:auto;-webkit-overflow-scrolling:touch;
  border-radius:24px;border:2px solid var(--line-strong);
  box-shadow:var(--shadow-md);background:var(--bg-card);
}
.cal{
  min-width:1020px;display:grid;grid-template-columns:58px repeat(7,minmax(136px,1fr));
  position:relative;background:var(--bg-card);
}

/* 左上角与时间轴 */
.corner{
  background:var(--bg-card-subtle);border-right:1.5px solid var(--line-strong);
  border-bottom:2px solid var(--line-strong);
  position:sticky;left:0;top:0;z-index:10;
  display:flex;align-items:center;justify-content:center;
  font-size:11px;font-weight:700;color:var(--ink-light);
}
.axcol{
  position:sticky;left:0;z-index:8;
  background:var(--bg-card-subtle);border-right:1.5px solid var(--line-strong);
}
.axh{
  position:absolute;right:8px;transform:translateY(-50%);
  font-size:11px;font-weight:700;color:var(--ink-light);
  font-variant-numeric:tabular-nums;user-select:none;
}

/* 星期表头 */
.dh{
  background:var(--bg-card);border-right:1px solid var(--line-subtle);
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
  background:var(--bg-card);border:1px solid var(--line-strong);
  border-radius:var(--radius-pill);padding:0 4px;
  color:var(--ink-muted);line-height:1.3;
  box-shadow:0 1px 2px rgba(0,0,0,0.05);
}
.ev .del{
  position:absolute;top:-5px;right:-3px;
  width:18px;height:18px;border-radius:50%;
  border:1px solid #fecaca;background:var(--bg-card);
  color:#ef4444;font-size:12px;font-weight:bold;
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
  position:fixed;right:20px;bottom:78px;
  background:linear-gradient(135deg, var(--dog-a) 0%, #ff85a2 100%);
  color:#fff;border:none;border-radius:var(--radius-pill);
  padding:11px 20px;font-size:14px;font-weight:800;
  box-shadow:0 8px 24px rgba(255,96,136,0.38);
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
  display:none;align-items:flex-start;justify-content:center;
  z-index:50;padding:26px 12px;overflow-y:auto;
}
.overlay.show{display:flex}
.modal{
  background:var(--bg-modal);border:2px solid var(--line-strong);
  border-radius:24px;max-width:460px;width:100%;
  padding:22px 24px;box-shadow:var(--shadow-lg);
  position:relative;animation:modalPop .16s ease-out;
  transform:translateZ(0);backface-visibility:hidden;
  color:var(--ink-primary);
}
@keyframes modalPop{from{opacity:0;transform:scale(0.97) translateZ(0)}to{opacity:1;transform:scale(1) translateZ(0)}}

.mhead{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:14px;border-bottom:1.5px dashed var(--line-strong);
  padding-bottom:12px;
}
.mhead-left{display:flex;align-items:center;gap:8px}
.mhead h3{font-size:17px;font-weight:800;color:var(--ink-primary)}
.mhead-img{width:46px;height:auto;flex:none}
html:not(.dark) .mhead-img{mix-blend-mode:multiply}

.field{margin-bottom:12px}
.field label{
  display:block;font-size:12.5px;font-weight:700;
  color:var(--ink-muted);margin-bottom:5px;
}
.field input,.field select,.field textarea{
  width:100%;border:1.5px solid var(--input-border);
  border-radius:12px;padding:9px 12px;
  font-size:14px;font-family:inherit;background:var(--input-bg);
  color:var(--ink-primary);transition:border-color .2s, box-shadow .2s;
}
.field input:focus,.field select:focus,.field textarea:focus{
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
  user-select:none;background:var(--bg-card);color:var(--ink-muted);
  transition:all .18s ease;display:flex;align-items:center;justify-content:center;gap:4px;
}
.seg .opt:hover{background:var(--bg-card-subtle);border-color:var(--line-strong)}
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
  background:var(--bg-card-subtle);border:1.5px solid var(--line-strong);
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
  background:var(--bg-card);color:var(--ink-muted);cursor:pointer;
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
  background:var(--bg-card-subtle);border:1.5px solid var(--line-strong);
  border-radius:14px;padding:9px 12px;font-size:13px;
  box-shadow:0 1px 4px rgba(0,0,0,0.05);
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
  padding:12px 8px;cursor:pointer;background:var(--bg-card);
  transition:all .2s ease;display:flex;flex-direction:column;
  align-items:center;gap:4px;user-select:none;
}
.login-card .ico{font-size:24px}
.login-card .name{font-size:13.5px;font-weight:800;color:var(--ink-primary)}
.login-card .desc{font-size:11px;color:var(--ink-light);font-weight:600}
.login-card:hover{background:var(--bg-card-subtle);border-color:var(--line-strong)}
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
  .topbar{padding:5px 6px;gap:4px}
  .brand-img{height:18px}
  .brand-title{font-size:13px;letter-spacing:0}
  .view-seg{margin-left:1px;padding:1px}
  .view-seg-opt{padding:2px 5px;font-size:10px}
  .icon-btn{padding:3px 6px;font-size:12px;border-radius:10px}
  .btn-txt{display:none} /* 移动端仅保留图标，省出横向空间 */
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
  .fab{right:16px;bottom:76px;padding:9px 16px;font-size:13px}
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
// 恢复深色模式偏好设置
(function(){
  try {
    var savedDark = localStorage.getItem("sched_dark_mode");
    var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    if (savedDark === "1" || (savedDark === null && prefersDark)){
      document.documentElement.classList.add("dark");
    }
  } catch(e){}
})();

var CURRENT_VIEW = "week"; // "week" | "month" | "summary" | "messages" | "wishes" | "anniv" | "settings"
var MONTH_ANCHOR = todayISO().slice(0, 7); // YYYY-MM
var MONTH_DATA = null;
var UNREAD_COUNT = 0;
var MESSAGES_DATA = [];
var curMsgFilter = "all";
var WISHES_DATA = null;
var curWishFilter = "all";
var addWishUid = "a";
var addWishCat = "travel";
var addWishPrio = 0;
var diaryPhotosToUpload = []; // base64 list
var curEditingDiary = null;
var curEditingEventId = null;
var activeDiaryDate = "";
var addUid = "a", addRep = "weekly", loginUid = "a", annivType = "love";

function setUnreadCount(count){
  UNREAD_COUNT = Math.max(0, parseInt(count) || 0);
  document.querySelectorAll("#bUnreadBadge, .b-badge").forEach(function(badge){
    if (UNREAD_COUNT > 0){
      badge.textContent = UNREAD_COUNT > 99 ? "99+" : UNREAD_COUNT;
      badge.style.display = "inline-flex";
    } else {
      badge.style.display = "none";
      badge.textContent = "";
    }
  });
  var unreadTab = document.querySelector('#msgFilterSeg .opt[data-v="unread"]');
  if (unreadTab){
    unreadTab.textContent = '未读' + (UNREAD_COUNT > 0 ? ' (' + UNREAD_COUNT + ')' : '');
  }
}

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

function load(opts){
  var silent = opts && opts.silent;
  if (CURRENT_VIEW === "month"){
    if (MONTH_DATA){
      render();
      if (silent) return Promise.resolve();
    }
    return api("/api/month?month=" + MONTH_ANCHOR).then(function(j){
      MONTH_DATA = j;
      if (j && j.unread_count !== undefined) setUnreadCount(j.unread_count);
      render();
    }).catch(function(e){ if (e.message !== "unauth") toast(e.message); });
  } else if (CURRENT_VIEW === "messages" || CURRENT_VIEW === "wishes"){
    render();
    if (silent) return Promise.resolve();
    return Promise.resolve();
  } else if (CURRENT_VIEW === "summary" || CURRENT_VIEW === "anniv" || CURRENT_VIEW === "settings"){
    if (DATA){
      render();
      if (silent) return Promise.resolve();
      return Promise.resolve();
    }
    return api("/api/week?date=" + ANCHOR).then(function(j){
      DATA = j;
      if (j && j.unread_count !== undefined) setUnreadCount(j.unread_count);
      render();
    }).catch(function(e){ if (e.message !== "unauth") toast(e.message); });
  } else {
    if (DATA){
      render();
      if (silent) return Promise.resolve();
    }
    return api("/api/week?date=" + ANCHOR).then(function(j){
      DATA = j;
      if (j && j.unread_count !== undefined) setUnreadCount(j.unread_count);
      render();
      // 预取月历手账数据，实现后续点击零延迟秒开
      if (!MONTH_DATA){
        api("/api/month?month=" + MONTH_ANCHOR).then(function(mj){
          MONTH_DATA = mj;
          if (mj && mj.unread_count !== undefined) setUnreadCount(mj.unread_count);
        }).catch(function(){});
      }
    }).catch(function(e){ if (e.message !== "unauth") toast(e.message); });
  }
}

/* 模态框打开与关闭（支持原生历史记录与物理/手势返回键） */
function openModal(modalId){
  var el = $(modalId);
  if (!el) return;
  el.classList.add("show");
  history.pushState({type:"modal", id:modalId}, "");
}

function closeAllModals(fromPop){
  var showed = false;
  document.querySelectorAll(".overlay.show").forEach(function(o){
    o.classList.remove("show");
    showed = true;
  });
  var pv = $("#ovPhotoViewer");
  if (pv && pv.style.display !== "none"){
    pv.style.display = "none";
    pv.classList.remove("show");
    showed = true;
  }
  return showed;
}
function renderTopBar(){
  var myIsA = ME.uid === "a";
  var myPill = '<span class="user-pill ' + ME.uid + '">' + (myIsA ? '🐶 ' : '🐾 ') + esc(ME.name) + '</span>';
  return '<div class="header-box" id="headerBox">' +
    '<header class="topbar">' +
      '<div class="brand" data-act="switch-view" data-v="week">' +
        '<img src="/img/dogheads.png" alt="线条小狗" class="brand-img">' +
        '<span class="brand-title">两人日程 🐾</span>' +
      '</div>' +
      '<div class="top-actions">' +
        myPill +
        '<button class="p-btn icon-btn" data-act="toggle-dark" id="darkToggleBtn" title="切换深色/浅色模式">' +
          (document.documentElement.classList.contains("dark") ? '☀️<span class="btn-txt"> 浅色</span>' : '🌙<span class="btn-txt"> 深色</span>') +
        '</button>' +
      '</div>' +
    '</header>' +
  '</div>';
}

function render(){
  if (!ME){ renderLogin(); } else {
    if (CURRENT_VIEW === "month") renderMonthApp();
    else if (CURRENT_VIEW === "summary") renderSummaryPage();
    else if (CURRENT_VIEW === "messages") renderMessagesPage();
    else if (CURRENT_VIEW === "wishes") renderWishesPage();
    else if (CURRENT_VIEW === "anniv") renderAnnivPage();
    else if (CURRENT_VIEW === "settings") renderSettingsPage();
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
/* =========================================================================
   独立全功能页面视图（时光简报 / 纪念日 / 设置）
   ========================================================================= */

function renderSummaryPage(){
  var html = renderTopBar() +
    '<div class="page-container">' +
      '<div class="page-header-card">' +
        '<div>' +
          '<div class="page-header-title">💌 俩汪时光简报与胶囊</div>' +
          '<div class="page-header-desc">✨ 由线条小狗 AI 陪伴记录的情侣时光小信</div>' +
        '</div>' +
        '<div style="width:44px;height:44px;flex-shrink:0"><img src="/img/dogheads.png" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div class="seg" id="summaryTabSeg" style="margin-bottom:16px">' +
          '<div class="opt' + (curSummaryTab==="day"?" on":"") + '" data-act="summary-tab" data-tab="day">🌅 今日晚安</div>' +
          '<div class="opt' + (curSummaryTab==="week"?" on":"") + '" data-act="summary-tab" data-tab="week">💌 心动周报</div>' +
          '<div class="opt' + (curSummaryTab==="month"?" on":"") + '" data-act="summary-tab" data-tab="month">📔 月度胶囊</div>' +
        '</div>' +
        '<div id="summaryContentBox" style="min-height:200px;font-size:13.5px;line-height:1.6"></div>' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:16px;flex-wrap:wrap;gap:8px;padding-top:14px;border-top:1px dashed var(--line-strong)">' +
          '<div style="display:flex;gap:8px">' +
            '<button type="button" class="p-btn pri" data-act="send-summary-wx" style="font-size:12.5px;padding:6px 14px">📲 推送到俩人微信</button>' +
            '<button type="button" class="p-btn" data-act="refresh-summary" title="让AI重新构思写一封" style="font-size:12.5px;padding:6px 14px">🔄 重新生成</button>' +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>';

  html += bottomNavHTML();
  html += modalsHTML();
  $("#app").innerHTML = html;
  loadSummary(curSummaryTab);
}

function renderMessagesPage(){
  var html = renderTopBar() +
    '<div class="page-container">' +
      '<div class="page-header-card">' +
        '<div>' +
          '<div class="page-header-title">💬 汪汪信箱 · 消息通知</div>' +
          '<div class="page-header-desc">🐾 日程变动、温馨留言与手账动态提醒</div>' +
        '</div>' +
        '<div style="width:44px;height:44px;flex-shrink:0"><img src="/img/dog-wave.png" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">' +
          '<div class="seg" id="msgFilterSeg" style="max-width:200px;margin-bottom:0">' +
            '<div class="opt' + (curMsgFilter==="all"?" on":"") + '" data-act="msg-filter" data-v="all">全部</div>' +
            '<div class="opt' + (curMsgFilter==="unread"?" on":"") + '" data-act="msg-filter" data-v="unread">未读' + (UNREAD_COUNT > 0 ? ' (' + UNREAD_COUNT + ')' : '') + '</div>' +
          '</div>' +
          '<div style="display:flex;gap:6px">' +
            '<button type="button" class="p-btn" data-act="read-all-msgs" style="font-size:11.5px;padding:4px 10px" title="将所有未读消息标记为已读">✨ 一键已读</button>' +
            '<button type="button" class="p-btn" data-act="clear-read-msgs" style="font-size:11.5px;padding:4px 10px" title="清理所有已读通知">🧹 清理已读</button>' +
            '<button type="button" class="p-btn" data-act="refresh-msgs" style="font-size:11.5px;padding:4px 8px" title="刷新消息列表">🔄</button>' +
          '</div>' +
        '</div>' +
        '<div id="messagesListContainer"></div>' +
      '</div>' +
    '</div>';

  html += bottomNavHTML();
  html += modalsHTML();
  $("#app").innerHTML = html;
  loadMessages();
}

function loadMessages(){
  var box = $("#messagesListContainer");
  if (box && (!MESSAGES_DATA || MESSAGES_DATA.length === 0)){
    box.innerHTML = '<div style="text-align:center;padding:30px 0;color:var(--ink-muted);font-weight:700">🐶 正在拉取信箱通知...</div>';
  }
  api("/api/messages").then(function(res){
    if (res && res.ok){
      MESSAGES_DATA = res.messages || [];
      setUnreadCount(res.unread_count);
      renderMessagesList();
    }
  }).catch(function(err){
    if (box) box.innerHTML = '<div style="color:red;padding:20px 0;text-align:center">加载失败：' + esc(err.message) + '</div>';
  });
}

function renderMessagesList(){
  var box = $("#messagesListContainer");
  if (!box) return;
  var list = MESSAGES_DATA || [];
  if (curMsgFilter === "unread"){
    list = list.filter(function(m){ return !m.is_read; });
  }
  if (list.length === 0){
    box.innerHTML = '<div style="text-align:center;padding:40px 10px;color:var(--ink-muted)">' +
      '<div style="width:72px;height:72px;margin:0 auto 10px;opacity:0.85"><img src="/img/dogrest.png" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '<div style="font-size:14px;font-weight:800;color:var(--ink-primary);margin-bottom:4px">' + (curMsgFilter==="unread" ? "太棒啦，所有消息都已读完汪~ 🎉" : "信箱空空如也汪~ 📭") + '</div>' +
      '<div style="font-size:12px;opacity:0.8">对方添加新日程、发表留言或更新手账时，都会第一时间在这里提醒你 🐾</div>' +
    '</div>';
    return;
  }

  box.innerHTML = list.map(function(m){
    var isUnread = !m.is_read;
    var icon = "🔔";
    if (m.title.indexOf("留言") !== -1 || m.content.indexOf("留言") !== -1) icon = "💬";
    else if (m.title.indexOf("日程") !== -1 || m.content.indexOf("日程") !== -1) icon = "🗓️";
    else if (m.title.indexOf("手账") !== -1 || m.content.indexOf("手账") !== -1) icon = "📔";
    else if (m.title.indexOf("信") !== -1 || m.title.indexOf("简报") !== -1) icon = "💌";
    else if (m.title.indexOf("测试") !== -1) icon = "🐶";

    var actionHint = "";
    if (m.target_action){
      if (m.target_action.indexOf("event:") === 0) actionHint = '<span class="msg-action-hint">查看相关日程 ➜</span>';
      else if (m.target_action.indexOf("diary:") === 0) actionHint = '<span class="msg-action-hint">查看相关手账 ➜</span>';
      else if (m.target_action.indexOf("view:summary") === 0) actionHint = '<span class="msg-action-hint">查看时光简报 ➜</span>';
      else if (m.target_action.indexOf("view:settings") === 0) actionHint = '<span class="msg-action-hint">前往设置 ➜</span>';
    }

    var timeStr = esc(m.created_at || "");

    return '<div class="msg-card' + (isUnread ? ' unread' : '') + '" data-act="open-msg" data-mid="' + m.id + '" data-target-action="' + esc(m.target_action || "") + '">' +
      '<div class="msg-card-top">' +
        '<div class="msg-card-title">' +
          '<span>' + icon + '</span>' +
          '<span>' + esc(m.title) + '</span>' +
          (isUnread ? '<span class="msg-unread-tag">NEW</span>' : '') +
        '</div>' +
        '<div class="msg-card-time">' + timeStr + '</div>' +
      '</div>' +
      '<div class="msg-card-body">' + esc(m.content) + '</div>' +
      '<div class="msg-card-footer">' +
        '<div>' + actionHint + '</div>' +
        '<div style="display:flex;gap:6px">' +
          (isUnread ? '<button type="button" class="msg-btn-sm" data-act="read-one-msg" data-mid="' + m.id + '" title="标为已读">✓ 标为已读</button>' : '') +
          '<button type="button" class="msg-btn-sm del" data-act="del-one-msg" data-mid="' + m.id + '" title="删除此消息">🗑️</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }).join("");
}

function renderWishesPage(){
  var html = renderTopBar() +
    '<div class="page-container">' +
      '<div style="margin-bottom:12px">' +
        '<div class="seg" style="margin-bottom:0">' +
          '<div class="opt" data-act="switch-view" data-v="month">📔 足迹手账</div>' +
          '<div class="opt on" data-act="switch-view" data-v="wishes">✨ 心愿备忘</div>' +
        '</div>' +
      '</div>' +

      '<div class="wish-progress-box" id="wishProgressBox">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;font-size:12.5px;font-weight:800;color:var(--ink-primary)">' +
          '<span id="wishProgressText">🐶 正在统计俩汪心愿进度...</span>' +
          '<span id="wishProgressPct" style="color:var(--dog-a-text)">0%</span>' +
        '</div>' +
        '<div class="wish-progress-bar"><div class="wish-progress-fill" id="wishProgressFill" style="width:0%"></div></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div class="seg" id="wishCatSeg" style="margin-bottom:14px;overflow-x:auto;flex-wrap:nowrap;padding-bottom:2px">' +
          '<div class="opt' + (curWishFilter==="all"?" on":"") + '" data-act="wish-filter" data-v="all">全部</div>' +
          '<div class="opt' + (curWishFilter==="travel"?" on":"") + '" data-act="wish-filter" data-v="travel">✈️ 旅行</div>' +
          '<div class="opt' + (curWishFilter==="food"?" on":"") + '" data-act="wish-filter" data-v="food">🍜 美食</div>' +
          '<div class="opt' + (curWishFilter==="movie"?" on":"") + '" data-act="wish-filter" data-v="movie">🎬 影音</div>' +
          '<div class="opt' + (curWishFilter==="life"?" on":"") + '" data-act="wish-filter" data-v="life">🏡 生活</div>' +
          '<div class="opt' + (curWishFilter==="other"?" on":"") + '" data-act="wish-filter" data-v="other">💡 其他</div>' +
          '<div class="opt' + (curWishFilter==="done"?" on":"") + '" data-act="wish-filter" data-v="done">🎉 已实现</div>' +
        '</div>' +
        '<div id="wishesListContainer"></div>' +
      '</div>' +
    '</div>' +
    '<button class="fab" data-act="open-add-wish" title="许下一个新心愿">✨ 许新愿 +</button>';

  html += bottomNavHTML();
  html += modalsHTML();
  $("#app").innerHTML = html;
  loadWishes();
}

function loadWishes(){
  var box = $("#wishesListContainer");
  if (box && (!WISHES_DATA || WISHES_DATA.length === 0)){
    box.innerHTML = '<div style="text-align:center;padding:30px 0;color:var(--ink-muted);font-weight:700">🐶 正在翻开俩汪心愿清单...</div>';
  }
  api("/api/wishes").then(function(res){
    if (res && res.ok){
      WISHES_DATA = res.wishes || [];
      updateWishStats(res.stats);
      renderWishesList();
    }
  }).catch(function(err){
    if (box) box.innerHTML = '<div style="color:red;padding:20px 0;text-align:center">加载失败：' + esc(err.message) + '</div>';
  });
}

function updateWishStats(stats){
  if (!stats) return;
  var total = stats.total || 0;
  var done = stats.done || 0;
  var pending = stats.pending || 0;
  var pct = total > 0 ? Math.round((done / total) * 100) : 0;
  var pText = $("#wishProgressText");
  var pPct = $("#wishProgressPct");
  var pFill = $("#wishProgressFill");
  if (pText){
    pText.textContent = total > 0 
      ? ("✨ 俩汪已携手完成 " + done + " 个心愿，还有 " + pending + " 个美好正在奔赴中~ 💕")
      : "还没有添加心愿哦，在下方许下你们的第一个心愿吧 🐾";
  }
  if (pPct) pPct.textContent = pct + "%";
  if (pFill) pFill.style.width = pct + "%";
}

function renderWishesList(){
  var box = $("#wishesListContainer");
  if (!box) return;
  var list = WISHES_DATA || [];
  if (curWishFilter === "done"){
    list = list.filter(function(w){ return w.is_done; });
  } else if (curWishFilter !== "all"){
    list = list.filter(function(w){ return !w.is_done && w.category === curWishFilter; });
  }

  if (list.length === 0){
    box.innerHTML = '<div style="text-align:center;padding:40px 10px;color:var(--ink-muted)">' +
      '<div style="width:72px;height:72px;margin:0 auto 10px;opacity:0.85"><img src="/img/dogrest.png" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '<div style="font-size:14px;font-weight:800;color:var(--ink-primary);margin-bottom:4px">' +
        (curWishFilter === "done" ? "还没有标记实现的心愿汪~ 继续加油！" : "此分类下暂无心愿便签汪~ 🐾") +
      '</div>' +
      '<div style="font-size:12px;opacity:0.8">点击右上角「许新心愿」记录下你们将来想一起做的事吧 ✨</div>' +
    '</div>';
    return;
  }

  var catMap = {
    travel: "✈️ 旅行",
    food: "🍜 美食",
    movie: "🎬 影音",
    life: "🏡 生活",
    other: "💡 其他"
  };

  box.innerHTML = list.map(function(w){
    var isDone = !!w.is_done;
    var catLabel = catMap[w.category] || "✨ 心愿";
    var isA = w.creator_uid === "a";
    var creatorPill = '<span class="school-badge ' + (isA ? "a" : "b") + '" style="font-size:10px;padding:1px 6px">' + (isA ? "🐶 " : "🐾 ") + esc(w.creator_name || (isA ? "小金毛" : "小白狗")) + '</span>';

    return '<div class="wish-card' + (w.priority ? ' priority' : '') + (isDone ? ' done' : '') + '">' +
      '<button type="button" class="wish-check-btn" data-act="toggle-wish" data-wid="' + w.id + '" title="' + (isDone ? "重新标记为待完成" : "打勾！实现这个心愿") + '">' +
        (isDone ? '✓' : '🐾') +
      '</button>' +
      '<div class="wish-content">' +
        '<div class="wish-title-row">' +
          '<span class="wish-cat-tag">' + catLabel + '</span>' +
          (w.priority ? '<span class="wish-fire-tag">🔥 超级想去</span>' : '') +
          '<span class="wish-title">' + esc(w.title) + '</span>' +
        '</div>' +
        (w.note ? ('<div class="wish-note">📝 ' + esc(w.note) + '</div>') : '') +
        '<div class="wish-meta-row">' +
          '<div style="display:flex;align-items:center;gap:6px">' +
            creatorPill +
            '<span>' + esc(w.created_at || "") + '</span>' +
            (isDone && w.done_at ? ('<span style="color:var(--free-text);font-weight:700"> · 🎉 ' + esc(w.done_at) + ' 达成</span>') : '') +
          '</div>' +
          '<div style="display:flex;gap:6px">' +
            (isDone ? ('<button type="button" class="msg-btn-sm" data-act="wish-to-diary" data-title="' + esc(w.title) + '" title="顺手记一篇手账纪念">📸 记手账</button>') : '') +
            '<button type="button" class="msg-btn-sm del" data-act="del-wish" data-wid="' + w.id + '" title="删除此心愿">🗑️</button>' +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>';
  }).join("");
}

function renderAnnivPage(){
  var html = renderTopBar() +
    '<div class="page-container">' +
      '<div class="page-header-card">' +
        '<div>' +
          '<div class="page-header-title">💖 纪念日与倒计时</div>' +
          '<div class="page-header-desc">🐾 记录每一个相恋心动的日子与生日</div>' +
        '</div>' +
        '<div style="width:44px;height:44px;flex-shrink:0"><img src="/img/dog-couple.png" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div style="font-size:13.5px;font-weight:800;color:var(--ink-primary);margin-bottom:12px;display:flex;align-items:center;gap:6px">' +
          '<span>📋 重要日子列表</span>' +
        '</div>' +
        '<div id="annivListContainer" style="margin-bottom:16px"></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div style="font-size:14.5px;font-weight:800;color:var(--ink-primary);margin-bottom:12px">🐾 添加新的纪念日</div>' +
        '<form id="annivForm">' +
          '<div class="field"><label>日子类型</label>' +
            '<div class="seg" id="annivTypeSeg">' +
              '<div class="opt on" data-act="anniv-type" data-v="love">💖 相恋纪念（累计天数）</div>' +
              '<div class="opt" data-act="anniv-type" data-v="birthday">🎂 生日/节日（每年倒计时）</div>' +
              '<div class="opt" data-act="anniv-type" data-v="countdown">⏳ 目标/大事件（单次倒计时）</div>' +
            '</div>' +
          '</div>' +
          '<div class="field"><label>纪念日名称</label><input id="an_title" required maxlength="60" placeholder="如: 我们相恋啦 / 菁宝生日 🎂"></div>' +
          '<div class="field"><label>日期</label><input type="date" id="an_date" required value="' + todayISO() + '"></div>' +
          '<button class="p-btn pri" style="margin-top:10px;width:100%;padding:10px;justify-content:center;font-size:14px">💖 记下这一天 🐾</button>' +
        '</form>' +
      '</div>' +
    '</div>';

  html += bottomNavHTML();
  html += modalsHTML();
  $("#app").innerHTML = html;
  renderAnnivList();
}

function renderSettingsPage(){
  var u = (DATA && DATA.users) || (MONTH_DATA && MONTH_DATA.users) || {a:{name:"小金毛"},b:{name:"小白狗"}};
  var s = (DATA && DATA.settings) || {window_start:"08:00",window_end:"22:00",min_gap:20};
  var isAdmin = ME && ME.uid === "a";
  var llmConfigHtml = isAdmin ? (
    '<div class="field" style="margin-top:16px;display:flex;align-items:center;justify-content:space-between">' +
      '<label style="margin-bottom:0">🤖 AI 时光小信大模型配置</label>' +
      '<span style="font-size:10px;background:var(--dog-a-bg);color:var(--dog-a-text);border:1px solid var(--dog-a-bd);padding:2px 8px;border-radius:12px;font-weight:800">👑 管理员专属 (两人共享)</span>' +
    '</div>' +
    '<div class="field"><label>API 基础地址 (Base URL)</label><input id="s_llm_base" placeholder="如: https://api.deepseek.com/v1" value="' + esc(s.llm_api_base||"") + '"></div>' +
    '<div class="row2">' +
      '<div class="field"><label>API Key 密钥</label><input type="password" id="s_llm_key" placeholder="sk-..." value="' + esc(s.llm_api_key||"") + '"></div>' +
      '<div class="field"><label>模型名称 (Model)</label><input id="s_llm_model" placeholder="如: deepseek-chat" value="' + esc(s.llm_model||"deepseek-chat") + '"></div>' +
    '</div>' +
    '<p style="font-size:11px;color:var(--ink-muted);line-height:1.45;margin:2px 0 10px">' +
      '💡 支持 DeepSeek、通义千问、Kimi、OpenAI 等标准兼容 API。配置后，时光简报将由 AI 以线条小狗口吻深情撰写！' +
    '</p>'
  ) : (
    '<div class="field" style="margin-top:16px;display:flex;align-items:center;justify-content:space-between">' +
      '<label style="margin-bottom:0">🤖 AI 时光小信大模型配置</label>' +
      '<span style="font-size:10px;color:var(--ink-muted)">（已由寰宇管理员统一配置）</span>' +
    '</div>' +
    '<p style="font-size:12px;color:var(--ink-secondary);background:var(--bg-card-subtle);border:1px dashed var(--line-strong);padding:10px 14px;border-radius:12px;line-height:1.55">' +
      '🐾 当前小窝已由 <strong>寰宇</strong> 配置守护，时光简报将自动由 AI 为你们两人专属生成，楚菁无需单独配置哦～✨' +
    '</p>'
  );

  var html = renderTopBar() +
    '<div class="page-container">' +
      '<div class="page-header-card">' +
        '<div>' +
          '<div class="page-header-title">⚙️ 小窝偏好设置</div>' +
          '<div class="page-header-desc">🛠️ 管理课表统计、微信提醒与小窝外观</div>' +
        '</div>' +
        '<div style="width:44px;height:44px;flex-shrink:0"><img src="/img/dog-set.png" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '</div>' +

      '<div class="page-main-card">' +
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

          '<div class="field" style="margin-top:16px"><label>📲 微信消息提醒（虾推啥/息知/Server酱）</label></div>' +
          '<div class="row2">' +
            '<div class="field"><label>🐶 a 微信 Token</label><input id="s_wxa" placeholder="贴入 a 的微信推送Token" value="' + esc((u.a.wx_uid)||"") + '"></div>' +
            '<div class="field"><label>🐾 b 微信 Token</label><input id="s_wxb" placeholder="贴入 b 的微信推送Token" value="' + esc((u.b.wx_uid)||"") + '"></div>' +
          '</div>' +
          '<p style="font-size:11px;color:var(--ink-muted);line-height:1.4;margin:2px 0 10px">' +
            '💡 支持 <strong>虾推啥 (wx.xtuis.cn)</strong>，对方留言或新手账时，微信卡片秒弹并直接显示对方说的话。' +
          '</p>' +

          '<div class="field" style="margin-top:16px"><label>🔔 手机原生通知（Android App）</label></div>' +
          '<div style="background:var(--bg-chip,#fff5ea);padding:10px 14px;border-radius:12px;font-size:12px;color:var(--ink-secondary);margin-bottom:12px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">' +
            '<div>' +
              '<span>客户端状态：<strong>' + (window.AndroidApp ? '🟢 前后台常驻守护已就绪' : '⚪ 网页浏览器环境') + '</strong></span>' +
              '<div style="font-size:11px;color:var(--ink-muted);margin-top:2px">' + (window.AndroidApp ? '新日程、留言将通过手机顶部横幅实时提醒，支持后台静默守护' : '使用 Android 客户端登录即可自动开启系统通知') + '</div>' +
            '</div>' +
            '<div style="display:flex;gap:6px">' +
              (window.AndroidApp ? '<button type="button" class="p-btn" data-act="battery-protect" style="padding:5px 10px;font-size:11px">🔋 后台防杀</button>' : '') +
              '<button type="button" class="p-btn" data-act="test-app-notice" style="padding:5px 12px;font-size:11px">🔔 测试通知</button>' +
            '</div>' +
          '</div>' +

          llmConfigHtml +

          '<div class="field" style="margin-top:16px"><label>🌓 外观主题模式</label></div>' +
          '<div style="margin-bottom:12px">' +
            '<div class="seg" id="themeSeg">' +
              '<div class="opt' + (!document.documentElement.classList.contains("dark") ? " on" : "") + '" data-act="set-theme" data-theme="light">☀️ 浅色温暖</div>' +
              '<div class="opt' + (document.documentElement.classList.contains("dark") ? " on" : "") + '" data-act="set-theme" data-theme="dark">🌙 暗夜黑夜</div>' +
            '</div>' +
          '</div>' +

          '<div class="field" style="margin-top:16px"><label>💖 纪念日与倒计时</label></div>' +
          '<div style="background:var(--bg-card-subtle);border:1.5px solid var(--line-strong);border-radius:14px;padding:12px 14px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;margin-bottom:14px" data-act="open-anniv-list">' +
            '<div>' +
              '<div style="font-size:13.5px;font-weight:800;color:var(--ink-primary)">💖 管理纪念日与倒计时列表</div>' +
              '<div style="font-size:11px;color:var(--ink-muted);margin-top:2px">相恋天数累计、生日倒计时与重要日子管理</div>' +
            '</div>' +
            '<span style="font-size:12px;color:var(--dog-a-text);font-weight:800">前往 ➜</span>' +
          '</div>' +

          '<div class="field" style="margin-top:16px"><label>✨ 俩汪心愿备忘录</label></div>' +
          '<div style="background:var(--bg-card-subtle);border:1.5px solid var(--line-strong);border-radius:14px;padding:12px 14px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;margin-bottom:14px" data-act="switch-view" data-v="wishes">' +
            '<div>' +
              '<div style="font-size:13.5px;font-weight:800;color:var(--ink-primary)">✨ 管理俩汪心愿备忘清单 (Bucket List)</div>' +
              '<div style="font-size:11px;color:var(--ink-muted);margin-top:2px">将来想一起做的事、想去的地方与想吃的美食</div>' +
            '</div>' +
            '<span style="font-size:12px;color:var(--dog-a-text);font-weight:800">前往 ➜</span>' +
          '</div>' +

          '<div class="field" style="margin-top:16px"><label>修改当前身份（' + esc(ME.name) + '）密码</label></div>' +
          '<div class="row2">' +
            '<div class="field"><input type="password" id="s_old" placeholder="旧密码（不改留空）" autocomplete="current-password"></div>' +
            '<div class="field"><input type="password" id="s_new" placeholder="新密码 ≥ 6 位" autocomplete="new-password"></div>' +
          '</div>' +

          '<div style="margin-top:18px;padding-top:16px;border-top:1px dashed var(--line-strong);display:flex;align-items:center;justify-content:space-between">' +
            '<span style="font-size:12px;color:var(--ink-muted)">当前狗狗：<strong>' + esc(ME.name) + '</strong></span>' +
            '<button type="button" class="p-btn danger" data-act="logout">🚪 退出当前登录</button>' +
          '</div>' +

          '<div style="margin-top:16px">' +
            '<button class="p-btn pri" style="width:100%;padding:11px;font-size:14.5px;justify-content:center">💾 保存设置</button>' +
          '</div>' +
        '</form>' +
      '</div>' +
    '</div>';

  html += bottomNavHTML();
  html += modalsHTML();
  $("#app").innerHTML = html;
}

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
          dayBadges.push('<span class="mday-badge" title="' + esc(item.title) + ' 倒计时">⏳</span>');
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
      '</div>' +
      '<div class="top-actions">' +
        myPill +
        '<button class="p-btn icon-btn" data-act="toggle-dark" id="darkToggleBtn" title="切换深色/浅色模式">' +
          (document.documentElement.classList.contains("dark") ? '☀️<span class="btn-txt"> 浅色</span>' : '🌙<span class="btn-txt"> 深色</span>') +
        '</button>' +
      '</div>' +
    '</header>' +
  '</div>' +

  '<div class="page-container">' +
    '<div style="margin-bottom:12px">' +
      '<div class="seg" style="margin-bottom:0">' +
        '<div class="opt on" data-act="switch-view" data-v="month">📔 足迹手账</div>' +
        '<div class="opt" data-act="switch-view" data-v="wishes">✨ 心愿备忘</div>' +
      '</div>' +
    '</div>' +

    '<div class="month-cal-wrap" style="padding:0">' +
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
  '</div>' +
  '<button class="fab" data-act="open-add-diary" title="记下今天去哪玩啦">🐾 记手账 +</button>';

  html += bottomNavHTML();
  html += modalsHTML();

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
    head += '<div class="dh' + (isToday ? ' today' : '') + '"' + (isToday ? ' id="colTodayHeader"' : '') + '>' +
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
    body += '<div class="dcol' + (isToday ? ' today' : '') + '"' + (isToday ? ' id="colToday"' : '') + ' style="height:' + CALH + 'px"><div class="divider"></div>' +
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

  var myIsA = ME.uid === "a";
  var myPill = '<span class="user-pill ' + ME.uid + '">' + (myIsA ? '🐶 ' : '🐾 ') + esc(ME.name) + '</span>';

  var html =
  '<div class="header-box" id="headerBox">' +
    '<header class="topbar">' +
      '<div class="brand">' +
        '<img src="/img/dogheads.png" alt="线条小狗" class="brand-img">' +
        '<span class="brand-title">两人日程 🐾</span>' +
      '</div>' +
      '<div class="top-actions">' +
        myPill +
        '<button class="p-btn icon-btn" data-act="toggle-dark" id="darkToggleBtn" title="切换深色/浅色模式">' +
          (document.documentElement.classList.contains("dark") ? '☀️<span class="btn-txt"> 浅色</span>' : '🌙<span class="btn-txt"> 深色</span>') +
        '</button>' +
      '</div>' +
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
  '<button class="fab" data-act="open-add">🐾 记新日程 ＋</button>';

  html += bottomNavHTML();
  html += modalsHTML();

  $("#app").innerHTML = html;
}

function bottomNavHTML(){
  var v = CURRENT_VIEW;
  return '<nav class="bottom-nav">' +
    '<div class="b-tab' + (v === "week" ? ' on' : '') + '" data-act="switch-view" data-v="week">' +
      '<span class="b-ico">🗓️</span><span class="b-txt">课表</span>' +
    '</div>' +
    '<div class="b-tab' + (v === "month" ? ' on' : '') + '" data-act="switch-view" data-v="month">' +
      '<span class="b-ico">📔</span><span class="b-txt">手账</span>' +
    '</div>' +
    '<div class="b-tab' + (v === "summary" ? ' on' : '') + '" data-act="switch-view" data-v="summary">' +
      '<span class="b-ico">💌</span><span class="b-txt">时光简报</span>' +
    '</div>' +
    '<div class="b-tab' + (v === "messages" ? ' on' : '') + '" data-act="switch-view" data-v="messages">' +
      '<span class="b-ico">💬' + (UNREAD_COUNT > 0 ? ('<span class="b-badge" id="bUnreadBadge">' + (UNREAD_COUNT > 99 ? '99+' : UNREAD_COUNT) + '</span>') : '<span class="b-badge" id="bUnreadBadge" style="display:none"></span>') + '</span><span class="b-txt">消息</span>' +
    '</div>' +
    '<div class="b-tab' + (v === "settings" ? ' on' : '') + '" data-act="switch-view" data-v="settings">' +
      '<span class="b-ico">⚙️</span><span class="b-txt">设置</span>' +
    '</div>' +
  '</nav>';
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
  var wStart = (DATA && DATA.week_start) || todayISO();

  return '<div class="overlay" id="ovAdd"><div class="modal">' +
    '<div class="mhead">' +
      '<div class="mhead-left"><h3 id="addModalTitle">添加日程 🐾</h3></div>' +
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
        '<button class="p-btn pri" id="addModalSubmitBtn">🐾 记在小本本上！</button>' +
      '</div>' +
    '</form></div></div>' +

    '<div class="overlay" id="ovDetail"><div class="modal" id="ovDetailBox"></div></div>' +

    '<div class="overlay" id="ovDiaryDay"><div class="modal" id="ovDiaryDayBox"></div></div>' +

    '<div class="overlay" id="ovAddWish"><div class="modal">' +
      '<div class="mhead">' +
        '<div class="mhead-left"><h3>许下一个新心愿 ✨</h3></div>' +
        '<img src="/img/dog-add.png" alt="" class="mhead-img">' +
      '</div>' +
      '<form id="addWishForm">' +
        '<div class="field"><label>谁的心愿？</label>' +
          '<div class="seg" id="wishUidSeg">' +
            '<div class="opt' + (addWishUid==="a"?" on":"") + '" data-act="pick-wish-uid" data-uid="a">🐶 ' + esc(u.a.name) + '</div>' +
            '<div class="opt' + (addWishUid==="b"?" on b-side":"") + '" data-act="pick-wish-uid" data-uid="b">🐾 ' + esc(u.b.name) + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="field"><label>心愿分类</label>' +
          '<div class="seg" id="wishAddCatSeg" style="flex-wrap:wrap">' +
            '<div class="opt on" data-act="pick-wish-cat" data-v="travel">✈️ 旅行打卡</div>' +
            '<div class="opt" data-act="pick-wish-cat" data-v="food">🍜 美食探索</div>' +
            '<div class="opt" data-act="pick-wish-cat" data-v="movie">🎬 影音娱乐</div>' +
            '<div class="opt" data-act="pick-wish-cat" data-v="life">🏡 日常浪漫</div>' +
            '<div class="opt" data-act="pick-wish-cat" data-v="other">💡 奇思妙想</div>' +
          '</div>' +
        '</div>' +
        '<div class="field"><label>心愿内容 💖</label>' +
          '<input id="wf_title" required maxlength="80" placeholder="如: 一起去海边看日出 / 去吃那家火锅">' +
        '</div>' +
        '<div class="field"><label>期盼程度</label>' +
          '<div class="seg" id="wishPrioSeg">' +
            '<div class="opt on" data-act="pick-wish-prio" data-v="0">✨ 普通心愿</div>' +
            '<div class="opt" data-act="pick-wish-prio" data-v="1">🔥 超级想做 (置顶标红)</div>' +
          '</div>' +
        '</div>' +
        '<div class="field"><label>详细备注 / 攻略地点 (选填)</label>' +
          '<textarea id="wf_note" rows="2" maxlength="300" placeholder="如: 计划在初夏去、大众点评店铺名、注意事项等"></textarea>' +
        '</div>' +
        '<div class="mfoot" style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">' +
          '<button type="button" class="p-btn" data-act="close">取消</button>' +
          '<button class="p-btn pri">💖 贴进心愿本 🐾</button>' +
        '</div>' +
      '</form>' +
    '</div></div>' +

    '<div class="overlay" id="ovPhotoViewer" style="background:rgba(0,0,0,0.92);z-index:9999;display:none;align-items:center;justify-content:center;touch-action:none">' +
      '<div style="position:relative;width:100vw;height:100vh;display:flex;align-items:center;justify-content:center;overflow:hidden" id="pvContainer">' +
        '<img id="pvImage" src="" style="max-width:94vw;max-height:86vh;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.6);object-fit:contain;transition:transform .2s ease, opacity .2s ease;user-select:none;-webkit-user-select:none">' +
        '<div id="pvIndicator" style="position:absolute;top:20px;left:50%;transform:translateX(-50%);color:#fff;background:rgba(0,0,0,0.5);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700">1 / 1</div>' +
        '<button type="button" data-act="pv-prev" id="pvBtnPrev" style="position:absolute;left:12px;top:50%;transform:translateY(-50%);background:rgba(255,255,255,0.25);border:none;border-radius:50%;width:42px;height:42px;color:#fff;font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px)">‹</button>' +
        '<button type="button" data-act="pv-next" id="pvBtnNext" style="position:absolute;right:12px;top:50%;transform:translateY(-50%);background:rgba(255,255,255,0.25);border:none;border-radius:50%;width:42px;height:42px;color:#fff;font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px)">›</button>' +
        '<button type="button" data-act="close-photo" style="position:absolute;top:18px;right:18px;background:rgba(239,68,68,0.9);color:#fff;border:none;border-radius:50%;width:36px;height:36px;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 10px rgba(0,0,0,0.5)">✕</button>' +
      '</div>' +
    '</div>' +

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
        '<div class="field"><label>拍立得照片（单篇最多可传 18 张，自动压缩秒传 📷）</label>' +
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
var pvPhotosList = [];
var pvCurrentIndex = 0;

function scrollToToday(){
  var wrap = document.querySelector(".calwrap");
  var target = document.querySelector("#colTodayHeader") || document.querySelector("#colToday");
  if (wrap && target){
    var offset = target.offsetLeft - 58; // 扣除左侧时间轴宽度
    if (offset < 0) offset = 0;
    wrap.scrollTo({
      left: Math.max(0, offset - 10),
      behavior: "smooth"
    });
  }
}

function updatePhotoViewer(){
  var img = $("#pvImage");
  var ind = $("#pvIndicator");
  var bPrev = $("#pvBtnPrev");
  var bNext = $("#pvBtnNext");
  if (!img) return;
  var total = pvPhotosList.length;
  if (total === 0) return;
  img.src = pvPhotosList[pvCurrentIndex];
  if (ind) ind.textContent = (pvCurrentIndex + 1) + " / " + total;
  if (bPrev) bPrev.style.display = total > 1 ? "flex" : "none";
  if (bNext) bNext.style.display = total > 1 ? "flex" : "none";
}

var curSummaryTab = "day";
var curSummaryData = null;

function loadSummary(tab, force){
  curSummaryTab = tab || "day";
  var box = $("#summaryContentBox");
  if (box) box.innerHTML = '<div style="text-align:center;padding:34px 0;color:var(--ink-muted);font-weight:700">🐶 线条小狗正在用心为你构思温馨时光信...<br><span style="font-size:11px;font-weight:normal;opacity:0.8">（若已配置AI模型，将由大模型深情撰写）</span></div>';
  var url = "/api/summary?type=" + curSummaryTab + (force ? "&refresh=1" : "");
  api(url).then(function(res){
    curSummaryData = res;
    renderSummaryBox();
  }).catch(function(e){
    if (box) box.innerHTML = '<div style="color:red;padding:20px 0">加载失败：' + esc(e.message) + '</div>';
  });
}

function renderSummaryBox(){
  var box = $("#summaryContentBox");
  if (!box || !curSummaryData) return;
  var d = curSummaryData;
  var aiTag = d.is_ai ? '<span style="font-size:10px;background:linear-gradient(135deg,#ff758c,#ff7eb3);color:#fff;padding:2px 7px;border-radius:10px;margin-left:auto;font-weight:800">✨ AI 专属小信</span>' : '';
  var html = '<div style="background:var(--bg-card-subtle);border:1.5px solid var(--line-strong);border-radius:16px;padding:14px;box-shadow:var(--shadow-sm)">' +
    '<div style="font-size:14.5px;font-weight:800;color:var(--ink-primary);margin-bottom:10px;display:flex;align-items:center;gap:6px">' +
      '<span>' + esc(d.title) + '</span>' + aiTag +
    '</div>' +
    '<div style="color:var(--ink-secondary);font-size:13px;line-height:1.75;white-space:pre-wrap;margin-bottom:10px;letter-spacing:0.2px">' + esc(d.content) + '</div>';

  if (d.stats && d.stats.length > 0){
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:10px;padding-top:10px;border-top:1px dashed var(--line-strong)">' +
      d.stats.map(function(s){
        return '<div style="background:var(--bg-card);border:1px solid var(--line-strong);border-radius:10px;padding:8px;text-align:center">' +
          '<div style="font-size:11px;color:var(--ink-muted)">' + esc(s.k) + '</div>' +
          '<div style="font-size:14px;font-weight:800;color:var(--dog-a);margin-top:2px">' + esc(s.v) + '</div>' +
        '</div>';
      }).join("") +
    '</div>';
  }
  html += '</div>';
  box.innerHTML = html;
}

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
    return '<div style="display:flex;align-items:center;justify-content:space-between;padding:6px 10px;background:var(--dog-a-bg);border:1px solid var(--dog-a-bd);color:var(--dog-a-text);border-radius:12px;margin-bottom:6px;font-size:12px">' +
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
          var pListJson = encodeURIComponent(JSON.stringify(d.photos.map(function(p){ return "/photos/" + p.file_name; })));
          photosHtml = '<div class="polaroid-gallery">' +
            d.photos.map(function(p, pIdx){
              return '<div class="polaroid-card">' +
                '<img src="/photos/' + p.file_name + '" class="polaroid-img" alt="" data-act="preview-photo" data-idx="' + pIdx + '" data-list="' + pListJson + '">' +
              '</div>';
            }).join("") +
          '</div>';
        }

        var dJson = encodeURIComponent(JSON.stringify(d));
        html += '<div class="diary-item-card">' +
          '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">' +
            '<div>' +
              '<span style="font-size:15px;font-weight:800;color:var(--ink-primary)">' + esc(d.title) + '</span>' +
              (d.mood ? (' <span style="font-size:12px;background:var(--dog-a-bg);color:var(--dog-a-text);border:1px solid var(--dog-a-bd);border-radius:10px;padding:2px 6px;margin-left:4px">' + esc(d.mood) + '</span>') : '') +
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
    openModal("#ovDiaryDay");
  }).catch(function(err){ if (err.message !== "unauth") toast(err.message); });
}

function openDetail(id, date){
  curDetail = {id: +id, date: date || todayISO()};
  var q = curDetail.date ? ("?date=" + curDetail.date) : "";
  api("/api/event/" + id + q).then(function(j){
    $("#ovDetailBox").innerHTML = detailHTML(j);
    openModal("#ovDetail");
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
    '<div class="foot" style="justify-content:space-between;margin-top:14px;flex-wrap:wrap;gap:8px">' +
      '<div style="display:flex;gap:6px">' +
        '<button class="p-btn pri" data-act="edit-event" data-id="' + ev.id + '">✏️ 编辑日程</button>' +
        '<button class="p-btn danger" data-act="del-event" data-id="' + ev.id + '">删除此日程</button>' +
      '</div>' +
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
   var fWd = $("#fldWd"), fDt = $("#fldDt");
   if (fWd) fWd.style.display = addRep === "weekly" ? "" : "none";
   if (fDt) fDt.style.display = addRep === "once" ? "" : "none";
 } else if (act === "quick-cmt"){
    var phrase = el.getAttribute("data-text");
    var inp = $("#cmtText");
    if (inp){
      inp.value = phrase;
      inp.focus();
    }
  } else if (act === "prev"){ ANCHOR = shiftISO(ANCHOR, -7); DATA = null; load(); }
  else if (act === "next"){ ANCHOR = shiftISO(ANCHOR, 7); DATA = null; load(); }
  else if (act === "today"){
    var curT = todayISO();
    if (ANCHOR === curT){
      scrollToToday();
    } else {
      ANCHOR = curT;
      DATA = null;
      load().then(function(){
        setTimeout(scrollToToday, 60);
      });
    }
  }
  else if (act === "open-add"){
    curEditingEventId = null;
    var addTitleEl = $("#addModalTitle");
    if (addTitleEl) addTitleEl.textContent = "添加日程 🐾";
    var addBtnEl = $("#addModalSubmitBtn");
    if (addBtnEl) addBtnEl.textContent = "🐾 记在小本本上！";
    addRep = "weekly";
    document.querySelectorAll("#repSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === "weekly");
    });
    var fWd = $("#fldWd"), fDt = $("#fldDt");
    if (fWd) fWd.style.display = "";
    if (fDt) fDt.style.display = "none";
    if ($("#f_title")) $("#f_title").value = "";
    if ($("#f_loc")) $("#f_loc").value = "";
    if ($("#f_note")) $("#f_note").value = "";
    if ($("#f_ws")) $("#f_ws").value = "";
    if ($("#f_ts")) $("#f_ts").value = "19:00";
    if ($("#f_te")) $("#f_te").value = "21:00";
    if ($("#f_date")) $("#f_date").value = (DATA && DATA.week_start) || todayISO();
    openModal("#ovAdd");
  }
  else if (act === "set-theme"){
    var theme = el.getAttribute("data-theme");
    var isDark = theme === "dark";
    document.documentElement.classList.toggle("dark", isDark);
    try {
      localStorage.setItem("sched_dark_mode", isDark ? "1" : "0");
    } catch(e){}
    document.querySelectorAll("#themeSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-theme") === theme);
    });
    var btn = $("#darkToggleBtn");
    if (btn){
      btn.innerHTML = isDark ? '☀️<span class="btn-txt"> 浅色</span>' : '🌙<span class="btn-txt"> 深色</span>';
    }
  }
  else if (act === "toggle-dark"){
    var isDark = document.documentElement.classList.toggle("dark");
    try {
      localStorage.setItem("sched_dark_mode", isDark ? "1" : "0");
    } catch(e){}
    var btn = $("#darkToggleBtn");
    if (btn){
      btn.innerHTML = isDark ? '☀️<span class="btn-txt"> 浅色</span>' : '🌙<span class="btn-txt"> 深色</span>';
    }
  }
  else if (act === "switch-view" || act === "open-settings" || act === "open-summary" || act === "open-anniv" || act === "open-anniv-list"){
    var targetV = el.getAttribute("data-v");
    if (act === "open-settings") targetV = "settings";
    else if (act === "open-summary") targetV = "summary";
    else if (act === "open-anniv" || act === "open-anniv-list") targetV = "anniv";

    // 记录切换视图的历史记录（支持浏览器/手机返回键返回上一页）
    if (targetV !== CURRENT_VIEW){
      history.pushState({type:"view", v:targetV}, "");
    }

    if (targetV === "week"){
      CURRENT_VIEW = "week";
      var curT = todayISO();
      if (ANCHOR !== curT){
        ANCHOR = curT;
        DATA = null;
      }
      load().then(function(){
        setTimeout(scrollToToday, 60);
      });
    } else {
      CURRENT_VIEW = targetV;
      load();
    }
  }
  else if (act === "month-prev"){
    var p = MONTH_ANCHOR.split("-"), y = +p[0], m = +p[1] - 1;
    if (m < 1){ m = 12; y--; }
    MONTH_ANCHOR = y + "-" + pad(m);
    MONTH_DATA = null;
    load();
  }
  else if (act === "month-next"){
    var p = MONTH_ANCHOR.split("-"), y = +p[0], m = +p[1] + 1;
    if (m > 12){ m = 1; y++; }
    MONTH_ANCHOR = y + "-" + pad(m);
    MONTH_DATA = null;
    load();
  }
  else if (act === "month-cur"){
    MONTH_ANCHOR = todayISO().slice(0, 7);
    MONTH_DATA = null;
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
    openModal("#ovAddDiary");
  }
  else if (act === "preview-photo"){
    try {
      var rawList = el.getAttribute("data-list");
      pvPhotosList = rawList ? JSON.parse(decodeURIComponent(rawList)) : [];
      pvCurrentIndex = +el.getAttribute("data-idx") || 0;
    } catch(e){
      pvPhotosList = [el.getAttribute("data-src") || el.src];
      pvCurrentIndex = 0;
    }
    updatePhotoViewer();
    var pv = $("#ovPhotoViewer");
    if (pv){
      pv.style.display = "flex";
      pv.classList.add("show");
      try {
        history.pushState({modal:"photo"}, "");
      } catch(e){}
    }
  }
  else if (act === "pv-prev"){
    if (pvPhotosList.length > 1){
      pvCurrentIndex = (pvCurrentIndex - 1 + pvPhotosList.length) % pvPhotosList.length;
      updatePhotoViewer();
    }
  }
  else if (act === "pv-next"){
    if (pvPhotosList.length > 1){
      pvCurrentIndex = (pvCurrentIndex + 1) % pvPhotosList.length;
      updatePhotoViewer();
    }
  }
  else if (act === "close-photo"){
    var pv = $("#ovPhotoViewer");
    if (pv){
      pv.style.display = "none";
      pv.classList.remove("show");
    }
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
      openModal("#ovAddDiary");
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
  else if (act === "open-summary"){
    $("#ovSummary").classList.add("show");
    loadSummary("day");
  }
  else if (act === "summary-tab"){
    var tab = el.getAttribute("data-tab");
    document.querySelectorAll("#summaryTabSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-tab") === tab);
    });
    loadSummary(tab);
  }
  else if (act === "refresh-summary"){
    loadSummary(curSummaryTab, true);
  }
  else if (act === "send-summary-wx"){
    api("/api/summary/push", {method:"POST", body:{type: curSummaryTab}}).then(function(res){
      toast("💌 时光简报已成功推送至微信！(已发送至 " + res.sent_count + " 人的微信)");
    }).catch(function(err){
      toast("❌ " + err.message);
    });
  }
  else if (act === "anniv-type"){
    annivType = el.getAttribute("data-v") || "love";
    document.querySelectorAll("#annivTypeSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === annivType);
    });
  }
  else if (act === "del-anniv"){
    if (!confirm("确定删除这个纪念日？")) return;
    api("/api/anniversaries/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
      .then(function(){
        toast("已删除 🐾");
        DATA = null;
        return load();
      })
      .then(function(){ renderAnnivList(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  }
  else if (act === "detail"){ openDetail(el.getAttribute("data-id"), el.getAttribute("data-date")); }
  else if (act === "send-cmt"){
    var inp = $("#cmtText");
    var text = inp ? inp.value.trim() : "";
    if (!text || !curDetail) return;
    var cmtDate = curDetail.date || todayISO();
    api("/api/comment", {method:"POST", body:{event_id:curDetail.id, text:text, date:cmtDate}})
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
  else if (act === "edit-event"){
    var eid = +el.getAttribute("data-id");
    api("/api/event/" + eid).then(function(j){
      var ev = j.event;
      if (!ev) return;
      curEditingEventId = ev.id;
      var addTitleEl = $("#addModalTitle");
      if (addTitleEl) addTitleEl.textContent = "修改日程 🐾";
      var addBtnEl = $("#addModalSubmitBtn");
      if (addBtnEl) addBtnEl.textContent = "🐾 保存修改！";

      addUid = ev.uid;
      document.querySelectorAll("#addSeg .opt").forEach(function(o){
        var uid = o.getAttribute("data-uid");
        var isA = uid === "a";
        o.className = "opt" + (uid === addUid ? (" on " + (isA ? "" : "b-side")) : "");
      });

      addRep = ev.repeat || "weekly";
      document.querySelectorAll("#repSeg .opt").forEach(function(o){
        o.classList.toggle("on", o.getAttribute("data-v") === addRep);
      });

      var fWd = $("#fldWd"), fDt = $("#fldDt");
      if (fWd) fWd.style.display = addRep === "weekly" ? "" : "none";
      if (fDt) fDt.style.display = addRep === "once" ? "" : "none";

      if ($("#f_wd")) $("#f_wd").value = ev.weekday != null ? ev.weekday : 0;
      if ($("#f_date")) $("#f_date").value = ev.date || todayISO();
      if ($("#f_ts")) $("#f_ts").value = ev.tstart || "19:00";
      if ($("#f_te")) $("#f_te").value = ev.tend || "21:00";
      if ($("#f_title")) $("#f_title").value = ev.title || "";
      if ($("#f_loc")) $("#f_loc").value = ev.location || "";
      if ($("#f_note")) $("#f_note").value = ev.note || "";
      if ($("#f_ws")) $("#f_ws").value = ev.week_spec || "";

      // 关闭详情弹窗，打开编辑弹窗
      var ovDetail = $("#ovDetail");
      if (ovDetail) ovDetail.classList.remove("show");
      openModal("#ovAdd");
    }).catch(function(err){ toast(err.message || "加载失败"); });
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
  else if (act === "close"){
    if (history.state && history.state.type === "modal"){
      history.back();
    } else {
      closeAllModals();
    }
  }
  else if (act === "logout"){
    api("/api/logout", {method:"POST"}).catch(function(){}).then(function(){
      document.querySelectorAll(".overlay").forEach(function(o){ o.classList.remove("show"); });
      ME = null; render();
    });
  }
  else if (act === "msg-filter"){
    curMsgFilter = el.getAttribute("data-v") || "all";
    document.querySelectorAll("#msgFilterSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === curMsgFilter);
    });
    renderMessagesList();
  }
  else if (act === "read-all-msgs"){
    api("/api/messages/read", {method:"POST", body:{all:true}}).then(function(res){
      toast("已全部标为已读 ✨");
      if (res && res.unread_count !== undefined) setUnreadCount(res.unread_count);
      else setUnreadCount(0);
      (MESSAGES_DATA || []).forEach(function(m){ m.is_read = 1; });
      renderMessagesList();
    }).catch(function(err){ toast(err.message || "操作失败"); });
  }
  else if (act === "clear-read-msgs"){
    if (!confirm("确定清理所有已读通知？")) return;
    api("/api/messages/delete", {method:"POST", body:{clear_read:true}}).then(function(res){
      toast("已清理已读通知 🧹");
      loadMessages();
    }).catch(function(err){ toast(err.message || "清理失败"); });
  }
  else if (act === "refresh-msgs"){
    loadMessages();
  }
  else if (act === "read-one-msg"){
    var mid = +el.getAttribute("data-mid");
    api("/api/messages/read", {method:"POST", body:{id:mid}}).then(function(res){
      if (res && res.unread_count !== undefined) setUnreadCount(res.unread_count);
      var m = (MESSAGES_DATA || []).find(function(x){ return x.id === mid; });
      if (m) m.is_read = 1;
      renderMessagesList();
    }).catch(function(err){ toast(err.message || "操作失败"); });
  }
  else if (act === "del-one-msg"){
    if (!confirm("确定删除这条消息？")) return;
    var mid = +el.getAttribute("data-mid");
    api("/api/messages/delete", {method:"POST", body:{id:mid}}).then(function(res){
      toast("已删除消息 🐾");
      loadMessages();
    }).catch(function(err){ toast(err.message || "删除失败"); });
  }
  else if (act === "open-msg"){
    if (e.target.closest("[data-act='read-one-msg']") || e.target.closest("[data-act='del-one-msg']")){
      return;
    }
    var mid = +el.getAttribute("data-mid");
    var targetAction = el.getAttribute("data-target-action");
    var m = (MESSAGES_DATA || []).find(function(x){ return x.id === mid; });
    if (m && !m.is_read){
      m.is_read = 1;
      api("/api/messages/read", {method:"POST", body:{id:mid}}).then(function(res){
        if (res && res.unread_count !== undefined) setUnreadCount(res.unread_count);
      }).catch(function(){});
    }
    if (targetAction){
      window.handleTargetAction(targetAction);
    } else {
      renderMessagesList();
    }
  }
  else if (act === "test-app-notice"){
    if (window.AndroidApp && window.AndroidApp.postNotification) {
      window.AndroidApp.postNotification("🐾 线条小狗通知测试", "手机原生通知权限已正常开启！后续对方日程与留言都将直接推送给你 🐾");
      toast("已触发手机通知 🐾");
    }
    api("/api/notifications/test", {method:"POST"}).then(function(){
      if (!window.AndroidApp) toast("已向后端写入测试通知 🐾");
      pollMessages();
    }).catch(function(err){ toast(err.message || "请求失败"); });
  }
  else if (act === "open-add-wish"){
    openModal("#ovAddWish");
  }
  else if (act === "pick-wish-uid"){
    addWishUid = el.getAttribute("data-uid") || "a";
    document.querySelectorAll("#wishUidSeg .opt").forEach(function(o){
      var isTarget = o.getAttribute("data-uid") === addWishUid;
      o.classList.toggle("on", isTarget);
      if (o.getAttribute("data-uid") === "b") o.classList.toggle("b-side", isTarget);
    });
  }
  else if (act === "pick-wish-cat"){
    addWishCat = el.getAttribute("data-v") || "travel";
    document.querySelectorAll("#wishAddCatSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === addWishCat);
    });
  }
  else if (act === "pick-wish-prio"){
    addWishPrio = +el.getAttribute("data-v") || 0;
    document.querySelectorAll("#wishPrioSeg .opt").forEach(function(o){
      o.classList.toggle("on", +o.getAttribute("data-v") === addWishPrio);
    });
  }
  else if (act === "toggle-wish"){
    var wid = +el.getAttribute("data-wid");
    api("/api/wishes/toggle", {method:"POST", body:{id:wid}}).then(function(res){
      toast(res.is_done ? "🎉 恭喜！共同实现了一个心愿！" : "已重新标记为待完成 🐾");
      loadWishes();
    }).catch(function(err){ toast(err.message || "操作失败"); });
  }
  else if (act === "del-wish"){
    if (!confirm("确定删除这个心愿便签？")) return;
    var wid = +el.getAttribute("data-wid");
    api("/api/wishes/delete", {method:"POST", body:{id:wid}}).then(function(){
      toast("已删除心愿 🐾");
      loadWishes();
    }).catch(function(err){ toast(err.message || "删除失败"); });
  }
  else if (act === "wish-filter"){
    curWishFilter = el.getAttribute("data-v") || "all";
    document.querySelectorAll("#wishCatSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === curWishFilter);
    });
    renderWishesList();
  }
  else if (act === "wish-to-diary"){
    var wishTitle = el.getAttribute("data-title") || "";
    CURRENT_VIEW = "month";
    load().then(function(){
      setTimeout(function(){
        curEditingDiary = null;
        if ($("#df_title")) $("#df_title").value = "实现了心愿: " + wishTitle;
        if ($("#df_content")) $("#df_content").value = "今天和 TA 一起打卡了心愿清单【" + wishTitle + "】！超级开心与满足~ 🥰";
        openModal("#ovAddDiary");
      }, 150);
    });
  }
  else if (act === "battery-protect"){
    if (window.AndroidApp && window.AndroidApp.requestBatteryOptimization) {
      window.AndroidApp.requestBatteryOptimization();
    } else {
      toast("请在 Android 手机客户端中使用此功能 🐾");
    }
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
      .then(function(j){
        ME = {uid:j.uid, name:j.name};
        ANCHOR = todayISO();
        if (window.AndroidApp && window.AndroidApp.saveAuthUid) {
          window.AndroidApp.saveAuthUid(j.uid);
        }
        toast("欢迎回家，" + j.name + " 🐾");
        if (j.unread_count !== undefined) setUnreadCount(j.unread_count);
        load().then(function(){ pollMessages(); });
      })
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
    if (curEditingEventId) {
      body.id = curEditingEventId;
    }
    api(curEditingEventId ? "/api/events/update" : "/api/events", {method:"POST", body:body})
      .then(function(){
        document.querySelectorAll(".overlay").forEach(function(o){ o.classList.remove("show"); });
        toast(curEditingEventId ? "已修改日程 🐾" : "已记下啦 🐾");
        curEditingEventId = null;
        load();
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
      wx_b: $("#s_wxb") ? $("#s_wxb").value.trim() : "",
      llm_api_base: $("#s_llm_base") ? $("#s_llm_base").value.trim() : "",
      llm_api_key: $("#s_llm_key") ? $("#s_llm_key").value.trim() : "",
      llm_model: $("#s_llm_model") ? $("#s_llm_model").value.trim() : "deepseek-chat"
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
        DATA = null;
        return load();
      })
      .then(function(){ renderAnnivList(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message); });
  } else if (f.id === "addWishForm"){
    e.preventDefault();
    var title = ($("#wf_title") ? $("#wf_title").value : "").trim();
    if (!title){ toast("请输入心愿内容 🐾"); return; }
    var note = ($("#wf_note") ? $("#wf_note").value : "").trim();
    api("/api/wishes", {
      method: "POST",
      body: {
        creator_uid: addWishUid,
        title: title,
        category: addWishCat,
        priority: addWishPrio,
        note: note
      }
    }).then(function(){
      closeAllModals();
      if ($("#wf_title")) $("#wf_title").value = "";
      if ($("#wf_note")) $("#wf_note").value = "";
      toast("心愿已贴进清单啦 ✨");
      loadWishes();
    }).catch(function(err){ toast(err.message || "添加失败"); });
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
  var pv = $("#ovPhotoViewer");
  var isOpen = pv && pv.style.display !== "none";
  if (e.key === "Escape"){
    if (isOpen){
      pv.style.display = "none";
      pv.classList.remove("show");
      return;
    }
  }
  if (isOpen && (e.key === "ArrowLeft" || e.key === "ArrowUp")){
    var bP = $("#pvBtnPrev"); if (bP) bP.click();
    return;
  }
  if (isOpen && (e.key === "ArrowRight" || e.key === "ArrowDown")){
    var bN = $("#pvBtnNext"); if (bN) bN.click();
    return;
  }
  if (e.target && e.target.id === "cmtText" && e.key === "Enter"){
    e.preventDefault();
    var btn = document.querySelector('[data-act="send-cmt"]');
    if (btn) btn.click();
  }
});

/* 手势左右滑动切图支持 */
(function(){
  var startX = 0, startY = 0;
  document.addEventListener("touchstart", function(e){
    var pv = $("#ovPhotoViewer");
    if (!pv || pv.style.display === "none") return;
    if (e.touches && e.touches.length === 1){
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
    }
  }, {passive:true});

  document.addEventListener("touchend", function(e){
    var pv = $("#ovPhotoViewer");
    if (!pv || pv.style.display === "none") return;
    if (e.changedTouches && e.changedTouches.length === 1){
      var diffX = e.changedTouches[0].clientX - startX;
      var diffY = e.changedTouches[0].clientY - startY;
      // 水平滑动距离大于 45px 且大于垂直滑动距离，触发左右切图
      if (Math.abs(diffX) > 45 && Math.abs(diffX) > Math.abs(diffY)){
        if (diffX > 0){
          var bP = $("#pvBtnPrev"); if (bP) bP.click();
        } else {
          var bN = $("#pvBtnNext"); if (bN) bN.click();
        }
      }
    }
  }, {passive:true});
})();

window.addEventListener("popstate", function(e){
  // 1. 若当前有弹窗或照片大图打开，物理返回优先平滑关闭弹窗
  var closed = closeAllModals(true);
  if (closed) return;

  // 2. 若是从其他页面返回，按状态恢复视图
  if (e.state && e.state.v){
    CURRENT_VIEW = e.state.v;
    render();
  } else if (!e.state || !e.state.v){
    // 默认回到最核心的课表主页
    if (CURRENT_VIEW !== "week"){
      CURRENT_VIEW = "week";
      render();
    }
  }
});

ANCHOR = todayISO();
api("/api/meta").then(function(j){ META = j; }).catch(function(){})
  .then(function(){ return api("/api/me").then(function(j){ ME = j; }); })
  .catch(function(){})
  .then(function(){
    if (ME) {
      if (window.AndroidApp && window.AndroidApp.saveAuthUid) {
        window.AndroidApp.saveAuthUid(ME.uid);
      }
      load().then(function(){
        if (CURRENT_VIEW === "week") setTimeout(scrollToToday, 100);
        pollMessages();
      });
    } else {
      render();
    }
  });

  window.handleTargetAction = function(action) {
    if (!action || typeof action !== "string") return;
    var parts = action.split(":");
    var type = parts[0];
    var val = parts[1] || "";

    if (type === "event") {
      var eid = parseInt(val);
      var eDate = parts[2] || "";
      if (!isNaN(eid)) {
        if (CURRENT_VIEW !== "week") {
          CURRENT_VIEW = "week";
          load().then(function(){
            setTimeout(function(){ openDetail(eid, eDate); }, 150);
          });
        } else {
          openDetail(eid, eDate);
        }
      }
    } else if (type === "diary") {
      if (CURRENT_VIEW !== "month") {
        CURRENT_VIEW = "month";
        load().then(function(){
          if (val) setTimeout(function(){ openDayDiaries(val); }, 150);
        });
      } else {
        if (val) openDayDiaries(val);
      }
    } else if (type === "view") {
      if (val && val !== CURRENT_VIEW) {
        CURRENT_VIEW = val;
        load();
      }
    } else if (type === "wish") {
      if (CURRENT_VIEW !== "wishes") {
        CURRENT_VIEW = "wishes";
        load();
      }
    }
  };

  function pollMessages() {
    if (!ME) return;
    api("/api/messages").then(function(res){
      if (res && res.ok) {
        var prevCount = UNREAD_COUNT;
        setUnreadCount(res.unread_count);
        if (CURRENT_VIEW === "messages") {
          MESSAGES_DATA = res.messages || [];
          renderMessagesList();
        } else if (res.unread_count > prevCount && prevCount >= 0) {
          var diff = res.unread_count - prevCount;
          var latest = (res.messages && res.messages[0]) ? res.messages[0] : null;
          if (latest) {
            toast("🔔 " + latest.title + "\n" + latest.content);
          } else {
            toast("🐾 收到 " + diff + " 条新消息提醒！");
          }
        }
      }
    }).catch(function(){});
  }
  setInterval(pollMessages, 15000);
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
                return self.send_json({
                    "event": dict(ev),
                    "owner": {"uid": ev["uid"], "name": users[ev["uid"]]["name"]},
                    "comments": [dict(c) for c in cmts],
                    "filter_date": req_date
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
                return self.send_json({
                    "uid": uid,
                    "name": users[uid]["name"],
                    "unread_count": get_unread_notifications_count(uid)
                })
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
                uid = self.authed()
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
                return self.send_json(week_payload(d, uid=uid))
            if path == "/api/month":
                uid = self.authed()
                if self.authed() is None:
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
                stype = (qs.get("type") or ["day"])[0]
                force = (qs.get("refresh") or ["0"])[0] == "1"
                return self.send_json(generate_period_summary(stype, force_refresh=force))
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
            if path == "/api/notifications/poll":
                uid = self.current_uid()
                if not uid:
                    qs = parse_qs(urlparse(self.path).query)
                    uid = (qs.get("uid") or [""])[0]
                if not uid or uid not in ("a", "b"):
                    return self.send_json({"ok": True, "notifications": []})
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
                    return self.send_json({
                        "ok": True,
                        "uid": uid,
                        "name": users[uid]["name"],
                        "unread_count": get_unread_notifications_count(uid)
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
                uid = self.current_uid()
                if not uid:
                    uid = d.get("uid") or "a"
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
                stype = d.get("type") or "day"
                summary, sent_count = push_summary_to_both(stype)
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

        # 微信与系统原生推送新日程提醒给对方狗狗
        users = get_users()
        other_uid = "b" if uid == "a" else "a"
        author_name = users[uid]["name"]
        wd_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        when_str = f"每周{wd_names[weekday]} {tstart}~{tend}" if repeat == "weekly" else f"{ev_date} {tstart}~{tend}"
        loc_str = f" · 📍 {location}" if location else ""
        notice_title = f"🐾 {author_name} 添加了新日程"
        notice_content = f"【{title}】{loc_str}，时间：{when_str}"
        push_system_notice(other_uid, notice_title, notice_content, f"event:{eid}")

        target_token = users[other_uid]["wx_uid"]
        if target_token:
            note_str = f"<br>📝 备注：{note}" if note else ""
            msg_title = f"🐾 {author_name} 添加了新日程"
            html_content = (
                f"🐶 <strong>{author_name}</strong> 记下了新日程：<br>"
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
        
        # 仅管理员(uid="a", 汤寰宇)有权更新大模型配置
        llm_meta_updates = []
        if uid == "a" and "llm_api_base" in d:
            llm_base = str(d.get("llm_api_base") or "").strip()[:200]
            llm_key = str(d.get("llm_api_key") or "").strip()[:200]
            llm_model = str(d.get("llm_model") or "deepseek-chat").strip()[:60]
            llm_meta_updates = [
                ("llm_api_base", llm_base),
                ("llm_api_key", llm_key),
                ("llm_model", llm_model)
            ]

        with LOCK:
            CONN.execute("UPDATE users SET name=?, week1=?, wx_uid=? WHERE uid='a'", (na, w1(d.get("week1_a")), wx_a))
            CONN.execute("UPDATE users SET name=?, week1=?, wx_uid=? WHERE uid='b'", (nb, w1(d.get("week1_b")), wx_b))
            for k, v in [("window_start", ws), ("window_end", we), ("min_gap", str(mg))] + llm_meta_updates:
                CONN.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
            if llm_meta_updates:
                CONN.execute("DELETE FROM summary_cache")
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
            "users": {u: {"name": users[u]["name"], "wx_uid": users[u].get("wx_uid", "")} for u in ("a", "b")},
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
        for idx, p_b64 in enumerate(photos_base64[:18]):  # 放宽至最多18张
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
        photos_base64 = d.get("photos") or []  # 新增的照片
        keep_photos = d.get("keep_photos") or []  # 保留的原有照片文件名

        if not title:
            return self.send_json({"error": "主题不能为空"}, 400)
        if not dt or not DATE_RE.match(dt):
            return self.send_json({"error": "日期无效"}, 400)

        os.makedirs(PHOTOS_DIR, exist_ok=True)
        import base64
        new_saved_photos = []
        for idx, p_b64 in enumerate(photos_base64[:18]):  # 放宽至最多18张
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


def cron_summary_worker():
    last_day_pushed = ""
    last_week_pushed = ""
    last_month_pushed = ""
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            # 1. 每日 22:30 晚安总结
            if now.hour == 22 and now.minute >= 30 and last_day_pushed != today_str:
                push_summary_to_both("day")
                last_day_pushed = today_str
                print(f"[Cron] 每日晚安总结推送完成: {today_str}", flush=True)

            # 2. 每周日 21:00 心动周报 (weekday 6 为周日)
            if now.weekday() == 6 and now.hour == 21 and now.minute >= 0 and last_week_pushed != today_str:
                push_summary_to_both("week")
                last_week_pushed = today_str
                print(f"[Cron] 周度心动周报推送完成: {today_str}", flush=True)

            # 3. 每月 1 号 09:00 月度时光胶囊
            ym_str = now.strftime("%Y-%m")
            if now.day == 1 and now.hour == 9 and now.minute >= 0 and last_month_pushed != ym_str:
                push_summary_to_both("month")
                last_month_pushed = ym_str
                print(f"[Cron] 月度时光胶囊推送完成: {ym_str}", flush=True)

        except Exception as e:
            sys.stderr.write(f"[Cron Worker Error]: {e}\n")
        time.sleep(30)


def main():
    global SECRET
    SECRET = load_secret()
    init_db()
    threading.Thread(target=cron_summary_worker, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("sched-share listening on 0.0.0.0:%d, data dir %s" % (PORT, DATA_DIR), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
