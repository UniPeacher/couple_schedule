#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片与媒体资产：/img/ 白名单映射、内存级图片缓存、ffmpeg 缩略图、
实况照片（Motion Photo）合成。纯文件/字节操作，不依赖数据库。"""
import contextlib
import hashlib
import os
import queue
import subprocess
import sys
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# /img/<base> 白名单 → 磁盘文件；前端实际引用的资产才在此登记
IMG_FILES = {"dogheads": "dogheads.png", "dogrest": "dogrest.png", "dogrest.webp": "dogrest.webp",
             "dog-add": "dog-add.png", "dog-set": "dog-set.png",
             "dog-wave": "dog-wave.png", "dog-walk1": "dog-walk1.png", "dog-walk2": "dog-walk2.png",
             "dog-walk3": "dog-walk3.png", "dog-walk4": "dog-walk4.png",
             "dog-couple": "dog-couple.png", "dog-couple.webp": "dog-couple.webp", "dog-set.webp": "dog-set.webp", "dog-walk1.webp": "dog-walk1.webp", "dog-walk2.webp": "dog-walk2.webp", "dog-walk3.webp": "dog-walk3.webp", "dog-walk4.webp": "dog-walk4.webp"}

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


# ---------------------------------------------------------------- 照片缩略图

_THUMB_LOCK = threading.Lock()

def ensure_thumb(src_fp, thumb_fp):
    """用 ffmpeg 生成 720px 缩略图（best-effort：失败返回 False，调用方回退原图）。

    实况照片是 JPEG 尾部拼 MP4 的 motion photo，强制 -f mjpeg 只解首帧，
    比默认探测快 4 倍；PNG 等其他格式走自动探测重试。
    """
    if os.path.exists(thumb_fp):
        return True
    if not os.path.exists(src_fp):
        return False
    tmp_fp = thumb_fp + ".tmp.jpg"
    ok = False
    with _THUMB_LOCK:
        if os.path.exists(thumb_fp):
            return True
        for extra in (["-f", "mjpeg"], []):
            try:
                r = subprocess.run(
                    ["ffmpeg", "-y"] + extra + ["-i", src_fp,
                     "-vf", "scale=w=min(iw\\,720):h=-2", "-q:v", "7",
                     "-frames:v", "1", tmp_fp],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                if r.returncode == 0 and os.path.exists(tmp_fp) and os.path.getsize(tmp_fp) > 500:
                    os.replace(tmp_fp, thumb_fp)
                    ok = True
                    break
            except Exception as e:
                sys.stderr.write(f"[thumb error] {src_fp}: {e}\n")
            finally:
                if os.path.exists(tmp_fp):
                    with contextlib.suppress(Exception):
                        os.remove(tmp_fp)
    return ok


# 大图查看用的衍生图：手机上大图最长边也就 ~1000px，原图动辄 20MB、
# 4096px 宽，直接下原图要好几秒，还会和实况视频抢同一条带宽。
_VIEW_MAX = 1600


def ensure_view_image(src_fp, view_fp):
    """生成大图查看用的衍生图（最长边 1600px）。best-effort：失败返回 False，调用方回退原图。"""
    if os.path.exists(view_fp):
        return True
    if not os.path.exists(src_fp):
        return False
    tmp_fp = view_fp + ".tmp.jpg"
    ok = False
    with _THUMB_LOCK:
        if os.path.exists(view_fp):
            return True
        for extra in (["-f", "mjpeg"], []):
            try:
                r = subprocess.run(
                    ["ffmpeg", "-y"] + extra + ["-i", src_fp,
                     "-vf", "scale=w=min(iw\\,%d):h=-2" % _VIEW_MAX, "-q:v", "5",
                     "-frames:v", "1", tmp_fp],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
                if r.returncode == 0 and os.path.exists(tmp_fp) and os.path.getsize(tmp_fp) > 500:
                    os.replace(tmp_fp, view_fp)
                    ok = True
                    break
            except Exception as e:
                sys.stderr.write("[view error] %s: %s\n" % (src_fp, e))
            finally:
                if os.path.exists(tmp_fp):
                    with contextlib.suppress(Exception):
                        os.remove(tmp_fp)
    return ok


# ---------------------------------------------------------------- 落盘后处理队列

# 手账照片写入磁盘后，转码、合成实况照片、生成缩略图都交给这一条后台线程串行跑。
# 单次提交多张 LIVE 时这些 ffmpeg 调用实测相加要几十秒，绝不能压在 HTTP 响应里：
# 客户端会先超时断开（nginx 侧就是 499），用户看到"还在上传"而服务端其实还在干活。
_MEDIA_QUEUE = queue.Queue()


def _media_worker():
    while True:
        job = _MEDIA_QUEUE.get()
        try:
            job()
        except Exception as e:
            sys.stderr.write(f"[media worker] {e}\n")
        finally:
            _MEDIA_QUEUE.task_done()


_media_thread = threading.Thread(target=_media_worker, name="media-worker", daemon=True)
_media_thread.start()


def enqueue_media_job(job):
    """把 ffmpeg 处理排进后台队列，调用方立即返回。"""
    _MEDIA_QUEUE.put(job)


def ensure_live_preview(video_fp):
    """生成独立的 H.264 播放副本；原视频保留给导出和实况照片合成。"""
    preview_fp = os.path.join(os.path.dirname(video_fp), "pv_" + os.path.basename(video_fp))
    if os.path.exists(preview_fp):
        return True
    if not os.path.exists(video_fp):
        return False
    tmp_fp = preview_fp + ".tmp.mp4"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-threads", "1", "-i", video_fp,
             "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
             "-threads", "1", "-preset", "veryfast", "-crf", "26",
             "-vf", "scale=w=720:h=720:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
             "-movflags", "+faststart", tmp_fp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        if r.returncode == 0 and os.path.exists(tmp_fp) and os.path.getsize(tmp_fp) > 1000 \
                and os.path.exists(video_fp):
            os.replace(tmp_fp, preview_fp)
            return True
    except Exception as e:
        sys.stderr.write(f"[media worker] 实况播放副本失败: {e}\n")
    finally:
        if os.path.exists(tmp_fp):
            with contextlib.suppress(Exception):
                os.remove(tmp_fp)
    return False


def finalize_media(photo_fp, video_fp):
    """后台补处理已落盘的手账媒体：视频转码 → 合成实况照片 → 生成缩略图。

    只接文件路径，不持有字节：一次提交十几张 LIVE 时原始字节可达数百 MB，
    排在队列里会把这台 1GB 内存的机器压到 OOM。
    video_fp 里已经是原始视频，所以转码前后都随时可预览；每一步都是 best-effort，
    失败就保留上一步的产物，不影响照片本身已可访问。
    photo_fp 是这条媒体的锚点：照片已进回收站（记录被删）就不再产出任何产物。
    """
    if not os.path.exists(photo_fp):
        return
    if video_fp and os.path.exists(video_fp) and not _moov_is_front(video_fp):
        tmp_fp = video_fp + ".tmp.mp4"
        try:
            # 源视频通常已是 H.264/AAC：能直接复制流就不重编码，省掉最贵的一步
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", video_fp, "-c", "copy",
                 "-movflags", "+faststart", tmp_fp],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if not (r.returncode == 0 and os.path.exists(tmp_fp) and os.path.getsize(tmp_fp) > 1000):
                r = subprocess.run(
                    ["ffmpeg", "-y", "-i", video_fp,
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                     "-vf", "scale='min(1080,iw)':-2", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                     tmp_fp],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            # 转码期间这篇手账可能已被删除（照片已进回收站），此时不再产出
            if r.returncode == 0 and os.path.exists(tmp_fp) and os.path.getsize(tmp_fp) > 1000 \
                    and os.path.exists(photo_fp):
                os.replace(tmp_fp, video_fp)
        except Exception as e:
            sys.stderr.write(f"[media worker] 转码实况视频失败 {video_fp}: {e}\n")
        finally:
            with contextlib.suppress(Exception):
                os.remove(tmp_fp)

    # 静态封面图 + MP4 视频轨合成为单文件实况照片，直接下载 .jpg 也是真·实况照片
    if video_fp and os.path.exists(video_fp) and os.path.exists(photo_fp):
        tmp_fp = photo_fp + ".tmp.jpg"
        try:
            with open(photo_fp, "rb") as pf:
                photo_bytes = pf.read()
            with open(video_fp, "rb") as vf:
                motion_jpg = synthesize_motion_photo_bytes(photo_bytes, vf.read())
            # 原子替换：合成期间并发的大图/缩略图请求不会读到半截文件
            with open(tmp_fp, "wb") as pf:
                pf.write(motion_jpg)
            os.replace(tmp_fp, photo_fp)
        except Exception as e:
            sys.stderr.write(f"[media worker] 合成实况照片失败 {photo_fp}: {e}\n")
            with contextlib.suppress(Exception):
                os.remove(tmp_fp)

    if os.path.exists(photo_fp):
        photo_dir = os.path.dirname(photo_fp)
        base = os.path.basename(photo_fp)
        with contextlib.suppress(Exception):
            ensure_thumb(photo_fp, os.path.join(photo_dir, "tb_" + base))
        if photo_fp.lower().endswith(".jpg"):
            with contextlib.suppress(Exception):
                ensure_view_image(photo_fp, os.path.join(photo_dir, "vw_" + base))
        if video_fp and os.path.exists(video_fp):
            ensure_live_preview(video_fp)


def _moov_is_front(video_fp):
    """moov 原子是否在 mdat 之前（faststart）。在尾部时浏览器要先发一个尾部 Range
    请求拿索引，移动网络下起播会多等一两秒。"""
    try:
        size = os.path.getsize(video_fp)
        moov_at = mdat_at = -1
        with open(video_fp, "rb") as f:
            head = f.read(min(size, 1 << 20))
            pos = head.find(b"moov")
            if pos > 0:
                moov_at = pos
            pos = head.find(b"mdat")
            if pos > 0:
                mdat_at = pos
        if moov_at > 0 and mdat_at > 0:
            return moov_at < mdat_at
        if moov_at < 0:
            # moov 不在前 1MB：一定是尾部，需要重封装
            with open(video_fp, "rb") as f:
                f.seek(max(0, size - (1 << 20)))
                tail = f.read()
            return b"moov" not in tail
        return True
    except Exception:
        return True


# ---------------------------------------------------------------- 实况照片合成

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
