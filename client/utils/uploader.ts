/**
 * 照片批量上传 v2（S-ST-1 分片协议 + 断点续传 · 2026-08-25）
 * —— 2026-08-26 Wave3 H：B4 客户端域增强
 *
 * 替换 v1 multipart（POST /contents/upload）为后端分片协议：
 *   init → chunk(PUT) → complete(meta)（后端 complete 建 contents 记录 + 入队管线）
 *   断点续传：upload_id 持久化到 uni storage，重试/重启后 GET /upload/status 只补缺失片
 *
 * 分片粒度说明：UTS 无可靠的 ArrayBuffer 切片/临时文件 API，MVP 照片 ≤20MB，
 * 采用单块协议（chunk_size = file_size，chunk_count = 1）——仍走完整分片状态机
 * （init 幂等 / status 断点 / complete 集成），断电续传语义完整；>8MB 真分片
 * 待 Windows 大文件场景（第四波）再补，后端已支持任意 chunk_size。
 *
 * Wave3 H 新增（B4 客户端 6 项中的 4 项）：
 *  - 流量约束（B4 §5）：uni.getNetworkType 判断——WiFi/有线传原图，蜂窝只传
 *    缩略图+元数据（调用 Agent G 的 upload_mode 参数；G 未完成前按契约 mock：
 *    蜂窝自动=暂缓原图入 held 队列，等 WiFi 或"立即上传原图"手动入口）；
 *    "立即上传原图" = uploadNowOriginal() / continuePendingUploads() 手动入口。
 *  - 指数退避重试：2s→4s→8s→8s→8s（5 次上限，复用 event_sync 模式）；
 *    4xx 停该条（参数/归属错误重试无意义）。
 *  - 批量失败暂停恢复：连续失败 ≥10 → pauseSync() 暂停剩余（保留队列）→
 *    顶部横幅"网络异常，已暂停同步" → "继续上传"一键恢复（与 sync_client 共享暂停控制器）。
 *  - held/failed 持久队列：蜂窝暂缓（held）+ 重试耗尽（failed）的照片路径持久化，
 *    供 UploadStatusBanner 展示"待上传/失败标红可点击重试/继续上传"。
 *
 * 兼容：uploadBatch(items, onProgress) 原签名不变（opts 可选，默认 auto）。
 */
import { getBaseUrl } from './config'
import { getToken, ensureLogin } from './auth'
import { PhotoItem } from '@/uni_modules/yishu-photo-watch/utssdk/interface.uts'
import {
	pauseSync,
	resumeSync,
	isSyncPaused,
	registerConsecutiveFailure,
	resetConsecutiveFailures,
	MAX_BATCH_FAILURES,
	onNetworkRestored
} from './sync_client'

export const MAX_CONCURRENCY: number = 3
/** 退避次数上限（与 event_sync BACKOFF_MS 对齐：2s→4s→8s→8s→8s） */
export const MAX_RETRY: number = 5
const BACKOFF_MS: number[] = [2000, 4000, 8000, 8000, 8000]

/** 断点续传：path|uploadId 逐行存 uni storage（UTS 无可靠 JSON.parse，用分隔串） */
const PENDING_KEY: string = 'yishu_pending_uploads'
/** 蜂窝暂缓原图：path|takenAt|width|height|size|lat|lng（等待 WiFi 或手动上传原图） */
const HELD_KEY: string = 'yishu_held_uploads'
/** 重试耗尽失败项：同上格式（失败标红可点击重试） */
const FAILED_KEY: string = 'yishu_failed_uploads'

export class UploadProgress {
	done: number
	total: number
	failed: number

	constructor(done: number, total: number, failed: number) {
		this.done = done
		this.total = total
		this.failed = failed
	}
}

/** 单张上传成功结果：本地照片 + 云侧 content_id（端侧聚合与事件上云用） */
export class UploadedPhoto {
	item: PhotoItem
	contentId: string

	constructor(item: PhotoItem, contentId: string) {
		this.item = item
		this.contentId = contentId
	}
}

/** 上传模式（B4 流量约束） */
export type UploadMode = 'auto' | 'original' | 'thumbnail' | 'hold'

