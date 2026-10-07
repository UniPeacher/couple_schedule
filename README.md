# 线条小狗 · 两人日程共享小窝 (Couple Schedule) 🐾

可爱治愈的线条小狗（Line Puppies）主题双人日程共享 Web 服务。

专为情侣/密友设计：基于 Python 标准库与轻量 SQLite 实现，双方日程互相可见，自动计算时间重叠与「俩汪贴贴」共同空闲时段！

---

## ✨ 特色功能

- 🐶 **线条小狗主题专属 UI**：
  - 两位主角：暖暖小金毛（Golden Puppy）与软软小白狗（Maltese）专属治愈配色与萌宠徽章
  - 治愈手账底纹质感，圆润 Q 弹微动效与按钮交互
  - 空白放假日：线条小狗呼呼大睡插画与漂浮 `z Z Z 💤` 气泡
  - 底部踏步散步小分队齐步走（Puppy Trot Parade）
- 📅 **智能日程与空闲计算**：
  - 支持**每周重复**（支持自定义周次如 `1-16`、`2-17`、`2-16双`）与**单次特别日程**
  - **俩汪贴贴时间**：自动计算并高亮显示双方全天的共同空闲时段
  - **时间重叠检测**：自动标记双方日程重叠冲突时段
- 💬 **情侣留言板与一键快捷短语**：
  - 每个日程卡片均配有情侣专属留言板，双角色气泡对话流
  - 内置情侣常用高频贴纸短语（`🐾 收到汪！`、`🥰 贴贴想你啦`、`🍜 等你一起吃饭！` 等）一键快速发送
- 📱 **移动端流畅适配**：
  - 时间刻度轴支持 Sticky 粘性定位，手机横滑课表时时间轴始终固定可见
  - 悬浮添加按钮（FAB）与卡片弹窗全面自适应小屏幕
- 🔒 **极简轻量，隐私可控**：
  - 仅用 Python 标准库（`http.server` + `sqlite3`），零第三方外部依赖
  - PBKDF2 安全密码哈希与防爆破限流保护
  - 数据完全自托管在本地 SQLite 数据库中

---

## 🚀 快速部署

### 方式一：Docker Compose（推荐）

1. 克隆本仓库：
   ```bash
   git clone https://github.com/UniPeacher/couple_schedule.git
   cd couple_schedule
   ```

2. 启动服务：
   ```bash
   docker compose up -d
   ```

3. 首次启动会自动在 `./data/` 目录下生成 `initial-passwords.txt` 记录两位用户的初始随机密码。
4. 浏览器访问 `http://<服务器IP>:8795` 即可使用。登录后可在右上方「设置」中随时修改名称、校历起始周及密码。

### 方式二：直接 Python 运行

```bash
export PORT=8795
export DATA_DIR=./data
python3 app.py
```

---

## 📱 Android 客户端与云端打包 (APK)

本项目包含专属的 Android 原生客户端代码（位于 `android/` 目录），已配置好 GitHub Actions 自动化编译工作流。

### 客户端亮点：
- 🐾 **线条小狗专属应用图标**（基于高清贴贴小狗绘制，多分辨率适配）
- 🥛 **沉浸式手账状态栏**（奶白与草莓粉主题状态栏沉浸）
- 🔄 **手势下拉刷新**（支持下拉实时同步课表与留言）
- 💾 **自动持久化登录状态**（基于 CookieManager，退出应用后再次打开无需重复输入密码）
- ⚙️ **灵活切换服务器**（默认连接部署地址，长按界面任意空白处可随时修改后端 URL）

### 获取 APK 安装包：
1. **GitHub 云端自动编译**：
   - 每次推送到 `main` 分支时，GitHub Actions 会自动编译生成最新 APK。
   - 访问仓库的 **[Actions 页面](../../actions)**，点击最新一次工作流运行，在 **Artifacts** 区域即可直接下载 `couple-schedule-apk`。
2. **手动一键触发编译**：
   - 在 GitHub 仓库导航到 **Actions** -> **Build Android APK** -> 点击 **Run workflow** 即可在云端一键编译。
3. **Release 发版**：
   - 只要给仓库打上标签（如 `git tag v1.0.0 && git push origin v1.0.0`），GitHub Actions 会自动创建 Release 并直接附带 `couple-schedule-v1.0.0.apk` 下载链接。

---

## 🛠️ 技术栈

- **后端**：Python 3.12（纯标准库，无 pip 依赖）
- **存储**：SQLite 3（WAL 模式）
- **前端**：原生现代 HTML5 / CSS3 / Vanilla ES6（零打包，极速加载）
- **容器化**：Docker & Docker Compose

---

## 📄 开源许可

本项目遵循 [MIT License](LICENSE)。
