#!/usr/bin/env node
/**
 * check_highlight_split.mjs —— client/utils/highlight.uts 的**切分逻辑**用例（2026-10-30 · P2 C-8）
 *
 * 用法：node scripts/dev/check_highlight_split.mjs
 * 退出码：0 = 全部通过；1 = 有用例不过；2 = **判据无法判定**（源码读不到 / 形态漂移 / 门禁环境误用探针）
 *    ⚠️ 2 必须在门禁里映射成"降级放行"，1 才是阻断。把 2 当 1 会把环境问题误报成代码缺陷。
 *    ⚠️ 探针模式（`CHECK_HIGHLIGHT_SRC`）只给本地变异测试用：生效时 stderr 打 `[PROBE MODE]` banner，
 *       且在 `CI` / `REVIEW_GATE` 下**直接 exit 2 拒绝** —— 否则"忘了 unset"会让人拿副本判绿。
 *
 * 为什么直读源码而不是抄一份镜像实现：
 * 抄镜像 = 两份实现，改了 `highlight.uts` 而没改镜像 ⇒ 用例绿、线上坏。这里直读
 * `client/utils/highlight.uts` 的**真实源码**，只剥类型注解后解释执行 ⇒ 无镜像漂移。
 *
 * ── 诚实边界（别把这份输出读成"功能已验"）──
 *  · 验的是**切分逻辑**（大小写不敏感、多次命中、边界不偏移、空文本/空关键词契约）；
 *  · **不**构成 UTS 类型正确性证据 —— 类型归编译门；
 *  · **不**构成端上渲染证据 —— 嵌套 `<text v-for>` 是否成立归真机；
 *  · 用例表 19 条与 `highlight.uts` 头注的表一一对应，改任一侧都要同步另一侧。
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const HERE = path.dirname(fileURLToPath(import.meta.url))
// 默认直读真实源码。`CHECK_HIGHLIGHT_SRC` 只服务**漂移探针**（复制一份改坏，确认脚本会红）——
// 但它本身会开一个"静默变绿"的侧门：探针副本留在变量里没 unset、后续提交就被判据副本判绿。
// 故两条硬约束（缺一不可）：
//   ① 门禁环境下（CI / REVIEW_GATE）**拒绝** override，exit 2 —— 探针不得参与判定；
//   ② override 生效时向 stderr 打醒目 banner —— 让人看见"这次结论不是真实源码给的"。
const OVERRIDE = process.env.CHECK_HIGHLIGHT_SRC
const GATE_ENV = !!(process.env.CI || process.env.REVIEW_GATE)
if (OVERRIDE && GATE_ENV) {
	console.error('[drift] 检测到门禁环境（CI / REVIEW_GATE），拒绝 CHECK_HIGHLIGHT_SRC 覆盖')
	console.error('        探针模式不得用于门禁判定；请 unset 该变量后重跑')
	process.exit(2)
}
const SRC = OVERRIDE ? path.resolve(OVERRIDE) : path.resolve(HERE, '../../client/utils/highlight.uts')
if (OVERRIDE) {
	console.error(`[PROBE MODE] 判据来自 ${SRC}，**不是**真实源码；本次结论不可用于交付/门禁`)
}

/** 防漂移 ①：函数/类被改名或删除时立刻报 exit 2，而不是"用例变少还全绿" */
const MARKERS = [
	'class HighlightPart',
	'function stripTrailingMarks',
	'function foldUnit',
	'function splitHighlight',
]

// 读不到源码必须是**环境错误 exit 2**，不能是 exit 1：1 的语义是"切分逻辑有缺陷"，
// 会把"文件被改名/路径写错/源码误删"误报成代码问题，挡住所有人的提交且原因不可读。
let raw
try {
	raw = readFileSync(SRC, 'utf8')
} catch (e) {
	console.error(`[drift] 读不到源码 ${SRC}：${e.message}`)
	console.error('        判据无法判定（环境/路径问题），不是切分逻辑缺陷')
	process.exit(2)
}
// 切片**从 class HighlightPart 起**：连类体一起进 eval，避免"类只被检查存在、改了字段却不红"的镜像残留
const start = raw.indexOf('class HighlightPart')
for (const m of MARKERS) {
	if (!raw.includes(m)) {
		console.error(`[drift] ${path.relative(process.cwd(), SRC)} 里找不到 "${m}" —— 用例脚本必须同步更新`)
		process.exit(2)
	}
}
if (start < 0) {
	console.error('[drift] 取不到 class HighlightPart 的起始位置（它必须在 stripTrailingMarks 之前）')
	process.exit(2)
}

// 只剥类型注解（UTS → JS）：形参/局部变量注解 `x: T`、返回值注解 `): T {`
const body = raw.slice(start).split('export ').join('')
const js = body
	.replace(/(\w+)\s*:\s*(Array<[^>]*>|string|number|boolean)/g, '$1')
	.replace(/\)\s*:\s*(Array<[^>]*>|string|number|boolean|void)\s*\{/g, ') {')

