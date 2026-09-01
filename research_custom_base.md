# uni-app x 自定义调试基座 + SVG 渲染方案调研报告

> 调研时间：2026-08-30 ｜ 项目：`D:\GuangH-App\.wt\wrap1-agentA2-ui-restore\client`
> 环境：uni-app x（.uvue，Vapor 蒸汽模式，manifest `"vapor": true`），HBuilderX 5.24（5.24.2026081301），Android 真机（华为 nova 11）

---

## 〇、背景与本地核查结论（先说重点）

1. **标准基座（io.dcloud.uniappx）不含任何第三方原生插件**。`hens-svg` 是"UTS 原生组件插件"（uts-vue-component，含 Kotlin/Swift 混编代码），其原生视图工厂必须编译进基座 App 才能注册，所以标准基座运行时报 `Cannot read properties of undefined (reading 'HensSvgView')`、编译提示 `uts插件[hens-svg]不存在，请重新打包自定义基座` —— 这是**预期行为，不是 bug**，唯一解法就是自定义调试基座（或换掉该插件）。
2. **hens-svg 是插件市场付费加密插件**（插件 ID 24922，普通授权 1 元 / 源码授权 9.9 元，作者 hens）。本地 `uni_modules/hens-svg/` 内带 `encrypt` 文件，**加密插件只能云打包，无法本地离线打包**。且云打包必须用"购买/授权该插件的 DCloud 账号"登录的 HBuilderX 提交，否则插件校验失败。
3. 本地核查：项目共 **7 个文件、41 处** `<hens-svg>` 使用（TabBar.uvue 5、detail 6、index 1、interview 2、messages 4、profile 15、record 8），static 下有 5 个 .svg 文件；TabBar 使用了**动态 `:color` 着色**（选中/未选中两色切换）——这一点对替代方案选型影响很大（见问题 5）。
4. `yishu-photo-watch`、`yishu-recorder` 为纯 UTS API 插件（无 .kt 原生视图文件），对自定义基座的依赖主要来自 hens-svg。
5. **风险提示**：manifest.json 中 appid 为 `__UNI__YISHU001`（疑似手工编写的非标准 appid）。云打包要求 appid 已在当前登录的 DCloud 账号下注册（dev.dcloud.net.cn 可查），否则可能直接打包失败。建议先在开发者中心核实该 appid 归属。
6. **风险提示**：hens-svg 在插件市场分类为"uni-app x 标准模式组件"，其页面标注兼容"uni-app x(4.76)"，**未声明支持 2026 年新推出的蒸汽模式**（Vapor，Android 5.21+ 才有）。本项目是蒸汽模式 + HBuilderX 5.24，存在"基座打出来了但插件在蒸汽模式下不工作"的风险。建议先按问题 6 的建议用最小 demo 验证，或直接咨询插件作者（页面留有 QQ：121116111）。

---

## 问题 1：uni-app x 制作自定义调试基座的正确流程（GUI + CLI）

**结论：正确流程如下；云打包自定义基座需要登录 DCloud 账号；Android 端证书可零成本（公共证书/云证书），但必须有证书这一环节。**

### 概念
- 运行基座分**标准基座**（DCloud 的包名 `io.dcloud.uniappx`、证书、SDK 配置）和**自定义基座**。
- 自定义调试基座可以让以下配置生效：App 名称/图标/包名/证书、**App 模块配置与三方 SDK 配置、uni 原生插件（含 UTS 原生组件插件）**、权限配置等。官方明确列出"uni原生插件"属于必须打包才能生效的配置 —— 这正是 hens-svg 需要它的原因。
- 注意：自定义调试基座生成的安装包**不能用于上架**。

