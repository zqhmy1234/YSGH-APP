/**
 * B4 端云字段级同步客户端（B4-2/3 · S-SY-1 客户端侧）——Wave 3 Agent H
 *
 * 后端三接口全接通（此前 audit_B4_sync §2/§8 标注"客户端零调用"，现已消费）：
 *   POST /api/v1/sync/push       操作批次提交（op_id 幂等，返回 applied/conflicts/rejected/server_version）
 *   GET  /api/v1/sync/pull       增量拉取（since 游标 → changes + 新游标；游标按 (user,device) 持久化）
 *   POST /api/v1/sync/reconcile  端云对账（本地快照 → 差异报告；need_push 补推 / need_pull 补拉）
 *
 * 本地操作日志队列（六字段契约：op_id/op_type/payload/status/created_at/retry_count）：
 *   - uni storage 行分隔 JSON 兜底（XView/SQLCipher 自定义基座未就绪，progress.md:14 注记；
 *     后端 offline_queue 六字段已就绪）。F9/O6 后读写统一走 queue_store.ts 共享存储
 *     （与 event_ops 合并单 key）——自定义基座落地后只需替换 queue_store 实现为 SQLite DAO，
 *     外部调用不变；本文件保留 sync push 批推路由（op_type: upsert_field/delete）。
 *   - op_id 幂等：push 返回 applied/conflicts/rejected（服务端按 (user_id,op_id) 去重），
 *     一次成功响应即整批出队（含服务端幂等跳过的 op_id）；conflicts 提示"已保留云端版本"；
 *     rejected 丢弃并记录；网络/5xx 指数退避重试（2s→4s→8s→8s→8s）；4xx 停整批。
 *   - 连续失败 ≥ MAX_BATCH_FAILURES → 暂停（pauseSync + 横幅 + resumeSync 一键继续），
 *     与照片上传共享同一暂停控制器。
 *
 * 三路触发（B4-5）：
 *   ① 前台主动：initSync() 在 App.onLaunch 启动 2h setInterval 定时兜底 +
 *      onNetworkStatusChange 网络恢复即补推（App 存活期间）
 *   ② 相册监听 ContentObserver（Wave 2 已接，本文件不重复）
 *   ③ 后台 WorkManager 定时 → registerBackgroundSync() 真接线（B5d：周期唤醒写 pending，
 *      应用层注册 handler 时 drain + App.onShow drain 消费，两段式不丢任务）
 */
// O5 收口：网络层统一走 api.ts 的 rawRequest（401 刷新重放 + 5xx Sentry 上报），
// 本地复制的请求封装已删，不再直接依赖 getToken/refreshToken
import { rawRequest, HttpResult } from './api'
// O3 收口：DEVICE_ID 唯一来源收敛到 auth.ts（历史双源定义出过事故），此处 import 不再本地定义
import { ensureLogin, DEVICE_ID } from './auth'
// TD-P2B（S1-M3/M4 收口）：退避表 + 重试统一走 retry.ts、ISO 时间统一走 time.ts；
// 此处保留导出别名（BACKOFF_MS/isoNow）兼容现有引用
import { retryAsync, BACKOFF_MS as SHARED_BACKOFF_MS } from './retry'
import { isoLocal } from './time'
// B5d 后台任务插件（Wave 4 K）：WorkManager 周期唤醒写 pending → 应用层 drain 消费（两段式）
import { initBackgroundTasks, setBackgroundTaskHandler, drainPendingTasks } from '@/uni_modules/yishu-background-tasks/utssdk/app-android/index.uts'

