"""页面模板、静态资源路由与 HTTP 缓存。"""
import hashlib
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_FILES = {
    "/app.js": "app.js",
    "/app.css": "app.css",
    "/cycle2.js": "cycle2.js",
    "/cycle2.css": "cycle2.css",
    "/settings_ui.js": "settings_ui.js",
    "/settings_ui.css": "settings_ui.css",
    "/trips.js": "pages/trips.js",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/sw.js": "sw.js",
}
_FILE_TYPES = {".js": "text/javascript", ".css": "text/css", ".webmanifest": "application/manifest+json"}
_IMG_FILES = {
    "dogheads": "dogheads.png", "dogrest": "dogrest.png", "dogrest.webp": "dogrest.webp",
    "dog-add": "dog-add.png", "dog-set": "dog-set.png", "dog-set.webp": "dog-set.webp",
    "dog-wave": "dog-wave.png", "dog-couple": "dog-couple.png", "dog-couple.webp": "dog-couple.webp",
    **{f"dog-walk{i}": f"dog-walk{i}.png" for i in range(1, 5)},
    **{f"dog-walk{i}.webp": f"dog-walk{i}.webp" for i in range(1, 5)},
}

_template_cache = None
_image_cache = {}


def get_page_html():
    """加载页面模板，文件修改后刷新缓存。"""
    global _template_cache
    file = WEB_DIR / "template_page.html"
    try:
        mtime = file.stat().st_mtime_ns
        cached = _template_cache
        if cached is None or cached[0] != mtime:
            cached = _template_cache = (mtime, file.read_text(encoding="utf-8"))
        return cached[1]
    except OSError as exc:
        raise RuntimeError(f"template_page.html 缺失或不可读: {exc}") from exc


def serve_file(handler, path):
    file = WEB_DIR / STATIC_FILES[path]
    stat = file.stat()
    etag = '"%x-%x"' % (stat.st_mtime_ns, stat.st_size)
    headers = [("ETag", etag), ("Cache-Control", "no-cache")]
    mime = _FILE_TYPES[file.suffix] + "; charset=utf-8"
    if etag in handler.headers.get("If-None-Match", ""):
        return handler.send_bytes(304, b"", mime, extra=headers)
    return handler.send_bytes(200, file.read_bytes(), mime, extra=headers)


def get_image_asset(base):
    if base in _image_cache:
        return _image_cache[base]
    name = _IMG_FILES.get(base)
    if not name:
        return None, None
    try:
        data = (WEB_DIR / "images" / name).read_bytes()
    except FileNotFoundError:
        return None, None
    etag = f'"{hashlib.md5(data).hexdigest()[:16]}"'
    _image_cache[base] = (data, etag)
    return data, etag


def serve_image(handler, name):
    # 保留 /img/<base>.png 的历史别名，已登记的 WebP 则按原格式返回。
    base = name if name.endswith(".webp") and name in _IMG_FILES else name.split(".")[0]
    data, etag = get_image_asset(base)
    if not data:
        return handler.send_json({"error": "not found"}, 404)
    mime = "image/webp" if base.endswith(".webp") else "image/png"
    headers = [("ETag", etag), ("Cache-Control", "public, max-age=2592000, immutable")]
    if handler.headers.get("If-None-Match", "").strip() == etag:
        return handler.send_bytes(304, b"", mime, extra=headers)
    return handler.send_bytes(200, data, mime, extra=headers)
