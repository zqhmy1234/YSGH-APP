/**
 * 指数退避重试共享层（TD-P2B · S1-M3：sync_client/event_sync/uploader 三份复制收口）
 *
 * 三处此前各自定义 BACKOFF_MS（2s→4s→8s→8s→8s，5 次上限）与同构的
 * setTimeout 递归重试（sync_client.ts:507-521 / event_sync.ts:77-90 / uploader.ts:607-627），
 * 改退避策略（次数/间隔/终止判定）需三处同步。现统一本模块。
 *
 * retryAsync 契约：
 *  - fn: 单次尝试 → Promise<T | null>（null = 需退避重试的可重试失败；非 null = 已定局）
 *  - isFatal(result, attempt): 该次失败是否不可重试（4xx/参数/归属错误 → true 立即终止）
 *  - onFail(result, attempt): 每次失败回调（返回 true 可提前中断剩余重试，如"已暂停"）
 * 返回最后一次 result（成功值或最终失败值；null 表示退避耗尽仍失败）。
 */
export const BACKOFF_MS: number[] = [2000, 4000, 8000, 8000, 8000]

export function retryAsync<T>(
	fn: () => Promise<T | null>,
	isFatal: (result: T | null, attempt: number) => boolean,
	onFail: (result: T | null, attempt: number) => boolean
): Promise<T | null> {
	return new Promise<T | null>((resolve) => {
		function loop(attempt: number): void {
			fn().then((result: T | null) => {
				if (result != null) {
					resolve(result)
					return
				}
				const stop = onFail(result, attempt)
				if (stop || isFatal(result, attempt) || attempt >= BACKOFF_MS.length) {
					resolve(result)
					return
				}
				setTimeout(() => {
					loop(attempt + 1)
				}, BACKOFF_MS[attempt])
			})
		}
		loop(0)
	})
}