### GUI 路径（推荐，HBuilderX 5.24）
1. HBuilderX 底部/右下角**登录 DCloud 账号**（必须是购买了 hens-svg 授权的账号）。
2. 菜单 **发行(U) → App-Android/iOS-云打包**（快捷键 Ctrl+U），打开"App 打包"界面。
3. 平台选 Android，填写包名（如 `com.yishu.guanghua`）。
4. 证书：调试基座选**公共证书**（或"云证书"，按 appid 自动生成，免费）；正式发版才需要自有证书。
5. 勾选/选择 **"打自定义运行基座"** 选项，提交云打包。
6. 打包成功后基座 apk 自动下载到 **`项目目录/unpackage/debug/android_debug.apk`**（一个项目只保留最后一次结果）。
7. 手机连 USB 开 USB 调试 → 菜单 **运行 → 运行到手机或模拟器** → 在设备选择窗口选择 **"自定义基座 - 本地基座"** → 运行。此后可正常热刷、看控制台日志。
   - （HBuilderX 较新版本也可在 运行→手机或模拟器→"制作自定义调试基座" 入口发起；云打包状态可通过菜单 **发行 → 查看云打包状态** 查看。）

### CLI 方式
见问题 3 的完整命令。运行阶段也可用官方 npm 包装器：`uni-launch app-android --playground custom`（需 HBuilderX 5.0+）。

### 登录与证书
- **必须登录 DCloud 账号**：云打包任务、云证书、付费插件授权、打包计费都绑定账号；开发者中心（dev.dcloud.net.cn）按 appid 管理证书与消费记录。
- **证书**：Android 云打包必须指定证书类型，但调试基座用"公共证书"即可，**无需自己制作、无费用**；自有证书可用 JDK keytool 免费生成（官方有指南）。

**依据：**
- 云打包/制作自定义调试基座（uni-app x 官方）：https://doc.dcloud.net.cn/uni-app-x/tutorial/app-package.html#制作自定义调试基座
- 使用自定义基座运行（GUI 选择"自定义基座-本地基座"、产物路径 unpackage/debug）：https://uniapp.dcloud.net.cn/tutorial/run/run-app.html#customplayground
- 标准基座信息（io.dcloud.uniappx）：https://doc.dcloud.net.cn/uni-app-x/tutorial/app-playground.html
- 云证书/公共证书说明：https://ask.dcloud.net.cn/article/35777 （Android 签名证书生成指南）、云打包文档"证书类型"一节（同上 app-package 链接）
- 官方 CLI 包装器（--playground custom）：https://github.com/dcloudio/hbuilderx-cli

---

## 问题 2：云打包"打包终止"常见原因 + 如何查状态/日志

**结论：官方没有"打包终止"这个标准术语的专门文档；它是 HBuilderX 控制台在打包任务异常中断时的通用提示。官方文档明确：'打包过程中如有错误会给出相应错误信息并中断操作'。常见原因按概率排序如下。**

### 常见原因（结合官方资料与本项目情况）
1. **账号/授权类**：未登录 DCloud 账号；appid 不属于当前账号（本项目 `__UNI__YISHU001` 需核实）；**付费插件（hens-svg）未购买/未用购买账号提交**；HBuilderX 版本与插件声明版本不符。
2. **计费/额度类**：2026-02-05 起云打包计费规则调整（原始体积 60MB 内免费；超限扣"App大小超限"余额，**余额不足会扣成负数，需补足后才能继续打包**）。若曾超限导致余额为负，后续打包会被中止。
3. **编译/插件冲突类**：项目编译错误；原生类重复（hens-svg 1.0.1 更新日志明确写了：曾把 `com.caverock:androidsvg-aar` 改为 `com.caverock:androidsvg`，**避免与其他同样依赖 AndroidSVG 的插件一起引入 jar/aar 两套同名类导致云打包 `checkReleaseDuplicateClasses` 失败**）——如果项目里还引入了其他用到 AndroidSVG 的插件，这是已知翻车点。
4. **参数/配置类**：包名格式不合法、证书信息错误、自定义基座不支持 aab 等。
5. **服务器侧**：高峰期排队（周五傍晚等）；极少数服务端故障。

