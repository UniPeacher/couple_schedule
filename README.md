# 线条小狗 · 两人日程

给两个人用的自托管日程和手账。双方各有账号，共用一份日程；应用自动标出时间冲突与共同空闲，可以直接用浏览器打开，也有 Android 客户端。

## 能做什么

| 功能 | 用途 |
| --- | --- |
| 共享课表 | 管理单次和每周重复的日程，支持单双周、单周改期和停课 |
| 足迹手账 | 从月历回看照片、实况、纪念日与恋爱节点，按月生成回顾 |
| 旅行清单与心愿 | 分别勾选两人的行李，记录一起想做的事 |
| 留言与提醒 | 在日程下留言，集中查看消息与提醒 |
| 周期记录 | 私密记录身体状态，按授权分享日期、预估与提醒 |

## 用 Docker Compose 启动

需要 Docker 和 Compose 插件。在 Linux 主机上，以拥有数据目录的普通用户启动：

```bash
git clone https://github.com/UniPeacher/couple_schedule.git
cd couple_schedule
mkdir -p data
SCHED_UID=$(id -u) SCHED_GID=$(id -g) docker compose up -d --build
```

- 同一台机器访问 [http://127.0.0.1:8795](http://127.0.0.1:8795)。其他设备访问请配置 HTTPS 反向代理，不需要公开应用端口。
- 首次启动在 `data/initial-passwords.txt` 写入两个账号的随机密码。登录后可在「设置」里修改昵称、课表和密码。
- `data/` 保存 SQLite 数据库、照片和会话签名文件。升级时保留该目录，并重新构建镜像。详见[部署与备份](docs/deployment.md)。

## Android

从 [Releases](https://github.com/UniPeacher/couple_schedule/releases) 获取已发布 APK，或从 [Build Android APK](https://github.com/UniPeacher/couple_schedule/actions/workflows/build-apk.yml) 下载某次成功构建的 `couple-schedule-apk`。自托管服务地址可以从应用设置或加载失败页修改。[安装、服务地址与签名说明](docs/android.md)

## 代码导航

| 位置 | 内容 |
| --- | --- |
| [`app.py`](app.py) | HTTP 入口、日程接口与 SQLite 初始化 |
| [`web/`](web/) · [`web_assets.py`](web_assets.py) | 页面模板、原生 JS/CSS、图片、Service Worker 与静态资源路由 |
| [`trips.py`](trips.py) · [`images.py`](images.py) · [`notices.py`](notices.py) | 旅行清单、照片处理、站内通知 |
| [`android/`](android/) | Android WebView 客户端与通知桥接 |
| [`tests/`](tests/) · [`docs/`](docs/) | 回归测试与项目文档 |

开发与测试命令见[架构与开发](docs/development.md)；模块说明见[周期记录](docs/cycle.md)与[设置页](docs/settings.md)。

代码使用 [MIT License](LICENSE)。
