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
 *     后端 offline_queue 六字段已就绪）。队列读写集中在 OpQueue 内聚函数 —— 自定义基座落地后
 *     只需替换 readQueue/writeQueue 的实现为 SQLite DAO，外部调用不变。
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
 *   ③ 后台 WorkManager 定时 → registerBackgroundSync() 钩子（登记给 Wave 4 Agent K）
 */
import { getBaseUrl } from './config'
import { getToken, ensureLogin, refreshToken } from './auth'
// TD-P2B（S1-M3/M4 收口）：退避表 + 重试统一走 retry.ts、ISO 时间统一走 time.ts；
// 此处保留导出别名（BACKOFF_MS/isoNow）兼容现有引用
import { retryAsync, BACKOFF_MS as SHARED_BACKOFF_MS } from './retry'
import { isoLocal } from './time'

export const DEVICE_ID: string = 'yishu-android-dev'
export const MAX_BATCH_FAILURES: number = 10
/** 指数退避（S1-M3 收口：与 event_sync/uploader 共享 retry.ts：2s→4s→8s→8s→8s，5 次上限） */
export const BACKOFF_MS: number[] = SHARED_BACKOFF_MS
const PUSH_BATCH_SIZE: number = 100
const DEFAULT_SYNC_INTERVAL_MS: number = 2 * 60 * 60 * 1000 // 2 小时定时兜底

const OP_QUEUE_KEY: string = 'yishu_sync_op_queue'
const CURSOR_KEY: string = 'yishu_sync_cursor'
const MIRROR_KEY: string = 'yishu_sync_mirror'
const PAUSED_KEY: string = 'yishu_sync_paused'

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

// ---------- 队列存储（uni storage 行分隔 JSON；XView/SQLCipher 落地后替换实现） ----------

function readQueue(): Array<string> {
	const raw = uni.getStorageSync(OP_QUEUE_KEY) as string
	if (raw == null || raw == '') {
		return []
	}
	const lines = raw.split('\n')
	const out: Array<string> = []
	for (let i = 0; i < lines.length; i++) {
		if (lines[i] != '') {
			out.push(lines[i])
		}
	}
	return out
}

function writeQueue(lines: Array<string>): void {
	uni.setStorageSync(OP_QUEUE_KEY, lines.join('\n'))
}

