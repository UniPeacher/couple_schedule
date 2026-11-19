# 部署与备份

## 启动与访问

按[快速开始](../README.md#用-docker-compose-启动)创建 `data/` 并启动。容器以 `SCHED_UID:SCHED_GID` 运行（默认 `1000:1000`）；使用宿主的实际数据目录所有者。固定部署可将这两个变量写入与 `compose.yaml` 同目录的 `.env`，后续启动和升级沿用。

应用默认只在宿主的 `127.0.0.1:8795` 监听。其他设备通过 HTTPS 反向代理访问；代理应允许照片与实况上传（应用 JSON 请求上限 150 MiB），保留视频的 `Range` 请求，并将外部来源与协议转发给应用。

首次启动生成账号 `a` 和 `b`，随机密码写在 `data/initial-passwords.txt`。请勿将该文件、`data/secret.key`、数据库或媒体目录提交到代码仓库。

构建镜像时如果默认 Debian 软件源不稳定，可用 `docker compose build --build-arg DEBIAN_MIRROR=https://<镜像站>`（该镜像站需提供 `/debian` 和 `/debian-security`）。构建完成后执行 `docker compose up -d --no-build`。

## 升级与回退

1. 保留当前 Compose 配置和镜像，做一次数据库一致性备份，并保留照片及签名文件。运行中的 SQLite 使用 SQLite backup API 或 `VACUUM INTO`，不要只复制主库文件。
2. 取得新源码后执行 `docker compose up -d --build`。应用代码和 Web 资源在镜像内，部署只挂载 `data/`。若现有部署仍有 `/app` 下的旧逐文件挂载，迁移时把它们从 Compose 中移除，避免旧源码遮住新镜像。
3. 验证 `curl -fsS http://127.0.0.1:8795/api/health`、外部 HTTPS、登录页及静态资源。需要回退时，先恢复旧应用镜像和原配置；不要用旧数据库覆盖新版启动后的业务数据。

完整备份包含业务数据库、签名文件和媒体，应放在私有存储中。异地备份只有在脱敏成功后才应上传；仓库里的 [`remote_backup.py`](../remote_backup.py) 是现有自动化的实现，不是容器启动的依赖。
