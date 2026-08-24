/**
 * 照片批量上传 v2（S-ST-1 分片协议 + 断点续传 · 2026-08-25）
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
 *  - 并发 ≤3（MAX_CONCURRENCY），单张失败重试 2 次，仍失败计入 failed 不阻塞整批
 *  - 进度回调 onProgress(done, total, failed)
 *  - 返回成功项列表（含 content_id —— 端侧聚合/事件上云依赖云侧 ID）
 */
import { getBaseUrl } from './config'
import { getToken, ensureLogin } from './auth'
import { PhotoItem } from '@/uni_modules/yishu-photo-watch/utssdk/interface.uts'

export const MAX_CONCURRENCY: number = 3
export const MAX_RETRY: number = 2

/** 断点续传：path|uploadId 逐行存 uni storage（UTS 无可靠 JSON.parse，用分隔串） */
const PENDING_KEY: string = 'yishu_pending_uploads'

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

/** POST 表单（application/x-www-form-urlencoded；后端 Form 字段）→ 响应 JSON 字符串 */
function formPost(path: string, body: string): Promise<string | null> {
	return new Promise<string | null>((resolve) => {
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
					resolve(JSON.stringify(res.data))
				} else {
					console.error('[yishu] upload form ' + res.statusCode + ' ' + path)
					resolve(null)
				}
			},
			fail: () => {
				console.error('[yishu] upload form NETWORK ' + path)
				resolve(null)
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

/** 建任务（client_upload_id=path 幂等）；返回 upload_id；失败空串 */
function initUpload(item: PhotoItem, fileSize: number): Promise<string> {
	return new Promise<string>((resolve) => {
		// 文件名取路径最后一段（后端 _final_key 拼接用）
		const slashIdx = item.path.lastIndexOf('/')
		const fileName = slashIdx >= 0 ? item.path.substring(slashIdx + 1) : item.path
		const body = 'client_upload_id=' + urlEncode(item.path) +
			'&file_name=' + urlEncode(fileName) +
			'&file_size=' + fileSize +
			'&chunk_size=' + fileSize
		formPost('/api/v1/upload/init', body).then((raw: string | null) => {
			if (raw == null) {
				resolve('')
				return
			}
			const uploadId = fieldOf(raw, 'upload_id')
			if (uploadId == '') {
				console.error('[yishu] init no upload_id: ' + raw.substring(0, 120))
				resolve('')
				return
			}
			resolve(uploadId)
		})
	})
}

/** 查断点状态：缺失分片列表（缺 0 或空数组=已完成） */
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
					// "missing_chunks":[] 或 [0,...] —— 空数组表示分片已齐
					resolve(missing != '[]' && missing != '')
				} else {
					resolve(true)
				}
			},
			fail: () => resolve(true)
		})
	})
}

/** 传单片（PUT multipart；后端幂等 + 大小校验） */
function putChunk(uploadId: string, item: PhotoItem): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
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
					resolve(false)
				}
			},
			fail: () => {
				console.error('[yishu] chunk NETWORK')
				resolve(false)
			}
		})
	})
}

/** 完成 + 建内容记录（meta 与 /contents/upload 对齐，含 GPS）→ content_id */
function completeUpload(uploadId: string, item: PhotoItem): Promise<string> {
	return new Promise<string>((resolve) => {
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
		formPost('/api/v1/upload/complete', body).then((raw: string | null) => {
			if (raw == null) {
				resolve('')
				return
			}
			const cid = fieldOf(raw, 'content_id')
			if (cid == '') {
				console.error('[yishu] complete no content_id: ' + raw.substring(0, 120))
				resolve('')
				return
			}
			resolve(cid)
		})
	})
}

// ---------- 单张上传（含断点续传） ----------

