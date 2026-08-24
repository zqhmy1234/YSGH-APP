/**
 * 语音入口（T-AU-1/2/3）：录音 wav → ASR 转写 → 可编辑 → 入库
 *
 * 链路（后端契约）：
 *  uni.getRecorderManager()      → 录 wav（uni-app x App 端支持 wav 格式）
 *  POST /api/v1/asr/transcribe   → multipart file（wav 16kHz 16bit 单声道 ≤8MB）→ AsrTranscribeResponse
 *    {text, channel, emotion, confidence, duration_ms, mock, guardrail:{passed, reason}}
 *  POST /api/v1/contents         → voice 类型入库（content_type=voice, text=转写文本, extra 带时长）
 *
 * 录音约束（后端 _MAX_AUDIO_BYTES=8MB ≈ 4 分钟 16kHz 16bit 单声道）：
 *  前端在 3.5 分钟自动停止，避免超限。
 *
 * 约定：resolve-only（永不 reject），失败 resolve(null) + toast。
 */
import { post, dataObj, showErrorToast } from './api'
import { getBaseUrl } from './config'
import { getToken as getTokenForUpload } from './auth'

export const MAX_RECORD_MS: number = 210000 // 3.5 分钟（8MB 上限前自动停）

export class AsrResult {
	text: string
	channel: string
	emotion: string
	confidence: number
	durationMs: number
	mock: boolean
	guardPassed: boolean
	guardReason: string

	constructor(
		text: string,
		channel: string,
		emotion: string,
		confidence: number,
		durationMs: number,
		mock: boolean,
		guardPassed: boolean,
		guardReason: string
	) {
		this.text = text
		this.channel = channel
		this.emotion = emotion
		this.confidence = confidence
		this.durationMs = durationMs
		this.mock = mock
		this.guardPassed = guardPassed
		this.guardReason = guardReason
	}
}

/** 录音管理器单例（uni-app x RecorderManager） */
let _recorder: RecorderManager | null = null

export function getRecorder(): RecorderManager {
	if (_recorder == null) {
		_recorder = uni.getRecorderManager()
	}
	return _recorder!
}

/** 录音临时文件路径（start 后 onStop 回调返回） */
let _tempFilePath: string = ''

/** 录音开始时刻（用于时长计算：onStop 结果无 duration 字段，需自行计时） */
let _recordStartMs: number = 0

export function lastTempFile(): string {
	return _tempFilePath
}

/** 开始录音（wav）；onStart/onStop/onError 由调用方通过参数注入（UTS 回调传递约束） */
export function startRecord(
	onStart: () => void,
	onStop: (path: string, durationMs: number) => void,
	onError: (msg: string) => void
): void {
	const mgr = getRecorder()
	_tempFilePath = ''
	_recordStartMs = Date.now()
	mgr.onStart(() => {
		onStart()
	})
	mgr.onStop((res: RecorderManagerOnStopResult) => {
		const path = res.tempFilePath
		_tempFilePath = path
		const dur = Date.now() - _recordStartMs
		onStop(path, dur)
	})
	mgr.onError((err: IRecorderManagerFail) => {
		onError(err.errMsg)
	})
	mgr.start({
		format: 'wav',
		sampleRate: 16000,
		numberOfChannels: 1,
		encodeBitRate: 256000,
		duration: MAX_RECORD_MS
	})
}

/** 停止录音 */
export function stopRecord(): void {
	const mgr = getRecorder()
	mgr.stop()
}

/** POST /asr/transcribe：上传 wav 转写（multipart）；返回 AsrResult 或 null */
export function transcribeWav(filePath: string): Promise<AsrResult | null> {
	return new Promise<AsrResult | null>((resolve) => {
		uni.uploadFile({
			url: getBaseUrl() + '/api/v1/asr/transcribe?preferred=auto',
			filePath: filePath,
			name: 'file',
			header: {
				'Authorization': 'Bearer ' + getTokenForUpload()
			},
			success: (res) => {
				if (res.statusCode === 200) {
					// 教训（lessons.md #3）：uploadFile 的 res.data 是 string（JS 引擎无 UTSJSONObject.parse）
					const txt = res.data as string
					const body = parseEnvelope(txt)
					if (body == null) {
						showErrorToast(new Error('转写响应解析失败'))
						resolve(null)
						return
					}
					const d = body.getJSON('data')
					if (d == null) {
						showErrorToast(new Error('转写结果为空'))
						resolve(null)
						return
					}
					const g = d.getJSON('guardrail')
					const passed = g != null ? (g.getBoolean('passed') ?? true) : true
					resolve(
						new AsrResult(
							d.getString('text') ?? '',
							d.getString('channel') ?? '',
							d.getString('emotion') ?? '平静',
							d.getNumber('confidence') as number,
							d.getNumber('duration_ms') as number,
							d.getBoolean('mock') ?? false,
							passed,
							g != null ? (g.getString('reason') ?? '') : ''
						)
					)
					return
				}
				// 非 200：尝试解析错误信封
				const errMsg = parseError(res.data as string)
				showErrorToast(new Error(errMsg))
				resolve(null)
			},
			fail: () => {
				showErrorToast(new Error('转写请求失败，请检查网络'))
				resolve(null)
			}
		})
	})
}

/** 上传响应信封解析（res.data 为 string；用字符串拆解拿 JSON 部分） */
function parseEnvelope(txt: string): UTSJSONObject | null {
	const idx = txt.indexOf('{')
	if (idx < 0) {
		return null
	}
	const jsonStr = txt.substring(idx)
	try {
		return JSON.parse(jsonStr) as UTSJSONObject
	} catch (e) {
		return null
	}
}

/** 错误信封解析（非 200 场景） */
function parseError(txt: string): string {
	const body = parseEnvelope(txt)
	if (body != null) {
		const msg = body.getString('message')
		if (msg != null && msg != '') {
			return msg
		}
	}
	return '请求失败（HTTP 非 200）'
}

/** POST /contents：语音内容入库（voice 类型）；返回 content_id 或 null */
export function saveVoiceContent(text: string, durationMs: number, emotion: string): Promise<string | null> {
	const extra: UTSJSONObject = {
		duration_ms: durationMs
	}
	const body: UTSJSONObject = {
		content_type: 'voice',
		text: text,
		extra: extra,
		source: 'app'
	}
	return new Promise<string | null>((resolve) => {
		post('/api/v1/contents', body).then((res: UTSJSONObject | null) => {
			if (res == null) {
				resolve(null)
				return
			}
			const d = dataObj(res)
			if (d == null) {
				resolve(null)
				return
			}
			resolve(d.getString('id'))
		})
	})
}