export class UploadOptions {
	mode: UploadMode

	constructor(mode: UploadMode = 'auto') {
		this.mode = mode
	}
}

/** HTTP 状态错误（4xx=永久停该条；5xx/网络=可退避重试） */
class UploadHttpError extends Error {
	status: number

	constructor(status: number, message: string) {
		super(message)
		this.status = status
	}
}

function is4xx(e: Error): boolean {
	if (e instanceof UploadHttpError) {
		const s = e.status
		return s >= 400 && s < 500
	}
	return false
}

/** epoch ms → ISO8601 本地时间（UTS Date 无 toISOString，手动拼接） */
export function isoString(ms: number): string {
	const d = new Date(ms)
	const pad = (n: number): string => (n < 10 ? '0' + n : '' + n)
	return (
		d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
		'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) + '+08:00'
	)
}

// ---------- 断点续传持久化 ----------

function readPending(): Array<string> {
	const raw = uni.getStorageSync(PENDING_KEY)
	if (raw == null || raw == '') {
		return []
	}
	return (raw as string).split('\n')
}

function writePending(lines: Array<string>): void {
	const clean: Array<string> = []
	for (let i = 0; i < lines.length; i++) {
		if (lines[i] != '') {
			clean.push(lines[i])
		}
	}
	uni.setStorageSync(PENDING_KEY, clean.join('\n'))
}

function findPending(path: string): string {
	const lines = readPending()
	for (let i = 0; i < lines.length; i++) {
		const parts = lines[i].split('|')
		if (parts.length >= 2 && parts[0] == path) {
			return parts[1]
		}
	}
	return ''
}

function savePending(path: string, uploadId: string): void {
	const lines = readPending()
	for (let i = 0; i < lines.length; i++) {
		if (lines[i].startsWith(path + '|')) {
			lines[i] = path + '|' + uploadId
			writePending(lines)
			return
		}
	}
	lines.push(path + '|' + uploadId)
	writePending(lines)
}

function clearPending(path: string): void {
	const lines = readPending()
	const kept: Array<string> = []
	for (let i = 0; i < lines.length; i++) {
		if (!lines[i].startsWith(path + '|')) {
			kept.push(lines[i])
		}
	}
	writePending(kept)
}

// ---------- 流量约束：网络类型判断 ----------

/** 网络类型：wifi（wifi/ethernet 无限流量视为 wifi）/ cellular / none / unknown */
export type NetKind = 'wifi' | 'cellular' | 'none' | 'unknown'

/** uni.getNetworkType 回调式封装（uni-app x）；ethernet 有线视为 wifi */
export function getNetKind(cb: (kind: NetKind) => void): void {
	uni.getNetworkType({
		success: (res) => {
			const t = res.networkType
			if (t == 'wifi' || t == 'ethernet') {
				cb('wifi')
			} else if (t == 'none' || t == '') {
				cb('none')
			} else {
				cb('cellular') // 4g/5g/3g/2g/unknown 一律按蜂窝保守处理
			}
		},
		fail: () => {
			cb('unknown')
		}
	})
}

/** 按请求模式 + 实际网络解析上传模式：
 *  - original：强制原图（WiFi 默认 / "立即上传原图"手动入口）
 *  - thumbnail：缩略图+元数据（Agent G upload_mode；G 未完成前按契约 mock=hold 暂缓）
 *  - auto：WiFi→original；蜂窝/none→hold（流量约束：蜂窝不自动传原图）
 *  - hold：一律暂缓
 * 返回 'original' | 'thumbnail' | 'hold' */
function resolveMode(mode: UploadMode, kind: NetKind): string {
	if (mode == 'original') {
		return 'original'
	}
	if (mode == 'thumbnail') {
		// 缩略图管线/upload_mode 未就绪（audit_B4_sync §1：全仓无缩略图生成）；
		// 契约先行：暂缓入 held，Agent G 落地后此处改 init(upload_mode='thumbnail')。
		console.log('[yishu] thumbnail 模式待 Agent G upload_mode 落地，暂缓上传')
		return 'hold'
	}
	if (mode == 'hold') {
		return 'hold'
	}
	// auto
	if (kind == 'wifi') {
		return 'original'
	}
	if (kind == 'cellular') {
		console.log('[yishu] 蜂窝网络：只传缩略图+元数据（mock=暂缓原图，等 WiFi/手动）')
		return 'hold'
	}
	console.log('[yishu] 网络不可用（' + kind + '）：暂缓原图')
	return 'hold'
}

