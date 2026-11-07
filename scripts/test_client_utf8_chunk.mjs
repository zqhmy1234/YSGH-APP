#!/usr/bin/env node
/**
 * 客户端纯函数用例 · 流式 UTF-8 跨块边界（P2 D-2 的证据件）
 *
 * ## 为什么要这个脚本
 * `client/utils/chat_api.uts` 的 SSE 通道是 `responseType: 'arraybuffer'` + `enableChunked`，
 * 每片 `onChunkReceived` 拿到的字节流**不保证落在字符边界**：一个汉字 3 字节、emoji 4 字节，
 * 完全可能被切成 1+2 / 2+1 两次到达。处理不当的两种典型故障：
 *   ① 整块 `decodeURIComponent` 抛错 ⇒ 整个 chunk 丢弃（"偶发少一段回复"）；
 *   ② 逐块解码再拼 ⇒ 半个汉字解出替换字符 U+FFFD（"中文长回复里的乱码"）。
 * `decodeUtf8`（chat_api.uts:283-303）用「尾部最多回退 4 字节 + 把未消费字节交还调用方
 * （`usedBytes`）」解决它。本仓已有该实现，但**从未有跨块用例证明**，故补此件。
 *
 * ## 单一事实源（不复制算法）
 * 脚本**从 `chat_api.uts` 原文里提取** `class Utf8Decoded` + `function decodeUtf8` 的函数体，
 * 落到临时 `.ts` 文件由 Node 原生类型擦除执行 ⇒ 测的是**生产代码本身**，不是它的复述。
 * 锚点/调用方暂存逻辑一旦在源码里变样，脚本**立即报错退出**（不允许静默跳过）：
 *   · 找不到 `class Utf8Decoded` / `function decodeUtf8(` ⇒ exit 1
 *   · 找不到 `pending = merged.slice(dec.usedBytes)`（调用方暂存契约）⇒ exit 1
 *
 * ## 覆盖
 *   A. 3 字节汉字、ASCII+汉字混合、4 字节 emoji：在**每一个**字节偏移处切成 2 块
 *   B. 三块切分（两个切点）
 *   C. 逐字节喂入（最坏情况：每片都只有 1 字节，跨 3~4 字节字符的全部中间态）
 *   D. 非法字节 0xFF 不吞后续合法文本，且保证 usedBytes ≥ 1（调用方循环可终止）
 *
 * ## 用法
 *   node scripts/test_client_utf8_chunk.mjs             # 全量
 *   node scripts/test_client_utf8_chunk.mjs --verbose   # 打印每个切分点
 * 退出码：0 = 全通过；1 = 有用例失败或锚点缺失（不得作为"通过"上报）。
 * 依赖：Node ≥ 22.18（原生 TS 类型擦除；本机实测 v24.0.1）。
 */
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(HERE, '..')
// 默认测生产文件；`YISHU_CHAT_API_PATH` 仅用于**补丁预演/回归对照**（把改过的副本指过来跑，
// 验证补丁确实让用例转绿，而不必先改生产文件）。生产验证请留空。
const CHAT_API = process.env.YISHU_CHAT_API_PATH
  ? resolve(process.env.YISHU_CHAT_API_PATH)
  : join(REPO_ROOT, 'client', 'utils', 'chat_api.uts')
const VERBOSE = process.argv.includes('--verbose')

// ── 0. Node 版本闸门（类型擦除是硬依赖，缺失即响亮失败）────────────────────────────
const [NODE_MAJOR, NODE_MINOR] = process.versions.node.split('.').map((x) => Number(x))
if (NODE_MAJOR < 22 || (NODE_MAJOR === 22 && NODE_MINOR < 18)) {
  console.error(`[FAIL] 需要 Node >= 22.18（原生 TS 类型擦除），当前 v${process.versions.node}`)
  process.exit(1)
}

function fail(msg) {
  console.error(`[FAIL] ${msg}`)
  process.exit(1)
}

