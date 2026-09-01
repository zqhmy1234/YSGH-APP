# uni-app x Vapor 模式运行时疑难问题调研报告

- 调研时间：2026-08-30 01:13 ~ 02:00（GMT+8）
- 项目：`D:\GuangH-App\.wt\wrap1-agentA2-ui-restore\client`（uni-app x / .uvue / manifest `vapor: true`）
- 环境：HBuilderX 5.24.2026081301（< 5.25），标准基座 `io.dcloud.uniappx`（version_code=524），Android 真机 nova 11（序列号 DKS9K23526028855），后端在本机 8000 端口，设备经 `adb reverse tcp:8000 tcp:8000` 访问 `127.0.0.1:8000`
- 调研方式：代码走查 + 官方文档/更新日志/论坛 + 官方源码仓（gitcode uni-api）+ **设备现场取证**（adb logcat 实时抓取 + 隧道连通性实测）

---

## 摘要（先看这里）

1. **问题 1（uni.request 不回调）不是封装代码问题，也不是 adb 隧道/cleartext/并发问题**。现场取证证明：请求在 **JS→原生派发阶段被运行时吞掉**（同一秒内裸参数请求能通、带完整参数的请求连 complete 都不触发，且隧道被实测为健康）。最可能的系统性根因是：**项目在 HBuilderX 5.25 之前 import 了大量 `.ts` 文件（21 个 utils），违反官方蒸汽模式明文约束**（官方公告：5.25 前不得 import `.ts/.js`，必须改名 `.uts`）。首要修复：把 `utils/*.ts` 全部改名为 `.uts`（内容不动）。
2. **问题 2（loadFontFace 报 `reading 'vm'`）根因明确**：在 `App.uvue onLaunch` 里调用 `uni.loadFontFace` 且**未传 `global: true`**——非 global 路径要求"当前页面"，onLaunch 时机没有页面实例，蒸汽运行时对空上下文解引用 `.vm` 抛 TypeError（官方 uni-api 源码 + 论坛同族案例佐证）。修复：加 `global: true`，并把 `weight` 从顶层挪进 `desc`。
3. 附带发现：`UTSAndroid is not defined`（initSync 被炸断）、`hens-svg is not found`、`uni.request(...).then is not a function`（uni-app x 的 uni.request 返回 RequestTask 而非 Promise，属文档行为）。详见正文。

---

## 问题 1：fetchTimeline 的 uni.request 不回调

### 1.1 现状确认（代码走查）

- `utils/api.ts` `doRequest`：`new Promise` + `uni.request({url, method, data, header, timeout: 15000, success, fail})`，回调式写法，**永不 reject**。写法本身符合官方文档（data 为 UTSJSONObject 字面量、header 为 UTSJSONObject）。
- 调用链：`index.uvue onLoad → ensureLogin().then(reload) → flushOpQueue().then(fetchAll) → fetchTimeline(null) → get('/api/v1/events/timeline') → doRequest`。`flushOpQueue` 无待处理条目时立即 `resolve(0)`，不会卡上游。
- 与裸调用的差异：`doRequest` 比裸 `uni.request` 多传了 **`header`（Content-Type + Authorization）、`timeout: 15000`、`data: {}`（GET 传空对象）**。

### 1.2 现场取证（2026-08-30 01:38 会话，logcat pid=20361）

> 用户已在代码中加入 `[dbg-req]`（fire/complete/ok/FAIL 四点）与 `[dbg-probe]` 探针，本次抓取直接验证了行为。

关键日志时间线：

```
01:38:29.522 [dbg-req] fire GET http://127.0.0.1:8000/api/v1/events/timeline   ← 发出
01:38:29.527 [dbg-req] fire GET http://127.0.0.1:8000/api/v1/echo/today        ← 发出
   （此后 7.5 秒内：两条请求均无 complete / ok / FAIL 任何日志）
01:38:37.025 [dbg-probe] probe1 callback-form fire   ← 裸请求（仅 url+success/fail，/healthz）
01:38:37.083 [dbg-probe] probe1 OK 200                ← 58ms 后成功返回！
01:38:37.069 TypeError: uni.request(...).then is not a function  ← probe2（Promise 形态）
```

补充实测（本调研在电脑侧直接执行）：

