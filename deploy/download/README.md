# 内测下载页（卡 E1 · 2026-09-21）

一张纯静态落地页：内测用户点链接或扫码即可下载 APK，并且知道**怎么装、出问题怎么反馈**。

## 为什么放在 `deploy/download/` 而不是 `client/static/`

原计划写在 `client/static/dist/`，实际改落此处（已在 `docs/决策台账.md` §1.14 登记为偏差-1）：

1. `client/static/**` 会被**整体打进 APK**（当前基座 63.1MB）——下载页是给**还没装 App 的人**看的，
   打进包里毫无意义，只是继续增大体积；
2. 这页必须**公网独立访问**，不能随包发布；
3. 它和部署工程（`deploy/`）同生命周期：都挂在同一台服务器上。

## 文件

```
deploy/download/
├── index.html                      # 页面（零 JavaScript）
├── style.css                       # 视觉唯一来源（Tailwind CDN 仅提效，断网也完整）
├── assets/app-icon.svg             # 手写品牌标识（年轮/光晕，品牌金→锈红）
├── qr/download-placeholder.svg     # 下载二维码占位（同名覆盖即生效）
├── qr/feedback-placeholder.svg     # 反馈群二维码占位（同名覆盖即生效）
└── apk/                            # APK 投放目录（C3 交接点，见该目录 README）
```

## 本地预览

```bash
cd deploy/download && python -m http.server 8080
# 浏览器打开 http://127.0.0.1:8080/
# 手机同网段可访问 http://<本机局域网IP>:8080/
```

## `[替换点]` 清单（C3 出包后逐项回填，共 5 处）

| # | 位置 | 现状 | 出包后 |
|---|---|---|---|
| 1 | 主按钮 `href` | `apk/yishu-beta-latest.apk` | 把 APK 按**固定文件名**放进 `apk/` 即自动生效（无需改 HTML） |
| 2 | 「约 — MB」 | 占位 | 回填真实文件大小 |
| 3 | 「更新于 —」 | 占位 | 回填出包日期 |
| 4 | `qr/download-placeholder.svg` | 占位件 | **同名覆盖**为真实二维码图片（改后缀需同步改 HTML） |
| 5 | `qr/feedback-placeholder.svg` | 占位件 | 同上（内测反馈群建好后） |

页脚还有两处**待备案完成后**填写：备案号、隐私政策 / 用户协议链接（当前是 `#` 占位）。

## 两处刻意的实现取舍（与原任务卡的差异，均已登记）

| 项 | 原卡要求 | 实际做法 | 理由 |
|---|---|---|---|
| `<img>` 的 `onerror` 兜底 | 每张图都带 | **不带**，改为放"永远能加载的占位 SVG" | `onerror` 本身就是 JavaScript 属性，与同一条要求里的「零 JavaScript」直接冲突。用占位文件同时满足两者：既不引 JS，也不会出现浏览器裂图图标。**本页严格零 JS**（无 `<script>`、无 `on*=` 属性） |
| 滚动渐显 | 要求有 | 用 CSS `animation-timeline: view()`，并**以"默认可见"为初始态** | 无 JS 实现滚动渐显只有这一条路。关键点是**不能用 `opacity:0` 当初始态**——那样在不支持该特性的浏览器上会整页看不见。已用 `@supports` + `prefers-reduced-motion` 双重门控 |
| 「一键复制邮箱」 | 要求有 | 改为 `mailto:` 链接 + 可长按选中的邮箱文本 | 复制到剪贴板必须用 JS（`navigator.clipboard`）；在"零 JS"约束下退化为 mailto，功能不减 |
| Tailwind CDN | 页面 CSS 用它 | 保留 `@import`，但版式/配色**全部由手写 CSS 承载** | 内测用户网络环境不可控；CDN 挂了页面不能垮。Tailwind 只作为将来加元素的提效手段 |

## 设计规格（取自计划书，改样式请回到这里核对，不要凭感觉调）

- 色板：底 `#F8F7F4` / 淡卡 `#F6EFE3` / 白 `#FFFFFF`；品牌金 `#C4913C` / 锈红 `#B05A3A` / 深金 `#8A6A3A`；主文字 `#3A2E25` / 次文字 `#6B5B4B`
- 字体：Sarasa Gothic SC（回退系统无衬线）；H1 40px·700、H2 20px·600、正文 16px·400
- 最大宽 960px 居中；桌面两列 / 移动单列；**手机端首屏必须看得到主按钮**（≤720px 时品牌区压缩、图标缩到标题右侧同行）
- 微动效只在 `:hover`；动效尊重 `prefers-reduced-motion`

## 部署

由主窗用 Cloud Studio 集成部署本目录（静态服务）后拿到公网地址。**部署前请先确认 5 处替换点已回填**，
否则用户看到的是「约 — MB」和占位二维码。
