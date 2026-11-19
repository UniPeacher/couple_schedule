# 设置页

| 分组 | 可修改内容 |
| --- | --- |
| 外观 | 本机浅色、深色或跟随系统 |
| 通知 | 当前账号的微信推送配置；Android 中可打开系统通知与后台设置 |
| 我们的名字 | 双方都可编辑昵称 |
| 课表与空闲 | 两份校历、空闲统计窗口与最短空闲时长 |
| 我们的数据 | 下载业务数据和当前账号自己的周期记录 |
| 登录密码 | 为当前账号改密 |
| AI 简报服务 | 账号 `a` 配置简报模型与服务；Android 中也可修改客户端服务地址 |

写入分组独立保存。`GET /api/settings` 不返回 Token 和密钥，只说明是否已配置；保存时留空保留，清除需明确勾选。`GET /api/export` 不导出密码哈希、推送 Token 或 AI 密钥。

前端见 [`web/settings_ui.js`](../web/settings_ui.js)，字段验证与保存见 [`settings_service.py`](../settings_service.py)，回归覆盖见 [`tests/test_settings.py`](../tests/test_settings.py)。
