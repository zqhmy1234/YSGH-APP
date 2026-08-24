/**
 * 事件手动操作（S-MO-1 · B3-5 用户操作优先，算法永不覆盖）
 *
 * 后端已就绪（api/events.py：merge/split/confirm + GET /events/{id}/items，
 * test_event_ops 9 项全过）。MVP UI 范围：confirm（转正）+ merge（合并到相邻
 * 上一张）+ split（选片拆分，2026-08-25 后端补 items 端点后启用）。
 *
 * 离线 op_log（2026-08-25 · 第二波遗留）：网络不可用时操作不丢失——
 * 入本地队列（uni storage，JSON 行分隔串），联网后 flushOpQueue 按序补发。
 * 后端 merge/split/confirm 非幂等（无 op_id），队列顺序执行 + 网络恢复后
 * 单飞 flush，重复概率极低；XView（SQLCipher op_log 表）随自定义基座落地。
 *
 * 契约：
 *  - GET  /api/v1/events/{id}/items           → 成员明细（split 选片）
 *  - POST /api/v1/events/confirm {event_id}   → EventOut（status=confirmed）
 *  - POST /api/v1/events/merge {target_event_id, source_event_ids:[>=1]}
 *  - POST /api/v1/events/split {event_id, content_ids:[>=1]}
 */
import { post, get, dataObj, dataArr } from './api'

const OP_LOG_KEY = 'yishu_op_log'

/** 拆分选片条目（GET /events/{id}/items 解析） */
export class SplitItem {
	contentId: string
	contentType: string
	title: string
	takenAt: number
	selected: boolean

	constructor(contentId: string, contentType: string, title: string, takenAt: number) {
		this.contentId = contentId
		this.contentType = contentType
		this.title = title
		this.takenAt = takenAt
		this.selected = false
	}
}

/** 网络检查（uni-app x getNetworkType 为回调式；none/未知视为离线） */
function checkNet(cb: (online: boolean) => void): void {
	uni.getNetworkType({
		success: (res) => {
			const t = res.networkType
			cb(t !== 'none' && t != '')
		},
		fail: () => {
			cb(false)
		}
	})
}

/** 确认事件（转正；title 可空=保持原标题）→ 成功 true（离线则入队返回 true） */
export function confirmEvent(eventId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		checkNet((online: boolean) => {
			if (!online) {
				const body: UTSJSONObject = {
					event_id: eventId
				}
				enqueueOp('confirm', body)
				resolve(true)
				return
			}
			const body2: UTSJSONObject = {
				event_id: eventId
			}
			post('/api/v1/events/confirm', body2).then((resp: UTSJSONObject | null) => {
				if (resp == null) {
					resolve(false)
					return
				}
				const data = dataObj(resp)
				if (data == null) {
					resolve(false)
					return
				}
				resolve(data.getString('id') != null)
			})
		})
	})
}

/** 合并：source 并入 target（source 软删，target 置 confirmed）→ 成功 true */
export function mergeEvent(sourceEventId: string, targetEventId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		checkNet((online: boolean) => {
			if (!online) {
				const body: UTSJSONObject = {
					target_event_id: targetEventId,
					source_event_ids: [sourceEventId]
				}
				enqueueOp('merge', body)
				resolve(true)
				return
			}
			const sourceIds: Array<string> = [sourceEventId]
			const body2: UTSJSONObject = {
				target_event_id: targetEventId,
				source_event_ids: sourceIds
			}
			post('/api/v1/events/merge', body2).then((resp: UTSJSONObject | null) => {
				if (resp == null) {
					resolve(false)
					return
				}
				const data = dataObj(resp)
				if (data == null) {
					resolve(false)
					return
				}
				resolve(data.getString('id') != null)
			})
		})
	})
}

/** 拉取事件成员明细（split 选片）→ 空数组=失败/无成员 */
export function fetchEventItems(eventId: string): Promise<Array<SplitItem>> {
	return new Promise<Array<SplitItem>>((resolve) => {
		get('/api/v1/events/' + eventId + '/items').then((resp: UTSJSONObject | null) => {
			if (resp == null) {
				resolve([])
				return
			}
			const arr = dataArr(resp)
			const out: Array<SplitItem> = []
			for (let i = 0; i < arr.length; i++) {
				const it = arr[i]
				out.push(new SplitItem(
					it.getString('content_id') ?? '',
					it.getString('content_type') ?? 'text',
					it.getString('title') ?? '',
					parseTimeMs(it.getString('taken_at') ?? '')
				))
			}
			resolve(out)
		})
	})
}

/** 拆分：选中内容拆出建新事件 → 成功 true（离线则入队返回 true） */
export function splitEvent(eventId: string, contentIds: Array<string>): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		checkNet((online: boolean) => {
			if (!online) {
				const body: UTSJSONObject = {
					event_id: eventId,
					content_ids: contentIds
				}
				enqueueOp('split', body)
				resolve(true)
				return
			}
			const body2: UTSJSONObject = {
				event_id: eventId,
				content_ids: contentIds
			}
			post('/api/v1/events/split', body2).then((resp: UTSJSONObject | null) => {
				if (resp == null) {
					resolve(false)
					return
				}
				const data = dataObj(resp)
				if (data == null) {
					resolve(false)
					return
				}
				resolve(data.getString('id') != null)
			})
		})
	})
}

