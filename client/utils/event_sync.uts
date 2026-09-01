/**
 * 端侧 L1 事件上云（S-SY-1 客户端侧 · B3-6）
 *
 * POST /api/v1/events/sync：批量提交端侧聚合的 L1 事件。
 *  - client_event_id 幂等：重试/重发只落一次（服务端去重）
 *  - 指数退避重试：2s→4s→8s→8s→8s（5 次上限），全失败返回 false（调用方提示/记录）
 *  - 4xx 停该批（参数/越权错误重试无意义）
 *
 * 2026-08-27 C1 收口（R3 O4/O5）：401 不再静默丢弃整批——网络层统一走 api.ts 的
 * rawRequest（内部自动 refresh_token 换新后重放一次，仍 401 才视为 4xx 停批）；
 * 5xx 由 rawRequest 统一 Sentry 上报。
 */
import { rawRequest, HttpResult } from './api'
import { ensureLogin, DEVICE_ID } from './auth'
import { retryAsync } from './retry'
// O15：事件上云端点路径统一走 contract.ts（与 OpenAPI 对齐）
import { PATH_EVENTS_SYNC } from './contract'
// O19：4xx 响应体可能含用户事件内容，脱敏后再落日志
import { redactLog } from './log'

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

/** 单次提交（无重试）；返回 null = 网络/5xx 可重试，Outcome = 服务端已处理（含 4xx 停批） */
function postOnce(events: Array<UTSJSONObject>): Promise<SyncOutcome | null> {
	return new Promise<SyncOutcome | null>((resolve) => {
		const body: UTSJSONObject = {
			device_id: DEVICE_ID,
			events: events
		}
		rawRequest(PATH_EVENTS_SYNC, 'POST', body).then((hr: HttpResult) => {
			if (hr.status === 200) {
				const d: UTSJSONObject | null = hr.body != null ? hr.body.getJSON('data') : null
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
			} else if (hr.status >= 400 && hr.status < 500) {
				// 4xx 不可重试（含 refresh 后仍 401——rawRequest 内部已刷新重放一次，不再静默丢批）
				// O19：响应体可能含用户事件内容，脱敏后再落日志
				console.error('[yishu] 事件同步 4xx=' + hr.status + ' ' + redactLog(JSON.stringify(hr.body)))
				resolve(new SyncOutcome(0, 0, events.length, []))
			} else {
				resolve(null) // 5xx/其他 → 重试
			}
		})
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
			// 每次尝试内部都经 rawRequest 重新取当前 token（401 刷新重放后自然带上新 token）
			// O18：isFatal/onFail 恒 false（4xx 停批已由 postOnce 返回非 null 实现），省略死参
			retryAsync<SyncOutcome>(
				() => postOnce(events)
			).then((r: SyncOutcome | null) => {
				resolve(r)
			})
		})
	})
}