### 查看状态/日志的途径
| 途径 | 操作 |
| --- | --- |
| HBuilderX 控制台 | 提交打包后控制台实时输出"检查云端打包状态…正在编译打包资源…向云端发送打包请求…"，**失败原因会直接打印在这里**，先回看控制台完整日志 |
| GUI 菜单 | **发行 → 查看云打包状态** |
| CLI 查询状态（HBuilderX 5.11+） | `D:\HBuilderX\cli.exe pack status --project D:\GuangH-App\.wt\wrap1-agentA2-ui-restore\client` |
| CLI 看打包日志（5.11+） | `D:\HBuilderX\cli.exe logcat pack`（另开终端） |
| CLI 取消任务（5.14+） | `D:\HBuilderX\cli.exe pack cancel --project <项目> --platform app-android` |
| 开发者中心 | https://dev.dcloud.net.cn/ → "App云打包增值服务"-"消费记录"，核对是否因扣费/余额为负被拦 |

**依据：**
- CLI pack/pack status/pack cancel/logcat pack 与"出错即中断"说明：https://hx.dcloud.net.cn/cli/pack
- 云打包计费规则调整公告（余额负数需补足）：https://ask.dcloud.net.cn/article/42315
- hens-svg 1.0.1 更新日志（checkReleaseDuplicateClasses 失败案例）：https://ext.dcloud.net.cn/plugin?id=24922
- 云打包流程与状态输出示例：同上 `hx.dcloud.net.cn/cli/pack`（含"发行-查看云打包状态"、高峰期排队说明）

---

## 问题 3：`cli pack` 自定义基座的正确参数写法

**结论：以下写法来自官方文档 + 本机 `D:\HBuilderX\cli.exe pack --help`（5.24.2026081301）双重确认。**

```bat
:: 前置：HBuilderX 必须先启动（cli open），且已登录 DCloud 账号
D:\HBuilderX\cli.exe pack --project D:\GuangH-App\.wt\wrap1-agentA2-ui-restore\client --platform android --iscustom true --android.packagename com.yishu.guanghua --android.androidpacktype 1
```

关键点：
- `--project`：HBuilderX 中**已导入**项目的绝对路径（或导入的目录名）。
- `--platform android`（默认即 android，可省略；多平台逗号分隔）。
- `--iscustom true`：启用自定义基座，**值只能是 true|false**。
- `--android.packagename`：**Android 必填**。
- `--android.androidpacktype`：0=自有证书（需再给 `--android.certalias/certfile/certpassword/storepassword`）、**1=公共证书（调试基座推荐，零成本）**；2=DCloud 老版证书、3=云端证书（注意：2 和 3 **仅适用于 uni-app 项目，uni-app x 不可用**）。
- 也可用配置文件：`cli pack --config ./configure.json`（JSON 内含 `iscustom`、`android.packagename`、`android.androidpacktype` 等字段；`--config` 与命令行参数**不可混用**）。
- 查询与取消：`cli pack status --project <项目>`、`cli pack cancel --project <项目> --platform app-android`。

**依据：**
- 官方 CLI 文档（含"自定义基座"官方示例命令、config JSON 格式）：https://hx.dcloud.net.cn/cli/pack
- 本机验证：`D:\HBuilderX\cli.exe pack --help` 输出（2026-08-30 实测）

---

## 问题 4：免云打包的本地方案（离线 SDK 本地打包调试基座）

**结论：官方存在该方案，但对本项目当前不可行/成本过高，不推荐。**

1. **官方方案存在**：uni-app x Android 原生 SDK（离线 SDK）支持本地制作自定义调试基座：
   - 下载 SDK → 原生工程引入 `debug-server-release.aar` + okhttp/zip4j/leakcanary 依赖 → AndroidManifest 加 `DCLOUD_DEBUG=true` → 用 Android Studio 编译出 apk → 重命名 `android_debug.apk` 放入项目 `unpackage/debug/` → HBuilderX 运行选"自定义基座"。（HBuilderX 4.71+ 还支持"已安装基座"联调模式。）
   - 文档：原生联调/离线生成自定义调试基座：https://doc.dcloud.net.cn/uni-app-x/native/debug/android.html ；原生工程配置：https://doc.dcloud.net.cn/uni-app-x/native/use/android.html ；SDK 下载页：https://doc.dcloud.net.cn/uni-app-x/native/download/android.html
