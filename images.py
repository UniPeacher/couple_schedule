#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片与媒体资产：/img/ 白名单映射、内存级图片缓存、ffmpeg 缩略图、
实况照片（Motion Photo）合成。纯文件/字节操作，不依赖数据库。"""
import contextlib
import hashlib
import os
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
    import subprocess
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
