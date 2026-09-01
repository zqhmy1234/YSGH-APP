/**
 * Sentry 客户端上报（轻量信封协议 · 决策 #12 可观测双通道·客户端侧）
 *
 * 背景：uni-app x App 端无 DOM，@sentry/vue 无法运行；标准调试基座无三方 SDK。
 * 方案：按 Sentry Envelope 公开协议，uni.request POST 到 DSN 的 /api/<project>/envelope/ 端点，
 *       零三方依赖、标准基座可用（不依赖自定义基座波次）。
 * 升级：自定义基座波次可换 sentry-android 原生 SDK（崩溃捕获更强），本模块对外接口不变。
 * 纪律：DSN 为公开标识（Sentry 官方明文内嵌惯例，非机密，可随客户端分发）；
 *       本模块只上报错误/面包屑，绝不携带任何密钥。
 */
import { ENV, SENTRY_DSN } from './config'
import { isoUtc } from './time'

class SentryConfig {
	host: string
	project: string
	key: string
	enabled: boolean

	constructor(host: string, project: string, key: string, enabled: boolean) {
		this.host = host
		this.project = project
		this.key = key
		this.enabled = enabled
	}
}

// UTS 模块级变量不做空窄化 → 用非空实例 + enabled 标志（禁用时 host/project/key 为空串）
let cfg: SentryConfig = new SentryConfig('', '', '', false)
const MAX_BREADCRUMBS: number = 10
const breadcrumbs: Array<string> = []

/** 解析 DSN：https://<key>@<host>/<project> → 各段；格式不对返回禁用实例（不抛错） */
function parseDsn(dsn: string): SentryConfig {
	if (dsn == '') {
		return new SentryConfig('', '', '', false)
	}
	const atIdx = dsn.lastIndexOf('@')
	if (atIdx < 0) {
		return new SentryConfig('', '', '', false)
	}
	const key = dsn.substring(0, atIdx).replace('https://', '')
	const rest = dsn.substring(atIdx + 1)
	const slashIdx = rest.indexOf('/')
	if (slashIdx < 0) {
		return new SentryConfig('', '', '', false)
	}
	const host = rest.substring(0, slashIdx)
	const project = rest.substring(slashIdx + 1)
	if (host == '' || project == '') {
		return new SentryConfig('', '', '', false)
	}
	return new SentryConfig(host, project, key, true)
}

/** 启动时调用一次（App.uvue onLaunch） */
export function initSentry(): void {
	cfg = parseDsn(SENTRY_DSN)
	if (cfg.enabled) {
		console.log('[yishu] sentry enabled: ' + cfg.host + ' project=' + cfg.project)
	} else {
		console.log('[yishu] sentry disabled (empty/invalid DSN)')
	}
}

function esc(s: string): string {
	let out = ''
	for (let i = 0; i < s.length; i++) {
		const c = s.charAt(i)
		if (c == '"') {
			out += '\\"'
		} else if (c == '\\') {
			out += '\\\\'
		} else if (c == '\n') {
			out += '\\n'
		} else if (c == '\r') {
			out += '\\r'
		} else if (c == '\t') {
			out += '\\t'
		} else {
			out += c
		}
	}
	return out
}

function randomHex(len: number): string {
	let s = ''
	for (let i = 0; i < len; i++) {
		s += Math.floor(Math.random() * 16).toString(16)
	}
	return s
}

/** ISO8601 UTC（S1-M4 收口：统一走 time.isoUtc；Sentry Envelope sent_at 用） */
function isoNow(): string {
	return isoUtc(Date.now())
}

/** 面包屑（本地环形缓冲，随下一个事件上报；超上限丢最旧） */
export function addBreadcrumb(category: string, message: string): void {
	const ts = Math.floor(Date.now() / 1000)
	breadcrumbs.push(
		'{"timestamp":' + ts + ',"category":"' + esc(category) + '","message":"' + esc(message) + '"}'
	)
	if (breadcrumbs.length > MAX_BREADCRUMBS) {
		breadcrumbs.shift()
	}
}

function buildEvent(eventId: string, level: string, message: string, eventType: string | null, extraJson: string): string {
	const ts = Math.floor(Date.now() / 1000)
	const bc = breadcrumbs.length > 0
		? ',"breadcrumbs":{"values":[' + breadcrumbs.join(',') + ']}'
		: ''
	const exc = eventType != null
		? ',"exception":{"values":[{"type":"' + esc(eventType) + '","value":"' + esc(message) + '"}]}'
		: ',"message":{"formatted":"' + esc(message) + '"}'
	return '{' +
		'"event_id":"' + eventId + '"' +
		',"timestamp":' + ts +
		',"platform":"javascript"' +
		',"level":"' + level + '"' +
		',"environment":"' + esc(ENV) + '"' +
		',"release":"yishu-client@0.1.0"' +
		exc +
		extraJson +
		bc +
		'}'
}

function report(level: string, message: string, eventType: string | null, extra: UTSJSONObject | null): void {
	if (!cfg.enabled) {
		return
	}
	const eventId = randomHex(32)
	let extraJson = ''
	if (extra != null) {
		extraJson = ',"extra":' + extra.toJSONString()
	}
	const eventJson = buildEvent(eventId, level, message, eventType, extraJson)
	const dsn = 'https://' + cfg.key + '@' + cfg.host + '/' + cfg.project
	const envelope = '{"event_id":"' + eventId + '","dsn":"' + dsn + '","sent_at":"' + isoNow() + '"}\n' +
		'{"type":"event","content_type":"application/json","length":' + eventJson.length + '}\n' +
		eventJson
	const header: UTSJSONObject = {
		'Content-Type': 'application/x-sentry-envelope'
	}
	uni.request({
		url: 'https://' + cfg.host + '/api/' + cfg.project + '/envelope/',
		method: 'POST',
		data: envelope,
		header: header,
		timeout: 10000,
		success: () => {},
		fail: () => {}
	})
}

/** 上报异常（eventType=null 时按 message 事件上报） */
export function captureException(message: string, eventType: string | null, extra: UTSJSONObject | null): void {
	report('error', message, eventType, extra)
}

/** 上报普通消息（level: error/warning/info/debug） */
export function captureMessage(message: string, level: string): void {
	report(level, message, null, null)
}
