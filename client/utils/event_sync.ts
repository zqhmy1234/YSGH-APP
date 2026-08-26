/**
 * 端侧 L1 事件上云（S-SY-1 客户端侧 · B3-6）
 *
 * POST /api/v1/events/sync：批量提交端侧聚合的 L1 事件。
 *  - client_event_id 幂等：重试/重发只落一次（服务端去重）
 *  - 指数退避重试：2s→4s→8s→8s→8s（5 次上限），全失败返回 false（调用方提示/记录）
 *  - 4xx 停该批（参数/越权错误重试无意义）
 */
import { getBaseUrl } from './config'
import { getToken, ensureLogin, DEVICE_ID } from './auth'
import { retryAsync } from './retry'

export class SyncOutcome {
	accepted: number
	duplicates: number
	rejected: number
	/** accepted 明细 [{client_event_id, event_id, photo_count}]（2026-08-26 Wave2 E：
	 *  照片条/封面需要 服务端 event_id → 成员照片 映射，靠 accepted 明细建立） */
	acceptedEvents: Array<UTSJSONObject>

	constructor(accepted: number, duplicates: number, rejected: number, acceptedEvents: Array<UTSJSONObject>) {
		this.accepted = accepted
		this.duplicates = duplicates
		this.rejected = rejected
		this.acceptedEvents = acceptedEvents
	}
}

/** 单次提交（无重试）；返回 null = 网络/5xx 可重试，Outcome = 服务端已处理 */
function postOnce(events: Array<UTSJSONObject>, token: string, resolve: (r: SyncOutcome | null) => void): void {
	uni.request({
		url: getBaseUrl() + '/api/v1/events/sync',
		method: 'POST',
		data: {
			device_id: DEVICE_ID,
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
				const acceptedEvents: Array<UTSJSONObject> = []
				if (d != null) {
					const acc = d.getArray('accepted') as Array<UTSJSONObject> | null
					const dup = d.getArray('duplicates')
					const rej = d.getArray('rejected')
					accepted = acc != null ? acc.length : 0
					duplicates = dup != null ? dup.length : 0
					rejected = rej != null ? rej.length : 0
					if (acc != null) {
						for (let i = 0; i < acc.length; i++) {
							acceptedEvents.push(acc[i])
						}
					}
				}
				resolve(new SyncOutcome(accepted, duplicates, rejected, acceptedEvents))
			} else if (res.statusCode >= 400 && res.statusCode < 500) {
				console.error('[yishu] 事件同步 4xx=' + res.statusCode + ' ' + JSON.stringify(res.data))
				resolve(new SyncOutcome(0, 0, events.length, [])) // 4xx 不可重试
			} else {
				resolve(null) // 5xx/其他 → 重试
			}
		},
		fail: () => {
			resolve(null) // 网络错误 → 重试
		}
	})
}

/** 提交端侧 L1 事件（带退避重试）；全失败返回 null */
export function syncClientEvents(events: Array<UTSJSONObject>): Promise<SyncOutcome | null> {
	return new Promise<SyncOutcome | null>((resolve) => {
		if (events.length === 0) {
			resolve(new SyncOutcome(0, 0, 0, []))
			return
		}
		ensureLogin().then((ok: boolean) => {
			if (!ok) {
				console.error('[yishu] 事件同步前登录失败')
				resolve(null)
				return
			}
			const token = getToken()
			retryAsync<SyncOutcome>(
				() => postOncePromise(events, token),
				(): boolean => false,
				(): boolean => false
			).then((r: SyncOutcome | null) => {
				resolve(r)
			})
		})
	})
}

/** 单次提交 Promise 化（null = 网络/5xx 可重试；SyncOutcome = 服务端已处理，含 4xx 停批语义） */
function postOncePromise(events: Array<UTSJSONObject>, token: string): Promise<SyncOutcome | null> {
	return new Promise<SyncOutcome | null>((resolve) => {
		postOnce(events, token, resolve)
	})
}

