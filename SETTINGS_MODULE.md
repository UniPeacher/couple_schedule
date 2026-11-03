# 设置页整理

## 信息结构

- 外观：本机浅色／深色，立即生效。
- 两人资料：双方昵称，共享修改，独立保存。
- 课表与空闲：各自校历、统计窗口和共同空闲阈值，共享修改。
- 通知与提醒：本机通知检测／测试、当前账号的微信 Token、周期提醒入口。
- 功能管理：周期关注、纪念日、心愿导航。
- 账号安全：当前账号密码、重复确认，独立提交，不混入其他设置。
- 高级配置：管理员 AI 服务；非管理员只显示说明。Android 服务地址有能力时保留。
- 页面底部退出当前账号，二次确认。

默认只展开外观与功能入口，其他按需展开。每个写入分组独立保存，标注影响范围，具有未保存／保存中／成功／错误反馈；页面内跳转和刷新对未保存修改进行提示。原功能不删减。

## API 与保存安全

- `GET /api/settings`：每次进入获取最新数据，认证并 no-store；只返回 Token／Key 是否已配置，不返回值。
- `POST /api/settings` 新 `scope`：profile、schedule、notifications、ai。每个分组有独立字段白名单与验证，事务提交，不覆盖其他组。
- 通知 Token 只能修改当前账号；留空保留，清除需明确勾选。
- AI 仅账号 a 可保存；密钥留空保留，清除需明确勾选，只有保存 AI 组才清除简报缓存。
- 修改密码继续独立调用原 `/api/password`，新增前端二次输入验证。
- 课表校历验证真实日期及周一，时间窗口验证小时／分钟与先后关系，空闲阈值 5～180 整数。
- 保存后回读设置并刷新课表内存，清理旧周/月缓存，避免修改成功但继续展示旧设置。
- 课表、月历和首屏 SSR 不再下发推送 Token 或 AI Key。前端清理旧版周/月本地缓存的相关敏感字段。
- 保留原不带 scope 的接口以兼容已经打开的旧版客户端；新页面仅使用分组接口。

## 文件

- settings_ui.js / settings_ui.css：模块化设置页面。
- settings_service.py：分组字段校验、安全读取、精准更新。
- template_page.html：新渲染入口。
- app.py：新设置读取、分组写入分派和静态资源；课表/月历不回传凭证。
- tests/test_settings.py：后端分组与权限测试。
- tests/settings_browser.cjs：隔离数据的手机浏览器验收，修改测试密码，严禁指向正式服务。
- tests/settings_production_smoke.cjs：正式服务双账号只读检查，不保存配置。

```bash
python3 -m unittest discover -s tests -v
node --check settings_ui.js
python3 tests/browser_server.py
# 另一终端，准备 Playwright 后：
NODE_PATH=/path/to/node_modules node tests/cycle_browser.cjs
NODE_PATH=/path/to/node_modules node tests/settings_browser.cjs
```

部署：`docker compose up -d --build`。不涉及数据库迁移，不重置已有配置。上线前保留数据库一致性快照和旧镜像。Android 通知权限／后台行为未做真机验收，页面仅检测桥接能力，不宣称权限已开启。
