/**
 * 暂停控制器（F9/R1#10 · sync_client 与 uploader 共享的暂停令牌）
 *
 * 原实现内聚在 sync_client.ts「暂停控制器（与照片上传共享）」一节（Wave 3 H）：
 * 照片批量上传与字段级同步共用同一暂停语义——连续失败 ≥MAX_BATCH_FAILURES 自动暂停
 * （顶部横幅"网络异常，已暂停同步"），resumeSync 一键继续。职责分离后本模块为唯一实现：
 *  - sync_client（字段级同步 push）与 uploader（照片批量上传）都从这里消费 pause/resume/isSyncPaused
 *  - UI 横幅（UploadStatusBanner）订阅状态变化
 * 行为等价迁移：存储 key（yishu_sync_paused）、暂停语义、连续失败阈值均不变。
 */
export const MAX_BATCH_FAILURES: number = 10

/** 订阅同步状态变化（UI 横幅用）；回调形如 (paused, reason) */
export type SyncStatusListener = (paused: boolean, reason: string) => void

const PAUSED_KEY: string = 'yishu_sync_paused'

let _statusListeners: Array<SyncStatusListener> = []
let _consecutiveFailures: number = 0

export function subscribeSyncStatus(cb: SyncStatusListener): void {
	_statusListeners.push(cb)
}

function emitStatus(): void {
	const paused = isSyncPaused()
	const reason = getPauseReason()
	for (let i = 0; i < _statusListeners.length; i++) {
		_statusListeners[i](paused, reason)
	}
}

export function pauseSync(reason: string): void {
	uni.setStorageSync(PAUSED_KEY, reason)
	console.log('[yishu] sync paused: ' + reason)
	emitStatus()
}

export function resumeSync(): void {
	uni.removeStorageSync(PAUSED_KEY)
	_consecutiveFailures = 0
	emitStatus()
}

export function isSyncPaused(): boolean {
	const r = uni.getStorageSync(PAUSED_KEY) as string
	return r != null && r != ''
}

export function getPauseReason(): string {
	if (!isSyncPaused()) {
		return ''
	}
	return uni.getStorageSync(PAUSED_KEY) as string
}

/** 连续失败登记：≥MAX_BATCH_FAILURES 自动暂停（供 uploader 批量上传 / sync 批推调用） */
export function registerConsecutiveFailure(): void {
	_consecutiveFailures++
	if (_consecutiveFailures >= MAX_BATCH_FAILURES) {
		pauseSync('网络异常，已暂停同步')
	}
}

export function resetConsecutiveFailures(): void {
	_consecutiveFailures = 0
}

/** 重新广播当前暂停状态给订阅方（sync_client pushOp 入队后刷新横幅用；原 emitStatus 语义） */
export function broadcastStatus(): void {
	emitStatus()
}