// ---------- held / failed 持久队列（PhotoItem 行分隔存储） ----------

function entryOf(item: PhotoItem): string {
	const la = item.lat != null ? '' + item.lat : ''
	const ln = item.lng != null ? '' + item.lng : ''
	return item.path + '|' + item.takenAt + '|' + item.width + '|' + item.height + '|' + item.size + '|' + la + '|' + ln
}

function parseEntry(line: string): PhotoItem | null {
	const parts = line.split('|')
	if (parts.length < 7) {
		return null
	}
	const la = parts[5] == '' ? null : parseFloat(parts[5])
	const ln = parts[6] == '' ? null : parseFloat(parts[6])
	return new PhotoItem(0, parts[0], parseInt(parts[1]), parseInt(parts[2]), parseInt(parts[3]), la, ln, parseInt(parts[4]))
}

function readLines(key: string): Array<string> {
	const raw = uni.getStorageSync(key) as string
	if (raw == null || raw == '') {
		return []
	}
	const out: Array<string> = []
	const lines = raw.split('\n')
	for (let i = 0; i < lines.length; i++) {
		if (lines[i] != '') {
			out.push(lines[i])
		}
	}
	return out
}

function writeLines(key: string, lines: Array<string>): void {
	uni.setStorageSync(key, lines.join('\n'))
}

function addEntry(key: string, line: string): void {
	const lines = readLines(key)
	for (let i = 0; i < lines.length; i++) {
		if (lines[i].startsWith(entryPath(line) + '|')) {
			lines[i] = line
			writeLines(key, lines)
			return
		}
	}
	lines.push(line)
	writeLines(key, lines)
}

function removeEntry(key: string, path: string): void {
	const lines = readLines(key)
	const kept: Array<string> = []
	for (let i = 0; i < lines.length; i++) {
		if (!lines[i].startsWith(path + '|')) {
			kept.push(lines[i])
		}
	}
	writeLines(key, kept)
}

function entryPath(line: string): string {
	const idx = line.indexOf('|')
	return idx >= 0 ? line.substring(0, idx) : line
}

function parseAll(key: string): Array<PhotoItem> {
	const lines = readLines(key)
	const out: Array<PhotoItem> = []
	for (let i = 0; i < lines.length; i++) {
		const it = parseEntry(lines[i])
		if (it != null) {
			out.push(it)
		}
	}
	return out
}

export function heldCount(): number {
	return readLines(HELD_KEY).length
}

export function failedCount(): number {
	return readLines(FAILED_KEY).length
}

/** 待上传总数（held 蜂窝暂缓 + failed 失败可重试），供 UploadStatusBanner 展示 */
export function pendingPhotoCount(): number {
	return heldCount() + failedCount()
}

export function heldPhotos(): Array<PhotoItem> {
	return parseAll(HELD_KEY)
}

export function failedPhotos(): Array<PhotoItem> {
	return parseAll(FAILED_KEY)
}

function addHeld(item: PhotoItem): void {
	addEntry(HELD_KEY, entryOf(item))
}

function removeHeld(path: string): void {
	removeEntry(HELD_KEY, path)
}

function addFailed(item: PhotoItem): void {
	addEntry(FAILED_KEY, entryOf(item))
}

function removeFailed(path: string): void {
	removeEntry(FAILED_KEY, path)
}

// ---------- 协议请求 ----------

function authHeader(): UTSJSONObject {
	const header: UTSJSONObject = {}
	const token = getToken()
	if (token != '') {
		header.set('Authorization', 'Bearer ' + token)
	}
	return header
}

/** URL 编码最小集（路径/file_name/upload_id/meta 均为 ASCII 可控字符；中文文件名后续补全） */
function urlEncode(s: string): string {
	let out = ''
	for (let i = 0; i < s.length; i++) {
		const c = s.charAt(i)
		if (c == '&') {
			out += '%26'
		} else if (c == '=') {
			out += '%3D'
		} else if (c == '%') {
			out += '%25'
		} else if (c == '+') {
			out += '%2B'
		} else if (c == ' ') {
			out += '%20'
		} else {
			out += c
		}
	}
	return out
}