// O3 收口：DEVICE_ID 由 auth.ts 唯一导出（见上方 import），不再本地重复定义
// F9/R1#10：暂停令牌单源 pause_controller（原内聚"暂停控制器（与照片上传共享）"一节）；
// 此处 import + 再导出兼容既有外部引用（uploader/UploadStatusBanner），行为等价
import {
	MAX_BATCH_FAILURES as SHARED_MAX_BATCH_FAILURES,
	SyncStatusListener,
	pauseSync as sharedPauseSync,
	resumeSync as sharedResumeSync,
	isSyncPaused as sharedIsSyncPaused,
	getPauseReason as sharedGetPauseReason,
	subscribeSyncStatus as sharedSubscribeSyncStatus,
	registerConsecutiveFailure as sharedRegisterConsecutiveFailure,
	resetConsecutiveFailures as sharedResetConsecutiveFailures,
	broadcastStatus as sharedBroadcastStatus
} from './pause_controller'

/** 指数退避（S1-M3 收口：与 event_sync/uploader 共享 retry.ts：2s→4s→8s→8s→8s，5 次上限） */
export const BACKOFF_MS: number[] = SHARED_BACKOFF_MS
const PUSH_BATCH_SIZE: number = 100
const DEFAULT_SYNC_INTERVAL_MS: number = 2 * 60 * 60 * 1000 // 2 小时定时兜底

// O6 双离线队列合并：队列存储单源 queue_store（sync/event 共用 yishu_offline_queue），
// 路由差异保留在本文件 flush（sync push 批推）与 event_ops flush（confirm/merge/split 顺序）
import { enqueueEntry, countPendingOfTypes, nextBatchOfTypes, removeByIds } from './queue_store'

/** 同步类型操作（与 event_ops confirm/merge/split 区分，同队列按 op_type 路由） */
const SYNC_TYPES: Array<string> = ['upsert_field', 'delete']
const CURSOR_KEY: string = 'yishu_sync_cursor'
const MIRROR_KEY: string = 'yishu_sync_mirror'

// ---------- 工具 ----------

/** epoch ms → ISO8601 本地时间（S1-M4 收口：统一走 time.isoLocal；保留导出名兼容现有引用） */
export function isoNow(): string {
	return isoLocal(Date.now())
}

/** op_id 唯一后缀（同毫秒并发防碰撞） */
let _opSeq: number = 0
function nextOpId(): string {
	_opSeq++
	return 'sync_' + Date.now().toString() + '_' + _opSeq.toString()
}

// ---------- 队列存储（F9/O6 合并：读写统一走 queue_store.ts 共享存储，本文件只留入队/批推路由） ----------

/** 入队一条操作（六字段契约；存储共享 queue_store，行为与原 readQueue/writeQueue 等价） */
function pushOp(opType: string, payload: UTSJSONObject): void {
	const entry: UTSJSONObject = {
		op_id: nextOpId(),
		op_type: opType,
		payload: payload,
		status: 'pending',
		created_at: isoNow(),
		retry_count: 0
	}
	enqueueEntry(entry)
	console.log('[yishu] sync enqueue ' + opType + ' queue=' + countPendingOfTypes(SYNC_TYPES))
	sharedBroadcastStatus()
}

/** 字段级操作入队（upsert_field；value 支持 string/number/boolean，按类型标记存储防 UTS 读取歧义） */
export function enqueueFieldOp(entityType: string, entityId: string, field: string, value: string | number | boolean, updatedAt: string = ''): void {
	let valueType: string = 'string'
	if (typeof value == 'number') {
		valueType = 'number'
	} else if (typeof value == 'boolean') {
		valueType = 'boolean'
	}
	const payload: UTSJSONObject = {
		entity_type: entityType,
		entity_id: entityId,
		field: field,
		value: '' + value,
		value_type: valueType,
		updated_at: updatedAt != '' ? updatedAt : isoNow()
	}
	pushOp('upsert_field', payload)
}

/** 软删除操作入队（delete 墓碑） */
export function enqueueDeleteOp(entityType: string, entityId: string, updatedAt: string = ''): void {
	const payload: UTSJSONObject = {
		entity_type: entityType,
		entity_id: entityId,
		updated_at: updatedAt != '' ? updatedAt : isoNow()
	}
	pushOp('delete', payload)
}