2. **对本项目不可行的三个原因**：
   - **SDK 版本滞后**：官方正式版 SDK 目前为 **5.15.2026070915**，5.21 还是 alpha；而本项目需要 HBuilderX 5.24 + 蒸汽模式。离线打包要求"SDK 版本与 HBuilderX 生成资源版本严格配套"，版本不配套直接失败。
   - **付费加密插件（hens-svg）无法本地打包**：加密插件的解密编译只发生在 DCloud 云端，本地离线工程拿不到可用产物。这是硬约束。
   - **成本高**：需自备 Android Studio + 原生工程 + 每次 SDK/引擎升级都要重新配套，投入产出比远差于云打包。
3. 结论：本地离线方案仅适合作为"未来正式发版/需要完全自主打包"时的路线；**调试基座阶段应走云打包**。

**依据：** 上述三个官方文档链接 + hens-svg 插件含 `encrypt` 文件（本地核查）+ 云打包计费公告（免费额度内云打包不花钱）：https://ask.dcloud.net.cn/article/42315

---

## 问题 5【关键备选】不用原生插件的官方矢量图方案

**结论：uni-app x 官方 image 组件原生支持 SVG（HBuilderX 4.81+，Android/iOS/鸿蒙），@font-face 与 iconfont 也官方支持。可以完全不用 hens-svg。唯一短板：内置方案不能像 hens-svg 那样给 SVG 动态换色，需要换色处用 iconfont 或双色图。**

### 5.1 image 组件支持 SVG
- 官方 image 组件文档明确：App 平台支持 **SVG（Android √；iOS 13+ √，均需 HBuilderX 4.81+；鸿蒙 √）**。
- 限制：**不支持 SVG 动画**；个别场景会转位图渲染（鸿蒙蒸汽模式开启 flatten、或部分 mode 裁剪时；Android 无此说明）。
- 用法：把 svg 放 `static/` 目录，`<image src="/static/icons/search.svg" style="width:22px;height:22px;" />`。注意 image 默认宽高 320×240px，**必须显式给宽高**。
- **不支持给 SVG 换色**（没有 color/tint 属性）——需要动态换色的图标见 5.2。
- 依据：https://doc.dcloud.net.cn/uni-app-x/component/image.html#svg-support （图片格式表 + "关于svg格式的矢量能力"一节）

### 5.2 @font-face / iconfont（官方支持，且是换色图标的正解）
- `@font-face { font-family: MyIcon; src: url('/static/xxx.ttf'); }` 官方支持；Android 支持 ttf/otf（不支持 woff/woff2）；也可用 API `uni.loadFontFace` 编程加载。
- 官方专门有"字体图标 @iconfont"一节：由于 App 平台**不支持伪元素**，跨端写法必须用 **unicode 直显**：`<text style="font-family: MyIcon;">{{'\uE601'}}</text>`。
- HBuilderX 4.33+ 还**内置 `uni-icon` 字体图标**可直接用（约 21 个常用图标：forward/back/share/home/more/close/search/download 等）。
- **iconfont 的优势**：文字即图标，`color`/`font-size` 随便改 —— 完美覆盖 TabBar 选中态换色需求。
- 坑：iconfont.cn 默认 font-family 名为 "iconfont" 易冲突，需在图标项目设置中改成独特名字。
- 依据：https://doc.dcloud.net.cn/uni-app-x/css/common/at-rules.md（对应线上页：.../uni-app-x/css/common/at-rules.html，"字体图标 @iconfont"、"uni-icon"）、https://doc.dcloud.net.cn/uni-app-x/css/font-family.html

### 5.3 结论
- 静态展示类图标/插画（如 empty-photo.svg）：**image + svg 文件**，零成本。
- 需要动态换色的图标（TabBar、按钮状态）：**iconfont（ttf + @font-face + unicode 直显）**。
- 复杂多色/带渐变图标：image + svg（svg 内自带颜色，无需换色即可）。

---

## 问题 6：社区案例

