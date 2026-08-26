/**
 * 语音入口（T-AU-1/2/3 + B5a Wave4 AgentJ J-4/J-7）
 *
 * 链路（后端契约）：
 *  uni.getRecorderManager()  → 录 wav（uni-app x App 端支持 wav 格式，16kHz 16bit 单声道）
 *  POST /api/v1/asr/transcribe（≤8MB 直传）→ AsrTranscribeResponse
 *    {text, channel, emotion, emotion_confidence, emotion_source, emotion_bonus, emotion_merge,
 *     audio_events, not_oral, silence_hint, snr_db, noise_weight, duration_ms, mock, guardrail}
 *  POST /api/v1/upload/init|chunk|complete（长录音 >8MB 分片持久化 → 音频落对象存储）
 *  POST /api/v1/contents（content_type=voice + cos_key → 管线 VAD 分段转写；>5min 进 VAD）
 *
 * J-4 长录音入口：上限 3.5min → 30min；>8MB 走分片持久化上传（复用 /upload 分片协议），
 * 后建 voice 内容（cos_key）触发后端 VAD 分段转写。
 *
 * J-7 录音中断状态机：由系统能力层插件 yishu-recorder 实现（RECORDING→INTERRUPTED→
 * 恢复/暂停/30min 自动结束分段保存），本文件为领域桥接，导出稳定函数签名。
 *
 * 约定：resolve-only（永不 reject），失败 resolve(null) + toast。
 */
// O9 收口：上传/错误信封解析统一走 api.ts（原本地 parseEnvelope/parseError 副本已删）
import { post, dataObj, showErrorToast, parseEnvelopeString, parseErrorString } from './api'
import { getBaseUrl } from './config'
import { getToken as getTokenForUpload } from './auth'
// TD-P2B（S1-H3）：分片上传协议统一走 upload_protocol.ts（原 urlEncode/formPost/fieldOf
// 与 uploader.ts 整段复制，收口后本文件只保留录音业务编排）
import { UploadResp, completeUpload, fieldOf, initUpload, putChunk } from './upload_protocol'
import { createRecorder, RecorderController } from '@/uni_modules/yishu-recorder/utssdk/app-android/index.uts'

/** 录音上限（J-4 放开 3.5min → 30min；后端 duration 到达自动 onStop 分段保存） */
export const MAX_RECORD_MS: number = 1800000
/** 直传转写上限（后端 MAX_AUDIO_BYTES=8MB；超过走分片持久化） */
export const MAX_DIRECT_UPLOAD_BYTES: number = 8 * 1024 * 1024

// 录音状态（J-7，转发插件常量）
export const REC_STATE_IDLE: string = 'idle'
export const REC_STATE_RECORDING: string = 'recording'
export const REC_STATE_INTERRUPTED: string = 'interrupted'
export const REC_STATE_PAUSED: string = 'paused'

export class AsrResult {
	text: string
	channel: string
	emotion: string
	emotionConfidence: number
	emotionSource: string
	/** 笑声等正向音频事件带来的情绪加分（B5a J-3；P1-A 对齐：消费后端 emotion_bonus） */
	emotionBonus: boolean
	emotionMerge: UTSJSONObject | null
	audioEvents: Array<string>
	notOral: boolean
	silenceHint: boolean
	snrDb: number | null
	noiseWeight: string
	confidence: number
	durationMs: number
	mock: boolean
	guardPassed: boolean
	guardReason: string

	constructor(
		text: string,
		channel: string,
		emotion: string,
		emotionConfidence: number,
		emotionSource: string,
		emotionBonus: boolean,
		emotionMerge: UTSJSONObject | null,
		audioEvents: Array<string>,
		notOral: boolean,
		silenceHint: boolean,
		snrDb: number | null,
		noiseWeight: string,
		confidence: number,
		durationMs: number,
		mock: boolean,
		guardPassed: boolean,
		guardReason: string
	) {
		this.text = text
		this.channel = channel
		this.emotion = emotion
		this.emotionConfidence = emotionConfidence
		this.emotionSource = emotionSource
		this.emotionBonus = emotionBonus
		this.emotionMerge = emotionMerge
		this.audioEvents = audioEvents
		this.notOral = notOral
		this.silenceHint = silenceHint
		this.snrDb = snrDb
		this.noiseWeight = noiseWeight
		this.confidence = confidence
		this.durationMs = durationMs
		this.mock = mock
		this.guardPassed = guardPassed
		this.guardReason = guardReason
	}
}

