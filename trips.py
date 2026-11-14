#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旅行清单数据层：分类模板、打包状态统计、条目排序与写入。

路由分发仍留在 app.Handler；数据库连接与全局锁经 app 命名空间在调用期取用
（app.CONN / app.LOCK / app.get_users），与测试「import app 后改写 app 全局」
的既有约定一致。
"""
import time
from datetime import date

import app

TRIP_CATEGORIES = [
    ("doc", "证件"), ("toilet", "洗漱"), ("skin", "护肤化妆"),
    ("cloth", "衣物"), ("tech", "电子"), ("med", "药品"), ("other", "其他"),
]
TRIP_CATEGORY_KEYS = [k for k, _ in TRIP_CATEGORIES]

# 新建旅行时可选套用的常用清单：(分组, 名称, 他带, 她带)，空字符串表示这一方不用带
TRIP_TEMPLATE = [
    ("doc", "身份证", "1", "1"), ("doc", "学生证", "1", "1"),
    ("doc", "银行卡", "1", "1"), ("doc", "现金", "1", ""),
    ("toilet", "牙刷牙膏", "1", "1"), ("toilet", "洗发水/护发素", "1", "1"),
    ("toilet", "沐浴露", "1", "1"), ("toilet", "一次性浴巾", "1", "1"),
    ("toilet", "洗脸巾", "1", "1"),
    ("skin", "防晒", "", "1"), ("skin", "水乳", "", "1"),
    ("skin", "粉底液", "", "1"), ("skin", "口红", "", "1"),
    ("skin", "面膜", "", "4"),
    ("cloth", "换洗衣物", "1", "1"), ("cloth", "睡衣", "1", "1"),
    ("cloth", "外套", "1", "1"), ("cloth", "袜子", "1", "1"),
    ("tech", "充电器", "1", "1"), ("tech", "充电宝", "1", "1"),
    ("tech", "耳机", "1", "1"), ("tech", "自拍杆", "1", ""),
    ("med", "创可贴", "1", ""), ("med", "常用药", "1", ""),
    ("other", "纸巾", "1", ""), ("other", "雨伞", "1", ""),
    ("other", "卫生巾", "", "1"),
]
TRIP_TEMPLATE_BY_NAME = {name: (a, b) for _, name, a, b in TRIP_TEMPLATE}


def _trip_status(start, end, today):
    if end and end < today:
        return "done"
    if start and start > today:
        return "upcoming"
    if not start and not end:
        return "upcoming"
    return "ongoing"


def _trip_item_order(trip_id, category):
    row = app.CONN.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM trip_items WHERE trip_id=? AND category=?",
        (trip_id, category)).fetchone()
    return row["n"] if row else 1


def _insert_trip_item(trip_id, category, name, qty_a="1", qty_b="1"):
    if category not in TRIP_CATEGORY_KEYS:
        category = "other"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    app.CONN.execute(
        "INSERT INTO trip_items(trip_id, category, name, qty, note, qty_a, qty_b, is_packed, sort_order, updated_at) "
        "VALUES(?,?,?,'','',?,?,0,?,?)",
        (trip_id, category, name, qty_a, qty_b, _trip_item_order(trip_id, category), now))


def trips_overview():
    today = date.today().isoformat()
    users = app.get_users()
    with app.LOCK:
        rows = app.CONN.execute(
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
    users = app.get_users()
    with app.LOCK:
        row = app.CONN.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not row:
            return None
        items = [dict(r) for r in app.CONN.execute(
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
