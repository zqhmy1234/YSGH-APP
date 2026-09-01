/**
 * 离线操作队列共享存储（O6 · F9/R1#10 双离线队列合并）
 *
 * 双离线队列并存：sync_client OpQueue（key=yishu_sync_op_queue）与 event_ops
 * op_log（key=yishu_op_log）——同六字段契约（op_id/op_type/payload/status/
 * created_at/retry_count）、uni storage 行分隔 JSON、不同实现。现统一到本模块
 * 单 key（yishu_offline_queue）共享存储；路由差异保留在各消费方 flush 循环
 * （sync push 批推 / event confirm/merge/split 顺序路由），退避统一走
 * retry.ts retryAsync。
 *
 * 一次性迁移：旧双 key 存量并入新 key（仅读+删，不写旧 key），升级不丢操作。
 *
 * 本模块只提供存储原语（入队/计数/取批/按 op_id 删除/退避 bump），不承载
 * 业务路由——sync_client 与 event_ops 各自消费并保持既有语义。
 */
const QUEUE_KEY: string = 'yishu_offline_queue'
const LEGACY_SYNC_KEY: string = 'yishu_sync_op_queue'
const LEGACY_EVENT_KEY: string = 'yishu_op_log'

let _migrated: boolean = false

/** 一次性迁移旧双 key → 单 key（新 key 空且旧 key 有数据时；仅读+删，不写旧 key） */
function migrateLegacyQueues(): void {
	if (_migrated) {
		return
	}
	_migrated = true
	const cur = uni.getStorageSync(QUEUE_KEY)
	if (cur != null && cur != '') {
		return
	}
	const syncRaw = uni.getStorageSync(LEGACY_SYNC_KEY) as string
	const eventRaw = uni.getStorageSync(LEGACY_EVENT_KEY) as string
	if ((syncRaw == null || syncRaw == '') && (eventRaw == null || eventRaw == '')) {
		return
	}
	const merged: Array<string> = []
	appendClean(syncRaw, merged)
	appendClean(eventRaw, merged)
	if (merged.length > 0) {
		uni.setStorageSync(QUEUE_KEY, merged.join('\n'))
	}
	uni.removeStorageSync(LEGACY_SYNC_KEY)
	uni.removeStorageSync(LEGACY_EVENT_KEY)
	console.log('[yishu] 离线队列已合并单 key：' + merged.length + ' 条迁移')
}

function appendClean(raw: string, out: Array<string>): void {
	if (raw == null || raw == '') {
		return
	}
	const lines = raw.split('\n')
	for (let i = 0; i < lines.length; i++) {
		if (lines[i] != '') {
			out.push(lines[i])
		}
	}
}

/** 读全部原始行（清空行；含脏行保留——不丢用户操作） */
function readLines(): Array<string> {
	migrateLegacyQueues()
	const raw = uni.getStorageSync(QUEUE_KEY) as string
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

function writeLines(lines: Array<string>): void {
	uni.setStorageSync(QUEUE_KEY, lines.join('\n'))
}

/** 解析一行六字段条目（脏行返回 null） */
function parseLine(line: string): UTSJSONObject | null {
	try {
		const e = JSON.parse(line) as UTSJSONObject
		if (e != null && typeof e == 'object') {
			return e
		}
	} catch (e) {
		// 脏行
	}
	return null
}

/** 入队一条（六字段 entry 已由消费方构造好） */
export function enqueueEntry(entry: UTSJSONObject): void {
	const lines = readLines()
	lines.push(JSON.stringify(entry))
	writeLines(lines)
}

/** 计数 pending 条目（op_type ∈ types；六字段契约 status='pending'） */
export function countPendingOfTypes(types: Array<string>): number {
	const lines = readLines()
	let n = 0
	for (let i = 0; i < lines.length; i++) {
		const e = parseLine(lines[i])
		if (e == null) {
			continue
		}
		if (e.getString('status') != 'pending') {
			continue
		}
		const t = e.getString('op_type') ?? ''
		for (let j = 0; j < types.length; j++) {
			if (types[j] == t) {
				n++
				break
			}
		}
	}
	return n
}

/** 取下一批 pending 条目（op_type ∈ types，队列顺序，≤size；消费方按 op_id 幂等去重） */
export function nextBatchOfTypes(types: Array<string>, size: number): Array<UTSJSONObject> {
	const lines = readLines()
	const out: Array<UTSJSONObject> = []
	for (let i = 0; i < lines.length && out.length < size; i++) {
		const e = parseLine(lines[i])
		if (e == null) {
			continue
		}
		if (e.getString('status') != 'pending') {
			continue
		}
		const t = e.getString('op_type') ?? ''
		let match = false
		for (let j = 0; j < types.length; j++) {
			if (types[j] == t) {
				match = true
				break
			}
		}
		if (match) {
			out.push(e)
		}
	}
	return out
}

/** 全部 pending 条目（op_type ∈ types，队列顺序；event_ops 顺序 flush 用） */
export function allPendingOfTypes(types: Array<string>): Array<UTSJSONObject> {
	const lines = readLines()
	const out: Array<UTSJSONObject> = []
	for (let i = 0; i < lines.length; i++) {
		const e = parseLine(lines[i])
		if (e == null) {
			continue
		}
		if (e.getString('status') != 'pending') {
			continue
		}
		const t = e.getString('op_type') ?? ''
		for (let j = 0; j < types.length; j++) {
			if (types[j] == t) {
				out.push(e)
				break
			}
		}
	}
	return out
}

/** 按 op_id 移除一批（脏行保留；服务端已按 op_id 幂等去重） */
export function removeByIds(ids: Array<string>): void {
	if (ids.length === 0) {
		return
	}
	const lines = readLines()
	const kept: Array<string> = []
	for (let i = 0; i < lines.length; i++) {
		const e = parseLine(lines[i])
		if (e == null) {
			kept.push(lines[i])
			continue
		}
		const id = e.getString('op_id') ?? ''
		let drop = false
		for (let j = 0; j < ids.length; j++) {
			if (ids[j] != '' && ids[j] == id) {
				drop = true
				break
			}
		}
		if (!drop) {
			kept.push(lines[i])
		}
	}
	writeLines(kept)
}

/** 失败保留时 retry_count +1（六字段契约使用方；脏行原样保留） */
export function bumpRetryById(id: string): void {
	if (id == '') {
		return
	}
	const lines = readLines()
	const out: Array<string> = []
	let bumped = false
	for (let i = 0; i < lines.length; i++) {
		const e = parseLine(lines[i])
		if (e == null) {
			out.push(lines[i])
			continue
		}
		if ((e.getString('op_id') ?? '') == id) {
			if (!bumped) {
				const cur = e.getNumber('retry_count') as number
				e.set('retry_count', (cur != null ? cur : 0) + 1)
				bumped = true
			}
			out.push(JSON.stringify(e))
		} else {
			out.push(lines[i])
		}
	}
	writeLines(out)
}
