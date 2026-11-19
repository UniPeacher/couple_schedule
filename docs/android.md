# Android 客户端

Android 应用通过 WebView 打开日程服务，保留登录状态，并提供下拉刷新和系统通知桥接。

- **下载**：从 [Releases](https://github.com/UniPeacher/couple_schedule/releases) 选择已发布版本。成功的 [Build Android APK](https://github.com/UniPeacher/couple_schedule/actions/workflows/build-apk.yml) 运行也会提供 `couple-schedule-apk`，构建产物保留 30 天。
- **服务地址**：页面加载后进入「设置 → AI 简报服务 → 修改客户端服务地址」；页面打不开时，可在原生加载失败界面直接修改。自托管请填自己的 HTTPS 地址。
- **云端构建**：工作流使用 JDK 17 和 Gradle 8.5。仓库维护者可手动触发构建；只有 `v*` 标签构建才创建 Release。

## 签名限制

当前构建使用 Gradle 的调试签名。不同构建环境生成的 APK 签名可能不同，不能保证覆盖安装已经发布的版本。安装前先核对签名；不要为了安装构建产物而卸载现有应用。

源码位于 [`android/`](../android/)，构建配置见 [`build-apk.yml`](../.github/workflows/build-apk.yml)。