/** 表单响应：status（0=网络失败） + raw（响应 JSON 字符串） */
class HttpResp {
	status: number
	raw: string

	constructor(status: number, raw: string) {
		this.status = status
		this.raw = raw
	}
}

/** POST 表单（application/x-www-form-urlencoded；后端 Form 字段）→ HttpResp */
function formPost(path: string, body: string): Promise<HttpResp> {
	return new Promise<HttpResp>((resolve) => {
		const header: UTSJSONObject = {
			'Content-Type': 'application/x-www-form-urlencoded'
		}
		const token = getToken()
		if (token != '') {
			header.set('Authorization', 'Bearer ' + token)
		}
		uni.request({
			url: getBaseUrl() + path,
			method: 'POST',
			data: body,
			header: header,
			timeout: 30000,
			success: (res) => {
				if (res.statusCode === 200) {
					resolve(new HttpResp(200, JSON.stringify(res.data)))
				} else {
					console.error('[yishu] upload form ' + res.statusCode + ' ' + path)
					resolve(new HttpResp(res.statusCode, JSON.stringify(res.data)))
				}
			},
			fail: () => {
				console.error('[yishu] upload form NETWORK ' + path)
				resolve(new HttpResp(0, ''))
			}
		})
	})
}

/** 取响应 JSON 字符串里某字段值（"key":"value" 或 "key":value；value 不含引号时原样返回） */
function fieldOf(raw: string, key: string): string {
	const needle = '"' + key + '":'
	const idx = raw.indexOf(needle)
	if (idx < 0) {
		return ''
	}
	const rest = raw.substring(idx + needle.length)
	if (rest.startsWith('"')) {
		return rest.substring(1).split('"')[0]
	}
	const end = rest.indexOf(',')
	return end >= 0 ? rest.substring(0, end) : rest
}

/** 建任务（client_upload_id=path 幂等；upload_mode 透传 Agent G 契约参数）；
 *  成功返回 upload_id，HTTP 错误 reject UploadHttpError（4xx 停条） */
function initUpload(item: PhotoItem, fileSize: number, uploadMode: string): Promise<string> {
	return new Promise<string>((resolve, reject) => {
		const slashIdx = item.path.lastIndexOf('/')
		const fileName = slashIdx >= 0 ? item.path.substring(slashIdx + 1) : item.path
		let body = 'client_upload_id=' + urlEncode(item.path) +
			'&file_name=' + urlEncode(fileName) +
			'&file_size=' + fileSize +
			'&chunk_size=' + fileSize +
			'&upload_mode=' + uploadMode
		formPost('/api/v1/upload/init', body).then((resp: HttpResp) => {
			if (resp.status === 200) {
				const uploadId = fieldOf(resp.raw, 'upload_id')
				if (uploadId == '') {
					console.error('[yishu] init no upload_id: ' + resp.raw.substring(0, 120))
					reject(new Error('init 无 upload_id'))
					return
				}
				resolve(uploadId)
				return
			}
			reject(new UploadHttpError(resp.status, 'init HTTP ' + resp.status))
		})
	})
}

/** 查断点状态：缺失分片列表（缺 0 或空数组=已完成）；非 200 视为需补传（交给 chunk 暴露真实错误） */
function statusMissing(uploadId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		uni.request({
			url: getBaseUrl() + '/api/v1/upload/status?upload_id=' + uploadId,
			method: 'GET',
			header: authHeader(),
			timeout: 15000,
			success: (res) => {
				if (res.statusCode === 200) {
					const raw = JSON.stringify(res.data)
					const missing = fieldOf(raw, 'missing_chunks')
					resolve(missing != '[]' && missing != '')
				} else {
					resolve(true)
				}
			},
			fail: () => resolve(true)
		})
	})
}