/** 录音控制器单例（系统能力层插件 yishu-recorder，J-7 状态机持有者） */
let _controller = createRecorder()

export function getRecorder(): RecorderController {
	return _controller
}

export function lastTempFile(): string {
	return _controller.lastFile()
}

/** 当前录音状态（REC_STATE_*，转发插件） */
export function recorderState(): string {
	return _controller.state()
}

export function recordedMs(): number {
	return _controller.recordedMs()
}

/** 设置中断恢复策略：来电/闹钟 shouldResume=true 时中断结束后自动恢复录音 */
export function setShouldResume(flag: boolean): void {
	_controller.setShouldResume(flag)
}

/**
 * 开始录音（wav 16k 单声道，≤30min）。回调：
 *  onStart / onStop / onError（原有）
 *  onInterrupted(autoPaused)（J-7：中断已自动暂停）
 *  onAutoStop(path, durationMs)（J-7：30min 自动结束，落盘分段）
 */
export function startRecord(
	onStart: () => void,
	onStop: (path: string, durationMs: number) => void,
	onError: (msg: string) => void,
	onInterrupted: (autoPaused: boolean) => void,
	onAutoStop: (path: string, durationMs: number) => void
): void {
	_controller.start(onStart, onStop, onError, onInterrupted, onAutoStop)
}

/** 停止录音（落盘当前分段） */
export function stopRecord(): void {
	_controller.stop()
}

/** J-7：用户手动暂停（RECORDING→PAUSED） */
export function pauseRecord(): void {
	_controller.pause()
}

/** J-7：恢复录音（PAUSED/INTERRUPTED→RECORDING） */
export function resumeRecord(): void {
	_controller.resume()
}

/** POST /asr/transcribe：上传 wav 转写（≤8MB 直传）；返回 AsrResult 或 null */
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
					const body = parseEnvelopeString(txt)
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
					// B5a J-1/J-3：音频事件 / 段级情绪合并 / 噪音标记
					const merge = d.getJSON('emotion_merge')
					const ae = d.getArray('audio_events') as Array<string> | null
					const snrRaw = d.getNumber('snr_db')
					const snr: number | null = snrRaw == null ? null : (snrRaw as number)
					resolve(
						new AsrResult(
							d.getString('text') ?? '',
							d.getString('channel') ?? '',
							d.getString('emotion') ?? '平静',
							(d.getNumber('emotion_confidence') as number) ?? 0,
							d.getString('emotion_source') ?? 'none',
							d.getBoolean('emotion_bonus') ?? false,
							merge,
							ae != null ? ae : [],
							d.getBoolean('not_oral') ?? false,
							d.getBoolean('silence_hint') ?? false,
							snr,
							d.getString('noise_weight') ?? 'high',
							(d.getNumber('confidence') as number) ?? 0,
							(d.getNumber('duration_ms') as number) ?? 0,
							d.getBoolean('mock') ?? false,
							passed,
							g != null ? (g.getString('reason') ?? '') : ''
						)
					)
					return
				}
				// 非 200：尝试解析错误信封
				const errMsg = parseErrorString(res.data as string)
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

