#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""消息通知三通道：ntfy 实时弹窗 / 系统消息中心落库 / 微信推送（虾推啥·息知·Server酱）。

数据库连接与全局锁仍归 app 所有，这里经 app 命名空间在调用期取用
（app.LOCK / app.CONN），与测试「import app 后改写 app 全局」的既有约定一致。
"""
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

import app

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
        with app.LOCK:
            app.CONN.execute(
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


def get_unread_notifications_count(uid):
    if not uid:
        return 0
    try:
        with app.LOCK:
            row = app.CONN.execute("SELECT COUNT(*) c FROM notifications WHERE target_uid=? AND is_read=0", (uid,)).fetchone()
            return row["c"] if row else 0
    except Exception:
        return 0