/** 待同步操作条数（pending，仅 sync 类型——共享队列按 op_type 过滤） */
export function pendingSyncCount(): number {
	return countPendingOfTypes(SYNC_TYPES)
}

// ---------- 暂停控制器（F9 职责分离：实现移至 pause_controller.ts，此处仅再导出兼容既有引用） ----------

export const MAX_BATCH_FAILURES: number = SHARED_MAX_BATCH_FAILURES
export function subscribeSyncStatus(cb: SyncStatusListener): void {
	sharedSubscribeSyncStatus(cb)
}
export function pauseSync(reason: string): void {
	sharedPauseSync(reason)
}
export function resumeSync(): void {
	sharedResumeSync()
}
export function isSyncPaused(): boolean {
	return sharedIsSyncPaused()
}
export function getPauseReason(): string {
	return sharedGetPauseReason()
}
export function registerConsecutiveFailure(): void {
	sharedRegisterConsecutiveFailure()
}
export function resetConsecutiveFailures(): void {
	sharedResetConsecutiveFailures()
}

// ---------- 游标持久化 ----------

function readCursor(): number {
	const raw = uni.getStorageSync(CURSOR_KEY) as string
	if (raw == null || raw == '') {
		return 0
	}
	const n = parseInt(raw)
	return isNaN(n) ? 0 : n
}

function writeCursor(cursor: number): void {
	uni.setStorageSync(CURSOR_KEY, '' + cursor)
}

// ---------- 本地镜像（实体 updated_at/deleted 快照，供 reconcile 对账 + 差异提示） ----------

