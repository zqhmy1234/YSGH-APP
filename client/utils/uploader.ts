/**
 * 照片批量上传（B-UP-1/2）：逐张 multipart POST /api/v1/contents/upload
 *
 *  - 并发 ≤3（MAX_CONCURRENCY）
 *  - 单张失败重试 2 次（MAX_RETRY），仍失败计入 failed 不阻塞整批
 *  - 进度回调 onProgress(done, total, failed)
 *  - 全部完成 → 调用方拉 timeline 刷新（B-UP-2 在页面层接线）
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

/** epoch ms → ISO8601 本地时间（UTS Date 无 toISOString，手动拼接） */
export function isoString(ms: number): string {
	const d = new Date(ms)
	const pad = (n: number): string => (n < 10 ? '0' + n : '' + n)
	return (
		d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
		'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) + '+08:00'
	)
}

function uploadOne(item: PhotoItem, attempt: number): Promise<boolean> {
	return new Promise<boolean>((resolve, reject) => {
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
					resolve(true)
				} else {
					reject(new Error('上传失败 HTTP ' + res.statusCode))
				}
			},
			fail: (err) => {
				reject(new Error('上传网络错误 ' + JSON.stringify(err)))
			}
		})
	})
}

/** 单张重试（模块级递归，避免 UTS 自引用箭头函数限制） */
function retryOnce(item: PhotoItem, attempt: number, resolve: (ok: boolean) => void): void {
	uploadOne(item, attempt).then((ok: boolean) => {
		resolve(ok)
	}, () => {
		if (attempt <= MAX_RETRY) {
			retryOnce(item, attempt + 1, resolve)
		} else {
			console.error('[yishu] 上传失败（重试耗尽）')
			resolve(false)
		}
	})
}

function uploadWithRetry(item: PhotoItem): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		retryOnce(item, 1, resolve)
	})
}

/** 并发池（游标式分配，天然实现 ≤3 并发） */
class UploadPool {
	items: Array<PhotoItem>
	onProgress: (p: UploadProgress) => void
	resolve: (p: UploadProgress) => void
	index: number = 0
	done: number = 0
	failed: number = 0
	total: number

	constructor(items: Array<PhotoItem>, onProgress: (p: UploadProgress) => void, resolve: (p: UploadProgress) => void) {
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
		uploadWithRetry(this.items[i]).then((ok: boolean) => {
			if (ok) {
				this.done++
			} else {
				this.failed++
			}
			const p = new UploadProgress(this.done, this.total, this.failed)
			this.onProgress(p)
			if (this.done + this.failed === this.total) {
				this.resolve(p)
			} else {
				this.next()
			}
		})
	}
}

/** 并发 ≤3 的批量上传；返回最终进度（上传前确保已登录，token 失效自动重登） */
export function uploadBatch(items: PhotoItem[], onProgress: (p: UploadProgress) => void): Promise<UploadProgress> {
	return new Promise<UploadProgress>((resolve) => {
		if (items.length === 0) {
			const empty = new UploadProgress(0, 0, 0)
			onProgress(empty)
			resolve(empty)
			return
		}
		ensureLogin().then((ok: boolean) => {
			if (!ok) {
				console.error('[yishu] 上传前登录失败')
				const fail = new UploadProgress(0, items.length, items.length)
				onProgress(fail)
				resolve(fail)
				return
			}
			const pool = new UploadPool(items, onProgress, resolve)
			pool.start()
		})
	})
}