/** 传单片（POST multipart；后端幂等 + 大小校验）；4xx reject UploadHttpError 停条 */
function putChunk(uploadId: string, item: PhotoItem): Promise<boolean> {
	return new Promise<boolean>((resolve, reject) => {
		const form: UTSJSONObject = {
			upload_id: uploadId,
			chunk_index: '0'
		}
		uni.uploadFile({
			url: getBaseUrl() + '/api/v1/upload/chunk',
			filePath: item.path,
			name: 'file',
			formData: form,
			header: authHeader(),
			timeout: 60000,
			success: (res) => {
				if (res.statusCode === 200) {
					resolve(true)
				} else {
					console.error('[yishu] chunk HTTP ' + res.statusCode)
					reject(new UploadHttpError(res.statusCode, 'chunk HTTP ' + res.statusCode))
				}
			},
			fail: () => {
				console.error('[yishu] chunk NETWORK')
				reject(new Error('chunk 网络失败'))
			}
		})
	})
}

/** 完成 + 建内容记录（meta 与 /contents/upload 对齐，含 GPS）→ content_id；4xx 停条 */
function completeUpload(uploadId: string, item: PhotoItem): Promise<string> {
	return new Promise<string>((resolve, reject) => {
		const meta: UTSJSONObject = {
			taken_at: isoString(item.takenAt),
			source: 'app',
			extra: {
				width: item.width,
				height: item.height
			}
		}
		if (item.lat != null && item.lng != null) {
			meta.set('gps_lat', '' + item.lat)
			meta.set('gps_lng', '' + item.lng)
		}
		const body = 'upload_id=' + urlEncode(uploadId) + '&meta=' + urlEncode(JSON.stringify(meta))
		formPost('/api/v1/upload/complete', body).then((resp: HttpResp) => {
			if (resp.status === 200) {
				const cid = fieldOf(resp.raw, 'content_id')
				if (cid == '') {
					console.error('[yishu] complete no content_id: ' + resp.raw.substring(0, 120))
					reject(new Error('complete 无 content_id'))
					return
				}
				resolve(cid)
				return
			}
			reject(new UploadHttpError(resp.status, 'complete HTTP ' + resp.status))
		})
	})
}

// ---------- 单张上传（含断点续传 + 4xx 区分） ----------

function uploadOne(item: PhotoItem, uploadMode: string): Promise<string> {
	return new Promise<string>((resolve, reject) => {
		// 0. 文件大小（init 必填）——2026-08-25 真机修复：不再用 getFileSystemManager()
		// .getFileInfo（uni-app x 沙箱读不了 MediaStore 绝对路径，必失败），改用
		// MediaStore SIZE 列（emitIncremental 已注入 PhotoItem.size）。
		const fileSize = item.size
		if (fileSize <= 0) {
			reject(new Error('文件大小为 0'))
			return
		}
		// 1. 断点：已有 upload_id 直接复用，否则 init
		const existing = findPending(item.path)
		if (existing != '') {
			statusMissing(existing).then((missing: boolean) => {
				if (!missing) {
					completeUpload(existing, item).then((cid: string) => {
						clearPending(item.path)
						resolve(cid)
					}, (e: Error) => {
						reject(e)
					})
				} else {
					putChunk(existing, item).then((ok: boolean) => {
						finishUpload(existing, ok, item, resolve, reject)
					}, (e: Error) => {
						reject(e)
					})
				}
			})
			return
		}
		initUpload(item, fileSize, uploadMode).then((uploadId: string) => {
			savePending(item.path, uploadId)
			putChunk(uploadId, item).then((ok: boolean) => {
				finishUpload(uploadId, ok, item, resolve, reject)
			}, (e: Error) => {
				reject(e)
			})
		}, (e: Error) => {
			reject(e)
		})
	})
}

function finishUpload(
	uploadId: string,
	chunkOk: boolean,
	item: PhotoItem,
	resolve: (cid: string) => void,
	reject: (e: Error) => void
): void {
	if (!chunkOk) {
		reject(new Error('分片上传失败'))
		return
	}
	completeUpload(uploadId, item).then((cid: string) => {
		clearPending(item.path)
		resolve(cid)
	}, (e: Error) => {
		reject(e)
	})
}