1. **hens-svg 本体**：插件市场 https://ext.dcloud.net.cn/plugin?id=24922 。付费（1 元/9.9 元源码），下载 15 次、购买 2 次，属于小众插件。更新记录只有一条（1.0.1 修复云打包 duplicate classes 失败）。**分类为"uni-app x 标准模式组件"，未声明蒸汽模式兼容性**。
2. **GitHub**：无 hens-svg 相关开源仓库（搜索 0 结果），无公开源码可参考。
3. **DCloud 论坛（ask.dcloud.net.cn）**：
   - 搜索"打包终止"无直接对应帖子（说明该提示非高频问题，需看具体控制台报错）；
   - 与云打包失败强相关的官方公告是《DCloud云打包计费规则调整公告》（2026-02-05 生效，评论区大量打包失败/扣费讨论）：https://ask.dcloud.net.cn/article/42315
   - 官方标准基座/自定义基座文档（含 uni-app x 离线生成自定义调试基座链接）：https://uniapp.dcloud.net.cn/tutorial/run/run-app.html#customplayground
4. **插件市场上的同类替代**（若坚持用原生组件渲染）：
   - "商业级 SVG 图标引擎"（id=28177，支持 uni-app x，原生渲染/主题切换/动画）：https://ext.dcloud.net.cn/plugin?id=28177
   - "原生UTS组件svg插件"（id=18803，uni-app 兼容模式，**不支持蒸汽模式**）：https://ext.dcloud.net.cn/plugin?id=18803
   - 注意这些同样是"需要自定义基座"的原生组件插件，换了插件也只是把"做自定义基座"这一步变得更必要。
5. **官方对"要不要用原生插件渲染 svg"的态度**：image 组件 4.81 起已内置 SVG 支持，官方文档建议"如需其他图片格式，可自行开发 uts 组件插件或搜索插件市场"——即内置 SVG 是首选路径，插件是补充。

---

## 最终推荐

### 方案 A：云打包制作自定义基座（保住 hens-svg 与现有 41 处用法）

**确切步骤：**
1. 【核实】登录 https://dev.dcloud.net.cn/ ，确认 appid `__UNI__YISHU001` 已注册在当前账号下（若未注册，先在 HBuilderX manifest 重新获取/绑定 appid）；确认 hens-svg 已用该账号购买；确认"App大小超限"余额不为负。
2. 【预检】先新建一个**最小 demo 工程**（uni-app x 蒸汽模式），放入 hens-svg，按第 3-5 步打自定义基座并在 nova 11 上运行，验证"hens-svg 在蒸汽模式 + 5.24 下真的能渲染"。若失败，直接转方案 B（或联系插件作者询问蒸汽模式兼容）。
3. HBuilderX 登录 DCloud 账号 → 打开项目 → 菜单 **发行 → App-Android/iOS-云打包** → 平台 Android → 填包名（如 `com.yishu.guanghua`）→ 证书选**公共证书** → 勾选**打自定义运行基座** → 提交。
   （CLI 等价命令：`D:\HBuilderX\cli.exe pack --project D:\GuangH-App\.wt\wrap1-agentA2-ui-restore\client --platform android --iscustom true --android.packagename com.yishu.guanghua --android.androidpacktype 1`；查询：`cli pack status --project <项目>`；日志：`cli logcat pack`。）
4. 若再次"打包终止"：看控制台具体报错 → 对照问题 2 的五类原因逐项排查（重点：付费插件授权、余额、duplicate classes）。
5. 成功后 apk 落到 `unpackage/debug/android_debug.apk` → 运行 → 设备选择窗口选 **自定义基座-本地基座** → 真机运行，热刷调试。
6. 正式发版时**不能**用调试基座，需重新走普通云打包（届时用自有证书）。

- 成本：0 元（60MB 内免费、公共证书免费），顺利的话 30 分钟内完成；风险点是蒸汽模式兼容性（需第 2 步预检兜底）。

### 方案 B：去掉 hens-svg，改用官方内置能力（推荐作为长期方案）