- `adb reverse --list` → `UsbFfs tcp:8000 tcp:8000`（规则在）；后端 0.0.0.0:8000 LISTENING（PID 19168）。
- 设备侧 `adb shell curl -m 10 http://127.0.0.1:8000/api/v1/events/timeline` → **HTTP 401，耗时 11ms**（无 token 故 401，但证明隧道畅通、后端可达、响应极快）。

### 1.3 排除项（均有依据）

| 假设 | 结论 | 依据 |
|---|---|---|
| adb reverse 隧道"僵死" | **本次排除**（但作为一般性风险保留，见 1.6） | 请求卡住期间，同隧道裸请求 58ms 成功；调研时设备 curl 11ms 达后端 |
| Android cleartext http 拦截（127.0.0.1 是否豁免） | 排除 | Android 9+ 默认禁明文且**环回地址不豁免**（RN 社区大量 `CLEARTEXT communication to 10.0.2.2 not permitted` 案例），但本案 http 请求多次成功（wechat/probe/curl），说明标准基座已放行明文；且若被拦截会**立刻走 fail**（CLEARTEXT 错误），不会静默挂起 |
| 并发请求数超限排队 | 排除 | uni-app x App-Android **无任何文档化的并发上限**（官方 request 文档无此约束）；"10 并发"仅存在于微信小程序平台（官方文档：`wx.request/wx.uploadFile/wx.downloadFile 的最大并发限制是 10 个`，见 https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html ）。且启动期并发仅 2~4 个请求 |
| utils 封装代码缺陷 | 排除 | 与 develop 逐字节一致且 develop 联调通过；探针证明同封装外的裸调用正常、封装内调用挂起，变量在"运行环境"而非代码文本 |
| 请求发出后后端不响应 | 排除 | 后端从未收到该请求（用户后端日志）+ 隧道实测畅通 → 请求根本没上线 |

### 1.4 根因判断

**直接根因：请求在 5.24 蒸汽模式 Android 运行时的"调用→派发"阶段被丢失**——带完整参数（`header`/`timeout`/`data:{}`）的请求既不发出、也不触发任何回调（连 `complete` 都没有）；同一时刻、同一隧道的裸参数请求正常。这是运行时缺陷，而非网络/隧道/业务代码问题。

**系统性根因（首要嫌疑，有官方明文依据）：项目在 5.25 之前 import `.ts` 文件，属于官方明令不支持的写法。**

官方公告（DCloud_heavensoft，2026-08-28 更新，置顶）原文：

> 注意：在 HBuilderX 5.25 之前，不要在 uvue 里的 script 写 lang="ts" 或 lang="js"……**不要 import 外部后缀为 .ts 或 .js 的文件，把 .ts 或 .js 文件名改名为 .uts，里面还是 ts/js 的内容。** 即，目前的状态是实际可以写 ts/js 代码，但文件名得叫 uts、script lang 不写或 lang="uts"。
> —— https://ask.dcloud.net.cn/article/42377

项目现状恰好踩中：`utils/` 下 **21 个 `.ts` 文件**被 `App.uvue` 与全部页面 import（`./utils/api`、`./utils/auth`、`@/utils/timeline`……）。同一次启动的 logcat 还显示多个"上下文/全局缺失"类运行时异常，印证 5.24 蒸汽运行时在该写法下桥接不稳定：

- `ReferenceError: UTSAndroid is not defined`（initSync → registerBackgroundSync 处炸断，onLaunch 后半段未执行）
- `[ERROR]uni_modules/hens-svg is not found` → `TypeError: Cannot read properties of undefined (reading 'HensSvgView')`
- `uni.request(...).then is not a function`（注：这条**符合文档**——uni-app x 的 `uni.request` 返回值是 `RequestTask` 不是 Promise，见官方 API 文档"返回值"一节；Promise 形态探针本身无效，回调式封装是对的）

参数级触发因子（次级定位，需在 P0 修复后仍复现时做隔离实验确认）：`timeout`、`header`、`data:{}` 三者之一或组合。历史旁证：早期版本曾有 "request timeout 参数传 null 直接超时" 的 Web 端 bug（官方更新日志），iOS26 曾有"带 Authorization 头请求一直 loading"的论坛案例（https://ask.dcloud.net.cn 搜索 "Authorization 一直 loading"），说明 timeout/header 参数路径是 request 缺陷高发区。

### 1.5 推荐修复方案

**P0（立即，最可能一击命中）：把 `utils/` 下 21 个 `.ts` 改名为 `.uts`（内容不改）。**