/** 入队一条操作（六字段契约） */
function pushOp(opType: string, payload: UTSJSONObject): void {
	const entry: UTSJSONObject = {
		op_id: nextOpId(),
		op_type: opType,
		payload: payload,
		status: 'pending',
		created_at: isoNow(),
		retry_count: 0
	}
	const lines = readQueue()
	lines.push(JSON.stringify(entry))
	writeQueue(lines)
	console.log('[yishu] sync enqueue ' + opType + ' queue=' + lines.length)
	emitStatus()
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

/** 待同步操作条数（pending） */
export function pendingSyncCount(): number {
	const lines = readQueue()
	let n = 0
	for (let i = 0; i < lines.length; i++) {
		try {
			const e = JSON.parse(lines[i]) as UTSJSONObject
			if (e != null && e.getString('status') == 'pending') {
				n++
			}
		} catch (e) {
			// 脏行不计
		}
	}
	return n
}

// ---------- 暂停控制器（与照片上传共享） ----------

/** 订阅同步状态变化（UI 横幅用）；回调形如 (paused, reason) */
export type SyncStatusListener = (paused: boolean, reason: string) => void
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

/** 连续失败登记：≥MAX_BATCH_FAILURES 自动暂停（供 uploader 批量上传调用） */
export function registerConsecutiveFailure(): void {
	_consecutiveFailures++
	if (_consecutiveFailures >= MAX_BATCH_FAILURES) {
		pauseSync('网络异常，已暂停同步')
	}
}

export function resetConsecutiveFailures(): void {
	_consecutiveFailures = 0
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

/** 统一 JSON 请求（401 → refresh 一次后重放，与 api.ts 对齐；status 0 = 网络失败） */
class HttpResult {
	status: number
	body: UTSJSONObject | null

	constructor(status: number, body: UTSJSONObject | null) {
		this.status = status
		this.body = body
	}
}

function syncHttp(path: string, method: string, data: UTSJSONObject | null, retried: boolean, resolve: (r: HttpResult) => void): void {
	uni.request({
		url: getBaseUrl() + path,
		method: method,
		data: data == null ? {} : data,
		header: {
			'Content-Type': 'application/json',
			'Authorization': 'Bearer ' + getToken()
		},
		timeout: 15000,
		success: (res) => {
			if (res.statusCode === 401 && !retried) {
				// 冷启动旧 token 失效：refresh 一次后重放（refresh 内部无 refresh_token 时重新登录）
				refreshToken().then((ok: boolean) => {
					if (ok) {
						syncHttp(path, method, data, true, resolve)
					} else {
						console.error('[yishu] sync 401 refresh 失败')
						resolve(new HttpResult(401, null))
					}
				})
				return
			}
			resolve(new HttpResult(res.statusCode, (res.data != null && typeof res.data == 'object') ? (res.data as UTSJSONObject) : null))
		},
		fail: () => {
			resolve(new HttpResult(0, null))
		}
	})
}

function syncHttpPromise(path: string, method: string, data: UTSJSONObject | null): Promise<HttpResult> {
	return new Promise<HttpResult>((resolve) => {
		syncHttp(path, method, data, false, resolve)
	})
}

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
		syncHttpPromise('/api/v1/sync/push', 'POST', reqBody).then((hr: HttpResult) => {
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

/** 从队列移除一批 op（push 成功响应后整批出队——服务端已按 op_id 幂等去重） */
function dropBatchFromQueue(ops: Array<UTSJSONObject>): void {
	const dropIds: Array<string> = []
	for (let i = 0; i < ops.length; i++) {
		dropIds.push(ops[i].getString('op_id') ?? '')
	}
	const lines = readQueue()
	const kept: Array<string> = []
	for (let i = 0; i < lines.length; i++) {
		let drop = false
		try {
			const e = JSON.parse(lines[i]) as UTSJSONObject
			const id = e != null ? e.getString('op_id') ?? '' : ''
			for (let j = 0; j < dropIds.length; j++) {
				if (dropIds[j] != '' && dropIds[j] == id) {
					drop = true
					break
				}
			}
		} catch (e) {
			// 脏行保留
		}
		if (!drop) {
			kept.push(lines[i])
		}
	}
	writeQueue(kept)
}

/** 取下一批待 push 的 op（≤PUSH_BATCH_SIZE） */
function nextBatch(): Array<UTSJSONObject> {
	const lines = readQueue()
	const out: Array<UTSJSONObject> = []
	for (let i = 0; i < lines.length && out.length < PUSH_BATCH_SIZE; i++) {
		try {
			const e = JSON.parse(lines[i]) as UTSJSONObject
			if (e != null && e.getString('status') == 'pending') {
				out.push(e)
			}
		} catch (e) {
			// 脏行跳过
		}
	}
	return out
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
			syncHttpPromise('/api/v1/sync/pull?device_id=' + DEVICE_ID + '&since=' + since + '&limit=200', 'GET', null).then((hr: HttpResult) => {
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
			syncHttpPromise('/api/v1/sync/reconcile', 'POST', reqBody).then((hr: HttpResult) => {
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

// ---------- 2h 定时兜底（App 存活期间 setInterval；后台 WorkManager 登记给 Wave 4 K） ----------

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

/** 后台 WorkManager 定时同步挂接点（Wave 4 Agent K）：自定义基座就绪后，
 *  在此调 yishu-bg-sync UTS 插件注册 2h 周期 WorkManager，到点回调 runSyncChain()。
 *  当前标准基座无后台能力，App 存活期由 startPeriodicSync 兜底。 */
export function registerBackgroundSync(): void {
	console.log('[yishu] sync_client: 后台 WorkManager 定时同步登记给 Wave 4 K（当前 setInterval 兜底）')
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
