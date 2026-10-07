#!/usr/bin/env python3
"""每日备份：VACUUM INTO 生成带日期的快照，保留最近 14 份。由宿主机 cron 调 docker exec 执行。"""
import glob
import os
import sqlite3
import time

os.makedirs("/data/backups", exist_ok=True)
out = "/data/backups/sched-%s.db" % time.strftime("%F")
src = sqlite3.connect("/data/sched.db")
src.execute("VACUUM INTO '%s'" % out)
src.close()
old = sorted(glob.glob("/data/backups/sched-*.db"))
for f in old[:-14]:
    if os.path.abspath(f) != os.path.abspath(out):
        os.remove(f)
print("backup ->", out)
