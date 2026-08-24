/**
 * 端侧 L1 事件上云（S-SY-1 客户端侧 · B3-6）
 *
 * POST /api/v1/events/sync：批量提交端侧聚合的 L1 事件。
 *  - client_event_id 幂等：重试/重发只落一次（服务端去重）
 *  - 指数退避重试：2s→4s→8s→8s→8s（5 次上限），全失败返回 false（调用方提示/记录）
 *  - 4xx 停该批（参数/越权错误重试无意义）
 */
import { getBaseUrl } from './config'
import { getToken, ensureLogin } from './auth'

export class SyncOutcome {
	accepted: number
	duplicates: number
	rejected: number

	constructor(accepted: number, duplicates: number, rejected: number) {
		this.accepted = accepted
		this.duplicates = duplicates
		this.rejected = rejected
	}
}

/** 单次提交（无重试）；返回 null = 网络/5xx 可重试，Outcome = 服务端已处理 */
function postOnce(events: Array<UTSJSONObject>, token: string, resolve: (r: SyncOutcome | null) => void): void {
	uni.request({
		url: getBaseUrl() + '/api/v1/events/sync',
		method: 'POST',
		data: {
			device_id: 'nova11',
			events: events
		},
		header: {
			'Content-Type': 'application/json',
			'Authorization': 'Bearer ' + token
		},
		timeout: 15000,
		success: (res) => {
			if (res.statusCode === 200) {
				const body = res.data as UTSJSONObject
				const d: UTSJSONObject | null = body.getJSON('data')
				let accepted = 0
				let duplicates = 0
				let rejected = 0
				if (d != null) {
					const acc = d.getArray('accepted')
					const dup = d.getArray('duplicates')
					const rej = d.getArray('rejected')
					accepted = acc != null ? acc.length : 0
					duplicates = dup != null ? dup.length : 0
					rejected = rej != null ? rej.length : 0
				}
				resolve(new SyncOutcome(accepted, duplicates, rejected))
			} else if (res.statusCode >= 400 && res.statusCode < 500) {
				console.error('[yishu] 事件同步 4xx=' + res.statusCode + ' ' + JSON.stringify(res.data))
				resolve(new SyncOutcome(0, 0, events.length)) // 4xx 不可重试
			} else {
				resolve(null) // 5xx/其他 → 重试
			}
		},
		fail: () => {
			resolve(null) // 网络错误 → 重试
		}
	})
}

const BACKOFF_MS: number[] = [2000, 4000, 8000, 8000, 8000]

/** 指数退避重试（模块级递归，规避自引用限制） */
function retryLoop(events: Array<UTSJSONObject>, token: string, attempt: number, resolve: (r: SyncOutcome | null) => void): void {
	postOnce(events, token, (r: SyncOutcome | null) => {
		if (r != null || attempt >= BACKOFF_MS.length) {
			resolve(r)
			return
		}
		setTimeout(() => {
			retryLoop(events, token, attempt + 1, resolve)
		}, BACKOFF_MS[attempt])
	})
}

/** 提交端侧 L1 事件（带退避重试）；全失败返回 null */
export function syncClientEvents(events: Array<UTSJSONObject>): Promise<SyncOutcome | null> {
	return new Promise<SyncOutcome | null>((resolve) => {
		if (events.length === 0) {
			resolve(new SyncOutcome(0, 0, 0))
			return
		}
		ensureLogin().then((ok: boolean) => {
			if (!ok) {
				console.error('[yishu] 事件同步前登录失败')
				resolve(null)
				return
			}
			retryLoop(events, getToken(), 0, resolve)
		})
	})
}

