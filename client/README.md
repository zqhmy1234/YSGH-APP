# 忆述光华 · 客户端（uni-app x）

第一波（W3-4）：F1 数据链路（相册→上传→云侧聚合→回读）+ F8 时间轴（L1 日卡片 + L2 主题，只读）。

## 环境要求

- HBuilderX 5.15+（已装 D:\HBuilderX，用 HBuilderX 打开本目录）
- JDK17 / Android SDK / adb（已就绪）
- 真机 nova 11（USB 调试需在手机上**允许授权**：连接后弹出授权框点允许；当前 adb 状态 `unauthorized` 时无法部署）

## 目录结构

```
client/
├── manifest.json                 # App 配置（权限：READ_MEDIA_IMAGES 等）
├── pages.json                    # 页面注册（单页：时间轴）
├── main.uts / App.uvue           # 入口（启动时静默登录预热 token）
├── pages/index/index.uvue        # F8 时间轴（L1 日卡片 + L2 分组 + 空状态）
├── static/empty-photo.svg        # 空状态插画（空白相纸，视觉规范 v1）
├── utils/
│   ├── config.ts                 # baseURL 开关（模拟器 10.0.2.2 / 真机局域网 IP）
│   ├── auth.ts                   # mock wechat login + token 加密存储 + refresh
│   ├── api.ts                    # 统一请求（超时/错误码/401 自动刷新）
│   ├── uploader.ts               # 批量上传（并发≤3、重试 2、进度回调）
│   └── timeline.ts               # timeline 拉取 + ISO 解析 + 日期分组
└── uni_modules/yishu-photo-watch/# UTS 插件（系统能力层）
    └── utssdk/
        ├── interface.uts         # 对外接口（PhotoWatch / 安全存储）
        └── app-android/          # Hybrid Mode：Kotlin 原生
            ├── index.uts         # UTS→Kotlin 桥接导出
            ├── PhotoObserver.kt  # ContentObserver + 游标去重 + 静默窗口攒批
            └── SecurePrefs.kt    # EncryptedSharedPreferences（token 加密落盘）
```

## 真机联调配置（B-CL-2）

`utils/config.ts` 中：

1. 模拟器：保持 `REAL_DEVICE_HOST = ''`（默认走 `10.0.2.2:8000`）
2. 真机：填后端所在电脑的局域网 IP，如 `REAL_DEVICE_HOST = '192.168.1.100'`
3. 后端需允许局域网访问（uvicorn 用 `--host 0.0.0.0`），且与真机同网段

## HBuilderX 步骤（峰宝）

1. HBuilderX → 文件 → 打开目录 → 选 `D:\GuangH-App\client`
2. 运行 → 运行到手机或模拟器 → 制作自定义基座（首次，权限一次配全）
3. 真机部署 nova 11；logcat 观察 `[yishu]` 前缀日志
4. 授权相册 → 触发监听 → 上传 → 时间轴刷新

## 第一波验收（对照 06_微观任务拆解 B-模块）

| 任务 | 验证 | 状态 |
|---|---|---|
| B-BE-1/2/3 后端 multipart 端点 | pytest 9 项新增 + curl 冒烟（50 张全链路） | ✅ 服务端已验证 |
| B-CL-1 工程骨架 | HBuilderX 打开可编译 | 待峰宝 |
| B-CL-2 环境配置 | 配置切换生效 | 代码就绪 |
| B-CL-3 认证封装 | 调通受保护 API | 代码就绪 |
| B-CL-4 网络层 | 错误 toast 正确 | 代码就绪 |
| B-UT-1..4 UTS 插件 | HBuilderX 编译 + 真机 logcat | 代码就绪 / 编译待峰宝 |
| B-UT-5 自定义基座 | nova 11 部署 | 待峰宝（需授权） |
| B-UP-1/2 上传链路 | 50 张上传 + 自动刷新 | 代码就绪 |
| B-F8-1..4 时间轴 UI | 真机截图 + 自审 ≥75 分 | 代码就绪 |
| B-VA-1 测试照片 | `python scripts/generate_test_photos.py --push` | 生成 ✅ / push 待设备 |

## 已知边界（第一波裁剪，已记录 progress.md）

- 端侧不落 XView；游标去重用 SharedPreferences（轻量）
- 服务端 timeline 无游标分页（契约 list），客户端分组渲染；服务端分页列第二波
- L2 语义归并待真实数据（P2-07 后端已标注 cloud-proto 候选）
- 图片感知哈希（perceptual_hash）客户端计算列第二波，第一波去重依赖后端