- 官方公告要求的 5.25 前唯一合规写法；import 路径不带后缀（`'./utils/api'`），改名后无需改任何 import 语句。
- 注意同目录已有 `agg_runner.uts`，混用状态本身就是风险面。
- 改名后冷启动重跑，观察 `[dbg-req] ok /api/v1/events/timeline` 是否出现。

**P1（若 P0 后仍复现）：参数隔离探针，二分定位触发参数。** 在 App.uvue setTimeout 探针处依次单变量测试（每次只加一个参数）：

```js
// A: 基线（已验证可通）
uni.request({ url: BASE + '/healthz', success: r => log('A ' + r.statusCode), fail: e => log('A fail') })
// B: 只加 timeout
uni.request({ url: BASE + '/healthz', timeout: 15000, success: ..., fail: ... })
// C: 只加 header
uni.request({ url: BASE + '/healthz', header: { 'Authorization': 'Bearer xxx' }, success: ..., fail: ... })
// D: 只加 data（GET 传空对象）
uni.request({ url: BASE + '/healthz', data: {}, success: ..., fail: ... })
```

对应规避（哪个触发就绕哪个，均为临时措施）：
- `timeout` 触发 → 移除 `timeout` 参数，改 JS 层超时竞速兜底（见下）；
- `data:{}` 触发 → `doRequest` 中 GET 且 data 为 null 时**不传 data 键**；
- `header` 触发 → dev 期把 token 挪到 query（`?access_token=`，需后端配合）或等官方修复。

**P2（强烈建议，无论根因）：业务层超时兜底**——即使框架再丢回调，业务永不无限挂起：

```ts
function withTimeout<T>(p: Promise<T>, ms: number, tag: string): Promise<T> {
	return new Promise<T>((resolve) => {
		let done = false
		const timer = setTimeout(() => {
			if (!done) { done = true; console.log('[watchdog] timeout ' + tag); resolve(null as T) }
		}, ms)
		p.then((v: T) => { if (!done) { done = true; clearTimeout(timer); resolve(v) } })
	})
}
// 用法：return withTimeout(doRequest(...), REQUEST_TIMEOUT_MS + 5000, path)
```

**P3：升级 HBuilderX 5.25**（截至 2026-08-30 官方正式版更新日志最新版为 5.24.2026081301，5.25 尚未发正式版，关注 https://doc.dcloud.net.cn/uni-app-x/release.html ）。5.25 起官方支持直接 import `.ts/.js`，届时可改回。

**P4：若仍复现，向 DCloud 提交最小复现**（裸工程 + 单请求 + 触发参数）。官方 bug 追踪：`https://issues.dcloud.net.cn/?mid=api.network.request`（需 DCloud 账号登录，本次调研无法访问）。

### 1.6 adb reverse 一般性维护（虽非本次根因）

- 现场实测命令（可复用）：`adb shell curl -sS -m 10 -o /dev/null -w '%{http_code} %{time_total}' http://127.0.0.1:8000/<任意接口>`
- 僵死时的标准处理：`adb reverse --remove-all && adb reverse tcp:8000 tcp:8000`；必要时 `adb kill-server && adb start-server` 后**重新**添加规则（server 重启后设备侧规则失效是 adb reverse 的经典坑）。
- 注意 adb 二进制一致性：本机 adb 为 SDK platform-tools 37.0.1（`C:\Users\ghf\AppData\Local\Android\Sdk\platform-tools\adb.exe`）；若 HBuilderX 使用自带 adb 且版本不一致，会触发 adb server 版本冲突重启，间接击穿 reverse 隧道。建议真机运行期间始终用同一个 adb。

---

## 问题 2：uni.loadFontFace 报 `Cannot read properties of undefined (reading 'vm')`

### 2.1 现状确认

`App.uvue onLaunch` 首行即 `loadCustomFonts()`，对 static/fonts/ 下 3 个 Sarasa Gothic SC ttf（每个约 9MB，已确认存在）调用：

```ts
uni.loadFontFace({ family: f.family, source: 'url(/static/fonts/xxx.ttf)', weight: f.weight, success, fail })
```

两个写法问题：**① 未传 `global: true`；② `weight` 放在顶层**（官方参数表中 weight 属于 `desc` 描述符，顶层无此参数）。

### 2.2 根因判断（依据充分）

**根因：在 App.uvue（应用入口、无当前页面）调用 `uni.loadFontFace` 且未传 `global: true`，运行时走"页面级字体"路径，对不存在的页面上下文解引用而抛 TypeError。**