/** POST /contents：语音内容入库（voice 类型，可选携带 cos_key 长音频）；返回 content_id 或 null */
export function saveVoiceContent(
	text: string,
	durationMs: number,
	emotion: string,
	cosKey: string = '',
	filePath: string = ''
): Promise<string | null> {
	const extra: UTSJSONObject = {
		duration_ms: durationMs
	}
	if (filePath != '') {
		extra.set('file_name', fileNameOf(filePath))
	}
	const body: UTSJSONObject = {
		content_type: 'voice',
		text: text,
		extra: extra,
		source: 'app'
	}
	if (cosKey != '') {
		// J-4：长录音音频已落对象存储 → 带 cos_key，管线据此下载转写（>5min 走 VAD）
		body.set('cos_key', cosKey)
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

function fileNameOf(path: string): string {
	const idx = path.lastIndexOf('/')
	return idx >= 0 ? path.substring(idx + 1) : path
}

// ---- J-4 长录音分片持久化上传（复用 /upload 分片协议；单块 = 整文件） ----
// 协议请求（init/chunk/complete + urlEncode/formPost/fieldOf）统一走 upload_protocol.ts（TD-P2B S1-H3）

/**
 * J-4 长录音上传：wav 文件 → 分片协议落对象存储（file_key）→ 建 voice 内容（cos_key）→ 管线转写。
 * 返回 {content_id, file_key, long_audio} 或 null。集成登记：后端 complete 的 meta 支持
 * content_type=voice 后直接返回 voice content_id（register_photo_content 增加 voice 分支），
 * 本函数随后可去掉二次建内容请求。
 */
export function uploadVoicePersistent(
	filePath: string,
	durationMs: number
): Promise<UTSJSONObject | null> {
	return new Promise<UTSJSONObject | null>((resolve) => {
		const clientUploadId = 'voice|' + filePath
		const fileName = fileNameOf(filePath)
		// 文件大小（录音临时文件可用 getFileSystemManager）
		uni.getFileSystemManager().getFileInfo({
			filePath: filePath,
			success: (info) => {
				const fileSize = info.size
				if (fileSize <= 0) {
					showErrorToast(new Error('音频文件大小为 0'))
					resolve(null)
					return
				}
				// 1. init（单块协议：chunk_size = file_size，同 uploader v2 约定）
				initUpload(clientUploadId, fileName, fileSize, 'original').then((initResp: UploadResp) => {
					if (initResp.status !== 200) {
						showErrorToast(new Error('长录音上传初始化失败'))
						resolve(null)
						return
					}
					const uploadId = fieldOf(initResp.raw, 'upload_id')
					if (uploadId == '') {
						showErrorToast(new Error('上传初始化无 upload_id'))
						resolve(null)
						return
					}
					// 2. chunk（POST multipart 单块）
					putChunk(uploadId, filePath, 120000).then((status: number) => {
						if (status !== 200) {
							showErrorToast(new Error('长录音分片上传失败'))
							resolve(null)
							return
						}
						// 3. complete（meta 带 content_type=voice，集成后后端直接建 voice 内容）
						const meta: UTSJSONObject = {
							content_type: 'voice',
							duration_ms: durationMs,
							source: 'app',
							extra: { file_name: fileName }
						}
						completeUpload(uploadId, meta).then((resp: UploadResp) => {
							if (resp.status !== 200) {
								showErrorToast(new Error('长录音上传完成失败'))
								resolve(null)
								return
							}
							const fileKey = fieldOf(resp.raw, 'file_key')
							// 4. 建 voice 内容：集成后 complete 直接返回 voice content_id（后端 voice 分支已建库+入队）
							//    旧后端无 content_id → 回退二次建内容请求（/contents 带 cos_key，后端按 cos_key 幂等去重）
							const voiceContentId = fieldOf(resp.raw, 'content_id')
							if (voiceContentId != '') {
								const out: UTSJSONObject = {
									content_id: voiceContentId,
									file_key: fileKey,
									long_audio: true
								}
								resolve(out)
								return
							}
							saveVoiceContent('', durationMs, '', fileKey, filePath).then((contentId: string | null) => {
								if (contentId == null) {
									showErrorToast(new Error('长录音内容入库失败'))
									resolve(null)
									return
								}
								const out: UTSJSONObject = {
									content_id: contentId,
									file_key: fileKey,
									long_audio: true
								}
								resolve(out)
							})
						})
					})
				})
			},
			fail: () => {
				showErrorToast(new Error('读取音频文件信息失败'))
				resolve(null)
			}
		})
	})
}