function readMirror(): Array<string> {
	const raw = uni.getStorageSync(MIRROR_KEY) as string
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

function mirrorSet(entityId: string, updatedAtIso: string, deleted: boolean): void {
	const lines = readMirror()
	const line = entityId + '|' + updatedAtIso + '|' + (deleted ? '1' : '0')
	for (let i = 0; i < lines.length; i++) {
		if (lines[i].startsWith(entityId + '|')) {
			lines[i] = line
			writeMirror(lines)
			return
		}
	}
	lines.push(line)
	writeMirror(lines)
}

function writeMirror(lines: Array<string>): void {
	uni.setStorageSync(MIRROR_KEY, lines.join('\n'))
}

/** 本地快照（reconcile 请求体 items）→ Array<{entity_id, updated_at, deleted}> */
function mirrorSnapshot(): Array<UTSJSONObject> {
	const lines = readMirror()
	const out: Array<UTSJSONObject> = []
	for (let i = 0; i < lines.length; i++) {
		const parts = lines[i].split('|')
		if (parts.length < 3) {
			continue
		}
		const it: UTSJSONObject = {
			entity_id: parts[0],
			updated_at: parts[1],
			deleted: parts[2] == '1'
		}
		out.push(it)
	}
	return out
}

/** 拉取变更应用到本地镜像（字段级 value 明细消费：MVP 记录实体 updated_at/deleted，
 *  供对账快照 + 差异提示；离线字段值库（本地 SQLite 实体表）随 XView 自定义基座落地） */
function applyChanges(changes: Array<UTSJSONObject>): void {
	for (let i = 0; i < changes.length; i++) {
		const ch = changes[i]
		const entityId = ch.getString('entity_id') ?? ''
		const updatedAt = ch.getString('updated_at') ?? ''
		const opType = ch.getString('op_type') ?? ''
		if (entityId == '') {
			continue
		}
		const deleted = opType == 'delete'
		if (updatedAt != '') {
			mirrorSet(entityId, updatedAt, deleted)
		}
	}
}

// ---------- HTTP ----------

/** 单次 push 结果 */
class PushBatchResult {
	ok: boolean
	applied: number
	conflicts: number
	rejected: number
	is4xx: boolean

	constructor(ok: boolean, applied: number, conflicts: number, rejected: number, is4xx: boolean) {
		this.ok = ok
		this.applied = applied
		this.conflicts = conflicts
		this.rejected = rejected
		this.is4xx = is4xx
	}
}

/** 单批提交（无重试）：ok=false 表示网络/5xx（可退避重试）或 4xx（is4xx=true 停批） */
function postBatch(ops: Array<UTSJSONObject>): Promise<PushBatchResult> {
	return new Promise<PushBatchResult>((resolve) => {
		const syncOps: Array<UTSJSONObject> = []
		for (let i = 0; i < ops.length; i++) {
			const op = ops[i]
			const payload = op.getJSON('payload')
			if (payload == null) {
				continue
			}
			const syncOp: UTSJSONObject = {
				op_id: op.getString('op_id') ?? '',
				op_type: op.getString('op_type') ?? 'upsert_field',
				entity_type: payload.getString('entity_type') ?? 'content',
				entity_id: payload.getString('entity_id') ?? ''
			}
			const field = payload.getString('field')
			if (field != null && field != '') {
				syncOp.set('field', field)
			}
			// value 按类型标记还原（string/number/boolean）
			const valueType = payload.getString('value_type') ?? 'string'
			const valueStr = payload.getString('value') ?? ''
			if (valueType == 'number') {
				syncOp.set('value', parseFloat(valueStr))
			} else if (valueType == 'boolean') {
				syncOp.set('value', valueStr == 'true')
			} else {
				syncOp.set('value', valueStr)
			}
			const updatedAt = payload.getString('updated_at')
			if (updatedAt != null && updatedAt != '') {
				syncOp.set('updated_at', updatedAt)
			}
			syncOps.push(syncOp)
		}
		if (syncOps.length === 0) {
			resolve(new PushBatchResult(true, 0, 0, 0, false))
			return
		}
		const reqBody: UTSJSONObject = {
			device_id: DEVICE_ID,
			ops: syncOps
		}
		rawRequest('/api/v1/sync/push', 'POST', reqBody).then((hr: HttpResult) => {
			const status = hr.status
			if (status === 200) {
				let applied = 0
				let conflicts = 0
				let rejected = 0
				if (hr.body != null) {
					const d: UTSJSONObject | null = hr.body.getJSON('data')
					if (d != null) {
						const app = d.getArray('applied')
						const conf = d.getArray('conflicts')
						const rej = d.getArray('rejected')
						applied = app != null ? app.length : 0
						conflicts = conf != null ? conf.length : 0
						rejected = rej != null ? rej.length : 0
					}
				}
				resolve(new PushBatchResult(true, applied, conflicts, rejected, false))
			} else if (status >= 400 && status < 500) {
				console.error('[yishu] sync push 4xx=' + status)
				resolve(new PushBatchResult(false, 0, 0, 0, true))
			} else {
				resolve(new PushBatchResult(false, 0, 0, 0, false))
			}
		})
	})
}

/** 从队列移除一批 op（push 成功响应后整批出队——服务端已按 op_id 幂等去重；存储共享 queue_store） */
function dropBatchFromQueue(ops: Array<UTSJSONObject>): void {
	const dropIds: Array<string> = []
	for (let i = 0; i < ops.length; i++) {
		dropIds.push(ops[i].getString('op_id') ?? '')
	}
	removeByIds(dropIds)
}

/** 取下一批待 push 的 op（≤PUSH_BATCH_SIZE，仅 sync 类型——共享队列按 op_type 过滤） */
function nextBatch(): Array<UTSJSONObject> {
	return nextBatchOfTypes(SYNC_TYPES, PUSH_BATCH_SIZE)
}

/** 清空整批（4xx 不可重试：整批丢弃并记录） */
function dropAll(ops: Array<UTSJSONObject>): void {
	dropBatchFromQueue(ops)
}

/** 单批提交 + 退避重试（TD-P2B S1-M3 收口：统一走 retry.ts retryAsync）
 *  网络/5xx → 重试（onFail 计数连续失败、暂停则中断）；4xx → isFatal 停批 */
function postBatchWithRetry(ops: Array<UTSJSONObject>): Promise<PushBatchResult> {
	return retryAsync<PushBatchResult>(
		() => postBatch(ops).then((r: PushBatchResult): PushBatchResult | null => {
			return r.ok || r.is4xx ? r : null
		}),
		(r: PushBatchResult | null): boolean => r != null && r.is4xx,
		(_r: PushBatchResult | null, _attempt: number): boolean => {
			registerConsecutiveFailure()
			return isSyncPaused()
		}
	).then((r: PushBatchResult | null): PushBatchResult => {
		if (r == null) {
			return new PushBatchResult(false, 0, 0, 0, false)
		}
		return r
	})
}

/** 队列主循环：逐批 push（每批内部退避重试），4xx 停批，连续失败≥N 暂停 */
function flushQueueLoop(
	applyAcc: number,
	conflictAcc: number,
	rejectAcc: number,
	resolve: (r: PushOutcome) => void
): void {
	const ops = nextBatch()
	if (ops.length === 0) {
		resolve(new PushOutcome(applyAcc, conflictAcc, rejectAcc, pendingSyncCount()))
		return
	}
	postBatchWithRetry(ops).then((r: PushBatchResult) => {
		if (r.ok) {
			resetConsecutiveFailures()
			dropBatchFromQueue(ops)
			if (r.conflicts > 0) {
				console.warn('[yishu] sync push conflicts=' + r.conflicts + '（已保留云端版本）')
			}
			if (r.rejected > 0) {
				console.warn('[yishu] sync push rejected=' + r.rejected + '（已丢弃）')
			}
			flushQueueLoop(applyAcc + r.applied, conflictAcc + r.conflicts, rejectAcc + r.rejected, resolve)
			return
		}
		if (r.is4xx) {
			console.error('[yishu] sync push 4xx 停整批（不可重试）')
			dropAll(ops)
			flushQueueLoop(applyAcc, conflictAcc, rejectAcc + ops.length, resolve)
			return
		}
		// 网络/5xx：退避耗尽或暂停中断 → 保留队列待下轮（连续失败计数/暂停判定在 retryAsync.onFail）
		console.error('[yishu] sync push 退避耗尽/暂停，剩余 ' + pendingSyncCount() + ' 条待下轮')
		resolve(new PushOutcome(applyAcc, conflictAcc, rejectAcc, pendingSyncCount()))
	})
}

/** 补推离线操作队列（op_id 幂等）→ 统计结果 */
export function pushPendingOps(): Promise<PushOutcome> {
	return new Promise<PushOutcome>((resolve) => {
		if (pendingSyncCount() === 0) {
			resolve(new PushOutcome(0, 0, 0, 0))
			return
		}
		ensureLogin().then((ok: boolean) => {
			if (!ok) {
				console.error('[yishu] sync push 前登录失败')
				resolve(new PushOutcome(0, 0, 0, pendingSyncCount()))
				return
			}
			flushQueueLoop(0, 0, 0, resolve)
		})
	})
}

/** 增量拉取（游标持久化 + 应用到本地镜像） */
export function pullIncremental(): Promise<PullOutcome> {
	return new Promise<PullOutcome>((resolve) => {
		ensureLogin().then((ok: boolean) => {
			if (!ok) {
				console.error('[yishu] sync pull 前登录失败')
				resolve(new PullOutcome(0, readCursor(), false))
				return
			}
			const since = readCursor()
			rawRequest('/api/v1/sync/pull?device_id=' + DEVICE_ID + '&since=' + since + '&limit=200', 'GET', null).then((hr: HttpResult) => {
				if (hr.status === 200) {
					let changes: Array<UTSJSONObject> = []
					let cursor = since
					let hasMore = false
					if (hr.body != null) {
						const d: UTSJSONObject | null = hr.body.getJSON('data')
						if (d != null) {
							const arr = d.getArray('changes')
							if (arr != null) {
								changes = arr as Array<UTSJSONObject>
							}
							cursor = d.getNumber('cursor') as number
							const hm = d.getBoolean('has_more')
							hasMore = hm != null ? hm : false
						}
					}
					applyChanges(changes)
					writeCursor(cursor)
					console.log('[yishu] sync pull since=' + since + ' -> cursor=' + cursor + ' changes=' + changes.length + ' hasMore=' + hasMore)
					resolve(new PullOutcome(changes.length, cursor, hasMore))
				} else {
					console.error('[yishu] sync pull HTTP ' + hr.status)
					resolve(new PullOutcome(0, since, false))
				}
			})
		})
	})
}

/** 端云对账（本地快照 → 差异报告；need_push 补推、need_pull 补拉） */
export function reconcileNow(): Promise<ReconcileReport | null> {
	return new Promise<ReconcileReport | null>((resolve) => {
		const items = mirrorSnapshot()
		if (items.length === 0) {
			resolve(new ReconcileReport(0, 0, 0, 0, 0))
			return
		}
		ensureLogin().then((ok: boolean) => {
			if (!ok) {
				resolve(null)
				return
			}
			const reqBody: UTSJSONObject = {
				items: items
			}
			rawRequest('/api/v1/sync/reconcile', 'POST', reqBody).then((hr: HttpResult) => {
				if (hr.status === 200 && hr.body != null) {
					const d: UTSJSONObject | null = hr.body.getJSON('data')
					if (d == null) {
						resolve(null)
						return
					}
					const summary = d.getJSON('summary')
					let needPush = 0
					let needPull = 0
					if (summary != null) {
						needPush = summary.getNumber('need_push') as number
						needPull = summary.getNumber('need_pull') as number
					}
					const missingOnCloudArr = d.getArray('missing_on_cloud')
					const missingOnClientArr = d.getArray('missing_on_client')
					const divergentArr = d.getArray('divergent')
					const report = new ReconcileReport(
						needPush,
						needPull,
						divergentArr != null ? divergentArr.length : 0,
						missingOnCloudArr != null ? missingOnCloudArr.length : 0,
						missingOnClientArr != null ? missingOnClientArr.length : 0
					)
					console.log('[yishu] sync reconcile need_push=' + needPush + ' need_pull=' + needPull)
					// 差异消费：need_push → 补推离线队列；need_pull → 补拉增量
					if (needPush > 0) {
						pushPendingOps()
					}
					if (needPull > 0) {
						pullIncremental()
					}
					resolve(report)
				} else {
					console.error('[yishu] sync reconcile HTTP ' + hr.status)
					resolve(null)
				}
			})
		})
	})
}

/** 完整同步链路：补推 → 增量拉取（暂停时直接返回） */
export function runSyncChain(): Promise<SyncChainResult> {
	return new Promise<SyncChainResult>((resolve) => {
		if (isSyncPaused()) {
			resolve(new SyncChainResult(0, 0, true, 0))
			return
		}
		pushPendingOps().then((push: PushOutcome) => {
			pullIncremental().then((pull: PullOutcome) => {
				resolve(new SyncChainResult(push.applied, pull.changes, isSyncPaused(), push.conflicts))
			})
		})
	})
}

// ---------- 2h 定时兜底（App 存活期间 setInterval；后台 WorkManager 两段式：唤醒写 pending → 前台 drain 消费） ----------

let _syncTimer: number | null = null

export function startPeriodicSync(intervalMs: number = DEFAULT_SYNC_INTERVAL_MS): void {
	if (_syncTimer != null) {
		return
	}
	const ms = intervalMs > 0 ? intervalMs : DEFAULT_SYNC_INTERVAL_MS
	_syncTimer = setInterval(() => {
		if (!isSyncPaused()) {
			console.log('[yishu] sync 2h 定时兜底触发')
			runSyncChain()
		}
	}, ms)
	console.log('[yishu] sync periodic started, interval=' + ms + 'ms')
}

export function stopPeriodicSync(): void {
	if (_syncTimer != null) {
		clearInterval(_syncTimer)
		_syncTimer = null
	}
}

/**
 * 后台任务接线（B5d · P0-3 真接线，取代原纯日志 stub）：
 *   - initBackgroundTasks(2)：注册 2h 周期 PeriodicWork（photo-watch.start() 亦引导，幂等可重复调；
 *     标准基座无 androidx.work → 内部降级 pending 记录，App 存活期由上方 setInterval 兜底）
 *   - setBackgroundTaskHandler：注册任务回调（注册即 drain 一次积压）；
 *     WorkManager 到点/标准基座 enqueueTask 写入的 pending 由此派发执行
 *   - 派发逻辑：统一 runSyncChain()（sync 周期 / voice_transcribe / sync_photo / 聚合 / 拉取等类型）；
 *     照片续传由 uploader 自身 onNetworkRestored 钩子负责——uploader 已依赖本文件（pauseSync 等），
 *     此处静态引用 uploader 会成环，故不在此接 continuePendingUploads
 *   - App.onShow 消费积压：drainBackgroundTasks()（本文件导出，内部逐条让出主线程）
 */
export function registerBackgroundSync(): void {
	initBackgroundTasks(2)
	setBackgroundTaskHandler((taskType: string) => {
		console.log('[yishu] sync_client 后台任务派发: ' + taskType)
		runSyncChain()
	})
	console.log('[yishu] sync_client: 后台任务接线完成（WorkManager 周期 + pending drain 消费）')
}

/** 前台消费积压后台任务（App.onShow 调用；幂等空跑无害） */
export function drainBackgroundTasks(): void {
	drainPendingTasks()
}

/** 网络恢复钩子（uploader 注册：WiFi 恢复自动补传暂缓原图；与 sync_client 无循环依赖） */
export type NetRestoredListener = () => void
let _netRestoredListeners: Array<NetRestoredListener> = []

export function onNetworkRestored(cb: NetRestoredListener): void {
	_netRestoredListeners.push(cb)
}

/** 应用启动挂接：2h 定时 + 网络恢复即补推（幂等，App.onLaunch 调用一次） */
export function initSync(): void {
	startPeriodicSync()
	registerBackgroundSync()
	// 网络恢复（wifi/蜂窝/有线）→ 解除暂停并补跑同步链路（App 存活期间）
	uni.onNetworkStatusChange((res) => {
		if (res.isConnected) {
			if (isSyncPaused()) {
				resumeSync()
			}
			console.log('[yishu] sync network restored: ' + res.networkType)
			runSyncChain()
			for (let i = 0; i < _netRestoredListeners.length; i++) {
				_netRestoredListeners[i]()
			}
		}
	})
}

// ---------- 结果类型 ----------

export class PushOutcome {
	applied: number
	conflicts: number
	rejected: number
	remaining: number

	constructor(applied: number, conflicts: number, rejected: number, remaining: number) {
		this.applied = applied
		this.conflicts = conflicts
		this.rejected = rejected
		this.remaining = remaining
	}
}

export class PullOutcome {
	changes: number
	cursor: number
	hasMore: boolean

	constructor(changes: number, cursor: number, hasMore: boolean) {
		this.changes = changes
		this.cursor = cursor
		this.hasMore = hasMore
	}
}

export class SyncChainResult {
	pushed: number
	pulled: number
	paused: boolean
	conflicts: number

	constructor(pushed: number, pulled: number, paused: boolean, conflicts: number) {
		this.pushed = pushed
		this.pulled = pulled
		this.paused = paused
		this.conflicts = conflicts
	}
}

export class ReconcileReport {
	needPush: number
	needPull: number
	divergent: number
	missingOnCloud: number
	missingOnClient: number

	constructor(needPush: number, needPull: number, divergent: number, missingOnCloud: number, missingOnClient: number) {
		this.needPush = needPush
		this.needPull = needPull
		this.divergent = divergent
		this.missingOnCloud = missingOnCloud
		this.missingOnClient = missingOnClient
	}
}