依据链：

1. **官方 uni-api 源码**（蒸汽模式 API 实现，app-android）：
   `https://gitcode.com/dcloud/uni-api/blob/alpha/uni_modules/uni-loadFontFace/utssdk/app-android/index.uts`
   ```ts
   if (options.global == true) {
       appLoadFontFace(...)            // 全局路径：应用级，不需要页面
   } else {
       const page = getCurrentPage()   // 页面路径：需要"当前页面"
       if (page === null) { res.reject(... 'page is not ready', 99); return }
       page.$fontFamilySet...; page.$nativePage!.loadFontFace(...)
   }
   ```
   非 global 路径强依赖当前页面。App.uvue onLaunch 时机无页面；5.24 蒸汽运行时在该分支对空上下文解引用（`.vm`）直接抛 JS TypeError（V8），而不是干净地走 fail——这正是"Cannot read properties of undefined (reading 'vm')"（vapor 模式页面逻辑由 JS 引擎驱动，官方说明见蒸汽模式文档）。
2. **论坛同族案例**（uni-app，错误形态为 `$page`，机制相同）：【报Bug】uni.loadFontFace 在 App.vue onLaunch 中调用报错——`TypeError: Cannot read property '$page' of undefined`，9 人回复确认；社区有效 workaround：延迟调用 / 移到页面 onLoad / 用 global。https://ask.dcloud.net.cn/question/192482
3. **官方文档口径**：`uni.loadFontFace` 的 `global` 参数说明"是否全局生效……需在 app.uvue 中调用"（在入口做全局字体本就该用 global:true）；官方示例的 source 用 `url('/static/font/uni.ttf')` 包裹。文档：https://doc.dcloud.net.cn/uni-app-x/api/load-font-face.html
4. **官方更新日志佐证该 API 在入口调用历来是缺陷高发区**：鸿蒙平台"修复 API uni.loadFontFace 在 app.uvue 中调用不生效"（issue id=17338）；"修复 uni.loadFontFace 多次请求同一网络字体时可能触发错误回调"。见 https://doc.dcloud.net.cn/uni-app-x/release.html
5. **现场取证的间歇性**：01:38 的一次启动中三次调用竟全部走了 success（`[yishu] font loaded 400/600/700`），说明该路径行为**不稳定/竞态**（页面上下文建立时机随启动速度变化）——这解释了"有时报 vm、有时又好像没事"，也说明必须用确定性写法（global:true）修复，不能赌时序。
6. 5.23-alpha 更新日志另有"Android 蒸汽模式 修复 CSS font-family 在部分设备加载字体可能引起内存泄漏"，蒸汽模式字体加载是已知脆弱区。

另注意：`loadCustomFonts()` 是 onLaunch 第一句，它一抛异常，**后续 initSentry/ensureLogin/initSync 全部不执行**（本次现场观察到 onLaunch 多次被不同异常炸断：字体 vm 错、`UTSAndroid is not defined` 等）。index 页自己补了 ensureLogin 所以登录没断，但这是侥幸不是设计。

### 2.3 推荐修复方案（具体代码）

```ts
/** 加载自定义字体：
 *  - global: true —— App.uvue 入口必须走应用级加载（非 global 需要当前页面，onLaunch 无页面 → 'vm' 报错根因）
 *  - weight 移入 desc（顶层 weight 非官方参数）
 *  - try/catch 包裹：字体失败不得炸断 onLaunch 后续初始化（sentry/登录/同步） */
function loadCustomFonts(): void {
	const fonts = [
		{ family: 'Sarasa Gothic SC', source: "url('/static/fonts/SarasaGothicSC-Regular.ttf')",  weight: '400' },
		{ family: 'Sarasa Gothic SC', source: "url('/static/fonts/SarasaGothicSC-SemiBold.ttf')", weight: '600' },
		{ family: 'Sarasa Gothic SC', source: "url('/static/fonts/SarasaGothicSC-Bold.ttf')",     weight: '700' }
	]
	for (let i = 0; i < fonts.length; i++) {
		const f = fonts[i]
		try {
			uni.loadFontFace({
				global: true,
				family: f.family,
				source: f.source,
				desc: { weight: f.weight },
				success: () => { console.log('[yishu] font loaded: ' + f.family + ' ' + f.weight) },
				fail: (err: any) => { console.log('[yishu] font load failed: ' + f.family + ' ' + f.weight + ' -> ' + JSON.stringify(err)) }
			})
		} catch (e: any) {
			console.log('[yishu] loadFontFace threw: ' + f.family + ' ' + f.weight + ' -> ' + e)
		}
	}
}
```

