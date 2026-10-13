#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日备份：VACUUM INTO 生成带日期的快照，保留最近 14 份。由宿主机 cron 调 docker exec 执行。"""
import glob
import os
import sqlite3
import time
import sys

backup_dir = "/data/backups"
os.makedirs(backup_dir, exist_ok=True)
today = time.strftime("%F")
out = os.path.join(backup_dir, f"sched-{today}.db")

# 如果当天已有备份，先移除旧文件避免 VACUUM INTO 报错
if os.path.exists(out):
    try:
        os.remove(out)
    except Exception as e:
        sys.stderr.write(f"[Warn] Cannot remove existing snapshot: {e}\n")

try:
    src = sqlite3.connect("/data/sched.db", timeout=20)
    src.execute(f"VACUUM INTO '{out}'")
    src.close()
    print("backup ->", out)
except Exception as e:
    sys.stderr.write(f"[Error] Backup failed: {e}\n")
    sys.exit(1)

old = sorted(glob.glob(os.path.join(backup_dir, "sched-*.db")))
for f in old[:-14]:
    if os.path.abspath(f) != os.path.abspath(out):
        try:
            os.remove(f)
        except Exception:
            pass