// ── 1. 从生产源码提取被测函数（花括号配对，跳过字符串/注释）──────────────────────
function extractBlock(src, startIndex) {
  // startIndex 指向 '{'；返回结束 '}' 之后的下标
  let depth = 0
  let i = startIndex
  let state = 'code'
  while (i < src.length) {
    const c = src[i]
    const n = src[i + 1]
    if (state === 'code') {
      if (c === '/' && n === '/') { state = 'line'; i += 2; continue }
      if (c === '/' && n === '*') { state = 'block'; i += 2; continue }
      if (c === "'") { state = 'sq'; i++; continue }
      if (c === '"') { state = 'dq'; i++; continue }
      if (c === '`') { state = 'bt'; i++; continue }
      if (c === '{') depth++
      else if (c === '}') {
        depth--
        if (depth === 0) return i + 1
      }
      i++
      continue
    }
    if (state === 'line') { if (c === '\n') state = 'code'; i++; continue }
    if (state === 'block') { if (c === '*' && n === '/') { state = 'code'; i += 2; continue } i++; continue }
    if (state === 'sq') { if (c === '\\') { i += 2; continue } if (c === "'") state = 'code'; i++; continue }
    if (state === 'dq') { if (c === '\\') { i += 2; continue } if (c === '"') state = 'code'; i++; continue }
    if (state === 'bt') { if (c === '\\') { i += 2; continue } if (c === '`') state = 'code'; i++; continue }
  }
  return -1
}

const src = readFileSync(CHAT_API, 'utf8')

const clsAnchor = 'class Utf8Decoded {'
const clsAt = src.indexOf(clsAnchor)
if (clsAt < 0) fail(`锚点缺失：${CHAT_API} 里找不到 \`${clsAnchor}\`（decoder 结构变了，用例必须同步）`)
const clsEnd = extractBlock(src, clsAt + clsAnchor.length - 1)
if (clsEnd < 0) fail('锚点解析失败：class Utf8Decoded 花括号不配对')

const fnAnchor = 'function decodeUtf8('
const fnAt = src.indexOf(fnAnchor)
if (fnAt < 0) fail(`锚点缺失：${CHAT_API} 里找不到 \`${fnAnchor}\`（被测函数被改名/移走）`)
const fnBrace = src.indexOf('{', fnAt)
const fnEnd = extractBlock(src, fnBrace)
if (fnEnd < 0) fail('锚点解析失败：function decodeUtf8 花括号不配对')

// 调用方暂存契约（chat_api.uts 的 appendBytes）：未消费字节必须回寄存，否则边界字符永久丢失
if (!src.includes('pending = merged.slice(dec.usedBytes)')) {
  fail('锚点缺失：chat_api.uts 不再有 `pending = merged.slice(dec.usedBytes)`（调用方暂存契约变了）')
}

// 从 class 起始到 decodeUtf8 结尾**连续切片**：这样补丁若在两者之间加私有辅助函数
// （如缺失前缀判定），也会被一并带上，不会因为"辅助函数没被提取"而假红。
const snippet = [
  '// 由 scripts/test_client_utf8_chunk.mjs 从 chat_api.uts 原样提取，请勿手改',
  src.slice(clsAt, fnEnd),
  '',
  'export { decodeUtf8 }',
  ''
].join('\n')

const tmpDir = mkdtempSync(join(tmpdir(), 'yishu-utf8-'))
const tmpFile = join(tmpDir, 'decoder_extracted.ts')
writeFileSync(tmpFile, snippet, 'utf8')

let decodeUtf8
try {
  const mod = await import(pathToFileURL(tmpFile).href)
  decodeUtf8 = mod.decodeUtf8
} catch (e) {
  rmSync(tmpDir, { recursive: true, force: true })
  fail(`无法加载提取出的 decodeUtf8（Node 类型擦除失败？）: ${e && e.message ? e.message : e}`)
}
if (typeof decodeUtf8 !== 'function') fail('提取结果里没有 decodeUtf8 函数')
console.log(`[INFO] 被测源码：${CHAT_API}`)
console.log(`[INFO] 提取块：class Utf8Decoded (${clsEnd - clsAt} 字符) + function decodeUtf8 (${fnEnd - fnAt} 字符)`)

// ── 2. 复刻调用方暂存循环（chat_api.uts:538-555 的 appendBytes 语义）──────────────
/**
 * 与生产链路逐句对应：
 *   merged  = pending + chunk
 *   dec     = decodeUtf8(merged)
 *   pending = merged.slice(dec.usedBytes)
 *   decoded += dec.text
 * 这里只做"喂字节"的编排，不含任何解码逻辑（解码全在 decodeUtf8 里）。
 */