同时建议：**onLaunch 内每个初始化步骤独立 try/catch**（至少把 `loadCustomFonts()` 包起来），避免任一步骤的运行时异常炸断整条启动链（本次取证已两次观察到 onLaunch 被异常腰斩）。

附加建议（性能）：3 个 ttf 合计约 27MB，启动即全量加载；可考虑只加载 Regular+Bold 两档、或用字体子集化工具裁剪到项目实际用字（官方亦提示蒸汽模式字体加载存在内存泄漏修复史）。`@font-face` 在 ucss 的支持仍以 `uni.loadFontFace` + `font-family` 引用为准，无需改动样式侧。

---

## 附带发现（同一次取证，建议一并跟进）

1. **`ReferenceError: UTSAndroid is not defined`**（app-service.js，initSync → registerBackgroundSync → setBackgroundTaskHandler 处）：`uni_modules/yishu-background-tasks` 插件的 JS 侧引用 `UTSAndroid`，在蒸汽模式 JS 上下文中不存在。后果：`initSync` 在启动时被炸断（periodic timer 已起，但后台任务注册未完成）。与问题 1 同源（5.24 蒸汽运行时 + .ts/插件桥接不稳），P0 改名后需复测；若仍在，向插件侧要蒸汽兼容版本或条件编译规避。
2. **`uni_modules/hens-svg is not found` → `reading 'HensSvgView'`**：SVG 组件插件在标准基座/蒸汽模式下未注册，同样属于插件蒸汽兼容问题（标准基座无自定义插件本就预期降级，建议在代码里对 hens-svg 做存在性兜底）。
3. **`uni.request(...).then is not a function`**：符合文档（返回 RequestTask），不是 bug；提示后续调试探针不要再写 Promise 形态。
4. **logcat 中文乱码**（`鍚庡彴浠诲姟...`）：控制台输出编码问题，仅影响阅读，不影响运行。

---

## 参考链接汇总

- 蒸汽模式说明（5.21+ Android、JS 引擎驱动、uts2js）：https://doc.dcloud.net.cn/uni-app-x/app-vapor.html
- 官方公告（5.25 前不得 import .ts/.js；lang 属性要求）：https://ask.dcloud.net.cn/article/42377
- uni-app x uni.request API（data 类型约束、timeout、errCode 5、返回值 RequestTask）：https://doc.dcloud.net.cn/uni-app-x/api/request.html
- uni-app x uni.loadFontFace API（global/desc/url() 包裹）：https://doc.dcloud.net.cn/uni-app-x/api/load-font-face.html
- loadFontFace 官方实现（global vs 页面路径）：https://gitcode.com/dcloud/uni-api/blob/alpha/uni_modules/uni-loadFontFace/utssdk/app-android/index.uts
- 论坛：loadFontFace 在 App.vue onLaunch 报错（$page undefined，同族案例）：https://ask.dcloud.net.cn/question/192482
- 论坛：loadFontFace global 属性在 App 环境不生效：https://ask.dcloud.net.cn/question/213781
- uni-app x 更新日志（正式版/Alpha；含 loadFontFace app.uvue 修复、蒸汽字体内存泄漏修复、request 各修复）：https://doc.dcloud.net.cn/uni-app-x/release.html 、https://doc.dcloud.net.cn/uni-app-x/release-note-alpha.html
- 微信小程序网络能力（10 并发上限出处）：https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html
- 官方 bug 追踪（需登录）：https://issues.dcloud.net.cn/?mid=api.network.request 、https://issues.dcloud.net.cn/?mid=api.ui.loadFontFace
- adb 工具文档：https://developer.android.google.cn/tools/adb

## 验证清单（修复后逐项打勾）

- [ ] utils 21 个 .ts 改名 .uts，冷启动，`[dbg-req] ok /api/v1/events/timeline status=200` 出现
- [ ] `[dbg-req] ok /api/v1/echo/today` 出现，首页时间轴渲染
- [ ] 字体日志出现 `font loaded`（或至少有明确 fail 错误码而非 vm 异常），且 onLaunch 后续日志（sentry/ensureLogin/sync）完整
- [ ] `UTSAndroid is not defined` 不再出现（或已定位到插件侧）
- [ ] 连续冷启动 3 次均稳定（排除竞态）