**做法：**
1. 封装一个轻量图标组件（如 `SvgIcon.uvue`）：
   - 静态 svg → `<image src="/static/icons/xx.svg">`（用于不需要换色的图标/插画）；
   - 需换色图标 → 从 iconfont.cn 生成 ttf（font-family 改成独特名字）放 static，`@font-face` 注册，组件内 `<text :style="{fontFamily:'YishuIcon', color, fontSize}">{{unicode}}</text>` 渲染；
   - 或优先使用官方内置 `uni-icon`（HBuilderX 4.33+，约 21 个常用图标）减少自备字体。
2. 替换 7 个文件共 41 处 `<hens-svg>`（TabBar 5 处换色 → iconfont；其余按是否换色分流到 image 或 iconfont）。
3. 移除 `uni_modules/hens-svg`（顺带摆脱付费插件授权、加密插件、云打包依赖、自定义基座依赖）。
4. 之后即可继续用**标准基座**热刷调试，无需自定义基座；未来如引入其他原生插件再做基座不迟。

**工作量评估：**
- 图标资产准备（iconfont 项目整理/生成、5 个现有 svg 归类）：约 1-2 小时；
- 封装组件 + 替换 41 处用法 + 走查：约 2-4 小时；
- 真机回归（7 个页面）：约 1 小时。
- **合计约 0.5-1 人日**，一次性投入，之后不再受插件/基座掣肘。

### 建议
- **短期**（今天就要跑起来看 UI）：先做方案 A 第 2 步的最小验证；若 hens-svg 在蒸汽模式下确实可用，走方案 A 把基座打出来继续开发。
- **中期**（本轮 UI 恢复完成后）：实施方案 B 替换掉 hens-svg。理由：① 官方内置 SVG/iconfont 支持已完备；② 项目只剩 Android 单端 + 静态图标需求，原生插件收益极低；③ 摆脱付费加密插件后，打包/基座/升级链路全部简化；④ 方案 B 的 iconfont 能力完全覆盖 TabBar 动态换色，而内置 image+svg 做不到，hens-svg 提供的差异化价值（换色）恰好有更便宜的官方替代。

---

## 附：本报告主要引用来源

| # | 来源 | 链接 |
| --- | --- | --- |
| 1 | uni-app x 云打包/制作自定义调试基座（官方） | https://doc.dcloud.net.cn/uni-app-x/tutorial/app-package.html |
| 2 | 真机运行/使用自定义基座运行（官方） | https://uniapp.dcloud.net.cn/tutorial/run/run-app.html#customplayground |
| 3 | uni-app x 标准基座信息（官方） | https://doc.dcloud.net.cn/uni-app-x/tutorial/app-playground.html |
| 4 | HBuilderX CLI pack/pack status/pack cancel/logcat pack（官方） | https://hx.dcloud.net.cn/cli/pack |
| 5 | image 组件 SVG 支持（官方） | https://doc.dcloud.net.cn/uni-app-x/component/image.html |
| 6 | @font-face/iconfont/uni-icon（官方） | https://doc.dcloud.net.cn/uni-app-x/css/common/at-rules.html 、 https://doc.dcloud.net.cn/uni-app-x/css/font-family.html |
| 7 | uni-app x Android 离线 SDK/原生联调（官方） | https://doc.dcloud.net.cn/uni-app-x/native/debug/android.html 、 https://doc.dcloud.net.cn/uni-app-x/native/download/android.html |
| 8 | 云打包计费规则调整公告 | https://ask.dcloud.net.cn/article/42315 |
| 9 | hens-svg 插件市场页 | https://ext.dcloud.net.cn/plugin?id=24922 |
| 10 | 同类 SVG 插件（28177/18803） | https://ext.dcloud.net.cn/plugin?id=28177 、 https://ext.dcloud.net.cn/plugin?id=18803 |
| 11 | HBuilderX CLI npm 包装器（--playground custom） | https://github.com/dcloudio/hbuilderx-cli |
| 12 | Android 签名证书生成指南 | https://ask.dcloud.net.cn/article/35777 |
