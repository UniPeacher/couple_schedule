# 架构与开发

- [`app.py`](../app.py) 启动基于 Python 标准库的 HTTP 服务，管理认证、SQLite 连接和接口。旅行清单、设置、周期、通知和媒体处理各有独立模块。
- [`web_assets.py`](../web_assets.py) 负责页面模板、静态资源路径及 ETag。磁盘布局在 [`web/`](../web/) 下，浏览器继续使用 `/app.js`、`/img/…` 等固定 URL。
- [`web/app.js`](../web/app.js) 管理页面切换、共享状态和通用交互；[`web/pages/trips.js`](../web/pages/trips.js) 负责旅行页面内部的渲染、操作及状态。新增资源如需离线可用，同时更新模板和 [`web/sw.js`](../web/sw.js) 的预缓存 URL。

## 本地运行

需要 Python 3.12+ 和 FFmpeg。使用临时数据目录，避免开发时读写已有部署的数据库：

```bash
DATA_DIR=$(mktemp -d) PORT=18795 python3 app.py
```

## 回归测试

```bash
python3 -m unittest discover -s tests
node --test tests/calendar_album_test.cjs tests/dialogs_test.cjs tests/diary_viewer_test.cjs
```

Python 测试用临时 SQLite 数据库；媒体转换用例需要 `ffmpeg` 和 `ffprobe`。Playwright 用例位于 [`tests/`](../tests/)，浏览器测试只使用同目录提供的隔离 fixture 服务，不要把可写测试指向实际部署。