function uploadOne(item: PhotoItem, attempt: number): Promise<string> {
	return new Promise<string>((resolve, reject) => {
		// 0. 文件大小（init 必填）——uni.getFileInfo 在 uni-app x 不可用，用 FileSystemManager
		uni.getFileSystemManager().getFileInfo({
			filePath: item.path,
			success: (info) => {
				const fileSize = info.size as number
				if (fileSize <= 0) {
					reject(new Error('文件大小为 0'))
					return
				}
				// 1. 断点：已有 upload_id 直接复用，否则 init
				const existing = findPending(item.path)
				if (existing != '') {
					statusMissing(existing).then((missing: boolean) => {
						if (!missing) {
							// 分片已齐（上次 complete 前中断）→ 直接 complete
							completeUpload(existing, item).then((cid: string) => {
								if (cid != '') {
									clearPending(item.path)
									resolve(cid)
								} else {
									reject(new Error('complete 失败'))
								}
							})
						} else {
							putChunk(existing, item).then((ok: boolean) => {
								finishUpload(existing, ok, item, resolve, reject)
							})
						}
					})
					return
				}
				initUpload(item, fileSize).then((uploadId: string) => {
					if (uploadId == '') {
						reject(new Error('init 失败'))
						return
					}
					savePending(item.path, uploadId)
					putChunk(uploadId, item).then((ok: boolean) => {
						finishUpload(uploadId, ok, item, resolve, reject)
					})
				})
			},
			fail: () => {
				reject(new Error('读取文件信息失败'))
			}
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
		// 保留 pending（断点续传），交给重试/下批
		reject(new Error('分片上传失败'))
		return
	}
	completeUpload(uploadId, item).then((cid: string) => {
		if (cid != '') {
			clearPending(item.path)
			resolve(cid)
		} else {
			reject(new Error('complete 失败'))
		}
	})
}

/** 单张重试（模块级递归，避免 UTS 自引用箭头函数限制） */
function retryOnce(item: PhotoItem, attempt: number, resolve: (cid: string) => void, fail: () => void): void {
	uploadOne(item, attempt).then((cid: string) => {
		resolve(cid)
	}, (e: Error) => {
		console.error('[yishu] upload ERR attempt=' + attempt + ' path=' + item.path + ' msg=' + (e != null && e.message != null ? e.message : 'unknown'))
		if (attempt <= MAX_RETRY) {
			retryOnce(item, attempt + 1, resolve, fail)
		} else {
			console.error('[yishu] 上传失败（重试耗尽，upload_id 已留存待续传）')
			fail()
		}
	})
}

function uploadWithRetry(item: PhotoItem): Promise<string | null> {
	return new Promise<string | null>((resolve) => {
		retryOnce(item, 1, (cid: string) => resolve(cid), () => resolve(null))
	})
}

/** 并发池（游标式分配，天然实现 ≤3 并发） */
class UploadPool {
	items: Array<PhotoItem>
	onProgress: (p: UploadProgress) => void
	resolve: (out: Array<UploadedPhoto>) => void
	index: number = 0
	done: number = 0
	failed: number = 0
	total: number
	results: Array<UploadedPhoto> = []

	constructor(items: Array<PhotoItem>, onProgress: (p: UploadProgress) => void, resolve: (out: Array<UploadedPhoto>) => void) {
		this.items = items
		this.onProgress = onProgress
		this.resolve = resolve
		this.total = items.length
	}

	start(): void {
		const slots = this.total < MAX_CONCURRENCY ? this.total : MAX_CONCURRENCY
		for (let s = 0; s < slots; s++) {
			this.next()
		}
	}

	next(): void {
		const i = this.index
		this.index++
		if (i >= this.total) {
			return
		}
		uploadWithRetry(this.items[i]).then((cid: string | null) => {
			if (cid != null) {
				this.done++
				this.results.push(new UploadedPhoto(this.items[i], cid))
			} else {
				this.failed++
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

/** 并发 ≤3 的批量上传；返回成功项列表（上传前确保已登录，token 失效自动重登） */
export function uploadBatch(items: PhotoItem[], onProgress: (p: UploadProgress) => void): Promise<Array<UploadedPhoto>> {
	return new Promise<Array<UploadedPhoto>>((resolve) => {
		if (items.length === 0) {
			const empty = new UploadProgress(0, 0, 0)
			onProgress(empty)
			resolve([])
			return
		}
		ensureLogin().then((ok: boolean) => {
			if (!ok) {
				console.error('[yishu] 上传前登录失败')
				const fail = new UploadProgress(0, items.length, items.length)
				onProgress(fail)
				resolve([])
				return
			}
			const pool = new UploadPool(items, onProgress, resolve)
			pool.start()
		})
	})
}