/** 入离线队列（payload 为 UTSJSONObject；行分隔 JSON 串存储） */
export function enqueueOp(opType: string, payload: UTSJSONObject): void {
	const entry: UTSJSONObject = {
		op_id: 'op_' + Date.now().toString(),
		op_type: opType,
		payload: payload
	}
	const raw = uni.getStorageSync(OP_LOG_KEY) as string
	let lines: Array<string> = []
	if (raw != '') {
		lines = raw.split('\n')
	}
	lines.push(JSON.stringify(entry))
	uni.setStorageSync(OP_LOG_KEY, lines.join('\n'))
	uni.showToast({ title: '已离线排队，联网后自动同步', icon: 'none' })
}

/** 待同步队列条数 */
export function pendingOpCount(): number {
	const raw = uni.getStorageSync(OP_LOG_KEY) as string
	if (raw == '') {
		return 0
	}
	return raw.split('\n').length
}

/** 联网后补发离线队列（按序；网络仍断则停，业务失败丢弃）→ 剩余条数 */
export function flushOpQueue(): Promise<number> {
	return new Promise<number>((resolve) => {
		const raw = uni.getStorageSync(OP_LOG_KEY) as string
		if (raw == '') {
			resolve(0)
			return
		}
		checkNet((online: boolean) => {
			if (!online) {
				resolve(raw.split('\n').length)
				return
			}
			flushNext(raw.split('\n'), [], 0, 0, resolve)
		})
	})
}

/** 单条处理（模块级函数：UTS 箭头函数不可自引用，递归必须用具名函数声明） */
function flushNext(lines: Array<string>, remain: Array<string>, flushed: number, idx: number, resolve: (n: number) => void): void {
	if (idx >= lines.length) {
		uni.setStorageSync(OP_LOG_KEY, remain.join('\n'))
		if (flushed > 0) {
			uni.showToast({ title: '已同步 ' + flushed + ' 条离线操作', icon: 'none' })
		}
		resolve(remain.length)
		return
	}
	const line = lines[idx]
	if (line == '') {
		flushNext(lines, remain, flushed, idx + 1, resolve)
		return
	}
	let entry: UTSJSONObject | null = null
	try {
		entry = JSON.parse(line) as UTSJSONObject
	} catch (e) {
		remain.push(line) // 解析失败保守保留，不丢用户操作
		flushNext(lines, remain, flushed, idx + 1, resolve)
		return
	}
	const opType = entry.getString('op_type') ?? ''
	const payload = entry.getJSON('payload')
	if (payload == null) {
		flushNext(lines, remain, flushed, idx + 1, resolve) // 无 payload 视为脏数据丢弃
		return
	}
	doOp(opType, payload).then((ok: boolean) => {
		if (ok) {
			flushNext(lines, remain, flushed + 1, idx + 1, resolve)
		} else {
			// 失败后重新探测网络：仍断 → 保留该条及其后全部；在线 → 业务失败丢弃
			checkNet((online: boolean) => {
				if (!online) {
					remain.push(line)
					for (let j = idx + 1; j < lines.length; j++) {
						if (lines[j] != '') {
							remain.push(lines[j])
						}
					}
					uni.setStorageSync(OP_LOG_KEY, remain.join('\n'))
					resolve(remain.length)
				} else {
					flushNext(lines, remain, flushed, idx + 1, resolve)
				}
			})
		}
	})
}

/** 单条离线操作补发（按 op_type 路由到后端端点） */
function doOp(opType: string, payload: UTSJSONObject): Promise<boolean> {
	if (opType === 'confirm') {
		return confirmEvent(payload.getString('event_id') ?? '')
	} else if (opType === 'merge') {
		const targetId = payload.getString('target_event_id') ?? ''
		const sourceIds = payload.getArray('source_event_ids') as Array<string> | null
		if (targetId == '' || sourceIds == null || sourceIds.length == 0) {
			return Promise.resolve(false)
		}
		return mergeEvent(sourceIds[0], targetId)
	} else if (opType === 'split') {
		const eventId = payload.getString('event_id') ?? ''
		const ids = payload.getArray('content_ids') as Array<string> | null
		if (eventId == '' || ids == null || ids.length == 0) {
			return Promise.resolve(false)
		}
		return splitEvent(eventId, ids)
	}
	return Promise.resolve(false)
}

/** ISO8601 → epoch ms（复用 timeline 同款解析；空返回 0） */
function parseTimeMs(iso: string): number {
	if (iso == '') {
		return 0
	}
	const tIdx = iso.indexOf('T')
	if (tIdx < 0) {
		return 0
	}
	const d = iso.substring(0, tIdx).split('-')
	if (d.length !== 3) {
		return 0
	}
	const rest = iso.substring(tIdx + 1)
	const t = rest.split('+')[0].split('.')[0]
	const hms = t.split(':')
	if (hms.length < 3) {
		return 0
	}
	const date = new Date(parseInt(d[0]), parseInt(d[1]) - 1, parseInt(d[2]), parseInt(hms[0]), parseInt(hms[1]), parseInt(hms[2]))
	return date.getTime()
}
