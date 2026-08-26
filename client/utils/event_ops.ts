/**
 * 事件手动操作（S-MO-1 · B3-5 用户操作优先，算法永不覆盖）
 *
 * 后端已就绪（api/events.py：merge/split/confirm + GET /events/{id}/items，
 * test_event_ops 9 项全过）。MVP UI 范围：confirm（转正）+ merge（合并到相邻
 * 上一张）+ split（选片拆分，2026-08-25 后端补 items 端点后启用）。
 *
 * 2026-08-26 Wave2 AgentE 新增：
 *  - fetchContentEvents：照片→事件反向入口（GET /api/v1/contents/{id}/events，
 *    新文件 api/event_items.py 提供；照片详情"属于"列表）
 *  - ignoreEventLocally/isIgnoredEvent：L2 待确认区"忽略"——MVP 本地忽略
 *    （后端无 reject 端点，Agent D 域；本地隐藏 + 可被未来 reject API 取代）
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
 *  - GET  /api/v1/contents/{id}/events        → 照片所属事件列表（反向入口）
 */
import { post, get, dataObj, dataArr } from './api'
import { isoLocal, parseIsoMs } from './time'
import { getNetKind, NetKind } from './uploader'

const OP_LOG_KEY = 'yishu_op_log'
const IGNORE_KEY = 'yishu_ignored_events'

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

/** 照片所属事件（反向入口 GET /contents/{id}/events 解析；照片详情"属于"列表） */
export class ContentEventRef {
	id: string
	level: number
	title: string
	coverContentId: string
	status: string
	confidence: number
	photoCount: number

	constructor(id: string, level: number, title: string, coverContentId: string, status: string, confidence: number, photoCount: number) {
		this.id = id
		this.level = level
		this.title = title
		this.coverContentId = coverContentId
		this.status = status
		this.confidence = confidence
		this.photoCount = photoCount
	}
}

/** 照片→事件反向查询（B3-4：照片详情"属于：事件列表"）；失败/无归属 → 空数组 */
export function fetchContentEvents(contentId: string): Promise<Array<ContentEventRef>> {
	return new Promise<Array<ContentEventRef>>((resolve) => {
		get('/api/v1/contents/' + contentId + '/events').then((resp: UTSJSONObject | null) => {
			if (resp == null) {
				resolve([])
				return
			}
			const arr = dataArr(resp)
			const out: Array<ContentEventRef> = []
			for (let i = 0; i < arr.length; i++) {
				const it = arr[i]
				out.push(new ContentEventRef(
					it.getString('id') ?? '',
					it.getNumber('level') as number,
					it.getString('title') ?? '',
					it.getString('cover_content_id') ?? '',
					it.getString('status') ?? 'draft',
					it.getNumber('confidence') as number,
					it.getNumber('photo_count') as number
				))
			}
			resolve(out)
		})
	})
}

/** L2 待确认区"忽略"（MVP 本地隐藏；后端 reject 端点由 Agent D 域，后续可替换） */
export function ignoreEventLocally(eventId: string): void {
	const raw = uni.getStorageSync(IGNORE_KEY) as string
	let ids: Array<string> = []
	if (raw != '') {
		ids = raw.split('\n')
	}
	for (let i = 0; i < ids.length; i++) {
		if (ids[i] == eventId) {
			return
		}
	}
	ids.push(eventId)
	uni.setStorageSync(IGNORE_KEY, ids.join('\n'))
}

/** 事件是否被本地忽略（待确认区过滤） */
export function isIgnoredEvent(eventId: string): boolean {
	const raw = uni.getStorageSync(IGNORE_KEY) as string
	if (raw == '') {
		return false
	}
	const ids = raw.split('\n')
	for (let i = 0; i < ids.length; i++) {
		if (ids[i] == eventId) {
			return true
		}
	}
	return false
}

/** 确认事件（转正；title 可空=保持原标题）→ 成功 true（离线则入队返回 true） */
export function confirmEvent(eventId: string): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		getNetKind((kind: NetKind) => {
			const online = kind != 'none'
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
		getNetKind((kind: NetKind) => {
			const online = kind != 'none'
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
					parseIsoMs(it.getString('taken_at') ?? '')
				))
			}
			resolve(out)
		})
	})
}

/** 拆分：选中内容拆出建新事件 → 成功 true（离线则入队返回 true） */
export function splitEvent(eventId: string, contentIds: Array<string>): Promise<boolean> {
	return new Promise<boolean>((resolve) => {
		getNetKind((kind: NetKind) => {
			const online = kind != 'none'
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

/** 入离线队列（payload 为 UTSJSONObject；行分隔 JSON 串存储）
 *  2026-08-26 Wave3 H：队列补齐六字段契约（op_id/op_type/payload/status/created_at/retry_count），
 *  与 sync_client 后端 offline_queue 六字段对齐（audit_B4_sync §4）。 */
export function enqueueOp(opType: string, payload: UTSJSONObject): void {
	const entry: UTSJSONObject = {
		op_id: 'op_' + Date.now().toString(),
		op_type: opType,
		payload: payload,
		status: 'pending',
		created_at: isoNow(),
		retry_count: 0
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

/** epoch ms → ISO8601 本地时间（S1-M4 收口：统一走 time.isoLocal，不再本地拼一份） */
function isoNow(): string {
	return isoLocal(Date.now())
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
		getNetKind((kind: NetKind) => {
			const online = kind != 'none'
			if (!online) {
				resolve(raw.split('\n').length)
				return
			}
			flushNext(raw.split('\n'), [], 0, 0, resolve)
		})
	})
}

/** 失败保留时 retry_count +1（六字段契约使用方；解析失败原样保留） */
function bumpRetry(line: string): string {
	try {
		const e = JSON.parse(line) as UTSJSONObject
		if (e != null) {
			const cur = e.getNumber('retry_count') as number
			e.set('retry_count', (cur != null ? cur : 0) + 1)
			return JSON.stringify(e)
		}
	} catch (e) {
		// 脏行原样保留
	}
	return line
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
			getNetKind((kind: NetKind) => {
				const online = kind != 'none'
				if (!online) {
					remain.push(bumpRetry(line))
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
