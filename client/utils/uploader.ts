/**
 * 照片批量上传（B-UP-1/2 + S-AG-3）：逐张 multipart POST /api/v1/contents/upload
 *
 *  - 并发 ≤3（MAX_CONCURRENCY）
 *  - 单张失败重试 2 次（MAX_RETRY），仍失败计入 failed 不阻塞整批
 *  - 进度回调 onProgress(done, total, failed)
 *  - 返回成功项列表（含 content_id —— 端侧聚合/事件上云依赖云侧 ID）
 */
import { getBaseUrl } from './config'
import { getToken, ensureLogin } from './auth'
import { PhotoItem } from '@/uni_modules/yishu-photo-watch/utssdk/interface.uts'

export const MAX_CONCURRENCY: number = 3
export const MAX_RETRY: number = 2

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

function uploadOne(item: PhotoItem, attempt: number): Promise<string> {
	return new Promise<string>((resolve, reject) => {
		const meta: UTSJSONObject = {
			taken_at: isoString(item.takenAt),
			source: 'app',
			extra: {
				width: item.width,
				height: item.height
			}
		}
		const header: UTSJSONObject = {}
		const token = getToken()
		if (token != '') {
			header.set('Authorization', 'Bearer ' + token)
		}
		uni.uploadFile({
			url: getBaseUrl() + '/api/v1/contents/upload',
			filePath: item.path,
			name: 'file',
			formData: {
				meta: JSON.stringify(meta)
			},
			header: header,
			timeout: 60000,
			success: (res) => {
				if (res.statusCode === 200) {
					// uni.uploadFile 的 res.data 是字符串（App 端）；JS 引擎无 UTSJSONObject.parse，
					// 用正则提取 content_id（响应 data.id 为 UUID）——2026-08-24 真机实测修复
					try {
						const raw = res.data as string
						// split 提取（UTS 检查下 RegExpExecArray 索引类型不可靠；纯字符串操作最稳）
						const parts = raw.split('"id":"')
						const cid = parts.length > 1 ? parts[1].split('"')[0] : ''
						if (cid != '') {
							resolve(cid)
						} else {
							console.error('[yishu] upload resp no id: ' + raw.substring(0, 120))
							reject(new Error('上传响应缺少 content_id'))
						}
					} catch (e) {
						reject(new Error('上传响应解析失败'))
					}
				} else {
					console.error('[yishu] upload HTTP ' + res.statusCode + ' url=' + getBaseUrl() + ' p=' + item.path)
					reject(new Error('上传失败 HTTP ' + res.statusCode))
				}
			},
			fail: (err) => {
				console.error('[yishu] upload FAIL url=' + getBaseUrl() + ' err=' + JSON.stringify(err))
				reject(new Error('上传网络错误 ' + JSON.stringify(err)))
			}
		})
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
			console.error('[yishu] 上传失败（重试耗尽）')
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

