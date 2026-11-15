# 设置页整理

## 信息结构

紧凑标题行（无 hero 卡片、无账号徽章行——顶栏已有身份显示）。除外观外全部折叠，
每组标题下方直接显示当前值摘要，不用点开就知道现状：

- 外观（默认展开）：本机浅色／深色，立即生效。
- 通知：本机通知测试／Android 后台设置、当前账号的微信推送 Token（含获取方式说明）。
- 我们的名字：双方昵称，共享修改；摘要显示「A & B」。
- 课表与空闲：各自校历、统计窗口和共同空闲阈值，共享修改；摘要显示校历与时段。
- 我们的数据：一键导出全部业务数据（日程、留言、纪念日、手账、心愿、旅行清单、我的周期记录），
  JSON 附件下载；不含密码哈希、推送 Token、AI 密钥。
- 登录密码：当前账号改密，重复确认，独立提交。
- AI 简报服务：管理员配置；非管理员只显示说明。Android 服务地址有能力时保留。
- 页面底部「关于」说明 + 退出当前账号，二次确认。

已移除的入口：原「功能管理」组（周期／纪念日／心愿的页面跳转——底部导航与首页已可达，
不是设置）、通知组内的周期模块跳转链接。

每个写入分组独立保存，具有未保存／保存中／成功／错误反馈；页面内跳转和刷新对未保存修改进行提示。
原功能不删减，只收敛入口。

## API 与保存安全

- `GET /api/settings`：每次进入获取最新数据，认证并 no-store；只返回 Token／Key 是否已配置，不返回值。
- `POST /api/settings` 新 `scope`：profile、schedule、notifications、ai。每个分组有独立字段白名单与验证，事务提交，不覆盖其他组。
- 通知 Token 只能修改当前账号；留空保留，清除需明确勾选。
- AI 仅账号 a 可保存；密钥留空保留，清除需明确勾选，只有保存 AI 组才清除简报缓存。
- 修改密码继续独立调用原 `/api/password`，新增前端二次输入验证。
- 课表校历验证真实日期及周一，时间窗口验证小时／分钟与先后关系，空闲阈值 5～180 整数。
- `GET /api/export`：登录用户全量导出（`build_export`），白名单业务表 + users(uid,name,week1) +
  meta 中非敏感三项 + `cycle2.export_data`（仅当前账号周期数据）；attachment JSON 下载。
- 保存后回读设置并刷新课表内存，清理旧周/月缓存，避免修改成功但继续展示旧设置。
- 课表、月历和首屏 SSR 不再下发推送 Token 或 AI Key。前端清理旧版周/月本地缓存的相关敏感字段。
- 保留原不带 scope 的接口以兼容已经打开的旧版客户端；新页面仅使用分组接口。

## 文件

- settings_ui.js / settings_ui.css：模块化设置页面。
- settings_service.py：分组字段校验、安全读取、精准更新。
- template_page.html：新渲染入口（引用时 bump `?v=`）。
- app.py：新设置读取、分组写入分派、`/api/export` 导出与静态资源；课表/月历不回传凭证。
- tests/test_settings.py：后端分组、权限与导出脱敏测试。
- tests/settings_browser.cjs：隔离数据的手机浏览器验收，修改测试密码，严禁指向正式服务。
- tests/settings_production_smoke.cjs：正式服务双账号只读检查，不保存配置。

```bash
python3 -m unittest discover -s tests -v
node --check settings_ui.js
python3 tests/browser_server.py   # TMPDIR=/tmp
NODE_PATH=/home/uni/uitest/node_modules node tests/settings_browser.cjs
```

部署：`docker compose up -d --build`。不涉及数据库迁移，不重置已有配置。上线前保留数据库一致性快照和旧镜像。Android 通知权限／后台行为未做真机验收，页面仅检测桥接能力，不宣称权限已开启。