function feedChunks(chunks) {
  let pending = new Uint8Array(0)
  let decoded = ''
  const steps = []
  for (const chunk of chunks) {
    const merged = new Uint8Array(pending.length + chunk.length)
    merged.set(pending, 0)
    merged.set(chunk, pending.length)
    const dec = decodeUtf8(merged)
    if (!(dec.usedBytes >= 0 && dec.usedBytes <= merged.length)) {
      throw new Error(`usedBytes 越界：${dec.usedBytes} / ${merged.length}`)
    }
    pending = merged.slice(dec.usedBytes)
    decoded += dec.text
    steps.push({ consumed: dec.usedBytes, rest: pending.length, text: dec.text })
  }
  return { decoded, pending, steps }
}

const enc = new TextEncoder()

// ── 3. 用例 ───────────────────────────────────────────────────────────────────
let passed = 0
const failures = []

function check(name, cond, detail) {
  if (cond) {
    passed++
    if (VERBOSE) console.log(`  ok   ${name}`)
    return
  }
  failures.push(`${name}${detail ? ' — ' + detail : ''}`)
  console.error(`  FAIL ${name}${detail ? ' — ' + detail : ''}`)
}

/** 目标文本 + 切点 → 跑一遍，断言：完整还原、无替换字符、每步输出都是最终文本的前缀 */
function runSplitCase(label, text, cuts) {
  const bytes = enc.encode(text)
  const chunks = []
  let prev = 0
  for (const cut of cuts) {
    chunks.push(bytes.slice(prev, cut))
    prev = cut
  }
  chunks.push(bytes.slice(prev))
  let out
  try {
    out = feedChunks(chunks)
  } catch (e) {
    check(label, false, `抛出异常：${e && e.message ? e.message : e}`)
    return
  }
  const got = out.decoded
  check(`${label} 还原`, got === text, `期望 ${JSON.stringify(text)} 实得 ${JSON.stringify(got)}`)
  check(`${label} 无替换字符`, got.indexOf('\uFFFD') < 0, `出现 U+FFFD（乱码）：${JSON.stringify(got)}`)
  check(`${label} 尾部无残留`, out.pending.length === 0, `仍有 ${out.pending.length} 字节未消费`)
}

const SAMPLES = [
  ['三字节汉字', '你好，我是忆述光华'],
  ['ASCII+汉字+中点', 'AI：已记录 3 条 · 今日回顾'],
  ['四字节 emoji', '今天种了🌱，很开心'],
  ['汉字紧邻 CRLF', '第一行要点\n第二行要点\n\n'],
]

// A. 每一个字节偏移处切成两块
for (const [label, text] of SAMPLES) {
  const n = enc.encode(text).length
  for (let k = 1; k < n; k++) {
    runSplitCase(`${label} 2块@${k}`, text, [k])
  }
  console.log(`[INFO] ${label}：${n} 字节，覆盖切分点 1..${n - 1}`)
}

// B. 三块切分（两切点）
for (const [label, text] of SAMPLES) {
  const n = enc.encode(text).length
  const picks = [Math.max(1, Math.floor(n / 3)), Math.max(2, Math.floor((2 * n) / 3))]
  runSplitCase(`${label} 3块@${picks.join(',')}`, text, picks)
}

// C. 逐字节喂入（跨 3~4 字节字符的所有中间态）
for (const [label, text] of SAMPLES) {
  const bytes = enc.encode(text)
  const oneByOne = []
  for (let i = 0; i < bytes.length; i++) oneByOne.push(bytes.slice(i, i + 1))
  runSplitCase(`${label} 整块`, text, [])
  let out
  try {
    out = feedChunks(oneByOne)
  } catch (e) {
    check(`${label} 逐字节(分片)`, false, `抛出异常：${e && e.message ? e.message : e}`)
    continue
  }
  check(`${label} 逐字节(分片) 还原`, out.decoded === text, `实得 ${JSON.stringify(out.decoded)}`)
  check(`${label} 逐字节(分片) 无替换字符`, out.decoded.indexOf('\uFFFD') < 0, '出现 U+FFFD')
  check(`${label} 逐字节(分片) 尾部无残留`, out.pending.length === 0, `残留 ${out.pending.length} 字节`)
}