/** 防漂移 ②：注解没剥干净 ⇒ 说明源码用了新的类型写法，脚本会 eval 失败，这里先给可读报错 */
if (/:\s*(Array<[^>]*>|string|number|boolean|void)/.test(js)) {
	const left = js.split('\n').filter((l) => /:\s*(Array<[^>]*>|string|number|boolean|void)/.test(l))
	console.error('[drift] 类型注解未剥净，请扩展本脚本的剥离规则：')
	for (const l of left) {
		console.error('        ' + l.trim())
	}
	process.exit(2)
}

let splitHighlight
try {
	// 用 IIFE 包住，避免声明落到模块作用域；末尾 return 出被测函数
	// 注意：`HighlightPart` **不在这里定义**，它随切片一起从真实源码 eval 进来（零镜像）
	const end = js.lastIndexOf('}')
	const factory = eval(`(function(){ ${js.slice(0, end + 1)} ; return splitHighlight })`)
	splitHighlight = factory()
} catch (e) {
	console.error('[drift] 源码无法解释执行：' + e.message)
	console.error('        常见原因：① 新类型写法未纳入上方剥离规则；② stripTrailingMarks 被挪到 foldUnit/splitHighlight 之后')
	process.exit(2)
}
if (typeof splitHighlight !== 'function') {
	console.error('[drift] eval 未取到 splitHighlight')
	process.exit(2)
}

/** `|0` = 普通段，`|1` = 命中段，段间用 ` ; ` 连接（与 highlight.uts 头注同一形态） */
const render = (parts) => parts.map((p) => `${p.text}|${p.hit ? 1 : 0}`).join(' ; ')

const CASES = [
	['今天在湖边散步', '湖边', '今天在|0 ; 湖边|1 ; 散步|0', '中文单命中，前后缀各一段'],
	['Lake lake LAKE', 'lake', 'Lake|1 ;  |0 ; lake|1 ;  |0 ; LAKE|1', '大小写不敏感、保留原文大小写、多次命中'],
	['abc', '', 'abc|0', '空关键词 ⇒ 原样单段'],
	['abc', '   ', 'abc|0', '纯空白关键词 ⇒ 原样单段'],
	['abc', 'zzz', 'abc|0', '未命中 ⇒ 原样单段'],
	['', 'x', '|0', '空文本仍返回一段（契约：至少一段）'],
	['aaaa', 'aa', 'aa|1 ; aa|1', '多次命中不重叠'],
	['湖边', '湖边散步', '湖边|0', '关键词比原文长，不崩'],
	['测试湖边湖边结束', '湖边', '测试|0 ; 湖边|1 ; 湖边|1 ; 结束|0', '相邻命中不产出空段'],
	['İstanbul', 'stan', 'İ|0 ; stan|1 ; bul|0', '长度变化字符：定位不偏移、不多吞'],
	['İX', 'İ', 'İ|1 ; X|0', '关键词自身长度变化：不多吞字符'],
	['ΟΣ', 'ΟΣ', 'ΟΣ|1', '终位 sigma：两侧同一归一化后才命中'],
	['ς', 'Σ', 'ς|1', 'ς→σ 双向收敛：输入终位 sigma 也命中 Σ'],
	['İstanbul', 'i', 'İ|1 ; stanbul|0', '单命中不误吞整词'],
	['ΑΣ', 'ς', 'Α|0 ; Σ|1', 'Σ/σ/ς 三形态收敛，命中落回原文 Σ'],
	['İİ', 'i', 'İ|1 ; İ|1', '相邻命中在 expansion 上不重叠、不重复出字符'],
	['İstanbul', 'istanbul', 'İstanbul|1', '剥离组合附加符后：纯拉丁关键词整串命中，不再"先亮后灭"'],
	['İ ', 'i', 'İ|1 ;  |0', '剥离只影响匹配，不改变回显文本与切分位置'],
	['a\u0307b', '\u0307', 'a\u0307b|0', '关键词全为组合附加符 ⇒ 剥离后为空 ⇒ 走"关键词为空"分支'],
]

let bad = 0
for (let i = 0; i < CASES.length; i++) {
	const [text, kw, expected, note] = CASES[i]
	const got = render(splitHighlight(text, kw))
	const ok = got === expected
	if (!ok) {
		bad += 1
		console.log(`case${i + 1} FAIL  ${note}`)
		console.log(`        in  text=${JSON.stringify(text)} kw=${JSON.stringify(kw)}`)
		console.log(`        exp ${JSON.stringify(expected)}`)
		console.log(`        got ${JSON.stringify(got)}`)
	} else {
		console.log(`case${i + 1} PASS`)
	}
}

console.log('')
if (bad === 0) {
	console.log(`ALL PASS (${CASES.length}) —— 仅证切分逻辑；UTS 类型归编译门，端上渲染归真机`)
	process.exit(0)
}
console.log(`MISMATCH ${bad}/${CASES.length}`)
process.exit(1)
