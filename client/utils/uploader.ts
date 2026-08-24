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

export interface UploadProgress {
	done: number
	total: number
	failed: number
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
		const meta: UTSJSON = {
			taken_at: isoString(item.takenAt),
			source: 'app',
			extra: {
				width: item.width,
				height: item.height
			}
		}
		const header: UTSJSON = {}
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

function uploadWithRetry(item: PhotoItem): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		let attempt = 0
		const tryOnce = (): void => {
			attempt++
			uploadOne(item, attempt).then((ok: boolean) => {
				resolve(ok)
			}).catch((err: Error | null) => {
				if (attempt <= MAX_RETRY) {
					tryOnce()
				} else {
					console.error('[yishu] 上传失败（重试耗尽）: ' + (err != null ? err.message : ''))
					resolve(false)
				}
			})
		}
		tryOnce()
	})
}

/** 并发 ≤3 的批量上传；返回最终进度 */
export function uploadBatch(items: PhotoItem[], onProgress: (p: UploadProgress) => void): Promise<UploadProgress> {
	return new Promise<UploadProgress>((resolve) => {
		if (items.length === 0) {
			const empty: UploadProgress = { done: 0, total: 0, failed: 0 }
			onProgress(empty)
			resolve(empty)
			return
		}
		let index = 0
		let done = 0
		let failed = 0
		const total = items.length

		const worker = (): void => {
			// 从队列取下一张（游标式分配，天然实现 ≤3 并发）
			const i = index
			index++
			if (i >= total) {
				return
			}
			uploadWithRetry(items[i]).then((ok: boolean) => {
				if (ok) {
					done++
				} else {
					failed++
				}
				const p: UploadProgress = { done: done, total: total, failed: failed }
				onProgress(p)
				if (done + failed === total) {
					resolve(p)
				} else {
					worker()
				}
			})
		}

		const slots = total < MAX_CONCURRENCY ? total : MAX_CONCURRENCY
		for (let s = 0; s < slots; s++) {
			worker()
		}
	})
}