/** 指数退避重试（2s→4s→8s→8s→8s，5 次上限）；4xx 立即停该条 */
function retryBackoff(item: PhotoItem, uploadMode: string, attempt: number, ok: (cid: string) => void, fail: () => void): void {
	uploadOne(item, uploadMode).then((cid: string) => {
		ok(cid)
	}, (e: Error) => {
		const msg = e != null && e.message != null ? e.message : 'unknown'
		console.error('[yishu] upload ERR attempt=' + attempt + ' path=' + item.path + ' msg=' + msg)
		if (is4xx(e)) {
			console.error('[yishu] 4xx 停该条（' + item.path + '）')
			fail()
			return
		}
		if (attempt >= BACKOFF_MS.length) {
			console.error('[yishu] 退避耗尽（' + item.path + '，upload_id 已留存待续传）')
			fail()
			return
		}
		setTimeout(() => {
			retryBackoff(item, uploadMode, attempt + 1, ok, fail)
		}, BACKOFF_MS[attempt])
	})
}

function uploadWithRetry(item: PhotoItem, uploadMode: string): Promise<string | null> {
	return new Promise<string | null>((resolve) => {
		retryBackoff(item, uploadMode, 0, (cid: string) => resolve(cid), () => resolve(null))
	})
}

/** 并发池（游标式分配，天然实现 ≤3 并发；批量失败 ≥10 暂停剩余） */
class UploadPool {
	items: Array<PhotoItem>
	onProgress: (p: UploadProgress) => void
	resolve: (out: Array<UploadedPhoto>) => void
	uploadMode: string = 'original'
	index: number = 0
	done: number = 0
	failed: number = 0
	consecutive: number = 0
	total: number
	results: Array<UploadedPhoto> = []

	constructor(items: Array<PhotoItem>, onProgress: (p: UploadProgress) => void, resolve: (out: Array<UploadedPhoto>) => void) {
		this.items = items
		this.onProgress = onProgress
		this.resolve = resolve
		this.total = items.length
	}

	start(uploadMode: string): void {
		this.uploadMode = uploadMode
		const slots = this.total < MAX_CONCURRENCY ? this.total : MAX_CONCURRENCY
		for (let s = 0; s < slots; s++) {
			this.next()
		}
	}

	/** 暂停：剩余项入 held 队列 + 提前返回已成功部分（横幅"继续上传"恢复） */
	holdRemaining(): void {
		for (let i = this.index; i < this.total; i++) {
			addHeld(this.items[i])
		}
		this.resolve(this.results)
	}

	next(): void {
		if (isSyncPaused()) {
			this.holdRemaining()
			return
		}
		const i = this.index
		this.index++
		if (i >= this.total) {
			return
		}
		const item = this.items[i]
		uploadWithRetry(item, this.uploadMode).then((cid: string | null) => {
			if (cid != null) {
				this.done++
				this.consecutive = 0
				this.results.push(new UploadedPhoto(item, cid))
				resetConsecutiveFailures()
			} else {
				this.failed++
				this.consecutive++
				addFailed(item)
				registerConsecutiveFailure()
				if (this.consecutive >= MAX_BATCH_FAILURES) {
					console.error('[yishu] 连续失败 ' + MAX_BATCH_FAILURES + ' 条，暂停剩余（横幅可继续）')
					pauseSync('网络异常，已暂停同步')
					this.holdRemaining()
					return
				}
			}
			const p = new UploadProgress(this.done, this.total, this.failed)
			this.onProgress(p)
			if (this.done + this.failed === this.total) {
				this.resolve(this.results)
			} else {
				this.next()
			}
		})
	}
}

/** 整批失败（登录失败/后端不可达）：全部入 failed 队列 + 连续失败计数（≥N 触发暂停横幅） */
function failWholeBatch(items: Array<PhotoItem>, onProgress: (p: UploadProgress) => void, resolve: (out: Array<UploadedPhoto>) => void): void {
	for (let i = 0; i < items.length; i++) {
		addFailed(items[i])
	}
	for (let i = 0; i < items.length; i++) {
		registerConsecutiveFailure()
	}
	const fail = new UploadProgress(0, items.length, items.length)
	onProgress(fail)
	resolve([])
}