// D. 明确点名领队要求的两种切法：'回' = E5 9B 9E
{
  const c = '回'
  const b = enc.encode(c)
  check("'回' 是 3 字节", b.length === 3, `实为 ${b.length} 字节`)
  // 1+2
  let out = feedChunks([b.slice(0, 1), b.slice(1, 3)])
  check("'回' 切成 1+2 正确", out.decoded === c && out.pending.length === 0, `实得 ${JSON.stringify(out.decoded)}`)
  // 2+1
  out = feedChunks([b.slice(0, 2), b.slice(2, 3)])
  check("'回' 切成 2+1 正确", out.decoded === c && out.pending.length === 0, `实得 ${JSON.stringify(out.decoded)}`)
  // 长前缀 + 半个字（验证"只回退 4 字节"不会把前面的合法文本一起回退掉）
  const prefix = enc.encode('前面已经有很长一段中文了')
  const merged = new Uint8Array(prefix.length + 2)
  merged.set(prefix, 0)
  merged.set(b.slice(0, 2), prefix.length)
  const dec1 = decodeUtf8(merged)
  check('长前缀+半个汉字：前缀完整解出', dec1.text === '前面已经有很长一段中文了', `实得 ${JSON.stringify(dec1.text)}`)
  check('长前缀+半个汉字：只回退 2 字节', dec1.usedBytes === prefix.length, `usedBytes=${dec1.usedBytes} 期望 ${prefix.length}`)
  const rest = merged.slice(dec1.usedBytes)
  const dec2 = decodeUtf8(new Uint8Array([...rest, b[2]]))
  check('长前缀+半个汉字：补全后得「回」', dec2.text === c, `实得 ${JSON.stringify(dec2.text)}`)
}

// E. 非法字节：必须**按字节推进**（不能整块丢弃/死循环），其余字节留在 pending 里由下一次解出
{
  const bad = new Uint8Array([0xff])
  const decBad = decodeUtf8(bad)
  check('非法单字节：usedBytes>=1（调用方循环可终止）', decBad.usedBytes >= 1, `usedBytes=${decBad.usedBytes}`)

  const good = enc.encode('你好')
  const merged = new Uint8Array(1 + good.length)
  merged[0] = 0xff
  merged.set(good, 1)
  const dec = decodeUtf8(merged)
  // 契约（见 decodeUtf8 兜底注释）：只丢 1 个坏字节，其余保留 ⇒ 下一次 decode 必须能解出正文
  check('非法字节+合法文本：只丢 1 字节', dec.usedBytes === 1, `usedBytes=${dec.usedBytes}`)
  const restDec = decodeUtf8(merged.slice(dec.usedBytes))
  check('非法字节+合法文本：剩余字节可解出「你好」', restDec.text === '你好', `实得 ${JSON.stringify(restDec.text)}`)

  // 生产形态（两片）：[坏字节] 然后 [你好]
  let outBad
  try {
    outBad = feedChunks([bad, good])
  } catch (e) {
    check('非法字节 分两片', false, `抛出异常：${e && e.message ? e.message : e}`)
    outBad = null
  }
  if (outBad != null) {
    check('非法字节 分两片：正文不丢', outBad.decoded === '你好', `实得 ${JSON.stringify(outBad.decoded)}`)
  }
}

rmSync(tmpDir, { recursive: true, force: true })

// ── 4. 结论 ──────────────────────────────────────────────────────────────────
// ⚠️ 这里**不用 process.exit()**：动态 import 之后强杀进程会触发 Windows 上 libuv 的
// `Assertion failed: !(handle->flags & UV_HANDLE_CLOSING)` 崩溃（实测 0xC0000409），
// 退出码被平台改写，红/绿无法被 CI 正确读取。改用 exitCode 让事件循环自然退出。
if (failures.length > 0) {
  console.error(`\n[FAIL] ${failures.length} 项失败 / 共 ${passed + failures.length} 项断言`)
  for (const f of failures) console.error(`  - ${f}`)
  process.exitCode = 1
} else {
  console.log(`\n[OK] UTF-8 跨块用例全通过：${passed} 项断言（解码逻辑取自 chat_api.uts 原文）`)
  process.exitCode = 0
}
