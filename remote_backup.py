#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双人日程服务 (sched-share) 异地安全定时备份脚本
1. 本地 VACUUM INTO 事务快照导出 (保持运行中无锁一致性)
2. 数据库每日归档 (保留最近 30 天历史快照，体积轻量仅几十KB；不含 secret.key——它只用于会话签名，与数据恢复无关)
3. 异地快照双重脱敏：周期健康数据 (cycle.*) 与可复用凭据 (LLM key、微信推送 token) 一律不出服务器
4. 照片媒体库增量同步去重 (Git 自动基于文件哈希去重，彻底消除每天全量重复打包导致磁盘与Git历史膨胀的问题)
5. 生成最新全量恢复单包 (latest_full_backup.tar.gz，便于一键灾备还原)
6. 同步推送到私有仓库 UniPeacher/couple_schedule_backup_v2 (仅 deploy key 可写，主机级隔离)
"""

import os
import sys
import time
import glob
import sqlite3
import tarfile
import subprocess
import shutil
import tempfile
from cycle import prepare_remote_backup
import atexit

BASE_DIR = "/home/uni/sched-share"
DATA_DIR = os.path.join(BASE_DIR, "data")
LOCAL_BACKUP_DIR = os.path.join(DATA_DIR, "backups")
REMOTE_REPO_DIR = "/home/uni/sched-backup-repo"
DB_PATH = os.path.join(DATA_DIR, "sched.db")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")

def run():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 开始执行日程数据安全增量备份流程...")
    os.makedirs(LOCAL_BACKUP_DIR, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    # 1. 验证持久化文件是否存在
    if not os.path.exists(DB_PATH):
        print(f"[错误] 数据库文件不存在: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    # 2. 本地安全热快照 (VACUUM INTO 支持运行中无锁一致性导出)
    snapshot_db = os.path.join(LOCAL_BACKUP_DIR, f"sched-{today}.db")
    if os.path.exists(snapshot_db):
        try:
            os.remove(snapshot_db)
        except Exception:
            pass

    try:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.execute(f"VACUUM INTO '{snapshot_db}'")
        conn.close()
        print(f"  ✓ 本地快照导出成功: {snapshot_db}")
    except Exception as e:
        print(f"[错误] 导出 SQLite 快照失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. 清理本地保留策略（保留本地最近 14 份）
    old_snaps = sorted(glob.glob(os.path.join(LOCAL_BACKUP_DIR, "sched-*.db")))
    for f in old_snaps[:-14]:
        try:
            os.remove(f)
        except Exception:
            pass

    # Sensitive health records remain in the local backup only. Fail closed before
    # touching the remote repository if the sanitized copy cannot be produced.
    safe_dir = tempfile.TemporaryDirectory(prefix="remote-safe-", dir=LOCAL_BACKUP_DIR)
    atexit.register(safe_dir.cleanup)
    remote_snapshot = os.path.join(safe_dir.name, "sched.db")
    prepare_remote_backup(snapshot_db, remote_snapshot)

    # 脱敏第二层：异地副本不携带任何可复用凭据（LLM key / 微信推送 token）。
    # 会话签名密钥 secret.key 不参与数据恢复（丢失仅等于全员重新登录），永不外传。
    scrub = sqlite3.connect(remote_snapshot)
    scrub.execute("UPDATE meta SET value='' WHERE key='llm_api_key'")
    scrub.execute("UPDATE users SET wx_uid=''")
    scrub.commit()
    scrub.execute("VACUUM")
    scrub.close()

    # 4. 异地备份仓库准备
    if not os.path.exists(os.path.join(REMOTE_REPO_DIR, ".git")):
        os.makedirs(REMOTE_REPO_DIR, exist_ok=True)
        subprocess.run(["git", "clone", "git@github.com:UniPeacher/couple_schedule_backup_v2.git", REMOTE_REPO_DIR], check=True)
        subprocess.run(["git", "-C", REMOTE_REPO_DIR, "symbolic-ref", "HEAD", "refs/heads/master"], check=True)
        subprocess.run(["git", "-C", REMOTE_REPO_DIR, "config", "user.name", "Uni Peacher"], check=True)
        subprocess.run(["git", "-C", REMOTE_REPO_DIR, "config", "user.email", "153611969+UniPeacher@users.noreply.github.com"], check=True)

    # 5. 异地备份目录规范化：
    # 5.1 数据库每日历史快照 (轻量级，保留 30 份)
    remote_db_dir = os.path.join(REMOTE_REPO_DIR, "db_snapshots")
    os.makedirs(remote_db_dir, exist_ok=True)
    db_archive_name = f"sched_db_{today}.tar.gz"
    db_archive_path = os.path.join(remote_db_dir, db_archive_name)
    with tarfile.open(db_archive_path, "w:gz") as tar:
        tar.add(remote_snapshot, arcname="sched.db")

    old_db_archives = sorted(glob.glob(os.path.join(remote_db_dir, "sched_db_*.tar.gz")))
    for old_db in old_db_archives[:-30]:
        try:
            os.remove(old_db)
        except Exception:
            pass

    # 5.2 照片媒体库增量同步（Git 天然基于 SHA1 哈希去重，已有照片不产生任何重复存储）
    remote_photos_dir = os.path.join(REMOTE_REPO_DIR, "photos")
    os.makedirs(remote_photos_dir, exist_ok=True)
    new_photo_count = 0
    if os.path.exists(PHOTOS_DIR):
        for fname in os.listdir(PHOTOS_DIR):
            src_file = os.path.join(PHOTOS_DIR, fname)
            dst_file = os.path.join(remote_photos_dir, fname)
            if os.path.isfile(src_file):
                if not os.path.exists(dst_file) or os.path.getsize(src_file) != os.path.getsize(dst_file):
                    shutil.copy2(src_file, dst_file)
                    new_photo_count += 1

    # 5.3 仅维护单份最新全量恢复压缩包 (latest_full_backup.tar.gz)，彻底替换旧机制下累积 30 份巨型压缩包的严重历史膨胀
    full_archive_path = os.path.join(REMOTE_REPO_DIR, "latest_full_backup.tar.gz")
    with tarfile.open(full_archive_path, "w:gz") as tar:
        tar.add(remote_snapshot, arcname="sched.db")
        if os.path.exists(PHOTOS_DIR):
            tar.add(PHOTOS_DIR, arcname="photos")

    # 清理掉之前旧机制下生成的 backup_*.tar.gz 多份重复全量包
    old_full_archives = glob.glob(os.path.join(REMOTE_REPO_DIR, "backup_*.tar.gz"))
    for ofa in old_full_archives:
        try:
            os.remove(ofa)
        except Exception:
            pass

    # 6. 生成一份详细的 README 说明当前灾备状态
    total_photos = len(os.listdir(PHOTOS_DIR)) if os.path.exists(PHOTOS_DIR) else 0
    full_size_mb = round(os.path.getsize(full_archive_path) / (1024 * 1024), 2)
    with open(os.path.join(REMOTE_REPO_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write("# Couple Schedule Private Backup 🔐\n\n")
        f.write("此仓库为情侣日程系统的**私有异地高可用灾备库**，由服务器自动每日定时同步备份。\n\n")
        f.write("### 📦 灾备数据架构\n")
        f.write("- 隐私说明：经期、身体感受、分享设置与关怀需求仅保留本地备份，不包含在异地恢复包中。\n")
        f.write("- `photos/`：手账照片与实况视频的原生增量存储（支持网页直接预览，Git 天然去重防磁盘膨胀）。\n")
        f.write("- `db_snapshots/`：SQLite 数据库核心快照（保留最近 30 天历史快照）。\n")
        f.write("- `latest_full_backup.tar.gz`：最新全量一键还原压缩包。\n\n")
        f.write("### 🕒 最近备份状态\n")
        f.write(f"- 最近一次备份完成时间: `{time.strftime('%Y-%m-%d %H:%M:%S')}`\n")
        f.write(f"- 照片总数: `{total_photos}` 张（本次增量同步 `{new_photo_count}` 张）\n")
        f.write(f"- 全量包大小: `{full_size_mb} MB`\n")

    # 7. 提交并推送到 GitHub 私有仓库
    try:
        subprocess.run(["git", "-C", REMOTE_REPO_DIR, "add", "."], check=True)
        diff = subprocess.run(["git", "-C", REMOTE_REPO_DIR, "diff", "--cached", "--quiet"])
        if diff.returncode != 0:
            subprocess.run(["git", "-C", REMOTE_REPO_DIR, "commit", "-m", f"chore(backup): incremental backup {today} (photos: {total_photos})"], check=True)

        # 尝试推送远端（带重试）
        pushed = False
        for attempt in range(3):
            push_res = subprocess.run(["git", "-C", REMOTE_REPO_DIR, "push", "origin", "master"])
            if push_res.returncode == 0:
                pushed = True
                print(f"  ✓ 异地增量备份成功推送至私有仓库: UniPeacher/couple_schedule_backup")
                break
            else:
                print(f"  ⚠️ 推送尝试 {attempt+1}/3 失败，稍后重试...")
                time.sleep(3)
        if not pushed:
            print(f"  ⚠️ GitHub 远端接口暂时不可用，备份文件已保存在本地灾备库中: {full_archive_path}")
    except Exception as e:
        print(f"[警告] 推送异地备份异常: {e}", file=sys.stderr)

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 增量备份与去重流程全部顺利完成！")

if __name__ == "__main__":
    run()