/** 批量上传主入口（并发 ≤3）；成功项返回（上传前确保已登录）。
 *  opts.mode：auto（WiFi 原图/蜂窝暂缓）/ original（强制原图）/ thumbnail / hold */
export function uploadBatch(items: PhotoItem[], onProgress: (p: UploadProgress) => void, opts: UploadOptions | null = null): Promise<Array<UploadedPhoto>> {
	return new Promise<Array<UploadedPhoto>>((resolve) => {
		if (items.length === 0) {
			const empty = new UploadProgress(0, 0, 0)
			onProgress(empty)
			resolve([])
			return
		}
		const mode: UploadMode = opts != null && opts.mode != null ? opts.mode : 'auto'
		getNetKind((kind: NetKind) => {
			const effective = resolveMode(mode, kind)
			if (effective == 'hold') {
				// 蜂窝/离线：暂缓入 held（流量约束），横幅展示"待 WiFi 上传/立即上传原图"
				for (let i = 0; i < items.length; i++) {
					addHeld(items[i])
				}
				const held = new UploadProgress(0, items.length, 0)
				onProgress(held)
				console.log('[yishu] 蜂窝/离线暂缓 ' + items.length + ' 张原图（held=' + heldCount() + '）')
				resolve([])
				return
			}
			ensureLogin().then((ok: boolean) => {
				if (!ok) {
					console.error('[yishu] 上传前登录失败（后端不可达），整批入失败队列')
					failWholeBatch(items, onProgress, resolve)
					return
				}
				const pool = new UploadPool(items, onProgress, resolve)
				pool.start(effective)
			})
		})
	})
}

/** "立即上传原图"手动入口（B4 §5：无视网络类型强制原图） */
export function uploadNowOriginal(items: PhotoItem[], onProgress: (p: UploadProgress) => void): Promise<Array<UploadedPhoto>> {
	return uploadBatch(items, onProgress, new UploadOptions('original'))
}

/** 一键继续：解除暂停 + 上传 held/failed 全部待处理照片（原图模式） */
export function continuePendingUploads(onProgress: (p: UploadProgress) => void): Promise<Array<UploadedPhoto>> {
	return new Promise<Array<UploadedPhoto>>((resolve) => {
		resumeSync()
		const held = heldPhotos()
		const failed = failedPhotos()
		const items: Array<PhotoItem> = []
		for (let i = 0; i < held.length; i++) {
			items.push(held[i])
		}
		for (let i = 0; i < failed.length; i++) {
			items.push(failed[i])
		}
		if (items.length === 0) {
			const empty = new UploadProgress(0, 0, 0)
			onProgress(empty)
			resolve([])
			return
		}
		ensureLogin().then((ok: boolean) => {
			if (!ok) {
				console.error('[yishu] 继续上传：登录失败（后端不可达）')
				failWholeBatch(items, onProgress, resolve)
				return
			}
			const pool = new UploadPool(items, onProgress, (out: Array<UploadedPhoto>) => {
				// 成功后从 held/failed 队列移除（无论池是否因暂停提前返回，只清已成功项）
				for (let i = 0; i < out.length; i++) {
					removeHeld(out[i].item.path)
					removeFailed(out[i].item.path)
				}
				resolve(out)
			})
			pool.start('original')
		})
	})
}

/** WiFi 恢复时自动补传暂缓原图（B4 §5 流量约束闭环：蜂窝暂缓 → 到 WiFi 自动续传）；
 *  由 sync_client.onNetworkRestored 在网络恢复时调用，本文件模块加载时注册（无循环依赖）。 */
function maybeUploadHeldOnWifi(): void {
	if (heldCount() === 0 && failedCount() === 0) {
		return
	}
	if (isSyncPaused()) {
		return
	}
	getNetKind((kind: NetKind) => {
		if (kind == 'wifi') {
			console.log('[yishu] WiFi 恢复，自动补传暂缓原图（held=' + heldCount() + ' failed=' + failedCount() + '）')
			continuePendingUploads(() => {})
		}
	})
}

// 模块加载时注册网络恢复钩子（sync_client 触发；App 启动经 index/profile 引入 uploader 即生效）
onNetworkRestored((): void => {
	maybeUploadHeldOnWifi()
})
